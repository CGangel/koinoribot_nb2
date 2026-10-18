"""新版钓鱼（fish）规则与配置访问层。

数据目录（品种、商店商品、各级别权重）的唯一数据源在 config_store 的
配置表（配置面板可增删改），本模块只负责读取、校验与公式：

- 级别长度区间（固定规则，基准长度 = 品种 base_length_cm，即 D 级最小长度）：
    D [基准, 基准×1.2)  C [×1.2, ×1.4)  B [×1.4, ×1.6)
    A [×1.6, ×1.9)      S [×1.9, ×2.2)  X [×2.2, ×3.0)
  各级区间首尾相接；水族箱成长上限（可成长至）= 钓获时长度 × 成长上限
  系数（config.fish_growth_cap_factor，默认 1.5）。
- 售价 = 基准售价 × 售价格指数^(出售时长度 / 基准长度)
- 三件装备各管一条，互不重叠：鱼竿决定级别概率（grade_weights）、
  鱼线提供幸运值（luck，抵扣空军）、鱼饵决定稀有度概率（rarity_weights）；
  某级别内的长度完全由随机数决定（不再有大小加成）。
- 稀有度基准权重 / 级别区间 / 品种表 / 商店表 / 槽位基准价：
  均可配置，缺项或非法值回落默认；初始鱼竿/鱼线/鱼饵条目被删除时
  自动补回（保证游戏可玩）。
- 商店计价货币：每件商品的购买价可单独指定 currency（gold 金币 /
  kirastone 宝石 / luckygold 幸运币）；鱼竿/鱼线的修理固定金币；
  非法值按类别回落（鱼竿/鱼线→幸运币，鱼饵→金币）。
- 彩蛋：独立于稀有度的特殊钓获栏（固定 5 件单品，权重各自可调）。
  每次钓鱼先按彩蛋概率判定，命中则按单品权重抽取（彩蛋不判定空军），
  未命中按原稀有度权重抽取鱼类；幸运暴击必然走鱼类分支。
- 空军：每种鱼有各自的基础空军概率（品种表 air_force_rate，百分点）；
  最终空军率 =（基础空军率 − 总幸运值）/ 100，下限 0。

TODO(balance): 具体数值待玩法设计定稿。
"""

import copy

from ...config_store import config
from ...config_store import (
    FISH_CURRENCIES,
    _FISH_COLLECTION_REWARDS_DEFAULT,
    _FISH_EGG_ITEMS_DEFAULT,
    _FISH_POTION_ITEMS_DEFAULT,
    _FISH_SHOP_ITEMS_DEFAULT,
    norm_bool,
)

# ================== 稀有度（品种分层，固定 5 档） ==================
RARITIES: dict[str, dict] = {
    "普通": {"weight": 600},
    "稀有": {"weight": 250},
    "史诗": {"weight": 100},
    "传说": {"weight": 45},
    "神话": {"weight": 5},
}

RARITY_ORDER: list[str] = ["普通", "稀有", "史诗", "传说", "神话"]


# 稀有度基准权重（仅当鱼饵未配置 rarity_weights 时回落使用）
_DEFAULT_RARITY_WEIGHTS: dict[str, float] = {
    "普通": 65.0, "稀有": 29.0, "史诗": 5.75, "传说": 0.25, "神话": 0.0,
}


def rarity_weights() -> dict[str, float]:
    """稀有度基准权重（配置面板 fish_rarity_weights 覆盖；非法项回落默认）"""
    return _norm_weights(config.fish_rarity_weights, RARITY_ORDER,
                         _DEFAULT_RARITY_WEIGHTS)


# ================== 商店计价货币 ==================
# 商店每件商品的购买价均可单独指定计价货币（currency 键）；
# 鱼竿/鱼线的修理固定金币。非法值按类别回落默认。
# 目录唯一数据源在 config_store.FISH_CURRENCIES（面板下拉与展示共用）。
CURRENCIES: dict[str, str] = FISH_CURRENCIES


def currency_name(key: str) -> str:
    """货币键的中文名（未知键回落「金币」）"""
    return CURRENCIES.get(key, CURRENCIES["gold"])


def _norm_currency(raw, default: str) -> str:
    if raw in CURRENCIES:
        return raw
    return default


# ================== 级别（D/C/B/A/S/X） ==================
# 级别序（用于图鉴“历史最大级别”比较，越靠后越大）
GRADE_ORDER: list[str] = ["D", "C", "B", "A", "S", "X"]

