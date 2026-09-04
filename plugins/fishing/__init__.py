"""
钓鱼插件 - fishing

完整迁移自旧版 koinoribot
功能：钓鱼、多连钓鱼、卖鱼、放生、漂流瓶
弃用功能：捉萝莉、系统红包
"""

import datetime
import random

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Depends
from nonebot.plugin import PluginMetadata

from ...tools import send_group_forward_msg, build_forward_chain
from ...config_store import config
from ...money import money
from ...tools import get_group_id, get_uid
from ...utils import FreqLimiter
from .getbottle import BottleManager
from .getfish import FISH_LIST, FISH_PRICE, PROBABILITY, PROBABILITY_2, FishingManager
from .serif import COOL_TIME_SERIF, GET_FISH_SERIF, NO_FISH_SERIF
from ...nickname import get_user_nickname

# 导入重构后的模块
from .util import CooldownManager, DatabaseManager
# SU 权限管理
from ...su_manager import is_su
__plugin_meta__ = PluginMetadata(
    name="fishing",
    description="钓鱼系统 - 完整版",
    usage="钓鱼 / 十连钓鱼 / 百连钓鱼 / 卖鱼 等",
)

# ===== 频率限制器 =====
freq = FreqLimiter(config.cool_time)
bait_freq = FreqLimiter(10)
throw_freq = FreqLimiter(config.throw_cool_time)
get_freq = FreqLimiter(config.salvage_cool_time)
comm_freq = FreqLimiter(config.comment_cool_time)

# 通用冷却管理器
general_cooldown = CooldownManager(config.fish_cd)


# ===== 命令处理器 =====

# ----- 钓鱼帮助 -----
fish_help_cmd = on_command("钓鱼帮助", priority=5, block=True)

# 钓鱼帮助内容（迁移自old_bot完整版）
help_1 = """
转账功能：
转账qq QQ号 金币数量
转账uid 用户UID 金币数量
示例：
转账qq 12345678 100
转账uid 10001 100

低保功能（仅限金币＜5000且没有私藏鱼饵和鱼时）：
直接发送 领低保
钓鱼功能：
1.#钓鱼帮助
2.#买鱼饵 数量（例：#买鱼饵 5）
3.钓鱼
4.十连钓鱼（95折优惠）、
5.百连钓鱼（9折优惠）
6.千连钓鱼/万连钓鱼/十万连钓鱼（仅用作调试）
7..#出售 鱼emoji 数量（例：#出售 🐟 2）
8.出售小鱼、一键出售
9.#放生 鱼emoji 数量（例：#放生 🐟 2）
10.#背包
11.钓鱼概率 （获取概率公示）
----------
鱼emoji如：🐟，🦐，🦀，🐡，🐠，🦈
数量可选，不填则默认为1
出售可获得金币，放生可获得等价值的水心碎片
每75个水心碎片会自动合成为水之心
"""

help_2 = """
漂流瓶功能：
1.#合成漂流瓶+数量（例：#合成漂流瓶 2）
2.#买漂流瓶+数量（例：#买漂流瓶 2）
3.#扔漂流瓶+内容（例：#扔漂流瓶 你好）
4.#捡漂流瓶
5.#漂流瓶数量
6.#回复 漂流瓶ID 内容（例：#回复114514 你好）
7.#删除回复
----------
数量可选，不填则默认为1
合成漂流瓶需要2个水之心
买漂流瓶需要225枚金币
捡漂流瓶需要一个水之心
回复他人的漂流瓶需要20金币
"""


@fish_help_cmd.handle()
async def handle_fish_help(event: Event, bot: Bot):
    try:
        # 构建转发消息链
        chain = await build_forward_chain(bot, [help_1, help_2])
        # 发送转发消息
        await send_group_forward_msg(event, bot, chain)
    except Exception as e:
        logger.error(f"钓鱼帮助失败: {e}")


# ----- 概率公示 -----
prob_cmd = on_command("概率公示", priority=5, block=True)


