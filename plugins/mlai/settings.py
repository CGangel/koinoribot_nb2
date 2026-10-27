"""mlai 有效配置读取层（带类型校验，缺键回落默认值）。"""


from ... import config_store
from ...config_store import config

PRESET_IDS = ("1", "2", "3", "4", "5")


def _defaults() -> dict:
    return config_store._MLAI_PRESETS_DEFAULT["1"]


def _active_raw() -> tuple[str, dict]:
    preset_id = str(getattr(config, "mlai_preset_id", "1") or "1").strip()
    if preset_id not in PRESET_IDS:
        preset_id = "1"
    table = config.mlai_presets if isinstance(config.mlai_presets, dict) else {}
    entry = table.get(preset_id)
    return preset_id, entry if isinstance(entry, dict) else {}


def _value(key: str):
    _, raw = _active_raw()
    if key in raw:
        return raw[key]
    return _defaults()[key]


def _to_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def preset_id() -> str:
    """生效槽位号（已规范到 1-5）。"""
    return _active_raw()[0]


def preset_name() -> str:
    return str(_value("name") or "").strip()


def preset_label() -> str:
    return f"{preset_id()} · {preset_name() or '未命名'}"


def enabled() -> bool:
    """AI 对话总开关（顶层字段，全局生效，与预设无关）。"""
    return config_store.norm_bool(getattr(config, "mlai_enable", True), True)


def api_key() -> str:
    return str(_value("mlai_api_key") or "").strip()


def api_base_url() -> str:
    return str(_value("mlai_api_base_url") or "").strip()


def model() -> str:
    return str(_value("mlai_model") or "").strip()


def system_prompt() -> str:
    return str(_value("mlai_system_prompt") or "")


def extra_params() -> dict:
    raw = _value("mlai_extra_params")
    return dict(raw) if isinstance(raw, dict) else {}


def timeout() -> int:
    """单次请求超时（顶层字段，全局生效）。"""
    return _to_int(getattr(config, "mlai_timeout", 60), 60, 1, 600)


def user_cd() -> int:
    """单人对话冷却（顶层字段，全局生效）。"""
    return _to_int(getattr(config, "mlai_user_cd", 20), 20, 0, 86400)


def is_configured() -> bool:
    """生效槽位的接口密钥与地址是否已配置。"""
    return bool(api_key() and api_base_url())


# ===== 触发与屏蔽（顶层字段） =====


def bot_name() -> str:
    """对话触发词（基础配置 bot_name，on_message 规则实时读取，面板
    修改即时生效；mlai / AI对话 前缀命令为固定名，不受此配置影响）。"""
    name = str(getattr(config, "bot_name", "梦灵") or "").strip()
    return name or "梦灵"


def starstone_enable() -> bool:
    """聊天计价开关：开启后每次触发对话消耗星星。"""
    return bool(getattr(config, "mlai_starstone_enable", True))


def starstone_chars() -> int:
    """每 1 星星计价的字符数（向上取整计费）。"""
    return _to_int(getattr(config, "mlai_starstone_chars", 1000), 1000, 1, 10**6)


def block_words() -> list[str]:
    """屏蔽词：消息包含任一词即静默不响应。空列表 = 不屏蔽。"""
    raw = getattr(config, "mlai_block_words", None)
    if not isinstance(raw, list):
        raw = ["梦灵酱"]
    return [str(word).strip() for word in raw if str(word).strip()]


# ===== 记忆库与群聊行为参数（顶层字段） =====


def private_enable() -> bool:
    """私聊总开关（关闭后仅等级 0 SU 可私聊对话与记录）。"""
    return config_store.norm_bool(
        getattr(config, "mlai_private_enable", True), True
    )


def group_enable() -> bool:
    return config_store.norm_bool(getattr(config, "mlai_group_enable", False), False)


def group_bind_id(openid: str) -> str:
    """官Bot group_openid → 真实群号；未绑定返回空串。"""
    mapping = getattr(config, "mlai_group_bind", None)
    if not isinstance(mapping, dict):
        return ""
    value = mapping.get(str(openid))
    return str(value).strip() if value is not None else ""


def bound_group_ids() -> set[str]:
    """mlai_group_bind 中已填真实群号的群号集合。"""
    mapping = getattr(config, "mlai_group_bind", None)
    if not isinstance(mapping, dict):
        return set()
    return {str(v).strip() for v in mapping.values() if str(v).strip()}


def group_whitelisted(scene: dict) -> bool:
    """群聊白名单（mlai_group_bind 兼官Bot 群绑定表，纯手动维护）：
    官Bot 群需 openid 已绑定真实群号；OneBot 群需群号出现在绑定值中；
    不在列表中的群聊无论群聊域开关一律静默。私聊场景恒放行。"""
    if not scene.get("is_group"):
        return True
    openid = scene.get("openid") or ""
    if openid:
        return bool(group_bind_id(openid))
    return scene.get("group_key", "") in bound_group_ids()


def active_window() -> int:
    return _to_int(getattr(config, "mlai_active_window", 20), 20, 0, 100)


def archive_per_scope() -> int:
    return _to_int(getattr(config, "mlai_archive_per_scope", 5), 5, 0, 20)


def ops_limit() -> int:
    return _to_int(getattr(config, "mlai_memory_ops_limit", 5), 5, 1, 20)


def memory_max_len() -> int:
    return _to_int(getattr(config, "mlai_memory_max_len", 200), 200, 10, 2000)


def temp_limit() -> int:
    """临时上下文保留条数（每条消息一行，旁听/对话/bot 回复共用，
    默认 20 条）。"""
    return _to_int(getattr(config, "mlai_temp_limit", 20), 20, 2, 100)
