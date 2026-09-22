"""新版钓鱼（fish）业务逻辑层。

FishService 面向命令层返回纯数据 dict，消息文案在 __init__ 里拼装。
规则与公式集中在 fish_config（长度区间/售价/成长上限/槽位价格为固定
规则，其余数值可配置），当前概率加成与成长公式为占位实现，
TODO(balance) 标记处待补充。
"""

import random
import time

from ...money import money
from . import fish_config as C
from .db import FishDB
from .fish_limit import FishLimitManager
from .fish_config import (
    GRADE_ORDER,
    RARITY_ORDER,
    air_force_rate,
    aquarium,
    bait_rarity_weights,
    bait_table,
    calc_sell_price,
    collection_rewards,
    grade_range,
    grade_rank,
    grade_weights,
    growth_rate_multiplier,
    line_table,
    lucky_grades,
    lucky_rarities,
    lucky_value_of_price,
    orb_energy_cap,
    orb_lucky_bonus,
    orb_info,
    orb_max_level,
    orb_upgrade_cost,
    pump_info,
    rarity_weights,
    rod_table,
    species_ids_by_rarity,
    species_table,
)


def _weighted_pick(weights: dict, order: list, allow: list | None = None) -> str:
    """按权重抽取一个 key。

    allow 非空时只在该子集内抽取，权重按其比率归一化
    （如 A/S/X 原为 7%/1%/0% → 归一化为 87.5%/12.5%/0%）。
    """
    keys = [key for key in order if key in weights]
    if allow:
        allowed = [key for key in keys if key in allow]
        if allowed:            # 子集为空时保持全集，避免抽不出来
            keys = allowed
    values = [max(0.0, float(weights.get(key, 0.0))) for key in keys]
    if not keys:
        return order[0]
    if sum(values) <= 0:
        # 权重全为 0 的自定义配置：退化为均匀抽取，避免 choices 抛错
        return random.choice(keys)
    return random.choices(keys, weights=values, k=1)[0]