@prob_cmd.handle()
async def handle_prob() -> None:
    air_force_prob = PROBABILITY[0]
    total_prob = sum(PROBABILITY)
    air_force_percentage = (air_force_prob / total_prob) * 100

    msg = f"【钓鱼概率公示】\n\n空军概率：{air_force_percentage:.2f}%\n\n钓到鱼后各鱼种概率：\n"

    fish_total = sum(PROBABILITY_2)
    for fish, prob in zip(FISH_LIST, PROBABILITY_2):
        percentage = (prob / fish_total) * 100
        msg += f"{fish}: {percentage:.2f}%\n"

    await prob_cmd.finish(msg.strip())


# ----- 单抽钓鱼 -----
single_fish_cmd = on_command("钓鱼", aliases={"🎣"}, priority=5, block=True)


@single_fish_cmd.handle()
async def handle_single_fish(
    uid: int = Depends(get_uid)
) -> None:
    # 冷却检测
    if not freq.check(uid):
        await single_fish_cmd.finish(
            random.choice(COOL_TIME_SERIF) + f"({int(freq.left_time(uid))}s)"
        )

    user_info = await FishingManager.get_user_info(uid)
    bait_cost = config.bait_price * config.bait_num

    auto_buy = False
    # 检查鱼饵
    if user_info["fish"].get("🍙", 0) < config.bait_num:
        user_gold = money.gold
        if user_gold >= bait_cost:
            money.gold -= bait_cost
            auto_buy = True
        else:
            await single_fish_cmd.finish(
                "金币或鱼饵不足喔...\n发送 领低保 来获取启动资金吧~", at_sender=True
            )

    freq.start_cd(uid)

    # 消耗鱼饵
    if not auto_buy:
        await FishingManager.decrease_value(
            uid, "fish", "🍙", config.bait_num, user_info
        )

    # 执行钓鱼
    resp = await FishingManager.do_fishing(uid, user_info=user_info)

    await FishingManager.save_user_info(uid, user_info)

    msg = resp["msg"]
    if auto_buy:
        msg = f"(已自动购买鱼饵-{bait_cost}金币)\n" + msg

    await single_fish_cmd.finish(msg, at_sender=True)


# ----- 十连钓鱼 -----
ten_fish_cmd = on_command("十连钓鱼", priority=5, block=True)


@ten_fish_cmd.handle()
async def handle_ten_fish(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
    ) -> None:
    await FishingManager.multi_fishing(
        uid,
        matcher,
        bot,
        event,
        10,
        95,
        config.star_price * 10 // 2,
        "十连钓鱼",
        general_cooldown,
    )


# ----- 百连钓鱼 -----
hundred_fish_cmd = on_command("百连钓鱼", priority=5, block=True)


@hundred_fish_cmd.handle()
async def handle_hundred_fish(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
    ) -> None:
    await FishingManager.multi_fishing(
        uid,
        matcher,
        bot,
        event,
        100,
        900,
        config.star_price * 100 // 2,
        "百连钓鱼",
        general_cooldown,
    )


# ----- 千连钓鱼 -----
thousand_fish_cmd = on_command("千连钓鱼", priority=5, block=True)


@thousand_fish_cmd.handle()
async def handle_thousand_fish(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
    ) -> None:
    await FishingManager.multi_fishing(
        uid,
        matcher,
        bot,
        event,
        1000,
        9000,
        config.star_price * 1000 // 2,
        "千连钓鱼",
        general_cooldown,
    )


# ----- 万连钓鱼 -----
ten_thousand_fish_cmd = on_command("万连钓鱼", priority=5, block=True)


@ten_thousand_fish_cmd.handle()
async def handle_ten_thousand_fish(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
    ) -> None:
    await FishingManager.multi_fishing(
        uid,
        matcher,
        bot,
        event,
        10000,
        90000,
        config.star_price * 10000 // 2,
        "万连钓鱼",
        general_cooldown,
    )


# ----- 十万连钓鱼 -----
hundred_thousand_fish_cmd = on_command("十万连钓鱼", priority=5, block=True)


