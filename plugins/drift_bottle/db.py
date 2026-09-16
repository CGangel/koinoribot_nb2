"""漂流瓶（drift_bottle）数据库层。

自旧版钓鱼插件完整迁出、与钓鱼玩法解耦：bottles / bottle_comments 两张
表沿用旧版结构（原库中的漂流瓶数据无缝保留，ID 继续从 10001 自增），
新增 bottle_inventory 表保存每人的漂流瓶持有数（旧版存于钓鱼背包
JSON，随旧版移除而废弃）。

约定：沿用全插件统一的 user_uid_mapping 外键级联；连接按次创建，
读写经 run_in_executor 包装为 async（同 fish / chongwu）。
"""

import asyncio
import sqlite3
import time
from typing import Optional, Tuple

from nonebot.log import logger

# 扔漂流瓶 / 评论的内容长度上限（沿用旧版）
MAX_CONTENT_LEN = 60
MAX_COMMENT_LEN = 20
# 捡漂流瓶要求池中至少有多少个瓶子（沿用旧版）
MIN_PICK_POOL = 5


def _now() -> int:
    return int(time.time())


def _row_to_comment(row: sqlite3.Row) -> dict:
    return {"uid": row["uid"], "content": row["content"], "time": row["created_time"]}


class BottleDB:
    """drift_bottle 插件数据库管理器"""

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
    def init_bottle_database(cls):
        """初始化漂流瓶数据库（幂等；与旧版插件共用同一库文件）"""
        if cls._db_initialized:
            return

        conn = cls.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL,
                content TEXT NOT NULL,
                pick_count INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                created_time INTEGER NOT NULL,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid)
                    ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottle_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bottle_id INTEGER NOT NULL,
                uid INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_time INTEGER NOT NULL,
                FOREIGN KEY (bottle_id) REFERENCES bottles(id) ON DELETE CASCADE,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid)
                    ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottle_inventory (
                uid INTEGER PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid)
                    ON UPDATE CASCADE ON DELETE CASCADE
            )
        ''')

        # 旧版钓鱼的 JSON 存档表已随旧版移除，漂流瓶接管旧库时顺带清理
        cursor.execute("DROP TABLE IF EXISTS fishing")

        # 确保 bottles 的 AUTOINCREMENT 从 10001 开始（沿用旧版种子）
        cursor.execute("SELECT COUNT(*) FROM bottles")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT OR IGNORE INTO sqlite_sequence (name, seq) "
                "VALUES ('bottles', 10000)"
            )

        conn.commit()
        conn.close()
        cls._db_initialized = True
        logger.info("漂流瓶数据库初始化完成")

    # ===== 持有数 =====

    @classmethod
    async def get_inventory(cls, uid: int) -> int:
        cls.init_bottle_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count FROM bottle_inventory WHERE uid = ?", (uid,)
            )
            row = cursor.fetchone()
            conn.close()
            return row["count"] if row else 0

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def add_inventory(cls, uid: int, num: int):
        cls.init_bottle_database()

        def _add():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO bottle_inventory (uid, count) VALUES (?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET count = count + ?",
                (uid, num, num),
            )
            conn.commit()
            conn.close()

        await asyncio.get_event_loop().run_in_executor(None, _add)

    @classmethod
    async def consume_inventory(cls, uid: int, num: int = 1) -> bool:
        """扣持有数，不足时不扣并返回 False"""
        cls.init_bottle_database()

        def _consume():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bottle_inventory SET count = count - ? "
                "WHERE uid = ? AND count >= ?",
                (num, uid, num),
            )
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            return affected > 0

        return await asyncio.get_event_loop().run_in_executor(None, _consume)

    # ===== 瓶子 =====

    @classmethod
    async def create_bottle(cls, uid: int, content: str) -> str:
        """创建漂流瓶，返回实际 ID（自增，从 10001 起）"""
        cls.init_bottle_database()

        def _create():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO bottles (uid, content, created_time) VALUES (?, ?, ?)",
                (uid, content, _now()),
            )
            real_id = str(cursor.lastrowid)
            conn.commit()
            conn.close()
            return real_id

        return await asyncio.get_event_loop().run_in_executor(None, _create)

    @classmethod
    async def get_bottle_amount(cls) -> int:
        """有效漂流瓶数量（未删除）"""
        cls.init_bottle_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bottles WHERE deleted = 0")
            count = cursor.fetchone()[0]
            conn.close()
            return count

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def pick_random_bottle(cls) -> Tuple[Optional[str], Optional[dict]]:
        """随机捞取一个未删除漂流瓶（含评论），并累计被捞起次数"""
        cls.init_bottle_database()

        def _pick():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, uid, content, pick_count, created_time "
                "FROM bottles WHERE deleted = 0 ORDER BY RANDOM() LIMIT 1"
            )
            row = cursor.fetchone()
            if row is None:
                conn.close()
                return None, None

            new_pick = row["pick_count"] + 1
            cursor.execute(
                "UPDATE bottles SET pick_count = ? WHERE id = ?",
                (new_pick, row["id"]),
            )
            cursor.execute(
                "SELECT uid, content, created_time FROM bottle_comments "
                "WHERE bottle_id = ? ORDER BY created_time ASC",
                (row["id"],),
            )
            comments = [_row_to_comment(c) for c in cursor.fetchall()]
            conn.commit()
            conn.close()

            bottle = {
                "uid": row["uid"],
                "content": row["content"],
                "pick_count": new_pick,
                "time": row["created_time"],
                "comments": comments,
            }
            return str(row["id"]), bottle

        return await asyncio.get_event_loop().run_in_executor(None, _pick)

    @classmethod
    async def add_comment(cls, bottle_id: int, uid: int, content: str) -> bool:
        """给未删除的漂流瓶添加评论；瓶子不存在返回 False"""
        cls.init_bottle_database()

        def _add():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM bottles WHERE id = ? AND deleted = 0",
                (bottle_id,),
            )
            if cursor.fetchone() is None:
                conn.close()
                return False
            cursor.execute(
                "INSERT INTO bottle_comments (bottle_id, uid, content, created_time) "
                "VALUES (?, ?, ?, ?)",
                (bottle_id, uid, content, _now()),
            )
            conn.commit()
            conn.close()
            return True

        return await asyncio.get_event_loop().run_in_executor(None, _add)

    @classmethod
    async def get_bottle_by_id(cls, bottle_id: int) -> Optional[dict]:
        """按 ID 查询漂流瓶（含评论，不过滤 deleted；SU 查看用）"""
        cls.init_bottle_database()

        def _query():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, uid, content, pick_count, deleted, created_time "
                "FROM bottles WHERE id = ?",
                (bottle_id,),
            )
            row = cursor.fetchone()
            if row is None:
                conn.close()
                return None
            cursor.execute(
                "SELECT uid, content, created_time FROM bottle_comments "
                "WHERE bottle_id = ? ORDER BY created_time ASC",
                (row["id"],),
            )
            comments = [_row_to_comment(c) for c in cursor.fetchall()]
            conn.close()
            return {
                "id": str(row["id"]),
                "uid": row["uid"],
                "content": row["content"],
                "pick_count": row["pick_count"],
                "deleted": row["deleted"],
                "time": row["created_time"],
                "comments": comments,
            }

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    @classmethod
    async def delete_bottle(cls, bottle_id: int) -> bool:
        """软删除漂流瓶"""
        cls.init_bottle_database()

        def _delete():
            conn = cls.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bottles SET deleted = 1 WHERE id = ? AND deleted = 0",
                (bottle_id,),
            )
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            return affected > 0

        return await asyncio.get_event_loop().run_in_executor(None, _delete)
