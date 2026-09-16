"""每日钓鱼次数限制（fish 模块内的共用工具）。

从旧版 fishing 插件的 util.DatabaseManager 抽出，只保留 fish_limit 表
相关能力：记录每个 UID 当日已钓鱼次数与当日上限。宠物技能（捕鱼达人）、
幸运转盘等奖励通过传入负数 count 为用户累加当日上限。

虽随 fish 模块存放，但 **chongwu / chaogu 也共用**这套每日次数口径
（三方写入同一张 fish_limit 表）；本模块不依赖 fish 玩法本身，可独立导入。
"""

import sqlite3
from datetime import datetime
from typing import Optional

from nonebot.log import logger

from ...config_store import config


def _fish_limit_statement(
    result,
    today: str,
    count: int,
    default_limit: int,
):
    """根据当前行与目标增量生成待执行的 SQL 语句。

    result 为该 uid 的现有行（date_str, count, limit_count），None 表示无行。
    count 为正数表示消耗次数，负数表示增加当日上限。
    """
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


class FishLimitManager:
    """fish_limit 表管理器（classmethod 风格，路径由外部注入）。"""

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
    def init_database(cls):
        """初始化 fish_limit 表（幂等）"""
        if cls._db_initialized:
            return

        conn = cls.get_connection()
        cursor = conn.cursor()

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

        conn.commit()
        conn.close()
        cls._db_initialized = True
        logger.info("fish_limit 表初始化完成")

    @classmethod
    def check_and_update_fish_limit(cls, uid: int, count: int) -> bool:
        """
        检查并更新用户钓鱼次数限制

        Args:
            uid: 用户ID
            count: 要增加的次数（可以是负数，负数表示增加当日上限）

        Returns:
            如果未达到上限则增加计数并返回True，达到上限返回False
        """
        cls.init_database()

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
    def refund_today_count(cls, uid: int, count: int = 1) -> bool:
        """退还今日已消耗的钓鱼次数（当日有记录才生效，减到 0 为止）。

        供钓鱼入口在「未实际抛竿」（如还有待处理鱼获、渔具损坏）时回滚
        已扣的次数，保证次数只在真正抛竿时消耗。
        """
        cls.init_database()

        today_str = datetime.now().strftime('%Y-%m-%d')
        conn = cls.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'UPDATE fish_limit SET count = MAX(0, count - ?), '
                'updated_time = CURRENT_TIMESTAMP '
                'WHERE uid = ? AND date_str = ?',
                (count, uid, today_str),
            )
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            return affected > 0
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"退还钓鱼次数时出错: {e}")
            return False

    @classmethod
    def get_user_fish_count_today(cls, uid: int) -> tuple:
        """
        获取用户今日已钓鱼次数

        Returns:
            (today_count, limit_count)
        """
        cls.init_database()

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
