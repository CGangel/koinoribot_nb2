"""
宠物插件 - chongwu

完整迁移自旧版 koinoribot
功能：扭蛋、领养、投喂、玩耍、进化、技能、商店
"""

import random
import time
import math
from datetime import date, datetime
from typing import Optional

from nonebot import on_command, get_driver
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Event, Bot, Message
from nonebot import logger
from nonebot.params import CommandArg, Depends

from ...money import money
from ...config_store import config
from ...tools import get_uid, send_group_forward_msg, build_forward_chain

from .petconfig import (
    GACHA_REWARDS, GACHA_CONSOLE_PRIZE, GACHA_CONFIG,
    EVOLUTIONS, PET_SHOP_ITEMS, PET_SKILLS,
    GROWTH_STAGE_1, GROWTH_STAGE_2
)
from .pet import (
    set_db_path, init_pet_database,
    get_user_pet, update_user_pet, remove_user_pet, get_user_pets,
    get_user_items, add_user_item, use_user_item,
    get_pet_data, get_status_description, update_pet_status, check_pet_evolution
)
from ...su_manager import is_su
from ..fishing.util import DatabaseManager
from ...nickname import get_user_nickname
from ...su_manager import get_excluded_su_uids
from ..feisheng.data import get_all_pet_feisheng_status

__plugin_meta__ = PluginMetadata(
    name="chongwu",
    description="宠物养成系统 - 完整版",
    usage="宠物帮助 / 开启扭蛋 / 我的宠物 等",
)

NO_PET_MESSAGE = "你还没有宠物！"
ORIGINAL_CONTRACT_ITEM = "最初的契约"


# ===== 宠物帮助 =====
pet_help_cmd = on_command("宠物帮助", priority=5, block=True)

# 宠物帮助内容（迁移自old_bot完整版）
pet_help = f"""
宠物养成系统帮助：
【扭蛋系统】
1. 购买普通/高级/传说扭蛋 [数量] - 购买宠物扭蛋
2. 开启扭蛋 - 开启一个扭蛋(可能获得宠物或安慰奖)
3. 领养宠物 [名字] - 领养扭蛋获得的宠物
4. 放弃宠物 - 放弃扭蛋获得的宠物

【宠物用品】
1. 宠物商店 - 查看可购买的宠物用品
2. 购买 [名称] [数量] - 购买指定宠物用品
3. 退还 [名称] [数量] - 以『50%的价格』退还指定宠物用品
4. 宠物背包 - 查看拥有的宠物用品
5. 投喂 [料理名称] [数量（可选）] 
6. 丟玩具球 - 消耗【玩具球】
7. 寻回宠物 - 消耗【{ORIGINAL_CONTRACT_ITEM}】
8. 重置进化路线 - 消耗【时之泪】
9. 进化宠物 - 消耗1个 【奶油蛋糕/豪华蛋糕】
10. 补充精力 - 消耗1个 【能量饮料】
11.学习技能 - 消耗1个 【技能药水】（具体请发送 技能帮助）
12.遗忘 [技能名称] - 消耗1个 【遗忘药水】
13.永恒誓约 - 消耗1个 【誓约戒指】

【宠物管理】
1. 我的宠物 - 查看宠物状态
2. 摸摸宠物 - 陪伴宠物（恢复好感）
3. 宠物改名 [新名字] - 为宠物改名
4. 放生宠物 确认 - 放生当前宠物
5. 宠物事件 - 触发宠物的所有技能
6. 技能百科 - 查看可学习的技能列表

【其他】
1. 买宝石 [数量] - 购买宝石
2. 卖宝石 [数量] - 退还宝石
3. 宠物帮助 - 显示本帮助
4. 宠物排行榜 - 查看成长值最高的成年体宠物
5. 宠物排名 - 查看自己宠物的排名

【温馨提醒】
1. 当饱食度或精力值过低时，好感度会迅速下降
2. 当好感度过低时，宠物会离家出走
3. 离家出走期间，宠物将停止长大
4. 排行榜功能需要宠物成长至完全体才能开启


"""

@pet_help_cmd.handle()
async def handle_pet_help(event: Event, bot: Bot):
    chain = await build_forward_chain(bot, [pet_help])
    await send_group_forward_msg(event, bot, chain)


