"""
宠物数据操作模块

处理宠物和物品的数据库操作
"""

import json
import sqlite3
import asyncio
import time
from typing import Optional, Dict, Any
from datetime import datetime

from .petconfig import BASE_PETS, STATUS_DESCRIPTIONS, GROWTH_STAGE_1, GROWTH_STAGE_2, GROWTH_STAGE_3

# 数据库路径
_db_path: Optional[str] = None


def set_db_path(path: str):
    """设置数据库路径"""
    global _db_path
    _db_path = path



def _get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    if _db_path is None:
        raise RuntimeError("数据库路径未设置")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    # 启用外键约束
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def get_user_pets() -> Dict[int, dict]:
    """获取所有用户的宠物数据"""
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT uid, pet_data FROM user_pets')
        results = cursor.fetchall()
        conn.close()
        
        user_pets = {}
        for row in results:
            if row['pet_data']:
                user_pets[row['uid']] = json.loads(row['pet_data'])
        return user_pets
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def get_user_pet(user_id: int) -> Optional[dict]:
    """获取单个用户的宠物"""
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT pet_data FROM user_pets WHERE uid = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result['pet_data']:
            return json.loads(result['pet_data'])
        return None
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def update_user_pet(user_id: int, pet_data: dict):
    """更新用户的宠物数据"""
    def _update():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO user_pets (uid, pet_data)
            VALUES (?, ?)
        ''', (user_id, json.dumps(pet_data, ensure_ascii=False)))
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update)


async def remove_user_pet(user_id: int) -> bool:
    """移除用户的宠物"""
    def _remove():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_pets WHERE uid = ?', (user_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _remove)


async def get_user_items(user_id: int) -> dict:
    """获取用户的物品"""
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT items_data FROM user_items WHERE uid = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and result['items_data']:
            return json.loads(result['items_data'])
        return {}
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def add_user_item(user_id: int, item_name: str, quantity: int = 1):
    """给用户添加物品"""
    def _add():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT items_data FROM user_items WHERE uid = ?', (user_id,))
        result = cursor.fetchone()
        
        if result and result['items_data']:
            items_data = json.loads(result['items_data'])
        else:
            items_data = {}
        
        items_data[item_name] = items_data.get(item_name, 0) + quantity
        
        cursor.execute('''
            INSERT OR REPLACE INTO user_items (uid, items_data)
            VALUES (?, ?)
        ''', (user_id, json.dumps(items_data, ensure_ascii=False)))
        
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _add)


async def use_user_item(user_id: int, item_name: str, quantity: int = 1) -> bool:
    """使用用户物品"""
    def _use():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT items_data FROM user_items WHERE uid = ?', (user_id,))
        result = cursor.fetchone()
        
        if not result or not result['items_data']:
            conn.close()
            return False
        
        items_data = json.loads(result['items_data'])
        current = items_data.get(item_name, 0)
        
        if current < quantity:
            conn.close()
            return False
        
        new_quantity = current - quantity
        if new_quantity <= 0:
            del items_data[item_name]
        else:
            items_data[item_name] = new_quantity
        
        cursor.execute('''
            INSERT OR REPLACE INTO user_items (uid, items_data)
            VALUES (?, ?)
        ''', (user_id, json.dumps(items_data, ensure_ascii=False)))
        
        conn.commit()
        conn.close()
        return True
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _use)


def get_pet_data() -> dict:
    """获取宠物基础数据"""
    return BASE_PETS


def get_status_description(stat_name: str, value: float) -> str:
    """获取状态描述"""
    thresholds = sorted(STATUS_DESCRIPTIONS[stat_name].keys(), reverse=True)
    for threshold in thresholds:
        if value >= threshold:
            return STATUS_DESCRIPTIONS[stat_name][threshold]
    return "状态异常"


async def update_pet_status(pet: dict) -> dict:
    """更新宠物状态（时间衰减）"""
    current_time = time.time()
    last_update = pet.get("last_update", current_time)
    time_passed = current_time - last_update
    
    pet["last_update"] = current_time
    
    # 离家出走状态不更新
    if pet.get("runaway", False):
        return pet
    
    # 初始化成长值上限
    if pet["stage"] == 0:
        pet["growth_required"] = GROWTH_STAGE_1
    elif pet["stage"] == 1:
        pet["growth_required"] = GROWTH_STAGE_2
    elif pet["stage"] == 2:
        pet["growth_required"] = GROWTH_STAGE_3
    
    # 状态衰减
    pet["hunger"] = max(0, pet["hunger"] - time_passed / 3600 * 2)
    pet["energy"] = max(0, pet["energy"] - time_passed / 3600 * 2)
    
    # 好感度衰减
    if pet["hunger"] < 10 or pet["energy"] < 10:
        pet["happiness"] = max(0, pet["happiness"] - time_passed / 3600 * 30)
    else:
        pet["happiness"] = max(0, pet["happiness"] - time_passed / 3600 * 1)
    
    # 成长值增加
    growth_rate = pet.get("growth_rate", 1.0)
    pet["growth"] = min(pet["growth_required"], pet.get("growth", 0) + time_passed / 3600 * growth_rate)
    if pet["happiness"] < 1:
        pet["runaway"] = True  # 标记为离家出走状态
    
    return pet


async def check_pet_evolution(pet: dict) -> Optional[str]:
    """检查宠物是否可以进化"""
    if pet["stage"] == 0 and pet["growth"] >= pet.get("growth_required", 100):
        return "stage1"
    elif pet["stage"] == 1 and pet["growth"] >= pet.get("growth_required", 200):
        return "stage2"
    return None
