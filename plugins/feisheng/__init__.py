import random
from nonebot import on_command, logger
from nonebot.adapters import Event, Bot, Message
from nonebot.params import CommandArg, Depends
from nonebot.permission import SUPERUSER

from ...tools import get_uid, send_group_forward_msg, build_forward_chain
from ...money import money

# 导入宠物系统相关函数
from ..chongwu.pet import get_user_pet, use_user_item

from .data import (
    get_feisheng_data, increase_pet_ascension_progress, update_feisheng_data,
    get_user_feisheng_items, add_feisheng_item, use_feisheng_item,
    check_daily_cultivation_limit, get_feisheng_leaderboard
)
from ...su_manager import is_su, get_excluded_su_uids
from ...nickname import get_user_nickname
import aiohttp
# 境界列表
REALMS = ["锻体", "练气", "筑基", "金丹", "具灵", "元婴", "化神", "悟道", "羽化", "渡劫"]

# 丹药配置 (境界Index -> 需要的丹药信息)
# Key: 当前境界Index (突破到下一境界所需)
PILL_CONFIG = {
    0: {"name": "聚气丹", "price": 2},      
    1: {"name": "凝元丹", "price": 5},      
    2: {"name": "玉清丹", "price": 20},     
    3: {"name": "玄灵丹", "price": 50},     
    4: {"name": "婴变丹", "price": 100},    
    5: {"name": "神游丹", "price": 200},    
    6: {"name": "悟玄丹", "price": 500},    
    7: {"name": "羽仙丹", "price": 1000},   
    8: {"name": "渡厄丹", "price": 2000},   
}

# 升仙丸 (渡劫 -> 飞升)
SHENGXIAN_WAN = {"name": "升仙丸", "price": 20000}

# 修炼消耗配置
CULTIVATION_COST = {
    0: 2,      # 锻体
    1: 4,      # 练气
    2: 10,     # 筑基
    3: 20,     # 金丹
    4: 40,     # 具灵
    5: 80,     # 元婴
    6: 250,    # 化神
    7: 500,    # 悟道
    8: 750,    # 羽化
    9: 1000,   # 渡劫
}

def get_realm_name(level):
    if 0 <= level < len(REALMS):
        return REALMS[level]
    return "未知"

def get_stage_name(progress):
    if progress >= 100:
        return "大圆满"
    elif progress >= 66:
        return "后期"
    elif progress >= 33:
        return "中期"
    else:
        return "前期"

# ===== 飞升帮助 =====
feisheng_help_cmd = on_command("飞升帮助", aliases={"修仙帮助"}, priority=5, block=True)

feisheng_help_txt = """
飞升之路帮助：
【飞升阶段】
1. 宠物飞升 - 开启飞升之路的第一步
   - 条件：宠物已誓约
   - 消耗：每次消耗1个【誓约戒指】
   - 说明：每次增加15-30点进度，满100点即飞升成功

【修炼阶段】
1. 修炼 - 提升自身境界
   - 条件：完成宠物飞升
   - 限制：每日限5次
   - 消耗：消耗宝石（随境界提升）
   - 说明：增加修炼进度，需达到大圆满（100%）才能突破

2. 普通突破 / 幸运突破 - 跨越境界瓶颈
   - 条件：当前境界修炼至大圆满
   - 说明：概率失败。失败扣除50%进度。

3. 幸运突破 - 增加突破成功率
   - 条件：消耗对应境界的【突破丹药】
   - 说明：成功率大幅提升！

4. 购买 [物品] [数量] - 购买飞升道具
5. 修仙背包 - 查看拥有的飞升道具
6. 修仙商店 - 查看出售的道具列表
7. 升仙榜 - 查看修仙排行榜
7. 获取激活码 - 飞升后获取SU激活码，跳出三界之外~

【最终阶段】
渡劫飞升 - 消耗【升仙丸】尝试升入仙界！
"""

@feisheng_help_cmd.handle()
async def handle_feisheng_help(event: Event, bot: Bot):
    chain = await build_forward_chain(bot, [feisheng_help_txt])
    await send_group_forward_msg(event, bot, chain)


# ===== 宠物飞升 (不变) =====
pet_ascend_cmd = on_command("宠物飞升", priority=5, block=True)

