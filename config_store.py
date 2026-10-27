"""配置存储：SQLite config 表 + 内存配置（模块级 config 实例身份不变、原地更新，面板经 update_config 改值）。"""

from __future__ import annotations

import copy
import importlib.util
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from nonebot.log import logger
from pydantic import BaseModel, ValidationError

_PLUGIN_DIR = Path(__file__).resolve().parent
PASSWD_FILENAME = "passwd.py"
PASSWD_TEMPLATE_FILENAME = "passwd.py.template"
LEGACY_CONFIG_FILENAME = "koinori_config.py"

DEFAULT_DB_PATH = _PLUGIN_DIR / "src" / "database" / "koinoribot.db"


# ================== 新版钓鱼（fish）默认数据目录 ==================
# 品种/商店/权重目录的唯一数据源在此（配置面板可增删改），plugins/fish
# 的 fish_config 只做读取与校验。三件装备各管一条：鱼竿 grade_weights
# 级别概率、鱼线 luck 幸运（降低空军）、鱼饵 rarity_weights 稀有度概率。

# 品种表按稀有度排列（普通 → 神话），面板二级子页同序展示；
# base_length_cm 为基准长度（D 级最小长度），base_price 为基准售价，
# air_force_rate 为该品种的基础空军率（百分点；最终 =（基础 − 总幸运）/100，下限 0）。
# 价格区间：普通 100-2000 / 稀有 2000-4000 / 史诗 5000-10000 / 传说 20000-100000 / 神话 100000-1000000。
# 基础空军率（百分点）梯度：普通 4-11 / 稀有 12-19 / 史诗 24-31 / 传说 38-45 / 神话 55-58。
# 长度仍为占位梯度，待玩法设计定稿。
_FISH_SPECIES_DEFAULT: dict = {
    # ---- 普通（8 种，基准售价 100-2000）----
    "blueberry_fish": {"name": "蓝莓鱼", "rarity": "普通", "base_price": 100, "base_length_cm": 16, "air_force_rate": 10, "art": "src/img/fish/art/blueberry_fish.png"},
    "cream_fish": {"name": "奶油鱼", "rarity": "普通", "base_price": 200, "base_length_cm": 18, "air_force_rate": 10, "art": "src/img/fish/art/cream_fish.png"},
    "strawberry_fish": {"name": "草莓鱼", "rarity": "普通", "base_price": 300, "base_length_cm": 20, "air_force_rate": 10, "art": "src/img/fish/art/strawberry_fish.png"},
    "peach_fish": {"name": "蜜桃鱼", "rarity": "普通", "base_price": 400, "base_length_cm": 22, "air_force_rate": 10, "art": "src/img/fish/art/peach_fish.png"},
    "matcha_fish": {"name": "抹茶鱼", "rarity": "普通", "base_price": 600, "base_length_cm": 24, "air_force_rate": 10, "art": "src/img/fish/art/matcha_fish.png"},
    "cocoa_fish": {"name": "可可鱼", "rarity": "普通", "base_price": 800, "base_length_cm": 26, "air_force_rate": 10, "art": "src/img/fish/art/cocoa_fish.png"},
    "caramel_fish": {"name": "焦糖鱼", "rarity": "普通", "base_price": 1300, "base_length_cm": 28, "air_force_rate": 10, "art": "src/img/fish/art/caramel_fish.png"},
    "pudding_fish": {"name": "布丁鱼", "rarity": "普通", "base_price": 2000, "base_length_cm": 30, "air_force_rate": 10, "art": "src/img/fish/art/pudding_fish.png"},
    # ---- 稀有（8 种，基准售价 2000-4000）----
    "sea_salt_fish": {"name": "海盐鱼", "rarity": "稀有", "base_price": 2000, "base_length_cm": 28, "air_force_rate": 15, "art": "src/img/fish/art/sea_salt_fish.png"},
    "morning_dew_fish": {"name": "晨露鱼", "rarity": "稀有", "base_price": 2200, "base_length_cm": 30, "air_force_rate": 15, "art": "src/img/fish/art/morning_dew_fish.png"},
    "clear_sky_fish": {"name": "晴空鱼", "rarity": "稀有", "base_price": 2400, "base_length_cm": 34, "air_force_rate": 15, "art": "src/img/fish/art/clear_sky_fish.png"},
    "wind_bell_fish": {"name": "风铃鱼", "rarity": "稀有", "base_price": 2700, "base_length_cm": 38, "air_force_rate": 15, "art": "src/img/fish/art/wind_bell_fish.png"},
    "mint_breeze_fish": {"name": "薄荷风鱼", "rarity": "稀有", "base_price": 3000, "base_length_cm": 30, "air_force_rate": 15, "art": "src/img/fish/art/mint_breeze_fish.png"},
    "lemon_soda_fish": {"name": "柠檬汽泡鱼", "rarity": "稀有", "base_price": 3300, "base_length_cm": 30, "air_force_rate": 15, "art": "src/img/fish/art/lemon_soda_fish.png"},
    "soda_bubble_fish": {"name": "苏打气泡鱼", "rarity": "稀有", "base_price": 3600, "base_length_cm": 30, "air_force_rate": 15, "art": "src/img/fish/art/soda_bubble_fish.png"},
    "cloud_marshmallow_fish": {"name": "云朵软糖鱼", "rarity": "稀有", "base_price": 4000, "base_length_cm": 30, "air_force_rate": 15, "art": "src/img/fish/art/cloud_marshmallow_fish.png"},
    # ---- 史诗（8 种，基准售价 5000-10000）----
    "white_ripple_fish": {"name": "白涟鱼", "rarity": "史诗", "base_price": 5000, "base_length_cm": 55, "air_force_rate": 20, "art": "src/img/fish/art/white_ripple_fish.png"},
    "summer_shade_fish": {"name": "夏影鱼", "rarity": "史诗", "base_price": 5500, "base_length_cm": 45, "air_force_rate": 20, "art": "src/img/fish/art/summer_shade_fish.png"},
    "autumn_tide_fish": {"name": "秋汐鱼", "rarity": "史诗", "base_price": 6100, "base_length_cm": 50, "air_force_rate": 20, "art": "src/img/fish/art/autumn_tide_fish.png"},
    "winter_firefly_fish": {"name": "冬萤鱼", "rarity": "史诗", "base_price": 6700, "base_length_cm": 60, "air_force_rate": 20, "art": "src/img/fish/art/winter_firefly_fish.png"},
    "spring_crimson_fish": {"name": "春绯鱼", "rarity": "史诗", "base_price": 7400, "base_length_cm": 40, "air_force_rate": 20, "art": "src/img/fish/art/spring_crimson_fish.png"},
    "sunset_glow_fish": {"name": "霞光鱼", "rarity": "史诗", "base_price": 8200, "base_length_cm": 35, "air_force_rate": 20, "art": "src/img/fish/art/sunset_glow_fish.png"},
    "iridescent_fish": {"name": "虹彩鱼", "rarity": "史诗", "base_price": 9100, "base_length_cm": 45, "air_force_rate": 20, "art": "src/img/fish/art/iridescent_fish.png"},
    "twilight_fish": {"name": "暮色鱼", "rarity": "史诗", "base_price": 10000, "base_length_cm": 50, "air_force_rate": 20, "art": "src/img/fish/art/twilight_fish.png"},
    # ---- 传说（8 种，基准售价 20000-100000）----
    "starfield_blue_fish": {"name": "星野蓝鱼", "rarity": "传说", "base_price": 20000, "base_length_cm": 56, "air_force_rate": 40, "art": "src/img/fish/art/starfield_blue_fish.png"},
    "night_glow_star_fish": {"name": "夜光星鱼", "rarity": "传说", "base_price": 25000, "base_length_cm": 78, "air_force_rate": 40, "art": "src/img/fish/art/night_glow_star_fish.png"},
    "cherry_blossom_blizzard_fish": {"name": "樱吹雪鱼", "rarity": "传说", "base_price": 32000, "base_length_cm": 60, "air_force_rate": 40, "art": "src/img/fish/art/cherry_blossom_blizzard_fish.png"},
    "dream_blue_fish": {"name": "梦境蓝鱼", "rarity": "传说", "base_price": 40000, "base_length_cm": 80, "air_force_rate": 40, "art": "src/img/fish/art/dream_blue_fish.png"},
    "mist_hidden_fish": {"name": "雾隐幽鱼", "rarity": "传说", "base_price": 50000, "base_length_cm": 50, "air_force_rate": 40, "art": "src/img/fish/art/mist_hidden_fish.png"},
    "cloudbreak_light_fish": {"name": "云隙光鱼", "rarity": "传说", "base_price": 63000, "base_length_cm": 130, "air_force_rate": 40, "art": "src/img/fish/art/cloudbreak_light_fish.png"},
    "frost_aurora_fish": {"name": "糖霜极光鱼", "rarity": "传说", "base_price": 79000, "base_length_cm": 70, "air_force_rate": 40, "art": "src/img/fish/art/frost_aurora_fish.png"},
    "snow_feather_streamer_fish": {"name": "雪羽流光鱼", "rarity": "传说", "base_price": 100000, "base_length_cm": 110, "air_force_rate": 40, "art": "src/img/fish/art/snow_feather_streamer_fish.png"},
    # ---- 神话（4 种，基准售价 100000-1000000）----
    "spirit_fish": {"name": "精灵鱼", "rarity": "神话", "base_price": 100000, "base_length_cm": 80, "air_force_rate": 60, "art": "src/img/fish/art/spirit_fish.png"},
    "phantom_star_fish": {"name": "幻境星鱼", "rarity": "神话", "base_price": 215000, "base_length_cm": 130, "air_force_rate": 60, "art": "src/img/fish/art/phantom_star_fish.png"},
    "silver_moon_emperor_fish": {"name": "银月皇鱼", "rarity": "神话", "base_price": 325000, "base_length_cm": 120, "air_force_rate": 60, "art": "src/img/fish/art/silver_moon_emperor_fish.png"},
    "glass_fantasy_fish": {"name": "琉璃幻彩鱼", "rarity": "神话", "base_price": 1000000, "base_length_cm": 100, "air_force_rate": 60, "art": "src/img/fish/art/glass_fantasy_fish.png"},
}

# 渔具加成模型（三件装备各管一条，互不重叠）：
#   - 鱼竿：决定「级别」概率分布（级别越高 → 鱼越长，即大鱼概率）；售价为幸运币
#   - 鱼线：提供幸运值（百分点），用于抵消品种基础空军率；售价为幸运币
#   - 鱼饵：决定「稀有度」概率分布；售价为金币
# 某级别内的长度完全由随机数决定（见 fish_grade_ranges）。