# 各级别长度区间系数的回落默认（配置项 fish_grade_ranges 覆盖）
_DEFAULT_GRADE_RANGES: dict[str, list] = {
    "D": [1.0, 1.2], "C": [1.2, 1.4], "B": [1.4, 1.6],
    "A": [1.6, 1.9], "S": [1.9, 2.2], "X": [2.2, 3.0],
}


def grade_ranges() -> dict[str, list]:
    """各级别长度区间系数 [lo, hi)：长度 = 基准长度 × 区间随机。

    可在配置面板修改（fish_grade_ranges）；非法项回落默认。
    """
    merged = {k: list(v) for k, v in _DEFAULT_GRADE_RANGES.items()}
    raw = config.fish_grade_ranges or {}
    if isinstance(raw, dict):
        for grade, pair in raw.items():
            if grade not in merged or not isinstance(pair, (list, tuple)):
                continue
            try:
                lo, hi = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            if lo > 0 and hi > lo:
                merged[grade] = [lo, hi]
    return merged


def grade_range(grade: str) -> list:
    """单个级别的长度区间系数（未知级别回落 D）"""
    ranges = grade_ranges()
    return ranges.get(grade, ranges["D"])

# 鱼竿级别概率的回落默认（鱼竿未配置 grade_weights 时使用，与初始鱼竿一致；
# 改初始鱼竿 grade_weights 时须同步此处，测试断言两者相等以防漂移）
_DEFAULT_GRADE_WEIGHTS: dict[str, float] = {
    "D": 60.0, "C": 20.0, "B": 14.0, "A": 5.0, "S": 1.0, "X": 0.0,
}


def _norm_weights(raw, keys: list[str], fallback: dict) -> dict[str, float]:
    """把 {key: 权重} 规范化到 keys 全集：非法/缺项取 fallback，负值归零。

    全为 0（或配置整体非法）时回落 fallback，避免出现无法抽取的分布。
    """
    merged = {key: max(0.0, float(fallback.get(key, 0.0))) for key in keys}
    if isinstance(raw, dict):
        for key in keys:
            if key not in raw:
                continue
            try:
                merged[key] = max(0.0, float(raw[key]))
            except (TypeError, ValueError):
                continue
    if sum(merged.values()) <= 0:
        merged = {key: max(0.0, float(fallback.get(key, 0.0))) for key in keys}
    return merged


def grade_weights(rod_config: dict | None = None) -> dict[str, float]:
    """鱼竿的级别概率（rod_config 未配置 grade_weights 时回落基准分布）"""
    raw = (rod_config or {}).get("grade_weights")
    return _norm_weights(raw, GRADE_ORDER, _DEFAULT_GRADE_WEIGHTS)


def bait_rarity_weights(bait_config: dict | None = None) -> dict[str, float]:
    """鱼饵的稀有度概率（bait_config 未配置 rarity_weights 时回落基准分布）"""
    raw = (bait_config or {}).get("rarity_weights")
    return _norm_weights(raw, RARITY_ORDER, rarity_weights())


# 稀有度简写（商店属性行用，避免超出双列宽度）
_RARITY_SHORT = {"普通": "普", "稀有": "稀", "史诗": "史",
                 "传说": "传", "神话": "神"}


def _pct(value: float) -> str:
    """百分比紧凑显示：35.0% → 35%，5.75% → 5.75%，0.25% → 0.25%"""
    return f"{value:g}%"


def gear_attr_text(entry: dict, kind: str) -> str:
    """商店用属性说明（紧凑）：鱼竿=级别概率，鱼线=幸运，鱼饵=稀有度概率"""
    if kind == "rod":
        w = entry.get("grade_weights") or {}
        total = sum(float(v) for v in w.values()) or 1.0
        parts = [f"{g}{_pct(round(float(w.get(g, 0)) / total * 100, 2))}"
                 for g in GRADE_ORDER if float(w.get(g, 0)) > 0]
        return "D/C/B/A/S/X " + " ".join(parts) if parts else "基准分布"
    if kind == "line":
        return f"幸运 +{int(entry.get('luck', 0))}"
    w = entry.get("rarity_weights") or {}
    total = sum(float(v) for v in w.values()) or 1.0
    parts = [f"{_RARITY_SHORT[r]}{_pct(round(float(w.get(r, 0)) / total * 100, 2))}"
             for r in RARITY_ORDER if float(w.get(r, 0)) > 0]
    return "普/稀/史/传/神 " + " ".join(parts) if parts else "基准分布"


def grade_rank(grade: str) -> int:
    """级别序数值，用于比较大小；非法级别按 D 处理"""
    try:
        return GRADE_ORDER.index(grade)
    except ValueError:
        return 0


