import sqlite3
import os
from pathlib import Path
from nonebot.log import logger

from typing import Optional

# 数据库文件路径
DB_PATH: Optional[str] = None


def set_db_path(path: str):
    """设置数据库路径"""
    global DB_PATH
    DB_PATH = path


def get_database_path() -> str:
    """获取数据库路径"""
    if DB_PATH is None:
        raise RuntimeError("数据库路径未设置，请先调用 set_db_path()")
    return DB_PATH


def _get_connection():
    """获取数据库连接"""
    return sqlite3.connect(get_database_path())


def get_user_nickname(uid: int) -> str:
    """获取用户设定的昵称"""
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT nickname FROM call_me_please_users WHERE uid = ?", (uid,))
            row = cursor.fetchone()
            if row:
                return row[0]
            return ""
    except Exception as e:
        logger.error(f"[call_me_please] 获取用户昵称失败: {e}")
        return ""


def set_user_nickname(uid: int, nickname: str) -> bool:
    """设定或更新用户昵称"""
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO call_me_please_users (uid, nickname)
                VALUES (?, ?)
                ON CONFLICT(uid) DO UPDATE SET nickname=excluded.nickname
                ''',
                (uid, nickname)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"[call_me_please] 更新用户昵称失败: {e}")
        return False
