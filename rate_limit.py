"""全局消息限频：限制同一用户触发处理器的频率（config.global_msg_cd，SU 豁免；0=关闭）。"""

from typing import Optional

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Event as OneBotV11Event
from nonebot.adapters.qq import Event as QQEvent
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor, run_preprocessor

from .config_store import config
from .su_manager import is_su
from .uid_manager import get_uid_by_external_id
from .utils import FreqLimiter

_limiter = FreqLimiter()


def _lookup_uid(event: Event) -> Optional[int]:
    """只读查询消息发送者的统一 UID；未知用户/不支持的事件返回 None。"""
    try:
        external = event.get_user_id()
    except Exception:
        return None
    if isinstance(event, OneBotV11Event):
        return get_uid_by_external_id("onebot", external)
    if isinstance(event, QQEvent):
        return get_uid_by_external_id("qqbot", external)
    return None


def _rate_limit_context(event: Event) -> tuple[int, int]:
    """返回 (冷却秒数, 用户 UID)；不适用限频时 cd 为 0。"""
    cd = int(getattr(config, "global_msg_cd", 0) or 0)
    if cd <= 0 or event.get_type() != "message":
        return 0, 0
    uid = _lookup_uid(event)
    if uid is None or is_su(uid):
        return 0, 0
    return cd, uid


@event_preprocessor
async def global_rate_limit(event: Event):
    """拦截：冷却中的消息静默丢弃（事件级，不落入任何处理器）。"""
    cd, uid = _rate_limit_context(event)
    if cd and not _limiter.check(uid):
        raise IgnoredException(f"[rate_limit] 用户 {uid} {cd} 秒内重复触发已忽略")


@run_preprocessor
async def global_rate_limit_touch(event: Event):
    """计时：只有真正命中处理器的消息才启动冷却窗口。

    check 守卫避免同一事件链上的多个处理器重复刷新（延长）窗口。
    """
    cd, uid = _rate_limit_context(event)
    if cd and _limiter.check(uid):
        _limiter.start_cd(uid, cd)
