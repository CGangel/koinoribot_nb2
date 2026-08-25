"""
AI 画图插件

使用 DeepSeek V4 Flash 将用户的模糊描述翻译为英文图像提示词，
再调用 GPT-Image-2 生成图像。支持附带参考图进行图片编辑。

费用：100000 金币/次，每日限制 5 次/UID。
"""

import sqlite3
import time
import asyncio
import aiohttp
from dataclasses import dataclass
from nonebot import on_command, get_driver
from nonebot.adapters import Event, Bot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.log import logger
from nonebot.params import CommandArg, Depends
from nonebot.plugin import PluginMetadata

from ...money import money, get_database_path
from ...su_manager import is_su_contributor
from ...tools import get_uid, build_image_msg, is_onebot, is_qqbot
from ...koinori_config import config as koinori_config
from ...utils import FreqLimiter
from ._image_meta import detect_image_upload_meta
from ._image_response import (
    ImageResponseParseError,
    decode_base64_image,
    extract_image_reference,
    format_response_body_dump,
    format_response_json_dump,
    parse_image_response_json,
)

__plugin_meta__ = PluginMetadata(
    name="ai_draw",
    description="AI画图 - DeepSeek翻译提示词 + GPT-Image-2生成图像",
    usage=(
        "冰祈画图 <描述>    生成图像\n"
        "冰祈画图high <描述>    生成高质量图像，仅限 level 0 SU\n"
        "冰祈修图 <描述> + 图片  编辑图像\n"
        "冰祈修图high <描述> + 图片  高质量编辑图像，仅限 level 0 SU\n"
        "全局重置画图次数 / 重置画图次数 <uid>  管理日限"
    ),
)

driver = get_driver()

# 频率限制：每用户 30 秒一次
draw_limiter = FreqLimiter(30)

# 正在画图中的用户集合，用于防止同一用户并发画图
_drawing_uids: set[int] = set()

# 注册命令
draw_cmd = on_command(
    "冰祈画图",
    aliases={"梦灵画图"},
    priority=5,
    block=True,
)
draw_high_cmd = on_command(
    "冰祈画图high",
    aliases={"梦灵画图high"},
    priority=5,
    block=True,
)
edit_cmd = on_command(
    "冰祈修图", aliases={"梦灵修图"}, priority=5, block=True
)
edit_high_cmd = on_command(
    "冰祈修图high", aliases={"梦灵修图high"}, priority=5, block=True
)
reset_all_usage_cmd = on_command("全局重置画图次数", priority=5, block=True)
reset_usage_cmd = on_command("重置画图次数", priority=5, block=True)

# DeepSeek 系统提示词
PROMPT_TRANSLATE_SYSTEM = """You are a professional image prompt engineer. Your task is to convert the user's Chinese description into a concise, high-quality English prompt suitable for AI image generation (DALL-E / GPT-Image-2).

Rules:
1. Output ONLY the English prompt, nothing else — no quotes, no prefixes, no explanations.
2. The prompt should be vivid and descriptive, including style, lighting, composition details.
3. Keep it under 200 characters.
4. Preserve the user's intent faithfully — do not add concepts the user didn't mention."""

# ═══════════════ SQLite 日限 ═══════════════

