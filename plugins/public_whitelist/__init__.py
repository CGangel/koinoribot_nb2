"""
公网多端白名单子插件 - public_whitelist

只有白名单中的bot才能触发事件。
领养流程：用户申请 → SU审核 → 通过后加入白名单。
使用SQLite持久化，启动时和新增/删除白名单时更新内存缓存。

迁移自旧版 koinoribot public_whitelist
"""

import json
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional, Dict, Set, List

from aiohttp import web
import nonebot.adapters.onebot.v11 as onebot_adapter
from nonebot import on_command, get_driver
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Event, Bot, Message
from nonebot import logger
from nonebot.params import CommandArg, Depends
from nonebot.message import event_preprocessor
from nonebot.exception import IgnoredException

from ...tools import (
    get_uid, get_group_id_optional, build_forward_node,
    send_group_forward_msg, get_sender_nickname, is_onebot, is_qqbot
)
from ...uid_manager import get_external_ids
from ...su_manager import is_su
from ...config_store import config as koinori_config

__plugin_meta__ = PluginMetadata(
    name="public_whitelist",
    description="公网多端白名单管理 — 领养申请 + SU审核 + 白名单过滤",
    usage="领养云冰祈 / 审核列表 / 审核通过 <id> / 审核拒绝 <id> / 查询领养状态",
)

# ================== 数据库 ==================

def _get_db_path() -> str:
    plugin_dir = Path(__file__).parent.parent.parent
    return str(plugin_dir / "src" / "database" / "koinoribot.db")


