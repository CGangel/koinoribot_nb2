import math

# 扭蛋配置
GACHA_COST = 10
GACHA_CONSOLE_PRIZE = 50

GACHA_REWARDS = {
    "普通": {"小猫咪": 50, "小狗": 50},
    "稀有": {"魔法兔": 50, "小狐狸": 50},
    "史诗": {"精灵宝宝": 50, "熊猫宝宝": 50},
    "传说": {"蠢萝莉": 100},
}

# 成长阶段所需成长值
GROWTH_STAGE_1 = 250
GROWTH_STAGE_2 = 500
GROWTH_STAGE_3 = math.inf

# 基础宠物类型
BASE_PETS = {
    "小猫咪": {
        "max_hunger": 100, "max_energy": 100, "max_happiness": 100,
        "growth_rate": 1.2, "rarity": "普通", "stage": 0, "growth_required": 100
    },
    "小狗": {
        "max_hunger": 100, "max_energy": 90, "max_happiness": 110,
        "growth_rate": 1.2, "rarity": "普通", "stage": 0, "growth_required": 100
    },
    "魔法兔": {
        "max_hunger": 80, "max_energy": 120, "max_happiness": 150,
        "growth_rate": 1.3, "rarity": "稀有", "stage": 0, "growth_required": 100
    },
    "蠢萝莉": {
        "max_hunger": 200, "max_energy": 200, "max_happiness": 200,
        "growth_rate": 1.5, "rarity": "传说", "stage": 0, "growth_required": 100
    },
    "小狐狸": {
        "max_hunger": 80, "max_energy": 140, "max_happiness": 130,
        "growth_rate": 1.3, "rarity": "稀有", "stage": 0, "growth_required": 100
    },
    "精灵宝宝": {
        "max_hunger": 100, "max_energy": 200, "max_happiness": 200,
        "growth_rate": 1.4, "rarity": "史诗", "stage": 0, "growth_required": 100
    },
    "熊猫宝宝": {
        "max_hunger": 200, "max_energy": 100, "max_happiness": 200,
        "growth_rate": 1.4, "rarity": "史诗", "stage": 0, "growth_required": 100
    },
}

# 进化路线
EVOLUTIONS = {
    # 幼年体 -> 成长体
    "小猫咪": {"成长体1": "灵猫", "成长体2": "玄猫", "成长体3": "小馋猫"},
    "小狗": {"成长体1": "金毛", "成长体2": "哈士奇", "成长体3": "柴犬"},
    "魔法兔": {"成长体1": "月兔", "成长体2": "玉兔", "成长体3": "魔兔"},
    "蠢萝莉": {"成长体1": "不那么蠢的萝莉", "成长体2": "嘴馋萝莉", "成长体3": "傲娇萝莉"},
    "小狐狸": {"成长体1": "藏狐", "成长体2": "赤狐", "成长体3": "月光灵狐"},
    "精灵宝宝": {"成长体1": "花精灵", "成长体2": "树精灵", "成长体3": "元素精灵"},
    "熊猫宝宝": {"成长体1": "暗夜修女", "成长体2": "纯白修女", "成长体3": "黑白圣女"},
    # 成长体 -> 成年体
    "灵猫": "猫耳女仆", "玄猫": "猫耳公主", "小馋猫": "猫耳萝莉",
    "金毛": "金发犬娘", "哈士奇": "极地雪狼", "柴犬": "神社守护",
    "月兔": "月宫仙子", "玉兔": "捣药仙兔", "魔兔": "魔法少女",
    "不那么蠢的萝莉": "灵歌少女", "嘴馋萝莉": "小萝莉", "傲娇萝莉": "大小姐",
    "藏狐": "高原守护", "赤狐": "火焰灵狐", "月光灵狐": "狐耳巫女",
    "花精灵": "百花圣女", "树精灵": "森林公主", "元素精灵": "元素神女",
    "暗夜修女": "黑天使", "纯白修女": "白天使", "黑白圣女": "双生神女",
}

# 宠物商店物品
PET_SHOP_ITEMS = {
    "普通料理": {"price": 5, "hunger": 40, "energy": 10, "happiness": 5, "growth": 0, "effect": "恢复宠物状态"},
    "高级料理": {"price": 15, "hunger": 120, "energy": 50, "happiness": 25, "growth": 2, "effect": "大幅恢复状态"},
    "豪华料理": {"price": 30, "hunger": 240, "energy": 120, "happiness": 90, "growth": 5, "effect": "超级恢复"},
    "玩具球": {"price": 5, "hunger": -5, "energy": -5, "happiness": 50, "growth": 0, "effect": "增加好感度"},
    "能量饮料": {"price": 5, "hunger": 0, "energy": 50, "happiness": 15, "growth": 0, "effect": "快速恢复精力"},
    "普通扭蛋": {"price": 10, "effect": "随机获得宠物", "type": "gacha"},
    "高级扭蛋": {"price": 100, "effect": "更高概率获得珍贵宠物", "type": "gacha"},
    "传说扭蛋": {"price": 1000, "effect": "必得传说宠物", "type": "gacha"},
    "奶油蛋糕": {"price": 200, "effect": "进化至成长体", "special": "evolve_1"},
    "豪华蛋糕": {"price": 500, "effect": "进化至成年体", "special": "evolve_2"},
    "时之泪": {"price": 100, "effect": "重置进化分支", "special": "reroll"},
    "最初的契约": {"price": 75, "effect": "寻回离家出走的宠物", "special": "retrieve"},
    "技能药水": {"price": 50, "effect": "学习新技能"},
    "遗忘药水": {"price": 10, "effect": "遗忘指定技能"},
    "誓约戒指": {"price": 5000, "effect": "你的宠物将对你『不离不弃』"},
}

GACHA_CONFIG = {
    "普通扭蛋": PET_SHOP_ITEMS["普通扭蛋"],
    "高级扭蛋": PET_SHOP_ITEMS["高级扭蛋"],
    "传说扭蛋": PET_SHOP_ITEMS["传说扭蛋"],
}

# 状态描述
STATUS_DESCRIPTIONS = {
    "hunger": {80: "吃得饱饱的", 50: "有点饿了", 30: "非常饥饿", 10: "饿得不行了"},
    "energy": {80: "精力充沛", 50: "有点累了", 30: "非常疲惫", 10: "精疲力尽"},
    "happiness": {80: "非常开心", 50: "心情一般", 30: "不太高兴", 10: "非常沮丧"},
}

# 宠物技能
PET_SKILLS = {
    "宝石爱好者": {"description": "捡回一些宝石"},
    "盼望长大": {"description": "获得10成长值"},
    "金币爱好者": {"description": "捡回一些金币"},
    "美食家": {"description": "捡回随机食物"},
    "自我管理": {"description": "恢复精力和好感"},
    "幸运星": {"description": "捡回幸运币"},
    "卖萌": {"description": "获得星星"},
    "捕鱼达人": {"description": "增加钓鱼次数"},
}
