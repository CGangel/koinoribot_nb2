"""每日钓鱼次数限制（fish 模块内的共用工具）。

从旧版 fishing 插件的 util.DatabaseManager 抽出，只保留 fish_limit 表
相关能力。表结构（uid 主键，日期变更时自动重置当日计数）：

- count       当日已钓次数（跨天清零）
- temp_bonus  当日临时增益（宠物技能「捕鱼达人」、幸运转盘等，跨天清零）
- perm_bonus  永久增益（图鉴奖励的「每日钓鱼次数上限」，跨天保留）
- date_str    以上三项所属日期（据此判断是否需要重置）

当天总次数上限 = 配置基础次数（config.fish_limit_count）
                + temp_bonus + perm_bonus

增益写入方式：临时增益由调用方以负数 count 追加（check_and_update_
fish_limit）；永久增益在图鉴奖励领取时经 add_perm_bonus 累加，并在
初始化 / 钓鱼模块启动时经 reconcile_perm_bonus 按「领取记录 + 当前
配置」重算校正（配置面板改动在重启后对已领取用户生效）。

虽随 fish 模块存放，但 **chongwu / chaogu 也共用**这套每日次数口径
（三方写入同一张 fish_limit 表）；本模块只读 fish_config 的图鉴奖励
配置，不依赖 fish 玩法本身。
"""

import sqlite3
from datetime import datetime
from typing import Optional

from nonebot.log import logger

from ...config_store import config
from .fish_config import collection_rewards


def _base_limit() -> int:
    """配置中的每日基础次数（非法值回落 10）"""
    try:
        return max(0, int(config.fish_limit_count))
    except (TypeError, ValueError):
        return 10


def _today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def _daily_limit(temp_bonus: int, perm_bonus: int) -> int:
    """当天总次数上限 = 配置基础次数 + 临时增益 + 永久增益"""
    return _base_limit() + int(temp_bonus) + int(perm_bonus)


def _table_exists(cursor, name: str) -> bool:
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _perm_bonus_by_uid(cursor) -> dict[int, int]:
    """由图鉴奖励领取记录 + 当前配置推导的永久增益（按 uid 汇总）。

    领取记录表不存在时返回空字典：此时不存在任何领取记录，加成 0 即正确值。
    """
    if not _table_exists(cursor, "fish_collection_rewards"):
        return {}
    rows = cursor.execute(
        'SELECT uid, rarity, grade FROM fish_collection_rewards'
    ).fetchall()
    rewards = collection_rewards()
    result: dict[int, int] = {}
    for row in rows:
        uid = int(row["uid"])
        bonus = sum(
            int(item.get("count", 0))
            for item in rewards.get(f"{row['rarity']}:{row['grade']}", [])
            if item.get("type") == "fish_limit"
        )
        if bonus:
            result[uid] = result.get(uid, 0) + bonus
    return result