# ===== 买宝石 =====
buy_kirastone_cmd = on_command("买宝石", priority=5, block=True)
@buy_kirastone_cmd.handle()
async def handle_buy_kirastone(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    arg_parts = args.extract_plain_text().split()
    if not arg_parts or not arg_parts[0].isdigit():
        await buy_kirastone_cmd.finish("请指定购买数量，例如：买宝石 1", at_sender=True)
    quantity = int(arg_parts[0])
    if quantity <= 0:
        await buy_kirastone_cmd.finish("购买数量必须是正整数！", at_sender=True)
    price_per_gem = 1000
    total_cost = quantity * price_per_gem
    user_money = money.gold
    if user_money < total_cost:
        await buy_kirastone_cmd.finish(f"金币不足！购买{quantity}个宝石需要{total_cost}金币，你只有{user_money}金币。", at_sender=True)

    money.gold -= total_cost
    money.kirastone += quantity
    await buy_kirastone_cmd.finish(f"你成功购买了{quantity}枚宝石，花费了{total_cost}金币！", at_sender=True)

# ===== 卖宝石 =====
sell_kirastone_cmd = on_command("卖宝石", priority=5, block=True)
@sell_kirastone_cmd.handle()
async def handle_sell_kirastone(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    arg_parts = args.extract_plain_text().split()
    if not arg_parts or not arg_parts[0].isdigit():
        await sell_kirastone_cmd.finish("请指定出售数量，例如：卖宝石 1", at_sender=True)
    quantity = int(arg_parts[0])
    if quantity <= 0:
        await sell_kirastone_cmd.finish("出售数量必须是正整数！", at_sender=True)
    user_gems = money.kirastone
    if user_gems < quantity:
        await sell_kirastone_cmd.finish(f"宝石不足！你只有{user_gems}枚宝石，无法出售{quantity}枚。", at_sender=True)

    money.kirastone -= quantity
    fee = int(quantity * 1000 * config.stone_fee)
    gold_earned = quantity * 1000 - fee
    money.gold += gold_earned
    await sell_kirastone_cmd.finish(f"你成功出售了{quantity}枚宝石，获得了{gold_earned}金币。(已自动扣除{fee}金币手续费)", at_sender=True)

# ===== 开启扭蛋 =====
open_gacha_cmd = on_command("开启", priority=5, block=True)


async def _finish_existing_pet_gacha(
    uid: int,
    pet_data: dict,
    gacha_name: str,
) -> None:
    if "temp_data" in pet_data:
        await add_user_item(uid, gacha_name)
        await open_gacha_cmd.finish(
            f"你已有一只宠物({pet_data['type']})等待领养，请先领养或放弃。",
            at_sender=True,
        )
        return
    if random.random() < 0.9:
        money.gold += GACHA_CONSOLE_PRIZE
        await open_gacha_cmd.finish(
            f"你已经有宠物了，本次扭蛋里没有宠物，获得{GACHA_CONSOLE_PRIZE}金币安慰奖...",
            at_sender=True,
        )
        return
    money.luckygold += 1
    await open_gacha_cmd.finish(
        "你已经有宠物了，本次扭蛋里没有宠物，但有1枚幸运币...",
        at_sender=True,
    )


def _select_gacha_pool(gacha_name: str) -> Optional[dict]:
    if gacha_name == "传说扭蛋":
        return GACHA_REWARDS["传说"]
    if gacha_name == "高级扭蛋":
        roll = random.random() * 100
        if roll < 55:
            return GACHA_REWARDS["稀有"]
        if roll < 90:
            return GACHA_REWARDS["史诗"]
        return GACHA_REWARDS["传说"]
    if random.random() < 0.5:
        return None

    roll = random.random() * 100
    if roll < 55:
        return GACHA_REWARDS["普通"]
    if roll < 80:
        return GACHA_REWARDS["稀有"]
    if roll < 98:
        return GACHA_REWARDS["史诗"]
    return GACHA_REWARDS["传说"]


async def _finish_empty_gacha() -> None:
    if random.random() < 0.8:
        money.gold += GACHA_CONSOLE_PRIZE
        await open_gacha_cmd.finish(
            f"扭蛋里没有宠物，获得{GACHA_CONSOLE_PRIZE}金币安慰奖...",
            at_sender=True,
        )
        return
    money.luckygold += 1
    await open_gacha_cmd.finish(
        "扭蛋里没有宠物，但有1枚幸运币...",
        at_sender=True,
    )


@open_gacha_cmd.handle()
async def handle_open_gacha(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    gacha_name = args.extract_plain_text()
    if not gacha_name or "扭蛋" not in gacha_name:
        return
    
    if gacha_name not in GACHA_CONFIG:
        await open_gacha_cmd.finish(f"未知的扭蛋类型，可用: {', '.join(GACHA_CONFIG.keys())}", at_sender=True)
    
    if not await use_user_item(uid, gacha_name):
        await open_gacha_cmd.finish(f"你没有[{gacha_name}]！使用'购买 {gacha_name}'来获取。", at_sender=True)
    
    pet_data = await get_user_pet(uid)
    if pet_data:
        return await _finish_existing_pet_gacha(uid, pet_data, gacha_name)

    pool = _select_gacha_pool(gacha_name)
    if pool is None:
        return await _finish_empty_gacha()

    pet_type = random.choices(
        list(pool),
        weights=list(pool.values()),
    )[0]
    temp_pet = {
        "type": pet_type,
        "temp_data": True,
        "gacha_time": time.time(),
    }
    await update_user_pet(uid, temp_pet)
    rarity = get_pet_data()[pet_type]["rarity"]
    await open_gacha_cmd.finish(
        f"🎉 恭喜！从[{gacha_name}]中抽中了{rarity}宠物【{pet_type}】！\n"
        f"请使用'领养宠物 [名字]'来领养她，或使用'放弃宠物'放弃。",
        at_sender=True,
    )


# ===== 领养宠物 =====
adopt_cmd = on_command("领养宠物", priority=5, block=True)

@adopt_cmd.handle()
async def handle_adopt(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    pet_name = args.extract_plain_text()
    
    if not pet_name:
        await adopt_cmd.finish("请为你的宠物取个名字！\n例如：领养宠物 小白", at_sender=True)
    
    if len(pet_name) > 10:
        await adopt_cmd.finish("宠物名字太长了，最多10个字符！", at_sender=True)
    
    temp_pet = await get_user_pet(uid)
    if not temp_pet or not temp_pet.get("temp_data"):
        await adopt_cmd.finish("你没有待领养的宠物，不妨试试开启扭蛋获取一个？", at_sender=True)
    
    user_pets = await get_user_pets()
    for other_uid, pet in user_pets.items():
        if pet.get("name") == pet_name and other_uid != uid:
            await adopt_cmd.finish(f"名字'{pet_name}'已被使用，请换一个！", at_sender=True)
    
    pet_type = temp_pet["type"]
    pet_data = get_pet_data()
    base_pet = pet_data[pet_type]

    new_pet = {
        "type": pet_type,
        "name": pet_name,
        "hunger": base_pet["max_hunger"],
        "energy": base_pet["max_energy"],
        "happiness": base_pet["max_happiness"],
        "max_hunger": base_pet["max_hunger"],
        "max_energy": base_pet["max_energy"],
        "max_happiness": base_pet["max_happiness"],
        "growth": 0,
        "growth_rate": base_pet["growth_rate"],
        "stage": 0,
        "growth_required": GROWTH_STAGE_1,
        "last_event_date": None,
        "skills": [],
        "runaway": False,
        "last_update": time.time(),
        "adopted_time": time.time()
    }
    
    await update_user_pet(uid, new_pet)
    await adopt_cmd.finish(f"🎉 恭喜！你成功领养了一只{pet_name}({pet_type})！", at_sender=True)


# ===== 放弃宠物 =====
cancel_adopt_cmd = on_command("放弃宠物", priority=5, block=True)

@cancel_adopt_cmd.handle()
async def handle_cancel_adopt(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    temp_pet = await get_user_pet(uid)
    if not temp_pet or not temp_pet.get("temp_data"):
        await cancel_adopt_cmd.finish("你没有待领养的宠物！", at_sender=True)
    
    pet_type = temp_pet["type"]
    await remove_user_pet(uid)
    await cancel_adopt_cmd.finish(f"你放弃了一只{pet_type}。", at_sender=True)


# ===== 我的宠物 =====
my_pet_cmd = on_command("我的宠物", priority=5, block=True)

@my_pet_cmd.handle()
async def handle_my_pet(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    pet = await get_user_pet(uid)
    if not pet:
        await my_pet_cmd.finish("你还没有宠物！使用'开启 普通扭蛋'来获取一个吧~", at_sender=True)
    
    if pet.get("temp_data"):
        await my_pet_cmd.finish(f"你有一只待领养的{pet['type']}，请使用'领养宠物 [名字]'来领养！", at_sender=True)
    
    pet = update_pet_status(pet)
    await update_user_pet(uid, pet)
    
    stage_names = ["幼年体", "成长体", "成年体"]
    stage_name = stage_names[min(pet["stage"], 2)]
    
    hunger_desc = get_status_description("hunger", pet["hunger"])
    energy_desc = get_status_description("energy", pet["energy"])
    happiness_desc = get_status_description("happiness", pet["happiness"])
    
    skills_str = "、".join(pet.get("skills", [])) or "暂无"
    
    adopted_time = pet.get("adopted_time")
    if adopted_time:
        adopted_date = datetime.fromtimestamp(adopted_time).strftime("%Y-%m-%d")
    else:
        adopted_date = "未知"
    
    status = min(pet["max_hunger"], pet["max_happiness"], pet["max_energy"])
    if status > 999999:
        if pet["stage"] == 2:
            growth_str = f'{pet["growth"]:.1f}'
        else:
            growth_str = f'{pet["growth"]:.1f}/{pet["growth_required"]}'
        msg = f"""
━━━━━━━━━━
名称: {pet['name']}
类型: {pet['type']} ({stage_name})
饱食度: 『满足』
精力: 『活泼』
好感度: 『爱慕』
成长值: {growth_str}
技能: {skills_str}
领养日期: {adopted_date}
请好好照顾她哦，也可以发送 宠物帮助 来查看全部指令~"""
    else:
        msg = f"""
━━━━━━━━━━
名称: {pet['name']}
类型: {pet['type']} ({stage_name})
饱食度: {pet['hunger']:.1f}/{pet['max_hunger']} ({hunger_desc})
精力: {pet['energy']:.1f}/{pet['max_energy']} ({energy_desc})
好感度: {pet['happiness']:.1f}/{pet['max_happiness']} ({happiness_desc})
成长值: {pet['growth']:.1f}/{pet['growth_required']}
技能: {skills_str}
领养日期: {adopted_date}
请好好照顾她哦，也可以发送 宠物帮助 来查看全部指令~"""
    
    if pet.get("runaway"):
        msg += "\n\n⚠️ 宠物已离家出走！使用'寻回宠物'找回~"
    
    await my_pet_cmd.finish(msg, at_sender=True)


# ===== 宠物商店 =====
shop_cmd = on_command("宠物商店", priority=5, block=True)

@shop_cmd.handle()
async def handle_shop(event: Event, bot: Bot):
    item_list = []
    for name, info in PET_SHOP_ITEMS.items():
        price = info["price"]
        effect = info.get("effect", "")
        item_list.append(f"• {name} - {price}宝石 ({effect})")
    
    msg = "🏪 宠物商店\n━━━━━━━━━━\n" + "\n".join(item_list)
    msg += "\n\n使用: 购买 物品名 数量"
    
    try:
        chain = await build_forward_chain(bot, [msg])
        await send_group_forward_msg(event, bot, chain)
    except Exception as e:
        logger.error(f"宠物商店合并消息发送失败: {e}")
        await shop_cmd.finish(msg)


# ===== 购买 =====
buy_cmd = on_command("购买", priority=5, block=True)

@buy_cmd.handle()
async def handle_buy(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    arg_parts = args.extract_plain_text().split()
    if not arg_parts:
        return
    
    item_name = arg_parts[0]
    try:
        quantity = int(arg_parts[1]) if len(arg_parts) > 1 else 1
        if quantity <= 0:
            await buy_cmd.finish("购买数量必须是正整数！", at_sender=True)
    except ValueError:
        await buy_cmd.finish("购买数量必须是有效的数字！", at_sender=True)
    
    if item_name not in PET_SHOP_ITEMS:
        return
    
    price = PET_SHOP_ITEMS[item_name]["price"] * quantity
    user_stones = money.kirastone
    
    if user_stones < price:
        await buy_cmd.finish(f"宝石不足！购买{quantity}个{item_name}需要{price}宝石，你只有{user_stones}宝石。", at_sender=True)
    
    money.kirastone -= price
    await add_user_item(uid, item_name, quantity)
    await buy_cmd.finish(f"✅ 成功购买了{quantity}个{item_name}！", at_sender=True)


# ===== 宠物背包 =====
pet_bag_cmd = on_command("宠物背包", priority=5, block=True)

@pet_bag_cmd.handle()
async def handle_pet_bag(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    user_items = await get_user_items(uid)
    
    if not user_items:
        await pet_bag_cmd.finish("你目前没有宠物用品。使用'购买'来获取。", at_sender=True)
    
    item_list = [f"• {name} ×{count}" for name, count in user_items.items()]
    msg = "\n宠物背包\n━━━━━━━━━\n" + "\n".join(item_list)
    
    await pet_bag_cmd.finish(msg, at_sender=True)


# ===== 退还宠物用品 =====
return_item_cmd = on_command("退还", priority=5, block=True)

@return_item_cmd.handle()
async def handle_return_item(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    arg_parts = args.extract_plain_text().split()
    
    if not arg_parts:
        return
    
    item_name = arg_parts[0]
    if item_name not in PET_SHOP_ITEMS:
        return
    
    user_items = await get_user_items(uid)
    count = user_items.get(item_name, 0)
    
    try:
        quantity = int(arg_parts[1]) if len(arg_parts) > 1 else 1
        if quantity <= 0:
            await return_item_cmd.finish("退还数量必须是正整数！", at_sender=True)
        if count < quantity:
            await return_item_cmd.finish(f"你当前只有{count}个{item_name}！", at_sender=True)
    except ValueError:
        await return_item_cmd.finish("退还数量必须是有效的数字！", at_sender=True)
    
    return_fee = getattr(config, 'return_item_fee', 0.5)
    price = int(PET_SHOP_ITEMS[item_name]["price"] * quantity * return_fee)
    fee_percent = int(return_fee * 100)
    
    if await use_user_item(uid, item_name, quantity):
        money.kirastone += price
        await return_item_cmd.finish(
            f"按照{fee_percent}%的价格成功退还了{quantity}个{item_name}！\n你获得了{price}个宝石。", at_sender=True)
    else:
        await return_item_cmd.finish("操作失败，请稍后再试！", at_sender=True)


# ===== 投喂 =====
feed_cmd = on_command("投喂", priority=5, block=True)

@feed_cmd.handle()
async def handle_feed(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    arg_text = args.extract_plain_text().strip()
    arg_parts = arg_text.split()
    
    if not arg_parts:
         await feed_cmd.finish("请指定食物：普通/高级/豪华料理\n例如：投喂 高级料理", at_sender=True)

    food_type = arg_parts[0]
    quantity = 1
    
    if len(arg_parts) > 1:
        try:
            quantity = int(arg_parts[1])
            if quantity <= 0:
                 await feed_cmd.finish("投喂数量必须是正整数！", at_sender=True)
        except ValueError:
            await feed_cmd.finish("投喂数量必须是有效的数字！", at_sender=True)
    
    valid_foods = {"普通料理", "高级料理", "豪华料理"}
    if food_type not in valid_foods:
        await feed_cmd.finish("请指定正确的食物：普通/高级/豪华料理\n例如：投喂 高级料理 5", at_sender=True)
    
    # 检查是否有足够的物品
    user_items = await get_user_items(uid)
    owned_count = user_items.get(food_type, 0)
    
    if owned_count < quantity:
         await feed_cmd.finish(f"你的{food_type}不足！当前拥有: {owned_count}个，需要: {quantity}个。", at_sender=True)

    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await feed_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    pet = update_pet_status(pet)
    if pet.get("runaway"):
        await feed_cmd.finish(f"你的宠物【{pet['name']}】离家出走了，无法投喂！", at_sender=True)
    
    # 扣除物品
    if not await use_user_item(uid, food_type, quantity):
         await feed_cmd.finish("扣除物品失败，请稍后再试。", at_sender=True)

    item = PET_SHOP_ITEMS[food_type]
    
    # 应用属性加成 (乘以数量)
    total_hunger = item["hunger"] * quantity
    total_energy = item["energy"] * quantity
    total_happiness = item["happiness"] * quantity
    total_growth = item["growth"] * quantity
    
    pet["hunger"] = min(pet["max_hunger"], pet["hunger"] + total_hunger)
    pet["energy"] = min(pet["max_energy"], pet["energy"] + total_energy)
    pet["happiness"] = min(pet["max_happiness"], pet["happiness"] + total_happiness)
    pet["growth"] = min(pet["growth_required"], pet["growth"] + total_growth)
    
    await update_user_pet(uid, pet)
    
    msg = f"你给{pet['name']}投喂了{quantity}份{food_type}！\n"
    msg += f"饱食度+{total_hunger} \n精力+{total_energy} \n"
    msg += f"好感度+{total_happiness} \n成长值+{total_growth}"
    
    await feed_cmd.finish(msg, at_sender=True)


# ===== 摸摸宠物 =====
pat_cmd = on_command("摸摸宠物", priority=5, block=True)

@pat_cmd.handle()
async def handle_pat(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await pat_cmd.finish(NO_PET_MESSAGE, at_sender=True)

    pet = update_pet_status(pet)

    if pet["energy"] < 20:
        await pat_cmd.finish(f"{pet['name']}太累了，需要休息！", at_sender=True)
    
    pet["energy"] = max(0, pet["energy"] - 5)
    pet["happiness"] = min(pet["max_happiness"], pet["happiness"] + 15)
    await update_user_pet(uid, pet)
    
    await pat_cmd.finish(
        f"{pet['name']}很享受你的抚摸，用脸蛋轻轻蹭了蹭你的手...\n精力-5 \n好感+15", at_sender=True)


# ===== 补充精力 =====
energy_cmd = on_command("补充精力", priority=5, block=True)

@energy_cmd.handle()
async def handle_energy(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    if not await use_user_item(uid, "能量饮料"):
        await energy_cmd.finish("你没有能量饮料！", at_sender=True)
    
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await add_user_item(uid, "能量饮料")
        await energy_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    pet = update_pet_status(pet)
    if pet.get("runaway"):
        await add_user_item(uid, "能量饮料")
        await energy_cmd.finish(f"你的宠物【{pet['name']}】离家出走了！", at_sender=True)
    
    item = PET_SHOP_ITEMS["能量饮料"]
    pet["energy"] = min(pet["max_energy"], pet["energy"] + item["energy"])
    pet["happiness"] = min(pet["max_happiness"], pet["happiness"] + item["happiness"])
    
    await update_user_pet(uid, pet)
    await energy_cmd.finish(
        f"你给{pet['name']}喝了能量饮料，她立刻精神焕发！\n精力+{item['energy']} \n好感+{item['happiness']}", at_sender=True)


# ===== 丢玩具球 =====
energy_cmd = on_command("丟玩具球", aliases={"丢玩具球"}, priority=5, block=True)
@energy_cmd.handle()
async def handle_throw_ball(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    if not await use_user_item(uid, "玩具球"):
        await energy_cmd.finish("你没有玩具球！", at_sender=True)
    
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await add_user_item(uid, "玩具球")
        await energy_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    pet = update_pet_status(pet)
    if pet.get("runaway"):
        await add_user_item(uid, "玩具球")
        await energy_cmd.finish(f"你的宠物【{pet['name']}】离家出走了！", at_sender=True)
    
    item = PET_SHOP_ITEMS["玩具球"]
    pet["hunger"] = max(0, pet["hunger"] + item["hunger"])
    pet["energy"] = max(0, pet["energy"] + item["energy"])
    pet["happiness"] = min(pet["max_happiness"], pet["happiness"] + item["happiness"])
    
    await update_user_pet(uid, pet)
    await energy_cmd.finish(
        f"你给{pet['name']}丢出了玩具球，她开心地地追了过去！\n饱食度{item['hunger']}\n精力{item['energy']}\n好感度+{item['happiness']}", at_sender=True)

# ===== 学习技能 =====
learn_skill_cmd = on_command("学习技能", priority=5, block=True)

@learn_skill_cmd.handle()
async def handle_learn_skill(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    if not await use_user_item(uid, "技能药水"):
        await learn_skill_cmd.finish("你没有技能药水！购买需要50宝石。", at_sender=True)
    
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await add_user_item(uid, "技能药水")
        await learn_skill_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    pet = update_pet_status(pet)
    if pet.get("runaway"):
        await add_user_item(uid, "技能药水")
        await learn_skill_cmd.finish(f"你的宠物【{pet['name']}】离家出走了！", at_sender=True)
    
    available_skills = [s for s in PET_SKILLS.keys() if s not in pet.get("skills", [])]
    if not available_skills:
        await learn_skill_cmd.finish("你的宠物已学会所有技能！", at_sender=True)
    
    max_skills = 1 + pet["stage"] * 2
    status = min(pet["max_hunger"], pet["max_happiness"], pet["max_energy"])
    if status > 999999:
        max_skills += 999
    if len(pet.get("skills", [])) >= max_skills:
        await add_user_item(uid, "技能药水")
        await learn_skill_cmd.finish(f"技能槽已满（当前最多{max_skills}个）！", at_sender=True)
    
    if random.random() < 0.6:
        new_skill = random.choice(available_skills)
        if "skills" not in pet:
            pet["skills"] = []
        pet["skills"].append(new_skill)
        await update_user_pet(uid, pet)
        await learn_skill_cmd.finish(
            f"🎉 {pet['name']}学会了【{new_skill}】！\n效果：{PET_SKILLS[new_skill]['description']}", at_sender=True)
    else:
        await learn_skill_cmd.finish("学习失败了...技能药水已消耗。", at_sender=True)


# ===== 遗忘技能 =====
forget_skill_cmd = on_command("遗忘技能", priority=5, block=True)

@forget_skill_cmd.handle()
async def handle_forget_skill(
    event: Event, bot: Bot,
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    skill_name = args.extract_plain_text().strip()
    if not skill_name:
        await forget_skill_cmd.finish("请指定要遗忘的技能名称！\n例如：遗忘 宝石爱好者", at_sender=True)

    if not await use_user_item(uid, "遗忘药水"):
        await forget_skill_cmd.finish("你没有遗忘药水！购买需要10宝石。", at_sender=True)

    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await add_user_item(uid, "遗忘药水")
        await forget_skill_cmd.finish(NO_PET_MESSAGE, at_sender=True)

    pet = update_pet_status(pet)
    if pet.get("runaway"):
        await add_user_item(uid, "遗忘药水")
        await forget_skill_cmd.finish(f"你的宠物【{pet['name']}】离家出走了！", at_sender=True)

    if skill_name not in pet.get("skills", []):
        await add_user_item(uid, "遗忘药水")
        current_skills = "、".join(pet.get("skills", [])) or "暂无"
        await forget_skill_cmd.finish(
            f"你的宠物没有学会【{skill_name}】！\n当前技能：{current_skills}", at_sender=True)

    pet["skills"].remove(skill_name)
    await update_user_pet(uid, pet)
    await forget_skill_cmd.finish(
        f"💫 {pet['name']}遗忘了【{skill_name}】！\n当前剩余技能：{'、'.join(pet['skills']) or '暂无'}", at_sender=True)


# ===== 宠物事件 =====
pet_event_cmd = on_command("宠物事件", priority=5, block=True)


def _pet_event_date(value) -> Optional[date]:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value).date()
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return None


async def _apply_pet_skill(skill_name: str, pet: dict, uid: int) -> str:
    pet_name = pet["name"]
    if skill_name == "宝石爱好者":
        amount = random.randint(1, 20)
        money.kirastone += amount
        return f"\n{pet_name}外出玩耍时偶遇无人看守的宝石矿井，偷偷捡回了{amount}枚宝石。"
    if skill_name == "盼望长大":
        growth_gain = 10
        pet["growth"] = min(
            pet.get("growth_required", math.inf),
            pet["growth"] + growth_gain,
        )
        return f"\n{pet_name}很喜欢你，决定要努力长大来报答你，成长值+{growth_gain}。"
    if skill_name == "金币爱好者":
        amount = random.randint(1000, 20000)
        money.gold += amount
        return f"\n{pet_name}外出玩耍时捡到了一个钱包，里面有{amount}金币。"
    if skill_name == "幸运星":
        amount = random.randint(5, 7)
        money.luckygold += amount
        return f"\n{pet_name}外出玩耍时偶遇音祈，由于可爱的外表，深受对方喜爱，获得了上帝的祝福。幸运币+{amount}。"
    if skill_name == "卖萌":
        amount = random.randint(100, 2000)
        money.starstone += amount
        return f"\n{pet_name}外出玩耍时偶遇梦灵，由于太可爱了，被大小姐rua了个爽，并收获了大量好感度（星星+{amount}）。"
    if skill_name == "美食家":
        food_item = random.choice(
            ["普通料理", "高级料理", "豪华料理", "能量饮料"]
        )
        await add_user_item(uid, food_item, 1)
        return f"\n{pet_name}外出玩耍时偶遇商店的抽奖活动，赢得了{food_item}。"
    if skill_name == "自我管理":
        energy_gain = random.randint(10, 80)
        happiness_gain = random.randint(10, 80)
        pet["energy"] = min(
            pet["max_energy"],
            pet["energy"] + energy_gain,
        )
        pet["happiness"] = min(
            pet["max_happiness"],
            pet["happiness"] + happiness_gain,
        )
        return (
            f"\n{pet_name}已经习惯了你早出晚归的生活，她知道你赚金币养她很不容易。"
            f"通过自我情绪管理，她恢复{energy_gain}精力和{happiness_gain}好感。"
        )
    if skill_name == "捕鱼达人":
        species_buff = 2 if "猫" in pet["type"] else 1
        add_count = random.randint(1, 5) * 100 * (3 ** pet["stage"]) * species_buff
        DatabaseManager.check_and_update_fish_limit(uid, -add_count)
        return f"\n{pet_name}在捕鱼大赛中名列前茅，赢得了鱼塘的特邀入场券。今日钓鱼次数+{add_count}。"
    return f"【{skill_name}】是未知技能，无法发动。"


@pet_event_cmd.handle()
async def handle_pet_event(event: Event, bot: Bot, uid: int = Depends(get_uid)):

    now_date = datetime.now().date()

    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await pet_event_cmd.finish(NO_PET_MESSAGE, at_sender=True)

    # 先更新一次宠物状态，处理自然衰减等
    pet = update_pet_status(pet)

    if pet.get("runaway"):
        await pet_event_cmd.finish(f"你的宠物【{pet['name']}】离家出走了，无法触发事件！", at_sender=True)

    last_event_date = _pet_event_date(pet.get("last_event_date"))

    # 检查是否已经执行过今日事件（超级用户绕过）
    if last_event_date and last_event_date == now_date and not is_su(uid):
        await pet_event_cmd.finish("今天已经触发过宠物事件了，请明天再来！", at_sender=True)

    if not pet.get("skills"):
        await pet_event_cmd.finish(f"{pet['name']}还没有学会任何技能！", at_sender=True)

    results = []
    for skill_name in pet["skills"]:
        try:
            results.append(await _apply_pet_skill(skill_name, pet, uid))
        except Exception as exc:
            results.append(f"【{skill_name}】发动时发生错误：{exc}")

    # 在所有技能执行完毕后，保存宠物状态和本次事件的日期
    pet["last_event_date"] = now_date.strftime("%Y-%m-%d")
    await update_user_pet(uid, pet)

    # 发送事件结果（合并转发）
    msg = f"{pet['name']}今天发生了以下事件：" + "\n".join(results)
    try:
        chain = await build_forward_chain(bot, [msg])
        await send_group_forward_msg(event, bot, chain)
    except Exception as e:
        logger.error(f"宠物事件合并消息发送失败: {e}")
        await pet_event_cmd.finish(msg, at_sender=True)


# ===== 宠物进化 =====
evolve_cmd = on_command("宠物进化", priority=5, block=True)


def _evolution_requirement(pet: dict) -> Optional[tuple[str, int, float]]:
    stage = pet["stage"]
    if stage == 0:
        return "奶油蛋糕", 1, GROWTH_STAGE_2
    if stage == 1:
        return "豪华蛋糕", 2, math.inf
    return None


def _next_evolution_type(pet: dict) -> Optional[str]:
    evolution = EVOLUTIONS.get(pet["type"])
    if isinstance(evolution, dict) and evolution:
        return random.choice(list(evolution.values()))
    if isinstance(evolution, str):
        return evolution
    return None


@evolve_cmd.handle()
async def handle_evolve(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await evolve_cmd.finish(NO_PET_MESSAGE, at_sender=True)

    pet = update_pet_status(pet)

    requirement = _evolution_requirement(pet)
    growth_required = pet.get(
        "growth_required",
        100 if pet["stage"] == 0 else 200,
    )
    if requirement is None or pet["growth"] < growth_required:
        await evolve_cmd.finish(
            f"{pet['name']}还不满足进化条件（成长值需达到上限）", at_sender=True)

    item_name, next_stage, next_growth_required = requirement
    if not await use_user_item(uid, item_name):
        await evolve_cmd.finish(f"进化需要{item_name}！", at_sender=True)
    if random.random() < 0.4:
        await evolve_cmd.finish(f"很可惜，{pet['name']}进化失败了...", at_sender=True)

    new_type = _next_evolution_type(pet)
    if new_type is None:
        await evolve_cmd.finish("进化路线有误！", at_sender=True)

    pet["type"] = new_type
    pet["stage"] = next_stage
    pet["growth"] = 0
    pet["growth_required"] = next_growth_required
    await update_user_pet(uid, pet)
    await evolve_cmd.finish(
        f"🎉 {pet['name']}成功进化为【{new_type}】！",
        at_sender=True,
    )

# ===== 重置进化路线 =====
reroll_evolution_cmd = on_command("重置进化路线", aliases={"重新进化"}, priority=5, block=True)


def _base_pet_type_for_evolution(evolved_type: str) -> Optional[str]:
    for base_type, evolutions in EVOLUTIONS.items():
        if isinstance(evolutions, dict) and evolved_type in evolutions.values():
            return base_type
    return None


@reroll_evolution_cmd.handle()
async def handle_reroll_evolution(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    if not await use_user_item(uid, "时之泪"):
        await reroll_evolution_cmd.finish("你没有时之泪！", at_sender=True)

    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await add_user_item(uid, "时之泪")
        await reroll_evolution_cmd.finish(NO_PET_MESSAGE, at_sender=True)

    pet = update_pet_status(pet)
    if pet.get("runaway"):
        await add_user_item(uid, "时之泪")
        await reroll_evolution_cmd.finish(f"你的宠物【{pet['name']}】离家出走了，无法重置进化！", at_sender=True)

    if pet["stage"] != 1:
        await add_user_item(uid, "时之泪")
        await reroll_evolution_cmd.finish("只有成长体宠物可以重置进化路线！", at_sender=True)

    original_type = pet["type"]
    
    if random.random() < 0.5:
        await reroll_evolution_cmd.finish(f"{pet['name']}的进化分支没有改变。", at_sender=True)

    base_type = _base_pet_type_for_evolution(original_type)
    if not base_type:
        await add_user_item(uid, "时之泪")
        await reroll_evolution_cmd.finish("无法找到原始进化路线。", at_sender=True)

    evolution_options = EVOLUTIONS[base_type]
    available_choices = [
        choice
        for choice, evolved_type in evolution_options.items()
        if evolved_type != original_type
    ]

    if not available_choices:
        await add_user_item(uid, "时之泪")
        await reroll_evolution_cmd.finish("没有可用的进化分支改变。", at_sender=True)

    evolution_choice = random.choice(available_choices)
    new_type = evolution_options[evolution_choice]
    pet["type"] = new_type

    await update_user_pet(uid, pet)
    await reroll_evolution_cmd.finish(f"✨ {pet['name']}的进化分支改变了！现在是{new_type}！", at_sender=True)


# ===== 宠物改名 =====
rename_cmd = on_command("宠物改名", priority=5, block=True)

@rename_cmd.handle()
async def handle_rename(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    new_name = args.extract_plain_text()
    
    if not new_name:
        await rename_cmd.finish("请提供新名字！例如：宠物改名 小黑", at_sender=True)
    
    if len(new_name) > 10:
        await rename_cmd.finish("名字太长了，最多10个字符！", at_sender=True)
    
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await rename_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    old_name = pet["name"]
    pet["name"] = new_name
    await update_user_pet(uid, pet)
    await rename_cmd.finish(f"成功将'{old_name}'改名为'{new_name}'！", at_sender=True)


# ===== 寻回宠物 =====
retrieve_cmd = on_command("寻回宠物", priority=5, block=True)

@retrieve_cmd.handle()
async def handle_retrieve(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    if not await use_user_item(uid, ORIGINAL_CONTRACT_ITEM):
        await retrieve_cmd.finish(f"你没有{ORIGINAL_CONTRACT_ITEM}！", at_sender=True)
    
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await add_user_item(uid, ORIGINAL_CONTRACT_ITEM)
        await retrieve_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    pet = update_pet_status(pet)
    if not pet.get("runaway"):
        await add_user_item(uid, ORIGINAL_CONTRACT_ITEM)
        await retrieve_cmd.finish("你的宠物没有离家出走！", at_sender=True)
    
    pet["runaway"] = False
    pet["happiness"] = pet["max_happiness"] * 0.3
    pet["hunger"] = pet["max_hunger"] * 0.3
    pet["energy"] = pet["max_energy"] * 0.3
    pet["last_update"] = time.time()
    
    await update_user_pet(uid, pet)
    await retrieve_cmd.finish(f"你找回了{pet['name']}，这一次，一定要好好珍惜哦~", at_sender=True)


# ===== 放生宠物 =====
release_pet_cmd = on_command("放生宠物", priority=5, block=True)

@release_pet_cmd.handle()
async def handle_release_pet(
    event: Event, bot: Bot, 
    args: Message = CommandArg(),
    uid: int = Depends(get_uid)
):
    pet = await get_user_pet(uid)
    if not pet:
        await release_pet_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    if pet.get("temp_data"):
        await release_pet_cmd.finish("你有一只待领养的宠物，请使用'放弃宠物'来放弃领养。", at_sender=True)
    
    # 更新宠物状态
    pet = update_pet_status(pet)
    await update_user_pet(uid, pet)
    
    # 确认操作
    confirm = args.extract_plain_text().strip().lower()
    if confirm != "确认":
        await release_pet_cmd.finish(
            f"确定要放生{pet['name']}吗？这将永久失去她！\n使用'放生宠物 确认'来确认操作", 
            at_sender=True
        )
    
    await remove_user_pet(uid)
    await release_pet_cmd.finish(f"你放生了{pet['name']}。", at_sender=True)


# ===== 永恒誓约 =====
oath_cmd = on_command("永恒誓约", priority=5, block=True)

@oath_cmd.handle()
async def handle_eternal_oath(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await oath_cmd.finish(NO_PET_MESSAGE, at_sender=True)
    
    pet = update_pet_status(pet)
    
    status = min(pet["max_hunger"], pet["max_happiness"], pet["max_energy"])
    if status > 999999:
        msg = f'\n{pet["name"]}有些害羞地看向你...\n"那种事情...不是已经做过了吗..."'
        await oath_cmd.finish(msg, at_sender=True)
    
    if pet.get("stage") != 2:
        msg = f'\n{pet["name"]}有些害羞的看着你...\n"hentai！人家...还没成年呢。"'
        await oath_cmd.finish(msg, at_sender=True)
    
    if pet.get("runaway"):
        await oath_cmd.finish(
            f"{pet['name']}已经离家出走了！使用'{ORIGINAL_CONTRACT_ITEM}'可以寻回她。",
            at_sender=True,
        )
    
    if not await use_user_item(uid, "誓约戒指"):
        msg = f'\n{pet["name"]}有些失落地看着你...\n"那种事情...没有戒指怎么行..."'
        await oath_cmd.finish(msg, at_sender=True)
    
    pet["energy"] = math.inf
    pet["happiness"] = math.inf
    pet["hunger"] = math.inf
    pet["max_happiness"] = math.inf
    pet["max_energy"] = math.inf
    pet["max_hunger"] = math.inf
    pet["growth"] = pet.get("growth", 0) + 1000
    pet["growth_rate"] = round(pet.get("growth_rate", 1.0) * 1.1, 2)
    
    await update_user_pet(uid, pet)
    msg = (f'\n成长值+1000\n基础成长速度+10%\n最大技能数量+999\n\n'
           f'{pet["name"]}有些害羞的看着你，乖巧地等你为她戴上戒指，最后轻轻在你额头上落下一吻...\n'
           f'"以后...不许丢下我。"')
    await oath_cmd.finish(msg, at_sender=True)


# ===== 宠物排行榜 =====
pet_ranking_cmd = on_command("宠物排行榜", priority=5, block=True)


def _normalized_uid(uid_key):
    try:
        return int(uid_key)
    except (TypeError, ValueError):
        return uid_key


def _rankable_pet_entries(
    user_pets: dict,
    excluded_suids: set,
    feisheng_status: dict,
) -> list[dict]:
    entries = []
    for uid_key, pet in user_pets.items():
        normalized_uid = _normalized_uid(uid_key)
        if normalized_uid in excluded_suids or pet.get("stage") != 2:
            continue

        current_pet = update_pet_status(dict(pet))
        if current_pet.get("runaway", False):
            continue

        ascension = feisheng_status.get(
            normalized_uid,
            {"is_pet_ascended": False, "is_ascended": False},
        )
        is_top = ascension["is_pet_ascended"]
        entries.append(
            {
                "growth": current_pet["growth"],
                "name": current_pet.get("name", "未知宠物"),
                "type": current_pet.get("type", "未知类型"),
                "uid": uid_key,
                "is_top": is_top,
                "hide_growth": is_top and ascension["is_ascended"],
            }
        )
    entries.sort(
        key=lambda item: (
            item["is_top"],
            item["hide_growth"],
            item["growth"],
        ),
        reverse=True,
    )
    return entries


def _pet_rankings(entries: list[dict]) -> dict:
    rankings = {}
    current_rank = 1
    previous_key = None
    for index, entry in enumerate(entries):
        key = (
            entry["is_top"],
            entry["hide_growth"],
            entry["growth"],
        )
        if key != previous_key:
            current_rank = index + 1
        rankings[entry["uid"]] = current_rank
        previous_key = key
    return rankings


@pet_ranking_cmd.handle()
async def handle_pet_ranking(event: Event, bot: Bot):
    """显示成长值最高的前10只成年体宠物"""
    excluded_suids = get_excluded_su_uids()
    
    # 获取飞升状态
    feisheng_status = await get_all_pet_feisheng_status()
    
    user_pets = await get_user_pets()

    adult_pets = _rankable_pet_entries(
        user_pets,
        excluded_suids,
        feisheng_status,
    )

    if not adult_pets:
        await pet_ranking_cmd.finish("目前还没有成年体宠物上榜哦！", at_sender=True)

    msg = ["🏆 宠物排行榜-TOP10 🏆"]
    for rank, entry in enumerate(adult_pets[:10], 1):
        owner_name = (
            get_user_nickname(int(entry["uid"]))
            or f"UID{entry['uid']}"
        )
        top_tag = "（飞升上界）" if entry["is_top"] else ""
        growth = (
            "无法探查"
            if entry["hide_growth"]
            else f"{entry['growth']:.1f}"
        )
        msg.append(
            f"{rank}. {entry['name']}({entry['type']}) - 主人:{owner_name}"
            f"\n成长值: {growth}{top_tag}"
        )

    msg_str = "\n".join(msg)
    chain = await build_forward_chain(bot, [msg_str])
    await send_group_forward_msg(event, bot, chain)


# ===== 宠物排名 =====
pet_my_ranking_cmd = on_command("宠物排名", priority=5, block=True)

@pet_my_ranking_cmd.handle()
async def handle_my_pet_ranking(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    """查看自己宠物的排名"""
    pet = await get_user_pet(uid)
    if not pet or pet.get("temp_data"):
        await pet_my_ranking_cmd.finish(NO_PET_MESSAGE, at_sender=True)

    pet = update_pet_status(pet)
    await update_user_pet(uid, pet)

    if pet.get("runaway", False):
        await pet_my_ranking_cmd.finish(
            f"你的宠物【{pet['name']}】离家出走了，无法参与排行", at_sender=True)

    if pet.get("stage") != 2:
        await pet_my_ranking_cmd.finish("只有成年体宠物可以查看排名哦！", at_sender=True)

    user_pets = await get_user_pets()
    from ...su_manager import get_excluded_su_uids
    from ..feisheng.data import get_all_pet_feisheng_status
    excluded_suids = get_excluded_su_uids()
    feisheng_status = await get_all_pet_feisheng_status()
    
    valid_pets = _rankable_pet_entries(
        user_pets,
        excluded_suids,
        feisheng_status,
    )

    if not valid_pets:
        await pet_my_ranking_cmd.finish("目前还没有有效的成年体宠物上榜哦！", at_sender=True)

    rankings = _pet_rankings(valid_pets)
    my_entry = next(
        (
            entry
            for entry in valid_pets
            if entry["uid"] in {uid, str(uid)}
        ),
        None,
    )
    if my_entry is None:
        await pet_my_ranking_cmd.finish("你的宠物未上榜！", at_sender=True)

    my_rank = rankings[my_entry["uid"]]
    top_tag = " ✨" if my_entry["is_top"] else ""
    growth_str = (
        "无法探查"
        if my_entry["hide_growth"]
        else f"{my_entry['growth']:.1f}"
    )
    await pet_my_ranking_cmd.finish(
        f"你的宠物【{pet['name']}】"
        f"\n当前排名: 第{my_rank}名（共{len(valid_pets)}只成年宠物）"
        f"\n成长值: {growth_str}{top_tag}",
        at_sender=True,
    )


# ===== 技能帮助 =====
skill_help_cmd = on_command("技能帮助", aliases={"技能百科"}, priority=5, block=True)

skill_list = []
for skill_name, skill_info in PET_SKILLS.items():
    skill_list.append(f'"{skill_name}": {skill_info["description"]}')

pet_skill = f"""幼年体/成长体/成年体可学习1/3/5个技能
指令：
学习技能 （消耗1个技能药水）
遗忘技能 技能名称 （消耗1个遗忘药水） 
可学习的技能一览：
{chr(10).join(skill_list)}"""

@skill_help_cmd.handle()
async def handle_skill_help(event: Event, bot: Bot):
    chain = await build_forward_chain(bot, [pet_skill])
    await send_group_forward_msg(event, bot, chain)


# ===== 查看进化分支 =====
luxian_cmd = on_command("查看进化分支", aliases={"查看进化路线"}, priority=5, block=True)

@luxian_cmd.handle()
async def handle_luxian_help(event: Event, bot: Bot):
    from pathlib import Path
    from ...resources import get

    # 加载进化路线图片
    luxian1_img = get('chongwu/luxian/luxian1.png')
    luxian2_img = get('chongwu/luxian/luxian2.png')

    # 构建 4 个消息节点，合并图文
    pet_luxian1 = [
        {"type": "text", "data": {"text": "幼年体→→成长体（三选一）\n"}},
        {"type": "image", "data": {"file": f"base64://{luxian1_img.base64}"}}
    ]
    pet_luxian2 = [
        {"type": "text", "data": {"text": "成长体→→成年体（固定路线）\n"}},
        {"type": "image", "data": {"file": f"base64://{luxian2_img.base64}"}}
    ]
    pet_luxian3 = "宠物处于『成长体』时，可使用 重置进化路线 来切换进化分支（需消耗1个时之泪）"
    pet_luxian4 = "当前版本中，同一幼年体的不同的进化分支，没有数值差异"

    msgs = [pet_luxian1, pet_luxian2, pet_luxian3, pet_luxian4]

    try:
        chain = await build_forward_chain(bot, msgs)
        await send_group_forward_msg(event, bot, chain)
    except Exception as e:
        logger.error(f"进化分支帮助合并消息发送失败: {e}")
        await luxian_cmd.finish("幼年体→→成长体（三选一），成长体→→成年体（固定路线）\n"
                                "宠物处于『成长体』时，可使用 重置进化路线 来切换进化分支（需消耗1个时之泪）\n"
                                "当前版本中，同一幼年体的不同的进化分支，没有数值差异")


# ===== 初始化 =====
driver = get_driver()

@driver.on_startup
async def init_chongwu():
    """初始化宠物插件"""
    from pathlib import Path
    plugin_dir = Path(__file__).parent.parent.parent
    db_path = plugin_dir / "src" / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    set_db_path(str(db_path))
    init_pet_database()
    logger.info("Chongwu 宠物插件初始化完成")
