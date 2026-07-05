"""
UID 统一管理模块

为 OneBot 和 QQ-Bot 双协议提供统一的用户标识(UID)系统。
- 自动创建唯一 UID（从 10001 开始）
- 双槽位设计：onebot_id 和 qqbot_id
- 预留手动绑定功能（后续实现）
"""

import random
import sqlite3
import time
from datetime import datetime
from typing import Literal, Optional

# 数据库路径
_db_path: Optional[str] = None

# UID 起始值
UID_START = 10001


def set_database_path(path: str) -> None:
    """设置数据库路径"""
    global _db_path
    _db_path = path


def get_database_path() -> str:
    """获取数据库路径"""
    if _db_path is None:
        raise RuntimeError("数据库路径未设置，请先调用 set_database_path()")
    return _db_path


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(get_database_path())
    conn.row_factory = sqlite3.Row
    # 启用外键约束
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_next_uid() -> int:
    """获取下一个可用的 UID 并自增"""
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT next_uid FROM uid_sequence WHERE id = 1')
    row = cursor.fetchone()
    next_uid = row['next_uid']
    
    cursor.execute('UPDATE uid_sequence SET next_uid = ? WHERE id = 1', (next_uid + 1,))
    conn.commit()
    conn.close()
    
    return next_uid


Platform = Literal["onebot", "qqbot"]


def get_uid(platform: Platform, external_id: str) -> int:
    """
    根据平台和外部 ID 获取或创建统一 UID
    
    Args:
        platform: "onebot" 或 "qqbot"
        external_id: 平台对应的用户标识（QQ号 或 OpenID）
    
    Returns:
        统一的内部 UID
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    # 根据平台查询对应字段
    column = "onebot_id" if platform == "onebot" else "qqbot_id"
    
    cursor.execute(f'SELECT uid FROM user_uid_mapping WHERE {column} = ?', (external_id,))
    row = cursor.fetchone()
    
    if row:
        conn.close()
        return row['uid']
    
    # 未找到，创建新 UID
    new_uid = _get_next_uid()
    created_at = datetime.now().isoformat()
    
    if platform == "onebot":
        cursor.execute(
            'INSERT INTO user_uid_mapping (uid, onebot_id, qqbot_id, created_at) VALUES (?, ?, NULL, ?)',
            (new_uid, external_id, created_at)
        )
    else:  # qqbot
        cursor.execute(
            'INSERT INTO user_uid_mapping (uid, onebot_id, qqbot_id, created_at) VALUES (?, NULL, ?, ?)',
            (new_uid, external_id, created_at)
        )
    
    conn.commit()
    conn.close()
    
    return new_uid


def get_uid_by_external_id(platform: Platform, external_id: str) -> Optional[int]:
    """
    查询 UID（不自动创建）
    
    Args:
        platform: "onebot" 或 "qqbot"
        external_id: 平台对应的用户标识
    
    Returns:
        UID，如果未找到则返回 None
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    column = "onebot_id" if platform == "onebot" else "qqbot_id"
    cursor.execute(f'SELECT uid FROM user_uid_mapping WHERE {column} = ?', (external_id,))
    row = cursor.fetchone()
    
    conn.close()
    return row['uid'] if row else None


def get_external_ids(uid: int) -> dict[str, Optional[str]]:
    """
    根据 UID 获取所有绑定的外部 ID
    
    Args:
        uid: 内部统一 UID
    
    Returns:
        {"onebot_id": ..., "qqbot_id": ...}
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT onebot_id, qqbot_id FROM user_uid_mapping WHERE uid = ?', (uid,))
    row = cursor.fetchone()
    
    conn.close()
    
    if row:
        return {
            "onebot_id": row['onebot_id'],
            "qqbot_id": row['qqbot_id']
        }
    return {"onebot_id": None, "qqbot_id": None}


def bind_external_id(uid: int, platform: Platform, external_id: str) -> bool:
    """
    为已有 UID 绑定新的外部 ID（手动绑定功能预留）
    
    Args:
        uid: 内部统一 UID
        platform: 要绑定的平台
        external_id: 外部 ID
    
    Returns:
        是否绑定成功
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    column = "onebot_id" if platform == "onebot" else "qqbot_id"
    
    # 检查 UID 是否存在
    cursor.execute('SELECT uid FROM user_uid_mapping WHERE uid = ?', (uid,))
    if not cursor.fetchone():
        conn.close()
        return False
    
    # 检查该外部 ID 是否已被绑定
    cursor.execute(f'SELECT uid FROM user_uid_mapping WHERE {column} = ?', (external_id,))
    if cursor.fetchone():
        conn.close()
        return False  # 已被其他 UID 绑定
    
    # 执行绑定
    try:
        cursor.execute(f'UPDATE user_uid_mapping SET {column} = ? WHERE uid = ?', (external_id, uid))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_all_uids() -> list[int]:
    """获取所有 UID 列表"""
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT uid FROM user_uid_mapping ORDER BY uid')
    rows = cursor.fetchall()
    
    conn.close()
    return [row['uid'] for row in rows]


