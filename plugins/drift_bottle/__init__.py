"""漂流瓶插件 - drift_bottle

自旧版钓鱼插件完整迁出、与钓鱼玩法解耦：
- 获取：买漂流瓶（金币） / 合成漂流瓶（星星，价格可配置，默认 10000）
- 扔漂流瓶（内容 ≤60 字）、捡漂流瓶（随机，消耗星星，默认 1000；
  池中至少 5 个，合并转发展示正文与评论）、评论漂流瓶（金币）、
  漂流瓶数量
- 管理（仅 SU）：捡指定漂流瓶 / 删除漂流瓶
旧版依赖的水之心体系随旧版钓鱼移除；漂流瓶持有数存 bottle_inventory 表。
"""

import datetime

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg, Depends
from nonebot.plugin import PluginMetadata

from ...config_store import config
from ...nickname import get_user_nickname
from ...su_manager import is_su
from ...tools import build_forward_chain, get_uid, send_group_forward_msg
from ...utils import FreqLimiter
from .service import BottleService

__plugin_meta__ = PluginMetadata(
    name="drift_bottle",
    description="漂流瓶系统（与钓鱼解耦） - 扔/捡/评论漂流瓶",
    usage="买漂流瓶 / 合成漂流瓶 / 扔漂流瓶 / 捡漂流瓶 / 评论漂流瓶 等",
)

# 冷却限制器（时长可热更新配置，修改需重启生效）
throw_freq = FreqLimiter(config.throw_cool_time)
get_freq = FreqLimiter(config.salvage_cool_time)
comm_freq = FreqLimiter(config.comment_cool_time)


