"""配置存储模块：SQLite config 表 + 内存配置。

- KoinoribotConfig 保留全部配置字段定义与默认值（superusers 除外，
  超级用户列表迁移到 passwd.py）。
- 启动时（插件导入期，先于子插件加载）把 config 表一次性加载进内存，
  模块级 ``config`` 实例身份终身不变，修改时原地更新，所有
  ``from .config_store import config`` 的持有方看到同一份数据。
- 旧版 koinori_config.py（文件式配置）在首次启动时自动迁移入库并删除。
- 8889 配置面板通过 ``update_config()`` 修改配置：pydantic 校验 →
  写库 → 原地更新内存，仅修改时才写。
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from nonebot.log import logger
from pydantic import BaseModel, ValidationError

_PLUGIN_DIR = Path(__file__).resolve().parent
PASSWD_FILENAME = "passwd.py"
PASSWD_TEMPLATE_FILENAME = "passwd.py.template"
LEGACY_CONFIG_FILENAME = "koinori_config.py"

DEFAULT_DB_PATH = _PLUGIN_DIR / "src" / "database" / "koinoribot.db"


class KoinoribotConfig(BaseModel):
    """Koinoribot 全局配置（superusers 除外，见 passwd.py）"""

    # ================== 官Bot AppID ==================
    qqbot_appid: str = ""                                              # 官方Bot AppID，用于通过 openid 获取用户昵称和头像
    qqbot_openid_api: str = "https://oiapi.net/api/Openid"            # OpenID 查询 API 地址

    # ================== 钓鱼配置 ==================
    cool_time: int = 100                    # 单抽钓鱼冷却时长
    fish_cd: int = 30                       # 通用钓鱼冷却
    throw_cool_time: int = 5                # 扔漂流瓶冷却时长
    salvage_cool_time: int = 5              # 捡漂流瓶冷却时长
    comment_cool_time: int = 5              # 评论漂流瓶冷却时长
    bait_num: int = 10                      # 钓鱼所需鱼饵
    bait_price: int = 3                     # 鱼饵的价格
    bottle_price: int = 100                 # 漂流瓶的价格
    comment_price: int = 50                 # 评论漂流瓶需要的金币
    frag_to_crystal: int = 50               # 碎片转化为水之心的数量
    crystal_to_bottle: int = 1              # 水之心转化为漂流瓶的数量
    crystal_to_net: int = 1                 # 捞漂流瓶需要的水之心数量
    fish_limit_count: int = 10000           # 每日最大钓鱼次数

    # 鱼的配置（getfish.py 运行时读取）
    fish_list: list = ['🐟', '🦐', '🦀', '🐡', '🐠', '🦈', '🌟']
    fish_price: dict = {
        '🍙': 1, '🐟': 5, '🦐': 10, '🦀': 35,
        '🐡': 45, '🐠': 75, '🦈': 100, '🌟': 2000
    }
    # 钓鱼结果权重（没钓到鱼, 随机事件, 钓到鱼, 钓到金币, 钓到水之心），需恰好 5 份
    probability: list = [5, 10, 74, 10, 1]

    # ================== 经济系统 ==================
    min_rest: int = 1000                    # 转账后最少剩余金币
    dibao: int = 3000                       # 低保金额
    gold_max: int = 9999999999              # 金币上限
    transfer_fee: float = 0.1               # 转账手续费比率
    stone_fee: float = 0.05                 # 退还宝石手续费比率
    return_item_fee: float = 0.5            # 退还宠物用品手续费比率

    # ================== 股票配置 ==================
    maxtype: int = 4                        # 股票持有种类上限
    maxcount: int = 500                     # 每种股票持有数量上限

    # ================== AI画图配置 ==================
    deepseek_api_key: str = ""                                         # DeepSeek API Key (用于翻译提示词)
    gpt_image_api_key: str = ""                                        # GPT-Image-2 API Key
    gpt_image_api_base_url: str = "https://api.example.com/v1"
    gpt_image_model: str = "gpt-image-2"
    gpt_image_response_format: str = "url"                             # 图片返回格式：url 或 base64/b64_json
    draw_cost: int = 200000                                            # 画图消耗金币
    daily_limit: int = 3                                               # 每日画图次数限制
    ai_draw_enable: bool = True                                        # 是否启用AI画图功能
    ai_draw_size: str = "auto"                                         # 普通画图尺寸
    shaojo_image_size: str = "800x1200"                                # 今日人设图尺寸
    aidraw_quality: str = "medium"                                     # 普通画图、修图、人设图质量
    aidraw_high_quality: str = "high"                                  # 高质量画图、修图、人设图质量
    enable_gold_aidraw: bool = True                                    # 是否允许消耗金币画图；False 时仅允许使用免费次数

    # ================== 其他配置 ==================
    star_price: int = 0                     # 多连钓鱼是否消耗星星
    extra_gold: int = 1                     # 钓鱼补贴开关

    # 黑名单用户
    blackusers: list = []

    # 公网白名单模式
    public_bot: bool = False                 # 是否启用云bot模式
    permit_bot: list = []                   # 自己的bot账号列表（如果上面一项为True，则此项必填）
    ip_address: str = ""                    # 本机公网ip地址（公网白名单模式下必填）


# 面板分组（顺序即展示顺序）
_FIELD_SECTIONS: dict[str, list[str]] = {
    "官Bot AppID": ["qqbot_appid", "qqbot_openid_api"],
    "钓鱼配置": [
        "cool_time", "fish_cd", "throw_cool_time", "salvage_cool_time",
        "comment_cool_time", "bait_num", "bait_price", "bottle_price",
        "comment_price", "frag_to_crystal", "crystal_to_bottle",
        "crystal_to_net", "fish_limit_count", "fish_list", "fish_price",
        "probability",
    ],
    "经济系统": [
        "min_rest", "dibao", "gold_max", "transfer_fee", "stone_fee",
        "return_item_fee",
    ],
    "股票配置": ["maxtype", "maxcount"],
    "AI画图配置": [
        "deepseek_api_key", "gpt_image_api_key", "gpt_image_api_base_url",
        "gpt_image_model", "gpt_image_response_format", "draw_cost",
        "daily_limit", "ai_draw_enable", "ai_draw_size", "shaojo_image_size",
        "aidraw_quality", "aidraw_high_quality", "enable_gold_aidraw",
    ],
    "其他配置": ["star_price", "extra_gold", "blackusers"],
    "公网白名单模式": ["public_bot", "permit_bot", "ip_address"],
}

# 面板字段中文说明（必须覆盖全部配置项，test_config_system 有完整性校验）
_FIELD_DESCRIPTIONS: dict[str, str] = {
    # 官Bot AppID
    "qqbot_appid": "官方 QQBot 的 AppID，用于换算用户昵称/头像",
    "qqbot_openid_api": "OpenID 查询昵称的第三方 API 地址（官方昵称字段的降级路径）",
    # 钓鱼配置
    "cool_time": "单次钓鱼的冷却时长（秒）",
    "fish_cd": "钓鱼通用冷却（秒）",
    "throw_cool_time": "扔漂流瓶冷却时长（秒）；修改需重启生效",
    "salvage_cool_time": "捡漂流瓶冷却时长（秒）；修改需重启生效",
    "comment_cool_time": "评论漂流瓶冷却时长（秒）；修改需重启生效",
    "bait_num": "钓鱼一次消耗的鱼饵数量",
    "bait_price": "鱼饵单价（金币）",
    "bottle_price": "购买漂流瓶的价格（金币）",
    "comment_price": "评论漂流瓶需要的金币",
    "frag_to_crystal": "碎片兑换 1 个水之心所需数量",
    "crystal_to_bottle": "1 个漂流瓶需要的水之心数量",
    "crystal_to_net": "捞一次漂流瓶需要的水之心数量",
    "fish_limit_count": "每人每日最大钓鱼次数",
    "fish_list": "鱼的种类列表（emoji 顺序对应鱼上钩权重，权重已硬编码弃用配置）",
    "fish_price": "物品单价表（键为 emoji，含鱼饵 🍙）",
    "probability": "钓鱼结果权重（份）：没钓到鱼, 随机事件, 钓到鱼, 钓到金币, 钓到水之心；需恰好 5 份，否则回落默认",
    # 经济系统
    "min_rest": "转账后账户最少需保留的金币",
    "dibao": "低保金额，贫穷时可以领取",
    "gold_max": "金币持有上限",
    "transfer_fee": "转账手续费比率（0.1 = 10%）",
    "stone_fee": "退还宝石的手续费比率",
    "return_item_fee": "退还宠物用品的手续费比率",
    # 股票配置
    "maxtype": "每人最多持有的股票种类数",
    "maxcount": "每种股票的最大持有数量",
    # AI画图配置
    "deepseek_api_key": "DeepSeek API Key（用于画图提示词翻译），注意保密",
    "gpt_image_api_key": "画图接口 API Key，注意保密",
    "gpt_image_api_base_url": "画图接口地址（OpenAI 兼容格式）",
    "gpt_image_model": "画图模型名",
    "gpt_image_response_format": "图片返回格式：url 或 b64_json",
    "draw_cost": "画图/修图单次消耗金币",
    "daily_limit": "每人每日画图/修图次数上限",
    "ai_draw_enable": "是否启用 AI 画图/修图功能",
    "ai_draw_size": "普通画图尺寸（auto / 1024x1024 / 1024x1536 等）",
    "shaojo_image_size": "今日人设图尺寸（如 800x1200）",
    "aidraw_quality": "普通画图/修图/人设图质量（low / medium / high）",
    "aidraw_high_quality": "高质量画图/修图/人设图质量（low / medium / high）",
    "enable_gold_aidraw": "是否允许花金币画图；关闭后只能用免费次数",
    # 其他配置
    "star_price": "多连钓鱼星星单价（0 = 不消耗星星）",
    "extra_gold": "钓鱼补贴开关：1 时百连钓鱼未用星星可获 300 金币补贴",
    "blackusers": "黑名单用户（统一 UID 列表）",
    # 公网白名单模式
    "public_bot": "是否启用云 bot（公网白名单）模式",
    "permit_bot": "自己的 bot 账号列表（云 bot 模式下必填）",
    "ip_address": "本机公网 IP（云 bot 模式必填；也用于冰祈配置回复的面板地址）",
}

# 面板展示时需要打码的字段（含 key/secret/appkey 的敏感项）
_SECRET_FIELD_HINTS = ("key", "secret")


def is_secret_field(name: str) -> bool:
    return any(hint in name.lower() for hint in _SECRET_FIELD_HINTS)


def mask_value(value: Any) -> str:
    text = str(value)
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


# 全局配置实例：身份终身不变，store 加载/修改都原地更新它
config: KoinoribotConfig = KoinoribotConfig()

_db_path: Optional[str] = None


def get_config() -> KoinoribotConfig:
    """获取配置实例（兼容旧 API）"""
    return config


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path or str(DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()


def _apply_in_place(model: BaseModel, new_model: BaseModel) -> None:
    """把 new_model 的字段原地写入 model，保持实例身份不变。"""
    object.__setattr__(model, "__dict__", dict(new_model.__dict__))
    object.__setattr__(
        model, "__pydantic_fields_set__", set(new_model.__pydantic_fields_set__)
    )


def _load_from_db() -> int:
    """把 config 表全部行加载进内存（内部使用）。返回加载的字段数。"""
    with _connect() as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT key, value FROM config").fetchall()

    data: dict[str, Any] = {}
    for row in rows:
        try:
            data[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[config_store] 配置项 {row['key']} 的值损坏，忽略")
    if data:
        _apply_in_place(config, KoinoribotConfig.model_validate(data))
    return len(rows)


def _write_rows(updates: dict[str, Any]) -> None:
    now = time.time()
    with _connect() as conn:
        _ensure_table(conn)
        conn.executemany(
            """
            INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            [
                (key, json.dumps(value, ensure_ascii=False), now)
                for key, value in updates.items()
            ],
        )
        conn.commit()


