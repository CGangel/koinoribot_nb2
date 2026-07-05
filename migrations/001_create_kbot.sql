-- 用户 UID 映射表
CREATE TABLE IF NOT EXISTS user_uid_mapping (
    uid INTEGER PRIMARY KEY,
    onebot_id TEXT UNIQUE,
    qqbot_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);
-- UID 序列表（用于生成自增 UID）
CREATE TABLE IF NOT EXISTS uid_sequence (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_uid INTEGER NOT NULL DEFAULT 10001
);
-- 初始化 UID 序列表(确保序列表有初始值)
-- 鉴于上面设置的默认值为 10001，所以这里插入 10001
-- 如果需要修改默认值，需要在以后的迁移文件中修改
INSERT OR IGNORE INTO uid_sequence (id, next_uid) VALUES (1, 10001);

-- 用户资产表
CREATE TABLE IF NOT EXISTS user_money (
    uid INTEGER PRIMARY KEY,
    gold INTEGER NOT NULL DEFAULT 3000,
    luckygold INTEGER NOT NULL DEFAULT 0,
    starstone INTEGER NOT NULL DEFAULT 12500,
    kirastone INTEGER NOT NULL DEFAULT 0,
    last_login INTEGER NOT NULL DEFAULT 0,
    rp INTEGER NOT NULL DEFAULT 0,
    logindays INTEGER NOT NULL DEFAULT 0,
    exgacha INTEGER NOT NULL DEFAULT 0,
    goodluck INTEGER NOT NULL DEFAULT 0,
    badluck INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 昵称表
CREATE TABLE IF NOT EXISTS call_me_please_users (
    uid INTEGER PRIMARY KEY,
    nickname TEXT NOT NULL
);

-- AI 绘画频率表
CREATE TABLE IF NOT EXISTS ai_draw_usage (
    uid INTEGER PRIMARY KEY,
    date TEXT NOT NULL DEFAULT '',
    count INTEGER NOT NULL DEFAULT 0,
    free_draw_count INTEGER NOT NULL DEFAULT 0
);

-- 股票数据表
CREATE TABLE IF NOT EXISTS stock_data (
    stock_name TEXT PRIMARY KEY,
    initial_price REAL NOT NULL,
    history_data TEXT NOT NULL,
    events_data TEXT NOT NULL,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 资产组合表
CREATE TABLE IF NOT EXISTS user_portfolios (
    uid INTEGER PRIMARY KEY,
    portfolio_data TEXT NOT NULL,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 豪赌记录表
CREATE TABLE IF NOT EXISTS gamble_record (
    uid INTEGER PRIMARY KEY,
    reduce_record INTEGER NOT NULL DEFAULT 0,
    increase_record INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 每日赌博限制表
CREATE TABLE IF NOT EXISTS daily_gamble_limits (
    uid INTEGER PRIMARY KEY,
    last_gamble_date TEXT NOT NULL,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 每日转盘次数限制表
CREATE TABLE IF NOT EXISTS daily_turntable_limits (
    uid INTEGER PRIMARY KEY,
    last_date TEXT NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 每日低保领取记录表
CREATE TABLE IF NOT EXISTS daily_prek (
    uid INTEGER PRIMARY KEY,
    last_prek_date TEXT NOT NULL,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 用户宠物表
CREATE TABLE IF NOT EXISTS user_pets (
    uid INTEGER PRIMARY KEY,
    pet_data TEXT NOT NULL,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 用户物品表
CREATE TABLE IF NOT EXISTS user_items (
    uid INTEGER PRIMARY KEY,
    items_data TEXT NOT NULL,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 用户飞升表
CREATE TABLE IF NOT EXISTS user_feisheng (
    uid INTEGER PRIMARY KEY,
    pet_ascension_progress INTEGER DEFAULT 0,
    ascension_progress INTEGER DEFAULT 0,
    is_pet_ascended INTEGER DEFAULT 0,
    is_ascended INTEGER DEFAULT 0,
    realm_level INTEGER DEFAULT 0,
    daily_cultivation_count INTEGER DEFAULT 0,
    cultivation_date TEXT DEFAULT '',
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 用户飞升物品表
CREATE TABLE IF NOT EXISTS user_feisheng_items (
    uid INTEGER,
    item_name TEXT,
    count INTEGER DEFAULT 0,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (uid, item_name),
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 钓鱼表
CREATE TABLE IF NOT EXISTS fishing (
    uid INTEGER PRIMARY KEY,
    fish_data TEXT NOT NULL,
    statis_data TEXT NOT NULL,
    rod_data TEXT NOT NULL,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 钓鱼限制表
CREATE TABLE IF NOT EXISTS fish_limit (
    uid INTEGER PRIMARY KEY,
    date_str TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    limit_count INTEGER NOT NULL DEFAULT 0,
    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
);
-- 漂流瓶表
CREATE TABLE IF NOT EXISTS bottles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER NOT NULL,
    content TEXT NOT NULL,
    pick_count INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    created_time INTEGER NOT NULL
);
-- 漂流瓶评论表
CREATE TABLE IF NOT EXISTS bottle_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bottle_id INTEGER NOT NULL,
    uid INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_time INTEGER NOT NULL,
    FOREIGN KEY (bottle_id) REFERENCES bottles(id) ON DELETE CASCADE
);
-- 确保 bottles 的 AUTOINCREMENT 从 10001 开始
INSERT INTO sqlite_sequence (name, seq)
SELECT 'bottles', 10000
WHERE (SELECT COUNT(*) FROM bottles) = 0
AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'bottles');

-- 公共白名单表
CREATE TABLE IF NOT EXISTS public_whitelist (
    owner_qq TEXT PRIMARY KEY,
    bot_qq TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);
-- 白名单审核表
CREATE TABLE IF NOT EXISTS whitelist_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_qq TEXT NOT NULL,
    bot_qq TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    compliance_commit TEXT NOT NULL DEFAULT '',
    tech_commit TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_qq TEXT DEFAULT NULL,
    review_comment TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT DEFAULT NULL
);