def _growth_cap_factor() -> float:
    """成长上限系数（配置面板可改）：非法值回落 1.5，小于 1 按 1 处理"""
    try:
        factor = float(config.fish_growth_cap_factor)
    except (TypeError, ValueError):
        return 1.5
    return max(1.0, factor)


def growth_cap(caught_length_cm: float) -> float:
    """水族箱成长上限（可成长至）= 钓获时长度 × 成长上限系数（默认 1.5）"""
    return round(max(0.1, float(caught_length_cm)) * _growth_cap_factor(), 1)


# 「已长至最大」的判定容差（长度以 0.1cm 步长记录）
_GROWN_EPSILON = 1e-6


def is_fully_grown(length_cm: float, caught_length_cm: float) -> bool:
    """水族箱中的鱼是否已长至成长上限。

    出售大鱼（批量）与水族箱面板的「已长至最大」共用同一判定口径。
    """
    try:
        return float(length_cm) >= growth_cap(caught_length_cm) - _GROWN_EPSILON
    except (TypeError, ValueError):
        return False


# ================== 品种表（配置面板可增删改） ==================

_FALLBACK_SPECIES: dict = {
    "blueberry_fish": {"name": "蓝莓鱼", "rarity": "普通", "base_price": 100,
                       "base_length_cm": 16, "air_force_rate": 0.04, "art": ""},
}


def _norm_species(entry) -> dict | None:
    """规范化品种条目，缺字段/非法时按默认补齐；完全不合法返回 None"""
    if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
        return None
    rarity = entry.get("rarity")
    if rarity not in RARITIES:
        rarity = RARITY_ORDER[0]
    try:
        base_price = max(1, int(entry.get("base_price", 1)))
        base_length = max(0.1, float(entry.get("base_length_cm", 20)))
    except (TypeError, ValueError):
        return None
    try:  # 基础空军率以百分点记（0~100），最终空军率再除以 100
        air_force = min(100.0, max(0.0, float(entry.get("air_force_rate", 10))))
    except (TypeError, ValueError):
        air_force = 10.0
    return {
        "name": str(entry["name"]),
        "rarity": rarity,
        "base_price": base_price,
        "base_length_cm": base_length,
        "air_force_rate": air_force,
        "art": str(entry.get("art", "") or ""),
    }


def species_table() -> dict[str, dict]:
    """品种表（name/rarity/base_price/base_length_cm/air_force_rate/art）。

    输出按稀有度层级排序（同层内保持配置顺序），保证图鉴等列表
    按稀有度分组显示。
    """
    raw = config.fish_species if isinstance(config.fish_species, dict) else {}
    table = {}
    for species_id, entry in raw.items():
        normed = _norm_species(entry)
        if normed is not None:
            table[str(species_id)] = normed
    if not table:
        table = {k: dict(v) for k, v in _FALLBACK_SPECIES.items()}
    rarity_index = {rarity: i for i, rarity in enumerate(RARITY_ORDER)}
    return dict(sorted(
        table.items(),
        key=lambda kv: rarity_index.get(kv[1]["rarity"], len(RARITY_ORDER)),
    ))


def get_species(species_id: str):
    return species_table().get(species_id)


def species_ids_by_rarity(rarity: str) -> list[str]:
    """某稀有度层级下的全部品种 id"""
    return [sid for sid, sp in species_table().items() if sp["rarity"] == rarity]


# ================== 鱼竿 / 鱼线 / 鱼饵商店（配置面板可增删改） ==================

DEFAULT_ROD = "rod_basic"
DEFAULT_LINE = "line_basic"
DEFAULT_BAIT = "bait_basic"

def _norm_gear_common(entry) -> dict | None:
    """鱼竿/鱼线的公共字段（名称/价格/计价货币/耐久/修复价）"""
    if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
        return None
    try:
        price = max(0, int(entry.get("price", 0)))
        repair = max(0, int(entry.get("repair_price", 0)))
    except (TypeError, ValueError):
        return None
    durability = entry.get("durability")
    if durability is not None:
        try:
            durability = max(1, int(durability))
        except (TypeError, ValueError):
            durability = None
    return {
        "name": str(entry["name"]),
        "price": price,
        "currency": _norm_currency(entry.get("currency"), "luckygold"),
        "desc": str(entry.get("desc", "") or ""),
        "durability": durability,
        "repair_price": repair,
    }


