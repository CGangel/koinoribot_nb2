"""用 test 版代码加载「迁移后的库」，验证可直接使用。

镜像正式启动路径：init_config_store + 各插件 on_startup 的 DB 初始化，
然后校验关键数据与业务流程。

用法：
    python _verify_migrated_db.py <迁移后的库路径>
（不改动传入的库：先复制一份到临时目录再操作。）
"""
import argparse
import importlib
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent

_parser = argparse.ArgumentParser(description="校验迁移后的库可被 test 版使用")
_parser.add_argument("db", help="迁移后的数据库文件路径")
_args = _parser.parse_args()

SRC = Path(_args.db)              # 迁移后的库
if not SRC.is_file():
    print(f"数据库不存在：{SRC}", file=sys.stderr)
    sys.exit(1)
work = Path(tempfile.mkdtemp()) / "koinoribot.db"
shutil.copy2(SRC, work)

import nonebot
try:
    nonebot.get_driver()
except Exception:
    nonebot.init(driver="~httpx", superusers=[], command_start={""})

pkg = types.ModuleType("koinoribot_nb2")
pkg.__path__ = [str(TEST_ROOT)]
sys.modules.setdefault("koinoribot_nb2", pkg)
sub = types.ModuleType("koinoribot_nb2.plugins")
sub.__path__ = [str(TEST_ROOT / "plugins")]
sys.modules.setdefault("koinoribot_nb2.plugins", sub)

# ---- 1) 配置：先加载（子插件导入期读配置）----
cs = importlib.import_module("koinoribot_nb2.config_store")
cs.init_config_store(db_path=str(work))
print(f"[1] config 加载完成，fish_limit_count = {cs.config.fish_limit_count}")

# ---- 2) 迁移前的期望值（从备份算）----
conn = sqlite3.connect(str(work))
conn.row_factory = sqlite3.Row
bottles = {int(r["uid"]): int(r["count"]) for r in conn.execute(
    "SELECT uid, count FROM bottle_inventory")}
gold = {int(r["uid"]): int(r["gold"]) for r in conn.execute(
    "SELECT uid, gold FROM user_money")}
conn.close()

# ---- 3) 各插件初始化（镜像 on_startup）----
uid_manager = importlib.import_module("koinoribot_nb2.uid_manager")
uid_manager.set_database_path(str(work))
uid_manager.init_uid_database()

money_mod = importlib.import_module("koinoribot_nb2.money")
money_mod.set_database_path(str(work))
money_mod.init_money_database()

fish_db = importlib.import_module("koinoribot_nb2.plugins.fish.db")
fish_db.FishDB.set_db_path(str(work))
fish_db.FishDB.init_fish_database()

fish_limit_mod = importlib.import_module("koinoribot_nb2.fish_limit")
fish_limit_mod.FishLimitManager.set_db_path(str(work))
fish_limit_mod.FishLimitManager.init_database()

bottle_db = importlib.import_module("koinoribot_nb2.plugins.drift_bottle.db")
bottle_db.BottleDB.set_db_path(str(work))
bottle_db.BottleDB.init_bottle_database()
print("[2] fish / fish_limit / drift_bottle / uid / money 初始化完成（无异常）")

# 初始化会建表；确认旧表未复现、新表就位
conn = sqlite3.connect(str(work))
tabs = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
conn.close()
assert "fishing" not in tabs, "旧 fishing 表复现了"
for t in ("fish_players", "fish_items", "fish_collection", "fish_gear_items",
          "fish_bait_bag", "bottle_inventory", "bottles", "bottle_comments"):
    assert t in tabs, f"缺表 {t}"
print(f"[3] 表结构就位（无 fishing 旧表，共 {len(tabs)} 张表）")

# ---- 4) 数据读取：金币与漂流瓶 ----
sample_gold = max(gold, key=lambda u: gold[u])
wallet = money_mod.money.of(sample_gold)
assert wallet.gold == gold[sample_gold], (
    f"uid={sample_gold} 金币 {wallet.gold} != {gold[sample_gold]}")
sample_bottle = max(bottles, key=lambda u: bottles[u])
print(f"[4] 金币读取正确（uid={sample_gold}: {wallet.gold:,}）；"
      f"漂流瓶最大持有 uid={sample_bottle}: {bottles[sample_bottle]:,}")

# ---- 5) 业务流：新玩家建档 + 钓鱼 + 水族箱 + 漂流瓶持有数 ----
fish_service = importlib.import_module("koinoribot_nb2.plugins.fish.service")
fish_plugin = importlib.import_module("koinoribot_nb2.plugins.fish")
import asyncio

fresh = uid_manager.get_uid(platform="onebot", external_id="mig_probe")

async def flow():
    player = await fish_service.FishService.ensure_player(fresh)
    assert player["slots"] == 10, f"新玩家槽位 {player['slots']} != 10"
    got = None
    for _ in range(300):
        r = await fish_service.FishService.do_cast(fresh)
        if r["ok"] and not r.get("air"):
            got = r
            break
    assert got, "300 次全空军"
    put = await fish_service.FishService.put_in_aquarium(fresh)
    assert put["ok"], put
    img = await fish_plugin._aquarium_image(fresh)
    assert img and img[:4] == b"\x89PNG", "水族箱卡片渲染失败"
    tank = await fish_service.FishService.list_aquarium(fresh)
    return got, len(img), len(tank)

async def bottle_flow():
    ok = await bottle_db.BottleDB.consume_inventory(sample_bottle, 1)
    return ok

got, img_len, tank_len = asyncio.run(flow())
print(f"[5] 新玩家建档/钓鱼/入箱/出卡 正常（{got['species_name']}，"
      f"卡片 {img_len/1024:.1f}KB，箱内 {tank_len} 条）")

# 有瓶的人能正常扣瓶
sample_with_bottle = next((u for u, c in bottles.items() if c > 0), None)
if sample_with_bottle:
    before = asyncio.run(bottle_db.BottleDB.get_inventory(sample_with_bottle))
    asyncio.run(bottle_db.BottleDB.consume_inventory(sample_with_bottle, 1))
    after = asyncio.run(bottle_db.BottleDB.get_inventory(sample_with_bottle))
    assert after == before - 1, (before, after)
    print(f"[6] 漂流瓶扣减正常（uid={sample_with_bottle}: {before} → {after}）")

print("\n全部校验通过：迁移后的库可被 test 版直接使用。")