class FishService:
    """钓鱼核心流程编排"""

    # ===== 玩家 =====

    @classmethod
    async def ensure_player(cls, uid: int) -> dict:
        player = await FishDB.get_player(uid)
        if player is None:
            player = await FishDB.create_player(uid)
        else:
            # 槽位上限配置调整时归一存量玩家：低于新初始值免费补足，
            # 高于新上限的收回到上限（面板太长会刷屏，见 README）
            initial = aquarium()["initial_slots"]
            maximum = aquarium()["max_slots"]
            if not initial <= player["slots"] <= maximum:
                player["slots"] = min(max(player["slots"], initial), maximum)
                await FishDB.save_player(player)
        return player

    # ===== 渔具解析 =====

    @classmethod
    async def _resolve_gear(cls, uid: int, gear_item_id, kind: str) -> dict:
        """返回装备概要：配置条目 + 实例（初始渔具无实例、无耐久）"""
        table = rod_table() if kind == "rod" else line_table()
        basic_id = C.DEFAULT_ROD if kind == "rod" else C.DEFAULT_LINE

        if gear_item_id is None:
            return {"gear_id": basic_id, "config": table[basic_id], "instance": None}

        instance = await FishDB.get_gear(gear_item_id, uid)
        if instance is None or instance["kind"] != kind:
            # 实例丢失（异常数据）回落初始渔具
            return {"gear_id": basic_id, "config": table[basic_id], "instance": None}

        config = table.get(instance["gear_id"])
        if config is None:
            # 配置中被删除的商品：回落初始渔具
            return {"gear_id": basic_id, "config": table[basic_id], "instance": None}
        return {"gear_id": instance["gear_id"], "config": config, "instance": instance}

    # ===== 钓鱼（仅单抽） =====

    @classmethod
    async def do_cast(cls, uid: int) -> dict:
        """单次钓鱼。冷却由命令层 FreqLimiter 控制。

        上一条钓获未处理（卖鱼/放生/放入水族箱）时不能再钓；
        钓到的鱼进入待处理区（pending），由命令层引导玩家选择。

        Returns:
            {"ok": False, "reason": "pending"|"bait"|"rod_broken"|"line_broken", ...}
            {"ok": True, "air": True, ...鱼属性} 空军（跑掉的鱼也返回属性供展示）
            {"ok": True, "air": False, ...鱼属性/售价/幸运值, "collection": 图鉴摘要}
        """
        player = await cls.ensure_player(uid)

        pending = await FishDB.get_pending_item(uid)
        if pending is not None:
            species = species_table().get(pending["species_id"], {})
            return {"ok": False, "reason": "pending",
                    "species_name": species.get("name", pending["species_id"]),
                    "item_id": pending["id"]}

        rod = await cls._resolve_gear(uid, player["rod_item_id"], "rod")
        line = await cls._resolve_gear(uid, player["line_item_id"], "line")

        # 耐久归零的商店渔具必须修复后才能继续
        if rod["instance"] is not None and rod["instance"]["durability"] <= 0:
            return {"ok": False, "reason": "rod_broken",
                    "gear_name": rod["config"]["name"]}
        if line["instance"] is not None and line["instance"]["durability"] <= 0:
            return {"ok": False, "reason": "line_broken",
                    "gear_name": line["config"]["name"]}

        # 鱼饵：初始鱼饵无限使用，商店鱼饵每次消耗 1 个
        bait_id = player["bait_id"]
        bait_config = bait_table().get(bait_id)
        if bait_config is None:
            bait_id = C.DEFAULT_BAIT
            bait_config = bait_table()[bait_id]
        if not bait_config.get("unlimited"):
            if not await FishDB.consume_bait(uid, bait_id, 1):
                return {"ok": False, "reason": "bait", "bait_id": bait_id}

        # 幸运宝珠：装备中且能量已满 → 本次钓鱼触发幸运暴击
        # （限定稀有度/级别 + 额外幸运值；触发后能量清零重新积累）
        orb_active = bool(player["orb_owned"] and player["orb_equipped"])
        orb_cap = orb_energy_cap(player["orb_level"]) if orb_active else 0
        lucky_cast = orb_active and player["orb_energy"] >= orb_cap
        if lucky_cast:
            player["orb_energy"] = 0
        # 幸运值只来自鱼线（+ 暴击时的宝珠加成）
        luck_total = line["config"].get("luck", 0) + (
            orb_lucky_bonus() if lucky_cast else 0.0
        )

        def _orb_info() -> dict | None:
            if not orb_active:
                return None
            return {
                "level": player["orb_level"],
                "energy": player["orb_energy"],
                "cap": orb_cap,
                "lucky_cast": lucky_cast,
                "lucky_bonus": orb_lucky_bonus() if lucky_cast else 0.0,
                "just_full": player["orb_energy"] >= orb_cap,
            }

        # 稀有度（鱼饵分布）→ 品种 → 级别（鱼竿分布）→ 长度（纯随机），逐级抽取。
        # 幸运暴击：稀有度限定 史诗及以上、级别限定 A 及以上，
        # 各自的概率按原比率归一化（如 A/S/X = 7%/1%/0% → 87.5%/12.5%/0%）。
        rarity = _weighted_pick(
            bait_rarity_weights(bait_config),
            RARITY_ORDER,
            lucky_rarities() if lucky_cast else None,
        )
        species_pool = species_ids_by_rarity(rarity)
        if not species_pool:  # 配置断层保护：该稀有度没有品种时回落最低档
            species_pool = species_ids_by_rarity(RARITY_ORDER[0])
        species_id = random.choice(species_pool)
        grade = _weighted_pick(
            grade_weights(rod["config"]),
            GRADE_ORDER,
            lucky_grades() if lucky_cast else None,
        )

        species = species_table()[species_id]
        length = cls._roll_length(species, grade)

        growth_cap_len = C.growth_cap(length)
        sell_price = calc_sell_price(species_id, length)
        grown_price = calc_sell_price(species_id, growth_cap_len)
        fish_info = {
            "species_id": species_id,
            "species_name": species["name"],
            "rarity": rarity,
            "grade": grade,
            "length": length,
            "growth_cap": growth_cap_len,
            "sell_price": sell_price,
            "lucky_value": lucky_value_of_price(sell_price),
            "grown_sell_price": grown_price,
            "grown_lucky_value": lucky_value_of_price(grown_price),
        }

        # 装备状态（供卡片展示）：鱼竿/鱼线名称与剩余耐久、鱼饵名称与剩余数量
        def _left(item: dict):
            """扣减本次消耗后的剩余耐久（初始装备无耐久 → None）"""
            instance = item["instance"]
            return None if instance is None else max(0, instance["durability"] - 1)

        gear_info = {
            "rod": {"name": rod["config"]["name"], "left": _left(rod),
                    "max": rod["config"]["durability"]},
            "line": {"name": line["config"]["name"], "left": _left(line),
                     "max": line["config"]["durability"]},
            "bait": {
                "name": bait_config["name"],
                "left": ("∞" if bait_config.get("unlimited")
                         else max(0, await FishDB.get_bait_count(uid, bait_id) - 1)),
            },
        }

        # 空军判定：最终空军率 = 品种基础空军率 - 总幸运值（含幸运暴击加成）
        if random.random() < air_force_rate(species["air_force_rate"], luck_total):
            await cls._wear_gear(rod)
            await cls._wear_gear(line)
            await FishDB.save_player(player)
            return {"ok": True, "air": True, "orb": _orb_info(),
                    "gear": gear_info, **fish_info}

        item_id = await FishDB.add_fish_item(uid, species_id, grade, length)
        collection = await FishDB.update_collection(uid, species_id, grade, length)

        # 成功钓到（不含空军）：宝珠能量 +1（封顶）
        if orb_active:
            player["orb_energy"] = min(orb_cap, player["orb_energy"] + 1)

        await cls._wear_gear(rod)
        await cls._wear_gear(line)
        await FishDB.save_player(player)

        # 自动处理：开启后按成长封顶售价分流——养大也不值钱的鱼
        # （可成长至的售价仍低于阈值）直接卖出；值得养的自动放入水族箱；
        # 水族箱已满时不处理，留在待处理区交给玩家选择
        auto_sold = auto_tank = False
        if player["auto_sell"]:
            if grown_price < C.auto_sell_threshold():
                money.of(uid).gold += sell_price
                await FishDB.delete_fish_item(item_id)
                auto_sold = True
            else:
                put = await cls.put_in_aquarium(uid)
                auto_tank = bool(put["ok"])

        return {
            "ok": True,
            "air": False,
            "item_id": None if (auto_sold or auto_tank) else item_id,
            "auto_sold": auto_sold,
            "auto_tank": auto_tank,
            "sell_price": calc_sell_price(species_id, length),
            "collection": collection,
            "orb": _orb_info(),
            "gear": gear_info,
            **fish_info,
        }

    @staticmethod
    async def _wear_gear(gear: dict):
        """商店渔具每次钓鱼耐久 -1（初始渔具无实例不消耗）"""
        instance = gear["instance"]
        if instance is None:
            return
        await FishDB.update_gear(instance["id"], instance["durability"] - 1)

    @staticmethod
    def _roll_length(species: dict, grade: str) -> float:
        """长度 = 基准长度 × 级别区间 [lo, hi) 内的均匀随机值。

        某级别内的长度完全由随机数决定（无额外的“大鱼加成”）。
        级别区间系数见 fish_config.grade_ranges()（配置可改）。
        """
        factor_low, factor_high = grade_range(grade)
        raw = float(species["base_length_cm"]) * random.uniform(factor_low, factor_high)
        return round(max(1.0, raw), 1)

    # ===== 钓获处理（卖鱼 / 放生 / 放入水族箱） =====

    @classmethod
    async def _sell_item(cls, uid: int, item: dict) -> dict:
        """卖出一条鱼；水族箱的鱼先结算养成长度再按当前长度计价"""
        if item["place"] == "aquarium":
            item = await cls._apply_growth(item)

        price = calc_sell_price(item["species_id"], item["length"])
        money.of(uid).gold += price

        player = await cls.ensure_player(uid)
        await FishDB.save_player(player)
        await FishDB.delete_fish_item(item["id"])

        species = species_table().get(item["species_id"], {})
        return {
            "ok": True,
            "price": price,
            "species_name": species.get("name", item["species_id"]),
            "grade": item["grade"],
            "length": item["length"],
        }

    @classmethod
    async def _release_item(cls, uid: int, item: dict) -> dict:
        """放生一条鱼：不获得金币，按售价分档获得幸运币。

        售价 <1万 的鱼幸运值为 0，无法放生（reason=no_lucky）。
        """
        if item["place"] == "aquarium":
            item = await cls._apply_growth(item)

        price = calc_sell_price(item["species_id"], item["length"])
        lucky = lucky_value_of_price(price)
        species = species_table().get(item["species_id"], {})
        if lucky < 1:
            return {"ok": False, "reason": "no_lucky", "price": price,
                    "species_name": species.get("name", item["species_id"])}

        money.of(uid).luckygold += lucky
        await FishDB.delete_fish_item(item["id"])
        return {
            "ok": True,
            "lucky_value": lucky,
            "price": price,
            "species_name": species.get("name", item["species_id"]),
            "grade": item["grade"],
            "length": item["length"],
        }

    @classmethod
    async def sell_pending(cls, uid: int) -> dict:
        """卖出待处理的鱼（每次钓获后需立即选择处理方式）"""
        item = await FishDB.get_pending_item(uid)
        if item is None:
            return {"ok": False, "reason": "no_pending"}
        return await cls._sell_item(uid, item)

    @classmethod
    async def release_pending(cls, uid: int) -> dict:
        """放生待处理的鱼，获得幸运币"""
        item = await FishDB.get_pending_item(uid)
        if item is None:
            return {"ok": False, "reason": "no_pending"}
        return await cls._release_item(uid, item)

    # ===== 水族箱 =====

    @classmethod
    async def put_in_aquarium(cls, uid: int) -> dict:
        """把待处理的鱼放入水族箱养成（槽位 = 玩家 slots，可扩展）"""
        item = await FishDB.get_pending_item(uid)
        if item is None:
            return {"ok": False, "reason": "no_pending"}

        player = await cls.ensure_player(uid)
        in_tank = await FishDB.list_fish_items(uid, place="aquarium")
        if len(in_tank) >= player["slots"]:
            return {"ok": False, "reason": "full"}

        await FishDB.update_fish_item(
            item["id"], place="aquarium", put_in_time=int(time.time())
        )
        return {"ok": True, "slot": len(in_tank) + 1,
                "species_name": species_table().get(
                    item["species_id"], {}).get("name", item["species_id"])}

    @classmethod
    async def list_aquarium(cls, uid: int) -> list[dict]:
        """列出水族箱的鱼（按入箱顺序），展示前结算成长，
        使面板的当前长度与“已长至最大”状态反映真实值"""
        items = await FishDB.list_fish_items(uid, place="aquarium")
        return [await cls._apply_growth(item) for item in items]

    @classmethod
    async def _aquarium_slot_item(cls, uid: int, slot_no: int) -> dict | None:
        """按水族箱槽位编号取鱼（1 起，按入箱顺序）"""
        items = await FishDB.list_fish_items(uid, place="aquarium")
        if not (1 <= slot_no <= len(items)):
            return None
        return items[slot_no - 1]

    @classmethod
    async def sell_aquarium(cls, uid: int, slot_no: int) -> dict:
        """卖出水族箱指定槽位的鱼（先结算养成长度）"""
        item = await cls._aquarium_slot_item(uid, slot_no)
        if item is None:
            return {"ok": False, "reason": "not_found"}
        result = await cls._sell_item(uid, item)
        result["slot"] = slot_no
        return result

    @classmethod
    async def release_aquarium(cls, uid: int, slot_no: int) -> dict:
        """放生水族箱指定槽位的鱼，获得幸运币"""
        item = await cls._aquarium_slot_item(uid, slot_no)
        if item is None:
            return {"ok": False, "reason": "not_found"}
        result = await cls._release_item(uid, item)
        result["slot"] = slot_no
        return result

    @classmethod
    async def sell_aquarium_batch(cls, uid: int, only_grown: bool = False) -> dict:
        """批量卖出水族箱的鱼（先结算养成长度，按当前长度计价）。

        only_grown=True 只卖已长至成长上限的鱼（出售大鱼），其余留在箱里；
        返回售出数量、总收入与留下数量。
        """
        items = await cls.list_aquarium(uid)
        sold = total = kept = 0
        for item in items:
            if only_grown and not C.is_fully_grown(
                item["length"], item["caught_length"]
            ):
                kept += 1
                continue
            result = await cls._sell_item(uid, item)
            sold += 1
            total += result["price"]
        return {"ok": True, "count": sold, "total": total, "kept": kept}

    @classmethod
    async def release_aquarium_all(
        cls, uid: int, only_grown: bool = False
    ) -> dict:
        """放生水族箱中全部可放生的鱼（幸运值≥1，即售价达到第一档阈值）。

        售价不足第一档的鱼无法通过放生获得幸运币，留在箱里（skipped）。
        only_grown=True 只处理已长至成长上限的鱼（放生大鱼），未长满的
        直接留下（kept，不计入放生尝试）。
        """
        items = await cls.list_aquarium(uid)
        released = total_lucky = skipped = kept = 0
        for item in items:
            if only_grown and not C.is_fully_grown(
                item["length"], item["caught_length"]
            ):
                kept += 1
                continue
            result = await cls._release_item(uid, item)
            if result["ok"]:
                released += 1
                total_lucky += result["lucky_value"]
            else:
                skipped += 1
        return {
            "ok": True, "count": released, "total_lucky": total_lucky,
            "skipped": skipped, "kept": kept,
        }

    @classmethod
    async def expand_slots(cls, uid: int) -> dict:
        """水族箱槽位 +1（金币），封顶 max_slots。

        价格 = 基准价 × 2^(槽位编号 - 初始槽位)。
        """
        player = await cls.ensure_player(uid)
        if player["slots"] >= aquarium()["max_slots"]:
            return {"ok": False, "reason": "max"}

        cost = C.slot_price(player["slots"] + 1)
        wallet = money.of(uid)
        if wallet.gold < cost:
            return {"ok": False, "reason": "no_gold", "cost": cost}
        wallet.gold -= cost

        player["slots"] += 1
        await FishDB.save_player(player)
        return {"ok": True, "slots": player["slots"], "cost": cost}

    @classmethod
    async def _apply_growth(cls, item: dict) -> dict:
        """结算水族箱养成增长并回写当前长度（纯时间成长，无需照料）。

        成长量 = 每日固定成长厘米数 × 在馆天数（天数按时间戳差值连续折算，
        不满一天按比例成长）；拥有氧气泵时成长速率按其加成提升
        （默认 +100% 即翻倍）；
        成长上限（可成长至）= 钓获时长度 × growth_cap_factor（1.5）。
        图鉴不在此更新——历史最大长度只统计钓获时的长度
        （fish_collection 由 update_collection 只在钓获时写入）。
        TODO(balance): 成长公式为占位。
        """
        if item["place"] != "aquarium" or not item["put_in_time"]:
            return item

        player = await cls.ensure_player(item["uid"])
        days = max(0.0, (time.time() - item["put_in_time"]) / 86400)
        growth = (
            aquarium()["daily_growth_cm"]
            * growth_rate_multiplier(player["pump_owned"])
            * days
        )
        cap = C.growth_cap(item["caught_length"])
        new_length = round(min(cap, item["caught_length"] + growth), 1)
        if new_length != item["length"]:
            await FishDB.update_fish_item(item["id"], length=new_length)
            item["length"] = new_length
        return item

    # ===== 商店与装备 =====

    @classmethod
    async def buy_gear(cls, uid: int, kind: str, gear_id: str) -> dict:
        """购买鱼竿/鱼线并自动装备（生成带耐久的实例；初始渔具不可购买）。

        非鱼饵商品限购一次：已拥有同款则拒绝。
        """
        table = rod_table() if kind == "rod" else line_table()
        gear = table.get(gear_id)
        if gear is None:
            return {"ok": False, "reason": "not_found"}
        if gear["durability"] is None:
            return {"ok": False, "reason": "basic", "name": gear["name"]}

        owned = await FishDB.list_gear(uid, kind)
        if any(g["gear_id"] == gear_id for g in owned):
            return {"ok": False, "reason": "owned", "name": gear["name"]}

        # 鱼竿/鱼线以幸运币计价
        wallet = money.of(uid)
        if wallet.luckygold < gear["price"]:
            return {"ok": False, "reason": "no_luckygold", "cost": gear["price"]}
        wallet.luckygold -= gear["price"]

        gear_item_id = await FishDB.add_gear(uid, kind, gear_id, gear["durability"])
        # 非消耗品买后自动装备
        player = await cls.ensure_player(uid)
        if kind == "rod":
            player["rod_item_id"] = gear_item_id
        else:
            player["line_item_id"] = gear_item_id
        await FishDB.save_player(player)
        return {"ok": True, "name": gear["name"], "gear_item_id": gear_item_id}

    @classmethod
    async def equip_gear(cls, uid: int, kind: str, gear_id: str) -> dict:
        """装备鱼竿/鱼线。初始渔具把实例位清空（NULL）；
        商店渔具从已拥有实例中取第一个可用实例。"""
        table = rod_table() if kind == "rod" else line_table()
        if gear_id not in table:
            return {"ok": False, "reason": "not_found"}

        player = await cls.ensure_player(uid)
        if table[gear_id]["durability"] is None:
            if kind == "rod":
                player["rod_item_id"] = None
            else:
                player["line_item_id"] = None
            await FishDB.save_player(player)
            return {"ok": True, "name": table[gear_id]["name"]}

        owned = await FishDB.list_gear(uid, kind)
        candidates = [g for g in owned if g["gear_id"] == gear_id]
        if not candidates:
            return {"ok": False, "reason": "not_owned"}
        # 优先耐久未归零的实例
        instance = next((g for g in candidates if g["durability"] > 0), candidates[0])
        if kind == "rod":
            player["rod_item_id"] = instance["id"]
        else:
            player["line_item_id"] = instance["id"]
        await FishDB.save_player(player)
        return {"ok": True, "name": table[gear_id]["name"]}

    @classmethod
    async def repair_gear(cls, uid: int, kind: str) -> dict:
        """修复装备中的商店鱼竿/鱼线，恢复满耐久。初始渔具无需修复。

        实际修复价 = 修复价 ×（最大耐久 - 当前耐久）/ 最大耐久。
        """
        player = await cls.ensure_player(uid)
        gear_item_id = player["rod_item_id"] if kind == "rod" else player["line_item_id"]
        if gear_item_id is None:
            return {"ok": False, "reason": "basic"}

        instance = await FishDB.get_gear(gear_item_id, uid)
        table = rod_table() if kind == "rod" else line_table()
        if instance is None or instance["kind"] != kind:
            return {"ok": False, "reason": "not_found"}
        config = table.get(instance["gear_id"])
        if config is None:
            return {"ok": False, "reason": "not_found"}

        max_durability = config["durability"]
        if instance["durability"] >= max_durability:
            return {"ok": False, "reason": "no_need", "name": config["name"]}

        # 按损耗比例计价：修得越及时越便宜
        cost = int(round(
            config["repair_price"]
            * (max_durability - instance["durability"]) / max_durability
        ))
        wallet = money.of(uid)
        if wallet.gold < cost:
            return {"ok": False, "reason": "no_gold", "cost": cost}
        wallet.gold -= cost
        await FishDB.update_gear(gear_item_id, max_durability)
        return {"ok": True, "name": config["name"], "durability": max_durability,
                "cost": cost}

    @classmethod
    async def buy_bait(cls, uid: int, bait_id: str, num: int) -> dict:
        baits = bait_table()
        bait = baits.get(bait_id)
        if num <= 0:
            return {"ok": False, "reason": "bad_args", "sub": "num"}
        if bait is None or bait.get("unlimited"):
            return {"ok": False, "reason": "bad_args"}

        cost = bait["price"] * num
        wallet = money.of(uid)
        if wallet.gold < cost:
            return {"ok": False, "reason": "no_gold", "cost": cost}
        wallet.gold -= cost
        await FishDB.add_bait(uid, bait_id, num)
        return {"ok": True, "name": bait["name"], "num": num, "cost": cost}

    @classmethod
    async def equip_bait(cls, uid: int, bait_id: str) -> dict:
        baits = bait_table()
        if bait_id not in baits:
            return {"ok": False, "reason": "not_found"}
        player = await cls.ensure_player(uid)
        player["bait_id"] = bait_id
        await FishDB.save_player(player)
        return {"ok": True, "name": baits[bait_id]["name"]}

    # ===== 幸运宝珠（与鱼竿/鱼线并列的装备） =====

    @classmethod
    async def buy_orb(cls, uid: int) -> dict:
        """购买幸运宝珠（限购一次；购买后自动装备，1 级 / 0 能量 / 无耐久）"""
        orb = orb_info()
        player = await cls.ensure_player(uid)
        if player["orb_owned"]:
            return {"ok": False, "reason": "owned", "name": orb["name"]}

        wallet = money.of(uid)
        if wallet.gold < orb["price"]:
            return {"ok": False, "reason": "no_gold", "cost": orb["price"]}
        wallet.gold -= orb["price"]

        player["orb_owned"] = 1
        player["orb_equipped"] = 1
        player["orb_level"] = 1
        player["orb_energy"] = 0
        await FishDB.save_player(player)
        return {
            "ok": True,
            "name": orb["name"],
            "price": orb["price"],
            "level": 1,
            "energy_cap": orb_energy_cap(1),
        }

    @classmethod
    async def upgrade_orb(cls, uid: int) -> dict:
        """升级幸运宝珠（花幸运币）：价格 = 基础价 × 升级前等级。

        每升 1 级能量上限 -10（1 级 50 → 5 级 10），最高 5 级；
        升级后能量超出新上限时截断到上限。
        """
        player = await cls.ensure_player(uid)
        if not player["orb_owned"]:
            return {"ok": False, "reason": "not_owned"}
        level = player["orb_level"]
        if level >= orb_max_level():
            return {"ok": False, "reason": "max", "level": level}

        cost = orb_upgrade_cost(level)
        wallet = money.of(uid)
        if wallet.luckygold < cost:
            return {"ok": False, "reason": "no_luckygold", "cost": cost}
        wallet.luckygold -= cost

        player["orb_level"] = level + 1
        cap = orb_energy_cap(level + 1)
        if player["orb_energy"] > cap:
            player["orb_energy"] = cap
        await FishDB.save_player(player)
        return {"ok": True, "level": player["orb_level"], "cost": cost, "energy_cap": cap}

    # ===== 氧气泵（被动装备：提升成长速率） =====

    @classmethod
    async def buy_pump(cls, uid: int) -> dict:
        """购买氧气泵（限购一次；幸运币计价，购买后被动生效无需装备）"""
        pump = pump_info()
        player = await cls.ensure_player(uid)
        if player["pump_owned"]:
            return {"ok": False, "reason": "owned", "name": pump["name"]}

        wallet = money.of(uid)
        if wallet.luckygold < pump["price"]:
            return {"ok": False, "reason": "no_luckygold", "cost": pump["price"]}
        wallet.luckygold -= pump["price"]

        player["pump_owned"] = 1
        await FishDB.save_player(player)
        return {"ok": True, "name": pump["name"], "cost": pump["price"],
                "growth_bonus": pump["growth_bonus"]}

    @classmethod
    async def set_auto_sell(cls, uid: int, enabled: bool) -> dict:
        """开启/关闭自动出售（成长封顶售价低于阈值的鱼钓上即卖）"""
        player = await cls.ensure_player(uid)
        player["auto_sell"] = 1 if enabled else 0
        await FishDB.save_player(player)
        return {"ok": True, "enabled": bool(player["auto_sell"]),
                "threshold": C.auto_sell_threshold()}

    # ===== 图鉴奖励 =====
    # 每个稀有度各 6 档（D/C/B/A/S/X）：该稀有度全部品种收集齐、且全部
    # 品种的历史最大级别都 ≥ 档位级别时可领取该档；领取记录落库后，
    # fish_limit（每日次数加成）与 获取激活码（SU 权限）按 记录+当前配置 推导。

    @classmethod
    async def collection_reward_states(cls, uid: int) -> dict:
        """全部档位的领取状态与各稀有度收集情况。

        返回 {"states": {稀有度: {级别档: "claimable"|"claimed"|"locked"}},
              "collected": {稀有度: 是否全品种已收集}}；
        collected 用于 传说/神话 档奖励内容的显示门槛（未全收集显示 ???）。
        """
        species = species_table()
        stats = {row["species_id"]: row
                 for row in await FishDB.get_collection(uid)}
        claimed = set(await FishDB.get_claimed_rewards(uid))

        states: dict[str, dict[str, str]] = {}
        collected: dict[str, bool] = {}
        for rarity in RARITY_ORDER:
            ids = [sid for sid, sp in species.items() if sp["rarity"] == rarity]
            if ids and all(sid in stats for sid in ids):
                collected[rarity] = True
                min_rank = min(grade_rank(stats[sid]["max_grade"]) for sid in ids)
            else:
                collected[rarity] = False
                min_rank = -1
            grade_states = {}
            for grade in GRADE_ORDER:
                if min_rank >= grade_rank(grade):
                    grade_states[grade] = (
                        "claimed" if (rarity, grade) in claimed else "claimable"
                    )
                else:
                    grade_states[grade] = "locked"
            states[rarity] = grade_states
        return {"states": states, "collected": collected}

    @classmethod
    async def claimable_reward_count(cls, uid: int) -> int:
        """当前可领取的档数（图鉴尾部提醒用）"""
        info = await cls.collection_reward_states(uid)
        return sum(
            1
            for grades in info["states"].values()
            for state in grades.values()
            if state == "claimable"
        )

    @classmethod
    async def _grant_reward_item(cls, uid: int, item: dict) -> str | None:
        """发放单项奖励，返回展示文本。

        已拥有的非消耗品（鱼竿/鱼线/宝珠/氧气泵）按售价等额折算为该商品
        的计价货币（鱼竿/鱼线/氧气泵为幸运币，宝珠为金币）；商品 id 已被
        配置删除时返回 None（不发放）。
        """
        wallet = money.of(uid)
        rtype = item["type"]
        count = int(item.get("count", 1))

        if rtype == "gold":
            wallet.gold += count
            return f"金币×{count}"
        if rtype == "luckygold":
            wallet.luckygold += count
            return f"幸运币×{count}"
        if rtype == "kirastone":
            wallet.kirastone += count
            return f"宝石×{count}"
        if rtype == "bait":
            bait = bait_table().get(item.get("id", ""))
            if bait is None:
                return None
            await FishDB.add_bait(uid, item["id"], count)
            return f"{bait['name']}×{count}"
        if rtype in ("rod", "line"):
            table = rod_table() if rtype == "rod" else line_table()
            gear = table.get(item.get("id", ""))
            if gear is None:
                return None
            owned = any(g["gear_id"] == item["id"]
                        for g in await FishDB.list_gear(uid, rtype))
            if owned:
                wallet.luckygold += gear["price"]
                return f"{gear['name']}（已拥有，折算 幸运币×{gear['price']}）"
            await FishDB.add_gear(uid, rtype, item["id"],
                                  gear["durability"] or 1)
            return f"{gear['name']}×1"
        if rtype == "orb":
            orb = orb_info()
            player = await cls.ensure_player(uid)
            if player["orb_owned"]:
                wallet.gold += orb["price"]
                return f"{orb['name']}（已拥有，折算 金币×{orb['price']}）"
            player["orb_owned"] = 1
            player["orb_equipped"] = 1
            await FishDB.save_player(player)
            return f"{orb['name']}×1"
        if rtype == "pump":
            pump = pump_info()
            player = await cls.ensure_player(uid)
            if player["pump_owned"]:
                wallet.luckygold += pump["price"]
                return f"{pump['name']}（已拥有，折算 幸运币×{pump['price']}）"
            player["pump_owned"] = 1
            await FishDB.save_player(player)
            return f"{pump['name']}×1"
        if rtype == "fish_limit":
            # 永久增益写入 fish_limit 表的 perm_bonus（当天总次数 = 基础 + 临时 + 永久）
            FishLimitManager.add_perm_bonus(uid, count)
            return f"每日钓鱼次数上限+{count}"
        if rtype == "su_code":
            return "SU激活码获取权限（发送 获取激活码 使用）"
        return None

    @classmethod
    async def claim_collection_rewards(cls, uid: int) -> dict:
        """领取全部可领取的图鉴奖励（先落领取记录再发放，防重复发放）。

        返回 {"count": 领取档数, "results": [{"rarity", "grade", "text"}, ...]}。
        """
        info = await cls.collection_reward_states(uid)
        rewards = collection_rewards()
        results = []
        for rarity in RARITY_ORDER:
            for grade in GRADE_ORDER:
                if info["states"][rarity][grade] != "claimable":
                    continue
                items = rewards.get(f"{rarity}:{grade}", [])
                if not items:
                    continue
                if not await FishDB.add_claimed_reward(uid, rarity, grade):
                    continue    # 并发领取：记录已存在，跳过发放
                grants = []
                for item in items:
                    text = await cls._grant_reward_item(uid, item)
                    if text:
                        grants.append(text)
                results.append({
                    "rarity": rarity,
                    "grade": grade,
                    "text": "、".join(grants) if grants else "无",
                })
        return {"count": len(results), "results": results}

    @classmethod
    async def su_code_unlocked(cls, uid: int) -> bool:
        """是否领取过含 SU激活码权限 的图鉴奖励档位（获取激活码 指令门槛）"""
        rewards = collection_rewards()
        for rarity, grade in await FishDB.get_claimed_rewards(uid):
            for item in rewards.get(f"{rarity}:{grade}", []):
                if item.get("type") == "su_code":
                    return True
        return False