@hundred_thousand_fish_cmd.handle()
async def handle_hundred_thousand_fish(
    matcher: Matcher,
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
    ) -> None:
    if not is_su(uid):
        await hundred_thousand_fish_cmd.finish("权限不足", at_sender=True)  
    await FishingManager.multi_fishing(
        uid,
        matcher,
        bot,
        event,
        100000,
        1,
        config.star_price * 0 // 2,
        "十万连钓鱼",
        general_cooldown,
    )


# ----- 买鱼饵 -----
buy_bait_cmd = on_command("买鱼饵", priority=5, block=True)


@buy_bait_cmd.handle()
async def handle_buy_bait(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
    ) -> None:
    message = args.extract_plain_text()
    num = int(message) if message.isdigit() else 1

    if num > 50000000:
        await buy_bait_cmd.finish("一次只能购买50000000个鱼饵喔", at_sender=True)

    if num <= 0:
        await buy_bait_cmd.finish("数量必须大于0", at_sender=True)

    cost = num * config.bait_price

    user_gold = money.gold
    if user_gold < cost:
        await buy_bait_cmd.finish("金币不足喔...", at_sender=True)

    money.gold -= cost
    await FishingManager.increase_value(uid, "fish", "🍙", num)

    await buy_bait_cmd.finish(f"成功购买{num}个鱼饵~(金币-{cost})", at_sender=True)


# ----- 背包 -----
bag_cmd = on_command("背包", aliases={"我的背包"}, priority=5, block=True)


@bag_cmd.handle()
async def handle_bag(uid: int = Depends(get_uid)) -> None:
    user_info = await FishingManager.get_user_info(uid)

    msg = "背包：\n"
    items = ""
    for item, count in user_info["fish"].items():
        if count > 0:
            items += f"{item}×{count}\n"

    if not items:
        items = "空空如也..."

    await bag_cmd.finish(msg + items.strip(), at_sender=True)


# ----- 出售 -----
sell_cmd = on_command("出售", priority=5, block=True)


@sell_cmd.handle()
async def handle_sell(
    uid: int = Depends(get_uid),
    args: Message = CommandArg(),
    ) -> None:
    message = args.extract_plain_text()
    parts = message.split()

    if not parts:
        await sell_cmd.finish("用法: 出售 鱼emoji [数量]", at_sender=True)

    fish = parts[0]
    if fish not in FISH_LIST + ["🍙"]:
        await sell_cmd.finish("这不是可出售的物品", at_sender=True)

    num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1

    user_info = await FishingManager.get_user_info(uid)

    if not user_info["fish"].get(fish) or user_info["fish"][fish] <= 0:
        await sell_cmd.finish(f"你没有{fish}喔", at_sender=True)

    if num > user_info["fish"][fish]:
        num = user_info["fish"][fish]

    await FishingManager.decrease_value(uid, "fish", fish, num, user_info)
    get_golds = FISH_PRICE.get(fish, 0) * num
    money.gold += get_golds
    await FishingManager.increase_value(uid, "statis", "sell", get_golds, user_info)
    await FishingManager.save_user_info(uid, user_info)

    await sell_cmd.finish(
        f"\n成功出售{num}条{fish}，得到{get_golds}枚金币~", at_sender=True
    )


# ----- 出售小鱼 -----
sell_small_cmd = on_command("出售小鱼", priority=5, block=True)


@sell_small_cmd.handle()
async def handle_sell_small(
    uid: int = Depends(get_uid), ) -> None:
    user_info = await FishingManager.get_user_info(uid)
    fishes = "🐟🦀🦐🐡🐠"

    total_gold = 0
    result = []

    for fish in fishes:
        count = user_info["fish"].get(fish, 0)
        if count > 0:
            gold = count * FISH_PRICE.get(fish, 0)
            total_gold += gold
            user_info["fish"][fish] = 0
            result.append(f"{fish}×{count} → {gold}金币")

    if total_gold > 0:
        money.gold += total_gold
        await FishingManager.increase_value(
            uid, "statis", "sell", total_gold, user_info
        )
        await FishingManager.save_user_info(uid, user_info)
        await sell_small_cmd.finish(
            "\n".join(result) + f"\n\n共获得{total_gold}金币~",
            at_sender=True,
        )
    else:
        await sell_small_cmd.finish("没有可出售的小鱼", at_sender=True)