def update_config(updates: dict[str, Any]) -> list[str]:
    """修改配置：pydantic 校验 → 写库 → 原地更新内存。

    仅出现在 updates 中的字段会被修改。返回被修改的字段名列表。
    校验失败抛出 ValueError（带 pydantic 错误信息），不落库不动内存。
    """
    unknown = [k for k in updates if k not in KoinoribotConfig.model_fields]
    if unknown:
        raise ValueError(f"未知配置项: {', '.join(unknown)}")

    try:
        new_model = KoinoribotConfig.model_validate(
            {**config.model_dump(), **updates}
        )
    except ValidationError as e:
        raise ValueError(str(e)) from e

    current = config.model_dump()
    changed_keys = [key for key in updates if current[key] != updates[key]]
    if not changed_keys:
        return []
    _write_rows({key: updates[key] for key in changed_keys})
    _apply_in_place(config, new_model)
    return changed_keys


def dump_for_panel(reveal: bool = False) -> dict[str, Any]:
    """生成面板数据：分区字段列表（含类型、中文说明与打码值）。"""
    dump = config.model_dump()
    sections: dict[str, list[dict[str, Any]]] = {}
    for section, fields in _FIELD_SECTIONS.items():
        items = []
        for name in fields:
            annotation = KoinoribotConfig.model_fields[name].annotation
            value = dump[name]
            secret = is_secret_field(name)
            items.append(
                {
                    "key": name,
                    "desc": _FIELD_DESCRIPTIONS.get(name, ""),
                    "type": getattr(annotation, "__name__", str(annotation)),
                    "value": value if (not secret or reveal) else mask_value(value),
                    "masked": secret and not reveal,
                }
            )
        sections[section] = items
    return {"sections": sections}


