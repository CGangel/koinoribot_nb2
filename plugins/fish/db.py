"""新版钓鱼（fish）数据库层。

表结构：
- fish_players:    玩家状态（装备的鱼竿/鱼线实例、使用中的鱼饵、水族箱槽位、统计）
- fish_gear_items: 鱼竿/鱼线实例（含耐久；初始渔具不落库、由 NULL 表示）
- fish_bait_bag:   商店鱼饵库存（uid + bait_id 唯一；初始鱼饵无限不落库）
- fish_items:      鱼实例（背包/水族箱共用，place 区分）
- fish_collection: 图鉴（按品种记录总数、历史最大级别与钓获时最大长度）

约定：沿用全插件统一的 user_uid_mapping 外键级联；连接按次创建，
写操作经 run_in_executor 包装为 async（同旧版 fishing / chongwu）。
"""

import asyncio
import json
import sqlite3
import time
from typing import Optional

from nonebot.log import logger

from .fish_config import DEFAULT_BAIT, aquarium, grade_rank


def _now() -> int:
    return int(time.time())


def _row_to_item(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "uid": row["uid"],
        "species_id": row["species_id"],
        "grade": row["grade"],
        "length": row["length"],
        "caught_length": row["caught_length"],
        "place": row["place"],
        "put_in_time": row["put_in_time"],
        "caught_time": row["caught_time"],
    }


def _row_to_gear(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "uid": row["uid"],
        "kind": row["kind"],
        "gear_id": row["gear_id"],
        "durability": row["durability"],
        "created_time": row["created_time"],
    }


