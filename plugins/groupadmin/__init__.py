"""群管理子插件（初步）：入群申请自动审批（事件驱动）。

QQBot 侧：GROUP_JOIN_REQUEST 推送事件（mlbot 根目录 qq_bot_api_patch.py
注册的本地补丁，需 .env QQ_BOTS.intent 开启 "group_members": true）。
按官方文档，查询申请列表与事件推送都要求 bot 为群管理员，因此能收到
事件即已具备管理员身份，无需再二次校验。

OneBot 侧：GroupRequestEvent 请求事件实时推送；平台无管理员保证，
审批前用 get_group_member_info 校验 bot 是否为管理员/群主。

放行规则：
1. 自动审批开关开启，且关键词列表非空；
2. 发起审批的 bot 在允许列表中（join_request_bots，可填 QQ 号或官Bot
   appid）：OneBot 的 self_id 即 QQ 号直接比对；官Bot 的 appid 直接
   比对，或经 join_request_bot_qq 绑定的 QQ 号比对（appid 在 bot
   连接时自动登记，只需补填 QQ 号）；
3. 验证内容（验证消息/问答答案）命中任一关键词。

配置见 8889 面板“群管理”分区。
"""

import nonebot
from nonebot import logger, on_notice
from nonebot.adapters import Bot, Event
from nonebot.adapters import qq
from nonebot.rule import Rule

import nonebot.adapters.onebot.v11 as onebot
from ... import config_store
from ...config_store import config as koinori_config
from ... import tools

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


def _allowed_bot_ids() -> set[str]:
    """允许自动审批的 bot 标识集合（QQ 号或官Bot appid，字符串化统一比对）。"""
    return {str(entry) for entry in koinori_config.join_request_bots}


def _auto_approve_enabled() -> bool:
    return bool(
        koinori_config.join_request_auto_approve
        and koinori_config.join_request_keywords
        and koinori_config.join_request_bots
    )


def qqbot_allowed(bot) -> bool:
    """官Bot 是否在自动审批允许列表（appid 直接命中，或绑定的 QQ 号命中）。"""
    allowed = _allowed_bot_ids()
    if bot.self_id in allowed:
        return True
    bound_qq = koinori_config.join_request_bot_qq.get(bot.self_id)
    return bool(bound_qq) and str(bound_qq) in allowed


def onebot_allowed(bot) -> bool:
    """OneBot 的 self_id 即 QQ 号，直接比对白名单。"""
    return str(bot.self_id) in _allowed_bot_ids()


# ================== 官Bot appid 自动登记 ==================


async def register_qqbot_appid(bot: Bot):
    """bot 连接时把官Bot appid 自动登记进 join_request_bot_qq，用户只需补填 QQ 号。"""
    if not isinstance(bot, qq.Bot):
        return
    mapping = {str(k): v for k, v in koinori_config.join_request_bot_qq.items()}
    if bot.self_id in mapping:
        return
    mapping[bot.self_id] = ""
    try:
        config_store.update_config({"join_request_bot_qq": mapping})
        logger.info(
            f"[groupadmin] 已自动登记官Bot appid {bot.self_id}，"
            "请在面板 join_request_bot_qq 中填写其对应 QQ 号"
        )
    except Exception as e:
        logger.warning(f"[groupadmin] 登记官Bot appid 失败: {e}")


nonebot.get_driver().on_bot_connect(register_qqbot_appid)


# ================== 审批核心 ==================


def _remember_processed(request_id: str) -> None:
    if not request_id:
        return
    if len(_processed_requests) > 500:
        _processed_requests.clear()
    _processed_requests.add(request_id)


async def _process_qq_join_request(bot, request: dict, group_openid: str) -> bool:
    """QQ 入群申请审批核心。request 为 JoinRequest.model_dump() 等价 dict。

    官方文档明确查询申请列表与事件推送均要求 bot 为群管理员，
    能收到事件即已具备管理员身份，不再二次校验。
    """
    if not _auto_approve_enabled():
        return False
    if not qqbot_allowed(bot):
        return False
    if request.get("auto_approved"):
        return False  # 平台策略已自动通过，无需处理
    request_id = request.get("join_request_id") or ""
    if request_id and request_id in _processed_requests:
        return False
    member_openid = request.get("member_openid") or ""
    if not member_openid:
        return False
    if not keyword_hit(request, koinori_config.join_request_keywords):
        return False
    ok = await tools.approve_group_join_request(
        bot,
        True,
        group_openid=group_openid,
        member_openid=member_openid,
        join_request_id=request_id,
    )
    if ok:
        _remember_processed(request_id)
        logger.info(
            f"[groupadmin] 已自动放行入群申请："
            f"{request.get('username') or member_openid} -> {group_openid}"
        )
    return ok


async def _ob_bot_is_group_admin(bot: onebot.Bot, group_id: int) -> bool:
    """查 bot 在 OneBot 群内的身份（role: owner/admin/member）。"""
    try:
        info = await bot.get_group_member_info(
            group_id=group_id, user_id=int(bot.self_id)
        )
    except Exception as e:
        logger.debug(f"[groupadmin] 查询bot群身份失败（视为非管理员）: {e}")
        return False
    return is_admin_role(info.get("role"))


async def _process_ob_join_request(bot: onebot.Bot, event: onebot.GroupRequestEvent) -> None:
    """OneBot 入群申请审批。"""
    if not _auto_approve_enabled():
        return
    if not onebot_allowed(bot):
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


# ================== 事件订阅 ==================


_qq_join_request_cls_cache = None


def _qq_join_request_event_cls():
    """GROUP_JOIN_REQUEST 事件类：优先适配器原生（发版后），回退本地补丁。"""
    global _qq_join_request_cls_cache
    if _qq_join_request_cls_cache is None:
        try:
            from nonebot.adapters.qq.event import GroupJoinRequestEvent
        except ImportError:
            from qq_bot_api_patch import GroupJoinRequestEvent
        _qq_join_request_cls_cache = GroupJoinRequestEvent
    return _qq_join_request_cls_cache


async def _is_join_request(event: Event) -> bool:
    """matcher 规则：只放行入群申请类事件，避免无关通知刷日志。"""
    if isinstance(event, onebot.GroupRequestEvent):
        return True
    try:
        return isinstance(event, _qq_join_request_event_cls())
    except Exception:
        return False


join_request_handler = on_notice(rule=Rule(_is_join_request), priority=5, block=False)


@join_request_handler.handle()
async def handle_join_request(bot: Bot, event: Event):
    if isinstance(event, onebot.GroupRequestEvent):
        if isinstance(bot, onebot.Bot):
            await _process_ob_join_request(bot, event)
        return
    if isinstance(bot, qq.Bot):
        await _process_qq_join_request(
            bot, event.model_dump(), event.group_openid
        )
