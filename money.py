"""
用户资产管理模块

管理用户金币、星星、宝石等资产，使用统一 UID 系统。
对业务层暴露当前用户钱包代理，例如 money.gold -= 1000。
"""

from contextvars import ContextVar
import sqlite3
import sys
from typing import Optional, Union

from nonebot.log import logger

from .config_store import get_config


# 默认初始资产
DEFAULT_ASSETS = {
    "gold": 3000,  # 金币
    "luckygold": 1,  # 幸运币
    "starstone": 12500,  # 星星
    "kirastone": 5,  # 羽毛石/宝石
    "last_login": 0,  # 最后签到日期
    "rp": 0,  # 运势值
    "logindays": 0,  # 连续签到天数
    "exgacha": 0,  # 抽卡券
    "goodluck": 0,  # 宜做事项索引
    "badluck": 0,  # 忌做事项索引
}

# 资产上限（运行时读取，8889 面板修改后即时生效）
def _gold_max() -> int:
    return get_config().gold_max

KEYWORD_LIST = list(DEFAULT_ASSETS)
KEYWORD_SET = set(KEYWORD_LIST)
KEY_LIST = ["gold", "luckygold", "starstone", "kirastone"]

# 货币名称映射
NAME_MAP = {
    "starstone": ["starstone", "星星", "星石", "星", "stars", "爱星", "艾星"],
    "luckygold": ["luckygold", "lucky", "幸运", "幸运币"],
    "gold": ["gold", "金币", "金子", "黄金"],
    "exgacha": ["井券", "兑换券", "exgacha"],
    "kirastone": ["羽毛石", "宝石", "kirastone"],
}

_current_uid: ContextVar[Optional[int]] = ContextVar("koinori_money_current_uid", default=None)


def _find_uid_in_stack() -> Optional[int]:
    frame = None
    try:
        frame = sys._getframe(2)
        while frame:
            candidate = frame.f_locals.get("uid")
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return int(candidate)
            frame = frame.f_back
    finally:
        del frame
    return None