def _norm_rod(entry) -> dict | None:
    """鱼竿：额外携带级别概率 grade_weights（只影响级别/大鱼概率）"""
    base = _norm_gear_common(entry)
    if base is None:
        return None
    raw = entry.get("grade_weights")
    base["grade_weights"] = _norm_weights(raw, GRADE_ORDER, _DEFAULT_GRADE_WEIGHTS)
    return base


def _norm_line(entry) -> dict | None:
    """鱼线：额外携带幸运值 luck（百分点，只影响空军概率）"""
    base = _norm_gear_common(entry)
    if base is None:
        return None
    try:
        base["luck"] = max(0, int(entry.get("luck", 0)))
    except (TypeError, ValueError):
        base["luck"] = 0
    return base


def _norm_bait(entry) -> dict | None:
    """规范化鱼饵条目；unlimited 为 True 表示无限使用。

    unlimited 经 norm_bool 解析：面板旧版会把嵌套布尔存成字符串
    （"false"/"true"），直接 bool() 会把 "false" 也判为无限。
    """
    if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
        return None
    try:
        price = max(0, int(entry.get("price", 0)))
    except (TypeError, ValueError):
        return None
    return {
        "name": str(entry["name"]),
        "price": price,
        "currency": _norm_currency(entry.get("currency"), "gold"),
        "desc": str(entry.get("desc", "") or ""),
        "unlimited": norm_bool(entry.get("unlimited")),
        "rarity_weights": _norm_weights(
            entry.get("rarity_weights"), RARITY_ORDER,
            _norm_weights(config.fish_rarity_weights, RARITY_ORDER,
                          _DEFAULT_RARITY_WEIGHTS)),
    }


def _table(config_value, normalizer, fallback: dict) -> dict[str, dict]:
    raw = config_value if isinstance(config_value, dict) else {}
    table = {}
    for key, entry in raw.items():
        normed = normalizer(entry)
        if normed is not None:
            table[str(key)] = normed
    for key, entry in fallback.items():
        table.setdefault(key, entry)
    return table


def rod_table() -> dict[str, dict]:
    """鱼竿商品表（price 为售价、currency 为计价货币）；初始鱼竿条目缺失时自动补回"""
    return _table(config.fish_shop_rods, _norm_rod, {
        DEFAULT_ROD: {"name": "木质鱼竿", "price": 0, "currency": "luckygold",
                      "desc": "溪边削出的第一根竿，握在手里刚刚好",
                      "durability": None, "repair_price": 0,
                      "grade_weights": dict(_DEFAULT_GRADE_WEIGHTS)},
    })


def line_table() -> dict[str, dict]:
    """鱼线商品表（price 为售价、currency 为计价货币）；初始鱼线条目缺失时自动补回"""
    return _table(config.fish_shop_lines, _norm_line, {
        DEFAULT_LINE: {"name": "尼龙鱼线", "price": 0, "currency": "luckygold",
                       "desc": "最常见的尼龙线，简单可靠",
                       "durability": None, "repair_price": 0, "luck": 0},
    })


def bait_table() -> dict[str, dict]:
    """鱼饵商品表（price 为单价、currency 为计价货币）；初始鱼饵条目缺失时自动补回"""
    return _table(config.fish_shop_baits, _norm_bait, {
        DEFAULT_BAIT: {"name": "普通鱼饵", "price": 0, "currency": "gold",
                       "desc": "水里到处都有，管够",
                       "unlimited": True,
                       "rarity_weights": dict(_DEFAULT_RARITY_WEIGHTS)},
    })


def _norm_shop_item(entry) -> dict | None:
    """规范化道具商品条目（钓鱼商店·道具栏；无附加行为字段）"""
    if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
        return None
    try:
        price = max(0, int(entry.get("price", 0)))
    except (TypeError, ValueError):
        return None
    return {
        "name": str(entry["name"]),
        "price": price,
        "currency": _norm_currency(entry.get("currency"), "gold"),
        "desc": str(entry.get("desc", "") or ""),
    }


def item_table() -> dict[str, dict]:
    """道具商品表（键为道具 id，购买后存钓鱼背包 fish_props）；
    条目缺失时按内置默认补回"""
    return _table(config.fish_shop_items, _norm_shop_item,
                  copy.deepcopy(_FISH_SHOP_ITEMS_DEFAULT))


