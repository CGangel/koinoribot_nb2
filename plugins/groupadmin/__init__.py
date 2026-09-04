"""群管理子插件（初步）：入群申请自动审批。

QQBot 侧：官Bot 在适配器 1.7.2 下没有入群申请推送事件，采用定时轮询
（默认 60 秒）。群 openid 从群消息事件自动收集（openid 按 bot 隔离）；
拉取列表、审批接口均要求 bot 为群管理员，身份不满足时跳过该群。

OneBot 侧：GroupRequestEvent 请求事件实时推送，命中规则即时审批。

放行规则（两端一致）：开关开启 + 验证内容（验证消息/问答答案）命中
任一关键词 + bot 为该群管理员/群主。

配置见 8889 面板“群管理”分区：
- join_request_auto_approve  自动审批开关
- join_request_keywords      关键词列表
"""

from nonebot import get_bots, logger, on_notice, require
from nonebot.adapters import Bot, Event
from nonebot.adapters import qq
from nonebot.message import event_preprocessor

import nonebot.adapters.onebot.v11 as onebot
from ...config_store import config as koinori_config
from ... import tools

POLL_INTERVAL_SECONDS = 60

# 群消息事件自动收集的群（openid 按 bot 隔离）：bot self_id -> {group_openid}
_known_groups: dict[str, set[str]] = {}
# 已处理过的申请，避免重复审批
_processed_requests: set[str] = set()


# ================== 纯逻辑（便于测试） ==================


def join_request_texts(request: dict) -> str:
    """拼接用于关键词匹配的验证文本（验证消息 + 问答的问题与答案）。"""
    verify = request.get("verify_info") or {}
    parts = [str(verify.get("verify_message") or "")]
    for qa in verify.get("review_qa_list") or []:
        parts.append(str(qa.get("question") or ""))
        parts.append(str(qa.get("answer") or ""))
    return "\n".join(part for part in parts if part)


def keyword_hit(request: dict, keywords) -> bool:
    """验证内容是否命中任一关键词（关键词为空视为不命中）。"""
    if not keywords:
        return False
    text = join_request_texts(request)
    return any(str(keyword) in text for keyword in keywords if str(keyword))


def is_admin_role(role) -> bool:
    """群身份是否为管理员/群主（QQBot member_role 与 OneBot role 通用）。"""
    return role in ("admin", "owner")


# ================== QQBot 侧：轮询审批 ==================


@event_preprocessor
async def _track_qq_groups(event: Event, bot: Bot):
    """从群消息事件自动收集需要轮询的群 openid。"""
    if not isinstance(bot, qq.Bot):
        return
    group_openid = getattr(event, "group_openid", None)
    if group_openid:
        _known_groups.setdefault(bot.self_id, set()).add(group_openid)


async def _bot_is_group_admin(bot, group_openid: str) -> bool:
    try:
        state = await bot.get_group_bot_state(group_id=group_openid)
    except Exception as e:
        logger.debug(f"[groupadmin] 查询bot群身份失败（视为非管理员）: {e}")
        return False
    return is_admin_role(state.member_role)


def _remember_processed(request_id: str) -> None:
    if not request_id:
        return
    if len(_processed_requests) > 500:
        _processed_requests.clear()
    _processed_requests.add(request_id)


async def process_pending_requests(bot) -> list[str]:
    """拉取并审批单个 QQBot 全部已知群的入群申请，返回放行的申请标识列表。"""
    if not koinori_config.join_request_auto_approve:
        return []
    keywords = koinori_config.join_request_keywords
    if not keywords:
        return []

    approved: list[str] = []
    for group_openid in _known_groups.get(bot.self_id, ()):
        try:
            result = await bot.get_group_join_request_list(group_id=group_openid)
        except Exception as e:
            # 列表接口要求群管理员；无权限/临时故障的群静默跳过
            logger.debug(f"[groupadmin] 拉取入群申请失败（跳过该群）: {e}")
            continue

        hits = [
            request
            for request in (r.model_dump() for r in result.requests)
            if keyword_hit(request, keywords)
            and (request.get("join_request_id") or "") not in _processed_requests
        ]
        if not hits:
            continue

        if not await _bot_is_group_admin(bot, group_openid):
            logger.info(
                f"[groupadmin] 群 {group_openid} 有命中关键词的入群申请，"
                "但bot不是管理员，跳过自动放行"
            )
            continue

        for request in hits:
            request_id = request.get("join_request_id") or ""
            member_openid = request.get("member_openid") or ""
            ok = await tools.approve_group_join_request(
                bot,
                True,
                group_openid=group_openid,
                member_openid=member_openid,
                join_request_id=request_id,
            )
            if ok:
                _remember_processed(request_id)
                approved.append(request_id or member_openid)
                logger.info(
                    f"[groupadmin] 已自动放行入群申请："
                    f"{request.get('username') or member_openid} -> {group_openid}"
                )
    return approved


try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler

    @scheduler.scheduled_job(
        "interval",
        seconds=POLL_INTERVAL_SECONDS,
        id="groupadmin_join_request_poll",
        misfire_grace_time=30,
    )
    async def _poll_join_requests():
        for bot in get_bots().values():
            if isinstance(bot, qq.Bot):
                try:
                    await process_pending_requests(bot)
                except Exception as e:
                    logger.debug(f"[groupadmin] 入群申请轮询异常: {e}")

except Exception as e:
    logger.warning(
        f"[groupadmin] 入群申请轮询任务注册失败（需要 nonebot_plugin_apscheduler）: {e}"
    )


# ================== OneBot 侧：请求事件实时审批 ==================


async def _ob_bot_is_group_admin(bot: onebot.Bot, group_id: int) -> bool:
    try:
        info = await bot.get_group_member_info(
            group_id=group_id, user_id=int(bot.self_id)
        )
    except Exception as e:
        logger.debug(f"[groupadmin] 查询bot群身份失败（视为非管理员）: {e}")
        return False
    return is_admin_role(info.get("role"))


ob_group_request = on_notice(priority=5, block=False)


@ob_group_request.handle()
async def handle_ob_group_request(
    bot: onebot.Bot, event: onebot.GroupRequestEvent
):
    if not koinori_config.join_request_auto_approve:
        return
    if event.sub_type != "add":
        return
    request = {"verify_info": {"verify_message": event.comment or ""}}
    if not keyword_hit(request, koinori_config.join_request_keywords):
        return
    if not await _ob_bot_is_group_admin(bot, event.group_id):
        logger.info(
            f"[groupadmin] 群 {event.group_id} 有命中关键词的入群申请，"
            "但bot不是管理员，跳过自动放行"
        )
        return
    try:
        await bot.set_group_add_request(flag=event.flag, sub_type="add", approve=True)
        logger.info(
            f"[groupadmin] 已自动放行入群申请：{event.user_id} -> {event.group_id}"
        )
    except Exception as e:
        logger.warning(f"[groupadmin] 自动放行入群申请失败: {e}")