# ----- 一键出售 -----
sell_all_cmd = on_command("一键出售", priority=5, block=True)


@sell_all_cmd.handle()
async def handle_sell_all(
    uid: int = Depends(get_uid), ) -> None:
    user_info = await FishingManager.get_user_info(uid)
    fishes = "🐟🦀🦐🐡🐠🦈🌟"

    total_gold = 0
    result = []

    for fish in fishes:
        count = user_info["fish"].get(fish, 0)
        if count > 0:
            gold = count * FISH_PRICE.get(fish, 0)
            total_gold += gold
            user_info["fish"][fish] = 0
            result.append(f"{fish}×{count} → {gold}金币")

    if total_gold > 0:
        money.gold += total_gold
        await FishingManager.increase_value(
            uid, "statis", "sell", total_gold, user_info
        )
        await FishingManager.save_user_info(uid, user_info)
        await sell_all_cmd.finish(
            "\n".join(result) + f"\n\n共获得{total_gold}金币~",
            at_sender=True,
        )
    else:
        await sell_all_cmd.finish("没有可出售的鱼", at_sender=True)


# ----- 放生 -----
free_cmd = on_command("放生", priority=5, block=True)


@free_cmd.handle()
async def handle_free(
    uid: int = Depends(get_uid), args: Message = CommandArg()
) -> None:
    message = args.extract_plain_text()
    parts = message.split()

    if not parts or parts[0] not in FISH_LIST:
        await free_cmd.finish("用法: 放生 鱼emoji [数量]", at_sender=True)

    fish = parts[0]
    num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1

    user_info = await FishingManager.get_user_info(uid)

    if not user_info["fish"].get(fish) or user_info["fish"][fish] <= 0:
        await free_cmd.finish(f"你没有{fish}喔", at_sender=True)

    if num > user_info["fish"][fish]:
        num = user_info["fish"][fish]

    await FishingManager.decrease_value(uid, "fish", fish, num, user_info)
    get_frags = FISH_PRICE.get(fish, 0) * num

    # 计算碎片转换
    user_frags = user_info["statis"].get("frags", 0)
    total_frags = user_frags + get_frags
    crystals = 0

    if total_frags >= config.frag_to_crystal:
        crystals = total_frags // config.frag_to_crystal
        remaining_frags = total_frags % config.frag_to_crystal
        user_info["statis"]["frags"] = remaining_frags
        await FishingManager.increase_value(uid, "fish", "🔮", crystals, user_info)
    else:
        user_info["statis"]["frags"] = total_frags

    await FishingManager.increase_value(uid, "statis", "free", num, user_info)
    await FishingManager.save_user_info(uid, user_info)

    addition = f"\n✨ {crystals}颗水之心合成成功！" if crystals > 0 else ""
    await free_cmd.finish(
        f"{num}条{fish}成功回到水里，获得{get_frags}个水心碎片~{addition}",
        at_sender=True,
    )


# ----- 钓鱼统计 -----
stat_cmd = on_command("钓鱼统计", priority=5, block=True)


@stat_cmd.handle()
async def handle_stat(uid: int = Depends(get_uid)) -> None:
    user_info = await FishingManager.get_user_info(uid)

    free_count = user_info["statis"].get("free", 0)
    sell_gold = user_info["statis"].get("sell", 0)
    total_fish = user_info["statis"].get("total_fish", 0)

    free_msg = f"已放生{free_count}条鱼" if free_count else "还没有放生过鱼"
    sell_msg = f"已卖出{sell_gold}金币的鱼" if sell_gold else "还没有出售过鱼"
    total_msg = f"总共钓上了{total_fish}条鱼" if total_fish else "还没有钓上过鱼"

    await stat_cmd.finish(
        f"📊 钓鱼统计：\n{free_msg}\n{sell_msg}\n{total_msg}",
        at_sender=True,
    )