# 商店计价货币（currency 键）：gold 金币 / kirastone 宝石 / luckygold 幸运币，
# 每件商品可单独配置（只作用于购买价；鱼竿/鱼线的修理固定金币；
# 读取层对非法值按类别回落）。目录唯一数据源在此（面板下拉与
# fish_config 展示共用，避免两处漂移）。
FISH_CURRENCIES: dict[str, str] = {
    "gold": "金币",
    "kirastone": "宝石",
    "luckygold": "幸运币",
}
_FISH_RODS_DEFAULT: dict = {
    "rod_basic": {"name": "木质鱼竿", "price": 0, "currency": "luckygold",
                  "desc": "溪边削出的第一根竿，握在手里刚刚好",
                  "durability": None, "repair_price": 0,
                  "grade_weights": {"D": 60, "C": 20, "B": 14, "A": 5, "S": 1, "X": 0}},
    "rod_fiberglass": {"name": "碳纤维鱼竿", "price": 50, "currency": "luckygold",
                       "desc": "轻得几乎感觉不到，却韧得能扛住大鱼",
                       "durability": 30, "repair_price": 5000,
                       "grade_weights": {"D": 47, "C": 25, "B": 18, "A": 7, "S": 2, "X": 1}},
    "rod_carbon": {"name": "樱花鱼竿", "price": 200, "currency": "luckygold",
                   "desc": "缠着樱花纹的竿，花开时节鱼格外活跃",
                   "durability": 60, "repair_price": 50000,
                   "grade_weights": {"D": 35, "C": 28, "B": 20, "A": 10, "S": 5, "X": 2}},
    "rod_master": {"name": "海神鱼竿", "price": 800, "currency": "luckygold",
                   "desc": "传说由海神鳞甲锻成，深海巨物也难逃",
                   "durability": 100, "repair_price": 100000,
                   "grade_weights": {"D": 30, "C": 25, "B": 25, "A": 10, "S": 6, "X": 4}},
}

_FISH_LINES_DEFAULT: dict = {
    "line_basic": {"name": "尼龙鱼线", "price": 0, "currency": "luckygold",
                   "desc": "最常见的尼龙线，简单可靠",
                   "durability": None, "repair_price": 0, "luck": 0},
    "line_nylon": {"name": "碳素鱼线", "price": 50, "currency": "luckygold",
                   "desc": "碳素编织，水下的动静再难逃过",
                   "durability": 30, "repair_price": 5000, "luck": 5},
    "line_braided": {"name": "樱花鱼线", "price": 200, "currency": "luckygold",
                     "desc": "泛着樱花柔光，鱼儿靠近便不再挣扎",
                     "durability": 60, "repair_price": 50000, "luck": 10},
    "line_dragon": {"name": "大师鱼线", "price": 800, "currency": "luckygold",
                    "desc": "名匠手作的传世之线，系住无数传说",
                    "durability": 100, "repair_price": 100000, "luck": 25},
}

_FISH_BAITS_DEFAULT: dict = {
    "bait_basic": {"name": "普通鱼饵", "price": 0, "currency": "gold",
                   "desc": "水里到处都有，管够",
                   "unlimited": True,
                   "rarity_weights": {"普通": 65, "稀有": 29, "史诗": 5.75,
                                      "传说": 0.25, "神话": 0}},
    "bait_worm": {"name": "秘制鱼饵", "price": 100, "currency": "gold",
                  "desc": "老师傅加了几味料，说不清但好使",
                  "unlimited": False,
                  "rarity_weights": {"普通": 55, "稀有": 34, "史诗": 10.5,
                                     "传说": 0.5, "神话": 0}},
    "bait_shrimp": {"name": "樱花鱼饵", "price": 1000, "currency": "gold",
                    "desc": "花瓣揉进饵料，香气能引来稀客",
                    "unlimited": False,
                    "rarity_weights": {"普通": 45, "稀有": 38, "史诗": 16,
                                       "传说": 0.75, "神话": 0.25}},
    "bait_lucky": {"name": "梦幻鱼饵", "price": 10000, "currency": "gold",
                   "desc": "如雾如幻，连传说里的鱼都为它现身",
                   "unlimited": False,
                   "rarity_weights": {"普通": 25, "稀有": 40, "史诗": 33.2,
                                      "传说": 1.2, "神话": 0.6}},
}

# 彩蛋单品目录（独立于稀有度的特殊钓获栏）：钓鱼先按 fish_egg_rate 判定
# 是否命中彩蛋，命中则按各单品 weight 权重抽取一件（彩蛋钓获不判定空军、
# 不占鱼类图鉴档位，以「彩蛋」档单独记录）。条目与名称固定不可增删改。
# 鱼饵包/无人认领的钱包/药水包三件固定钓到自动开启（无法储存，内容效果
# 待补充）；钓鱼扫荡券存入钓鱼背包（fish_props）。
_FISH_EGG_ITEMS_DEFAULT: dict = {
    "egg_bait_pack": {"name": "鱼饵包", "weight": 20,
                      "desc": "一个鱼饵包裹，掉落随机鱼饵", "art": ""},
    "egg_wallet": {"name": "无人认领的钱包", "weight": 20,
                   "desc": "不知是谁掉在河里的钱包", "art": ""},
    "egg_potion_pack": {"name": "药水包", "weight": 10,
                        "desc": "一个药水包裹，掉落随机药水", "art": ""},
    "egg_sweep_ticket": {"name": "钓鱼扫荡券", "weight": 50,
                         "desc": "一键钓鱼扫荡券", "art": ""},
}

# 药水目录（固定 3 种，由彩蛋「药水包」随机掉落）：装备后生效（同时只能
# 装备一种，用完自动卸下）。效果见 fish_config 药水常量（非彩蛋消耗，
# 稀有度/大鱼提升对应随机落点，回溯空军重判一次）。
# 存储复用 fish_props（钓鱼背包道具表），装备位在 fish_players.potion_id。
_FISH_POTION_ITEMS_DEFAULT: dict = {
    "potion_bigfish": {"name": "大鱼药水",
                       "desc": "额外提高大鱼概率"},
    "potion_rarity": {"name": "稀有度药水",
                      "desc": "额外提高稀有度"},
    "potion_rollback": {"name": "回溯药水",
                        "desc": "空军时回溯一次时空"},
}

# 钓鱼商店·道具商品表（键为道具 id，与钓鱼背包 fish_props 同一命名空间，
# 如彩蛋「钓鱼扫荡券」的 egg_sweep_ticket）：条目与名称固定不可增删改，
# 只能改各属性与价格。
_FISH_SHOP_ITEMS_DEFAULT: dict = {
    "egg_sweep_ticket": {"name": "钓鱼扫荡券", "price": 10000,
                         "currency": "gold",
                         "desc": "一键钓鱼的入场券，一次性消耗10次钓鱼次数"},
}

# 图鉴奖励目录：键 "稀有度:级别档"（每个稀有度 6 档 D/C/B/A/S/X，共 30 档，
# 档位键固定不可增删改），值为 {"奖励": {奖励键: 数量}}——外层「奖励」包裹
# 使面板二级子页以嵌套键值块渲染，可自由增删改单项与配置多项奖励：
#   预留键：gold 金币 / luckygold 幸运币 / kirastone 宝石 / orb 幸运宝珠 /
#           pump 氧气泵 / fish_limit 每日钓鱼次数上限 / su_code SU激活码获取权限
#   商品键：钓鱼商店商品 id（鱼饵/鱼竿/鱼线），类型按所在表自动推导
# 非消耗品（orb/pump/su_code/鱼竿/鱼线）数量强制 1（读取层规范化兜底）；
# 鱼竿/鱼线/宝珠/氧气泵发放时若已拥有则按售价折算为对应货币。
_FISH_COLLECTION_REWARDS_DEFAULT: dict = {
    "普通:D": {"奖励": {"gold": 10000}},
    "普通:C": {"奖励": {"bait_worm": 200}},
    "普通:B": {"奖励": {"rod_fiberglass": 1}},
    "普通:A": {"奖励": {"luckygold": 20}},
    "普通:S": {"奖励": {"rod_carbon": 1}},
    "普通:X": {"奖励": {"luckygold": 40}},
    "稀有:D": {"奖励": {"gold": 50000}},
    "稀有:C": {"奖励": {"bait_shrimp": 100}},
    "稀有:B": {"奖励": {"line_nylon": 1}},
    "稀有:A": {"奖励": {"luckygold": 30}},
    "稀有:S": {"奖励": {"line_braided": 1}},
    "稀有:X": {"奖励": {"luckygold": 60}},
    "史诗:D": {"奖励": {"gold": 100000}},
    "史诗:C": {"奖励": {"bait_lucky": 100}},
    "史诗:B": {"奖励": {"pump": 1}},
    "史诗:A": {"奖励": {"luckygold": 40}},
    "史诗:S": {"奖励": {"fish_limit": 5}},
    "史诗:X": {"奖励": {"luckygold": 80}},
    "传说:D": {"奖励": {"gold": 500000}},
    "传说:C": {"奖励": {"bait_lucky": 200}},
    "传说:B": {"奖励": {"rod_master": 1}},
    "传说:A": {"奖励": {"luckygold": 50}},
    "传说:S": {"奖励": {"line_dragon": 1}},
    "传说:X": {"奖励": {"luckygold": 100}},
    "神话:D": {"奖励": {"gold": 2000000}},
    "神话:C": {"奖励": {"bait_lucky": 400}},
    "神话:B": {"奖励": {"fish_limit": 5}},
    "神话:A": {"奖励": {"luckygold": 60}},
    "神话:S": {"奖励": {"fish_limit": 10}},
    "神话:X": {"奖励": {"su_code": 1}},
}

# 稀有度基准权重 / 级别长度区间的内置默认（键即稀有度/级别名，
# 固定不可增删改——面板与 update_config 双重强制）
_FISH_RARITY_WEIGHTS_DEFAULT: dict = {"普通": 65, "稀有": 29, "史诗": 5.75,
                                      "传说": 0.25, "神话": 0}
_FISH_GRADE_RANGES_DEFAULT: dict = {"D": [1.0, 1.2], "C": [1.2, 1.4],
                                    "B": [1.4, 1.6], "A": [1.6, 1.9],
                                    "S": [1.9, 2.2], "X": [2.2, 3.0]}

# mlai 供应商预设：固定 5 个槽位（键 1-5 不可增删），AI 对话的供应商
# 接入参数内嵌于每个槽位，mlai_preset_id 选择生效槽位（面板下拉切换）。
# 记忆库/群聊等行为参数在顶层「AI对话」分区与预设项并列配置。
_MLAI_PRESETS_DEFAULT: dict = {
    str(slot): {
        "name": "",
        "mlai_api_key": "",
        "mlai_api_base_url": "",
        "mlai_model": "deepseek-chat",
        "mlai_system_prompt": "你是冰祈，一个温柔可爱的 QQ 群聊 AI 助手，回答简洁友好，偶尔使用颜文字。",
        "mlai_extra_params": {},
    }
    for slot in range(1, 6)
}