@dataclass(frozen=True)
class DrawPayment:
    success: bool
    used_free_draw: bool = False

    def __bool__(self) -> bool:
        return self.success


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_database_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_ai_draw_usage_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_draw_usage (
                uid INTEGER PRIMARY KEY,
                date TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL DEFAULT 0,
                free_draw_count INTEGER NOT NULL DEFAULT 0
            )"""
    )


def init_db() -> None:
    with _get_db() as conn:
        _ensure_ai_draw_usage_schema(conn)
        conn.commit()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


async def check_daily_limit(uid: int) -> bool:
    """检查日限。跨天视为已清零。返回 True 表示未超限。"""
    loop = __import__("asyncio").get_event_loop()
    today = _today()

    def _do():
        with _get_db() as conn:
            _ensure_ai_draw_usage_schema(conn)
            row = conn.execute(
                "SELECT date, count FROM ai_draw_usage WHERE uid=?", (uid,)
            ).fetchone()

            if row is None:
                return True

            if row["date"] != today:
                return True

            return row["count"] < koinori_config.daily_limit

    return await loop.run_in_executor(None, _do)


def _get_free_draw_count_sync(uid: int) -> int:
    with _get_db() as conn:
        _ensure_ai_draw_usage_schema(conn)
        row = conn.execute(
            "SELECT free_draw_count FROM ai_draw_usage WHERE uid=?", (uid,)
        ).fetchone()
        return 0 if row is None else int(row["free_draw_count"] or 0)


def _add_free_draw_count_sync(uid: int, amount: int) -> int:
    amount = max(0, int(amount))
    if amount == 0:
        return _get_free_draw_count_sync(uid)

    with _get_db() as conn:
        _ensure_ai_draw_usage_schema(conn)
        row = conn.execute("SELECT uid FROM ai_draw_usage WHERE uid=?", (uid,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO ai_draw_usage (uid, free_draw_count) VALUES (?, ?)",
                (uid, amount),
            )
        else:
            conn.execute(
                "UPDATE ai_draw_usage SET free_draw_count=free_draw_count+? WHERE uid=?",
                (amount, uid),
            )
        conn.commit()

    return _get_free_draw_count_sync(uid)


async def get_free_draw_count(uid: int) -> int:
    """获取用户剩余的免费画图次数。"""
    loop = __import__("asyncio").get_event_loop()
    return await loop.run_in_executor(None, _get_free_draw_count_sync, uid)


async def add_free_draw_count(uid: int, amount: int) -> int:
    """增加长期有效的免费画图次数，返回增加后的剩余次数。"""
    loop = __import__("asyncio").get_event_loop()
    return await loop.run_in_executor(None, _add_free_draw_count_sync, uid, amount)


async def record_draw_success(uid: int) -> None:
    """成功生成图片后递增今日次数。level 0 SU 不计入日限。"""
    if is_su_contributor(uid):
        return

    loop = __import__("asyncio").get_event_loop()
    today = _today()

    def _do():
        with _get_db() as conn:
            _ensure_ai_draw_usage_schema(conn)
            row = conn.execute(
                "SELECT date FROM ai_draw_usage WHERE uid=?", (uid,)
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO ai_draw_usage (uid, date, count) VALUES (?, ?, 1)",
                    (uid, today),
                )
            elif row["date"] != today:
                conn.execute(
                    "UPDATE ai_draw_usage SET date=?, count=1 WHERE uid=?",
                    (today, uid),
                )
            else:
                conn.execute(
                    "UPDATE ai_draw_usage SET count=count+1 WHERE uid=? AND date=?",
                    (uid, today),
                )
            conn.commit()

    await loop.run_in_executor(None, _do)


async def reset_draw_usage(target_uid: int | None = None) -> int:
    """重置画图次数。target_uid 为 None 时重置全部。返回影响行数。"""
    loop = __import__("asyncio").get_event_loop()

    def _do():
        with _get_db() as conn:
            _ensure_ai_draw_usage_schema(conn)
            if target_uid is None:
                cursor = conn.execute(
                    "UPDATE ai_draw_usage SET date='', count=0 WHERE date<>'' OR count<>0"
                )
            else:
                cursor = conn.execute(
                    "UPDATE ai_draw_usage SET date='', count=0 WHERE uid=? AND (date<>'' OR count<>0)",
                    (target_uid,),
                )
            conn.commit()
            return cursor.rowcount

    return await loop.run_in_executor(None, _do)


# ═══════════════ 启动初始化 ═══════════════

@driver.on_startup
async def on_startup():
    """初始化数据库"""
    loop = __import__("asyncio").get_event_loop()
    await loop.run_in_executor(None, init_db)
    logger.info(
        f"ai_draw 初始化完成 "
        f"(deepseek_key={'***' if koinori_config.deepseek_api_key else '未配置'}, "
        f"gpt_image_key={'***' if koinori_config.gpt_image_api_key else '未配置'})"
    )


# ═══════════════ 图片提取 ═══════════════

def _extract_onebot_image_url(event: Event) -> str | None:
    for segment in event.message:
        if segment.type == "image":
            return segment.data.get("url") or segment.data.get("file")
    return None


def _extract_qqbot_attachment_url(event: Event) -> str | None:
    for attachment in getattr(event, "attachments", None) or ():
        attachment_url = getattr(attachment, "url", None)
        if attachment_url:
            return attachment_url
    return None


def _extract_qqbot_message_image_url(event: Event) -> str | None:
    for segment in event.message:
        if segment.type in {"image", "attachment"}:
            return segment.data.get("url")
    return None


def _extract_image_url(event: Event) -> str | None:
    if is_onebot(event):
        return _extract_onebot_image_url(event)
    if is_qqbot(event):
        return (
            _extract_qqbot_attachment_url(event)
            or _extract_qqbot_message_image_url(event)
        )
    return None


async def _download_reference_image(image_url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url, timeout=30) as response:
            if response.status != 200:
                logger.error(
                    f"下载参考图失败: HTTP {response.status}, url={image_url[:80]}"
                )
                raise RuntimeError(f"下载参考图失败: {response.status}")
            return await response.read()


async def extract_image(event: Event) -> bytes | None:
    """从消息中提取第一张图片并下载，返回 bytes；无图片则返回 None"""
    image_url = _extract_image_url(event)
    if not image_url:
        return None
    return await _download_reference_image(image_url)


# ═══════════════ DeepSeek 翻译 ═══════════════

async def translate_prompt(api_key: str, user_text: str) -> str:
    """调用 DeepSeek V4 Flash 翻译提示词"""
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": PROMPT_TRANSLATE_SYSTEM},
            {"role": "user", "content": user_text},
        ],
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "stream": False,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"DeepSeek API error: {resp.status} {text}")
                raise RuntimeError(f"DeepSeek API 返回错误: {resp.status}")

            data = await resp.json()
            english_prompt = data["choices"][0]["message"]["content"].strip()
            logger.info(f"DeepSeek translated prompt: {english_prompt}")
            return english_prompt


# ═══════════════ GPT-Image-2 ═══════════════

# AI画图超时时间：10分钟（600秒）
IMAGE_API_TIMEOUT = aiohttp.ClientTimeout(total=600, connect=30, sock_read=600)


def _get_image_response_format() -> str:
    value = getattr(koinori_config, "gpt_image_response_format", "url")
    normalized = str(value or "url").strip().lower()
    if normalized in {"base64", "b64", "b64_json"}:
        return "b64_json"
    if normalized != "url":
        logger.warning(
            f"未知的 gpt_image_response_format={value!r}，已按 url 处理"
        )
    return "url"


async def _read_response_body(resp: aiohttp.ClientResponse, label: str) -> bytes:
    try:
        return await resp.read()
    except asyncio.TimeoutError as e:
        raise RuntimeError(
            f"{label} 响应读取超时（HTTP {resp.status} 已返回，图片可能已生成并扣费，"
            "但本地没有收到完整响应体）"
        ) from e
    except aiohttp.ClientError as e:
        raise RuntimeError(
            f"{label} 响应读取失败（图片可能已生成）: {type(e).__name__}: {e}"
        ) from e


def _append_response_debug(message: str, dump: str) -> str:
    return f"{message}\n\n接收到的响应内容：\n```text\n{dump}\n```"


async def _consume_image_response(
    session: aiohttp.ClientSession,
    resp: aiohttp.ClientResponse,
    label: str,
) -> bytes:
    body = await _read_response_body(resp, label)
    content_type = resp.headers.get("Content-Type", "")

    if resp.status != 200:
        dump = format_response_body_dump(body, content_type)
        logger.error(f"{label} API error: {resp.status}\n{dump}")
        raise RuntimeError(
            _append_response_debug(f"{label} API 返回错误: {resp.status}", dump)
        )

    try:
        data = parse_image_response_json(body, content_type)
    except ImageResponseParseError as e:
        dump = format_response_body_dump(body, content_type)
        logger.error(f"{label} response parse failed, raw response:\n{dump}")
        message = f"{label} 响应解析失败（图片可能已生成）: {e}"
        raise RuntimeError(_append_response_debug(message, dump)) from e

    try:
        image_ref = extract_image_reference(data, _get_image_response_format())
        if image_ref.url:
            return await _download_image_url(session, image_ref.url)
        return decode_base64_image(image_ref.b64_json or "")
    except RuntimeError as e:
        dump = format_response_json_dump(data)
        logger.error(f"{label} response had no usable image data:\n{dump}")
        raise RuntimeError(_append_response_debug(str(e), dump)) from e


async def _download_image_url(session: aiohttp.ClientSession, image_url: str) -> bytes:
    last_error = ""
    for attempt in range(1, 4):
        try:
            async with session.get(image_url) as img_resp:
                body = await _read_response_body(img_resp, "生成图片下载")
                if img_resp.status == 200:
                    return body

                dump = format_response_body_dump(body, img_resp.headers.get("Content-Type", ""))
                last_error = f"HTTP {img_resp.status}\n{dump}"
                if img_resp.status < 500:
                    break
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            detail = str(e) or type(e).__name__
            last_error = f"{type(e).__name__}: {detail}"

        if attempt < 3:
            await asyncio.sleep(attempt)

    raise RuntimeError(f"下载生成的图片失败（已重试 3 次）: {last_error}")


async def generate_image(
    api_key: str,
    prompt: str,
    size: str = "auto",
    quality: str = "medium",
) -> bytes:
    """调用 GPT-Image-2 文本生图"""
    url = f"{koinori_config.gpt_image_api_base_url}/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": koinori_config.gpt_image_model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "response_format": _get_image_response_format(),
    }

    async with aiohttp.ClientSession(timeout=IMAGE_API_TIMEOUT) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            return await _consume_image_response(session, resp, "GPT-Image-2")


async def generate_image_edit(
    api_key: str,
    prompt: str,
    image_bytes: bytes,
    size: str = "auto",
    quality: str = "medium",
) -> bytes:
    """调用 GPT-Image-2 图片编辑（含参考图）"""
    url = f"{koinori_config.gpt_image_api_base_url}/images/edits"

    form_data = aiohttp.FormData()
    form_data.add_field("model", koinori_config.gpt_image_model)
    form_data.add_field("prompt", prompt)
    form_data.add_field("size", size)
    form_data.add_field("quality", quality)
    form_data.add_field("response_format", _get_image_response_format())
    image_filename, image_content_type = detect_image_upload_meta(image_bytes)
    form_data.add_field(
        "image",
        image_bytes,
        filename=image_filename,
        content_type=image_content_type,
    )
    logger.debug(
        "GPT-Image-2 Edit reference image: "
        f"filename={image_filename}, content_type={image_content_type}, bytes={len(image_bytes)}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    async with aiohttp.ClientSession(timeout=IMAGE_API_TIMEOUT) as session:
        async with session.post(url, headers=headers, data=form_data) as resp:
            return await _consume_image_response(session, resp, "GPT-Image-2 Edit")


# ═══════════════ 费用 & 日限检查 ═══════════════

async def check_quota_and_balance(uid: int, cmd, allow_free_draw: bool = True) -> bool:
    """检查日限和余额，任意不满足则发送提示并返回 False。"""
    # level 0 SU 不受日限
    if not is_su_contributor(uid):
        ok = await check_daily_limit(uid)
        if not ok:
            await cmd.finish(f"你一天只能画 {koinori_config.daily_limit} 张图，明天再来吧~", at_sender=True)
            return False

    if allow_free_draw and await get_free_draw_count(uid) > 0:
        return True

    if not koinori_config.enable_gold_aidraw:
        await cmd.finish("当前仅允许使用免费画图次数，请先获取免费次数后再来画图~", at_sender=True)
        return False

    wallet = money.of(uid)
    user_gold = wallet.gold
    if user_gold < koinori_config.draw_cost:
        await cmd.finish(
            f"金币不足！画一张图需要 {koinori_config.draw_cost} 金币，你当前只有 {user_gold} 金币。",
            at_sender=True,
        )
        return False

    return True


def pay_draw_cost(uid: int, allow_free_draw: bool = True) -> DrawPayment:
    if allow_free_draw:
        with _get_db() as conn:
            _ensure_ai_draw_usage_schema(conn)
            cursor = conn.execute(
                """
                UPDATE ai_draw_usage
                SET free_draw_count=free_draw_count-1
                WHERE uid=? AND free_draw_count>0
                """,
                (uid,),
            )
            conn.commit()
            if cursor.rowcount:
                return DrawPayment(success=True, used_free_draw=True)

    if not koinori_config.enable_gold_aidraw:
        return DrawPayment(success=False)

    money.of(uid).gold -= koinori_config.draw_cost
    return DrawPayment(success=True)


def refund_draw_payment(uid: int, payment: DrawPayment) -> str:
    if payment.used_free_draw:
        _add_free_draw_count_sync(uid, 1)
        return "已返还免费画图次数。"

    money.of(uid).gold += koinori_config.draw_cost
    return "已退还金币。"


def format_draw_progress(
    uid: int,
    default_text: str,
    payment: DrawPayment,
    progress_text: str | None = None,
) -> str:
    if not payment.used_free_draw:
        return progress_text or default_text

    title = (progress_text or default_text).splitlines()[0]
    remaining = _get_free_draw_count_sync(uid)
    return f"{title}\n已使用 1 次免费画图次数（剩余 {remaining} 次）"


# ═══════════════ 画图处理 ═══════════════

def _build_text_image_message(text: str, image_msg) -> Message:
    """构建文本和图片合并的一条 OneBot 消息。"""
    msg = Message()
    if text.strip():
        msg.append(MessageSegment.text(f"{text.rstrip()}\n"))
    msg.append(image_msg)
    return msg


async def ensure_draw_available(event: Event, uid: int, cmd) -> None:
    """检查文本生图功能是否可用。"""
    if not koinori_config.gpt_image_api_key:
        await cmd.finish("未配置 GPT-Image-2 API Key，请联系主人配置~", at_sender=True)
#    if is_qqbot(event):
#        await cmd.finish("AI画图功能暂不支持QQbot~", at_sender=True)
    if not koinori_config.ai_draw_enable and not is_su_contributor(uid):
        await cmd.finish("AI画图功能维护中，暂时不可用~", at_sender=True)


async def ensure_edit_available(event: Event, uid: int, cmd) -> None:
    """检查图片编辑功能是否可用。"""
    if not koinori_config.gpt_image_api_key:
        await cmd.finish("未配置 GPT-Image-2 API Key，请联系主人配置~", at_sender=True)
#    if is_qqbot(event):
#        await cmd.finish("AI修图功能暂不支持QQbot~", at_sender=True)
    if not koinori_config.ai_draw_enable and not is_su_contributor(uid):
        await cmd.finish("AI修图功能维护中，暂时不可用~", at_sender=True)


async def ensure_high_quality_allowed(uid: int, cmd) -> None:
    """高质量画图/修图仅限 level 0 SU。"""
    if not is_su_contributor(uid):
        await cmd.finish("高质量AI绘图/修图仅限权限等级为 0 的 SU 使用。", at_sender=True)


async def do_draw(
    event: Event,
    uid: int,
    user_text: str,
    cmd=None,
    progress_text: str | None = None,
    success_text: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> None:
    """执行文本生图"""
    if cmd is None:
        cmd = draw_cmd
    draw_size = koinori_config.ai_draw_size if size is None else size
    draw_quality = koinori_config.aidraw_quality if quality is None else quality

    if not await check_quota_and_balance(uid, cmd):
        return

    if not draw_limiter.check(uid):
        left = round(draw_limiter.left_time(uid))
        await cmd.finish(f"画图太频繁啦，请等待 {left}s 后再试~", at_sender=True)
    draw_limiter.start_cd(uid)

    if uid in _drawing_uids:
        await cmd.finish("你有一个画图请求正在处理中，请等待完成后再试~", at_sender=True)

    payment = pay_draw_cost(uid)
    if not payment:
        await cmd.finish("扣除金币失败，请稍后再试。", at_sender=True)

    await cmd.send(
        format_draw_progress(
            uid,
            f"少女画图中…\n已扣除{koinori_config.draw_cost}金币",
            payment,
            progress_text,
        )
    )

    _drawing_uids.add(uid)
    try:
        image_bytes = await generate_image(
            koinori_config.gpt_image_api_key,
            user_text,
            size=draw_size,
            quality=draw_quality,
        )
        image_msg = build_image_msg(event, image_bytes)
    except RuntimeError as e:
        refund_text = refund_draw_payment(uid, payment)
        logger.error(f"画图失败: {e}")
        await cmd.finish(f"画图失败: {e}\n{refund_text}", at_sender=True)
    except Exception as e:
        refund_text = refund_draw_payment(uid, payment)
        logger.error(f"画图异常: {type(e).__name__}: {e}")
        await cmd.finish(f"画图出错了: {e}\n{refund_text}", at_sender=True)
    else:
        await record_draw_success(uid)
        try:
            base_text = success_text or f"提示词：\n{user_text}"
            size_quality_info = f"\n预期尺寸：{draw_size} | 质量：{draw_quality}"
            result_msg = _build_text_image_message(
                base_text + size_quality_info,
                image_msg,
            )
            await cmd.send(result_msg, at_sender=True)
        except ActionFailed:
            logger.warning("发送图片超时，但图片可能已送达")
        await cmd.finish()
    finally:
        _drawing_uids.discard(uid)


async def do_edit(
    event: Event,
    uid: int,
    user_text: str,
    cmd=None,
    quality: str | None = None,
) -> None:
    """执行图片编辑"""
    if cmd is None:
        cmd = edit_cmd
    edit_quality = koinori_config.aidraw_quality if quality is None else quality

    try:
        ref_image = await extract_image(event)
    except RuntimeError as e:
        await cmd.finish(f"获取参考图失败: {e}", at_sender=True)
        return

    if not ref_image:
        await cmd.finish(
            "请附带一张参考图再使用修图命令哦~\n例: 发送冰祈修图[附带图片] 把背景换成赛博朋克风格",
            at_sender=True,
        )
        return

    if not user_text.strip():
        await cmd.finish("请输入修图描述，例如: 冰祈修图[附带图片] 把猫变成金色的", at_sender=True)
        return

    if not await check_quota_and_balance(uid, cmd, allow_free_draw=False):
        return

    if not draw_limiter.check(uid):
        left = round(draw_limiter.left_time(uid))
        await cmd.finish(f"修图太频繁啦，请等待 {left}s 后再试~", at_sender=True)
    draw_limiter.start_cd(uid)

    if uid in _drawing_uids:
        await cmd.finish("你有一个画图请求正在处理中，请等待完成后再试~", at_sender=True)

    prompt = user_text.strip()
    payment = pay_draw_cost(uid, allow_free_draw=False)
    if not payment:
        await cmd.finish("扣除金币失败，请稍后再试。", at_sender=True)

    edit_size = koinori_config.ai_draw_size
    user_gold_after = money.of(uid).gold
    await cmd.send(f"少女修图中…\n已扣除 {koinori_config.draw_cost} 金币 (剩余 {user_gold_after})")

    _drawing_uids.add(uid)
    try:
        image_bytes = await generate_image_edit(
            koinori_config.gpt_image_api_key,
            prompt,
            ref_image,
            size=edit_size,
            quality=edit_quality,
        )
        image_msg = build_image_msg(event, image_bytes)
    except RuntimeError as e:
        refund_text = refund_draw_payment(uid, payment)
        logger.error(f"修图失败: {e}")
        await cmd.finish(f"修图失败: {e}\n{refund_text}", at_sender=True)
    except Exception as e:
        refund_text = refund_draw_payment(uid, payment)
        logger.error(f"修图异常: {type(e).__name__}: {e}")
        await cmd.finish(f"修图出错了: {e}\n{refund_text}", at_sender=True)
    else:
        await record_draw_success(uid)
        try:
            result_msg = _build_text_image_message(
                f"提示词：\n{prompt}\n预期尺寸：{edit_size} | 质量：{edit_quality}",
                image_msg,
            )
            await cmd.send(result_msg, at_sender=True)
        except ActionFailed:
            logger.warning("修图发送图片超时，但图片可能已送达")
        await cmd.finish()
    finally:
        _drawing_uids.discard(uid)


# ═══════════════ 命令入口 ═══════════════


def _is_level0_su(uid: int) -> bool:
    return is_su_contributor(uid)


@reset_all_usage_cmd.handle()
async def handle_reset_all_usage(uid: int = Depends(get_uid)):
    if not _is_level0_su(uid):
        await reset_all_usage_cmd.finish("权限不足，仅限权限等级为 0 的 SU 使用。", at_sender=True)

    affected = await reset_draw_usage()
    await reset_all_usage_cmd.finish(f"已重置全部用户今日画图次数（更新 {affected} 条记录）。", at_sender=True)


@reset_usage_cmd.handle()
async def handle_reset_usage(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
):
    if not _is_level0_su(uid):
        await reset_usage_cmd.finish("权限不足，仅限权限等级为 0 的 SU 使用。", at_sender=True)

    arg_text = args.extract_plain_text().strip()
    if not arg_text:
        await reset_usage_cmd.finish("格式：重置画图次数 uid", at_sender=True)

    try:
        target_uid = int(arg_text.split()[0])
    except ValueError:
        await reset_usage_cmd.finish("UID 必须是整数。", at_sender=True)

    affected = await reset_draw_usage(target_uid)
    if affected:
        await reset_usage_cmd.finish(f"已重置 UID:{target_uid} 今日画图次数。", at_sender=True)
    await reset_usage_cmd.finish(f"UID:{target_uid} 今日画图次数已经是 0。", at_sender=True)


@draw_cmd.handle()
async def handle_draw(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
):
#    if not koinori_config.deepseek_api_key:
#        await draw_cmd.finish("未配置 DeepSeek API Key，请联系主人配置~", at_sender=True)
    await handle_draw_command(event, uid, args, draw_cmd)


@draw_high_cmd.handle()
async def handle_draw_high(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
):
#    if not koinori_config.deepseek_api_key:
#        await draw_high_cmd.finish("未配置 DeepSeek API Key，请联系主人配置~", at_sender=True)
    await ensure_high_quality_allowed(uid, draw_high_cmd)
    await handle_draw_command(
        event,
        uid,
        args,
        draw_high_cmd,
        quality=koinori_config.aidraw_high_quality,
    )


async def handle_draw_command(
    event: Event,
    uid: int,
    args: Message,
    cmd,
    quality: str | None = None,
) -> None:
    await ensure_draw_available(event, uid, cmd)

    user_text = args.extract_plain_text().strip()
    if not user_text:
        await cmd.finish("请输入画图描述，例如: 冰祈画图 一只可爱的猫", at_sender=True)

    await do_draw(event, uid, user_text, cmd=cmd, quality=quality)


@edit_cmd.handle()
async def handle_edit(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
):
#    if not koinori_config.deepseek_api_key:
#        await edit_cmd.finish("未配置 DeepSeek API Key，请联系主人配置~", at_sender=True)
    await handle_edit_command(event, uid, args, edit_cmd)


@edit_high_cmd.handle()
async def handle_edit_high(
    event: Event,
    bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
):
#    if not koinori_config.deepseek_api_key:
#        await edit_high_cmd.finish("未配置 DeepSeek API Key，请联系主人配置~", at_sender=True)
    await ensure_high_quality_allowed(uid, edit_high_cmd)
    await handle_edit_command(
        event,
        uid,
        args,
        edit_high_cmd,
        quality=koinori_config.aidraw_high_quality,
    )


async def handle_edit_command(
    event: Event,
    uid: int,
    args: Message,
    cmd,
    quality: str | None = None,
) -> None:
    await ensure_edit_available(event, uid, cmd)
    user_text = args.extract_plain_text().strip()
    await do_edit(event, uid, user_text, cmd=cmd, quality=quality)
