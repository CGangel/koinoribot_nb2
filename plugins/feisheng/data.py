import sqlite3
import json
import asyncio
from typing import Optional, Dict

from ... import uid_manager

def _get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    # 复用 uid_manager 的数据库路径，确保在同一个库中以支持外键
    db_path = uid_manager.get_database_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 启用外键约束
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_feisheng_database():
    """初始化飞升数据库表"""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        # 飞升数据表
        cursor.execute('''
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
            )
        ''')
        
        # 检查 realm_level 列是否存在 (用于旧表迁移)
        cursor.execute("PRAGMA table_info(user_feisheng)")
        columns = [column[1] for column in cursor.fetchall()]
        if "realm_level" not in columns:
            cursor.execute("ALTER TABLE user_feisheng ADD COLUMN realm_level INTEGER DEFAULT 0")
        if "daily_cultivation_count" not in columns:
             cursor.execute("ALTER TABLE user_feisheng ADD COLUMN daily_cultivation_count INTEGER DEFAULT 0")
        if "cultivation_date" not in columns:
             cursor.execute("ALTER TABLE user_feisheng ADD COLUMN cultivation_date TEXT DEFAULT ''")
        
        conn.commit()
        conn.close()
    except Exception:
        # 如果uid_manager还没初始化db_path，可能会报错，忽略或记录日志
        pass

async def get_feisheng_data(uid: int) -> dict:
    """获取用户的飞升数据"""
    init_feisheng_database()
    
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM user_feisheng WHERE uid = ?', (uid,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        else:
            return {
                "uid": uid,
                "pet_ascension_progress": 0,
                "ascension_progress": 0,
                "is_pet_ascended": 0,
                "is_ascended": 0,
                "realm_level": 0,
                "daily_cultivation_count": 0,
                "cultivation_date": ""
            }
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)