# ===== 漂流瓶功能 =====

# ----- 买漂流瓶 -----
buy_bottle_cmd = on_command("买漂流瓶", priority=5, block=True)


@buy_bottle_cmd.handle()
async def handle_buy_bottle(
    uid: int = Depends(get_uid),
    args: Message = CommandArg(),
    ) -> None:
    message = args.extract_plain_text()
    num = int(message) if message.isdigit() else 1

    if num > 10:
        await buy_bottle_cmd.finish("一次只能购买10个漂流瓶喔", at_sender=True)

    cost = num * config.bottle_price

    user_gold = money.gold
    if user_gold < cost:
        await buy_bottle_cmd.finish("金币不足喔...", at_sender=True)

    money.gold -= cost
    await FishingManager.increase_value(uid, "fish", "✉", num)

    await buy_bottle_cmd.finish(f"成功买下{num}个漂流瓶~(金币-{cost})", at_sender=True)


# ----- 合成漂流瓶 -----
compound_bottle_cmd = on_command("合成漂流瓶", priority=5, block=True)


@compound_bottle_cmd.handle()
async def handle_compound_bottle(
    uid: int = Depends(get_uid), args: Message = CommandArg()
) -> None:
    message = args.extract_plain_text()
    logger.error(message)
    num = int(message) if message.isdigit() else 1

    user_info = await FishingManager.get_user_info(uid)
    crystal_need = num * config.crystal_to_bottle

    if user_info["fish"].get("🔮", 0) < crystal_need:
        await compound_bottle_cmd.finish(
            f"需要{crystal_need}个水之心才能合成{num}个漂流瓶",
            at_sender=True,
        )

    await FishingManager.decrease_value(uid, "fish", "🔮", crystal_need, user_info)
    await FishingManager.increase_value(uid, "fish", "✉", num, user_info)
    await FishingManager.save_user_info(uid, user_info)

    await compound_bottle_cmd.finish(
        f"{crystal_need}个🔮融合成了{num}个漂流瓶！", at_sender=True
    )


# ----- 扔漂流瓶 -----
throw_bottle_cmd = on_command("扔漂流瓶", priority=5, block=True)


@throw_bottle_cmd.handle()
async def handle_throw_bottle(
    uid: int = Depends(get_uid),
    args: Message = CommandArg(),
) -> None:
    logger.debug("扔漂流瓶-开始")
    if not throw_freq.check(uid):
        logger.debug("扔漂流瓶-cd")
        await throw_bottle_cmd.finish(
            f"休息一会再扔吧~({int(throw_freq.left_time(uid))}s)"
        )

    user_info = await FishingManager.get_user_info(uid)

    if user_info["fish"].get("✉", 0) <= 0:
        await throw_bottle_cmd.finish("背包里没有漂流瓶喔", at_sender=True)

    content = args.extract_plain_text()
    if content == "":
        await throw_bottle_cmd.finish("漂流瓶内容不能为空喔", at_sender=True)

    if len(content) > 60:
        await throw_bottle_cmd.finish("内容太长了（最多60字）", at_sender=True)

    # 扣除漂流瓶
    await FishingManager.decrease_value(uid, "fish", "✉", 1, user_info)
    await FishingManager.save_user_info(uid, user_info)

    # 生成漂流瓶并保存（ID由数据库自增生成）
    bottle_id = BottleManager.create_bottle("", uid, content)

    throw_freq.start_cd(uid)

    await throw_bottle_cmd.finish(
        f"你将漂流瓶放入水中，目送它漂向诗与远方...\n(漂流瓶ID: {bottle_id})",
        at_sender=True,
    )


# ----- 捡漂流瓶 -----
pick_bottle_cmd = on_command("捡漂流瓶", priority=5, block=True)