def get_uid_count() -> int:
    """获取 UID 总数"""
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as count FROM user_uid_mapping')
    row = cursor.fetchone()
    
    conn.close()
    return row['count']


def is_uid_exists(uid: int) -> bool:
    """
    检查 UID 是否存在
    
    Args:
        uid: 用户 UID
    
    Returns:
        True 如果存在，否则 False
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT 1 FROM user_uid_mapping WHERE uid = ?', (uid,))
    result = cursor.fetchone()
    
    conn.close()
    return result is not None


# ===== 绑定验证码系统 =====

# 内存存储: {code: {"uid": int, "expire": float}}
_bind_codes: dict[str, dict] = {}

# 验证码有效期（秒）
BIND_CODE_EXPIRE = 300  # 5分钟


def _clean_expired_codes():
    """清理过期验证码"""
    now = time.time()
    expired = [c for c, v in _bind_codes.items() if v["expire"] < now]
    for c in expired:
        del _bind_codes[c]


def generate_bind_code(uid: int) -> str:
    """
    为指定 UID 生成6位绑定验证码

    Args:
        uid: 用户 UID

    Returns:
        6位数字验证码字符串
    """
    _clean_expired_codes()

    # 移除该 uid 之前的未过期验证码
    old_codes = [c for c, v in _bind_codes.items() if v["uid"] == uid]
    for c in old_codes:
        del _bind_codes[c]

    code = str(random.randint(100000, 999999))
    # 防止碰撞
    while code in _bind_codes:
        code = str(random.randint(100000, 999999))

    _bind_codes[code] = {
        "uid": uid,
        "expire": time.time() + BIND_CODE_EXPIRE
    }
    return code


def verify_bind_code(code: str) -> Optional[int]:
    """
    校验绑定验证码

    Args:
        code: 6位验证码

    Returns:
        源 UID（验证成功），None（验证码无效或过期）
    """
    _clean_expired_codes()

    if code not in _bind_codes:
        return None

    uid = _bind_codes[code]["uid"]
    del _bind_codes[code]
    return uid


def delete_uid_mapping(uid: int) -> bool:
    """
    删除 UID 映射行

    Args:
        uid: 要删除的 UID

    Returns:
        是否删除成功
    """
    conn = _get_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM user_uid_mapping WHERE uid = ?', (uid,))
    affected = cursor.rowcount

    conn.commit()
    conn.close()
    return affected > 0


def rebind_external_id(target_uid: int, platform: Platform, external_id: str) -> bool:
    """
    将 external_id 绑定到 target_uid 的对应槽位。
    如果 external_id 已绑定到其他 uid，先删除旧映射行。

    Args:
        target_uid: 目标 UID
        platform: 平台 ("onebot" / "qqbot")
        external_id: 外部 ID

    Returns:
        是否成功
    """
    conn = _get_connection()
    cursor = conn.cursor()
    column = "onebot_id" if platform == "onebot" else "qqbot_id"

    try:
        # 删除 external_id 的旧映射（如果存在）
        cursor.execute(f'DELETE FROM user_uid_mapping WHERE {column} = ? AND uid != ?',
                       (external_id, target_uid))

        # 将 external_id 写入 target_uid 的槽位
        cursor.execute(f'UPDATE user_uid_mapping SET {column} = ? WHERE uid = ?',
                       (external_id, target_uid))

        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()