# ================== 彩蛋（独立于稀有度的特殊钓获栏） ==================
# 每次钓鱼先按彩蛋概率（fish_egg_rate，百分点）判定是否命中彩蛋；命中则
# 按各单品 weight 权重抽取一件（彩蛋钓获不判定空军），未命中按原稀有度
# 权重抽取鱼类；幸运暴击必然走鱼类分支。条目固定 4 件，名称与条目均
# 不可增删改，weight 各自可调；被删条目自动补回（与商店表回落策略一致）。
# 鱼饵包/无人认领的钱包/药水包三件固定钓到自动开启（无法储存），发放
# 路径按 id 分派（见 service._open_egg_item）；图鉴只记是否钓到
#（无次数/级别/长度，未钓到时隐藏名称）。

# 彩蛋单品 id（顺序即面板/图鉴展示顺序）
EGG_ORDER: list[str] = list(_FISH_EGG_ITEMS_DEFAULT)

# 前三件固定自动开启（不产生可储存物品，直接执行奖励发放分支）
EGG_BAIT_PACK_ID = "egg_bait_pack"       # 鱼饵包 → 随机鱼饵
EGG_WALLET_ID = "egg_wallet"             # 无人认领的钱包 → 金币、幸运币
EGG_POTION_PACK_ID = "egg_potion_pack"   # 药水包 → 随机药水
# 第四件入钓鱼背包
EGG_SWEEP_TICKET_ID = "egg_sweep_ticket"   # 钓鱼扫荡劵 → 钓鱼背包


def egg_rate() -> float:
    """彩蛋概率（百分点，0~100），配置可改"""
    try:
        return min(100.0, max(0.0, float(config.fish_egg_rate)))
    except (TypeError, ValueError):
        return 10.0


def _norm_egg(entry) -> dict | None:
    """规范化彩蛋单品条目（缺字段/非法值按内置默认补齐；多余键丢弃）"""
    if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
        return None
    try:
        weight = max(0.0, float(entry.get("weight", 20)))
    except (TypeError, ValueError):
        weight = 20.0
    return {
        "name": str(entry["name"]),
        "weight": weight,
        "desc": str(entry.get("desc", "") or ""),
        "art": str(entry.get("art", "") or ""),
    }


def egg_table() -> dict[str, dict]:
    """彩蛋单品目录（配置面板可调权重/描述/图片；条目被删时自动补回）。

    按 EGG_ORDER 过滤：存量配置里已被下架的旧条目（如 egg_gacha）不再
    出现在目录/图鉴/背包中。"""
    table = _table(config.fish_egg_items, _norm_egg, {
        key: dict(value) for key, value in _FISH_EGG_ITEMS_DEFAULT.items()
    })
    return {key: table[key] for key in EGG_ORDER if key in table}


def egg_weights() -> dict[str, float]:
    """彩蛋单品抽取权重（按 EGG_ORDER 全集规范化；全 0 时回落等权）"""
    table = egg_table()
    weights = {key: float(table.get(key, {}).get("weight", 0.0)) for key in EGG_ORDER}
    if sum(weights.values()) <= 0:
        weights = {key: 1.0 for key in EGG_ORDER}
    return weights


# ================== 药水（钓鱼背包道具，彩蛋「药水包」掉落） ==================
# 固定 3 种（大鱼/稀有度/回溯）：存储复用 fish_props（钓鱼背包道具表），
# 装备位在 fish_players.potion_id（同时只能装备一种，用完自动卸下）。
# 条目与名称固定不可增删改。

# 药水 id（顺序即面板/背包展示顺序）
POTION_ORDER: list[str] = list(_FISH_POTION_ITEMS_DEFAULT)
POTION_RARITY_ID = "potion_rarity"      # 稀有度药水
POTION_BIGFISH_ID = "potion_bigfish"    # 大鱼药水
POTION_ROLLBACK_ID = "potion_rollback"  # 回溯药水

# 药水效果（固定规则，非面板配置项）：
# - 稀有度/大鱼药水：非彩蛋钓获各消耗 1 个，把对应抽取的随机落点值
#   加上加成（落点与加成之和不超过 1，即最高档封顶）
# - 回溯药水：空军时消耗 1 个，按原有空军概率重判一次（仅一次），
#   见 service.rollback_cast
POTION_RARITY_BONUS = 0.02
POTION_BIGFISH_BONUS = 0.05

# 鱼饵包的两种掉落（固定引用商店鱼饵条目；表条目固定不可删，稳定存在）
EGG_BAIT_PACK_COMMON_ID = "bait_shrimp"   # 75%：樱花鱼饵 ×3~6
EGG_BAIT_PACK_RARE_ID = "bait_lucky"      # 25%：梦幻鱼饵 ×1~3


def _norm_potion(entry) -> dict | None:
    """规范化药水条目（缺字段/非法值按内置默认补齐；多余键丢弃）"""
    if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
        return None
    return {
        "name": str(entry["name"]),
        "desc": str(entry.get("desc", "") or ""),
    }


