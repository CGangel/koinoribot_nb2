#!/usr/bin/env python3
"""旧版钓鱼数据结算：旧背包物品按旧版价格折成金币、漂流瓶持有数迁移，
最后删除旧版钓鱼存档表 fishing。

背景
----
正式版用新的 fish 插件替代了旧版 fishing 插件。旧版把玩家资产存在
``koinoribot.db`` 的 ``fishing`` 表里（JSON 列 ``fish_data``），新版不再读取
它；更要注意的是，新版 ``plugins/drift_bottle`` 初始化时会执行
``DROP TABLE IF EXISTS fishing``——若直接启动新版，旧背包资产会**直接丢失**。
本脚本必须在启用新版之前运行。

结算规则（价格取线上 config 表里的旧版配置，缺省回落旧版默认值）
--------------------------------------------------------------
    鱼 / 星星（🐟🦐🦀🐡🐠🦈🌟）  按 ``fish_price`` 折算金币
                                （与旧版「一键出售」完全一致，🌟 亦按 2000 计）
    鱼饵 🍙                      按 ``bait_price``（**买入价**，线上 3）折算金币
    水之心 🔮                    按 ``crystal_to_bottle`` 合成漂流瓶
    漂流瓶 ✉                     保留持有数

折出的金币按 ``user_money.gold_max`` 截断（与游戏内 ``money.increase`` 的
``MIN(gold + ?, gold_max)`` 行为一致）；🔮 与 ✉ 转入 ``bottle_inventory``
（新版漂流瓶的持有数表，字段与 ``drift_bottle/db.py`` 的定义一致）。

安全边界
--------
只删除旧版钓鱼表 ``fishing``。以下表一律不动：
    bottles / bottle_comments / bottle_inventory（漂流瓶数据）
    fish_limit（新版 fish 复用；新旧结构一致，保留玩家的每日次数）

用法
----
    python migrate_old_fishing.py             # 预演：只报告，不写库
    python migrate_old_fishing.py --apply     # 执行（先自动备份数据库文件）

默认库路径为 ``<本脚本目录>/src/database/koinoribot.db``（与正式版一致），
可用 ``--db`` 指定其它文件（如先复制一份副本做验证）。
"""

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = (
    Path(__file__).resolve().parent / "src" / "database" / "koinoribot.db"
)

# 旧版默认价格（与 plugins/fishing/getfish.py 的 _DEFAULT_FISH_PRICE 一致），
# 线上 config.fish_price 存在时以其为准
DEFAULT_FISH_PRICE = {
    "🍙": 1, "🐟": 5, "🦐": 10, "🦀": 35,
    "🐡": 45, "🐠": 75, "🦈": 100, "🌟": 2000,
}
DEFAULT_BAIT_PRICE = 3
DEFAULT_CRYSTAL_TO_BOTTLE = 1

BAIT = "🍙"          # 鱼饵：按买入价结算，而非回收价
CRYSTAL = "🔮"        # 水之心：合成漂流瓶
BOTTLE = "✉"          # 漂流瓶持有数：直接迁移

# 旧版钓鱼存档表（本脚本要删的表）
OLD_FISHING_TABLE = "fishing"
# 绝不能动的表（漂流瓶数据 + 新版复用的每日次数表）
PROTECTED_TABLES = ("bottles", "bottle_comments", "bottle_inventory", "fish_limit")

BOTTLE_INVENTORY_DDL = """
    CREATE TABLE IF NOT EXISTS bottle_inventory (
        uid INTEGER PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (uid) REFERENCES user_uid_mapping(uid)
            ON UPDATE CASCADE ON DELETE CASCADE
    )
"""

# 旧版钓鱼遗留配置项：新版已无对应字段（pydantic 按 extra 忽略，不会报错，
# 但残留在 config 表里易与将来的同名键混淆），随本次迁移一并清理。
# 注意：throw_cool_time / salvage_cool_time / comment_cool_time / bottle_price /
# comment_price 是**漂流瓶**共用键，新版仍在用，绝不能删。
OBSOLETE_CONFIG_KEYS = (
    "fish_list", "fish_price", "probability", "cool_time", "fish_cd",
    "bait_num", "bait_price", "frag_to_crystal", "crystal_to_bottle",
    "crystal_to_net", "star_price", "extra_gold",
)

