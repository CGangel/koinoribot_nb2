"""
钓鱼插件工具模块 - util.py

包含数据库管理和冷却时间管理类
"""

import sqlite3
import time
from datetime import datetime
from typing import Optional, Dict

from nonebot import logger

from ...koinori_config import config


class DatabaseManager:
    """数据库管理器"""
    
    _db_path: Optional[str] = None
    
    @classmethod
    def set_db_path(cls, path: str):
        """设置数据库路径"""
        cls._db_path = path
    
    @classmethod
    def get_connection(cls) -> sqlite3.Connection:
        """获取数据库连接"""
        if cls._db_path is None:
            raise RuntimeError("数据库路径未设置")
        conn = sqlite3.connect(cls._db_path)
        conn.row_factory = sqlite3.Row
        # 启用外键约束
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def check_and_update_fish_limit(cls, uid: int, count: int) -> bool:
        """
        检查并更新用户钓鱼次数限制
        
        Args:
            uid: 用户ID
            count: 要增加的次数（可以是负数）
            
        Returns:
            如果未达到上限则增加计数并返回True，达到上限返回False
        """
        
        today_str = datetime.now().strftime('%Y-%m-%d')
        conn = cls.get_connection()
        cursor = conn.cursor()
        
        try:
            # 查询用户今天的记录
            cursor.execute(
                'SELECT date_str, count, limit_count FROM fish_limit WHERE uid = ?', 
                (uid,)
            )
            result = cursor.fetchone()
            
            # 每日限制，默认值
            default_limit = config.fish_limit_count
            
            if result:
                date_str, current_count, current_limit_count = result
                
                # 如果是同一天
                if date_str == today_str:
                    # 负数直接增加 limit_count
                    if count < 0:
                        new_limit_count = current_limit_count - count
                        cursor.execute(
                            'UPDATE fish_limit SET limit_count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
                            (new_limit_count, uid)
                        )
                    else:
                        # 正常情况：检查是否超过上限
                        new_count = current_count + count
                        if new_count > current_limit_count:
                            conn.close()
                            return False
                        # 更新计数
                        cursor.execute(
                            'UPDATE fish_limit SET count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
                            (new_count, uid)
                        )
                else:
                    # 不是同一天，重置计数和限制次数
                    if count < 0:
                        # 对于负数，重置后增加 limit_count
                        new_limit_count = default_limit - count
                        cursor.execute(
                            'UPDATE fish_limit SET date_str = ?, count = 0, limit_count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
                            (today_str, new_limit_count, uid)
                        )
                    else:
                        # 对于正数，正常重置
                        cursor.execute(
                            'UPDATE fish_limit SET date_str = ?, count = ?, limit_count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
                            (today_str, count, default_limit, uid)
                        )
            else:
                # 没有记录，插入新记录
                if count < 0:
                    new_limit_count = default_limit - count
                    cursor.execute(
                        'INSERT INTO fish_limit (uid, date_str, count, limit_count) VALUES (?, ?, 0, ?)',
                        (uid, today_str, new_limit_count)
                    )
                else:
                    cursor.execute(
                        'INSERT INTO fish_limit (uid, date_str, count, limit_count) VALUES (?, ?, ?, ?)',
                        (uid, today_str, count, default_limit)
                    )
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"更新钓鱼次数限制时出错: {e}")
            return False

    @classmethod
    def get_user_fish_count_today(cls, uid: int) -> tuple:
        """
        获取用户今日已钓鱼次数
        
        Returns:
            (today_count, limit_count)
        """
        
        today_str = datetime.now().strftime('%Y-%m-%d')
        conn = cls.get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'SELECT count, limit_count FROM fish_limit WHERE uid = ? AND date_str = ?', 
            (uid, today_str)
        )
        result = cursor.fetchone()
        
        conn.close()
        
        if result:
            return result['count'], result['limit_count']
        else:
            return 0, config.fish_limit_count


class CooldownManager:
    """冷却时间管理器"""
    
    _cooldown_data: Dict[int, float] = {}
    
    def __init__(self, default_cd: float = 5.0):
        """
        初始化冷却管理器
        
        Args:
            default_cd: 默认冷却时间（秒）
        """
        self._default_cd = default_cd
    
    def start_cd(self, uid: int, cd_time: float = None):
        """
        启动用户冷却
        
        Args:
            uid: 用户ID
            cd_time: 冷却时间，不传则使用默认值
        """
        cd = cd_time if cd_time is not None else self._default_cd
        CooldownManager._cooldown_data[uid] = time.time() + cd
    
    def left_time(self, uid: int) -> float:
        """
        获取用户剩余冷却时间
        
        Args:
            uid: 用户ID
            
        Returns:
            剩余冷却时间（秒），已过期返回0
        """
        if uid not in CooldownManager._cooldown_data:
            return 0
        return max(0, CooldownManager._cooldown_data[uid] - time.time())
    
    def check(self, uid: int) -> bool:
        """
        检查用户冷却是否结束
        
        Args:
            uid: 用户ID
            
        Returns:
            True 表示冷却已结束，可以执行操作
        """
        return self.left_time(uid) <= 0
