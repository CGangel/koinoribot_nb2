import json
import random
import asyncio
from typing import Dict

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from ...money import money
from ...config_store import config
from .util import DatabaseManager
from .serif import GET_FISH_SERIF, NO_FISH_SERIF, COOL_TIME_SERIF
from ...su_manager import is_su
# ===== 常量配置 =====
FISH_LIST = ['🐟', '🦐', '🦀', '🐡', '🐠', '🦈', '🌟']
FISH_PRICE = {
    '🍙': 1, '🐟': 5, '🦐': 10, '🦀': 35, 
    '🐡': 45, '🐠': 75, '🦈': 100, '🌟': 2000
}
# 概率配置 (没钓到鱼, 随机事件, 钓到鱼, 钓到金币, 钓到水之心)
PROBABILITY = (5, 10, 74, 10, 1)
# 各种鱼上钩概率
PROBABILITY_2 = (25, 23, 20, 15, 9, 7, 1)

# 默认用户背包
DEFAULT_INFO = {
    'fish': {'🐟': 0, '🦐': 0, '🦀': 0, '🐡': 0, '🐠': 0, '🔮': 3, '✉': 3, '🍙': 0, '🦈': 0, '🌟': 0},
    'statis': {'free': 0, 'sell': 0, 'total_fish': 0, 'frags': 0},
    'rod': {'current': 0, 'total_rod': [0]}
}


def _select_fish(second_choose: int) -> str:
    probability_sum = 0
    for index, probability in enumerate(PROBABILITY_2):
        probability_sum += probability * 10
        if second_choose <= probability_sum:
            return FISH_LIST[index]
    return FISH_LIST[0]


def _lucky_gold_reward(value: int, actual_cost: int, times: int) -> int:
    if actual_cost <= 0 or times < 100:
        return 0
    ratio = value / actual_cost
    if ratio > 3:
        return 3
    if ratio > 2.5:
        return 2
    if ratio > 2:
        return 1
    return 0


def _format_multi_summary(
    command_name: str,
    result_summary: dict[str, int],
    auto_buy: bool,
    cost: int,
    value: int,
    subsidy: int,
    lucky_gold: int,
    actual_cost: int,
    star_cost: int,
    star_price: int,
) -> str:
    lines = [
        f"你的{command_name}结果：",
        "发送 概率公示 可查活动和概率",
    ]
    if auto_buy:
        lines.append(f"(已自动购买{cost}个鱼饵~)")
    if result_summary:
        lines.append("".join(
            f"{fish}: {count}条"
            for fish, count in result_summary.items()
        ))
    else:
        lines.append("什么都没钓到...")

    value_text = f"总价值：{value}金币"
    if subsidy:
        value_text += f"+{subsidy}金币(活动补贴)"
    lines.append(value_text)

    cost_text = f"总花费：{actual_cost}金币"
    if star_price:
        cost_text += f" {star_cost}星星"
    lines.append(cost_text)
    if lucky_gold:
        lines.append(f"幸运币+{lucky_gold}")
    return "\n".join(lines)