@pick_bottle_cmd.handle()
async def handle_pick_bottle(bot: Bot, event: Event, uid: int = Depends(get_uid)) -> None:
    if not get_freq.check(uid):
        await pick_bottle_cmd.finish(
            f"休息一会再捡吧~({int(get_freq.left_time(uid))}s)"
        )

    user_info = await FishingManager.get_user_info(uid)

    if user_info["fish"].get("🔮", 0) < config.crystal_to_net:
        await pick_bottle_cmd.finish(
            f"捡漂流瓶需要{config.crystal_to_net}个水之心喔", at_sender=True
        )

    bottle_count = BottleManager.get_bottle_amount()
    if bottle_count < 5:
        await pick_bottle_cmd.finish(
            f"漂流瓶太少了（{bottle_count}/5个）", at_sender=True
        )

    # 随机捞取
    bottle_id, bottle = BottleManager.pick_random_bottle()

    if bottle_id is None:
        await pick_bottle_cmd.finish("没有可捞取的漂流瓶", at_sender=True)

    # 扣除水之心
    await FishingManager.decrease_value(
        uid, "fish", "🔮", config.crystal_to_net, user_info
    )
    await FishingManager.save_user_info(uid, user_info)

    get_freq.start_cd(uid)

    # 格式化漂流瓶正文
    create_time = datetime.datetime.fromtimestamp(bottle["time"]).strftime(
        "%Y-%m-%d %H:%M"
    )

    thrower_uid = bottle.get('uid', '未知')
    if str(thrower_uid).isdigit():
        thrower_name = get_user_nickname(int(thrower_uid)) or f"UID {thrower_uid}"
    else:
        thrower_name = thrower_uid

    bottle_msg = f"🍾 漂流瓶 #{bottle_id}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"{bottle['content']}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"投放者: {thrower_name}\n"
    bottle_msg += f"投放时间: {create_time}\n"
    bottle_msg += f"被捞起次数: {bottle['pick_count']}"

    # 构建合并转发消息
    forward_messages = [bottle_msg]

    # 添加评论节点
    comments = bottle.get("comments", [])
    if comments:
        for c in comments:
            comment_time = datetime.datetime.fromtimestamp(c.get("time", 0)).strftime(
                "%Y-%m-%d %H:%M"
            )
            comment_msg = f"💬 [UID: {c.get('uid', '未知')}]\n{c['content']}\n— {comment_time}"
            forward_messages.append(comment_msg)

    try:
        chain = await build_forward_chain(bot, forward_messages)
        await send_group_forward_msg(event, bot, chain)
    except Exception as e:
        logger.error(f"捡漂流瓶合并消息发送失败: {e}")
        # 降级为普通消息
        await pick_bottle_cmd.finish(bottle_msg)


# ----- 漂流瓶数量 -----
bottle_count_cmd = on_command("漂流瓶数量", priority=5, block=True)


@bottle_count_cmd.handle()
async def handle_bottle_count() -> None:
    count = BottleManager.get_bottle_amount()

    if count == 0:
        await bottle_count_cmd.finish("目前水中没有漂流瓶...")
    else:
        await bottle_count_cmd.finish(f"当前一共有{count}个漂流瓶~")


# ----- 捡指定漂流瓶（仅 SU） -----
pick_by_id_cmd = on_command("捡指定漂流瓶", aliases={"查看指定漂流瓶", "查看漂流瓶"}, priority=4, block=True)


