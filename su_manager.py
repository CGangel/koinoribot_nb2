"""
Superuser (SU) 管理工具。

模块级函数保留为外部调用 API；具体实现收拢到内部类，避免权限、数据库、
每日额度等细节散在模块顶层。
"""

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from nonebot.log import logger


SU_LEVEL_CONTRIBUTOR = 0  # 最高权限
SU_LEVEL_NORMAL = 1       # 普通 SU
SU_LEVEL_TRUSTED = 2      # trusted SU

SU_HONGBAO_DAILY_LIMIT = 100000
SU_TRANSFER_DAILY_LIMIT = 100000


class _SuManager:
    """内部 SU 管理实现。外部请使用本模块下方的包装函数。"""

    def get_db_path(self) -> str:
        plugin_dir = Path(__file__).parent
        return str(plugin_dir / "src" / "database" / "koinoribot.db")

    def get_config_superusers(self) -> list[int]:
        """等级 0 超级用户种子列表（passwd.py 的 SUPERUSERS）。"""
        try:
            from .config_store import get_passwd_superusers
            return get_passwd_superusers()
        except Exception:
            return []

    def query_table(self, uid: int) -> Optional[int]:
        conn = sqlite3.connect(self.get_db_path())
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT level FROM superusers WHERE uid = ?", (uid,))
            row = cursor.fetchone()
            if row is not None:
                return row[0]
            return None
        finally:
            conn.close()

    def table_exists(self) -> bool:
        conn = sqlite3.connect(self.get_db_path())
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='superusers'"
            )
            return cursor.fetchone() is not None
        except Exception:
            return False
        finally:
            conn.close()

    def init_superusers_table(self) -> None:
        conn = sqlite3.connect(self.get_db_path())
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS superusers (
                    uid INTEGER PRIMARY KEY,
                    level INTEGER NOT NULL DEFAULT 1,
                    activated_at REAL NOT NULL,
                    activation_code TEXT,
                    daily_hongbao_used INTEGER NOT NULL DEFAULT 0,
                    daily_transfer_used INTEGER NOT NULL DEFAULT 0,
                    daily_date TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.commit()
            logger.info("[su_manager] superusers table initialized")
        except Exception as e:
            logger.error(f"[su_manager] failed to initialize superusers table: {e}")
        finally:
            conn.close()

    def register_su(self, uid: int, level: int, activation_code: str) -> bool:
        conn = sqlite3.connect(self.get_db_path())
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO superusers (uid, level, activated_at, activation_code)
                VALUES (?, ?, ?, ?)
                """,
                (uid, level, time.time(), activation_code),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"[su_manager] uid {uid} is already SU, skip registration")
            return False
        except Exception as e:
            logger.error(f"[su_manager] failed to register SU: {e}")
            return False
        finally:
            conn.close()

    def get_su_level(self, uid: int) -> Optional[int]:
        try:
            level = self.query_table(uid)
            if level is not None:
                return level
        except sqlite3.OperationalError:
            config_sus = self.get_config_superusers()
            if uid in config_sus:
                return SU_LEVEL_CONTRIBUTOR
            return None
        except Exception as e:
            logger.error(f"[su_manager] failed to query SU table: {e}")

        config_sus = self.get_config_superusers()
        if uid in config_sus:
            return SU_LEVEL_CONTRIBUTOR
        return None

    def is_su(self, uid: int) -> bool:
        return self.get_su_level(uid) is not None

    def is_su_normal(self, uid: int) -> bool:
        return self.get_su_level(uid) == SU_LEVEL_NORMAL

    def is_su_contributor(self, uid: int) -> bool:
        return self.get_su_level(uid) == SU_LEVEL_CONTRIBUTOR

    def get_all_su_uids(self) -> list[int]:
        su_uids = set(self.get_config_superusers())

        try:
            if self.table_exists():
                conn = sqlite3.connect(self.get_db_path())
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT uid FROM superusers")
                    for row in cursor.fetchall():
                        su_uids.add(row[0])
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[su_manager] failed to get all SU uids: {e}")

        return list(su_uids)

    def get_excluded_su_uids(self) -> list[int]:
        excluded = set(self.get_config_superusers())

        try:
            if self.table_exists():
                conn = sqlite3.connect(self.get_db_path())
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT uid, level FROM superusers")
                    for row in cursor.fetchall():
                        if row[1] != SU_LEVEL_NORMAL:
                            excluded.add(row[0])
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[su_manager] failed to get excluded SU uids: {e}")

        return list(excluded)

    def get_today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def get_daily_usage(self, uid: int) -> dict[str, int]:
        today = self.get_today_str()
        try:
            conn = sqlite3.connect(self.get_db_path())
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT daily_hongbao_used, daily_transfer_used, daily_date
                    FROM superusers
                    WHERE uid = ?
                    """,
                    (uid,),
                )
                row = cursor.fetchone()
                if row is None or row["daily_date"] != today:
                    return {"hongbao": 0, "transfer": 0}
                return {
                    "hongbao": row["daily_hongbao_used"] or 0,
                    "transfer": row["daily_transfer_used"] or 0,
                }
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[su_manager] failed to get daily usage: {e}")
            return {"hongbao": 0, "transfer": 0}

    def record_su_usage(self, uid: int, action: str, amount: int) -> None:
        level = self.get_su_level(uid)
        if level is None or level == SU_LEVEL_CONTRIBUTOR:
            return

        today = self.get_today_str()
        column = (
            "daily_hongbao_used"
            if action == "hongbao"
            else "daily_transfer_used"
        )

        try:
            conn = sqlite3.connect(self.get_db_path())
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT daily_date FROM superusers WHERE uid = ?", (uid,))
                row = cursor.fetchone()
                if row is None:
                    return

                if row[0] != today:
                    cursor.execute(
                        """
                        UPDATE superusers
                        SET daily_hongbao_used = 0,
                            daily_transfer_used = 0,
                            daily_date = ?
                        WHERE uid = ?
                        """,
                        (today, uid),
                    )

                cursor.execute(
                    f"UPDATE superusers SET {column} = {column} + ?, daily_date = ? WHERE uid = ?",
                    (amount, today, uid),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[su_manager] failed to record daily usage: {e}")

    def check_su_permission(self, uid: int, action: str, **kwargs) -> tuple[bool, str]:
        level = self.get_su_level(uid)
        if level is None:
            return (True, "")

        if level == SU_LEVEL_CONTRIBUTOR:
            return (True, "")

        usage = self.get_daily_usage(uid)

        if action == "hongbao":
            amount = kwargs.get("amount", 0)
            used = usage["hongbao"]
            remaining = SU_HONGBAO_DAILY_LIMIT - used
            if amount > remaining:
                return (
                    False,
                    f"SU用户(Lv.{level})每日红包额度不足\n"
                    f"每日上限: {SU_HONGBAO_DAILY_LIMIT} 金币\n"
                    f"今日已用: {used} 金币\n"
                    f"剩余额度: {remaining} 金币\n"
                    f"本次请求: {amount} 金币",
                )

        elif action == "transfer":
            amount = kwargs.get("amount", 0)
            used = usage["transfer"]
            remaining = SU_TRANSFER_DAILY_LIMIT - used
            if amount > remaining:
                return (
                    False,
                    f"SU用户(Lv.{level})每日转账额度不足\n"
                    f"每日上限: {SU_TRANSFER_DAILY_LIMIT} 金币\n"
                    f"今日已用: {used} 金币\n"
                    f"剩余额度: {remaining} 金币\n"
                    f"本次请求: {amount} 金币",
                )

        elif action == "payment":
            target_uid = kwargs.get("target_uid")
            if target_uid is not None and target_uid != uid:
                return (False, f"SU用户(Lv.{level})只能向自己打款")

        return (True, "")


_su_manager = _SuManager()


def init_superusers_table() -> None:
    """初始化 superusers 表，供 SU 注册插件在启动时调用。"""
    _su_manager.init_superusers_table()


def register_su(
    uid: int,
    level: int = SU_LEVEL_NORMAL,
    activation_code: str = "",
) -> bool:
    """注册 SU。此函数作为外部工具保留给注册插件调用。"""
    return _su_manager.register_su(uid, level, activation_code)


def is_su(uid: int) -> bool:
    return _su_manager.is_su(uid)


def is_su_normal(uid: int) -> bool:
    return _su_manager.is_su_normal(uid)


def is_su_contributor(uid: int) -> bool:
    return _su_manager.is_su_contributor(uid)


def get_su_level(uid: int) -> Optional[int]:
    return _su_manager.get_su_level(uid)


def get_all_su_uids() -> list[int]:
    return _su_manager.get_all_su_uids()


def get_excluded_su_uids() -> list[int]:
    return _su_manager.get_excluded_su_uids()


def record_su_usage(uid: int, action: str, amount: int) -> None:
    _su_manager.record_su_usage(uid, action, amount)


def check_su_permission(uid: int, action: str, **kwargs) -> tuple[bool, str]:
    return _su_manager.check_su_permission(uid, action, **kwargs)


__all__ = [
    "SU_LEVEL_CONTRIBUTOR",
    "SU_LEVEL_NORMAL",
    "SU_LEVEL_TRUSTED",
    "SU_HONGBAO_DAILY_LIMIT",
    "SU_TRANSFER_DAILY_LIMIT",
    "init_superusers_table",
    "register_su",
    "is_su",
    "is_su_normal",
    "is_su_contributor",
    "get_su_level",
    "get_all_su_uids",
    "get_excluded_su_uids",
    "record_su_usage",
    "check_su_permission",
]