# 条目（键）固定的集合配置：渔具/鱼饵商品表、稀有度权重、级别区间、
# 图鉴奖励档位——键不可新增/删除/改名，只能改各自属性；update_config
# 强制保留内置默认键集，面板同侧禁用增删改键入口。
_FIXED_ENTRY_FIELDS: dict[str, dict] = {
    "fish_shop_rods": _FISH_RODS_DEFAULT,
    "fish_shop_lines": _FISH_LINES_DEFAULT,
    "fish_shop_baits": _FISH_BAITS_DEFAULT,
    "fish_shop_items": _FISH_SHOP_ITEMS_DEFAULT,
    "fish_egg_items": _FISH_EGG_ITEMS_DEFAULT,
    "fish_potion_items": _FISH_POTION_ITEMS_DEFAULT,
    "fish_rarity_weights": _FISH_RARITY_WEIGHTS_DEFAULT,
    "fish_grade_ranges": _FISH_GRADE_RANGES_DEFAULT,
    "fish_collection_rewards": _FISH_COLLECTION_REWARDS_DEFAULT,
    "mlai_presets": _MLAI_PRESETS_DEFAULT,
}

# 钓鱼商店商品与彩蛋/药水目录名称不可通过热更新修改（只能改属性与
# 价格）；条目本身的增删由 _FIXED_ENTRY_FIELDS 一并锁定
# 幸运宝珠为唯一单件装备（存 owned 标记而非条目 id），改名不影响已有数据，故不锁定
_SHOP_NAME_LOCKED_FIELDS = (
    "fish_shop_rods", "fish_shop_lines", "fish_shop_baits",
    "fish_shop_items", "fish_egg_items", "fish_potion_items",
)

# 商店条目内嵌权重块的固定键集：鱼竿级别概率（D/C/B/A/S/X）与鱼饵
# 稀有度概率（5 档稀有度）——键即级别/稀有度名，不可增删改名，只能改
# 权重数值；update_config 强制保留键集，面板同侧禁用增删改键入口。
# 内层字典的值仅为异常数据补回时的兜底权重（0）。
_FIXED_NESTED_KEYS: dict[str, dict[str, dict[str, float]]] = {
    "fish_shop_rods": {
        "grade_weights": {grade: 0.0 for grade in _FISH_GRADE_RANGES_DEFAULT},
    },
    "fish_shop_baits": {
        "rarity_weights": {rarity: 0.0
                           for rarity in _FISH_RARITY_WEIGHTS_DEFAULT},
    },
}

# 图鉴奖励的奖励键下拉选项：预留键（中文标签）+ 商店商品 id（标签带
# 类别前缀与商品名，如「鱼饵·秘制鱼饵」）；面板「奖励」块内以下拉选择
_FISH_REWARD_RESERVED_CHOICES: list[tuple[str, str]] = [
    ("gold", "金币"),
    ("luckygold", "幸运币"),
    ("kirastone", "宝石"),
    ("orb", "幸运宝珠"),
    ("pump", "氧气泵"),
    ("fish_limit", "每日钓鱼次数上限"),
    ("su_code", "SU激活码获取权限"),
]

# 计价货币下拉：条目内 currency 属性（商店表/彩蛋目录）与标量字段
# （幸运宝珠/氧气泵）共用同一组选项
_SHOP_CURRENCY_ATTR_FIELDS = (
    "fish_shop_rods", "fish_shop_lines", "fish_shop_baits",
    "fish_shop_items", "fish_egg_items",
)
_SCALAR_CURRENCY_FIELDS = ("fish_orb_currency", "fish_pump_currency")

# 条目内渲染为多行文本框的属性（长文本类，如预设槽位的人格设定）
_MULTILINE_ATTR_FIELDS: dict[str, tuple[str, ...]] = {
    "mlai_presets": ("mlai_system_prompt",),
}


def _currency_choices() -> list[dict[str, str]]:
    """计价货币下拉选项 [{value, label}]"""
    return [
        {"value": key, "label": label} for key, label in FISH_CURRENCIES.items()
    ]


def _mlai_preset_choices() -> list[dict[str, str]]:
    """mlai 生效预设下拉选项：1-5，标签带槽位名（未命名显示占位）。"""
    table = config.mlai_presets if isinstance(config.mlai_presets, dict) else {}
    choices = []
    for slot in ("1", "2", "3", "4", "5"):
        entry = table.get(slot)
        name = str(entry.get("name", "") or "").strip() if isinstance(entry, dict) else ""
        choices.append({"value": slot, "label": f"{slot} · {name or '未命名'}"})
    return choices


def _fish_reward_key_choices() -> list[dict[str, str]]:
    """奖励键下拉选项 [{value, label}]：预留键 + 当前商店商品"""
    choices = [
        {"value": value, "label": label}
        for value, label in _FISH_REWARD_RESERVED_CHOICES
    ]

    def _add(table, prefix: str) -> None:
        if not isinstance(table, dict):
            return
        for item_id, entry in table.items():
            name = entry.get("name") if isinstance(entry, dict) else None
            choices.append({
                "value": str(item_id),
                "label": f"{prefix}·{name or item_id}",
            })

    _add(config.fish_shop_baits, "鱼饵")
    _add(config.fish_shop_rods, "鱼竿")
    _add(config.fish_shop_lines, "鱼线")
    return choices