def potion_table() -> dict[str, dict]:
    """药水目录（配置面板可改描述；条目被删时自动补回）"""
    return _table(config.fish_potion_items, _norm_potion, {
        key: dict(value) for key, value in _FISH_POTION_ITEMS_DEFAULT.items()
    })


# ================== 幸运宝珠（与鱼竿/鱼线并列的装备） ==================

# 幸运宝珠是唯一单件装备（只能购买一次、购买后自动装备、无耐久、不可卸下、
# 不可升级以外的方式变更）；名称/价格/描述与全部数值均可在配置面板修改。
def orb_info() -> dict:
    """幸运宝珠的展示信息（名称/价格/计价货币/描述），配置可改"""
    try:
        price = max(0, int(config.fish_orb_price))
    except (TypeError, ValueError):
        price = 100000
    return {
        "name": str(config.fish_orb_name or "幸运宝珠"),
        "price": price,
        "currency": _norm_currency(config.fish_orb_currency, "gold"),
        "desc": str(config.fish_orb_desc or ""),
    }


def _orb_int(value, default: int, low: int = 1) -> int:
    try:
        return max(low, int(value))
    except (TypeError, ValueError):
        return default


def pump_info() -> dict:
    """氧气泵的展示信息（名称/价格/计价货币/描述/成长加成），配置可改"""
    try:
        price = max(0, int(config.fish_pump_price))
    except (TypeError, ValueError):
        price = 200
    try:
        bonus = max(0.0, float(config.fish_pump_growth_bonus))
    except (TypeError, ValueError):
        bonus = 100.0
    return {
        "name": str(config.fish_pump_name or "氧气泵"),
        "price": price,
        "currency": _norm_currency(config.fish_pump_currency, "luckygold"),
        "desc": str(config.fish_pump_desc or ""),
        "growth_bonus": bonus,
    }


def growth_rate_multiplier(pump_owned) -> float:
    """水族箱成长速率倍率：氧气泵按百分点加成（100 → 2 倍）"""
    if not pump_owned:
        return 1.0
    return 1.0 + pump_info()["growth_bonus"] / 100.0


def orb_energy_cap(level: int) -> int:
    """能量上限 = 基础值 - 每级递减 × (等级-1)，配置可改"""
    try:
        level = max(1, int(level))
    except (TypeError, ValueError):
        level = 1
    base = _orb_int(config.fish_orb_energy_cap_base, 50)
    reduce = _orb_int(config.fish_orb_cap_reduce_per_level, 10, 0)
    return max(10, base - reduce * (level - 1))


def orb_max_level() -> int:
    """幸运宝珠最高等级，配置可改"""
    return _orb_int(config.fish_orb_max_level, 5)


def orb_upgrade_cost(current_level: int) -> int:
    """升到下一级的价格（幸运币）= 基础价 × 升级前等级"""
    try:
        base = max(1, int(config.fish_orb_upgrade_base_price))
    except (TypeError, ValueError):
        base = 100
    return base * max(1, int(current_level))


def orb_lucky_bonus() -> float:
    """幸运暴击的幸运值加成（配置可改；0.2 = +20%，直接抵扣空军概率）"""
    try:
        return max(0.0, float(config.fish_orb_lucky_bonus))
    except (TypeError, ValueError):
        return 0.2


def lucky_rarities() -> list[str]:
    """幸运暴击可选稀有度（配置项 fish_orb_lucky_min_rarity 及以上）"""
    min_rarity = str(config.fish_orb_lucky_min_rarity or "")
    start = RARITY_ORDER.index(min_rarity) if min_rarity in RARITY_ORDER else 0
    return RARITY_ORDER[start:]


def lucky_grades() -> list[str]:
    """幸运暴击可选级别（配置项 fish_orb_lucky_min_grade 及以上）"""
    min_grade = str(config.fish_orb_lucky_min_grade or "")
    start = GRADE_ORDER.index(min_grade) if min_grade in GRADE_ORDER else 0
    return GRADE_ORDER[start:]


# ================== 水族箱 ==================
# 养成公式（service._apply_growth，无需照料、纯时间成长）：
#   成长量 = 钓获时长度 × 每日自然成长率 × 在馆天数
#   当前长度 = min(成长上限, 钓获时长度 + 成长量)
#   成长上限（可成长至）= 钓获时长度 × 成长上限系数
#   （config.fish_growth_cap_factor，默认 1.5；展示口径同此）
# 图鉴“历史最大长度”只按钓获时的长度统计，养成增长不回写图鉴。
# 槽位价格：第 n 个槽位 = 基准价 × 2^(n-初始槽位)。
# 以上数值均可在配置面板修改。