class FishingManager:
    """钓鱼核心管理器"""
    
    @classmethod
    async def get_user_info(cls, uid: int) -> dict:
        """获取用户钓鱼背包"""
        DatabaseManager.init_fishing_database()
        
        def _query():
            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT fish_data, statis_data, rod_data FROM fishing WHERE uid = ?', (uid,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return {
                    'fish': json.loads(result['fish_data']),
                    'statis': json.loads(result['statis_data']),
                    'rod': json.loads(result['rod_data'])
                }
            return None
        
        loop = asyncio.get_event_loop()
        user_info = await loop.run_in_executor(None, _query)
        
        if not user_info:
            user_info = json.loads(json.dumps(DEFAULT_INFO))
            await cls.save_user_info(uid, user_info)
        
        return user_info
    
    @classmethod
    async def save_user_info(cls, uid: int, user_info: dict):
        """保存用户钓鱼背包"""
        DatabaseManager.init_fishing_database()
        
        def _save():
            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()
            
            fish_data = json.dumps(user_info.get('fish', {}))
            statis_data = json.dumps(user_info.get('statis', {}))
            rod_data = json.dumps(user_info.get('rod', {}))
            
            cursor.execute('''
                INSERT OR REPLACE INTO fishing (uid, fish_data, statis_data, rod_data)
                VALUES (?, ?, ?, ?)
            ''', (uid, fish_data, statis_data, rod_data))
            
            conn.commit()
            conn.close()
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _save)
    
    @classmethod
    async def increase_value(cls, uid: int, mainclass: str, subclass: str, num: int, user_info: dict = None):
        """增加物品数量"""
        if user_info is not None:
            if subclass not in user_info[mainclass]:
                user_info[mainclass][subclass] = 0
            user_info[mainclass][subclass] += num
        else:
            info = await cls.get_user_info(uid)
            if subclass not in info[mainclass]:
                info[mainclass][subclass] = 0
            info[mainclass][subclass] += num
            await cls.save_user_info(uid, info)
    
    @classmethod
    async def decrease_value(cls, uid: int, mainclass: str, subclass: str, num: int, user_info: dict = None):
        """减少物品数量"""
        if user_info is not None:
            if subclass not in user_info[mainclass]:
                user_info[mainclass][subclass] = 0
            user_info[mainclass][subclass] = max(0, user_info[mainclass][subclass] - num)
        else:
            info = await cls.get_user_info(uid)
            if subclass not in info[mainclass]:
                info[mainclass][subclass] = 0
            info[mainclass][subclass] = max(0, info[mainclass][subclass] - num)
            await cls.save_user_info(uid, info)

    @classmethod
    async def _catch_fish(cls, uid: int, user_info: dict) -> dict:
        fish = _select_fish(random.randint(1, 1000))
        await cls.increase_value(uid, 'fish', fish, 1, user_info)
        await cls.increase_value(uid, 'statis', 'total_fish', 1, user_info)
        if random.randint(1, 10) <= 5:
            message = f'钓到了一条{fish}~'
        else:
            message = random.choice(GET_FISH_SERIF).format(fish)
        return {'code': 1, 'msg': message + '\n你将鱼放进了背包。'}

    @staticmethod
    def _catch_coins(uid: int) -> dict:
        if random.randint(1, 1000) <= 800:
            coin_amount = random.randint(1, 30)
            money.of(uid).gold += coin_amount
            return {'code': 1, 'msg': f'你钓到了一个布包，里面有{coin_amount}枚金币~'}
        coin_amount = random.randint(1, 3)
        money.of(uid).luckygold += coin_amount
        return {'code': 1, 'msg': f'你钓到了一个锦囊，里面有{coin_amount}枚幸运币！'}
    
    @classmethod
    async def do_fishing(cls, uid: int, skip_random_events: bool = False, user_info: dict = None) -> dict:
        """
        执行钓鱼逻辑
        
        Args:
            uid: 用户ID
            skip_random_events: 是否跳过随机事件（多连钓鱼时使用）
            user_info: 用户信息（可选，传入以避免重复查询）
            
        Returns:
            {"code": 1|2|3, "msg": str}
            code 1: 普通结果（钓到鱼/空军/金币）
            code 2: 钓到水之心
            code 3: 随机事件（跳过时返回普通）
        """
        if user_info is None:
            user_info = await cls.get_user_info(uid)
        
        first_choose = random.randint(1, 1000)
        if skip_random_events:
            fish_start = (PROBABILITY[0] + PROBABILITY[1]) * 10
            fish_end = fish_start + PROBABILITY[2] * 10
            if fish_start < first_choose <= fish_end:
                return await cls._catch_fish(uid, user_info)
            return {'code': 1, 'msg': random.choice(NO_FISH_SERIF)}

        empty_end = PROBABILITY[0] * 10
        event_end = empty_end + PROBABILITY[1] * 10
        fish_end = event_end + PROBABILITY[2] * 10
        coin_end = fish_end + PROBABILITY[3] * 10
        if first_choose <= empty_end:
            return {'code': 1, 'msg': random.choice(NO_FISH_SERIF)}
        if first_choose <= event_end:
            await cls.increase_value(uid, 'fish', '✉', 1, user_info)
            return {'code': 1, 'msg': '你的鱼钩碰到了一个空漂流瓶！可以使用"扔漂流瓶+内容"使用它'}
        if first_choose <= fish_end:
            return await cls._catch_fish(uid, user_info)
        if first_choose <= coin_end:
            return cls._catch_coins(uid)
        await cls.increase_value(uid, 'fish', '🔮', 1, user_info)
        return {'code': 2, 'msg': '你发现鱼竿有着异于平常的感觉，竟然钓到了一颗水之心🔮~'}
    
    @classmethod
    def cal_all_fish_value(cls, result: Dict[str, int]) -> int:
        """计算所有鱼的总价值"""
        total_value = 0
        for fish, count in result.items():
            if fish in FISH_PRICE:
                total_value += count * FISH_PRICE[fish]
        return total_value

    @classmethod
    async def _prepare_multi_fishing(
        cls,
        uid: int,
        matcher: Matcher,
        wallet,
        cost: int,
        star_cost: int,
        actual_cost: int,
        cooldown_manager,
    ):
        if config.star_price and wallet.starstone < star_cost:
            await matcher.finish("星星不够用了呢...", at_sender=True)

        user_info = await cls.get_user_info(uid)
        if cooldown_manager.left_time(uid) > 0:
            remaining = int(cooldown_manager.left_time(uid))
            await matcher.finish(random.choice(COOL_TIME_SERIF) + f'({remaining}s)')

        auto_buy = user_info['fish'].get('🍙', 0) < cost
        if auto_buy:
            if wallet.gold < actual_cost:
                await matcher.finish("金币或鱼饵不足喔...", at_sender=True)
            wallet.gold -= actual_cost
        return user_info, auto_buy

    @staticmethod
    async def _check_multi_fishing_limit(
        uid: int,
        times: int,
        matcher: Matcher,
        wallet,
        actual_cost: int,
        auto_buy: bool,
    ) -> bool:
        allowed = DatabaseManager.check_and_update_fish_limit(uid, times)
        fish_count, limit_count = DatabaseManager.get_user_fish_count_today(uid)
        if is_su(uid) or allowed:
            return True

        rest_count = limit_count - fish_count
        await matcher.send(
            f'\n今日钓鱼次数已达上限喔...你还能钓鱼{rest_count}次。\n明天再来吧~',
            at_sender=True,
        )
        if auto_buy:
            wallet.gold += actual_cost
        return False

    @classmethod
    async def _consume_multi_fishing_costs(
        cls,
        uid: int,
        wallet,
        user_info: dict,
        auto_buy: bool,
        cost: int,
        star_cost: int,
    ):
        if config.star_price:
            wallet.starstone -= star_cost
        if not auto_buy:
            await cls.decrease_value(uid, "fish", "🍙", cost, user_info)

    @classmethod
    async def _collect_multi_fishing_results(
        cls,
        uid: int,
        times: int,
        user_info: dict,
    ) -> tuple[Dict[str, int], bool]:
        result_summary: Dict[str, int] = {}
        for _ in range(times):
            response = await cls.do_fishing(
                uid,
                skip_random_events=True,
                user_info=user_info,
            )
            if response["code"] != 1:
                continue
            fish = next(
                (item for item in FISH_LIST if item in response["msg"]),
                None,
            )
            if fish:
                result_summary[fish] = result_summary.get(fish, 0) + 1
        return result_summary, '🌟' in result_summary

    @staticmethod
    def _apply_multi_fishing_rewards(
        wallet,
        value: int,
        actual_cost: int,
        times: int,
        have_star: bool,
    ) -> tuple[int, int]:
        subsidy = 300 if not have_star and config.extra_gold == 1 and times == 100 else 0
        wallet.gold += subsidy
        lucky_gold = _lucky_gold_reward(value, actual_cost, times)
        wallet.luckygold += lucky_gold
        return subsidy, lucky_gold

    @staticmethod
    def _multi_fishing_count_message(uid: int) -> str:
        if is_su(uid):
            return ""
        fish_count, limit_count = DatabaseManager.get_user_fish_count_today(uid)
        return (
            f"今日已钓鱼：{fish_count}次\n"
            f"剩余次数：{limit_count - fish_count}次"
        )

    @staticmethod
    async def _send_multi_fishing_result(
        matcher: Matcher,
        summary_message: str,
        count_message: str,
    ):
        forward_messages = [summary_message]
        if count_message:
            forward_messages.append(count_message)
        try:
            await matcher.send("\n\n".join(forward_messages))
        except Exception as error:
            logger.error(f"发送合并转发消息失败: {error}，降级为普通消息")
            await matcher.send(summary_message)
            if count_message:
                await matcher.send(count_message)

    @classmethod
    async def multi_fishing(cls, uid:int, matcher: Matcher, bot: Bot, event: Event,
                           times: int, cost: int, star_cost: int, command_name: str,
                           cooldown_manager):
        """
        多连钓鱼核心逻辑

        Args:
            uid: 统一用户ID
            matcher: 事件实例
            bot: Bot 对象（用于发送合并转发消息）
            event: Event 对象（用于发送合并转发消息）
            times: 钓鱼次数
            cost: 鱼饵消耗
            star_cost: 星星消耗
            command_name: 命令名称（用于显示）
            cooldown_manager: 冷却管理器实例
        """

        wallet = money.of(uid)
        actual_cost = cost * config.bait_price
        user_info, auto_buy = await cls._prepare_multi_fishing(
            uid,
            matcher,
            wallet,
            cost,
            star_cost,
            actual_cost,
            cooldown_manager,
        )
        if not await cls._check_multi_fishing_limit(
            uid,
            times,
            matcher,
            wallet,
            actual_cost,
            auto_buy,
        ):
            return

        cooldown_manager.start_cd(uid)
        await cls._consume_multi_fishing_costs(
            uid,
            wallet,
            user_info,
            auto_buy,
            cost,
            star_cost,
        )
        result_summary, have_star = await cls._collect_multi_fishing_results(
            uid,
            times,
            user_info,
        )
        await cls.save_user_info(uid, user_info)
        value = cls.cal_all_fish_value(result_summary)
        subsidy, lucky_gold = cls._apply_multi_fishing_rewards(
            wallet,
            value,
            actual_cost,
            times,
            have_star,
        )
        summary_message = _format_multi_summary(
            command_name,
            result_summary,
            auto_buy,
            cost,
            value,
            subsidy,
            lucky_gold,
            actual_cost,
            star_cost,
            config.star_price,
        )
        count_message = cls._multi_fishing_count_message(uid)
        await cls._send_multi_fishing_result(
            matcher,
            summary_message,
            count_message,
        )