class KoinoribotConfig(BaseModel):
    """Koinoribot 全局配置（superusers 除外，见 passwd.py）"""

    # ================== 群管理 ==================
    join_request_auto_approve: bool = False       # 入群申请自动审批开关
    join_request_keywords: list = ["abc", "def"]  # 入群验证内容含任一关键词则自动放行
    join_request_bots: list = []                  # 允许自动审批的 bot QQ 号列表（空列表不审批）
    join_request_bot_qq: dict = {}                # 官Bot appid→QQ号 绑定（官Bot self_id 是 appid）
    reply_quote: bool = False                     # 被动回复自动引用触发消息（OneBot/官Bot 双协议）
    at_sender: bool = True                        # 回复时 @ 触发者并换行（仅 OneBot；官Bot 平台暂不支持）

    # ================== 官Bot AppID ==================
    qqbot_appid: str = ""                                              # 官方Bot AppID，用于通过 openid 获取用户昵称和头像
    qqbot_openid_api: str = "https://oiapi.net/api/Openid"            # OpenID 查询 API 地址

    # ================== 漂流瓶配置 ==================
    throw_cool_time: int = 5                # 扔漂流瓶冷却时长
    salvage_cool_time: int = 5              # 捡漂流瓶冷却时长
    comment_cool_time: int = 5              # 评论漂流瓶冷却时长
    bottle_price: int = 100                 # 漂流瓶的价格（金币）
    comment_price: int = 50                 # 评论漂流瓶需要的金币
    bottle_craft_gold: int = 10000          # 合成 1 个漂流瓶需要的金币
    bottle_salvage_gold: int = 1000         # 捞 1 次漂流瓶需要的金币

    # ================== 新版钓鱼（fish） ==================
    # 稀有度基准权重（键为稀有度名，固定不可增删）；仅当某鱼饵未配置 rarity_weights 时回落使用
    fish_rarity_weights: dict = _FISH_RARITY_WEIGHTS_DEFAULT
    # 品种表 {品种id: {name, rarity, base_price, base_length_cm, air_force_rate, art}}，
    # base_length_cm 为基准长度（D 级最小长度），base_price 为基准售价，
    # air_force_rate 为该品种的基础空军概率
    fish_species: dict = _FISH_SPECIES_DEFAULT
    # 鱼竿商品表 {id: {name, price, desc, durability, repair_price, grade_weights}}，
    # grade_weights 为级别概率 {D/C/B/A/S/X}；durability 为 null 表示无耐久
    fish_shop_rods: dict = _FISH_RODS_DEFAULT
    fish_shop_lines: dict = _FISH_LINES_DEFAULT
    # 鱼饵商品表 {id: {name, price, desc, unlimited, effect}}，unlimited 为
    # true 表示无限使用；其余为一次性用品
    fish_shop_baits: dict = _FISH_BAITS_DEFAULT
    # 道具商品表 {id: {name, price, currency, desc}}（钓鱼商店·道具栏，
    # 键为道具 id，与钓鱼背包 fish_props 同一命名空间）
    fish_shop_items: dict = _FISH_SHOP_ITEMS_DEFAULT
    # 幸运宝珠（唯一单件装备：只能购买一次，购买后自动装备、无耐久、不可卸下）
    fish_orb_name: str = "幸运宝珠"
    fish_orb_price: int = 100000
    fish_orb_currency: str = "gold"
    fish_orb_desc: str = "内里跃动着幸运微光，攒满便会应验"
    # 幸运宝珠：幸运暴击时额外增加的幸运值（百分点，与鱼线幸运同单位）
    fish_orb_lucky_bonus: float = 20
    # 幸运宝珠升级基础价格（幸运币）：升到 n+1 级花费 = 基础价 × n（升级前等级）
    fish_orb_upgrade_base_price: int = 100
    # 幸运宝珠：最高等级 / 1 级能量上限 / 每升 1 级能量上限递减
    fish_orb_max_level: int = 5
    fish_orb_energy_cap_base: int = 50
    fish_orb_cap_reduce_per_level: int = 10
    # 幸运暴击只出该稀有度及以上、该级别及以上的鱼
    fish_orb_lucky_min_rarity: str = "史诗"
    fish_orb_lucky_min_grade: str = "A"
    # 水族箱：槽位固定 10（上限 20，4 列 × 5 行卡片），不可配置；
    # 每日固定成长厘米数（按时间戳连续计算）
    fish_aquarium_daily_growth_cm: float = 15
    # 氧气泵（唯一单件：购买后被动生效，水族箱成长速率提升）
    fish_pump_name: str = "氧气泵"
    fish_pump_price: int = 200
    fish_pump_currency: str = "luckygold"
    fish_pump_desc: str = "咕嘟咕嘟冒着气泡，鱼儿长得更快了"
    # 氧气泵成长速率加成（百分点，100 = +100% 即翻倍）
    fish_pump_growth_bonus: float = 100
    # 水族箱槽位扩展基准价格：第 n 个槽位价格 = 基准价 × 2^(n-初始槽位)
    fish_slot_base_price: int = 5000
    # 水族箱成长上限系数：可成长至 = 钓获时长度 × 该系数
    fish_growth_cap_factor: float = 1.5
    # 单次钓鱼冷却（秒）
    fish_cast_cd: int = 60
    # 售价指数：售价 = 基准售价 × 指数^(出售时长度 / 基准长度)
    fish_sell_price_exponent: float = 1.8
    # 放生幸运币分档：[[售价下限, 幸运币], ...]，售价低于首档下限则不可放生
    fish_lucky_tiers: list = [[10000, 1], [50000, 2], [100000, 5], [1000000, 10]]
    # 级别长度区间系数 {级别: [lo, hi]}：长度 ∈ [基准×lo, 基准×hi)
    # （键为级别名 D/C/B/A/S/X，固定不可增删）
    fish_grade_ranges: dict = _FISH_GRADE_RANGES_DEFAULT
    # 每日最大钓鱼次数（等级 0 的 SU 不受限制；宠物技能/幸运转盘可加当日上限）
    fish_limit_count: int = 10
    # 自动出售阈值：开启自动出售后，成长到最大（可成长至）售价仍低于该值的鱼钓上即卖
    fish_auto_sell_threshold: int = 10000
    # 图鉴奖励目录 {"稀有度:级别档": {"奖励": {奖励键: 数量}}}，共 30 档
    # （档位键固定不可增删改）；奖励键为预留键（gold/luckygold/kirastone/
    # orb/pump/fish_limit/su_code）或钓鱼商店商品 id；详见
    # _FISH_COLLECTION_REWARDS_DEFAULT 注释
    fish_collection_rewards: dict = _FISH_COLLECTION_REWARDS_DEFAULT
    # 彩蛋概率（百分点）：每次钓鱼先判定是否命中彩蛋，命中改抽彩蛋单品
    # （不判定空军）；幸运暴击必然走鱼类分支
    fish_egg_rate: float = 10
    # 彩蛋单品目录 {id: {name, weight, desc, art}}，5 件固定
    # 不可增删改；weight 为单品抽取权重（各自可调）
    fish_egg_items: dict = _FISH_EGG_ITEMS_DEFAULT
    # 药水目录 {id: {name, desc}}，固定 3 种不可增删改；由彩蛋「药水包」
    # 随机掉落，装备后生效（同时只能装备一种，用完自动卸下）
    fish_potion_items: dict = _FISH_POTION_ITEMS_DEFAULT

    # ================== 经济系统 ==================
    min_rest: int = 1000                    # 转账后最少剩余金币
    dibao: int = 3000                       # 低保金额
    gold_max: int = 9999999999              # 金币上限
    kirastone_max: int = 999999             # 宝石上限
    transfer_fee: float = 0.1               # 转账手续费比率
    stone_fee: float = 0.05                 # 退还宝石手续费比率
    return_item_fee: float = 0.5            # 退还宠物用品手续费比率
    init_gold: int = 3000                   # 新用户初始金币
    init_luckygold: int = 1                 # 新用户初始幸运币
    init_starstone: int = 100               # 新用户初始星星
    init_kirastone: int = 5                 # 新用户初始宝石（羽毛石）

    # ================== 货币API ==================
    money_api_key: str = ""                             # 货币 API 密钥（留空则启动时自动生成强随机值并写回）
    money_api_allow_hosts: list = ["127.0.0.1", "::1"]  # 允许调用货币 API 的客户端 IP 白名单

    # ================== 股票配置 ==================
    maxtype: int = 4                        # 股票持有种类上限
    maxcount: int = 500                     # 每种股票持有数量上限

    # ================== AI画图配置 ==================
    gpt_image_api_key: str = ""                                        # GPT-Image-2 API Key
    gpt_image_api_base_url: str = "https://api.example.com/v1"
    gpt_image_model: str = "gpt-image-2"
    gpt_image_response_format: str = "url"                             # 图片返回格式：url 或 base64/b64_json
    draw_cost: int = 200000                                            # 画图消耗金币
    daily_limit: int = 3                                               # 每日画图次数限制
    ai_draw_enable: bool = True                                        # 是否启用AI画图功能
    ai_draw_size: str = "auto"                                         # 普通画图尺寸
    shaojo_image_size: str = "800x1200"                                # 今日人设图尺寸
    aidraw_quality: str = "medium"                                     # 普通画图、修图、人设图质量
    aidraw_high_quality: str = "high"                                  # 高质量画图、修图、人设图质量
    enable_gold_aidraw: bool = True                                    # 是否允许消耗金币画图；False 时仅允许使用免费次数

    # ================== AI对话（mlai） ==================
    mlai_enable: bool = True                     # AI 对话总开关
    # 触发与屏蔽（触发词实时读取，热更新即时生效）
    mlai_block_words: list = ["梦灵酱"]          # 屏蔽词（消息含任一词即不响应）
    mlai_timeout: int = 60                       # 单次请求超时（秒）
    mlai_user_cd: int = 20                       # 单人对话冷却（秒）
    # 聊天计价（星星消耗）
    mlai_starstone_enable: bool = True           # 聊天计价开关（开=每次对话消耗星星）
    mlai_starstone_chars: int = 1000             # 每 1 星星计价的字符数（向上取整）
    # 供应商接入参数内嵌于 mlai_presets 槽位，此字段选择生效槽位
    mlai_preset_id: str = "1"                      # 生效的供应商预设槽位（1-5，面板下拉）
    mlai_presets: dict = _MLAI_PRESETS_DEFAULT     # 供应商预设（固定 5 槽位，含全部对话参数）

    # ================== AI对话·记忆（mlai） ==================
    mlai_private_enable: bool = True       # 私聊总开关（关=仅等级0 SU可私聊，其余不触发不记录）
    mlai_group_enable: bool = False        # 群聊域开关（关=群聊完全不响应不记录；开=白名单群可对话）
    mlai_group_bind: dict = {}             # 官Bot group_openid → 真实群号 绑定（兼群聊白名单，纯手动维护）
    mlai_active_window: int = 20           # 活跃记忆窗口条数（按更新时间取最近 N 条，0=不注入仅检索）
    mlai_archive_per_scope: int = 5        # 归档记忆每域关键词检索上限
    mlai_memory_ops_limit: int = 5         # 每次对话允许的记忆操作数上限
    mlai_memory_max_len: int = 200         # 单条记忆内容长度上限
    mlai_temp_limit: int = 20              # 临时上下文保留条数（每条消息一行，旁听/对话/bot回复共用）

    # ================== 基础配置 ==================
    # bot 名字（全局，AI 对话触发词；由 AI 对话模块实时读取，改名即时生效）
    bot_name: str = "梦灵"
    # 黑名单用户
    blackusers: list = []
    global_msg_cd: int = 1                      # 全局每用户消息冷却（秒，0=关闭）
    # 周期缓存清理间隔（秒）：周期 malloc_trim 把 glibc 囤住的空闲页还给
    # 系统，缓解高流量后 RSS 棘轮上涨；0 = 关闭
    cache_clean_interval: int = 0
    # 合并消息始终渲染为图片：true 时无论平台（含 OneBot 群聊）都不发送
    # 真实合并转发，一律渲染为图片发送（渲染失败自动回落合并转发）
    forward_as_image: bool = False

    # ================== 其他配置·内容审核 ==================
    nsfw_review_enable: bool = False      # 用户提交内容外接审核总开关
    nsfw_review_api_key: str = ""         # 审核 API Key
    nsfw_review_api_base_url: str = ""    # 审核接口地址（OpenAI 兼容）
    nsfw_review_model: str = ""           # 审核模型名
    nsfw_review_extra_params: dict = {}   # 审核接口自定义请求参数（JSON）

    # 公网白名单模式
    public_bot: bool = False                 # 是否启用云bot模式
    permit_bot: list = []                   # 自己的bot账号列表（如果上面一项为True，则此项必填）
    ip_address: str = ""                    # 本机公网ip地址（公网白名单模式下必填）


# 面板分组（顺序即展示顺序）
_FIELD_SECTIONS: dict[str, list[str]] = {
    "基础配置": ["bot_name", "blackusers", "global_msg_cd",
                "cache_clean_interval", "forward_as_image", "nsfw_review_enable",
                "nsfw_review_api_key", "nsfw_review_api_base_url",
                "nsfw_review_model", "nsfw_review_extra_params"],
    "群管理": [
        "join_request_auto_approve", "join_request_keywords", "join_request_bots",
        "join_request_bot_qq", "reply_quote", "at_sender",
    ],
    "官Bot AppID": ["qqbot_appid", "qqbot_openid_api"],
    "漂流瓶": [
        "throw_cool_time", "salvage_cool_time", "comment_cool_time",
        "bottle_price", "comment_price", "bottle_craft_gold",
        "bottle_salvage_gold",
    ],
    "钓鱼·基础": [
        "fish_cast_cd", "fish_limit_count",
        "fish_sell_price_exponent", "fish_lucky_tiers",
        "fish_auto_sell_threshold",
    ],
    "钓鱼·图鉴奖励": [
        "fish_collection_rewards",
    ],
    "钓鱼·品种与稀有度": [
        "fish_rarity_weights", "fish_species",
        "fish_grade_ranges",
    ],
    "钓鱼·商店": [
        "fish_shop_rods", "fish_shop_lines", "fish_shop_baits",
        "fish_shop_items",
    ],
    "钓鱼·彩蛋": [
        "fish_egg_rate", "fish_egg_items",
    ],
    "钓鱼·药水": [
        "fish_potion_items",
    ],
    "钓鱼·幸运宝珠": [
        "fish_orb_name", "fish_orb_price", "fish_orb_currency",
        "fish_orb_desc",
        "fish_orb_lucky_bonus", "fish_orb_upgrade_base_price",
        "fish_orb_max_level", "fish_orb_energy_cap_base",
        "fish_orb_cap_reduce_per_level",
        "fish_orb_lucky_min_rarity", "fish_orb_lucky_min_grade",
    ],
    "钓鱼·水族箱": [
        "fish_aquarium_daily_growth_cm",
        "fish_pump_name", "fish_pump_price", "fish_pump_currency",
        "fish_pump_desc",
        "fish_pump_growth_bonus",
        "fish_slot_base_price", "fish_growth_cap_factor",
    ],
    "经济系统": [
        "min_rest", "dibao", "gold_max", "kirastone_max", "transfer_fee", "stone_fee",
        "return_item_fee", "init_gold", "init_luckygold", "init_starstone",
        "init_kirastone",
    ],
    "货币API": ["money_api_key", "money_api_allow_hosts"],
    "股票配置": ["maxtype", "maxcount"],
    "AI画图配置": [
        "gpt_image_api_key", "gpt_image_api_base_url",
        "gpt_image_model", "gpt_image_response_format", "draw_cost",
        "daily_limit", "ai_draw_enable", "ai_draw_size", "shaojo_image_size",
        "aidraw_quality", "aidraw_high_quality", "enable_gold_aidraw",
    ],
    "AI对话": [
        "mlai_enable", "mlai_block_words",
        "mlai_timeout", "mlai_user_cd",
        "mlai_starstone_enable", "mlai_starstone_chars",
        "mlai_preset_id", "mlai_presets",
        "mlai_private_enable", "mlai_group_enable", "mlai_group_bind",
        "mlai_active_window",
        "mlai_archive_per_scope", "mlai_memory_ops_limit",
        "mlai_memory_max_len", "mlai_temp_limit",
    ],
    "公网白名单模式": ["public_bot", "permit_bot", "ip_address"],
}