# 水族箱槽位：固定 10 起、上限 20（= 水族箱卡片 4 列 × 5 行），不可配置
AQUARIUM_INITIAL_SLOTS = 10
AQUARIUM_MAX_SLOTS = 20


def aquarium() -> dict:
    """水族箱参数（槽位固定、每日固定成长厘米数），逐项读取配置并做兜底"""
    def _float(value, default, low):
        try:
            return max(low, float(value))
        except (TypeError, ValueError):
            return default

    return {
        "initial_slots": AQUARIUM_INITIAL_SLOTS,
        "max_slots": AQUARIUM_MAX_SLOTS,
        "daily_growth_cm": _float(config.fish_aquarium_daily_growth_cm, 15, 0.0),
    }


def slot_price(slot_no: int) -> int:
    """扩展到第 slot_no 个槽位的价格：基准价 × 2^(slot_no - 初始槽位数)"""
    try:
        base_price = float(config.fish_slot_base_price)
    except (TypeError, ValueError):
        base_price = 5000.0
    exponent = max(0, int(slot_no) - aquarium()["initial_slots"])
    return int(base_price * (2 ** exponent))


def auto_sell_threshold() -> int:
    """自动出售阈值：开启后成长封顶售价低于该值的鱼钓上即卖（配置可改）"""
    return _orb_int(config.fish_auto_sell_threshold, 10000, 0)


# ================== 图鉴奖励（配置面板可改） ==================

# 配置格式：{"稀有度:级别档": {"奖励": {奖励键: 数量}}}——外层「奖励」包裹
# 使面板二级子页以嵌套键值块渲染（可自由增删改单项、单档配多项）；
# 旧扁平格式 {"稀有度:级别档": {奖励键: 数量}} 仍可读取。
# 奖励键：预留键（gold/luckygold/kirastone/orb/pump/fish_limit/su_code）
# 或钓鱼商店商品 id（鱼饵/鱼竿/鱼线，类型按所在表推导）。
# 读取时规范化为 [{type, count[, id]}, ...]；非消耗品数量强制 1。
REWARD_CONSUMABLE_TYPES = {"gold", "luckygold", "kirastone", "bait", "fish_limit"}
REWARD_TYPES = REWARD_CONSUMABLE_TYPES | {
    "rod", "line", "orb", "pump", "su_code",
}
# 需要商品 id 的类型（对应钓鱼商店表条目）
REWARD_ID_TYPES = {"bait", "rod", "line"}

# 预留奖励键 → 类型（与商店商品 id 区分开）
_REWARD_RESERVED: dict[str, str] = {
    "gold": "gold",
    "luckygold": "luckygold",
    "kirastone": "kirastone",
    "orb": "orb",
    "pump": "pump",
    "fish_limit": "fish_limit",
    "su_code": "su_code",
}


def _reward_key(rarity: str, grade: str) -> str:
    return f"{rarity}:{grade}"


def _norm_reward_items(raw) -> list[dict]:
    """规范化单档奖励：解包「奖励」嵌套块（兼容旧扁平格式）后，
    预留键直接映射类型，其余按钓鱼商店表推导鱼饵/鱼竿/鱼线；
    数量非法/未知的键丢弃；非消耗品数量强制 1。"""
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("奖励"), dict):
        raw = raw["奖励"]
    items: list[dict] = []
    for key, value in raw.items():
        key = str(key).strip()
        if not key or key == "奖励":
            continue
        try:
            count = max(0, int(value))
        except (TypeError, ValueError):
            continue
        if key in _REWARD_RESERVED:
            rtype = _REWARD_RESERVED[key]
        elif key in bait_table():
            rtype = "bait"
        elif key in rod_table():
            rtype = "rod"
        elif key in line_table():
            rtype = "line"
        else:
            continue
        item = {"type": rtype}
        if rtype in REWARD_ID_TYPES:
            item["id"] = key
        item["count"] = 1 if rtype not in REWARD_CONSUMABLE_TYPES else count
        if item["count"] >= 1:
            items.append(item)
    return items