class _MoneyRepository:
    """SQLite 资产仓库。业务层请使用 money / UserWallet。"""

    def __init__(self):
        self._db_path: Optional[str] = None
        self._db_initialized = False

    # ===== 数据库路径管理 =====

    def set_database_path(self, path: str):
        """设置数据库路径"""
        self._db_path = path
        self._db_initialized = False

    def get_database_path(self) -> str:
        """获取数据库路径"""
        if self._db_path is None:
            raise RuntimeError("数据库路径未设置，请先调用 money.set_database_path()")
        return self._db_path

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.get_database_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ===== 数据库初始化 =====

    def init_database(self):
        """初始化用户资产数据库表"""
        if self._db_initialized:
            return

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
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
            )
        """)

        conn.commit()
        conn.close()
        self._db_initialized = True

    def _ensure_initialized(self):
        """确保数据库已初始化"""
        if not self._db_initialized:
            self.init_database()

    def _ensure_user_exists(self, cursor, uid: int) -> bool:
        """确保用户记录存在，不存在则创建"""
        cursor.execute("SELECT uid FROM user_money WHERE uid = ?", (uid,))
        if cursor.fetchone() is None:
            cursor.execute(
                """
                INSERT INTO user_money
                (uid, gold, luckygold, starstone, kirastone, last_login, rp, logindays, exgacha, goodluck, badluck)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    uid,
                    DEFAULT_ASSETS["gold"],
                    DEFAULT_ASSETS["luckygold"],
                    DEFAULT_ASSETS["starstone"],
                    DEFAULT_ASSETS["kirastone"],
                    DEFAULT_ASSETS["last_login"],
                    DEFAULT_ASSETS["rp"],
                    DEFAULT_ASSETS["logindays"],
                    DEFAULT_ASSETS["exgacha"],
                    DEFAULT_ASSETS["goodluck"],
                    DEFAULT_ASSETS["badluck"],
                ),
            )
            return True
        return False

    # ===== 用户资产 CRUD =====

    def get(self, uid: int, *keys: str) -> Optional[Union[int, tuple]]:
        """获取用户指定资源的数量。"""
        self._ensure_initialized()

        if not keys or any(key not in KEYWORD_SET for key in keys):
            return None

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            created = self._ensure_user_exists(cursor, uid)
            if created:
                conn.commit()

            columns = ", ".join(keys)
            cursor.execute(f"SELECT {columns} FROM user_money WHERE uid = ?", (uid,))
            result = cursor.fetchone()

            if result is None:
                return None

            if len(keys) == 1:
                return int(result[0]) if result[0] is not None else None
            return tuple(int(v) if v is not None else None for v in result)

        except Exception as e:
            logger.error(f"[money] 获取用户数据失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def set(self, uid: int, key: str, value: int) -> int:
        """直接设置用户某种资源。gold 只限制上限，不限制下限。"""
        self._ensure_initialized()

        if key not in KEYWORD_SET:
            return 0

        value = int(value)
        if key == "gold":
            value = min(value, _gold_max())

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            self._ensure_user_exists(cursor, uid)
            cursor.execute(f"UPDATE user_money SET {key} = ? WHERE uid = ?", (value, uid))

            conn.commit()
            return 1
        except Exception as e:
            logger.error(f"[money] 设置用户数据失败: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    def increase(self, uid: int, key: str, value: int) -> int:
        """原子增加用户某种资源。"""
        self._ensure_initialized()

        if key not in KEYWORD_SET:
            return 0

        value = int(value)
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            self._ensure_user_exists(cursor, uid)
            if key == "gold":
                cursor.execute(
                    f"UPDATE user_money SET {key} = MIN({key} + ?, ?) WHERE uid = ?",
                    (value, _gold_max(), uid),
                )
            else:
                cursor.execute(
                    f"UPDATE user_money SET {key} = {key} + ? WHERE uid = ?", (value, uid)
                )

            conn.commit()
            return 1
        except Exception as e:
            logger.error(f"[money] 增加用户资产失败: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    def decrease(self, uid: int, key: str, value: int) -> int:
        """原子减少用户某种资源。gold 允许扣成负数。"""
        self._ensure_initialized()

        if key not in KEYWORD_SET:
            return 0

        value = int(value)
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            created = self._ensure_user_exists(cursor, uid)
            if created:
                conn.commit()
                return 0  # 新用户无法扣款

            cursor.execute(
                f"UPDATE user_money SET {key} = {key} - ? WHERE uid = ?", (value, uid)
            )

            conn.commit()
            return 1
        except Exception as e:
            logger.error(f"[money] 减少用户资产失败: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    # ===== 批量操作 =====

    def increase_all(self, key: str, value: int) -> int:
        """增加所有用户某种资源。"""
        self._ensure_initialized()

        if key not in KEYWORD_SET:
            return 0

        value = int(value)
        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if key == "gold":
                cursor.execute(f"UPDATE user_money SET {key} = MIN({key} + ?, ?)", (value, _gold_max()))
            else:
                cursor.execute(f"UPDATE user_money SET {key} = {key} + ?", (value,))

            conn.commit()
            return 1
        except Exception as e:
            logger.error(f"[money] 增加所有用户资产失败: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    def get_all(self, key: str) -> dict[int, int]:
        """获取所有用户指定资产，返回 {uid: value}。"""
        self._ensure_initialized()

        if key not in KEYWORD_SET:
            return {}

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute(f"SELECT uid, {key} FROM user_money")
            results = cursor.fetchall()

            return {row["uid"]: row[key] for row in results}
        except Exception as e:
            logger.error(f"[money] 获取所有用户资产失败: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def delete_user(self, uid: int) -> int:
        """删除用户账户。"""
        self._ensure_initialized()

        conn = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("DELETE FROM user_money WHERE uid = ?", (uid,))
            affected = cursor.rowcount

            conn.commit()
            return 1 if affected > 0 else 0
        except Exception as e:
            logger.error(f"[money] 删除用户账户失败: {e}")
            return 0
        finally:
            if conn:
                conn.close()


_repository = _MoneyRepository()


class _MutationApplied:
    """标记增强赋值已经通过原子更新落库，随后 __setattr__ 无需再写一次。"""


_MUTATION_APPLIED = _MutationApplied()


class _AssetValue(int):
    """可当作 int 使用，并让 += / -= 转成原子资产更新。"""

    def __new__(cls, value: int, wallet: "UserWallet", key: str):
        obj = int.__new__(cls, value)
        obj._wallet = wallet
        obj._key = key
        return obj

    def __iadd__(self, value: int):
        self._wallet._increase(self._key, int(value))
        return _MUTATION_APPLIED

    def __isub__(self, value: int):
        self._wallet._decrease(self._key, int(value))
        return _MUTATION_APPLIED


class UserWallet:
    """绑定某个 UID 的钱包对象。属性读写会立即落库。"""

    __slots__ = ("uid",)

    def __init__(self, uid: int):
        object.__setattr__(self, "uid", int(uid))

    def __getattr__(self, key: str) -> int:
        if key not in KEYWORD_SET:
            raise AttributeError(key)
        value = _repository.get(self.uid, key)
        return _AssetValue(int(value) if value is not None else 0, self, key)

    def __setattr__(self, key: str, value: int):
        if value is _MUTATION_APPLIED:
            return
        if key == "uid":
            object.__setattr__(self, key, int(value))
            return
        if key not in KEYWORD_SET:
            raise AttributeError(key)
        _repository.set(self.uid, key, int(value))

    def __getitem__(self, key: str) -> int:
        if key not in KEYWORD_SET:
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key: str, value: int):
        if value is _MUTATION_APPLIED:
            return
        if key not in KEYWORD_SET:
            raise KeyError(key)
        setattr(self, key, value)

    def get(self, *keys: str) -> Optional[Union[int, tuple]]:
        """获取一个或多个资产字段。"""
        return _repository.get(self.uid, *keys)

    def set(self, key: str, value: int) -> int:
        """直接设置资产字段。"""
        return _repository.set(self.uid, key, value)

    def _increase(self, key: str, value: int) -> int:
        return _repository.increase(self.uid, key, value)

    def _decrease(self, key: str, value: int) -> int:
        return _repository.decrease(self.uid, key, value)



class MoneyProxy:
    """当前用户钱包代理。需要先由 tools.get_uid 绑定当前 UID。"""

    @property
    def uid(self) -> int:
        stack_uid = _find_uid_in_stack()
        if stack_uid is not None:
            bind_current_uid(stack_uid)
            return stack_uid

        context_uid = _current_uid.get()
        if context_uid is not None:
            return context_uid

        raise RuntimeError("当前用户 UID 未绑定，请先通过 get_uid 依赖注入或 money.bind(uid) 绑定")

    @property
    def current(self) -> UserWallet:
        return UserWallet(self.uid)

    def bind(self, uid: int) -> UserWallet:
        """绑定当前上下文 UID，并返回该用户钱包。"""
        return bind_current_uid(uid)

    def of(self, uid: int) -> UserWallet:
        """获取指定 UID 的钱包。"""
        return UserWallet(uid)

    def __getattr__(self, key: str) -> int:
        if key not in KEYWORD_SET:
            raise AttributeError(key)
        return getattr(self.current, key)

    def __setattr__(self, key: str, value: int):
        if key not in KEYWORD_SET:
            raise AttributeError(key)
        setattr(self.current, key, value)

    def get(self, *keys: str) -> Optional[Union[int, tuple]]:
        """获取当前用户一个或多个资产字段。"""
        return self.current.get(*keys)

    def set(self, key: str, value: int) -> int:
        """直接设置当前用户资产字段。"""
        return self.current.set(key, value)

    def add_all(self, key: str, value: int) -> int:
        """增加所有用户某种资产。"""
        return _repository.increase_all(key, value)

    def all(self, key: str) -> dict[int, int]:
        """获取所有用户某种资产。"""
        return _repository.get_all(key)

    def delete(self, uid: int) -> int:
        """删除指定 UID 的资产账户。"""
        return _repository.delete_user(uid)


money = MoneyProxy()


def bind_current_uid(uid: int) -> UserWallet:
    """绑定当前上下文 UID，并返回该用户钱包。"""
    uid = int(uid)
    _current_uid.set(uid)
    return UserWallet(uid)


# ===== 模块级函数（保留非货币/初始化类接口） =====

def set_database_path(path: str):
    _repository.set_database_path(path)


def get_database_path() -> str:
    return _repository.get_database_path()


def init_money_database():
    _repository.init_database()


def translate_name(name: str) -> str:
    """将货币昵称转换为关键字。"""
    for key, names in NAME_MAP.items():
        if name in names:
            return key
    return ""


__all__ = [
    "DEFAULT_ASSETS",
    "KEYWORD_LIST",
    "KEY_LIST",
    "NAME_MAP",
    "UserWallet",
    "money",
    "bind_current_uid",
    "set_database_path",
    "get_database_path",
    "init_money_database",
    "translate_name",
]
