"""mlai 存储层：单行 JSON 的记忆库与临时上下文。"""


import asyncio
import json
import sqlite3
import time
from typing import Optional

from nonebot.log import logger

SCOPE_GLOBAL = "global"
SCOPE_GROUP = "group"
SCOPE_USER = "user"
SCOPES = (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_USER)

KIND_GROUP = "group"
KIND_USER = "user"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# 全局写锁：所有变更都是整行读-改-写，须串行（个人 bot 规模下锁
# 竞争可忽略）
_store_lock = asyncio.Lock()


def _now() -> int:
    """毫秒时间戳：updated_time 兼作活跃边界，毫秒精度保证「修改后
    重新进入活跃窗口」在同秒内的多次操作也能正确排序。"""
    return int(time.time() * 1000)


def _sanitize_term(term: str) -> str:
    """关键词清理：去空白与 LIKE 遗留通配符，限制长度。"""
    text = str(term or "").strip().replace("%", "").replace("_", "")
    return text[:32]


def _parse_list(raw) -> list:
    """行内 JSON 安全解析：坏数据按空列表处理。"""
    try:
        data = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


class MlaiStore:
    """mlai 记忆库与临时上下文存储（单行 JSON）"""

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
            raise RuntimeError("数据库路径未设置，请先调用 MlaiStore.set_db_path()")
        conn = sqlite3.connect(cls._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @classmethod
    def _vacuum_sync(cls) -> int:
        """VACUUM 重整库文件，返回回收的页数。

        单行 JSON 整行重写（含内联图片）会持续换页，被淘汰数据的页
        进入 freelist 但 SQLite 不会自动归还操作系统——文件只涨不缩，
        需定期 VACUUM。空闲不足 1MB 时跳过（VACUUM 本身要重写全库）。
        """
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            if free * page_size < 1024 * 1024:
                return 0
            before = conn.execute("PRAGMA page_count").fetchone()[0]
            conn.isolation_level = None
            conn.execute("VACUUM")
            after = conn.execute("PRAGMA page_count").fetchone()[0]
            return before - after
        finally:
            conn.close()

    @classmethod
    def init_database(cls):
        """建表并把旧版多行表聚合迁移为单行 JSON。"""
        if cls._db_initialized:
            return
        conn = cls.get_connection()
        try:
            cursor = conn.cursor()
            pending_mem = cls._read_and_drop_legacy_memories(cursor)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mlai_memories (
                    scope TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (scope, scope_key)
                )
            """)
            if pending_mem:
                cursor.executemany(
                    "INSERT OR REPLACE INTO mlai_memories "
                    "(scope, scope_key, data) VALUES (?, ?, ?)",
                    [
                        (scope, key, json.dumps(items, ensure_ascii=False))
                        for scope, key, items in pending_mem
                    ],
                )
            pending_temp = cls._read_and_drop_legacy_temp(cursor)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mlai_temp_context (
                    kind TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (kind, owner)
                )
            """)
            if pending_temp:
                cursor.executemany(
                    "INSERT OR REPLACE INTO mlai_temp_context "
                    "(kind, owner, data) VALUES (?, ?, ?)",
                    [
                        (kind, owner, json.dumps(items, ensure_ascii=False))
                        for kind, owner, items in pending_temp
                    ],
                )
            cursor.execute("DROP TABLE IF EXISTS mlai_messages")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mlai_chat_switch (
                    owner TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()
        cls._db_initialized = True

    @classmethod
    def _read_and_drop_legacy_memories(cls, cursor) -> list:
        """旧多行 mlai_memories → [(scope, key, items)]，随后删旧表；
        非旧表结构（不存在或已是单行 JSON）返回 []。"""
        exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='mlai_memories'"
        ).fetchone()
        if exists is None:
            return []
        cols = {row[1] for row in cursor.execute(
            "PRAGMA table_info(mlai_memories)"
        )}
        if "content" not in cols:  # 已是新表
            return []
        legacy = cursor.execute(
            "SELECT scope, scope_key, id, content, keywords, "
            "created_time, updated_time FROM mlai_memories ORDER BY id"
        ).fetchall()
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in legacy:
            items = grouped.setdefault((row[0], str(row[1] or "")), [])
            items.append({
                "content": row[3],
                "keywords": str(row[4] or ""),
                "created_time": int(row[5]), "updated_time": int(row[6]),
            })
        cursor.execute("DROP TABLE mlai_memories")
        # 旧全局 id 改为域内编号：各域按原顺序重编为 1..N
        renumbered = []
        for (scope, key), items in grouped.items():
            for idx, item in enumerate(items, start=1):
                item["id"] = idx
            renumbered.append((scope, key, items))
        if legacy:
            logger.info(
                f"[mlai] 旧多行记忆表已迁移为单行 JSON"
                f"（{len(legacy)} 条 → {len(grouped)} 行，id 已改为域内编号）"
            )
        return renumbered

    @classmethod
    def _read_and_drop_legacy_temp(cls, cursor) -> list:
        """旧多行 mlai_temp_context → [(kind, owner, items)]，随后删旧表。"""
        exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='mlai_temp_context'"
        ).fetchone()
        if exists is None:
            return []
        cols = {row[1] for row in cursor.execute(
            "PRAGMA table_info(mlai_temp_context)"
        )}
        if "role" not in cols:  # 已是新表
            return []
        legacy = cursor.execute(
            "SELECT kind, owner, role, content FROM mlai_temp_context "
            "ORDER BY id"
        ).fetchall()
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in legacy:
            items = grouped.setdefault((row[0], str(row[1])), [])
            items.append({
                "seq": len(items) + 1,
                "time": 0,
                "role": row[2],
                "name": "",
                "content": row[3],
                "images": [],
            })
        cursor.execute("DROP TABLE mlai_temp_context")
        if legacy:
            logger.info(
                f"[mlai] 旧多行临时上下文表已迁移为单行 JSON"
                f"（{len(legacy)} 条 → {len(grouped)} 行）"
            )
        return [(s, k, v) for (s, k), v in grouped.items()]

    @classmethod
    def _ensure_initialized(cls):
        if not cls._db_initialized:
            cls.init_database()

    # ===== 记忆库：同步核心（经 run_in_executor 调用）=====

    @classmethod
    def _scope_filter(cls, scope: str, scope_key: str) -> tuple[str, str]:
        """user/group 域限定 scope_key；全局域忽略 scope_key。"""
        if scope == SCOPE_GLOBAL:
            return SCOPE_GLOBAL, ""
        return scope, str(scope_key)

    @classmethod
    def _load_memories_sync(cls, conn, scope: str, scope_key: str) -> list[dict]:
        row = conn.execute(
            "SELECT data FROM mlai_memories WHERE scope = ? AND scope_key = ?",
            cls._scope_filter(scope, scope_key),
        ).fetchone()
        return _parse_list(row[0] if row else None)

    @classmethod
    def _save_memories_sync(cls, conn, scope, scope_key, items: list[dict]):
        scope, scope_key = cls._scope_filter(scope, scope_key)
        if items:
            conn.execute(
                "INSERT INTO mlai_memories (scope, scope_key, data) "
                "VALUES (?, ?, ?) ON CONFLICT(scope, scope_key) "
                "DO UPDATE SET data = excluded.data",
                (scope, scope_key, json.dumps(items, ensure_ascii=False)),
            )
        else:
            conn.execute(
                "DELETE FROM mlai_memories WHERE scope = ? AND scope_key = ?",
                (scope, scope_key),
            )

    @classmethod
    def _find_memory_sync(cls, conn, scope, scope_key, mem_id):
        """在指定域内定位记忆：返回 (items, index) 或 None（id 域内
        唯一，跨域可能撞号，调用方必须连同域一起定位）。"""
        items = cls._load_memories_sync(conn, scope, scope_key)
        for idx, m in enumerate(items):
            if m.get("id") == int(mem_id):
                return items, idx
        return None

    @classmethod
    def _add_memory_sync(
        cls, scope: str, scope_key: str, content: str, keywords: str
    ) -> Optional[int]:
        """新增记忆；同域已有相同内容的记忆则跳过（返回 None）。
        id 在所属域内独立自增（各群/各用户/全局各自从 1 开始）。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            items = cls._load_memories_sync(conn, scope, scope_key)
            if any(m.get("content") == str(content).strip() for m in items):
                return None
            now = _now()
            mem_id = max((m.get("id", 0) for m in items), default=0) + 1
            items.append({
                "id": mem_id,
                "content": str(content).strip(),
                "keywords": str(keywords or "").strip(),
                "created_time": now,
                "updated_time": now,
            })
            cls._save_memories_sync(conn, scope, scope_key, items)
            conn.commit()
            return mem_id
        finally:
            conn.close()

    @classmethod
    def _fetch_active_sync(cls, scope, scope_key, limit) -> list[dict]:
        """活跃记忆：按更新时间最近 limit 条（时间升序返回）。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            items = cls._load_memories_sync(conn, scope, scope_key)
        finally:
            conn.close()
        if limit <= 0:
            return []
        ordered = _sorted_by_update(items)
        return [
            {"id": m["id"], "content": m["content"],
             "keywords": m.get("keywords", "")}
            for m in list(reversed(ordered[:limit]))
        ]

    @classmethod
    def _search_archived_sync(
        cls, scope, scope_key, terms, limit, active_limit
    ) -> list[dict]:
        """归档检索：content/keywords 含任一关键词（大小写不敏感），
        排除活跃窗口内条目，按更新时间新→旧取 limit 条。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            items = cls._load_memories_sync(conn, scope, scope_key)
        finally:
            conn.close()
        terms = [t for t in (_sanitize_term(t) for t in terms) if t]
        if not terms or limit <= 0:
            return []
        ordered = _sorted_by_update(items)
        active_ids = (
            {m["id"] for m in ordered[:active_limit]} if active_limit > 0
            else set()
        )
        lowered = [t.lower() for t in terms]
        hits: list[dict] = []
        for m in ordered:
            if m["id"] in active_ids:
                continue
            hay = (
                str(m.get("content", "")) + "\n" + str(m.get("keywords", ""))
            ).lower()
            if any(t in hay for t in lowered):
                hits.append({
                    "id": m["id"], "content": m["content"],
                    "keywords": m.get("keywords", ""),
                })
                if len(hits) >= limit:
                    break
        return hits

    @classmethod
    def _get_memory_sync(cls, scope, scope_key, mem_id) -> Optional[dict]:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            found = cls._find_memory_sync(conn, scope, scope_key, mem_id)
            if found is None:
                return None
            items, idx = found
            m = items[idx]
            return {
                "id": m["id"], "scope": scope, "scope_key": str(scope_key),
                "content": m["content"], "keywords": m.get("keywords", ""),
            }
        finally:
            conn.close()

    @classmethod
    def _update_memory_sync(cls, scope, scope_key, mem_id, content, keywords) -> bool:
        """修改记忆内容/关键词；更新时间前移（重新进入活跃窗口）。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            found = cls._find_memory_sync(conn, scope, scope_key, mem_id)
            if found is None:
                return False
            items, idx = found
            items[idx].update({
                "content": str(content).strip(),
                "keywords": str(keywords or "").strip(),
                "updated_time": _now(),
            })
            cls._save_memories_sync(conn, scope, scope_key, items)
            conn.commit()
            return True
        finally:
            conn.close()

    @classmethod
    def _delete_memory_sync(cls, scope, scope_key, mem_id) -> bool:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            found = cls._find_memory_sync(conn, scope, scope_key, mem_id)
            if found is None:
                return False
            items, idx = found
            items.pop(idx)
            cls._save_memories_sync(conn, scope, scope_key, items)
            conn.commit()
            return True
        finally:
            conn.close()

    @classmethod
    def _count_memories_sync(cls, scope, scope_key) -> int:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            return len(cls._load_memories_sync(conn, scope, scope_key))
        finally:
            conn.close()

    @classmethod
    def _clear_memories_sync(cls, scope, scope_key) -> int:
        """清空某域全部记忆，返回删除条数。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            count = len(cls._load_memories_sync(conn, scope, scope_key))
            if count:
                conn.execute(
                    "DELETE FROM mlai_memories WHERE scope = ? AND scope_key = ?",
                    cls._scope_filter(scope, scope_key),
                )
                conn.commit()
            return count
        finally:
            conn.close()

    @classmethod
    def _clear_all_memories_sync(cls) -> int:
        """清空全部记忆（全局/群聊/用户），返回删除条数。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            total = sum(
                len(_parse_list(row[0]))
                for row in conn.execute("SELECT data FROM mlai_memories")
            )
            if total:
                conn.execute("DELETE FROM mlai_memories")
                conn.commit()
            return total
        finally:
            conn.close()

    # ===== 临时上下文：同步核心 =====

    @classmethod
    def _load_temp_sync(cls, conn, kind: str, owner) -> list[dict]:
        row = conn.execute(
            "SELECT data FROM mlai_temp_context WHERE kind = ? AND owner = ?",
            (kind, str(owner)),
        ).fetchone()
        return _parse_list(row[0] if row else None)

    @classmethod
    def _append_message_sync(
        cls, kind, owner, role, name, content, images, keep
    ) -> int:
        """追加单条消息（旁听/对话/bot 回复统一入口），滚动保留最近
        keep 条；返回该条消息的 seq（行内单调递增）。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            items = cls._load_temp_sync(conn, kind, owner)
            seq = max((it.get("seq", 0) for it in items), default=0) + 1
            items.append({
                "seq": seq,
                "time": _now(),
                "role": role,
                "name": str(name or ""),
                "content": content,
                "images": [str(u) for u in images or []],
            })
            items = items[-max(1, keep):]
            conn.execute(
                "INSERT INTO mlai_temp_context (kind, owner, data) "
                "VALUES (?, ?, ?) ON CONFLICT(kind, owner) "
                "DO UPDATE SET data = excluded.data",
                (kind, str(owner), json.dumps(items, ensure_ascii=False)),
            )
            conn.commit()
            return seq
        finally:
            conn.close()

    @classmethod
    def _fetch_temp_sync(cls, kind, owner, limit, exclude_seq=None) -> list[dict]:
        """读取最近 limit 条（时间升序）；exclude_seq 起之后的条目
        （含）不返回——对话路径先记录当前消息再取上下文时排除自身。"""
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            items = cls._load_temp_sync(conn, kind, owner)
        finally:
            conn.close()
        if exclude_seq is not None:
            items = [it for it in items if it.get("seq", 0) < exclude_seq]
        limit = int(limit)
        if limit <= 0:
            return []
        return items[-limit:]

    @classmethod
    def _clear_temp_sync(cls, kind, owner) -> int:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            count = len(cls._load_temp_sync(conn, kind, owner))
            if count:
                conn.execute(
                    "DELETE FROM mlai_temp_context WHERE kind = ? AND owner = ?",
                    (kind, str(owner)),
                )
                conn.commit()
            return count
        finally:
            conn.close()

    @classmethod
    def _count_temp_sync(cls, kind, owner) -> int:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            return len(cls._load_temp_sync(conn, kind, owner))
        finally:
            conn.close()

    # ===== 聊天开关（每用户分场景存储，默认关闭） =====

    @classmethod
    def _get_switch_sync(cls, owner) -> bool:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            row = conn.execute(
                "SELECT enabled FROM mlai_chat_switch WHERE owner = ?",
                (str(owner),),
            ).fetchone()
            return bool(row["enabled"]) if row else False
        finally:
            conn.close()

    @classmethod
    def _set_switch_sync(cls, owner, enabled: bool) -> None:
        cls._ensure_initialized()
        conn = cls.get_connection()
        try:
            conn.execute(
                "INSERT INTO mlai_chat_switch (owner, enabled) VALUES (?, ?) "
                "ON CONFLICT(owner) DO UPDATE SET enabled = excluded.enabled",
                (str(owner), 1 if enabled else 0),
            )
            conn.commit()
        finally:
            conn.close()

    # ===== async 接口（统一异常兜底：存储失败不影响对话主流程）=====
    # 变更操作全部经 _store_lock 串行：整行读-改-写，群聊/全局域
    # 跨用户共享同一行，无锁并发会互相覆盖丢更新

    @classmethod
    async def _run(cls, sync_fn, *args, default=None):
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, sync_fn, *args
            )
        except Exception as e:
            logger.error(f"[mlai] 存储操作失败 {sync_fn.__name__}: {e}")
            return default

    @classmethod
    async def add_memory(cls, scope, scope_key, content, keywords) -> Optional[int]:
        async with _store_lock:
            return await cls._run(
                cls._add_memory_sync, scope, scope_key, content, keywords
            )

    @classmethod
    async def active_memories(cls, scope, scope_key, limit) -> list[dict]:
        return await cls._run(
            cls._fetch_active_sync, scope, scope_key, limit, default=[]
        ) or []

    @classmethod
    async def search_archived(
        cls, scope, scope_key, terms, limit, active_limit
    ) -> list[dict]:
        return await cls._run(
            cls._search_archived_sync, scope, scope_key, terms, limit, active_limit,
            default=[],
        ) or []

    @classmethod
    async def get_memory(cls, scope, scope_key, mem_id) -> Optional[dict]:
        return await cls._run(cls._get_memory_sync, scope, scope_key, mem_id)

    @classmethod
    async def update_memory(cls, scope, scope_key, mem_id, content, keywords) -> bool:
        async with _store_lock:
            result = await cls._run(
                cls._update_memory_sync, scope, scope_key, mem_id,
                content, keywords, default=False,
            )
        return bool(result)

    @classmethod
    async def delete_memory(cls, scope, scope_key, mem_id) -> bool:
        async with _store_lock:
            result = await cls._run(
                cls._delete_memory_sync, scope, scope_key, mem_id,
                default=False,
            )
        return bool(result)

    @classmethod
    async def count_memories(cls, scope, scope_key) -> int:
        return await cls._run(
            cls._count_memories_sync, scope, scope_key, default=0
        ) or 0

    @classmethod
    async def clear_memories(cls, scope, scope_key) -> int:
        async with _store_lock:
            return await cls._run(
                cls._clear_memories_sync, scope, scope_key, default=0
            ) or 0

    @classmethod
    async def clear_all_memories(cls) -> int:
        async with _store_lock:
            return await cls._run(cls._clear_all_memories_sync, default=0) or 0

    @classmethod
    async def append_message(
        cls, kind, owner, role, name, content, keep, images=None
    ) -> int:
        async with _store_lock:
            return await cls._run(
                cls._append_message_sync, kind, owner, role, name,
                content, images, keep, default=0,
            ) or 0

    @classmethod
    async def get_temp(cls, kind, owner, limit, exclude_seq=None) -> list[dict]:
        return await cls._run(
            cls._fetch_temp_sync, kind, owner, limit, exclude_seq, default=[]
        ) or []

    @classmethod
    async def clear_temp(cls, kind, owner) -> int:
        async with _store_lock:
            return await cls._run(cls._clear_temp_sync, kind, owner, default=0) or 0

    @classmethod
    async def count_temp(cls, kind, owner) -> int:
        return await cls._run(cls._count_temp_sync, kind, owner, default=0) or 0

    @classmethod
    async def get_switch(cls, owner) -> bool:
        return bool(await cls._run(cls._get_switch_sync, owner, default=False))

    @classmethod
    async def set_switch(cls, owner, enabled: bool) -> None:
        async with _store_lock:
            await cls._run(cls._set_switch_sync, owner, enabled)

    @classmethod
    async def vacuum(cls) -> int:
        """VACUUM 回收空闲页（异步包装，返回回收页数）。"""
        return await cls._run(cls._vacuum_sync, default=0) or 0


def _sorted_by_update(items: list[dict]) -> list[dict]:
    """按（更新时间, id）新→旧排序（活跃窗口与归档检索的统一序）。"""
    return sorted(
        items,
        key=lambda m: (m.get("updated_time", 0), m.get("id", 0)),
        reverse=True,
    )