class FishDB:
    """fish 插件数据库管理器"""

    _db_path: Optional[str] = None
    _db_initialized: bool = False

    # ===== 路径与连接 =====

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
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def init_fish_database(cls):
        """初始化新版钓鱼数据库（幂等；旧框架表结构自动重建）"""
        if cls._db_initialized:
            return

        conn = cls.get_connection()
        cursor = conn.cursor()

        cls._drop_legacy_tables(cursor)
        cls._create_tables(cursor)

        conn.commit()
        conn.close()
        cls._db_initialized = True
        logger.info("新版钓鱼数据库初始化完成")

    @staticmethod
    def _drop_legacy_tables(cursor):
        """框架阶段结构变更时丢弃旧 fish_* 表（无线上数据，重建即可）"""
        cursor.execute("PRAGMA table_info(fish_players)")
        columns = {row[1] for row in cursor.fetchall()}
        if columns and ("slots" not in columns or "orb_owned" not in columns
                        or "pump_owned" not in columns
                        or "auto_sell" not in columns):
            for table in (
                "fish_players", "fish_gear_items", "fish_bait_bag",
                "fish_items", "fish_collection",
            ):
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            logger.warning("检测到旧版 fish 框架表结构，已删除重建（测试阶段数据不保留）")

    @staticmethod
    def _create_tables(cursor):
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_players (
                uid INTEGER PRIMARY KEY,
                rod_item_id INTEGER,
                line_item_id INTEGER,
                bait_id TEXT NOT NULL DEFAULT 'bait_basic',
                slots INTEGER NOT NULL DEFAULT 10,
                orb_owned INTEGER NOT NULL DEFAULT 0,
                orb_equipped INTEGER NOT NULL DEFAULT 0,
                orb_level INTEGER NOT NULL DEFAULT 1,
                orb_energy INTEGER NOT NULL DEFAULT 0,
                pump_owned INTEGER NOT NULL DEFAULT 0,
                auto_sell INTEGER NOT NULL DEFAULT 0,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_gear_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL,
                kind TEXT NOT NULL,
                gear_id TEXT NOT NULL,
                durability INTEGER NOT NULL,
                created_time INTEGER NOT NULL,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_bait_bag (
                uid INTEGER NOT NULL,
                bait_id TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, bait_id),
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL,
                species_id TEXT NOT NULL,
                grade TEXT NOT NULL,
                length REAL NOT NULL,
                caught_length REAL NOT NULL,
                place TEXT NOT NULL DEFAULT 'bag',
                put_in_time INTEGER,
                caught_time INTEGER NOT NULL,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fish_collection (
                uid INTEGER NOT NULL,
                species_id TEXT NOT NULL,
                total_count INTEGER NOT NULL DEFAULT 0,
                max_grade TEXT NOT NULL,
                max_length REAL NOT NULL,
                first_caught_time INTEGER NOT NULL,
                updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (uid, species_id),
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid) ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

    # ===== 玩家 =====

    @classmethod
    async def get_player(cls, uid: int) -> Optional[dict]:
        """获取玩家状态，不存在返回 None"""
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT uid, rod_item_id, line_item_id, bait_id, slots,'
                ' orb_owned, orb_equipped, orb_level, orb_energy, pump_owned,'
                ' auto_sell'
                ' FROM fish_players WHERE uid = ?',
                (uid,),
            )
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return None
            return {
                "uid": row["uid"],
                "rod_item_id": row["rod_item_id"],
                "line_item_id": row["line_item_id"],
                "bait_id": row["bait_id"],
                "slots": row["slots"],
                "orb_owned": row["orb_owned"],
                "orb_equipped": row["orb_equipped"],
                "orb_level": row["orb_level"],
                "orb_energy": row["orb_energy"],
                "pump_owned": row["pump_owned"],
                "auto_sell": row["auto_sell"],
            }

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def create_player(cls, uid: int) -> dict:
        """创建玩家初始状态：初始鱼竿/鱼线（无耐久、NULL 实例）+ 初始鱼饵（无限）；
        幸运宝珠未拥有、未装备"""
        cls.init_fish_database()
        player = {
            "uid": uid,
            "rod_item_id": None,
            "line_item_id": None,
            "bait_id": DEFAULT_BAIT,
            "slots": aquarium()["initial_slots"],
            "orb_owned": 0,
            "orb_equipped": 0,
            "orb_level": 1,
            "orb_energy": 0,
            "pump_owned": 0,
            "auto_sell": 0,
        }

        def _create():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR IGNORE INTO fish_players'
                ' (uid, rod_item_id, line_item_id, bait_id, slots,'
                '  orb_owned, orb_equipped, orb_level, orb_energy, pump_owned,'
                '  auto_sell)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (uid, None, None, player["bait_id"], player["slots"],
                 player["orb_owned"], player["orb_equipped"],
                 player["orb_level"], player["orb_energy"],
                 player["pump_owned"], player["auto_sell"]),
            )
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _create)
        return player

    @classmethod
    async def save_player(cls, player: dict):
        """保存玩家状态（整行覆盖）"""
        cls.init_fish_database()

        def _save():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE fish_players SET rod_item_id = ?, line_item_id = ?,'
                ' bait_id = ?, slots = ?, orb_owned = ?, orb_equipped = ?,'
                ' orb_level = ?, orb_energy = ?, pump_owned = ?, auto_sell = ?,'
                ' updated_time = CURRENT_TIMESTAMP WHERE uid = ?',
                (player["rod_item_id"], player["line_item_id"], player["bait_id"],
                 player["slots"], player["orb_owned"], player["orb_equipped"],
                 player["orb_level"], player["orb_energy"], player["pump_owned"],
                 player["auto_sell"], player["uid"]),
            )
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _save)

    # ===== 渔具（鱼竿/鱼线实例） =====

    @classmethod
    async def add_gear(cls, uid: int, kind: str, gear_id: str, durability: int) -> int:
        """购买渔具生成实例，返回实例 id"""
        cls.init_fish_database()

        def _add():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO fish_gear_items (uid, kind, gear_id, durability, created_time)'
                ' VALUES (?, ?, ?, ?, ?)',
                (uid, kind, gear_id, durability, _now()),
            )
            gear_id_new = cursor.lastrowid
            conn.commit()
            conn.close()
            return gear_id_new

        return await asyncio.get_event_loop().run_in_executor(None, _add)

    @classmethod
    async def get_gear(cls, gear_item_id: int, uid: Optional[int] = None) -> Optional[dict]:
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM fish_gear_items WHERE id = ?', (gear_item_id,),
            )
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return None
            if uid is not None and row["uid"] != uid:
                return None
            return _row_to_gear(row)

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def list_gear(cls, uid: int, kind: Optional[str] = None) -> list[dict]:
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            if kind is None:
                cursor.execute(
                    'SELECT * FROM fish_gear_items WHERE uid = ? ORDER BY id', (uid,),
                )
            else:
                cursor.execute(
                    'SELECT * FROM fish_gear_items WHERE uid = ? AND kind = ? ORDER BY id',
                    (uid, kind),
                )
            rows = cursor.fetchall()
            conn.close()
            return [_row_to_gear(row) for row in rows]

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def update_gear(cls, gear_item_id: int, durability: int):
        """更新渔具耐久"""
        cls.init_fish_database()

        def _update():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE fish_gear_items SET durability = ? WHERE id = ?',
                (durability, gear_item_id),
            )
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _update)

    # ===== 鱼饵 =====

    @classmethod
    async def get_bait_count(cls, uid: int, bait_id: str) -> int:
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT count FROM fish_bait_bag WHERE uid = ? AND bait_id = ?',
                (uid, bait_id),
            )
            row = cursor.fetchone()
            conn.close()
            return row["count"] if row else 0

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def add_bait(cls, uid: int, bait_id: str, num: int):
        """增加鱼饵库存"""
        cls.init_fish_database()

        def _add():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO fish_bait_bag (uid, bait_id, count) VALUES (?, ?, ?)'
                ' ON CONFLICT(uid, bait_id) DO UPDATE SET'
                ' count = count + excluded.count, updated_time = CURRENT_TIMESTAMP',
                (uid, bait_id, num),
            )
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _add)

    @classmethod
    async def consume_bait(cls, uid: int, bait_id: str, num: int = 1) -> bool:
        """原子消耗鱼饵，库存不足返回 False（不修改）"""
        cls.init_fish_database()

        def _consume():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE fish_bait_bag SET count = count - ?, updated_time = CURRENT_TIMESTAMP'
                ' WHERE uid = ? AND bait_id = ? AND count >= ?',
                (num, uid, bait_id, num),
            )
            changed = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return changed

        return await asyncio.get_event_loop().run_in_executor(None, _consume)

    # ===== 鱼实例 =====

    @classmethod
    async def add_fish_item(
        cls, uid: int, species_id: str, grade: str, length: float,
        place: str = "pending",
    ) -> int:
        """钓获入待处理区（pending，等待 卖鱼/放生/放入水族箱），返回鱼实例 id"""
        cls.init_fish_database()
        now = _now()

        def _add():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO fish_items'
                ' (uid, species_id, grade, length, caught_length, place, caught_time)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?)',
                (uid, species_id, grade, length, length, place, now),
            )
            item_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return item_id

        return await asyncio.get_event_loop().run_in_executor(None, _add)

    @classmethod
    async def get_pending_item(cls, uid: int) -> Optional[dict]:
        """获取待处理的鱼（钓获后未 卖鱼/放生/放入水族箱，最多一条）"""
        items = await cls.list_fish_items(uid, place="pending")
        return items[0] if items else None

    @classmethod
    async def get_fish_item(cls, item_id: int, uid: Optional[int] = None) -> Optional[dict]:
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM fish_items WHERE id = ?', (item_id,))
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return None
            if uid is not None and row["uid"] != uid:
                return None
            return _row_to_item(row)

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def list_fish_items(cls, uid: int, place: Optional[str] = None) -> list[dict]:
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            if place is None:
                cursor.execute(
                    'SELECT * FROM fish_items WHERE uid = ? ORDER BY id', (uid,),
                )
            else:
                cursor.execute(
                    'SELECT * FROM fish_items WHERE uid = ? AND place = ? ORDER BY id',
                    (uid, place),
                )
            rows = cursor.fetchall()
            conn.close()
            return [_row_to_item(row) for row in rows]

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def update_fish_item(cls, item_id: int, **fields):
        """更新鱼实例字段（length/place/put_in_time）"""
        cls.init_fish_database()
        if not fields:
            return
        allowed = {"length", "place", "put_in_time"}
        columns = [key for key in fields if key in allowed]
        if not columns:
            return
        assignments = ", ".join(f"{key} = ?" for key in columns)
        parameters = [fields[key] for key in columns] + [item_id]

        def _update():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                f'UPDATE fish_items SET {assignments} WHERE id = ?', parameters,
            )
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _update)

    @classmethod
    async def delete_fish_item(cls, item_id: int):
        cls.init_fish_database()

        def _delete():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM fish_items WHERE id = ?', (item_id,))
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _delete)

    # ===== 图鉴 =====

    @classmethod
    async def update_collection(
        cls, uid: int, species_id: str, grade: str, length: float
    ) -> dict:
        """钓获时更新图鉴（读改写事务）。

        只按钓获时的 grade/length 统计历史最大值；水族箱养成后的增长
        不允许走这里。返回图鉴更新摘要：
        {"new_unlock", "grade_improved", "old_grade", "new_grade",
         "length_improved", "old_length", "new_length"}
        首次钓到只标记 new_unlock，不算刷新纪录。
        """
        cls.init_fish_database()
        now = _now()

        def _update():
            conn = cls.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT total_count, max_grade, max_length FROM fish_collection'
                    ' WHERE uid = ? AND species_id = ?',
                    (uid, species_id),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        'INSERT INTO fish_collection'
                        ' (uid, species_id, total_count, max_grade, max_length, first_caught_time)'
                        ' VALUES (?, ?, 1, ?, ?, ?)',
                        (uid, species_id, grade, length, now),
                    )
                    conn.commit()
                    return {
                        "new_unlock": True,
                        "grade_improved": False,
                        "old_grade": None,
                        "new_grade": grade,
                        "length_improved": False,
                        "old_length": None,
                        "new_length": length,
                    }

                grade_up = grade_rank(grade) > grade_rank(row["max_grade"])
                length_up = length > row["max_length"]
                cursor.execute(
                    'UPDATE fish_collection SET total_count = total_count + 1,'
                    ' max_grade = ?, max_length = ?, updated_time = CURRENT_TIMESTAMP'
                    ' WHERE uid = ? AND species_id = ?',
                    (
                        grade if grade_up else row["max_grade"],
                        max(length, row["max_length"]),
                        uid,
                        species_id,
                    ),
                )
                conn.commit()
                return {
                    "new_unlock": False,
                    "grade_improved": grade_up,
                    "old_grade": row["max_grade"],
                    "new_grade": grade,
                    "length_improved": length_up,
                    "old_length": row["max_length"],
                    "new_length": length,
                }
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        return await asyncio.get_event_loop().run_in_executor(None, _update)

    @classmethod
    async def get_collection(cls, uid: int) -> list[dict]:
        cls.init_fish_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT species_id, total_count, max_grade, max_length, first_caught_time'
                ' FROM fish_collection WHERE uid = ? ORDER BY species_id',
                (uid,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

        return await asyncio.get_event_loop().run_in_executor(None, _query)