class FishLimitManager:
    """fish_limit 表管理器（classmethod 风格，路径由外部注入）。"""

    _db_path: Optional[str] = None
    _db_initialized: bool = False

    # ===== 路径与建表 =====

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
        """初始化 fish_limit 表（幂等；旧结构自动重建为新结构）"""
        if cls._db_initialized:
            return

        conn = cls.get_connection()
        cursor = conn.cursor()

        cls._migrate_legacy_table(cursor)
        cls._create_table(cursor)

        conn.commit()
        conn.close()
        cls._db_initialized = True
        # 永久增益按「领取记录 + 当前配置」校正（领取记录表尚未建立时为空操作）
        cls.reconcile_perm_bonus()
        logger.info("fish_limit 表初始化完成")

    @staticmethod
    def _create_table(cursor):
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_limit (
                uid INTEGER PRIMARY KEY,
                date_str TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                temp_bonus INTEGER NOT NULL DEFAULT 0,
                perm_bonus INTEGER NOT NULL DEFAULT 0,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

    @classmethod
    def _migrate_legacy_table(cls, cursor) -> None:
        """旧结构（count + limit_count 当日上限）重建为新结构。

        旧 limit_count 中超出「基础次数 + 图鉴奖励永久加成」的部分还原为
        当日临时增益；永久加成按领取记录推导写入；跨天行计数与临时增益
        清零、永久增益保留。
        """
        if not _table_exists(cursor, "fish_limit"):
            return
        columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(fish_limit)")
        }
        if "temp_bonus" in columns:
            return                       # 已是新结构

        cursor.execute("ALTER TABLE fish_limit RENAME TO fish_limit_legacy")
        cls._create_table(cursor)
        perm_map = _perm_bonus_by_uid(cursor)
        base = _base_limit()
        today = _today()
        rows = cursor.execute(
            "SELECT uid, date_str, count, limit_count FROM fish_limit_legacy"
        ).fetchall()
        for row in rows:
            uid = int(row["uid"])
            perm = perm_map.get(uid, 0)
            if row["date_str"] == today:
                count = int(row["count"] or 0)
                temp = max(0, int(row["limit_count"] or 0) - base - perm)
            else:
                count = temp = 0        # 跨天：当日计数与临时增益清零
            cursor.execute(
                'INSERT INTO fish_limit'
                ' (uid, date_str, count, temp_bonus, perm_bonus)'
                ' VALUES (?, ?, ?, ?, ?)',
                (uid, today, count, temp, perm),
            )
        cursor.execute("DROP TABLE fish_limit_legacy")
        logger.warning(
            f"[fish_limit] 旧表结构已重建（{len(rows)} 行，"
            "临时/永久增益已还原）"
        )

    # ===== 当日状态读写 =====

    @staticmethod
    def _load_state(cursor, uid: int, today: str) -> dict:
        """读取当日状态；跨天（或无行）时已钓次数与临时增益视为 0，
        永久增益保留——重置在下次写入时落库。"""
        row = cursor.execute(
            'SELECT date_str, count, temp_bonus, perm_bonus'
            ' FROM fish_limit WHERE uid = ?',
            (uid,),
        ).fetchone()
        if row is None:
            return {"count": 0, "temp_bonus": 0, "perm_bonus": 0}
        perm = int(row["perm_bonus"] or 0)
        if row["date_str"] != today:
            return {"count": 0, "temp_bonus": 0, "perm_bonus": perm}
        return {
            "count": int(row["count"] or 0),
            "temp_bonus": int(row["temp_bonus"] or 0),
            "perm_bonus": perm,
        }

    @staticmethod
    def _write_state(cursor, uid: int, today: str, count: int,
                     temp_bonus: int, perm_bonus: int) -> None:
        cursor.execute(
            'INSERT INTO fish_limit'
            ' (uid, date_str, count, temp_bonus, perm_bonus)'
            ' VALUES (?, ?, ?, ?, ?)'
            ' ON CONFLICT(uid) DO UPDATE SET'
            ' date_str = excluded.date_str, count = excluded.count,'
            ' temp_bonus = excluded.temp_bonus,'
            ' perm_bonus = excluded.perm_bonus,'
            ' updated_time = CURRENT_TIMESTAMP',
            (uid, today, count, temp_bonus, perm_bonus),
        )

    # ===== 次数消耗与增益 =====

    @classmethod
    def check_and_update_fish_limit(cls, uid: int, count: int) -> bool:
        """消耗当日次数或追加当日临时增益。

        Args:
            uid: 用户ID
            count: 正数为消耗次数（超出当天总上限时返回 False 且不修改）；
                   负数为追加当日临时增益（宠物技能/幸运转盘，恒成功）；
                   0 为空操作

        Returns:
            是否成功更新
        """
        cls.init_database()

        today = _today()
        conn = cls.get_connection()
        cursor = conn.cursor()

        try:
            state = cls._load_state(cursor, uid, today)
            if count > 0:
                limit = _daily_limit(state["temp_bonus"], state["perm_bonus"])
                if state["count"] + count > limit:
                    conn.close()
                    return False
                new_count = state["count"] + count
                new_temp = state["temp_bonus"]
            elif count < 0:
                new_count = state["count"]
                new_temp = state["temp_bonus"] - count
            else:
                conn.close()
                return True

            cls._write_state(
                cursor, uid, today, new_count, new_temp, state["perm_bonus"]
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
    def add_perm_bonus(cls, uid: int, amount: int) -> int:
        """累加永久增益（图鉴奖励领取时调用），返回累加后的值。

        失败返回 0（调用方据此判断是否发放成功）。
        """
        if amount <= 0:
            return 0
        cls.init_database()

        today = _today()
        conn = cls.get_connection()
        cursor = conn.cursor()
        try:
            state = cls._load_state(cursor, uid, today)
            perm = state["perm_bonus"] + int(amount)
            cls._write_state(
                cursor, uid, today, state["count"], state["temp_bonus"], perm
            )
            conn.commit()
            conn.close()
            return perm
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"累加永久钓鱼次数失败: {e}")
            return 0

    @classmethod
    def reconcile_perm_bonus(cls) -> int:
        """按图鉴奖励领取记录 + 当前配置重算全部用户的永久增益，
        仅在与表内值不同时写回，返回更新的用户数。

        初始化 / 钓鱼模块启动时调用：完成历史数据还原，并让配置面板对
        图鉴奖励的修改在重启后对已领取用户生效。领取记录表不存在时为
        空操作（不视为「加成归零」）。
        """
        if cls._db_path is None:
            return 0

        today = _today()
        conn = cls.get_connection()
        cursor = conn.cursor()
        try:
            if not _table_exists(cursor, "fish_limit"):
                conn.close()
                return 0
            expected = _perm_bonus_by_uid(cursor)
            updated = 0
            seen = set()
            rows = cursor.execute(
                'SELECT uid, date_str, count, temp_bonus, perm_bonus'
                ' FROM fish_limit'
            ).fetchall()
            for row in rows:
                uid = int(row["uid"])
                seen.add(uid)
                want = expected.get(uid, 0)
                if int(row["perm_bonus"] or 0) == want:
                    continue
                if row["date_str"] != today:
                    cls._write_state(cursor, uid, today, 0, 0, want)
                else:
                    cls._write_state(
                        cursor, uid, today, int(row["count"] or 0),
                        int(row["temp_bonus"] or 0), want,
                    )
                updated += 1
            # 有领取记录但表中尚无行的用户：补一行（仅永久增益）
            for uid, want in expected.items():
                if uid in seen or want <= 0:
                    continue
                cls._write_state(cursor, uid, today, 0, 0, want)
                updated += 1
            conn.commit()
            conn.close()
            if updated:
                logger.info(f"[fish_limit] 永久增益校正完成：{updated} 个用户")
            return updated
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"[fish_limit] 永久增益校正失败: {e}")
            return 0

    @classmethod
    def refund_today_count(cls, uid: int, count: int = 1) -> bool:
        """退还今日已消耗的钓鱼次数（当日有记录才生效，减到 0 为止）。

        供钓鱼入口在「未实际抛竿」（如还有待处理鱼获、渔具损坏）时回滚
        已扣的次数，保证次数只在真正抛竿时消耗。
        """
        cls.init_database()

        today = _today()
        conn = cls.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'UPDATE fish_limit SET count = MAX(0, count - ?), '
                'updated_time = CURRENT_TIMESTAMP '
                'WHERE uid = ? AND date_str = ?',
                (count, uid, today),
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
        """获取用户今日已钓鱼次数与当天总次数上限。

        Returns:
            (today_count, limit_count)；limit = 基础次数 + 临时增益 + 永久增益
        """
        cls.init_database()

        today = _today()
        conn = cls.get_connection()
        cursor = conn.cursor()
        try:
            state = cls._load_state(cursor, uid, today)
        finally:
            conn.close()
        return state["count"], _daily_limit(
            state["temp_bonus"], state["perm_bonus"]
        )