def _fmt_time(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _thrower_name(uid) -> str:
    uid = uid if isinstance(uid, int) else str(uid)
    if isinstance(uid, str) and uid.isdigit():
        return get_user_nickname(int(uid)) or f"UID {uid}"
    return str(uid)


# ===== 漂流瓶帮助 =====
bottle_help_cmd = on_command("漂流瓶帮助", priority=5, block=True)


def _build_help_text() -> str:
    return f"""【漂流瓶】
漂流瓶帮助 —— 本帮助
买漂流瓶 [数量] —— 花金币购买（{config.bottle_price}金币/个，一次最多10个）
合成漂流瓶 [数量] —— 花星星合成（{config.bottle_craft_starstone}星星/个）
我的漂流瓶 —— 查看持有数
扔漂流瓶 内容 —— 把漂流瓶放入水中（最多60字）
捡漂流瓶 —— 随机捞一个漂流瓶（{config.bottle_salvage_starstone}星星/次，水中至少要有5个）
漂流瓶数量 —— 查看水中共有多少漂流瓶
评论漂流瓶 漂流瓶ID 内容 —— 回复他人的漂流瓶（{config.comment_price}金币，最多20字）
----------
数量可选，不填则默认为1"""


@bottle_help_cmd.handle()
async def handle_bottle_help():
    await bottle_help_cmd.finish(_build_help_text())


# ===== 买漂流瓶 =====
buy_bottle_cmd = on_command("买漂流瓶", priority=5, block=True)


@buy_bottle_cmd.handle()
async def handle_buy_bottle(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    message = args.extract_plain_text().strip()
    num = int(message) if message.isdigit() else 1

    result = await BottleService.buy_bottles(uid, num)
    if not result["ok"]:
        if result["reason"] == "bad_num":
            await buy_bottle_cmd.finish(f"一次只能购买{result['max']}个漂流瓶喔")
        if result["reason"] == "no_gold":
            await buy_bottle_cmd.finish(f"金币不足喔...（需要 {result['cost']} 金币）")
        await buy_bottle_cmd.finish("购买失败...")

    await buy_bottle_cmd.finish(
        f"成功买下{result['num']}个漂流瓶~(金币-{result['cost']})"
        f"\n当前持有 {result['owned']} 个"
    )


# ===== 合成漂流瓶 =====
compound_bottle_cmd = on_command("合成漂流瓶", priority=5, block=True)


@compound_bottle_cmd.handle()
async def handle_compound_bottle(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    message = args.extract_plain_text().strip()
    num = int(message) if message.isdigit() else 1

    result = await BottleService.craft_bottles(uid, num)
    if not result["ok"]:
        if result["reason"] == "bad_num":
            await compound_bottle_cmd.finish("数量必须大于0")
        if result["reason"] == "no_starstone":
            await compound_bottle_cmd.finish(
                f"星星不足喔...（合成{num}个需要 {result['cost']} 星星）"
            )
        await compound_bottle_cmd.finish("合成失败...")

    await compound_bottle_cmd.finish(
        f"{result['cost']}颗星星凝聚成了{result['num']}个漂流瓶！"
        f"\n当前持有 {result['owned']} 个"
    )


# ===== 我的漂流瓶 =====
my_bottle_cmd = on_command("我的漂流瓶", aliases={"漂流瓶背包"}, priority=5, block=True)


@my_bottle_cmd.handle()
async def handle_my_bottle(uid: int = Depends(get_uid)) -> None:
    owned = await BottleService.get_owned_count(uid)
    if owned <= 0:
        await my_bottle_cmd.finish(
            "你还没有漂流瓶，发送 买漂流瓶 / 合成漂流瓶 获取~"
        )
    await my_bottle_cmd.finish(f"你持有 {owned} 个漂流瓶~")


# ===== 扔漂流瓶 =====
throw_bottle_cmd = on_command("扔漂流瓶", priority=5, block=True)


@throw_bottle_cmd.handle()
async def handle_throw_bottle(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    if not throw_freq.check(uid):
        await throw_bottle_cmd.finish(
            f"休息一会再扔吧~({int(throw_freq.left_time(uid))}s)"
        )

    content = args.extract_plain_text()
    result = await BottleService.throw_bottle(uid, content)
    if not result["ok"]:
        if result["reason"] == "empty":
            await throw_bottle_cmd.finish("漂流瓶内容不能为空喔")
        if result["reason"] == "too_long":
            await throw_bottle_cmd.finish(f"内容太长了（最多{result['max']}字）")
        if result["reason"] == "no_bottle":
            await throw_bottle_cmd.finish(
                "背包里没有漂流瓶喔，发送 买漂流瓶 / 合成漂流瓶 获取~"
            )
        await throw_bottle_cmd.finish("扔漂流瓶失败...")

    throw_freq.start_cd(uid)
    await throw_bottle_cmd.finish(
        "你将漂流瓶放入水中，目送它漂向诗与远方...\n"
        f"(漂流瓶ID: {result['bottle_id']}，还持有 {result['owned']} 个)"
    )


# ===== 捡漂流瓶 =====
pick_bottle_cmd = on_command("捡漂流瓶", priority=5, block=True)


@pick_bottle_cmd.handle()
async def handle_pick_bottle(
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    if not get_freq.check(uid):
        await pick_bottle_cmd.finish(
            f"休息一会再捡吧~({int(get_freq.left_time(uid))}s)"
        )

    result = await BottleService.pick_bottle(uid)
    if not result["ok"]:
        if result["reason"] == "pool_empty":
            await pick_bottle_cmd.finish(
                f"漂流瓶太少了（{result['amount']}/{result['min']}个）"
            )
        if result["reason"] == "no_starstone":
            await pick_bottle_cmd.finish(
                f"星星不足喔...（捞一次需要 {result['cost']} 星星）"
            )
        await pick_bottle_cmd.finish("没有可捞取的漂流瓶")

    get_freq.start_cd(uid)
    bottle = result["bottle"]

    bottle_msg = "🍾 漂流瓶 #" + result["bottle_id"] + "\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"{bottle['content']}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"投放者: {_thrower_name(bottle.get('uid', '未知'))}\n"
    bottle_msg += f"投放时间: {_fmt_time(bottle['time'])}\n"
    bottle_msg += f"被捞起次数: {bottle['pick_count']}\n"

    forward_messages = [bottle_msg]
    for c in bottle.get("comments", []):
        comment_msg = (
            f"💬 [UID: {c.get('uid', '未知')}]\n{c['content']}\n"
            f"— {_fmt_time(c.get('time', 0))}"
        )
        forward_messages.append(comment_msg)

    try:
        chain = await build_forward_chain(bot, forward_messages)
        await send_group_forward_msg(event, bot, chain)
    except Exception as error:
        logger.error(f"捡漂流瓶合并消息发送失败: {error}")
        await pick_bottle_cmd.finish(bottle_msg)


# ===== 漂流瓶数量 =====
bottle_count_cmd = on_command("漂流瓶数量", priority=5, block=True)


@bottle_count_cmd.handle()
async def handle_bottle_count() -> None:
    count = await BottleService.bottle_amount()
    if count == 0:
        await bottle_count_cmd.finish("目前水中没有漂流瓶...")
    await bottle_count_cmd.finish(f"当前一共有{count}个漂流瓶~")


# ===== 评论漂流瓶 =====
comment_bottle_cmd = on_command("评论漂流瓶", aliases={"回复漂流瓶"}, priority=5, block=True)


@comment_bottle_cmd.handle()
async def handle_comment_bottle(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    if not comm_freq.check(uid):
        await comment_bottle_cmd.finish(
            f"休息一会再评论吧~({int(comm_freq.left_time(uid))}s)"
        )

    message = args.extract_plain_text()
    parts = message.split(" ", 1)
    if len(parts) != 2 or not parts[0].strip().isdigit():
        await comment_bottle_cmd.finish("用法: 评论漂流瓶 漂流瓶ID 内容")
    bottle_id = int(parts[0].strip())
    content = parts[1]

    result = await BottleService.comment_bottle(uid, bottle_id, content)
    if not result["ok"]:
        if result["reason"] == "empty":
            await comment_bottle_cmd.finish("评论内容不能为空喔")
        if result["reason"] == "too_long":
            await comment_bottle_cmd.finish(
                f"评论内容太长了（最多{result['max']}字）"
            )
        if result["reason"] == "no_gold":
            await comment_bottle_cmd.finish(
                f"评论漂流瓶需要{result['cost']}枚金币"
            )
        if result["reason"] == "not_found":
            await comment_bottle_cmd.finish("找不到这个漂流瓶")
        await comment_bottle_cmd.finish("评论失败...")

    comm_freq.start_cd(uid)
    await comment_bottle_cmd.finish(f"评论成功！(金币-{result['cost']})")


# ===== 捡指定漂流瓶（仅 SU） =====
pick_by_id_cmd = on_command(
    "捡指定漂流瓶", aliases={"查看指定漂流瓶", "查看漂流瓶"}, priority=4, block=True
)


@pick_by_id_cmd.handle()
async def handle_pick_by_id(
    bot: Bot,
    event: Event,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    if not is_su(uid):
        await pick_by_id_cmd.finish("权限不足")

    bottle_id_str = args.extract_plain_text().strip()
    if not bottle_id_str.isdigit():
        await pick_by_id_cmd.finish("用法: 捡指定漂流瓶 漂流瓶ID")

    result = await BottleService.get_bottle(int(bottle_id_str))
    if not result["ok"]:
        await pick_by_id_cmd.finish(f"找不到漂流瓶 #{bottle_id_str}")
    bottle = result["bottle"]

    deleted_tag = "【已删除】" if bottle["deleted"] else ""
    bottle_msg = f"🍾 漂流瓶 #{bottle['id']} {deleted_tag}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"{bottle['content']}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"投放者UID: {bottle['uid']}\n"
    bottle_msg += f"投放时间: {_fmt_time(bottle['time'])}\n"
    bottle_msg += f"被捞起次数: {bottle['pick_count']}"

    forward_messages = [bottle_msg]
    for c in bottle.get("comments", []):
        comment_msg = (
            f"💬 [UID: {c.get('uid', '未知')}]\n{c['content']}\n"
            f"— {_fmt_time(c.get('time', 0))}"
        )
        forward_messages.append(comment_msg)

    try:
        chain = await build_forward_chain(bot, forward_messages)
        await send_group_forward_msg(event, bot, chain)
    except Exception as error:
        logger.error(f"捡指定漂流瓶合并消息发送失败: {error}")
        await pick_by_id_cmd.finish(bottle_msg)


# ===== 删除漂流瓶（仅 SU） =====
delete_bottle_cmd = on_command("删除漂流瓶", priority=4, block=True)


@delete_bottle_cmd.handle()
async def handle_delete_bottle(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    if not is_su(uid):
        await delete_bottle_cmd.finish("权限不足")

    bottle_id_str = args.extract_plain_text().strip()
    if not bottle_id_str.isdigit():
        await delete_bottle_cmd.finish("用法: 删除漂流瓶 漂流瓶ID")

    result = await BottleService.delete_bottle(int(bottle_id_str))
    if not result["ok"]:
        await delete_bottle_cmd.finish(
            f"漂流瓶 #{bottle_id_str} 不存在或已被删除"
        )
    await delete_bottle_cmd.finish(f"漂流瓶 #{bottle_id_str} 已删除")


# ===== 初始化 =====
driver = get_driver()


@driver.on_startup
async def init_drift_bottle():
    """初始化漂流瓶插件（与旧版共用同一数据库，瓶子数据无缝保留）"""
    from pathlib import Path

    from .db import BottleDB

    plugin_dir = Path(__file__).parent.parent.parent
    db_path = plugin_dir / "src" / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    BottleDB.set_db_path(str(db_path))
    BottleDB.init_bottle_database()
    logger.info("Drift_bottle 漂流瓶插件初始化完成")