# 面板字段中文说明（必须覆盖全部配置项，test_config_system 有完整性校验）
_FIELD_DESCRIPTIONS: dict[str, str] = {
    # 群管理
    "join_request_auto_approve": "开启后自动审批入群申请：验证内容命中关键词且bot为群管理员时自动放行",
    "join_request_keywords": "入群自动放行关键词（每项一行）；验证消息或问答答案含任一关键词即命中",
    "join_request_bots": "允许自动审批的 bot 标识，QQ 号或官Bot appid 均可（每项一行）；空列表时所有 bot 均不自动审批",
    "join_request_bot_qq": "官Bot 的 appid→QQ号 绑定；appid 在 bot 连接时自动登记（值为空），只需补填对应 QQ 号；白名单填 appid 时可不填 QQ 号",
    "reply_quote": "双协议（OneBot/官Bot）被动回复自动以引用形式回复触发消息；OneBot 用原生 reply 消息段、官Bot 用 message_reference；官Bot 被动回复 5 分钟窗口内有效",
    "at_sender": "回复消息时 @ 触发用户并换行（@用户+换行+正文）；OneBot 用 at 消息段，官Bot 群聊用官方 <qqbot-at-user/> 标签并以 markdown 载体发送，官Bot 富媒体消息无法携带 @ 时自动降级为引用回复（不受引用开关影响）；由本开关统一管理，插件内不再单独控制",
    # 官Bot AppID
    "qqbot_appid": "官方 QQBot 的 AppID，用于换算用户昵称/头像",
    "qqbot_openid_api": "OpenID 查询昵称的第三方 API 地址（官方昵称字段的降级路径）",
    # 漂流瓶
    "throw_cool_time": "扔漂流瓶冷却时长（秒）",
    "salvage_cool_time": "捡漂流瓶冷却时长（秒）",
    "comment_cool_time": "评论漂流瓶冷却时长（秒）",
    "bottle_price": "购买漂流瓶的价格（金币）",
    "comment_price": "评论漂流瓶需要的金币",
    "bottle_craft_gold": "合成 1 个漂流瓶需要的金币",
    "bottle_salvage_gold": "捞 1 次漂流瓶需要的金币",
    # 新版钓鱼
    "fish_rarity_weights": "稀有度基准权重（键为固定 5 档稀有度名：普通/稀有/史诗/传说/神话，不可增删改）；仅当某鱼饵未配置 rarity_weights 时回落使用",
    "fish_species": "鱼品种表：{品种id: {name, rarity, base_price, base_length_cm, air_force_rate, art}}；base_length_cm 为基准长度（D 级最小长度，决定各级长度区间），base_price 为基准售价，air_force_rate 为基础空军概率（最终空军率 = 基础空军率 - 总幸运值，下限 0）；可在此新增/删除品种",
    "fish_shop_rods": "鱼竿商品表：{id: {name, price, currency, desc, durability, repair_price, grade_weights}}；grade_weights 为级别概率 {D/C/B/A/S/X}（只影响级别，级别越高鱼越长）；currency 为购买计价货币（gold 金币/kirastone 宝石/luckygold 幸运币；修理固定金币）；durability 填 null 表示无耐久（如初始鱼竿）；条目与名称均不可新增/删除/修改，只能改各属性与价格",
    "fish_shop_lines": "鱼线商品表：{id: {name, price, currency, desc, durability, repair_price, luck}}；luck 为幸运值（百分点），用于抵消品种基础空军率，只影响空军概率；currency 为购买计价货币（gold 金币/kirastone 宝石/luckygold 幸运币；修理固定金币）；条目与名称均不可新增/删除/修改，只能改各属性与价格",
    "fish_shop_baits": "鱼饵商品表：{id: {name, price, currency, desc, unlimited, rarity_weights}}；rarity_weights 为稀有度概率 {普通/稀有/史诗/传说/神话}（只影响稀有度）；currency 为计价货币（gold 金币/kirastone 宝石/luckygold 幸运币）；unlimited 填 true 表示无限使用（初始鱼饵）；其余为一次性用品，每次钓鱼消耗 1 个；条目与名称均不可新增/删除/修改，只能改各属性与价格",
    "fish_shop_items": "道具商品表：{id: {name, price, currency, desc}}（钓鱼商店·道具栏）；键为道具 id（如钓鱼扫荡券），购买后存入钓鱼背包；currency 为计价货币（gold 金币/kirastone 宝石/luckygold 幸运币）；条目与名称均不可新增/删除/修改，只能改各属性与价格",
    "fish_grade_ranges": "各级别的长度区间系数 {级别: [lo, hi]}：长度 = 基准长度 × 区间随机（D 从 ×1.0 起，各级首尾相接；修改会影响所有鱼的取长）；键为固定 6 级 D/C/B/A/S/X，不可增删改",
    "fish_sell_price_exponent": "售价指数：售价 = 基准售价 × 指数^(出售时长度 / 基准长度)；越大则养大后越值钱",
    "fish_lucky_tiers": "放生幸运币分档：[[售价下限, 幸运币], ...]；售价低于首档下限的鱼不可放生（默认 1万→1、5万→2、10万→5、100万→10）",
    "fish_cast_cd": "单次钓鱼冷却（秒）",
    "fish_orb_name": "幸运宝珠名称（唯一单件装备，只能购买一次，购买后自动装备、无耐久、不可卸下）",
    "fish_orb_price": "幸运宝珠售价（金币）",
    "fish_orb_desc": "幸运宝珠商店描述文案",
    "fish_orb_lucky_bonus": "幸运宝珠：幸运暴击时额外增加的幸运值（百分点，直接抵扣空军概率；20 = 抵消 20% 空军率）",
    "fish_orb_upgrade_base_price": "幸运宝珠升级基础价格（幸运币）：升到 n+1 级花费 = 基础价 × 升级前等级",
    "fish_orb_max_level": "幸运宝珠最高等级",
    "fish_orb_energy_cap_base": "幸运宝珠 1 级时的能量上限（每成功钓鱼一次 +1 能量，能量满后下一次触发幸运暴击）",
    "fish_orb_cap_reduce_per_level": "幸运宝珠每升 1 级能量上限的递减量（越高等级越容易攒满，暴击越频繁）",
    "fish_orb_lucky_min_rarity": "幸运暴击最低稀有度（只出该稀有度及以上的鱼）",
    "fish_orb_lucky_min_grade": "幸运暴击最低级别（只出该级别及以上的鱼）",
    "fish_aquarium_daily_growth_cm": "水族箱每日成长量：每天固定成长该厘米数（按时间戳连续计算，受成长上限系数封顶）",
    "fish_pump_name": "氧气泵名称（唯一单件，购买后被动生效）",
    "fish_pump_price": "氧气泵售价（幸运币，限购一次）",
    "fish_pump_desc": "氧气泵描述（商店展示用）",
    "fish_pump_growth_bonus": "氧气泵成长速率加成：百分点（100 = +100% 即成长翻倍）",
    "fish_slot_base_price": "水族箱槽位扩展基准价格：第 n 个槽位的价格 = 基准价 × 2^(n-10)（槽位固定 10 起、上限 20，不可配置）",
    "fish_growth_cap_factor": "水族箱成长上限系数：可成长至 = 钓获时长度 × 该系数（默认 1.5；小于 1 按 1 处理）",
    "fish_limit_count": "每人每日最大钓鱼次数（等级 0 的 SU 不受限制；宠物技能/幸运转盘可加当日上限）",
    "fish_auto_sell_threshold": "自动处理阈值：开启自动处理后，成长到最大（可成长至）售价仍低于该金币数的鱼自动卖出，其余自动放入水族箱（水族箱满时交由玩家处理）",
    "fish_collection_rewards": "图鉴奖励目录：{稀有度:级别档: {奖励: {奖励键: 数量}}}；档位键固定（每稀有度 D/C/B/A/S/X 共 6 档、5 稀有度共 30 档，不可增删改），进入条目后在「奖励」块内增删改行即可调整奖励内容，单档可含多项；奖励键可用预留键 gold 金币/luckygold 幸运币/kirastone 宝石/orb 幸运宝珠/pump 氧气泵/fish_limit 每日钓鱼次数上限/su_code SU激活码获取权限，或钓鱼商店商品 id（鱼饵/鱼竿/鱼线，类型自动推导）；非消耗品（orb/pump/su_code/鱼竿/鱼线）数量强制 1；奖励发放时若已拥有该非消耗品按售价折算为对应货币；fish_limit 与 su_code 在领取后永久生效；档位解锁条件：该稀有度全部品种收集且全部品种级别 ≥ 档位",
    "fish_egg_rate": "彩蛋概率（百分点）：每次钓鱼先判定是否命中彩蛋，命中则按各彩蛋单品权重抽取单品（彩蛋不判定空军）；未命中按原稀有度权重抽取鱼类；幸运暴击必然走鱼类分支；0 = 关闭彩蛋",
    "fish_egg_items": "彩蛋单品目录：{id: {name, weight, desc, art}}；weight 为单品抽取权重（各自可调，按比率归一）；鱼饵包/无人认领的钱包/药水包三件固定钓到自动开启（无法储存，奖励直接发放），钓鱼扫荡券收入钓鱼背包；条目与名称固定不可增删改；art 为图片资源（URL 或相对路径，留空发文本）",
    "fish_potion_items": "药水目录：{id: {name, desc}}；固定 3 种（大鱼/稀有度/回溯），由彩蛋「药水包」随机掉落，发送 装备药水 <名称> 装备（同时只能装备一种，用完自动卸下）；大鱼/稀有度药水非彩蛋钓获各消耗1个提升对应抽取落点，回溯药水空军时消耗并重判一次；条目与名称固定不可增删改",
    "fish_orb_currency": "幸运宝珠计价货币（gold 金币/kirastone 宝石/luckygold 幸运币）",
    "fish_pump_currency": "氧气泵计价货币（gold 金币/kirastone 宝石/luckygold 幸运币）",
    # 经济系统
    "min_rest": "转账后账户最少需保留的金币",
    "dibao": "低保金额，贫穷时可以领取",
    "gold_max": "金币持有上限",
    "kirastone_max": "宝石持有上限",
    "transfer_fee": "转账手续费比率（0.1 = 10%）",
    "stone_fee": "退还宝石的手续费比率",
    "return_item_fee": "退还宠物用品的手续费比率",
    "init_gold": "新用户首次入库时赠送的初始金币",
    "init_luckygold": "新用户首次入库时赠送的初始幸运币",
    "init_starstone": "新用户首次入库时赠送的初始星星",
    "init_kirastone": "新用户首次入库时赠送的初始宝石（羽毛石）",
    # 货币API
    "money_api_key": "货币 API（/money）调用密钥；留空时启动自动生成强随机密钥并写回此项，可点「显示敏感值」后查看；修改后立即生效（无需重启）",
    "money_api_allow_hosts": "允许访问货币 API 的客户端 IP 白名单（每项一行）；默认仅本机（127.0.0.1 与 ::1）；填 * 允许全部来源（仅靠密钥保护，供跨机调用）；留空则拒绝所有访问",
    # 股票配置
    "maxtype": "每人最多持有的股票种类数",
    "maxcount": "每种股票的最大持有数量",
    # AI画图配置
    "gpt_image_api_key": "画图接口 API Key，注意保密",
    "gpt_image_api_base_url": "画图接口地址（OpenAI 兼容格式）",
    "gpt_image_model": "画图模型名",
    "gpt_image_response_format": "图片返回格式：url 或 b64_json",
    "draw_cost": "画图/修图单次消耗金币",
    "daily_limit": "每人每日画图/修图次数上限",
    "ai_draw_enable": "是否启用 AI 画图/修图功能",
    "ai_draw_size": "普通画图尺寸（auto / 1024x1024 / 1024x1536 等）",
    "shaojo_image_size": "今日人设图尺寸（如 800x1200）",
    "aidraw_quality": "普通画图/修图/人设图质量（low / medium / high）",
    "aidraw_high_quality": "高质量画图/修图/人设图质量（low / medium / high）",
    "enable_gold_aidraw": "是否允许花金币画图；关闭后只能用免费次数",
    # AI对话
    "mlai_enable": "AI 对话总开关；关闭后触发词不响应（全局生效，与预设无关）",
    "mlai_block_words": "屏蔽词列表（每项一行）：消息中包含任一词则完全不响应（静默，不回复任何内容）；默认含 梦灵酱（另一 bot 的触发词，更改触发词后仍建议保留）",
    "mlai_timeout": "单次对话请求的超时时间（秒），全局生效，与预设无关",
    "mlai_user_cd": "同一用户两次对话的最小间隔（秒），防止刷接口；全局生效，与预设无关",
    "mlai_starstone_enable": "聊天计价开关：开启（默认）后每次触发对话消耗星星；触发时先组装第 2 轮完整输入计价（文本字符数 + 每张图片固定 100 字），星星不足则静默不响应；审核未通过或对话未正常完成时已扣星星自动退回",
    "mlai_starstone_chars": "聊天计价费率：每消耗 1 星星对应的字符数（默认 1000 字/星，按向上取整计费；每张图片固定按 100 字计入）",
    "mlai_preset_id": "当前生效的供应商预设槽位（下拉选择 1-5，选择即生效）；供应商接入参数都在下方 mlai_presets 槽位内配置",
    "mlai_presets": "供应商预设（固定 5 个槽位，键不可增删）：每槽位内嵌供应商接入参数 {name, mlai_api_key, mlai_api_base_url, mlai_model, mlai_system_prompt（多行）, mlai_extra_params}；由 mlai_preset_id 选择生效槽位，热更新即时生效；自定义参数 true/false 与数字自动转类型，model/messages/stream/tools 不可覆盖；槽位内含密钥，注意保密",
    # AI对话·记忆
    "mlai_private_enable": "私聊总开关：开启（默认）时任何人可私聊对话；关闭后仅等级 0 的超级用户可私聊（供维护测试），其他人的私聊不触发对话、不记录上下文（静默）",
    "mlai_group_enable": "群聊域总开关：开启后白名单（mlai_group_bind）中的群聊可对话（注入群聊记忆与群聊临时上下文，官Bot 群需绑定 group_openid→真实群号）；关闭后所有群聊完全不响应（静默，不触发不旁听不记录），私聊不受影响",
    "mlai_group_bind": "群聊白名单（官Bot 群绑定表 {group_openid: 真实群号}，纯手动维护，不再自动登记）：不在列表中的群聊无论群聊域开关一律不响应（静默）。官Bot 群以 openid 为键、填真实群号后放行（同时供群聊记忆定位）；OneBot 群按绑定值（真实群号）匹配，需放行某 OneBot 群时填 {该群群号: 该群群号} 即可",
    "mlai_active_window": "活跃记忆窗口：全局/群聊/用户记忆各自按更新时间取最近 N 条直接注入上下文，更早的为归档记忆（仅可被关键词检索）；0 = 不注入活跃记忆，仅靠检索",
    "mlai_archive_per_scope": "归档记忆检索上限：第 1 轮提取的关键词在全局/群聊/用户归档记忆中检索时，每域最多注入条数",
    "mlai_memory_ops_limit": "单次对话允许 AI 执行的记忆操作（新增/修改/删除）数量上限，超出部分忽略",
    "mlai_memory_max_len": "单条记忆内容的最大长度（字），超长的记忆操作会被拒绝",
    "mlai_temp_limit": "群聊/私聊临时上下文各自滚动保留的最近条数（每条消息一行：旁听消息、触发对话的消息与 bot 回复共用额度，默认 20 条）",
    # 基础配置
    "bot_name": "bot 名字（全局）：AI 对话触发词，消息中任意位置出现该词即触发对话（对话内容 = 去掉该词后的整条消息）；mlai / AI对话 前缀命令始终可用；修改即时生效；开启/关闭 AI 聊天指令的别名也基于此名",
    "blackusers": "黑名单用户（统一 UID 列表）",
    "global_msg_cd": "全局限频：同一用户两条消息的最小间隔（秒），间隔内的消息静默忽略；SU 豁免；0 = 关闭",
    "cache_clean_interval": "缓存清理周期（单位为秒），可优化内存占用，为0时关闭",
    "forward_as_image": "合并消息始终渲染为图片：开启后无论是否官Bot，合并消息（帮助/商店/图鉴等长文本）都不发送合并转发，一律渲染为图片发送（渲染失败自动回落合并转发）",
    "nsfw_review_enable": "用户提交内容外接审核总开关：开启后用户可上传/自定义的内容（请叫我、上传头像、上传签到图片、宠物改名、扔/评论漂流瓶、我以后想吃）先经 LLM 审核，含色情、暴力、叛国、粗口、人身攻击等不适宜内容，或结合内容用途明显反常的则拒绝提交；关闭（默认）或接口未配置完整时不审核",
    "nsfw_review_api_key": "内容审核 API Key（OpenAI 兼容接口），注意保密",
    "nsfw_review_api_base_url": "内容审核接口地址（OpenAI 兼容，如 https://api.xx.com/v1）",
    "nsfw_review_model": "内容审核模型名；需支持图片输入（多模态）才能审图片类内容，纯文本模型仅能审文字",
    "nsfw_review_extra_params": "审核接口自定义请求参数（键值对）：true/false 与数字自动转类型；model/messages/stream 不可覆盖；可用于 thinking、reasoning_effort 等思考等级或服务商特有参数",
    # 公网白名单模式
    "public_bot": "是否启用云 bot（公网白名单）模式",
    "permit_bot": "豁免名单（云 bot 模式必填）：本名单内的bot将跳过白名单检查；QQ 号/官Bot appid 精确匹配，官Bot 还可经 join_request_bot_qq 绑定的 QQ 号命中",
    "ip_address": "本机公网 IP（云 bot 模式必填；面板地址与官Bot图文markdown的临时图床也基于它；未配置时官Bot富媒体回复降级为引用）",
}