def _init_tables():
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS public_whitelist (
                owner_qq TEXT PRIMARY KEY,
                bot_qq TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS whitelist_review (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_qq TEXT NOT NULL,
                bot_qq TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                compliance_commit TEXT NOT NULL DEFAULT '',
                tech_commit TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer_qq TEXT DEFAULT NULL,
                review_comment TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT DEFAULT NULL
            )
        ''')
        conn.commit()
    finally:
        conn.close()


# ================== 内存缓存 ==================

_cache_owner_to_bot: Dict[str, str] = {}
_cache_bot_set: Set[str] = set()

WHITELIST_WEB_HOST = "0.0.0.0"
WHITELIST_WEB_PORT = 8888
_whitelist_web_runner: Optional[web.AppRunner] = None
_whitelist_web_site: Optional[web.TCPSite] = None


def load_cache():
    global _cache_owner_to_bot, _cache_bot_set
    conn = sqlite3.connect(_get_db_path())
    try:
        rows = conn.execute(
            'SELECT owner_qq, bot_qq FROM public_whitelist'
        ).fetchall()
        _cache_owner_to_bot = {row[0]: row[1] for row in rows}
        _cache_bot_set = {row[1] for row in rows}
        logger.info(f"[public_whitelist] 已加载 {len(_cache_owner_to_bot)} 个白名单bot到内存")
    finally:
        conn.close()


def is_whitelisted(bot_qq: str) -> bool:
    return bot_qq in _cache_bot_set


def get_owner(bot_qq: str) -> Optional[str]:
    for owner, bqq in _cache_owner_to_bot.items():
        if bqq == bot_qq:
            return owner
    return None


def add_to_whitelist(owner_qq: str, bot_qq: str) -> bool:
    conn = sqlite3.connect(_get_db_path())
    try:
        conn.execute(
            'INSERT INTO public_whitelist (owner_qq, bot_qq, created_at) VALUES (?, ?, ?)',
            (owner_qq, bot_qq, datetime.now().isoformat())
        )
        conn.commit()
        _cache_owner_to_bot[owner_qq] = bot_qq
        _cache_bot_set.add(bot_qq)
        logger.info(f"[public_whitelist] 添加白名单: owner={owner_qq}, bot={bot_qq}")
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_from_whitelist(owner_qq: str) -> Optional[str]:
    conn = sqlite3.connect(_get_db_path())
    try:
        row = conn.execute(
            'SELECT bot_qq FROM public_whitelist WHERE owner_qq = ?', (owner_qq,)
        ).fetchone()
        if not row:
            return None
        bot_qq = row[0]
        conn.execute('DELETE FROM public_whitelist WHERE owner_qq = ?', (owner_qq,))
        conn.commit()
        _cache_owner_to_bot.pop(owner_qq, None)
        _cache_bot_set.discard(bot_qq)
        logger.info(f"[public_whitelist] 删除白名单: owner={owner_qq}, bot={bot_qq}")
        return bot_qq
    finally:
        conn.close()


def delete_review_records_for_bot(bot_qq: str) -> int:
    """删除指定bot的所有审核记录，返回删除条数。"""
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM whitelist_review WHERE bot_qq = ?', (bot_qq,))
        deleted_count = cursor.rowcount
        conn.commit()
        logger.info(f"[public_whitelist] 删除bot审核记录: bot={bot_qq}, count={deleted_count}")
        return deleted_count
    finally:
        conn.close()


def get_whitelist_size() -> int:
    return len(_cache_owner_to_bot)


# ================== WS地址配置 ==================


def get_ws_info() -> dict:
    """获取WS连接信息（地址根据 ip_address + driver.port 自动拼接）"""
    port = get_driver().config.port
    ws_address = f"ws://{koinori_config.ip_address}:{port}/onebot/v11/ws"
    return {
        "ws_address": ws_address,
        "connection_help": [
            "请新建ws反向连接，连接地址如下：",
            f"{ws_address}",
        ],
    }


def get_whitelist_web_url() -> str:
    """获取白名单网页查询地址。"""
    return f"http://{koinori_config.ip_address}:{WHITELIST_WEB_PORT}/"


# ================== 白名单WS查询网页 ==================

def _is_whitelist_pair(owner_qq: str, bot_qq: str) -> bool:
    """校验主人QQ和bot QQ是否为同一条白名单绑定关系。"""
    return _cache_owner_to_bot.get(owner_qq) == bot_qq


def _get_latest_review(owner_qq: str, bot_qq: str) -> Optional[dict]:
    """查询同一组账号最近一条领养申请。"""
    conn = sqlite3.connect(_get_db_path())
    try:
        row = conn.execute(
            '''SELECT id, status, review_comment, reviewed_at, created_at
               FROM whitelist_review
               WHERE owner_qq = ? AND bot_qq = ?
               ORDER BY id DESC
               LIMIT 1''',
            (owner_qq, bot_qq)
        ).fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "status": row[1],
            "review_comment": row[2],
            "reviewed_at": row[3],
            "created_at": row[4],
        }
    finally:
        conn.close()


def _render_whitelist_page(
    owner_qq: str = "",
    bot_qq: str = "",
    result_html: str = "",
) -> str:
    safe_owner = escape(owner_qq)
    safe_bot = escape(bot_qq)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>云冰祈 WS 查询</title>
  <style>
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      color: #202124;
      background: #f6f5f2;
    }}
    main {{
      width: min(760px, calc(100% - 32px));
      margin: 0 auto;
      padding: 56px 0;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      line-height: 1.2;
      font-weight: 700;
    }}
    .subtitle {{
      margin: 0 0 28px;
      color: #5f6368;
      line-height: 1.7;
    }}
    form {{
      display: grid;
      gap: 16px;
      padding: 24px;
      border: 1px solid #dedbd4;
      border-radius: 8px;
      background: #ffffff;
      box-shadow: 0 12px 30px rgba(44, 39, 31, 0.08);
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 14px;
      font-weight: 700;
      color: #343330;
    }}
    input {{
      width: 100%;
      min-height: 44px;
      padding: 10px 12px;
      border: 1px solid #c8c4bd;
      border-radius: 6px;
      font-size: 16px;
      color: #202124;
      background: #fbfaf8;
      outline: none;
    }}
    input:focus {{
      border-color: #1b7f67;
      box-shadow: 0 0 0 3px rgba(27, 127, 103, 0.14);
    }}
    button {{
      width: fit-content;
      min-height: 42px;
      padding: 0 18px;
      border: 0;
      border-radius: 6px;
      font-size: 15px;
      font-weight: 700;
      color: #ffffff;
      background: #1b7f67;
      cursor: pointer;
    }}
    button:hover {{
      background: #156a56;
    }}
    .result {{
      margin-top: 18px;
      padding: 20px;
      border-radius: 8px;
      border: 1px solid #dedbd4;
      background: #ffffff;
    }}
    .result h2 {{
      margin: 0 0 12px;
      font-size: 20px;
      line-height: 1.3;
    }}
    .result p {{
      margin: 0;
      line-height: 1.7;
      color: #4b4d4f;
    }}
    .success {{
      border-left: 4px solid #1b7f67;
    }}
    .pending {{
      border-left: 4px solid #c97813;
    }}
    .error {{
      border-left: 4px solid #b3261e;
    }}
    dl {{
      display: grid;
      grid-template-columns: 96px 1fr;
      gap: 8px 12px;
      margin: 0 0 16px;
    }}
    dt {{
      color: #5f6368;
    }}
    dd {{
      margin: 0;
      word-break: break-all;
    }}
    .code {{
      padding: 12px;
      border-radius: 6px;
      background: #f1f3f1;
      font-family: Consolas, "Courier New", monospace;
      word-break: break-all;
    }}
    ol {{
      margin: 12px 0 0;
      padding-left: 22px;
      line-height: 1.8;
    }}
    @media (max-width: 560px) {{
      main {{
        width: min(100% - 24px, 760px);
        padding: 32px 0;
      }}
      h1 {{
        font-size: 26px;
      }}
      form {{
        padding: 18px;
      }}
      button {{
        width: 100%;
      }}
      dl {{
        grid-template-columns: 1fr;
        gap: 4px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>云冰祈 WS 查询</h1>
    <p class="subtitle">输入主人 QQ 与 bot QQ，查询审核状态和 WS 连接信息。</p>
    <form method="post" action="/">
      <label>
        主人 QQ
        <input name="owner_qq" value="{safe_owner}" inputmode="numeric" pattern="[0-9]*" autocomplete="off" required>
      </label>
      <label>
        bot QQ
        <input name="bot_qq" value="{safe_bot}" inputmode="numeric" pattern="[0-9]*" autocomplete="off" required>
      </label>
      <button type="submit">查询连接信息</button>
    </form>
    {result_html}
  </main>
</body>
</html>"""


def _render_success_result(owner_qq: str, bot_qq: str) -> str:
    ws_info = get_ws_info()
    ws_address = escape(ws_info.get("ws_address", "未配置"))
    safe_owner = escape(owner_qq)
    safe_bot = escape(bot_qq)
    return f"""
    <section class="result success">
      <h2>白名单验证通过</h2>
      <dl>
        <dt>审核状态</dt>
        <dd>已通过，白名单已生效</dd>
        <dt>主人 QQ</dt>
        <dd>{safe_owner}</dd>
        <dt>bot QQ</dt>
        <dd>{safe_bot}</dd>
        <dt>WS 地址</dt>
        <dd><div class="code">{ws_address}</div></dd>
      </dl>
      <p>连接方法</p>
      <ol>
        <li>在你的 OneBot V11 客户端中新建 ws 反向连接。</li>
        <li>连接地址填写上方 WS 地址。</li>
        <li>保存配置并启动 bot，审核通过后即可接入。</li>
      </ol>
    </section>"""


def _render_pending_result(owner_qq: str, bot_qq: str, review: dict) -> str:
    ws_info = get_ws_info()
    ws_address = escape(ws_info.get("ws_address", "未配置"))
    safe_owner = escape(owner_qq)
    safe_bot = escape(bot_qq)
    review_id = int(review["id"])
    created_at = escape(str(review.get("created_at") or "未知"))
    return f"""
    <section class="result pending">
      <h2>申请正在审核中</h2>
      <dl>
        <dt>审核状态</dt>
        <dd>待审核</dd>
        <dt>申请编号</dt>
        <dd>{review_id}</dd>
        <dt>申请时间</dt>
        <dd>{created_at}</dd>
        <dt>主人 QQ</dt>
        <dd>{safe_owner}</dd>
        <dt>bot QQ</dt>
        <dd>{safe_bot}</dd>
        <dt>WS 地址</dt>
        <dd><div class="code">{ws_address}</div></dd>
      </dl>
      <p>这组账号已提交领养申请，可以先配置连接；审核通过前无法实际接入使用。</p>
      <ol>
        <li>在你的 OneBot V11 客户端中新建 ws 反向连接。</li>
        <li>连接地址填写上方 WS 地址。</li>
        <li>等待管理员审核通过后再启动或重连 bot。</li>
      </ol>
    </section>"""


def _render_rejected_result(owner_qq: str, bot_qq: str, review: dict) -> str:
    safe_owner = escape(owner_qq)
    safe_bot = escape(bot_qq)
    review_id = int(review["id"])
    reason = escape(str(review.get("review_comment") or "无"))
    reviewed_at = escape(str(review.get("reviewed_at") or "未知"))
    return f"""
    <section class="result error">
      <h2>申请已被拒绝</h2>
      <dl>
        <dt>审核状态</dt>
        <dd>已拒绝</dd>
        <dt>申请编号</dt>
        <dd>{review_id}</dd>
        <dt>主人 QQ</dt>
        <dd>{safe_owner}</dd>
        <dt>bot QQ</dt>
        <dd>{safe_bot}</dd>
        <dt>审核时间</dt>
        <dd>{reviewed_at}</dd>
        <dt>拒绝理由</dt>
        <dd>{reason}</dd>
      </dl>
      <p>该申请未通过审核，因此暂不返回 WS 连接地址。</p>
    </section>"""


def _render_error_result(message: str, status_text: str = "未找到") -> str:
    safe_status = escape(status_text)
    return f"""
    <section class="result error">
      <h2>未返回连接信息</h2>
      <dl>
        <dt>审核状态</dt>
        <dd>{safe_status}</dd>
      </dl>
      <p>{escape(message)}</p>
    </section>"""


def _render_whitelist_query_result(owner_qq: str, bot_qq: str) -> str:
    if not owner_qq or not bot_qq:
        return _render_error_result("主人 QQ 和 bot QQ 都需要填写。", "无法查询")
    if not owner_qq.isdigit() or not bot_qq.isdigit():
        return _render_error_result("主人 QQ 和 bot QQ 只能填写数字。", "无法查询")
    if _is_whitelist_pair(owner_qq, bot_qq):
        return _render_success_result(owner_qq, bot_qq)

    review = _get_latest_review(owner_qq, bot_qq)
    if not review:
        return _render_error_result("没有找到匹配的白名单绑定关系或待审核申请。")

    status = review.get("status")
    if status == "pending":
        return _render_pending_result(owner_qq, bot_qq, review)
    if status == "rejected":
        return _render_rejected_result(owner_qq, bot_qq, review)
    if status == "approved":
        return _render_error_result(
            "审核状态为已通过，但白名单尚未生效，请联系管理员处理。",
            "已通过，白名单未生效",
        )
    return _render_error_result(
        "申请存在，但当前状态暂不返回 WS 连接地址。",
        str(status or "未知"),
    )


async def _handle_whitelist_web_index(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    return web.Response(
        text=_render_whitelist_page(),
        content_type="text/html",
        charset="utf-8",
    )


async def _handle_whitelist_web_query(request: web.Request) -> web.Response:
    data = await request.post()
    owner_qq = str(data.get("owner_qq", "")).strip()
    bot_qq = str(data.get("bot_qq", "")).strip()

    result_html = _render_whitelist_query_result(owner_qq, bot_qq)

    return web.Response(
        text=_render_whitelist_page(owner_qq, bot_qq, result_html),
        content_type="text/html",
        charset="utf-8",
    )


async def _handle_whitelist_web_health(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    return web.json_response({"ok": True, "whitelist_size": get_whitelist_size()})


def _create_whitelist_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _handle_whitelist_web_index)
    app.router.add_post("/", _handle_whitelist_web_query)
    app.router.add_get("/public_whitelist", _handle_whitelist_web_index)
    app.router.add_post("/public_whitelist", _handle_whitelist_web_query)
    app.router.add_get("/health", _handle_whitelist_web_health)
    return app


async def _start_whitelist_web_server():
    global _whitelist_web_runner, _whitelist_web_site

    if _whitelist_web_runner is not None:
        return

    runner = web.AppRunner(_create_whitelist_web_app())
    await runner.setup()
    site = web.TCPSite(runner, WHITELIST_WEB_HOST, WHITELIST_WEB_PORT)

    try:
        await site.start()
    except OSError as e:
        await runner.cleanup()
        logger.error(
            f"[public_whitelist] WS查询网页启动失败，端口 {WHITELIST_WEB_PORT} 可能已被占用: {e}"
        )
        return

    _whitelist_web_runner = runner
    _whitelist_web_site = site
    logger.info(
        f"[public_whitelist] WS查询网页已启动: http://{WHITELIST_WEB_HOST}:{WHITELIST_WEB_PORT}/"
    )


async def _stop_whitelist_web_server():
    global _whitelist_web_runner, _whitelist_web_site

    runner = _whitelist_web_runner
    _whitelist_web_runner = None
    _whitelist_web_site = None

    if runner is not None:
        await runner.cleanup()
        logger.info("[public_whitelist] WS查询网页已关闭")


# ================== 跨平台私信 ==================

async def send_private_message(bot: Bot, event: Event, uid: int, message: str):
    """跨平台发送私聊消息（OneBot用send_private_msg，QQ-Bot用send_to_c2c）"""
    external_ids = get_external_ids(uid)

    if is_onebot(event):
        qq = external_ids.get('onebot_id')
        if qq:
            try:
                await bot.send_private_msg(user_id=int(qq), message=message)
                return
            except Exception as e:
                logger.error(f"[public_whitelist] OneBot私信发送失败: {e}")

    if is_qqbot(event):
        openid = external_ids.get('qqbot_id')
        if openid:
            try:
                await bot.send_to_c2c(openid=openid, message=message)
                return
            except Exception as e:
                logger.error(f"[public_whitelist] QQ-Bot私信发送失败: {e}")

    logger.warning(f"[public_whitelist] 无法发送私信: uid={uid}, onebot_id={external_ids.get('onebot_id')}, qqbot_id={external_ids.get('qqbot_id')}")


# ================== 事件过滤器 ==================

@event_preprocessor
async def _whitelist_filter(event: Event):
    if not koinori_config.public_bot:
        return

    self_id = str(getattr(event, 'self_id', ''))
    if not self_id:
        return

    # koinori自己的bot账号不受白名单限制
    permit_bot = {str(b) for b in koinori_config.permit_bot}
    if self_id in permit_bot:
        return

    # 外部bot：必须在白名单中
    if self_id not in _cache_bot_set:
        raise IgnoredException(
            f"[public_whitelist] bot({self_id}) 不在白名单中，事件已忽略"
        )


# ================== 领养申请上下文（内存，多轮对话用） ==================

_apply_context: Dict[str, dict] = {}  # {user_id: {step, owner_qq, bot_qq, reason, ...}}
ADOPTION_ENDED_MESSAGE = "已结束领养云冰祈~"


# ================== 领养云冰祈 — 多轮对话 ==================

adopt_cmd = on_command("领养云冰祈", aliases={"云冰祈"}, priority=5, block=True)


@adopt_cmd.handle()
async def handle_adopt_start(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """第一步：检查资格，开始流程"""
    group_id = get_group_id_optional(event)

    if not group_id:
        await adopt_cmd.finish("领养云冰祈仅支持群聊~")

    external_ids = get_external_ids(uid)
    owner_qq = external_ids.get('onebot_id')
    if not owner_qq:
        await adopt_cmd.finish(
            "你还没有绑定QQ号，无法领养云冰祈~\n"
            "请先在onebot端使用bot，或在设置中绑定QQ号",
            at_sender=True
        )

    if owner_qq in _cache_owner_to_bot:
        await adopt_cmd.finish(
            "你已经领养过一个云冰祈了~\n如果想重新领养，请先 注销云冰祈",
            at_sender=True
        )

    conn = sqlite3.connect(_get_db_path())
    try:
        row = conn.execute(
            'SELECT id FROM whitelist_review WHERE owner_qq = ? AND status = ?',
            (owner_qq, 'pending')
        ).fetchone()
        if row:
            await adopt_cmd.finish(
                f"你已有一个待审核的领养申请（ID: {row[0]}），请耐心等待审核~",
                at_sender=True
            )
    finally:
        conn.close()

    _apply_context[event.get_user_id()] = {
        'step': 'bot_qq',
        'owner_qq': owner_qq,
        'bot_qq': '',
        'reason': '',
        'compliance_commit': '',
        'tech_commit': '',
        'group_id': group_id,
    }

    await adopt_cmd.send(
        "要领养云冰祈咯~\n\n请输入要注册的bot的QQ号（回复 退出 结束领养）：",
        at_sender=True
    )
    await adopt_cmd.pause()


@adopt_cmd.handle()
async def handle_step_bot_qq(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """第二步：输入bot QQ"""
    user_id = event.get_user_id()
    user_input = event.get_plaintext().strip()
    ctx = _apply_context.get(user_id)
    if not ctx:
        return
    if user_input == '退出':
        _apply_context.pop(user_id, None)
        await adopt_cmd.finish(ADOPTION_ENDED_MESSAGE, at_sender=True)

    if not user_input.isdigit():
        await adopt_cmd.reject("需要输入QQ号码~", at_sender=True)
    if user_input == ctx['owner_qq']:
        await adopt_cmd.reject("请使用bot账号的QQ~而且不能是自己的QQ", at_sender=True)
    if user_input in _cache_bot_set:
        await adopt_cmd.finish("该bot已被认领~", at_sender=True)

    conn = sqlite3.connect(_get_db_path())
    try:
        row = conn.execute(
            'SELECT id FROM whitelist_review WHERE bot_qq = ? AND status = ?',
            (user_input, 'pending')
        ).fetchone()
        if row:
            await adopt_cmd.finish("该bot已有待审核的领养申请~", at_sender=True)
    finally:
        conn.close()

    ctx['bot_qq'] = user_input
    await adopt_cmd.send(
        f"bot QQ: {user_input}\n\n请输入领养理由（如：想让bot活跃群气氛、在别的群使用冰祈、仅自用等）：",
        at_sender=True
    )
    await adopt_cmd.pause()


@adopt_cmd.handle()
async def handle_step_reason(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """第三步：输入领养理由"""
    user_id = event.get_user_id()
    user_input = event.get_plaintext().strip()
    ctx = _apply_context.get(user_id)
    if not ctx:
        return
    if user_input == '退出':
        _apply_context.pop(user_id, None)
        await adopt_cmd.finish(ADOPTION_ENDED_MESSAGE, at_sender=True)

    ctx['reason'] = user_input
    await adopt_cmd.send(
        "请确认是否承诺合规使用云冰祈：\n"
        "- 不用于违法、违规用途\n"
        "- 不用于骚扰、广告等行为\n"
        "- 遵守QQ平台用户协议\n\n"
        "请回复 我承诺合规使用 确认：",
        at_sender=True
    )
    await adopt_cmd.pause()


@adopt_cmd.handle()
async def handle_step_compliance(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """第四步：确认合规承诺"""
    user_id = event.get_user_id()
    user_input = event.get_plaintext().strip()
    ctx = _apply_context.get(user_id)
    if not ctx:
        return
    if user_input == '退出':
        _apply_context.pop(user_id, None)
        await adopt_cmd.finish(ADOPTION_ENDED_MESSAGE, at_sender=True)

    if user_input != '我承诺合规使用':
        await adopt_cmd.reject(
            "请回复 我承诺合规使用 来确认承诺，或回复 退出 取消申请",
            at_sender=True
        )

    ctx['compliance_commit'] = user_input
    await adopt_cmd.send(
        "云冰祈需要你自行搭建使用onebotv11协议的bot客户端并新建ws反向连接。\n"
        "请确认你了解这一技术要求：\n\n"
        "请回复 我已经搭建bot客户端 确认：",
        at_sender=True
    )
    await adopt_cmd.pause()


@adopt_cmd.handle()
async def handle_step_tech_confirm(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """第五步：确认技术能力"""
    user_id = event.get_user_id()
    user_input = event.get_plaintext().strip()
    ctx = _apply_context.get(user_id)
    if not ctx:
        return
    if user_input == '退出':
        _apply_context.pop(user_id, None)
        await adopt_cmd.finish(ADOPTION_ENDED_MESSAGE, at_sender=True)

    if user_input != '我已经搭建bot客户端':
        await adopt_cmd.reject(
            "请回复 我已经搭建bot客户端 来确认，或回复 退出 取消申请",
            at_sender=True
        )

    ctx['tech_commit'] = user_input
    summary = (
        "=== 领养申请确认 ===\n"
        f"主人QQ: {ctx['owner_qq']}\n"
        f"bot QQ: {ctx['bot_qq']}\n"
        f"领养理由: {ctx['reason']}\n"
        f"合规承诺: {ctx['compliance_commit']}\n"
        f"技术确认: {ctx['tech_commit']}\n"
        f"申请群号: {ctx['group_id']}\n"
        f"申请时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"==================\n"
        "回复 确认提交 提交审核，或回复 退出 取消"
    )
    await adopt_cmd.send(summary, at_sender=True)
    await adopt_cmd.pause()


@adopt_cmd.handle()
async def handle_step_confirm(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """第六步：确认提交"""
    user_id = event.get_user_id()
    user_input = event.get_plaintext().strip()
    ctx = _apply_context.get(user_id)
    if not ctx:
        return
    if user_input == '退出':
        _apply_context.pop(user_id, None)
        await adopt_cmd.finish(ADOPTION_ENDED_MESSAGE, at_sender=True)

    if user_input != '确认提交':
        await adopt_cmd.reject(
            "请回复 确认提交 来提交申请，或回复 退出 取消",
            at_sender=True
        )

    now_iso = datetime.now().isoformat()
    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO whitelist_review
               (owner_qq, bot_qq, reason, compliance_commit, tech_commit, group_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (ctx['owner_qq'], ctx['bot_qq'], ctx['reason'],
             ctx['compliance_commit'], ctx.get('tech_commit', ''), ctx['group_id'], 'pending', now_iso)
        )
        conn.commit()
        review_id = cursor.lastrowid
    finally:
        conn.close()

    _apply_context.pop(user_id, None)

    web_url = get_whitelist_web_url()
    await adopt_cmd.finish(
        f"=== 云冰祈领养申请已提交 ===\n"
        f"申请编号: {review_id}\n"
        f"主人 QQ: {ctx['owner_qq']}\n"
        f"bot QQ: {ctx['bot_qq']}\n\n"
        f"网页查询审核状态和WS地址：\n{web_url}\n"
        "在网页输入主人QQ和bot QQ即可查询。\n\n"
        "也可以在QQ内发送「查询领养状态」查看审核进度。",
        at_sender=True
    )


# ================== SU审核命令 ==================

review_list_cmd = on_command("审核列表", aliases={"审核云冰祈"}, priority=5, block=True)


@review_list_cmd.handle()
async def handle_review_list(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """SU查看待审核列表（合并转发消息）"""
    if not is_su(uid):
        return

    conn = sqlite3.connect(_get_db_path())
    try:
        rows = conn.execute(
            '''SELECT id, owner_qq, bot_qq, reason, compliance_commit,
                      tech_commit, group_id, created_at
               FROM whitelist_review
               WHERE status = 'pending'
               ORDER BY id ASC'''
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await review_list_cmd.finish("没有待审核的领养申请~")

    chain = onebot_adapter.Message()
    for row in rows:
        info = (
            f"=== 审核编号: {row[0]} ===\n"
            f"主人QQ: {row[1]}\n"
            f"bot QQ: {row[2]}\n"
            f"领养理由: {row[3]}\n"
            f"合规承诺: {row[4]}\n"
            f"技术确认: {row[5]}\n"
            f"申请群号: {row[6]}\n"
            f"申请时间: {row[7]}\n"
            "——————————————\n"
            "操作: 审核通过 {id}  或  审核拒绝 {id} [理由]"
        )
        node = await build_forward_node(bot, info)
        chain.append(node)

    chain.append(await build_forward_node(bot, f"共 {len(rows)} 条待审核申请"))

    if isinstance(event, onebot_adapter.GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=chain)
    else:
        await bot.send_private_forward_msg(user_id=event.get_user_id(), messages=chain)


approve_cmd = on_command("审核通过", priority=5, block=True)


@approve_cmd.handle()
async def handle_approve(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    """SU审核通过"""
    if not is_su(uid):
        return

    text = args.extract_plain_text().strip()
    try:
        review_id = int(text.split()[0])
    except (ValueError, IndexError):
        await approve_cmd.finish("用法: 审核通过 <申请编号>")

    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            'SELECT owner_qq, bot_qq FROM whitelist_review WHERE id = ? AND status = ?',
            (review_id, 'pending')
        ).fetchone()

        if not row:
            await approve_cmd.finish(f"申请编号 {review_id} 不存在或已处理")

        owner_qq, bot_qq = row[0], row[1]

        # 添加到白名单
        if not add_to_whitelist(owner_qq, bot_qq):
            await approve_cmd.finish(
                f"添加白名单失败：主人({owner_qq})或bot({bot_qq})已存在"
            )

        # 更新审核状态
        now_iso = datetime.now().isoformat()
        cursor.execute(
            '''UPDATE whitelist_review
               SET status = 'approved', reviewer_qq = ?, reviewed_at = ?
               WHERE id = ?''',
            (str(event.get_user_id()), now_iso, review_id)
        )
        conn.commit()
    finally:
        conn.close()

    await approve_cmd.finish(
        f"审核通过！bot({bot_qq})已加入白名单，绑定主人({owner_qq})"
    )


reject_cmd = on_command("审核拒绝", priority=5, block=True)


@reject_cmd.handle()
async def handle_reject(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    """SU审核拒绝"""
    if not is_su(uid):
        return

    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    try:
        review_id = int(parts[0])
    except (ValueError, IndexError):
        await reject_cmd.finish("用法: 审核拒绝 <申请编号> [理由]")

    reason = parts[1] if len(parts) > 1 else "无"

    conn = sqlite3.connect(_get_db_path())
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            'SELECT owner_qq, bot_qq FROM whitelist_review WHERE id = ? AND status = ?',
            (review_id, 'pending')
        ).fetchone()

        if not row:
            await reject_cmd.finish(f"申请编号 {review_id} 不存在或已处理")

        now_iso = datetime.now().isoformat()
        cursor.execute(
            '''UPDATE whitelist_review
               SET status = 'rejected', reviewer_qq = ?, review_comment = ?, reviewed_at = ?
               WHERE id = ?''',
            (str(event.get_user_id()), reason, now_iso, review_id)
        )
        conn.commit()
    finally:
        conn.close()

    await reject_cmd.finish(
        f"已拒绝申请编号 {review_id} (bot: {row[1]})\n理由: {reason}"
    )


# ================== 用户自助查询 ==================

query_cmd = on_command("查询领养状态", aliases={"我的领养"}, priority=5, block=True)


@query_cmd.handle()
async def handle_query_status(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """用户自助查询领养审核状态"""
    external_ids = get_external_ids(uid)
    owner_qq = external_ids.get('onebot_id')
    url = get_whitelist_web_url()
    if not owner_qq:
        await query_cmd.finish("你还没有绑定QQ号~", at_sender=True)

    # 先查白名单
    if owner_qq in _cache_owner_to_bot:
        bot_qq = _cache_owner_to_bot[owner_qq]
        await query_cmd.finish(
            f"你已成功领养云冰祈~\n主人QQ: {owner_qq}\nbot QQ: {bot_qq}\n配置方法请查看：{url}",
            at_sender=True
        )

    # 查审核列表
    conn = sqlite3.connect(_get_db_path())
    try:
        rows = conn.execute(
            '''SELECT id, bot_qq, status, reason, review_comment, reviewed_at, created_at
               FROM whitelist_review
               WHERE owner_qq = ?
               ORDER BY id DESC LIMIT 5''',
            (owner_qq,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await query_cmd.finish("你还没有领养过云冰祈~\n发送 领养云冰祈 开始领养", at_sender=True)

    status_map = {'pending': '待审核', 'approved': '已通过', 'rejected': '已拒绝'}
    lines = ["=== 领养状态查询 ==="]
    for row in rows:
        status_text = status_map.get(row[2], row[2])
        line = (
            f"申请编号: {row[0]}\n"
            f"bot QQ: {row[1]}\n"
            f"审核状态: {status_text}\n"
            f"申请时间: {row[6]}\n"
            f"你也可以访问：{url}快速查询/提前配置"
        )
        if row[2] == 'rejected':
            line += f"\n拒绝理由: {row[4] or '无'}"
        if row[2] == 'approved':
            line += f"\n通过时间: {row[5]}"
        lines.append(line)

    await query_cmd.finish("\n\n".join(lines), at_sender=True)


# ================== SU手动添加白名单 ==================

add_wl = on_command("添加公网白名单", priority=5, block=True)


@add_wl.handle()
async def handle_add_whitelist(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    """SU手动添加白名单（跳过审核）"""
    if not is_su(uid):
        return

    text = args.extract_plain_text().strip()
    parts = text.split()
    if len(parts) != 2:
        await add_wl.finish("用法: 添加公网白名单 <主人QQ> <botQQ>")

    owner_qq = parts[0]
    bot_qq = parts[1]

    if not bot_qq.isdigit():
        await add_wl.finish("botQQ必须是数字")

    if owner_qq in _cache_owner_to_bot:
        await add_wl.finish("该主人已有bot~")
    if bot_qq in _cache_bot_set:
        await add_wl.finish("该bot已被认领~")

    if add_to_whitelist(owner_qq, bot_qq):
        await add_wl.finish(f"已成功添加该bot({bot_qq})，并绑定主人({owner_qq})")
    else:
        await add_wl.finish("添加失败，可能已存在")


# ================== 注销云冰祈 ==================

logout_wl = on_command("注销云冰祈", priority=5, block=True)


@logout_wl.handle()
async def handle_logout_whitelist(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """用户自助注销云冰祈"""
    external_ids = get_external_ids(uid)
    owner_qq = external_ids.get('onebot_id')
    if not owner_qq:
        await logout_wl.finish("你还没有绑定QQ号~", at_sender=True)

    bot_qq = remove_from_whitelist(owner_qq)
    if bot_qq:
        deleted_count = delete_review_records_for_bot(bot_qq)
        await logout_wl.finish(
            f"bot({bot_qq})注销云冰祈了...\n已删除相关审核记录 {deleted_count} 条",
            at_sender=True
        )
    else:
        await logout_wl.finish("你还没有领养云冰祈...", at_sender=True)


# ================== 启动初始化 ==================

driver = get_driver()


@driver.on_startup
async def _init_whitelist():
    _init_tables()
    load_cache()
    await _start_whitelist_web_server()


@driver.on_shutdown
async def _shutdown_whitelist():
    await _stop_whitelist_web_server()