@pick_by_id_cmd.handle()
async def handle_pick_by_id(
    bot: Bot,
    event: Event,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    if not is_su(uid):
        await pick_by_id_cmd.finish("权限不足", at_sender=True)

    bottle_id_str = args.extract_plain_text().strip()
    if not bottle_id_str.isdigit():
        await pick_by_id_cmd.finish("用法: 捡指定漂流瓶 漂流瓶ID", at_sender=True)

    bottle = BottleManager.get_bottle_by_id(bottle_id_str)
    if bottle is None:
        await pick_by_id_cmd.finish(f"找不到漂流瓶 #{bottle_id_str}", at_sender=True)

    create_time = datetime.datetime.fromtimestamp(bottle["time"]).strftime(
        "%Y-%m-%d %H:%M"
    )
    deleted_tag = "【已删除】" if bottle["deleted"] else ""

    bottle_msg = f"🍾 漂流瓶 #{bottle['id']} {deleted_tag}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"{bottle['content']}\n"
    bottle_msg += "━━━━━━━━━━\n"
    bottle_msg += f"投放者UID: {bottle['uid']}\n"
    bottle_msg += f"投放时间: {create_time}\n"
    bottle_msg += f"被捞起次数: {bottle['pick_count']}"

    forward_messages = [bottle_msg]

    comments = bottle.get("comments", [])
    if comments:
        for c in comments:
            comment_time = datetime.datetime.fromtimestamp(c.get("time", 0)).strftime(
                "%Y-%m-%d %H:%M"
            )
            comment_msg = f"💬 [UID: {c.get('uid', '未知')}]\n{c['content']}\n— {comment_time}"
            forward_messages.append(comment_msg)

    try:
        chain = await build_forward_chain(bot, forward_messages)
        await send_group_forward_msg(event, bot, chain)
    except Exception as e:
        logger.error(f"捡指定漂流瓶合并消息发送失败: {e}")
        await pick_by_id_cmd.finish(bottle_msg)


# ----- 评论漂流瓶 -----
comment_bottle_cmd = on_command("评论漂流瓶", aliases={"回复漂流瓶"}, priority=5, block=True)


@comment_bottle_cmd.handle()
async def handle_comment_bottle(
    uid: int = Depends(get_uid),
    args: Message = CommandArg(),
    ):
    if not comm_freq.check(uid):
        await comment_bottle_cmd.finish(
            f"休息一会再评论吧~({int(comm_freq.left_time(uid))}s)"
        )

    user_gold = money.gold
    if user_gold < config.comment_price:
        await comment_bottle_cmd.finish(
            f"评论漂流瓶需要{config.comment_price}枚金币", at_sender=True
        )

    message = args.extract_plain_text()
    parts = message.split(" ", 1)

    if len(parts) != 2:
        await comment_bottle_cmd.finish("用法: 评论 漂流瓶ID 内容", at_sender=True)

    bottle_id = parts[0]
    content = parts[1]

    if len(content) > 20:
        await comment_bottle_cmd.finish("评论内容太长了（最多20字）", at_sender=True)

    # 使用 BottleManager 添加评论
    if not BottleManager.add_comment(bottle_id, uid, content):
        await comment_bottle_cmd.finish("找不到这个漂流瓶", at_sender=True)

    money.gold -= config.comment_price
    comm_freq.start_cd(uid)

    await comment_bottle_cmd.finish(
        f"评论成功！(金币-{config.comment_price})", at_sender=True
    )


# ----- 删除漂流瓶（仅 SU） -----
delete_bottle_cmd = on_command("删除漂流瓶", priority=4, block=True)


@delete_bottle_cmd.handle()
async def handle_delete_bottle(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    if not is_su(uid):
        await delete_bottle_cmd.finish("权限不足", at_sender=True)

    bottle_id_str = args.extract_plain_text().strip()
    if not bottle_id_str.isdigit():
        await delete_bottle_cmd.finish("用法: 删除漂流瓶 漂流瓶ID", at_sender=True)

    if BottleManager.delete_bottle(bottle_id_str):
        await delete_bottle_cmd.finish(f"漂流瓶 #{bottle_id_str} 已删除", at_sender=True)
    else:
        await delete_bottle_cmd.finish(f"漂流瓶 #{bottle_id_str} 不存在或已被删除", at_sender=True)


# ===== 初始化 =====
driver = get_driver()


@driver.on_startup
async def init_fishing():
    """初始化钓鱼插件"""
    from pathlib import Path

    plugin_dir = Path(__file__).parent.parent.parent
    db_path = plugin_dir / "src" / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    DatabaseManager.set_db_path(str(db_path))
    DatabaseManager.init_fishing_database()
    logger.info("Fishing 插件初始化完成")
