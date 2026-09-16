"""漂流瓶（drift_bottle）业务逻辑层。

BottleService 面向命令层返回纯数据 dict，消息文案在 __init__ 里拼装。
与钓鱼玩法完全解耦：获取途径为 金币购买 与 星星合成，捞取消耗星星
（旧版的水之心体系随旧版钓鱼移除）。冷却时长与价格均可热更新配置。
"""

from ...config_store import config
from ...money import money
from .db import (
    MAX_COMMENT_LEN,
    MAX_CONTENT_LEN,
    MIN_PICK_POOL,
    BottleDB,
)

# 一次最多可购买/合成的漂流瓶个数（沿用旧版限购）
MAX_BUY_NUM = 10


class BottleService:
    """漂流瓶业务：购买 / 合成 / 扔 / 捡 / 评论 / 管理"""

    @staticmethod
    async def get_owned_count(uid: int) -> int:
        return await BottleDB.get_inventory(uid)

    @staticmethod
    async def buy_bottles(uid: int, num: int) -> dict:
        """花金币购买漂流瓶"""
        if num <= 0 or num > MAX_BUY_NUM:
            return {"ok": False, "reason": "bad_num", "max": MAX_BUY_NUM}
        cost = num * config.bottle_price
        wallet = money.of(uid)
        if wallet.gold < cost:
            return {"ok": False, "reason": "no_gold", "cost": cost}
        wallet.gold -= cost
        await BottleDB.add_inventory(uid, num)
        owned = await BottleDB.get_inventory(uid)
        return {"ok": True, "num": num, "cost": cost, "owned": owned}

    @staticmethod
    async def craft_bottles(uid: int, num: int) -> dict:
        """花星星合成漂流瓶（与钓鱼解耦后的合成途径）"""
        if num <= 0:
            return {"ok": False, "reason": "bad_num"}
        cost = num * config.bottle_craft_starstone
        wallet = money.of(uid)
        if wallet.starstone < cost:
            return {"ok": False, "reason": "no_starstone", "cost": cost}
        wallet.starstone -= cost
        await BottleDB.add_inventory(uid, num)
        owned = await BottleDB.get_inventory(uid)
        return {"ok": True, "num": num, "cost": cost, "owned": owned}

    @staticmethod
    async def throw_bottle(uid: int, content: str) -> dict:
        """扔出一个持有的漂流瓶"""
        if not content.strip():
            return {"ok": False, "reason": "empty"}
        if len(content) > MAX_CONTENT_LEN:
            return {"ok": False, "reason": "too_long", "max": MAX_CONTENT_LEN}
        if not await BottleDB.consume_inventory(uid, 1):
            return {"ok": False, "reason": "no_bottle"}
        bottle_id = await BottleDB.create_bottle(uid, content)
        owned = await BottleDB.get_inventory(uid)
        return {"ok": True, "bottle_id": bottle_id, "owned": owned}

    @staticmethod
    async def bottle_amount() -> int:
        return await BottleDB.get_bottle_amount()

    @staticmethod
    async def pick_bottle(uid: int) -> dict:
        """随机捞取一个漂流瓶（消耗星星；池子不足或星星不足时拒绝）"""
        amount = await BottleDB.get_bottle_amount()
        if amount < MIN_PICK_POOL:
            return {"ok": False, "reason": "pool_empty", "amount": amount,
                    "min": MIN_PICK_POOL}
        cost = config.bottle_salvage_starstone
        wallet = money.of(uid)
        if wallet.starstone < cost:
            return {"ok": False, "reason": "no_starstone", "cost": cost}
        bottle_id, bottle = await BottleDB.pick_random_bottle()
        if bottle_id is None:
            return {"ok": False, "reason": "pool_empty", "amount": amount,
                    "min": MIN_PICK_POOL}
        wallet.starstone -= cost
        return {"ok": True, "bottle_id": bottle_id, "bottle": bottle,
                "cost": cost}

    @staticmethod
    async def get_bottle(bottle_id: int) -> dict:
        bottle = await BottleDB.get_bottle_by_id(bottle_id)
        if bottle is None:
            return {"ok": False, "reason": "not_found"}
        return {"ok": True, "bottle": bottle}

    @staticmethod
    async def comment_bottle(uid: int, bottle_id: int, content: str) -> dict:
        """花金币评论漂流瓶"""
        if not content.strip():
            return {"ok": False, "reason": "empty"}
        if len(content) > MAX_COMMENT_LEN:
            return {"ok": False, "reason": "too_long", "max": MAX_COMMENT_LEN}
        wallet = money.of(uid)
        if wallet.gold < config.comment_price:
            return {"ok": False, "reason": "no_gold",
                    "cost": config.comment_price}
        if not await BottleDB.add_comment(bottle_id, uid, content):
            return {"ok": False, "reason": "not_found"}
        wallet.gold -= config.comment_price
        return {"ok": True, "bottle_id": bottle_id, "cost": config.comment_price}

    @staticmethod
    async def delete_bottle(bottle_id: int) -> dict:
        """软删除漂流瓶（SU）"""
        if not await BottleDB.delete_bottle(bottle_id):
            return {"ok": False, "reason": "not_found"}
        return {"ok": True, "bottle_id": bottle_id}
