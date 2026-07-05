import sqlite3
from pathlib import Path

from nonebot import logger

PLUGIN_DIR = Path(__file__).parent
SRC_DIR = PLUGIN_DIR / "src"
DB_PATH = SRC_DIR / "koinoribot_nb2.db"
MIGRATION_DIR = PLUGIN_DIR / "migrations"


def run_migrations() -> None:
    """运行数据库迁移"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    # 创建数据库版本记录表(不重复创建)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 获取所有迁移文件，按文件名排序
    # 文件名格式: 迁移序号_迁移描述.sql
    # 迁移序号 是迁移顺序，数字越小越先执行
    # 001_create_kbot.sql 是第一份迁移文件，用于创建数据库表
    # 迁移文件不允许删除和修改，否则会导致数据库表结构异常
    # 如果需要修改数据库表结构，需要创建新的迁移文件
    migrations_files = sorted(
        MIGRATION_DIR.glob("*.sql"), key=lambda x: int(x.stem.split("_")[0])
    )

    # 执行每个迁移文件
    for migration_file in migrations_files:
        migration = migration_file.name

        # 检查迁移文件是否已应用
        cursor.execute(
            "SELECT 1 FROM _migrations WHERE filename = ?", (migration,)
        )
        if cursor.fetchone():
            continue  # 已应用，跳过

        # 没执行过，读取并执行迁移 SQL 语句
        sql = migration_file.read_text(encoding="utf-8")
        cursor.executescript(sql)

        # 记录这个迁移文件已应用
        cursor.execute(
            "INSERT INTO _migrations (filename) VALUES (?)", (migration,)
        )
        conn.commit()
        logger.info(f"已应用迁移文件: {migration}")
    conn.commit()
    conn.close()
    logger.info("数据库初始化完成")