# 面板展示时需要打码的字段（仅 API Key 类；显式清单，避免 join_request_keywords
# 等含 "key" 字样的普通字段被误伤）
_SECRET_FIELDS = {"gpt_image_api_key", "money_api_key",
                  "nsfw_review_api_key"}


def is_secret_field(name: str) -> bool:
    return name in _SECRET_FIELDS


def mask_value(value: Any) -> str:
    text = str(value)
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


# 全局配置实例：身份终身不变，store 加载/修改都原地更新它
config: KoinoribotConfig = KoinoribotConfig()

_db_path: Optional[str] = None


def get_config() -> KoinoribotConfig:
    """获取配置实例（兼容旧 API）"""
    return config


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path or str(DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()


def _apply_in_place(model: BaseModel, new_model: BaseModel) -> None:
    """把 new_model 的字段原地写入 model，保持实例身份不变。"""
    object.__setattr__(model, "__dict__", dict(new_model.__dict__))
    object.__setattr__(
        model, "__pydantic_fields_set__", set(new_model.__pydantic_fields_set__)
    )


def _load_from_db() -> int:
    """把 config 表全部行加载进内存（内部使用）。返回加载的字段数。"""
    with _connect() as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT key, value FROM config").fetchall()

    data: dict[str, Any] = {}
    for row in rows:
        try:
            data[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[config_store] 配置项 {row['key']} 的值损坏，忽略")
    if data:
        _apply_in_place(config, KoinoribotConfig.model_validate(data))
    return len(rows)


def _write_rows(updates: dict[str, Any]) -> None:
    now = time.time()
    with _connect() as conn:
        _ensure_table(conn)
        conn.executemany(
            """
            INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            [
                (key, json.dumps(value, ensure_ascii=False), now)
                for key, value in updates.items()
            ],
        )
        conn.commit()


def update_config(updates: dict[str, Any]) -> list[str]:
    """修改配置：pydantic 校验 → 写库 → 原地更新内存。

    仅出现在 updates 中的字段会被修改。返回被修改的字段名列表。
    校验失败抛出 ValueError（带 pydantic 错误信息），不落库不动内存。
    """
    unknown = [k for k in updates if k not in KoinoribotConfig.model_fields]
    if unknown:
        raise ValueError(f"未知配置项: {', '.join(unknown)}")

    # 钓鱼商店：已有商品的名称不可通过热更新修改或删除（只能改属性与价格）
    for key in _SHOP_NAME_LOCKED_FIELDS:
        if key not in updates or not isinstance(updates[key], dict):
            continue
        old_table = getattr(config, key) or {}
        new_table = updates[key]
        for item_id, old_entry in old_table.items():
            old_name = old_entry.get("name") if isinstance(old_entry, dict) else None
            entry = new_table.get(item_id)
            if not old_name or not isinstance(entry, dict):
                continue
            if entry.get("name") != old_name:
                new_table[item_id] = {**entry, "name": old_name}

    # 条目固定的集合配置（渔具/鱼饵商品表、稀有度权重、级别区间、图鉴
    # 奖励档位）：丢弃新增键、补回被删键（保留现行属性值），键不可增删改
    for key, default_table in _FIXED_ENTRY_FIELDS.items():
        if key not in updates or not isinstance(updates[key], dict):
            continue
        old_table = getattr(config, key) or {}
        new_table = {
            entry_key: value for entry_key, value in updates[key].items()
            if entry_key in default_table
        }
        for fixed_key, default_value in default_table.items():
            if fixed_key not in new_table:
                # 深拷贝补回，避免后续处理原地改动现行配置对象
                new_table[fixed_key] = copy.deepcopy(
                    old_table.get(fixed_key, default_value)
                )
        updates[key] = new_table

    # 商店条目内嵌权重块的键固定（鱼竿级别概率 D/C/B/A/S/X、鱼饵稀有度
    # 概率 5 档）：丢弃新增/改名键、补回被删键（保留现行权重值），
    # 只能改权重数值
    for key, nested_specs in _FIXED_NESTED_KEYS.items():
        if key not in updates or not isinstance(updates[key], dict):
            continue
        old_table = getattr(config, key) or {}
        for entry_key, entry in updates[key].items():
            if not isinstance(entry, dict):
                continue
            old_entry = old_table.get(entry_key)
            for nested_key, fixed_keys in nested_specs.items():
                sub = entry.get(nested_key)
                if not isinstance(sub, dict):
                    continue
                old_sub = (
                    old_entry.get(nested_key)
                    if isinstance(old_entry, dict) else None
                )
                old_sub = old_sub if isinstance(old_sub, dict) else {}
                merged = {
                    sub_key: value for sub_key, value in sub.items()
                    if sub_key in fixed_keys
                }
                for fixed_key, fallback in fixed_keys.items():
                    if fixed_key not in merged:
                        merged[fixed_key] = old_sub.get(fixed_key, fallback)
                entry[nested_key] = merged

    # 图鉴奖励：数量统一转 int（客户端可能提交数字字符串），非法值丢弃
    if "fish_collection_rewards" in updates and isinstance(
        updates["fish_collection_rewards"], dict
    ):
        for tier, value in updates["fish_collection_rewards"].items():
            if not isinstance(value, dict):
                continue
            block = value.get("奖励")
            if not isinstance(block, dict):
                continue
            cleaned: dict = {}
            for reward_key, count in block.items():
                try:
                    cleaned[reward_key] = int(count)
                except (TypeError, ValueError):
                    continue
            value["奖励"] = cleaned

    try:
        new_model = KoinoribotConfig.model_validate(
            {**config.model_dump(), **updates}
        )
    except ValidationError as e:
        raise ValueError(str(e)) from e

    current = config.model_dump()
    changed_keys = [key for key in updates if current[key] != updates[key]]
    if not changed_keys:
        return []
    _write_rows({key: updates[key] for key in changed_keys})
    _apply_in_place(config, new_model)
    return changed_keys


def dump_for_panel(reveal: bool = False) -> dict[str, Any]:
    """生成面板数据：分区字段列表（含类型、中文说明与打码值）。

    dict 字段额外携带 entry_count（条目数）与 collection（是否为集合
    配置：嵌套 dict 或条目超过 12 项，面板为其提供二级编辑子页）；
    条目固定的字段带 fixed_entries，内嵌权重块固定的字段带
    locked_nested_keys，奖励类字段带 nested_choices（下拉选项）。
    """
    dump = config.model_dump()
    sections: dict[str, list[dict[str, Any]]] = {}
    for section, fields in _FIELD_SECTIONS.items():
        items = []
        for name in fields:
            annotation = KoinoribotConfig.model_fields[name].annotation
            value = dump[name]
            secret = is_secret_field(name)
            item = {
                "key": name,
                "desc": _FIELD_DESCRIPTIONS.get(name, ""),
                "type": getattr(annotation, "__name__", str(annotation)),
                "value": value if (not secret or reveal) else mask_value(value),
                "masked": secret and not reveal,
                # 条目固定（键不可增删改）：面板据此禁用键编辑与增删入口
                "fixed_entries": name in _FIXED_ENTRY_FIELDS,
            }
            if isinstance(value, dict):
                item["entry_count"] = len(value)
                item["collection"] = (
                    any(isinstance(v, dict) for v in value.values())
                    or len(value) > 12
                )
                locked_nested = _FIXED_NESTED_KEYS.get(name)
                if locked_nested:
                    item["locked_nested_keys"] = sorted(locked_nested)
                multiline = _MULTILINE_ATTR_FIELDS.get(name)
                if multiline:
                    item["multiline_attrs"] = sorted(multiline)
                if name == "fish_collection_rewards":
                    item["nested_choices"] = {
                        "奖励": _fish_reward_key_choices(),
                    }
                # 条目内的 currency 属性渲染为下拉（计价货币三选一）
                if name in _SHOP_CURRENCY_ATTR_FIELDS:
                    item["attr_choices"] = {"currency": _currency_choices()}
            # 标量字段下拉（幸运宝珠/氧气泵计价货币三选一；mlai 生效预设）
            if name in _SCALAR_CURRENCY_FIELDS:
                item["choices"] = _currency_choices()
            elif name == "mlai_preset_id":
                item["choices"] = _mlai_preset_choices()
            items.append(item)
        sections[section] = items
    return {"sections": sections}


# ================== 旧版文件迁移 ==================


def _import_legacy_config(path: Path):
    spec = importlib.util.spec_from_file_location("koinori_config_legacy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载旧配置文件: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "config", None)


def _migrate_legacy_file(path: Path) -> list[int]:
    """把旧版 koinori_config.py 的值迁移入 config 表，成功后删除文件。

    返回旧文件中的 superusers 列表（供 passwd.py 播种），失败时保留文件。
    """
    try:
        legacy_config = _import_legacy_config(path)
        if legacy_config is None:
            raise ImportError("旧配置文件中没有 config 实例")
        legacy_data = legacy_config.model_dump()
    except Exception as e:
        logger.error(f"[config_store] 旧配置文件加载失败，保留原文件: {e}")
        return []

    superusers = legacy_data.pop("superusers", [])
    known = {
        key: value
        for key, value in legacy_data.items()
        if key in KoinoribotConfig.model_fields
    }
    try:
        _write_rows(known)
    except Exception as e:
        logger.error(f"[config_store] 旧配置迁移入库失败，保留原文件: {e}")
        return []

    try:
        path.unlink()
    except OSError as e:
        logger.warning(f"[config_store] 旧配置文件删除失败（值已入库）: {e}")

    logger.info(
        f"[config_store] 已迁移旧配置 koinori_config.py：{len(known)} 项入库，原文件已删除"
    )
    return [int(uid) for uid in superusers]


# 字段改名映射：旧键的值在 stale 清理前迁入新键（避免被当废弃行删除）
_KEY_RENAMES: dict[str, str] = {
    "qqbot_reply_quote": "reply_quote",
    "bottle_craft_starstone": "bottle_craft_gold",
    "bottle_salvage_starstone": "bottle_salvage_gold",
    "mlai_bot_name": "bot_name",
}


def _migrate_fish_collection_rewards() -> None:
    """旧版图鉴奖励扁平格式（{档位: {奖励键: 数量}}）升级为面板二级子页
    可编辑的嵌套格式（{档位: {奖励: {...}}}）；已是嵌套格式时不动作。"""
    raw = config.fish_collection_rewards
    if not isinstance(raw, dict):
        return
    wrapped: dict = {}
    changed = False
    for key, value in raw.items():
        if isinstance(value, dict) and "奖励" not in value:
            wrapped[key] = {"奖励": dict(value)}
            changed = True
        else:
            wrapped[key] = value
    if not changed:
        return
    try:
        update_config({"fish_collection_rewards": wrapped})
        logger.info("[config_store] 图鉴奖励配置已迁移为嵌套格式（奖励内容可在面板编辑）")
    except ValueError as e:
        logger.error(f"[config_store] 图鉴奖励配置格式迁移失败: {e}")


def _migrate_shop_currency() -> None:
    """为存量商店表条目补上 currency 计价货币键（新增字段前入库的数据
    没有该键，补齐后配置面板才能直接编辑；非法值交由读取层回落）。"""
    defaults = {
        "fish_shop_rods": "luckygold",
        "fish_shop_lines": "luckygold",
        "fish_shop_baits": "gold",
        "fish_shop_items": "gold",
    }
    for field, currency in defaults.items():
        table = getattr(config, field, None)
        if not isinstance(table, dict):
            continue
        changed = False
        filled = {}
        for item_id, entry in table.items():
            if isinstance(entry, dict) and "currency" not in entry:
                filled[item_id] = {**entry, "currency": currency}
                changed = True
            else:
                filled[item_id] = entry
        if changed:
            try:
                update_config({field: filled})
                logger.info(f"[config_store] {field} 已补齐 currency 计价货币键")
            except ValueError as e:
                logger.error(f"[config_store] {field} 补齐 currency 失败: {e}")


def norm_bool(value, default: bool = False) -> bool:
    """把配置面板可能存成字符串的布尔值规范成 bool。

    面板二级子页早期版本把条目内的布尔按文本框序列化成 "false"/"true"
    字符串入库，而 bool("false") 恒为 True，读取层必须显式解析字符串。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return default


def _migrate_bait_unlimited() -> None:
    """把面板早期存成字符串的鱼饵 unlimited 值规范回真布尔。

    旧版面板二级子页按文本框序列化嵌套布尔（unlimited: "false"），读取层
    bool("false") 恒为真，导致所有商店鱼饵被当成无限使用（不消耗、不可
    购买、面板显示 ∞）；此处一次性修正存量数据，其余属性原样保留。
    """
    table = getattr(config, "fish_shop_baits", None)
    if not isinstance(table, dict):
        return
    changed = False
    filled = {}
    for item_id, entry in table.items():
        if isinstance(entry, dict) and isinstance(entry.get("unlimited"), str):
            filled[item_id] = {
                **entry,
                "unlimited": norm_bool(entry["unlimited"]),
            }
            changed = True
        else:
            filled[item_id] = entry
    if changed:
        try:
            update_config({"fish_shop_baits": filled})
            logger.info("[config_store] 鱼饵 unlimited 已规范为布尔值（修复无限鱼饵问题）")
        except ValueError as e:
            logger.error(f"[config_store] 鱼饵 unlimited 规范化失败: {e}")


# 药水效果实装前的占位描述（仅用于一次性迁移识别，勿再改动）
_POTION_LEGACY_DESCS = {
    "potion_bigfish": "似乎更容易钓上大鱼（效果待实装）",
    "potion_rarity": "散发稀有鱼儿喜欢的气味（效果待实装）",
    "potion_rollback": "时间在其中缓缓倒流（效果待实装）",
}


def _migrate_potion_descs() -> None:
    """药水效果实装：把存量库中仍是占位描述的药水 desc 更新为定稿文案。

    只覆盖仍等于旧占位值的条目（用户在面板改过的自定义描述保留）。
    """
    table = getattr(config, "fish_potion_items", None)
    if not isinstance(table, dict):
        return
    changed = False
    filled = {}
    for item_id, entry in table.items():
        if (isinstance(entry, dict)
                and entry.get("desc") == _POTION_LEGACY_DESCS.get(item_id)
                and item_id in _FISH_POTION_ITEMS_DEFAULT):
            filled[item_id] = {
                **entry,
                "desc": _FISH_POTION_ITEMS_DEFAULT[item_id]["desc"],
            }
            changed = True
        else:
            filled[item_id] = entry
    if changed:
        try:
            update_config({"fish_potion_items": filled})
            logger.info("[config_store] 药水描述已更新为效果实装文案")
        except ValueError as e:
            logger.error(f"[config_store] 药水描述迁移失败: {e}")


def _migrate_sweep_ticket_name() -> None:
    """错字修正：存量库中钓鱼扫荡券的旧名「钓鱼扫荡劵」/旧描述更新为
    「券」（只覆盖仍等于旧默认值的条目，用户自定义值保留）。"""
    fixes = {
        "fish_egg_items": {
            "name": ("钓鱼扫荡劵", "钓鱼扫荡券"),
            "desc": ("一键钓鱼扫荡劵", "一键钓鱼扫荡券"),
        },
        "fish_shop_items": {
            "name": ("钓鱼扫荡劵", "钓鱼扫荡券"),
        },
    }
    for field, pairs in fixes.items():
        table = getattr(config, field, None)
        if not isinstance(table, dict):
            continue
        new_table = {}
        changed = False
        for item_id, entry in table.items():
            if isinstance(entry, dict):
                new_entry = dict(entry)
                for attr, (old, new) in pairs.items():
                    if entry.get(attr) == old:
                        new_entry[attr] = new
                        changed = True
                new_table[item_id] = new_entry
            else:
                new_table[item_id] = entry
        if changed:
            try:
                update_config({field: new_table})
                logger.info(f"[config_store] 钓鱼扫荡券错字修正：{field}")
            except ValueError as e:
                logger.error(f"[config_store] 钓鱼扫荡券错字迁移失败: {e}")


def _rename_legacy_keys(conn) -> None:
    valid_keys = KoinoribotConfig.model_fields
    for old, new in _KEY_RENAMES.items():
        if new not in valid_keys:
            continue
        row = conn.execute(
            "SELECT value, updated_at FROM config WHERE key = ?", (old,)
        ).fetchone()
        if row is None:
            continue
        exists = conn.execute(
            "SELECT 1 FROM config WHERE key = ?", (new,)
        ).fetchone()
        if exists is None:
            conn.execute(
                "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                (new, row["value"], row["updated_at"]),
            )
            logger.info(f"[config_store] 配置项改名迁移：{old} → {new}（值已保留）")
        conn.execute("DELETE FROM config WHERE key = ?", (old,))
        conn.commit()


# mlai 旧版顶层字段并入 mlai_presets 后的存量迁移键（搬入槽位 1）。
# mlai_timeout / mlai_user_cd 已回归顶层 schema 字段（其顶层行是现行
# 配置而非遗留数据），不在此列——若列入会与 knobs 上移迁移互相乒乓
# （每次启动先搬进槽位 1、再被搬回顶层并重写 presets）
_MLAI_LEGACY_FIELDS = (
    "mlai_api_key", "mlai_api_base_url", "mlai_model", "mlai_system_prompt",
    "mlai_max_context", "mlai_extra_params",
)


def _migrate_mlai_legacy(conn) -> None:
    """把已入库的 mlai 旧顶层配置搬进预设槽位 1（仅覆盖仍为默认值的项）。

    需先于 stale 清理执行，避免旧值被当废弃行删除。
    """
    legacy: dict[str, Any] = {}
    for field in _MLAI_LEGACY_FIELDS:
        row = conn.execute(
            "SELECT value FROM config WHERE key = ?", (field,)
        ).fetchone()
        if row is None:
            continue
        try:
            legacy[field] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue
    if not legacy:
        return

    row = conn.execute(
        "SELECT value FROM config WHERE key = 'mlai_presets'"
    ).fetchone()
    try:
        presets = (
            json.loads(row["value"]) if row else copy.deepcopy(_MLAI_PRESETS_DEFAULT)
        )
    except (json.JSONDecodeError, TypeError):
        presets = copy.deepcopy(_MLAI_PRESETS_DEFAULT)
    slot = presets.get("1") if isinstance(presets, dict) else None
    if not isinstance(slot, dict):
        slot = copy.deepcopy(_MLAI_PRESETS_DEFAULT["1"])

    defaults = _MLAI_PRESETS_DEFAULT["1"]
    moved = [
        field for field, value in legacy.items()
        if slot.get(field, defaults.get(field)) == defaults.get(field)
    ]
    for field in moved:
        slot[field] = legacy[field]
    if not moved:
        return

    presets["1"] = slot
    conn.execute(
        "INSERT INTO config (key, value, updated_at) VALUES ('mlai_presets', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (json.dumps(presets, ensure_ascii=False), time.time()),
    )
    conn.commit()
    logger.info(f"[config_store] mlai 旧顶层配置已并入预设槽位 1：{', '.join(moved)}")


# mlai_timeout / mlai_user_cd 上移为顶层字段后，槽位内自定义值的搬迁默认值
_MLAI_PRESET_KNOBS = (("mlai_timeout", 60), ("mlai_user_cd", 20))


def _migrate_mlai_preset_knobs(conn) -> None:
    """把预设槽位内自定义的 mlai_timeout / mlai_user_cd 搬出为顶层字段。

    仅当顶层尚无该行时搬（优先槽位 1，其次任一非默认槽位），搬完从
    各槽位删除旧键；全部为默认值时只清理旧键。
    """
    row = conn.execute(
        "SELECT value FROM config WHERE key = 'mlai_presets'"
    ).fetchone()
    if row is None:
        return
    try:
        presets = json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(presets, dict):
        return

    moved: list[str] = []
    for field, default in _MLAI_PRESET_KNOBS:
        exists = conn.execute(
            "SELECT 1 FROM config WHERE key = ?", (field,)
        ).fetchone()
        if exists is not None:
            continue
        value = default
        for slot_id in ("1", "2", "3", "4", "5"):
            entry = presets.get(slot_id)
            if not isinstance(entry, dict):
                continue
            raw = entry.get(field)
            if raw is not None and str(raw) != str(default):
                value = raw
                break
        conn.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            (field, json.dumps(value), time.time()),
        )
        moved.append(field)

    cleaned = False
    for entry in presets.values():
        if not isinstance(entry, dict):
            continue
        if any(field in entry for field, _ in _MLAI_PRESET_KNOBS):
            for field, _ in _MLAI_PRESET_KNOBS:
                entry.pop(field, None)
            cleaned = True
    if not moved and not cleaned:
        return

    conn.execute(
        "INSERT INTO config (key, value, updated_at) VALUES ('mlai_presets', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (json.dumps(presets, ensure_ascii=False), time.time()),
    )
    conn.commit()
    if moved:
        logger.info(f"[config_store] mlai 预设槽位内的 {', '.join(moved)} 已上移为顶层配置")


def init_config_store(
    db_path: Optional[str] = None,
    legacy_config_path: Optional[Path] = None,
) -> list[int]:
    """初始化 config 表并一次性加载到内存。

    需要在子插件加载之前调用（子插件在导入期读取配置值）。
    返回迁移出的 superusers 列表（无迁移时为空列表）。
    """
    global _db_path
    _db_path = db_path or str(DEFAULT_DB_PATH)
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        _ensure_table(conn)
        # 字段改名迁移（需先于 stale 清理，保留旧键的值）
        _rename_legacy_keys(conn)
        # mlai 旧顶层字段并入预设槽位 1（需先于 stale 清理）
        _migrate_mlai_legacy(conn)
        # mlai_timeout / mlai_user_cd 槽位值上移为顶层（需先于 stale 清理）
        _migrate_mlai_preset_knobs(conn)
        # 清理已从 schema 删除的字段残留行
        valid_keys = tuple(KoinoribotConfig.model_fields)
        placeholders = ",".join("?" * len(valid_keys))
        stale = conn.execute(
            f"DELETE FROM config WHERE key NOT IN ({placeholders})", valid_keys
        ).rowcount
        if stale:
            conn.commit()
            logger.info(f"[config_store] 已清理 {stale} 条废弃配置项")
        row_count = conn.execute("SELECT COUNT(*) AS c FROM config").fetchone()["c"]

    migrated_superusers: list[int] = []
    legacy_path = (
        legacy_config_path
        if legacy_config_path is not None
        else _PLUGIN_DIR / LEGACY_CONFIG_FILENAME
    )
    if row_count == 0 and legacy_path.exists():
        migrated_superusers = _migrate_legacy_file(legacy_path)

    loaded = _load_from_db()
    # 图鉴奖励旧扁平格式 → 面板可编辑嵌套格式（升级后首次启动执行）
    _migrate_fish_collection_rewards()
    # 商店表存量条目补齐 currency 计价货币键（面板可见可编辑）
    _migrate_shop_currency()
    # 鱼饵 unlimited 字符串布尔规范回真布尔（修复无限鱼饵问题）
    _migrate_bait_unlimited()
    # 药水效果实装：占位描述更新为定稿文案（自定义描述保留）
    _migrate_potion_descs()
    # 钓鱼扫荡券错字修正（自定义名称/描述保留）
    _migrate_sweep_ticket_name()
    logger.info(f"[config_store] 配置加载完成：{loaded} 项（数据库 {_db_path}）")
    return migrated_superusers


# ================== passwd.py 管理 ==================

# 模板缺失时的兜底内容（正常情况使用 passwd.py.template）
_PASSWD_FALLBACK_TEMPLATE = '''\
# Koinoribot 敏感配置（请勿提交到仓库）
# 配置面板（挂载于驱动端口 /config）的访问密码
PANEL_PASSWORD = "{password}"

# 超级用户列表（等级 0 = 最高权限，可使用“冰祈配置”指令）
# 值为统一 UID（与数据库 superusers 表的 uid 同一命名空间）
SUPERUSERS = {superusers}
'''


def ensure_passwd_file(
    superusers: Optional[list[int]] = None,
    plugin_dir: Optional[Path] = None,
) -> tuple[Path, bool]:
    """确保 passwd.py 存在；不存在时按模板生成（面板密码为随机值）。

    superusers 来自旧配置迁移，会替换模板中的 SUPERUSERS 默认值；
    已存在的 passwd.py 不会被修改。返回 (路径, 是否新建)。
    """
    base = plugin_dir or _PLUGIN_DIR
    path = base / PASSWD_FILENAME
    if path.exists():
        return path, False

    template_path = base / PASSWD_TEMPLATE_FILENAME
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
    else:
        logger.warning(
            f"[config_store] 缺少 {PASSWD_TEMPLATE_FILENAME}，使用内置兜底模板"
        )
        content = _PASSWD_FALLBACK_TEMPLATE.format(
            password="__RANDOM__", superusers="[]"
        )

    if superusers:
        content = _replace_superusers_line(content, superusers)
    content = content.replace("__RANDOM__", secrets.token_urlsafe(16))
    path.write_text(content, encoding="utf-8")
    logger.warning(
        f"[config_store] 已生成 {PASSWD_FILENAME}（面板密码为随机值，请打开文件查看并妥善保管）"
    )
    return path, True


def _replace_superusers_line(content: str, superusers: list[int]) -> str:
    lines = []
    for line in content.splitlines():
        if line.strip().startswith("SUPERUSERS"):
            lines.append(f"SUPERUSERS = {superusers!r}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def get_panel_password() -> str:
    """读取面板密码；passwd.py 缺失时返回空串（面板将拒绝一切登录）。"""
    try:
        from . import passwd
    except ImportError:
        return ""
    return str(getattr(passwd, "PANEL_PASSWORD", ""))


def get_passwd_superusers() -> list[int]:
    """读取等级 0 超级用户列表（passwd.SUPERUSERS）。"""
    try:
        from . import passwd
        raw = getattr(passwd, "SUPERUSERS", [])
    except ImportError:
        return []
    result = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            logger.warning(f"[config_store] passwd.SUPERUSERS 含非法项: {item!r}")
    return result