# 新版每日钓鱼次数上限。旧版默认为 10000 且线上从未改过，属旧版默认值；
# 若原样保留，新版会以 10000 次/日运行（与新版设计不符），故复位为新版默认。
NEW_FISH_LIMIT_COUNT = 10


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _read_config(conn: sqlite3.Connection) -> dict:
    """读 config 表的旧版钓鱼配置（值存为 JSON）；缺表时返回空 dict"""
    if not _table_exists(conn, "config"):
        return {}
    out: dict = {}
    for row in conn.execute("SELECT key, value FROM config"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _int_or(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def collect(conn: sqlite3.Connection) -> dict:
    """汇总结算数据（只读，不改库）"""
    cfg = _read_config(conn)
    price = cfg.get("fish_price") or DEFAULT_FISH_PRICE
    if not isinstance(price, dict) or not price:
        price = DEFAULT_FISH_PRICE
    price = {str(k): _int_or(v, 0) for k, v in price.items()}
    bait_price = _int_or(cfg.get("bait_price"), DEFAULT_BAIT_PRICE)
    crystal_rate = _int_or(
        cfg.get("crystal_to_bottle"), DEFAULT_CRYSTAL_TO_BOTTLE
    )
    gold_cap = _int_or(cfg.get("gold_max"), 0)

    rows = list(conn.execute(
        f"SELECT uid, fish_data, statis_data FROM {OLD_FISHING_TABLE}"
    ))
    gold_by_uid: dict[int, int] = {}
    bottles_by_uid: dict[int, int] = {}
    item_totals: dict[str, int] = {}
    item_gold: dict[str, int] = {}
    unknown: dict[str, int] = {}
    sold: dict[str, int] = {}
    broken: list[int] = []
    frags_total = 0

    for row in rows:
        uid = int(row["uid"])
        try:
            data = json.loads(row["fish_data"])
        except (json.JSONDecodeError, TypeError):
            broken.append(uid)
            continue
        if not isinstance(data, dict):
            broken.append(uid)
            continue

        gold = 0
        bottles = 0
        for item, raw_count in data.items():
            if isinstance(raw_count, bool) or not isinstance(
                raw_count, (int, float)
            ):
                continue
            count = int(raw_count)
            if count <= 0:
                continue
            item_totals[item] = item_totals.get(item, 0) + count
            if item == CRYSTAL:
                bottles += count * crystal_rate
            elif item == BOTTLE:
                bottles += count
            elif item == BAIT:
                # 鱼饵按买入价结算（旧版 fish_price 里只有回收价，故不走 price）
                gold += count * bait_price
                item_gold[item] = item_gold.get(item, 0) + count * bait_price
            elif item in price:
                gold += count * price[item]
                item_gold[item] = item_gold.get(item, 0) + count * price[item]
            else:
                unknown[item] = unknown.get(item, 0) + count

        if gold:
            gold_by_uid[uid] = gold
        if bottles:
            bottles_by_uid[uid] = bottles

        # 碎片（statis_data.frags）本脚本不折算，仅统计提示
        try:
            frags_total += _int_or(
                json.loads(row["statis_data"]).get("frags"), 0
            )
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    # 现有金币（用于预估上限截断）。整表读入：user_money 行数不大，
    # 避免 WHERE IN (...) 触碰 SQLite 的变量数上限
    current_gold: dict[int, int] = {}
    if _table_exists(conn, "user_money"):
        for row in conn.execute("SELECT uid, gold FROM user_money"):
            current_gold[int(row["uid"])] = int(row["gold"] or 0)

    capped: list[tuple[int, int, int]] = []   # (uid, 应得, 实得)
    for uid, gain in gold_by_uid.items():
        if gold_cap <= 0:
            continue
        before = current_gold.get(uid, 0)
        after = before + gain
        if after > gold_cap:
            capped.append((uid, gain, max(0, gold_cap - before)))

    return {
        "config": {"price": price, "bait_price": bait_price,
                   "crystal_rate": crystal_rate, "gold_cap": gold_cap},
        "users": len(rows),
        "gold_by_uid": gold_by_uid,
        "bottles_by_uid": bottles_by_uid,
        "item_totals": item_totals,
        "item_gold": item_gold,
        "unknown": unknown,
        "broken": broken,
        "frags_total": frags_total,
        "capped": capped,
        # 待清理的遗留配置项 / 每日次数复位
        "stale_keys": [k for k in OBSOLETE_CONFIG_KEYS if k in cfg],
        "limit_count_now": cfg.get("fish_limit_count"),
    }


def report(summary: dict) -> bool:
    """打印预演报告；返回是否有需要人工确认的异常（未知物品/损坏行）"""
    cfg = summary["config"]
    print("=" * 68)
    print("旧版钓鱼数据结算 —— 预演报告")
    print("=" * 68)
    print(f"结算价格来源：fish_price"
          f"（鱼饵 {BAIT} 改按 bait_price={cfg['bait_price']} 买入价）")
    print(f"水之心 {CRYSTAL} → 漂流瓶：1:{cfg['crystal_rate']}")
    cap = cfg["gold_cap"]
    print(f"金币上限：{cap if cap > 0 else '未配置（不截断）'}")
    print()
    print(f"旧版存档用户：{summary['users']} 人")
    print()
    print(f"{'物品':<6}{'数量':>16}{'折算':>18}")
    print("-" * 68)
    total_gold = 0
    for item, count in sorted(
        summary["item_totals"].items(), key=lambda kv: -kv[1]
    ):
        gain = summary["item_gold"].get(item, 0)
        total_gold += gain
        note = ""
        if item == CRYSTAL or item == BOTTLE:
            note = f"→ 漂流瓶 ×{cfg['crystal_rate'] if item == CRYSTAL else 1}"
        elif item not in summary["item_gold"] and item not in (CRYSTAL, BOTTLE):
            note = "（无价，未结算）"
        print(f"{item:<6}{count:>16,}"
              f"{(f'{gain:,} 金币' if gain else note):>18}")
    print("-" * 68)
    print(f"{'合计':<6}{sum(summary['item_totals'].values()):>16,}"
          f"{f'{total_gold:,} 金币':>18}")
    print()
    print(f"金币受益用户：{len(summary['gold_by_uid'])} 人，"
          f"合计 {total_gold:,} 金币")
    print(f"漂流瓶迁入用户：{len(summary['bottles_by_uid'])} 人，"
          f"合计 {sum(summary['bottles_by_uid'].values()):,} 个")
    if summary["capped"]:
        lost = sum(g - a for _, g, a in summary["capped"])
        print()
        print(f"⚠ 触及金币上限被截断：{len(summary['capped'])} 人，"
              f"共少发 {lost:,} 金币")
        for uid, gain, actual in summary["capped"][:5]:
            print(f"    uid={uid} 应得 {gain:,}，实得 {actual:,}")
        if len(summary["capped"]) > 5:
            print(f"    ...（其余 {len(summary['capped']) - 5} 人略）")

    flagged = False
    if summary["unknown"]:
        flagged = True
        print()
        print("⚠ 发现价格表以外的物品（未结算，请人工确认）：")
        for item, count in summary["unknown"].items():
            print(f"    {item} × {count:,}")
    if summary["broken"]:
        flagged = True
        print()
        print(f"⚠ fish_data 解析失败的用户 {len(summary['broken'])} 个"
              f"（未结算）：{summary['broken'][:10]}")
    if summary["frags_total"]:
        print()
        print(f"提示：旧版碎片 statis_data.frags 合计 {summary['frags_total']:,} 个，"
              f"本脚本未折算（如需要请另行确认兑换规则）")

    print()
    print("=" * 68)
    print("新版兼容处理（config 表）")
    print("=" * 68)
    print(f"清理旧版钓鱼遗留配置项：{len(summary['stale_keys'])} 个")
    if summary["stale_keys"]:
        print(f"    {', '.join(summary['stale_keys'])}")
    print(f"每日钓鱼次数 fish_limit_count："
          f"{summary['limit_count_now']} → {NEW_FISH_LIMIT_COUNT}")
    print("（漂流瓶仍在用的键 throw/salvage/comment_cool_time、"
          "bottle_price、comment_price 会保留）")
    return flagged


def apply_migration(conn: sqlite3.Connection, summary: dict) -> None:
    """单事务写入：加金币 + 迁漂流瓶 + 清理遗留配置 + 删旧表
    （要么全成，要么全回滚）"""
    cap = summary["config"]["gold_cap"]
    cursor = conn.cursor()
    cursor.execute(BOTTLE_INVENTORY_DDL)

    for uid, gain in summary["gold_by_uid"].items():
        if cap > 0:
            cursor.execute(
                "INSERT INTO user_money (uid, gold) VALUES (?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET gold = MIN(user_money.gold + ?, ?)",
                (uid, min(gain, cap), gain, cap),
            )
        else:
            cursor.execute(
                "INSERT INTO user_money (uid, gold) VALUES (?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET gold = user_money.gold + ?",
                (uid, gain, gain),
            )

    for uid, count in summary["bottles_by_uid"].items():
        cursor.execute(
            "INSERT INTO bottle_inventory (uid, count) VALUES (?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET count = bottle_inventory.count + ?",
            (uid, count, count),
        )

    # 清理旧版钓鱼遗留配置项 + 复位每日次数上限
    if summary["stale_keys"]:
        cursor.execute(
            "DELETE FROM config WHERE key IN "
            f"({','.join('?' * len(summary['stale_keys']))})",
            tuple(summary["stale_keys"]),
        )
    cursor.execute(
        "INSERT INTO config (key, value, updated_at) VALUES ('fish_limit_count', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (json.dumps(NEW_FISH_LIMIT_COUNT), time.time()),
    )

    cursor.execute(f"DROP TABLE IF EXISTS {OLD_FISHING_TABLE}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="旧版钓鱼数据结算（折金币 + 迁漂流瓶 + 删旧表）",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help=f"数据库文件（默认 {DEFAULT_DB}）")
    parser.add_argument("--apply", action="store_true",
                        help="真正写库；不给则只预演报告")
    parser.add_argument("--no-backup", action="store_true",
                        help="--apply 时不备份数据库文件（默认会备份）")
    parser.add_argument("--force", action="store_true",
                        help="预演发现异常项（未知物品/损坏行）时仍继续执行")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, OLD_FISHING_TABLE):
            print(f"库中已无 {OLD_FISHING_TABLE} 表（可能已结算过或新版已启动）"
                  f"，无需处理。")
            return 0

        summary = collect(conn)
        flagged = report(summary)

        if not args.apply:
            print()
            print("（预演模式，未改动数据库。确认无误后加 --apply 执行。）")
            return 0

        if flagged and not args.force:
            print()
            print("存在需人工确认的项（见上方 ⚠）。为安全起见已中止；"
                  "确认可忽略后加 --force 继续。", file=sys.stderr)
            return 2

        if not args.no_backup:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = db_path.with_suffix(db_path.suffix + f".bak-{stamp}")
            shutil.copy2(db_path, backup)
            print(f"已备份数据库：{backup}")

        # 应用（单事务）
        conn.execute("BEGIN IMMEDIATE")
        try:
            apply_migration(conn, summary)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        left = [t for t in PROTECTED_TABLES if _table_exists(conn, t)]
        print()
        print("完成：")
        print(f"  金币发放     {len(summary['gold_by_uid'])} 人")
        print(f"  漂流瓶迁入   {len(summary['bottles_by_uid'])} 人"
              f"（{sum(summary['bottles_by_uid'].values()):,} 个）")
        print(f"  已删除表     {OLD_FISHING_TABLE}")
        print(f"  保留表       {', '.join(left) if left else '（无）'}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