def collection_rewards() -> dict[str, list[dict]]:
    """图鉴奖励目录（规范化后）{"稀有度:级别档": [{type, count[, id]}, ...]}。

    配置面板可增删改（fish_collection_rewards）；键缺失或整档条目非法时
    该档回落内置默认值，保证 30 档奖励始终可玩（与商店表回落策略一致）。
    """
    raw = config.fish_collection_rewards
    raw = raw if isinstance(raw, dict) else {}
    table: dict[str, list[dict]] = {}
    valid_keys = {
        _reward_key(rarity, grade)
        for rarity in RARITY_ORDER for grade in GRADE_ORDER
    }
    for key in valid_keys:
        items = _norm_reward_items(raw.get(key))
        if not items:
            items = _norm_reward_items(_FISH_COLLECTION_REWARDS_DEFAULT.get(key))
        if items:
            table[key] = items
    return table


def reward_items_of(rarity: str, grade: str) -> list[dict]:
    """单档奖励列表（无奖励返回空列表）"""
    return collection_rewards().get(_reward_key(rarity, grade), [])


def reward_item_display(item: dict) -> str | None:
    """奖励项展示文本（查看图鉴奖励用；商品 id 失效时显示原始 id）"""
    rtype = item.get("type")
    try:
        count = int(item.get("count", 1))
    except (TypeError, ValueError):
        count = 1
    if rtype == "gold":
        return f"金币×{count}"
    if rtype == "luckygold":
        return f"幸运币×{count}"
    if rtype == "kirastone":
        return f"宝石×{count}"
    if rtype == "bait":
        bait = bait_table().get(item.get("id", ""))
        name = bait["name"] if bait else str(item.get("id", "未知鱼饵"))
        return f"{name}×{count}"
    if rtype in ("rod", "line"):
        table = rod_table() if rtype == "rod" else line_table()
        gear = table.get(item.get("id", ""))
        name = gear["name"] if gear else str(item.get("id", "未知装备"))
        return name
    if rtype == "orb":
        return orb_info()["name"]
    if rtype == "pump":
        return pump_info()["name"]
    if rtype == "fish_limit":
        return f"每日钓鱼次数+{count}"
    if rtype == "su_code":
        return "SU激活码获取权限"
    return None


# ================== 杂项 ==================

def cast_cd() -> int:
    """单次钓鱼冷却（秒），配置可改"""
    try:
        return max(0, int(config.fish_cast_cd))
    except (TypeError, ValueError):
        return 60


def lucky_tiers() -> list:
    """放生幸运币分档 [[售价下限, 幸运币], ...]（按售价升序），配置可改"""
    default = [[10000, 1], [50000, 2], [100000, 5], [1000000, 10]]
    raw = config.fish_lucky_tiers
    if not isinstance(raw, (list, tuple)):
        return default
    tiers = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            tiers.append([int(item[0]), int(item[1])])
        except (TypeError, ValueError):
            continue
    return sorted(tiers) if tiers else default


def lucky_value_of_price(price: int) -> int:
    """放生可获得的幸运币（按售价分档，档位可在配置面板修改）：

    售价低于首档下限 → 0（不可放生）；否则取「售价下限 ≤ 售价」的最高档。
    """
    try:
        price = int(price)
    except (TypeError, ValueError):
        return 0
    tiers = lucky_tiers()
    value = 0
    for threshold, coins in tiers:
        if price >= threshold:
            value = coins
        else:
            break
    return value


def air_force_rate(base_rate: float, luck_total: float) -> float:
    """最终空军率 =（品种基础空军率 − 总幸运值）/ 100，下限 0。

    base_rate 来自品种表 air_force_rate；luck_total 为鱼线幸运值
    （+ 幸运暴击时的宝珠加成），两者均为百分点。
    """
    try:
        base = max(0.0, float(base_rate))
    except (TypeError, ValueError):
        base = 0.0
    try:
        luck = max(0.0, float(luck_total))
    except (TypeError, ValueError):
        luck = 0.0
    return max(0.0, (base - luck) / 100.0)


def sell_price_exponent() -> float:
    """售价指数（配置可改，默认 1.8）"""
    try:
        value = float(config.fish_sell_price_exponent)
    except (TypeError, ValueError):
        return 1.8
    return value if value > 1.0 else 1.8


def calc_sell_price(species_id: str, length_cm: float) -> int:
    """售价 = 基准售价 × 指数^(出售时长度 / 基准长度)，至少 1 金币。

    级别通过其长度区间间接影响售价；水族箱养大后按养成长度计价；
    指数可在配置面板修改（fish_sell_price_exponent）。
    """
    sp = get_species(species_id)
    if not sp or sp["base_length_cm"] <= 0:
        return 1
    ratio = max(0.1, length_cm / sp["base_length_cm"])
    return max(1, int(sp["base_price"] * (sell_price_exponent() ** ratio)))
