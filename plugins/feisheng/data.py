"""
飞升系统数据模块

处理飞升相关的数据存储
"""

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

async def get_feisheng_data(uid: int) -> dict:
    """获取用户的飞升数据"""
    
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
    """更新用户的飞升数据"""
    
    def _update():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO user_feisheng 
            (uid, pet_ascension_progress, ascension_progress, is_pet_ascended, is_ascended, realm_level, daily_cultivation_count, cultivation_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            uid, 
            data.get("pet_ascension_progress", 0),
            data.get("ascension_progress", 0),
            int(data.get("is_pet_ascended", 0)),
            int(data.get("is_ascended", 0)),
            data.get("realm_level", 0),
            data.get("daily_cultivation_count", 0),
            data.get("cultivation_date", "")
        ))
        
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update)

async def check_daily_cultivation_limit(uid: int, limit: int = 5) -> bool:
    """检查每日修炼限制。返回 True 表示未超限，可以修炼"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    
    data = await get_feisheng_data(uid)
    last_date = data.get("cultivation_date", "")
    count = data.get("daily_cultivation_count", 0)
    
    if last_date != today:
        # 新的一天，重置
        data["cultivation_date"] = today
        data["daily_cultivation_count"] = 0
        await update_feisheng_data(uid, data)
        return True
        
    if count >= limit:
        return False
        
    return True

async def increase_cultivation_count(uid: int):
    """增加每日修炼计数"""
    data = await get_feisheng_data(uid)
    data["daily_cultivation_count"] = data.get("daily_cultivation_count", 0) + 1
    await update_feisheng_data(uid, data)

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

async def get_user_feisheng_items(uid: int) -> Dict[str, int]:
    """获取用户所有飞升物品"""
    
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