# ================== 旧版文件迁移 ==================


def _import_legacy_config(path: Path):
    spec = importlib.util.spec_from_file_location("koinori_config_legacy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载旧配置文件: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "config", None)


def _migrate_legacy_file(path: Path) -> list[int]:
    """把旧版 koinori_config.py 的值迁移入 config 表，成功后删除文件。

    返回旧文件中的 superusers 列表（供 passwd.py 播种），失败时保留文件。
    """
    try:
        legacy_config = _import_legacy_config(path)
        if legacy_config is None:
            raise ImportError("旧配置文件中没有 config 实例")
        legacy_data = legacy_config.model_dump()
    except Exception as e:
        logger.error(f"[config_store] 旧配置文件加载失败，保留原文件: {e}")
        return []

    superusers = legacy_data.pop("superusers", [])
    known = {
        key: value
        for key, value in legacy_data.items()
        if key in KoinoribotConfig.model_fields
    }
    try:
        _write_rows(known)
    except Exception as e:
        logger.error(f"[config_store] 旧配置迁移入库失败，保留原文件: {e}")
        return []

    try:
        path.unlink()
    except OSError as e:
        logger.warning(f"[config_store] 旧配置文件删除失败（值已入库）: {e}")

    logger.info(
        f"[config_store] 已迁移旧配置 koinori_config.py：{len(known)} 项入库，原文件已删除"
    )
    return [int(uid) for uid in superusers]


def init_config_store(
    db_path: Optional[str] = None,
    legacy_config_path: Optional[Path] = None,
) -> list[int]:
    """初始化 config 表并一次性加载到内存。

    需要在子插件加载之前调用（fishing 等插件在导入期读取配置值）。
    返回迁移出的 superusers 列表（无迁移时为空列表）。
    """
    global _db_path
    _db_path = db_path or str(DEFAULT_DB_PATH)
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        _ensure_table(conn)
        # 清理已从 schema 删除的字段残留行
        valid_keys = tuple(KoinoribotConfig.model_fields)
        placeholders = ",".join("?" * len(valid_keys))
        stale = conn.execute(
            f"DELETE FROM config WHERE key NOT IN ({placeholders})", valid_keys
        ).rowcount
        if stale:
            conn.commit()
            logger.info(f"[config_store] 已清理 {stale} 条废弃配置项")
        row_count = conn.execute("SELECT COUNT(*) AS c FROM config").fetchone()["c"]

    migrated_superusers: list[int] = []
    legacy_path = (
        legacy_config_path
        if legacy_config_path is not None
        else _PLUGIN_DIR / LEGACY_CONFIG_FILENAME
    )
    if row_count == 0 and legacy_path.exists():
        migrated_superusers = _migrate_legacy_file(legacy_path)

    loaded = _load_from_db()
    logger.info(f"[config_store] 配置加载完成：{loaded} 项（数据库 {_db_path}）")
    return migrated_superusers


# ================== passwd.py 管理 ==================

# 模板缺失时的兜底内容（正常情况使用 passwd.py.template）
_PASSWD_FALLBACK_TEMPLATE = '''\
# Koinoribot 敏感配置（请勿提交到仓库）
# 8889 配置面板的访问密码
PANEL_PASSWORD = "{password}"

# 超级用户列表（等级 0 = 最高权限，可使用“冰祈配置”指令）
# 值为统一 UID（与数据库 superusers 表的 uid 同一命名空间）
SUPERUSERS = {superusers}
'''


def ensure_passwd_file(
    superusers: Optional[list[int]] = None,
    plugin_dir: Optional[Path] = None,
) -> tuple[Path, bool]:
    """确保 passwd.py 存在；不存在时按模板生成（面板密码为随机值）。

    superusers 来自旧配置迁移，会替换模板中的 SUPERUSERS 默认值；
    已存在的 passwd.py 不会被修改。返回 (路径, 是否新建)。
    """
    base = plugin_dir or _PLUGIN_DIR
    path = base / PASSWD_FILENAME
    if path.exists():
        return path, False

    template_path = base / PASSWD_TEMPLATE_FILENAME
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
    else:
        logger.warning(
            f"[config_store] 缺少 {PASSWD_TEMPLATE_FILENAME}，使用内置兜底模板"
        )
        content = _PASSWD_FALLBACK_TEMPLATE.format(
            password="__RANDOM__", superusers="[]"
        )

    if superusers:
        content = _replace_superusers_line(content, superusers)
    content = content.replace("__RANDOM__", secrets.token_urlsafe(16))
    path.write_text(content, encoding="utf-8")
    logger.warning(
        f"[config_store] 已生成 {PASSWD_FILENAME}（面板密码为随机值，请打开文件查看并妥善保管）"
    )
    return path, True


def _replace_superusers_line(content: str, superusers: list[int]) -> str:
    lines = []
    for line in content.splitlines():
        if line.strip().startswith("SUPERUSERS"):
            lines.append(f"SUPERUSERS = {superusers!r}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def get_panel_password() -> str:
    """读取面板密码；passwd.py 缺失时返回空串（面板将拒绝一切登录）。"""
    try:
        from . import passwd
    except ImportError:
        return ""
    return str(getattr(passwd, "PANEL_PASSWORD", ""))


def get_passwd_superusers() -> list[int]:
    """读取等级 0 超级用户列表（passwd.SUPERUSERS）。"""
    try:
        from . import passwd
        raw = getattr(passwd, "SUPERUSERS", [])
    except ImportError:
        return []
    result = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            logger.warning(f"[config_store] passwd.SUPERUSERS 含非法项: {item!r}")
    return result
