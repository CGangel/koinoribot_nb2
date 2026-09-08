"""
红包插件 - hongbao

提供发红包、抢红包功能
迁移自旧版 koinoribot
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import time

from nonebot import on_command
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Event, Bot, Message
from nonebot import logger
from nonebot.params import CommandArg, Depends

from ...money import money
from ...utils import FreqLimiter, get_double_mean_money
from ...tools import get_uid, get_group_id_optional, get_sender_nickname
from ...su_manager import check_su_permission, record_su_usage
__plugin_meta__ = PluginMetadata(
    name="hongbao",
    description="红包功能",
    usage="发红包 金额 份数 / 抢红包",
)


@dataclass
class HongbaoSession:
    """红包会话"""
    owner_uid: int           # 发起者 UID
    group_id: str            # 群组 ID
    total_amount: int        # 总金额
    packets: List[int]       # 红包列表
    claimed_uids: List[int]  # 已领取的用户
    create_time: float       # 创建时间
    expire_seconds: int = 600  # 过期时间（秒）
    
    @property
    def is_expired(self) -> bool:
        return time.time() - self.create_time > self.expire_seconds
    
    @property
    def remaining_count(self) -> int:
        return len(self.packets)
    
    @property
    def remaining_amount(self) -> int:
        return sum(self.packets)


# 存储各群的红包会话
# key: group_id, value: HongbaoSession
_sessions: Dict[str, HongbaoSession] = {}

# 频率限制
freq = FreqLimiter(10)


def get_session(group_id: str) -> Optional[HongbaoSession]:
    """获取群红包会话"""
    if group_id not in _sessions:
        return None
    session = _sessions[group_id]
    if session.is_expired:
        # 过期返还
        return_amount = session.remaining_amount
        if return_amount > 0:
            money.of(session.owner_uid).gold += return_amount
        del _sessions[group_id]
        return None
    return session


def close_session(group_id: str):
    """关闭群红包会话"""
    if group_id in _sessions:
        del _sessions[group_id]


def _parse_hongbao_args(message: str) -> tuple[int, int] | None:
    parts = message.split()
    if not parts:
        return None
    amount = int(parts[0])
    packet_count = int(parts[1]) if len(parts) > 1 else 5
    return amount, packet_count


# ===== 发红包命令 =====
fa_hongbao = on_command("发红包", priority=5, block=True)


@fa_hongbao.handle()
async def handle_fa_hongbao(
    event: Event, 
    bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    """处理发红包命令"""
    group_id = get_group_id_optional(event)
    
    if not group_id:
        await fa_hongbao.finish("红包功能仅支持群聊")
    
    if get_session(group_id):
        await fa_hongbao.finish("当前还有没领完的金币红包~")
    
    # 频率限制
    if not freq.check(uid):
        await fa_hongbao.finish("十秒钟之内只能发一个红包")
    
    message = args.extract_plain_text()
    try:
        parsed_args = _parse_hongbao_args(message)
    except ValueError:
        await fa_hongbao.finish("金额和份数必须是数字")
    if parsed_args is None:
        await fa_hongbao.finish("用法: 发红包 金额 [份数]\n例如: 发红包 1000 5")
    amount, num_packets = parsed_args
    
    if num_packets <= 2:
        await fa_hongbao.finish("红包份数至少要3份")
    
    if amount <= num_packets:
        await fa_hongbao.finish("金额太少啦，每份红包至少要1金币")
    
    # SU 红包金额限制
    allowed, reason = check_su_permission(uid, 'hongbao', amount=amount)
    if not allowed:
        await fa_hongbao.finish(reason)
    
    # 检查用户金币
    user_gold = money.gold
    if amount > user_gold:
        await fa_hongbao.finish(f"金币不足！你只有 {user_gold} 金币")
    
    # 扣除金币
    money.gold -= amount
    record_su_usage(uid, 'hongbao', amount)
    
    # 创建红包
    packets = get_double_mean_money(amount, num_packets)
    session = HongbaoSession(
        owner_uid=uid,
        group_id=group_id,
        total_amount=amount,
        packets=packets,
        claimed_uids=[],
        create_time=time.time()
    )
    _sessions[group_id] = session
    
    freq.start_cd(uid)
    
    nickname = await get_sender_nickname(event) or f"用户{uid}"
    await fa_hongbao.finish(
        f"{nickname} 发了一个 {amount} 金币的红包，共 {num_packets} 份~\n发送「抢红包」来领取！"
    )


# ===== 抢红包命令 =====
qiang_hongbao = on_command("抢红包", priority=5, block=True)


@qiang_hongbao.handle()
async def handle_qiang_hongbao(
    event: Event, 
    bot: Bot,
    uid: int = Depends(get_uid)
):
    """处理抢红包命令"""
    group_id = get_group_id_optional(event)
    
    if not group_id:
        return
    
    session = get_session(group_id)
    if not session:
        return
    
    # 检查是否已抢过
    if uid in session.claimed_uids:
        await qiang_hongbao.finish("你已经抢过红包了！")
    
    # 抢红包
    if session.packets:
        amount = session.packets.pop()
        session.claimed_uids.append(uid)
        money.gold += amount
        
        await qiang_hongbao.send(f"恭喜抢到 {amount} 金币~")
        
        # 检查是否抢完
        if not session.packets:
            close_session(group_id)
            await qiang_hongbao.send("红包领完啦~")