@pet_ascend_cmd.handle()
async def handle_pet_ascend(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await pet_ascend_cmd.finish("你还没有宠物，无法进行宠物飞升！")
    
    max_hunger = pet.get("max_hunger", 0)
    max_happiness = pet.get("max_happiness", 0)
    max_energy = pet.get("max_energy", 0)
    
    status = min(max_hunger, max_happiness, max_energy)
    
    if status <= 999999:
        await pet_ascend_cmd.finish("你的宠物还未达到誓约状态，当前的羁绊还不足以支撑飞升！")
    
    fs_data = await get_feisheng_data(uid)
    if fs_data["is_pet_ascended"]:
        await pet_ascend_cmd.finish("你的宠物已经成功飞升了！请使用 飞升 指令开启新篇章。")
        
    if not await use_user_item(uid, "誓约戒指", 1):
        await pet_ascend_cmd.finish("宠物飞升需要消耗1个【誓约戒指】！")
        
    progress_add = random.randint(15, 30)
    new_data = await increase_pet_ascension_progress(uid, progress_add)
    
    current_progress = new_data["pet_ascension_progress"]
    
    if new_data["is_pet_ascended"]:
        msg = "消耗了1个誓约戒指...\n"
        msg += f"一道金光闪过，进度+{progress_add}，{pet['name']}开始飞升...\n"
        msg += f"恭喜！你的宠物【{pet.get('name', '宠物')}】已成功完成飞升仪式！前往了更高的维度...\n"
        msg += f"\n {pet['name']}回首人间，心觉不舍，强行为你打开了飞升之路。发送 飞升之路 查看进度。"
        await pet_ascend_cmd.finish(msg)
    else:
        msg = "消耗了1个誓约戒指...\n"
        msg += f"飞升仪式正在进行中，进度+{progress_add}\n"
        msg += f"当前进度：{current_progress}%"
        await pet_ascend_cmd.finish(msg)


# ===== 修炼 =====
cultivate_cmd = on_command("修炼", priority=5, block=True)

@cultivate_cmd.handle()
async def handle_cultivate(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    #检查每日限制
    if not await check_daily_cultivation_limit(uid, 5):
        await cultivate_cmd.finish("今日修炼次数已达上限（5次）！\n请注意劳逸结合，明日再来。")

    fs_data = await get_feisheng_data(uid)
    
    if not fs_data["is_pet_ascended"]:
        await cultivate_cmd.finish("请先完成【宠物飞升】开启修炼之路！")
        
    if fs_data["is_ascended"]:
        await cultivate_cmd.finish("你已飞升上界，无需再在凡间修炼。")
        
    current_realm = fs_data.get("realm_level", 0)
    current_progress = fs_data.get("ascension_progress", 0)
    
    if current_realm >= len(REALMS):
        await cultivate_cmd.finish("你的境界已达化境，无法继续修炼！")
        
    if current_progress >= 100:
        realm_name = get_realm_name(current_realm)
        await cultivate_cmd.finish(f"你的【{realm_name}】境界已臻至大圆满！\n请使用『突破』或『幸运突破』指令尝试突破瓶颈。")
    
    cost = CULTIVATION_COST.get(current_realm, 20000) # 默认最高消耗
    user_money = money.kirastone
    if user_money < cost:
         await cultivate_cmd.finish(f"修炼需要{cost}宝石，你只有{user_money}宝石！")
    money.kirastone -= cost
         
    progress_add = random.randint(5, 10)
    fs_data["ascension_progress"] = min(100, current_progress + progress_add)
    # 增加计数
    if not is_su(uid):
        fs_data["daily_cultivation_count"] = fs_data.get("daily_cultivation_count", 0) + 1
        fs_data["cultivation_date"] = fs_data.get("cultivation_date", "") 
    
    await update_feisheng_data(uid, fs_data)
    
    realm_name = get_realm_name(current_realm)
    new_progress = fs_data["ascension_progress"]
    stage = get_stage_name(new_progress)
    
    msg = "🧘开始修炼...\n"
    msg += f"消耗{cost}宝石，吸纳天地灵气...\n"
    msg += f"进度+{progress_add}，当前境界：{realm_name}境 {stage} ({new_progress}%)"
    msg += f"\n(今日已修炼 {fs_data['daily_cultivation_count']}/5 次)"
    
    if new_progress >= 100:
        if current_realm >= len(REALMS) - 1:
             msg += "\n🎉 境界已圆满，可以飞升！\n请使用 渡劫飞升 尝试飞升上界！"
        else:
            b_rate = max(10, 90 - (current_realm * 8))
            l_rate = b_rate + (100 - b_rate) // 2
            msg += f"\n🎉 境界已圆满，可以突破！\n普通突破 成功率{b_rate}% \n幸运突破 成功率{l_rate}%"
        
    await cultivate_cmd.finish(msg)


# ===== 修仙商店 =====
shop_cmd = on_command("修仙商店", priority=5, block=True)

@shop_cmd.handle()
async def handle_shop(event: Event, bot: Bot):
    msg = " 修仙商店\n━━━━━━━━━━\n"
    msg += "【境界突破丹】\n(幸运突破需要用到突破丹，可以增加突破成功的概率。)\n（如不想使用突破丹，请直接发送 突破）\n"
    for idx, info in PILL_CONFIG.items():
        if idx < len(REALMS) - 1: # 只要到羽化->渡劫
            realm_name = get_realm_name(idx)
            next_realm = get_realm_name(idx + 1)
            msg += f"• {info['name']}: {info['price']}宝石 ({realm_name}->{next_realm})\n"
            
    msg += "\n【其它】\n"
    msg += f"• {SHENGXIAN_WAN['name']}: {SHENGXIAN_WAN['price']}宝石 (飞升道具)\n"
    msg += "\n使用: 购买 [物品名] [数量]"
    
    chain = await build_forward_chain(bot, [msg])
    await send_group_forward_msg(event, bot, chain)


# ===== 购买 =====
buy_item_cmd = on_command("购买", priority=5, block=True)

@buy_item_cmd.handle()
async def handle_buy_item(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    arg_parts = args.extract_plain_text().split()
    if not arg_parts:
        await buy_item_cmd.finish("请指定购买物品和数量，例如：购买 聚气丹 1")
        
    item_name = arg_parts[0]
    quantity = 1
    if len(arg_parts) > 1:
        try:
            quantity = int(arg_parts[1])
            if quantity <= 0:
                await buy_item_cmd.finish("数量必须是正整数！")
        except ValueError:
            await buy_item_cmd.finish("数量格式错误！")
             
    # 查找物品价格
    price = 0
    found = False
    
    if item_name == SHENGXIAN_WAN["name"]:
        price = SHENGXIAN_WAN["price"]
        found = True
    else:
        for info in PILL_CONFIG.values():
            if info["name"] == item_name:
                price = info["price"]
                found = True
                break
    
    if not found:
        #await buy_item_cmd.finish(f"商店里没有【{item_name}】这个物品！")
        return
        
    total_cost = price * quantity
    user_money = money.kirastone
    if user_money < total_cost:
        await buy_item_cmd.finish(f"宝石不足！购买{quantity}个{item_name}需要{total_cost}宝石，你只有{user_money}宝石。")
    money.kirastone -= total_cost
    await add_feisheng_item(uid, item_name, quantity)
    await buy_item_cmd.finish(f"✅ 成功购买了{quantity}个{item_name}！\n花费了{total_cost}宝石。")


# ===== 修仙背包 =====
backpack_cmd = on_command("修仙背包", priority=5, block=True)

@backpack_cmd.handle()
async def handle_backpack(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    items = await get_user_feisheng_items(uid)
    if not items:
        await backpack_cmd.finish("你的修仙背包空空如也...")
        
    msg = "修仙背包\n━━━━━━━━━━\n"
    for name, count in items.items():
        msg += f"• {name} ×{count}\n"
        
    await backpack_cmd.finish(msg)


# ===== 通用突破逻辑 =====
async def process_breakthrough(uid: int, bot: Bot, event: Event, use_lucky: bool):
    fs_data = await get_feisheng_data(uid)
    
    if not fs_data["is_pet_ascended"]:
        await bot.send(event, "\n仙路尚未开启，请先完成宠物飞升...")
        return
        
    if fs_data["is_ascended"]:
        await bot.send(event, "\n你已经在上界了！")
        return
        
    current_realm = fs_data.get("realm_level", 0)
    current_progress = fs_data.get("ascension_progress", 0)
    realm_name = get_realm_name(current_realm)
    
    if current_progress < 100:
        await bot.send(event, f"\n你的【{realm_name}】境界根基未稳({current_progress}%)，无法突破！\n请继续 修炼 至大圆满。")
        return
        
    if current_realm >= len(REALMS) - 1:
        await bot.send(event, f"\n你已达【{realm_name}】大圆满，人间已无路可进！\n请使用 渡劫飞升 指令尝试飞升上界！")
        return

    # 基础概率
    base_rate = 90 - (current_realm * 8)
    base_rate = max(10, base_rate)
    
    final_rate = base_rate
    msg = ""
    
    if use_lucky:
        # 检查是否有对应丹药
        if current_realm not in PILL_CONFIG:
            await bot.send(event, "\n本境界无法使用丹药突破（或配置缺失）！")
            return
            
        pill_name = PILL_CONFIG[current_realm]["name"]
        if not await use_feisheng_item(uid, pill_name, 1):
             await bot.send(event, f"\n幸运突破需要消耗【{pill_name}】，你没有该物品！\n请去『修仙商店』购买。")
             return
             
        bonus = (100 - base_rate) // 2
        final_rate += bonus
        msg = f"服用了{pill_name}，药力流转全身...\n突破成功率提升了{bonus}%！(当前: {final_rate}%)"
    else:
        msg = f"当前突破成功率: {base_rate}%\n"
    
    roll = random.randint(1, 100)
    
    if roll <= final_rate:
        fs_data["realm_level"] += 1
        fs_data["ascension_progress"] = 0
        await update_feisheng_data(uid, fs_data)
        
        new_realm = get_realm_name(fs_data["realm_level"])
        msg += f"突破成功！\n恭喜你突破瓶颈，晋升为【{new_realm}】！"
        await bot.send(event, msg)
    else:
        fs_data["ascension_progress"] = 50
        await update_feisheng_data(uid, fs_data)
        
        msg += "突破失败！\n境界跌落至中期。"
        await bot.send(event, msg)

# ===== 普通突破 =====
breakthrough_cmd = on_command("普通突破", aliases={"突破"}, priority=5, block=True)

@breakthrough_cmd.handle()
async def handle_breakthrough(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    await process_breakthrough(uid, bot, event, use_lucky=False)

# ===== 幸运突破 =====
lucky_breakthrough_cmd = on_command("幸运突破", priority=5, block=True)

@lucky_breakthrough_cmd.handle()
async def handle_lucky_breakthrough(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    await process_breakthrough(uid, bot, event, use_lucky=True)


# ===== 飞升 =====
ascend_cmd = on_command("渡劫飞升", priority=5, block=True)


def _ascension_confirmation(pill_name: str, has_pill: bool) -> str:
    msg = "【飞升提醒】\n"
    if has_pill:
        msg += f"检测到你持有【{pill_name}】，使用它护体可保 100% 飞升成功！\n"
        return msg + "发送『渡劫飞升 确认』开始渡劫。"
    msg += f"警告：你当前【未持有】{pill_name}！\n"
    msg += "强行渡劫成功率仅为 50%\n"
    msg += "失败后果：【身死道消】，境界重置为锻体！\n"
    return msg + "若执意逆天而行，请发送『渡劫飞升 确认』。"


async def _attempt_ascension(uid: int, pill_name: str, has_pill: bool):
    if has_pill:
        if not await use_feisheng_item(uid, pill_name, 1):
            return None, "物品消耗失败，请稍后再试。"
        return True, f"吞服{pill_name}，药力化作金光护住心脉...\n"
    return (
        random.randint(1, 100) <= 50,
        "你仰天长啸，决意以肉身硬撼天劫！\n",
    )


def _ascension_story(success: bool, pet_name: str) -> str:
    if success:
        return (
            "轰——！九道紫霄神雷落下，却被你的无上法力尽数化解！\n"
            "天门洞开，仙音缭绕，一道接引神光将你缓缓托起...\n"
            "恭喜道友成功渡劫飞升，位列仙班！\n"
            "仙气缭绕的上界，你远远地就望见一道熟悉的身影...\n"
            f"“主人！终于...又见面了...”{pet_name}紧紧地抱着你...一如初见。\n"
            f"“对了，主人，{pet_name}独自在上界这么多年，已经为主人攒好了见面礼哦~”\n"
            "(发送 注册激活码 可以获取su权限)"
        )
    return (
        "轰——！最后一道灭世神雷挟毁天灭地之威落下，瞬间击碎了你的护体灵气...\n"
        "肉身崩解，神魂将散，你感到无尽的冰冷与黑暗袭来，意识逐渐模糊。\n"
        "“就这样结束了吗...”\n"
        f"就在你即将湮灭之际，一个熟悉的身影——{pet_name}逆流而下，拼尽全力挡在了你的残魂之前。\n"
        "“主人，这一次，该我来守护你了...”\n"
        "金光包裹着你的那一缕真灵，强行冲破了轮回的迷雾，而借来的仙力也随即消散...\n"
        "\n......\n"
        "不知过了多久，你再次惊醒，发现自己躺在熟悉的草地上，已是凡人之躯。\n"
        "虽然千载修为尽失(重置为锻体)，但掌心残留的温热让你明白，这一世，绝不能再辜负这份羁绊。"
    )


@ascend_cmd.handle()
async def handle_ascend(event: Event, bot: Bot, args: Message = CommandArg(), uid: int = Depends(get_uid)):
    fs_data = await get_feisheng_data(uid)
    
    if not fs_data["is_pet_ascended"]:
        await ascend_cmd.finish("你还没有完成【宠物飞升】，无法进行飞升！\n请先发送『宠物飞升』。")
        
    if fs_data["is_ascended"]:
        await ascend_cmd.finish("你已经飞升过了，快去探索新世界吧！")
        
    current_realm = fs_data.get("realm_level", 0)
    current_progress = fs_data.get("ascension_progress", 0)
    last_realm_index = len(REALMS) - 1 # 9 渡劫
    
    if current_realm < last_realm_index or (current_realm == last_realm_index and current_progress < 100):
        await ascend_cmd.finish("你的境界未达【渡劫】大圆满，无法承受飞升雷劫！\n请继续修炼。")
    
    arg_str = args.extract_plain_text().strip()
    pill_name = SHENGXIAN_WAN["name"]
    user_items = await get_user_feisheng_items(uid)
    has_pill_count = user_items.get(pill_name, 0)
    
    if arg_str != "确认":
        await ascend_cmd.finish(
            _ascension_confirmation(pill_name, has_pill_count > 0),
        )

    success, msg = await _attempt_ascension(uid, pill_name, has_pill_count > 0)
    if success is None:
        await ascend_cmd.finish(msg)

    pet = await get_user_pet(uid)
    pet_name = pet.get("name", "昔日的伙伴") if pet else "昔日的伙伴"
    if success:
        fs_data["is_ascended"] = 1
    else:
        fs_data["realm_level"] = 0
        fs_data["ascension_progress"] = 0
    await update_feisheng_data(uid, fs_data)
    await ascend_cmd.finish(msg + _ascension_story(success, pet_name))


# ===== 飞升之路 (查询进度) =====
feisheng_path_cmd = on_command("飞升之路", priority=5, block=True)


def _format_feisheng_progress(fs_data: dict) -> str:
    pet_progress = fs_data["pet_ascension_progress"]
    is_pet_ascended = fs_data["is_pet_ascended"]
    is_ascended = fs_data["is_ascended"]
    realm_level = fs_data.get("realm_level", 0)
    ascension_progress = fs_data.get("ascension_progress", 0)
    realm_name = get_realm_name(realm_level)
    stage_name = get_stage_name(ascension_progress)

    if not is_pet_ascended:
        return (
            f"【宠物飞升】：进行中 ({pet_progress}%)\n"
            "【个人境界】：仙路未开启\n"
        )
    if is_ascended:
        return f"【宠物飞升】：已完成\n【个人境界】：{realm_name} (已飞升)\n"
    return (
        "【宠物飞升】：已完成\n"
        f"【个人境界】：{realm_name}境 {stage_name} ({ascension_progress}%)\n"
    )


def _next_feisheng_action(fs_data: dict) -> str:
    if not fs_data["is_pet_ascended"]:
        return "消耗誓约戒指进行宠物飞升"
    if fs_data["is_ascended"]:
        return "你已立于云端之上。\n可用 获取激活码 获得SU权限。"

    ascension_progress = fs_data.get("ascension_progress", 0)
    if ascension_progress < 100:
        return "消耗宝石进行修炼"

    realm_level = fs_data.get("realm_level", 0)
    if realm_level >= len(REALMS) - 1:
        return "已达渡劫大圆满，请使用 渡劫飞升"
    base_rate = max(10, 90 - (realm_level * 8))
    lucky_rate = base_rate + (100 - base_rate) // 2
    return (
        "境界已圆满，可以突破！\n"
        f"普通突破 成功率{base_rate}% \n幸运突破 成功率{lucky_rate}%"
    )


@feisheng_path_cmd.handle()
async def handle_feisheng_path(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await feisheng_path_cmd.finish("你还没有宠物，未开启飞升之路。")
    
    max_hunger = pet.get("max_hunger", 0)
    max_happiness = pet.get("max_happiness", 0)
    max_energy = pet.get("max_energy", 0)
    if min(max_hunger, max_happiness, max_energy) <= 999999:
        await feisheng_path_cmd.finish(f"你的宠物【{pet.get('name')}】尚未誓约，未达到开启飞升之路的条件。")
        
    fs_data = await get_feisheng_data(uid)
    msg = "飞升之路━━━━━━━━━━━━━━\n"
    msg += _format_feisheng_progress(fs_data)
    msg += "━━━━━━━━━━━━━━\n"
    msg += _next_feisheng_action(fs_data)
    await feisheng_path_cmd.finish(msg)


# ===== 获取激活码 =====

async def generate_su_code(uid: int) -> str:
    """通过远程服务获取激活码"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:5000/salt?uid={uid}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("key", "")
                else:
                    logger.error(f"[feisheng] 获取激活码失败，HTTP状态码: {resp.status}")
                    return ""
    except Exception as e:
        logger.error(f"[feisheng] 获取激活码异常: {e}")
        return ""

get_code_cmd = on_command("获取激活码", aliases={"注册激活码"}, priority=5, block=True)

@get_code_cmd.handle()
async def handle_get_code(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    fs_data = await get_feisheng_data(uid)
    
    if not fs_data["is_ascended"]:
        await get_code_cmd.finish("你尚未飞升，无法获取激活码！\n请继续努力修炼。")
        
    code = await generate_su_code(uid)
    if not code:
        await get_code_cmd.finish("激活码获取失败，请稍后再试。")
    await get_code_cmd.finish(f"你的SU激活码：\n{code}\n\n使用方式：注册su 激活码\n(例如：注册su 123）\n（激活码当天有效，过期请重新获取）")


# ===== 升仙榜 =====
feisheng_rank_cmd = on_command("升仙榜", aliases={"修仙榜"}, priority=5, block=True)

@feisheng_rank_cmd.handle()
async def handle_feisheng_rank(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    
    # 获取排行数据 (取前50用于过滤)
    raw_rank_data = await get_feisheng_leaderboard(50)
    
    if not raw_rank_data:
        await feisheng_rank_cmd.finish("排行榜暂无数据，快去修炼吧！")
        
    # 获取SU列表用于过滤
    su_uids = get_excluded_su_uids()
    
    # 过滤掉SU
    filtered_rank = [row for row in raw_rank_data if row["uid"] not in su_uids]
    
    # 截取前10
    top_10 = filtered_rank[:10]
    
    if not top_10:
        await feisheng_rank_cmd.finish("排行榜暂无数据（所有数据均为SU或无数据）。")
        
    msg = "🏆 升仙榜-TOP10 🏆\n"
    
    for idx, row in enumerate(top_10, 1):
        r_uid = row["uid"]
        owner_name = get_user_nickname(int(r_uid)) or f"UID {r_uid}"
        realm = row["realm_level"]
        progress = row["ascension_progress"]
        is_ascended = row["is_ascended"]
        
        realm_str = get_realm_name(realm)
        
        if is_ascended:
             msg += f"{idx}. {owner_name} - {realm_str} (已飞升)\n"
        else:
             msg += f"{idx}. {owner_name} - {realm_str} ({progress}%)\n"
             
    # 查询自己排名
    my_rank = -1
    for idx, row in enumerate(filtered_rank, 1):
         if row["uid"] == uid:
             my_rank = idx
             break
             
    if my_rank != -1:
        msg += f"\n您的排名: 第{my_rank}名"
    else:
        msg += "\n您的排名: 未上榜"
    msg += "\n\n\n使用 冰祈请叫我 可以修改自己的昵称哦~"

    chain = await build_forward_chain(bot, [msg])
    await send_group_forward_msg(event, bot, chain)
