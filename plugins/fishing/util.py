import sqlite3
import time
from datetime import datetime
from typing import Optional, Dict

from nonebot import logger

from ...config_store import config


def _fish_limit_statement(
    result,
    today: str,
    count: int,
    default_limit: int,
):
    if result is None:
        if count < 0:
            return (
                'INSERT INTO fish_limit (uid, date_str, count, limit_count) VALUES (?, ?, 0, ?)',
                (today, default_limit - count),
            )
        return (
            'INSERT INTO fish_limit (uid, date_str, count, limit_count) VALUES (?, ?, ?, ?)',
            (today, count, default_limit),
        )

    date_str, current_count, current_limit = result
    if date_str != today:
        reset_count = 0 if count < 0 else count
        reset_limit = default_limit - count if count < 0 else default_limit
        return (
            'UPDATE fish_limit SET date_str = ?, count = ?, limit_count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
            (today, reset_count, reset_limit),
        )

    if count < 0:
        return (
            'UPDATE fish_limit SET limit_count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
            (current_limit - count,),
        )

    new_count = current_count + count
    if new_count > current_limit:
        return None
    return (
        'UPDATE fish_limit SET count = ?, updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
        (new_count,),
    )


class DatabaseManager:
    """数据库管理器"""
    
    _db_path: Optional[str] = None
    _db_initialized: bool = False
    
    @classmethod
    def set_db_path(cls, path: str):
        """设置数据库路径"""
        cls._db_path = path
        cls._db_initialized = False
    
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
    def init_fishing_database(cls):
        """初始化钓鱼数据库"""
        if cls._db_initialized:
            return
        
        conn = cls.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fishing (
                uid INTEGER PRIMARY KEY,
                fish_data TEXT NOT NULL,
                statis_data TEXT NOT NULL,
                rod_data TEXT NOT NULL,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_limit (
                uid INTEGER PRIMARY KEY,
                date_str TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                limit_count INTEGER NOT NULL DEFAULT 0,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        # 漂流瓶主表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL,
                content TEXT NOT NULL,
                pick_count INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                created_time INTEGER NOT NULL
            )
        ''')

        # 漂流瓶评论表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottle_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bottle_id INTEGER NOT NULL,
                uid INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_time INTEGER NOT NULL,
                FOREIGN KEY (bottle_id) REFERENCES bottles(id) ON DELETE CASCADE
            )
        ''')

        # 确保 bottles 的 AUTOINCREMENT 从 10001 开始
        cursor.execute("SELECT COUNT(*) FROM bottles")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT OR IGNORE INTO sqlite_sequence (name, seq) VALUES ('bottles', 10000)"
            )

        conn.commit()
        conn.close()
        cls._db_initialized = True
        logger.info("钓鱼数据库初始化完成")

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
        cls.init_fishing_database()
        
        today_str = datetime.now().strftime('%Y-%m-%d')
        conn = cls.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'SELECT date_str, count, limit_count FROM fish_limit WHERE uid = ?', 
                (uid,)
            )
            result = cursor.fetchone()
            default_limit = config.fish_limit_count
            statement = _fish_limit_statement(
                result,
                today_str,
                count,
                default_limit,
            )
            if statement is None:
                conn.close()
                return False

            sql, parameters = statement
            cursor.execute(sql, (uid, *parameters) if sql.startswith('INSERT') else (*parameters, uid))
            
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
        cls.init_fishing_database()
        
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