async def update_feisheng_data(uid: int, data: dict):
    """更新用户的飞升数据（宠物飞升/突破/渡劫等整行场景）。

    每日修炼计数两列（daily_cultivation_count / cultivation_date）不在此
    写入：整行回写携带的是调用方读取时的快照，并发下会把
    consume_cultivation_count 的原子计数覆盖回旧值（跨天边界尤其）。
    这两列只允许 consume_cultivation_count / refund_cultivation_count
    变更；新行插入时取默认值（0 / ''）。
    """
    init_feisheng_database()

    def _update():
        conn = _get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO user_feisheng
            (uid, pet_ascension_progress, ascension_progress, is_pet_ascended, is_ascended, realm_level, daily_cultivation_count, cultivation_date)
            VALUES (?, ?, ?, ?, ?, ?, 0, '')
            ON CONFLICT(uid) DO UPDATE SET
            pet_ascension_progress = excluded.pet_ascension_progress,
            ascension_progress = excluded.ascension_progress,
            is_pet_ascended = excluded.is_pet_ascended,
            is_ascended = excluded.is_ascended,
            realm_level = excluded.realm_level,
            updated_time = CURRENT_TIMESTAMP
        ''', (
            uid,
            data.get("pet_ascension_progress", 0),
            data.get("ascension_progress", 0),
            int(data.get("is_pet_ascended", 0)),
            int(data.get("is_ascended", 0)),
            data.get("realm_level", 0)
        ))

        conn.commit()
        conn.close()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update)

async def add_cultivation_progress(uid: int, amount: int) -> int:
    """原子累加修炼进度（封顶 100），返回累加后的进度值。

    整行「读取→改内存→REPLACE 回写」在并发修炼下会互相覆盖（进度
    少加、计数回退）；修炼进度只经本函数、每日计数只经
    consume_cultivation_count / refund_cultivation_count 的单条 SQL 变更
    （update_feisheng_data 已不再写入计数两列）。
    """
    init_feisheng_database()

    def _add():
        conn = _get_connection()
        cursor = conn.cursor()
        capped = min(100, max(0, int(amount)))
        cursor.execute(
            'INSERT INTO user_feisheng (uid, ascension_progress) VALUES (?, ?)'
            ' ON CONFLICT(uid) DO UPDATE SET'
            ' ascension_progress = MIN(100, user_feisheng.ascension_progress'
            '   + excluded.ascension_progress),'
            ' updated_time = CURRENT_TIMESTAMP',
            (uid, capped),
        )
        progress = cursor.execute(
            'SELECT ascension_progress FROM user_feisheng WHERE uid = ?', (uid,)
        ).fetchone()['ascension_progress']
        conn.commit()
        conn.close()
        return int(progress)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _add)


async def refund_cultivation_count(uid: int) -> None:
    """修炼后续步骤失败时退还已消耗的当日次数（原子递减，当日下限 0）。"""
    from datetime import datetime
    init_feisheng_database()
    today = datetime.now().strftime("%Y-%m-%d")

    def _refund():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE user_feisheng SET'
            ' daily_cultivation_count = daily_cultivation_count - 1,'
            ' updated_time = CURRENT_TIMESTAMP'
            ' WHERE uid = ? AND cultivation_date = ?'
            ' AND daily_cultivation_count > 0',
            (uid, today),
        )
        conn.commit()
        conn.close()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _refund)


async def check_daily_cultivation_limit(uid: int, limit: int = 5) -> bool:
    """检查每日修炼限制（纯预检，只读不写）。返回 True 表示未超限。

    跨天重置由 consume_cultivation_count 的 UPSERT 原子完成，预检不再
    写库——旧的「读整行→重置→整行回写」会把过期快照写回，跨天边界
    与其他请求的原子更新互相覆盖。
    """
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    data = await get_feisheng_data(uid)
    last_date = data.get("cultivation_date", "")
    count = data.get("daily_cultivation_count", 0)

    if last_date != today:
        return True

    return count < limit


async def consume_cultivation_count(uid: int, limit: int = 5) -> tuple[bool, int]:
    """原子消耗一次当日修炼次数（跨天自动重置计数）。

    判定与计数在同一条 UPDATE / UPSERT 内完成（旧的「先查再写」在并发
    修炼下会双双通过、超出每日上限）。返回 (是否成功, 今日已修炼次数)；
    调用方应把返回的计数写回 fs_data 再整体落库，避免旧值覆盖。
    """
    from datetime import datetime
    init_feisheng_database()
    today = datetime.now().strftime("%Y-%m-%d")

    def _consume():
        conn = _get_connection()
        cursor = conn.cursor()

        # 同日且未达上限：原子 +1
        cursor.execute(
            'UPDATE user_feisheng SET'
            ' daily_cultivation_count = daily_cultivation_count + 1,'
            ' updated_time = CURRENT_TIMESTAMP'
            ' WHERE uid = ? AND cultivation_date = ?'
            ' AND daily_cultivation_count < ?',
            (uid, today, limit),
        )
        if cursor.rowcount == 1:
            count = cursor.execute(
                'SELECT daily_cultivation_count FROM user_feisheng WHERE uid = ?',
                (uid,),
            ).fetchone()['daily_cultivation_count']
            conn.commit()
            conn.close()
            return True, int(count)

        # 无记录或跨天：原子抢占当日首次（仅旧行非今日时才重置为 1）
        cursor.execute(
            'INSERT INTO user_feisheng (uid, daily_cultivation_count, cultivation_date)'
            ' VALUES (?, 1, ?)'
            ' ON CONFLICT(uid) DO UPDATE SET'
            ' daily_cultivation_count = 1,'
            ' cultivation_date = excluded.cultivation_date,'
            ' updated_time = CURRENT_TIMESTAMP'
            ' WHERE user_feisheng.cultivation_date != excluded.cultivation_date',
            (uid, today),
        )
        ok = cursor.rowcount == 1
        conn.commit()
        conn.close()
        return ok, 1 if ok else limit

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _consume)

async def increase_pet_ascension_progress(uid: int, amount: int) -> dict:
    """增加宠物飞升进度"""
    data = await get_feisheng_data(uid)
    
    # 如果已经飞升，直接返回
    if data["is_pet_ascended"]:
        return data
        
    current = data["pet_ascension_progress"]
    new_progress = current + amount
    
    if new_progress >= 100:
        data["pet_ascension_progress"] = 100
        data["is_pet_ascended"] = 1
    else:
        data["pet_ascension_progress"] = new_progress
        
    await update_feisheng_data(uid, data)
    return data

# ===== 物品系统 =====

def init_feisheng_items_table():
    """初始化飞升物品表"""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_feisheng_items (
                uid INTEGER,
                item_name TEXT,
                count INTEGER DEFAULT 0,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, item_name),
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')
        
        conn.commit()
        conn.close()
    except Exception:
        pass

async def get_user_feisheng_items(uid: int) -> Dict[str, int]:
    """获取用户所有飞升物品"""
    init_feisheng_items_table()
    
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT item_name, count FROM user_feisheng_items WHERE uid = ? AND count > 0', (uid,))
        rows = cursor.fetchall()
        conn.close()
        
        return {row["item_name"]: row["count"] for row in rows}
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)

async def add_feisheng_item(uid: int, item_name: str, count: int = 1):
    """添加飞升物品"""
    init_feisheng_items_table()
    
    def _update():
        conn = _get_connection()
        cursor = conn.cursor()
        
        # Check existing
        cursor.execute('SELECT count FROM user_feisheng_items WHERE uid = ? AND item_name = ?', (uid, item_name))
        row = cursor.fetchone()
        
        if row:
            new_count = row["count"] + count
            cursor.execute('UPDATE user_feisheng_items SET count = ? WHERE uid = ? AND item_name = ?', (new_count, uid, item_name))
        else:
            cursor.execute('INSERT INTO user_feisheng_items (uid, item_name, count) VALUES (?, ?, ?)', (uid, item_name, count))
            
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update)

async def use_feisheng_item(uid: int, item_name: str, count: int = 1) -> bool:
    """使用/消耗飞升物品"""
    init_feisheng_items_table()
    
    def _update():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT count FROM user_feisheng_items WHERE uid = ? AND item_name = ?', (uid, item_name))
        row = cursor.fetchone()
        
        if not row or row["count"] < count:
            conn.close()
            return False
            
        new_count = row["count"] - count
        if new_count <= 0:
            cursor.execute('DELETE FROM user_feisheng_items WHERE uid = ? AND item_name = ?', (uid, item_name))
        else:
            cursor.execute('UPDATE user_feisheng_items SET count = ? WHERE uid = ? AND item_name = ?', (new_count, uid, item_name))
            
        conn.commit()
        conn.close()
        return True
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _update)

async def get_all_feisheng_status() -> dict[int, bool]:
    """获取所有用户的飞升状态
    
    Returns:
        {uid: is_ascended} 已飞升为True，未飞升为False
    """
    init_feisheng_database()
    
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT uid, is_ascended FROM user_feisheng WHERE is_ascended = 1')
        rows = cursor.fetchall()
        conn.close()
        
        return {row["uid"]: bool(row["is_ascended"]) for row in rows}
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def get_all_pet_feisheng_status() -> dict[int, dict]:
    """获取所有用户的宠物飞升和个人飞升状态
    
    Returns:
        {uid: {"is_pet_ascended": bool, "is_ascended": bool}}
    """
    init_feisheng_database()
    
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT uid, is_pet_ascended, is_ascended FROM user_feisheng WHERE is_pet_ascended = 1 OR is_ascended = 1')
        rows = cursor.fetchall()
        conn.close()
        
        return {row["uid"]: {"is_pet_ascended": bool(row["is_pet_ascended"]), "is_ascended": bool(row["is_ascended"])} for row in rows}
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def get_feisheng_leaderboard(limit: int = 50) -> list[dict]:
    """获取飞升排行榜数据"""
    init_feisheng_database()
    
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        # 排序规则：已飞升优先(is_ascended DESC)，其次境界高(realm_level DESC)，最后进度高(ascension_progress DESC)
        cursor.execute('''
            SELECT uid, realm_level, ascension_progress, is_ascended 
            FROM user_feisheng 
            ORDER BY is_ascended DESC, realm_level DESC, ascension_progress DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)
