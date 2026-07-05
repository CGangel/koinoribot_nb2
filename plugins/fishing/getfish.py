"""
钓鱼核心模块 - getfish.py

包含钓鱼管理类
"""

import json
import random
import asyncio
from typing import Dict, Optional

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from ...money import money
from ...tools import get_uid, send_group_forward_msg, build_forward_chain
from ...koinori_config import config
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



class FishingManager:
    """钓鱼核心管理器"""
    
    @classmethod
    async def get_user_info(cls, uid: int) -> dict:
        """获取用户钓鱼背包"""
        
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
        
        probability = PROBABILITY
        probability_2 = PROBABILITY_2
        
        first_choose = random.randint(1, 1000)
        
        # 跳过随机事件时的处理
        if skip_random_events:
            if first_choose <= probability[0] * 10:
                return {'code': 1, 'msg': random.choice(NO_FISH_SERIF)}
            elif first_choose <= (probability[1] + probability[0]) * 10:
                return {'code': 1, 'msg': random.choice(NO_FISH_SERIF)}
            elif first_choose <= (probability[2] + probability[1] + probability[0]) * 10:
                # 钓到鱼
                second_choose = random.randint(1, 1000)
                prob_sum = 0
                fish = FISH_LIST[0]
                for i, prob in enumerate(probability_2):
                    prob_sum += prob * 10
                    if second_choose <= prob_sum and i < len(FISH_LIST):
                        fish = FISH_LIST[i]
                        break
                
                await cls.increase_value(uid, 'fish', fish, 1, user_info)
                await cls.increase_value(uid, 'statis', 'total_fish', 1, user_info)
                msg = f'钓到了一条{fish}~' if random.randint(1, 10) <= 5 else random.choice(GET_FISH_SERIF).format(fish)
                return {'code': 1, 'msg': msg + '\n你将鱼放进了背包。'}
            else:
                return {'code': 1, 'msg': random.choice(NO_FISH_SERIF)}
        
        # 正常钓鱼
        if first_choose <= probability[0] * 10:
            return {'code': 1, 'msg': random.choice(NO_FISH_SERIF)}
        elif first_choose <= (probability[1] + probability[0]) * 10:
            # 随机事件 - 简化为获得漂流瓶
            await cls.increase_value(uid, 'fish', '✉', 1, user_info)
            return {'code': 1, 'msg': '你的鱼钩碰到了一个空漂流瓶！可以使用"扔漂流瓶+内容"使用它'}
        elif first_choose <= (probability[2] + probability[1] + probability[0]) * 10:
            # 钓到鱼
            second_choose = random.randint(1, 1000)
            prob_sum = 0
            fish = FISH_LIST[0]
            for i, prob in enumerate(probability_2):
                prob_sum += prob * 10
                if second_choose <= prob_sum and i < len(FISH_LIST):
                    fish = FISH_LIST[i]
                    break
            
            await cls.increase_value(uid, 'fish', fish, 1, user_info)
            await cls.increase_value(uid, 'statis', 'total_fish', 1, user_info)
            msg = f'钓到了一条{fish}~' if random.randint(1, 10) <= 5 else random.choice(GET_FISH_SERIF).format(fish)
            return {'code': 1, 'msg': msg + '\n你将鱼放进了背包。'}
        elif first_choose <= (probability[3] + probability[2] + probability[1] + probability[0]) * 10:
            # 钓到金币
            second_choose = random.randint(1, 1000)
            if second_choose <= 800:
                coin_amount = random.randint(1, 30)
                money.of(uid).gold += coin_amount
                return {'code': 1, 'msg': f'你钓到了一个布包，里面有{coin_amount}枚金币~'}
            else:
                coin_amount = random.randint(1, 3)
                money.of(uid).luckygold += coin_amount
                return {'code': 1, 'msg': f'你钓到了一个锦囊，里面有{coin_amount}枚幸运币！'}
        else:
            # 钓到水之心
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

        # 检查星星
        if config.star_price != 0:
            user_starstone = wallet.starstone
            if user_starstone < star_cost:
                await matcher.finish("星星不够用了呢...", at_sender=True)

        user_info = await cls.get_user_info(uid)
        actual_cost = cost * config.bait_price

        # 冷却检测
        if cooldown_manager.left_time(uid) > 0:
            await matcher.finish(random.choice(COOL_TIME_SERIF) + f'({int(cooldown_manager.left_time(uid))}s)')

        auto_buy = False
        # 检查鱼饵
        if user_info['fish'].get('🍙', 0) < cost:
            user_gold = wallet.gold
            if user_gold >= actual_cost:
                wallet.gold -= actual_cost
                auto_buy = True
            else:
                await matcher.finish("金币或鱼饵不足喔...", at_sender=True)

        # 检查次数限制
        limit = DatabaseManager.check_and_update_fish_limit(uid, times)
        fish_count, limit_count = DatabaseManager.get_user_fish_count_today(uid)
        rest_count = limit_count - fish_count

        if not is_su(uid) and not limit:
            await matcher.send(f'\n今日钓鱼次数已达上限喔...你还能钓鱼{rest_count}次。\n明天再来吧~', at_sender=True)
            if auto_buy:
                wallet.gold += actual_cost
            return

        cooldown_manager.start_cd(uid)

        # 扣星星
        if config.star_price != 0:
            wallet.starstone -= star_cost

        # 消耗鱼饵
        if not auto_buy:
            await cls.decrease_value(uid, "fish", "🍙", cost, user_info)

        # 执行多次钓鱼
        result_summary: Dict[str, int] = {}
        have_star = False

        for _ in range(times):
            resp = await cls.do_fishing(uid, skip_random_events=True, user_info=user_info)
            if resp["code"] == 1:
                msg = resp["msg"]
                for fish in FISH_LIST:
                    if fish in msg:
                        result_summary[fish] = result_summary.get(fish, 0) + 1
                        if fish == '🌟':
                            have_star = True

        # 保存数据
        await cls.save_user_info(uid, user_info)

        # 计算价值
        value = cls.cal_all_fish_value(result_summary)

        # ===== 构建钓鱼结果消息（放入合并转发） =====
        summary_message = f"你的{command_name}结果：\n"
        summary_message += f"发送 概率公示 可查活动和概率\n"
        if auto_buy:
            summary_message += f"(已自动购买{cost}个鱼饵~)\n"

        if result_summary:
            summary_message += "".join(f"{fish}: {count}条" for fish, count in result_summary.items())
        else:
            summary_message += "什么都没钓到..."

        summary_message += f"\n总价值：{value}金币"

        # 活动补贴
        if not have_star and config.extra_gold == 1 and times == 100:
            wallet.gold += 300
            summary_message += f"+300金币(活动补贴)"

        summary_message += f"\n总花费：{actual_cost}金币"
        if config.star_price != 0:
            summary_message += f" {star_cost}星星"

        # 幸运币奖励
        if actual_cost > 0 and times >= 100:
            ratio = value / actual_cost
            if ratio > 3:
                wallet.luckygold += 3
                summary_message += "\n幸运币+3"
            elif ratio > 2.5:
                wallet.luckygold += 2
                summary_message += "\n幸运币+2"
            elif ratio > 2:
                wallet.luckygold += 1
                summary_message += "\n幸运币+1"

        # ===== 构建次数统计消息（放入合并转发） =====
        count_message = ""
        if not is_su(uid):
            fish_count, limit_count = DatabaseManager.get_user_fish_count_today(uid)
            rest_count = limit_count - fish_count
            count_message = f"今日已钓鱼：{fish_count}次\n剩余次数：{rest_count}次"

        # ===== 构建价值汇总消息（单独发送） =====
        value_message = f"总价值：{value}金币"
        if not have_star and config.extra_gold == 1 and times == 100:
            actual_value = value + 300
            value_message = f"总价值：{actual_value}金币"
        value_message += f"\n总花费：{actual_cost}金币"
        if config.star_price != 0:
            value_message += f" {star_cost}星星"
        if actual_cost > 0 and times >= 100:
            ratio = value / actual_cost
            if ratio > 3:
                value_message += " 幸运币+3"
            elif ratio > 2.5:
                value_message += " 幸运币+2"
            elif ratio > 2:
                value_message += " 幸运币+1"

        # ===== 发送合并转发消息（钓鱼结果 + 次数统计） =====
        forward_msgs = [summary_message]
        if count_message:
            forward_msgs.append(count_message)

        try:
            #chain = await build_forward_chain(bot, forward_msgs)
            #await send_group_forward_msg(event, bot, chain)
            await matcher.send("\n\n".join(forward_msgs))
        except Exception as e:
            logger.error(f"发送合并转发消息失败: {e}，降级为普通消息")
            await matcher.send(summary_message)
            if count_message:
                await matcher.send(count_message)

        # ===== 单独发送价值汇总 =====
        #await asyncio.sleep(0.5)
        #await matcher.finish(value_message, at_sender=True)
