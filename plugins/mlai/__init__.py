"""mlai - AI 对话插件：触发词/命令对话、旁听上下文、重置与状态指令。"""


import asyncio
from typing import Optional

import nonebot.adapters.onebot.v11 as onebot
import nonebot.adapters.qq as qq
from nonebot import get_driver, logger, on_command, on_message
from nonebot.adapters import Bot, Event, Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Depends
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from ...su_manager import SU_LEVEL_CONTRIBUTOR, get_su_level, is_su
from ...tools import (
    build_forward_chain,
    get_sender_nickname,
    get_uid,
    resolve_uid,
    send_group_forward_msg,
)
from ...utils import FreqLimiter
from . import settings
from .client import MlaiAPIError, chat_completion_message, download_image_data_url
from .flow import run_dialogue
from .service import (
    KIND_GROUP,
    KIND_USER,
    ROLE_USER,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    SCOPE_USER,
    MlaiStore,
)

__plugin_meta__ = PluginMetadata(
    name="mlai",
    description="AI 对话插件 - 记忆库架构的两轮对话（NSFW 审核 + 记忆自主维护）",
    usage="提到触发词即对话 / 聊天帮助 / 重置聊天 / 重置记忆 / mlai状态",
)

driver = get_driver()

# 每用户冷却（时长在 start_cd 时现读配置，面板热更新即时生效）
chat_freq = FreqLimiter()

# 等待返回中的对话用户集合：同一用户已有进行中的请求时，后续触发
# 直接静默丢弃（check+add 之间无 await，事件循环内原子无竞态）
_processing_uids: set[int] = set()


def _resolve_scene(event: Event) -> dict:
    """场景解析：is_group / group_key（存储用群号）/ openid（官Bot）。"""
    if isinstance(event, onebot.GroupMessageEvent):
        return {"is_group": True, "group_key": str(event.group_id), "openid": ""}
    if isinstance(event, qq.Event):
        openid = str(getattr(event, "group_openid", "") or "")
        if openid:
            return {"is_group": True, "group_key": "", "openid": openid}
        return {"is_group": False, "group_key": "", "openid": ""}
    return {"is_group": False, "group_key": "", "openid": ""}


def _is_blocked(text: str) -> bool:
    """消息包含任一屏蔽词则完全不响应（默认含另一 bot 的触发词）。"""
    return any(word in text for word in settings.block_words())


def _trigger_match(text: str) -> bool:
    """触发判定：消息文本中任意位置出现触发词即触发；命中屏蔽词不触发。"""
    if not text or _is_blocked(text):
        return False
    return settings.bot_name() in text


def _extract_image_urls(event: Event) -> list[str]:
    """提取消息中的图片附件 URL（官Bot attachment 段 / OneBot image 段，
    上限 3 张；兼容 CQ 转义的 &amp;）。"""
    urls: list[str] = []
    try:
        for seg in event.message or []:
            if str(getattr(seg, "type", "")) == "image":
                url = str(seg.data.get("url", "") or "").strip()
                if url:
                    urls.append(url.replace("&amp;", "&"))
    except Exception:
        return []
    return urls[:3]


async def _sender_name(event: Event, uid: int) -> str:
    """发言人昵称：自定义昵称表（call_me_please）> 平台昵称（OneBot
    群名片/昵称；官Bot 消息事件自带 author.username）；取不到回落
    「用户N」。"""
    name = ""
    try:
        name = str(await get_sender_nickname(event) or "").strip()
    except Exception:
        name = ""
    return name or f"用户{uid}"


def _is_self_message(event: Event) -> bool:
    """OneBot 自身消息（开启「上报自身消息」后上报的 bot 发言）：
    bot 回复已由发送侧自行落库，须跳过以防重复记录与自触发。
    qq 官Bot 事件无 user_id/self_id 字段，恒为 False。"""
    user_id = getattr(event, "user_id", None)
    self_id = getattr(event, "self_id", None)
    if user_id is None or self_id is None:
        return False
    try:
        return int(user_id) == int(self_id)
    except (TypeError, ValueError):
        return False


async def _capture_images(event: Event) -> list[str]:
    """下载并压缩消息中的图片为 data URL（单张失败跳过，不影响记录）。"""
    data_urls: list[str] = []
    for url in _extract_image_urls(event):
        try:
            data_urls.append(await download_image_data_url(url))
        except MlaiAPIError as e:
            logger.warning(f"[mlai] 旁听图片下载失败: {e}")
    return data_urls


async def _bot_name_rule(event: Event) -> bool:
    """on_message 规则：仅做内存判断，未命中不进 handler、不产生日志。

    触发条件只有文本含触发词；图片不构成触发条件，只随触发的消息
    （图文消息）或 mlai 命令附件一起被识别。"""
    try:
        text = event.message.extract_plain_text() if event.message else ""
    except Exception:
        return False
    if _is_self_message(event):
        return False  # OneBot 上报的自身消息不触发对话（防自回环）
    if not _trigger_match(text):
        return False
    # 群聊门槛：群聊域关闭或不在 mlai_group_bind 白名单 → 完全静默；
    # 私聊门槛：mlai_private_enable 关闭时仅等级 0 SU
    scene = _resolve_scene(event)
    if scene["is_group"]:
        if not _group_ready(scene):
            return False
    elif not _private_allowed(event):
        return False
    return True


def _is_owner(uid: int) -> bool:
    """等级 0 的 SU（最高权限）。"""
    return get_su_level(uid) == SU_LEVEL_CONTRIBUTOR


def _group_ready(scene: dict) -> bool:
    """群聊是否可响应：群聊域开启 且 在白名单中。"""
    return settings.group_enable() and settings.group_whitelisted(scene)


def _private_allowed(event: Event) -> bool:
    """私聊是否可响应：mlai_private_enable 开启时任何人；关闭时仅
    等级 0 SU（供维护测试）。"""
    if settings.private_enable():
        return True
    try:
        uid = resolve_uid(event, event.get_user_id())
    except Exception:
        return False
    return _is_owner(uid)


def _switch_owner(scene: dict, uid: int) -> Optional[str]:
    """聊天开关存储键：私聊每用户、群聊每用户每群（官Bot 群需已绑定
    真实群号；未绑定等异常场景返回 None，视为未开启）。"""
    if scene["is_group"]:
        if scene["openid"]:
            bound = settings.group_bind_id(scene["openid"])
            if not bound:
                return None
            return f"g:{bound}:{uid}"
        return f"g:{scene['group_key']}:{uid}"
    return f"u:{uid}"


async def _chat_enabled(scene: dict, uid: int) -> bool:
    """当前用户在本场景是否已开启 AI 聊天（默认关闭，用户级分场景）。"""
    owner = _switch_owner(scene, uid)
    if owner is None:
        return False
    return await MlaiStore.get_switch(owner)


def _help_text() -> str:
    name = settings.bot_name()
    return f"""【AI聊天】
==对话==
  启用AI聊天 —— 「{name}请和我说话」也行；开启后提到「{name}」即可聊
  关闭AI聊天 —— 「不理{name}了」也行；关闭后不再回应你（默认关闭）
  连续对话会记住上文；告诉过{name}的事会被长期记住；每次对话消耗星星

==记录管理==
  重置聊天 —— 私聊：清空我的对话记录；群聊：清空本群对话记录
  重置记忆 —— 清空我的全部长期记忆
  mlai状态 —— 查看我的记忆与对话概况"""


async def _finish_as_forward(
    matcher: Matcher, event: Event, bot: Bot, text: str
) -> None:
    """帮助长文本以合并转发发送，避免刷屏；官Bot 平台自动转图片（双列
    排版）；失败降级为直接发送。"""
    sent = False
    try:
        chain = await build_forward_chain(bot, [text])
        await send_group_forward_msg(
            event, bot, chain, width=1000, two_column=True
        )
        sent = True
    except Exception as error:
        logger.warning(f"[mlai] 合并转发发送失败，回退纯文本: {error}")
    if sent:
        await matcher.finish()
    await matcher.finish(text)


# ===== 帮助 =====

mlai_help_cmd = on_command(
    "聊天帮助", aliases={"mlai帮助", "AI对话帮助"}, priority=4, block=True
)


@mlai_help_cmd.handle()
async def handle_help(bot: Bot, event: Event, matcher: Matcher):
    await _finish_as_forward(matcher, event, bot, _help_text())


# ===== 对话 =====

# 触发：消息中任意位置出现触发词（on_message 规则实时读配置，改名
# 即时生效）；mlai / AI对话 前缀命令始终可用。低优先级且不 block，
# 不干扰其他插件的命令
mlai_mention_msg = on_message(rule=Rule(_bot_name_rule), priority=10, block=False)
mlai_chat_cmd = on_command("mlai", aliases={"AI对话"}, priority=5, block=True)


async def _process_chat(
    event: Event,
    matcher: Matcher,
    bot: Bot,
    text: str,
    uid: int,
    images: Optional[list[str]] = None,
):
    scene = _resolve_scene(event)
    # 场景门槛（与触发规则同判，防其它入口绕过）：群聊须群聊域开启且
    # 在白名单；私聊开关关闭时仅等级 0 SU
    if scene["is_group"]:
        if not _group_ready(scene):
            return
    elif not settings.private_enable() and not _is_owner(uid):
        return
    if _is_self_message(event):
        return  # OneBot 上报的自身消息：bot 回复已由发送侧落库，防重复
    # 用户级聊天开关（默认关闭）：未开启则完全不响应（静默）
    if not await _chat_enabled(scene, uid):
        return
    speaker = await _sender_name(event, uid)
    if not text and not images:
        await _finish_as_forward(matcher, event, bot, _help_text())

    if not settings.enabled():
        await matcher.finish("AI 对话功能当前未开启")
    if not chat_freq.check(uid):
        await matcher.finish(f"太快啦，休息 {int(chat_freq.left_time(uid)) + 1} 秒再来～")
    if not settings.is_configured():
        await matcher.finish(
            "生效预设尚未配置接口（mlai_api_key / mlai_api_base_url），"
            "请到配置面板「AI对话」分区的预设槽位填写"
        )

    if uid in _processing_uids:
        return  # 已有等待返回中的对话：后续触发直接静默
    _processing_uids.add(uid)
    try:
        result = await run_dialogue(
            text=text,
            uid=uid,
            is_su=is_su(uid),
            scene=scene,
            chat_fn=chat_completion_message,
            images=images,
            speaker=speaker,
        )
    except MlaiAPIError as e:
        await matcher.finish(f"AI 出错了：{e}")
    finally:
        _processing_uids.discard(uid)
    if result["status"] == "silent":
        # 星星不足：静默终止（不回复、不计冷却，消息也不落库）
        return
    if result["status"] == "refused":
        await matcher.finish("这个话题不太合适，换个别的聊聊吧～")
    if result["status"] == "unbound_group":
        # 入口白名单已保证官Bot 群聊均已绑定，此分支仅为防御性兜底
        await matcher.finish(
            "官Bot 群聊对话需要先在配置面板 mlai_group_bind 中以本群真实"
            "群号为键、group_openid 为值添加白名单后才能使用"
        )
    if result["status"] == "error":
        await matcher.finish(result["message"])

    chat_freq.start_cd(uid, settings.user_cd())
    await matcher.finish(result["reply"])


# ===== 旁听记录 =====
# 白名单群聊与私聊的每条消息（文本/图/图文，无论是否触发对话）都进
# 临时上下文，动态滚动。触发对话的消息由对话路径记录（避免双写），
# 此处跳过；mlai 前缀命令（priority=5, block=True）命中时阻断本匹配器
mlai_record_msg = on_message(priority=20, block=False)


def _recording_target(scene: dict, uid: int) -> Optional[tuple[str, str]]:
    """上下文存储目标：群聊须群聊域开启且在白名单（官Bot 群先解析
    openid → 绑定真实群号），取本群行；私聊须私聊开关开启（关闭时仅
    等级 0 SU），取用户行。不满足返回 None 不记录。"""
    if scene["is_group"]:
        if not settings.group_enable():
            return None
        if scene["openid"]:
            bound = settings.group_bind_id(scene["openid"])
            if not bound:
                return None
            scene = {**scene, "group_key": bound}
        if not settings.group_whitelisted(scene):
            return None
        return KIND_GROUP, scene["group_key"]
    if not settings.private_enable() and not _is_owner(uid):
        return None
    return KIND_USER, str(uid)


# mlai 非对话类指令（帮助/状态/重置/聊天开关，含别名）：指令消息与
# bot 回复都不进入上下文——命令匹配器 block=True 已阻断本匹配器，此处
# 再显式跳过双保险。对话指令（mlai/AI对话）不在列，由对话路径记录
_MLAI_COMMAND_PREFIXES = (
    "聊天帮助", "mlai帮助", "AI对话帮助",
    "mlai状态", "AI对话状态",
    "重置聊天", "mlai重置", "AI对话重置",
    "重置记忆", "重置群聊记忆", "重置全局记忆", "重置所有记忆",
    "启用AI聊天", "启用ai聊天", "关闭AI聊天", "关闭ai聊天",
    f"{settings.bot_name()}请和我说话", f"不理{settings.bot_name()}了",
    "冰祈帮助", f"{settings.bot_name()}帮助",
)


def _is_mlai_command(text: str) -> bool:
    """是否 mlai 非对话类指令消息（容忍 / # 等命令起始符与前导空白）。"""
    body = str(text or "").strip().lstrip("/#＃").lstrip()
    return any(body.startswith(p) for p in _MLAI_COMMAND_PREFIXES)


def _is_chat_command(text: str) -> bool:
    """是否 mlai/AI对话 前缀的对话命令消息（由对话命令路径处理：正常
    对话时由对话路径记录；触发限频被拒时两者都不落库）。"""
    body = str(text or "").strip().lstrip("/#＃").lstrip()
    return body.startswith(("mlai", "AI对话"))


@mlai_record_msg.handle()
async def handle_passive_record(
    event: Event,
    uid: int = Depends(get_uid),
):
    """旁听记录：不触发对话的消息也进入临时上下文（带发言人昵称）。"""
    if _is_self_message(event):
        return  # bot 自身消息已由发送侧落库，不重复记录
    target = _recording_target(_resolve_scene(event), uid)
    if target is None:
        return
    kind, owner = target
    try:
        text = event.message.extract_plain_text() if event.message else ""
    except Exception:
        return
    text = text.strip()
    if _trigger_match(text):
        return  # 触发消息由对话路径记录
    if _is_mlai_command(text) or _is_chat_command(text):
        return  # mlai 指令消息不入上下文：非对话类指令完全排除，
        # 对话命令由对话路径记录（限频被拒时不落库）
    images = await _capture_images(event)
    if not text and not images:
        return
    await MlaiStore.append_message(
        kind, owner, ROLE_USER, await _sender_name(event, uid),
        text or "（图片）", settings.temp_limit(), images=images,
    )


@mlai_mention_msg.handle()
async def handle_mention_chat(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    uid: int = Depends(get_uid),
):
    """触发词匹配：对话内容 = 去掉首次出现的触发词后的整条消息。"""
    raw = event.message.extract_plain_text() if event.message else ""
    text = raw.replace(settings.bot_name(), "", 1).strip()
    await _process_chat(
        event, matcher, bot, text, uid, images=_extract_image_urls(event)
    )


@mlai_chat_cmd.handle()
async def handle_chat(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    args: Optional[Message] = CommandArg(),
    uid: int = Depends(get_uid),
):
    raw = event.message.extract_plain_text() if event.message else ""
    if _is_blocked(raw):
        return  # 屏蔽词命中：静默不响应
    text = args.extract_plain_text().strip() if args else ""
    await _process_chat(
        event, matcher, bot, text, uid, images=_extract_image_urls(event)
    )


# ===== 聊天开关指令 =====
# 用户级、分场景（私聊每用户、群聊每用户每群），默认关闭：未开启时
# 聊天完全不响应（静默）。别名中的触发词在插件加载时按当前 bot_name
# 固定（改名后需重启才更新别名；触发词本身实时生效）
switch_on_cmd = on_command(
    "启用AI聊天",
    aliases={"启用ai聊天", f"{settings.bot_name()}请和我说话"},
    priority=4,
    block=True,
)
switch_off_cmd = on_command(
    "关闭AI聊天",
    aliases={"关闭ai聊天", f"不理{settings.bot_name()}了"},
    priority=4,
    block=True,
)


async def _handle_chat_switch(
    event: Event, matcher: Matcher, uid: int, enabled: bool
):
    """聊天开关指令：场景门槛与对话同判（非白名单群/私聊关闭时静默），
    指令与回复都不进入上下文（block=True 已阻断记录器）。"""
    scene = _resolve_scene(event)
    if scene["is_group"]:
        if not _group_ready(scene):
            return
    elif not settings.private_enable() and not _is_owner(uid):
        return
    if _is_self_message(event):
        return
    owner = _switch_owner(scene, uid)
    if owner is None:
        return
    await MlaiStore.set_switch(owner, enabled)
    name = settings.bot_name()
    if enabled:
        await matcher.finish(f"好呀～{name}会回应你啦")
    await matcher.finish(f"好，{name}先不打扰你啦")


@switch_on_cmd.handle()
async def handle_switch_on(
    event: Event, matcher: Matcher, uid: int = Depends(get_uid)
):
    await _handle_chat_switch(event, matcher, uid, True)


@switch_off_cmd.handle()
async def handle_switch_off(
    event: Event, matcher: Matcher, uid: int = Depends(get_uid)
):
    await _handle_chat_switch(event, matcher, uid, False)


# ===== 重置指令 =====

reset_chat_cmd = on_command(
    "重置聊天", aliases={"mlai重置", "AI对话重置"}, priority=4, block=True
)


@reset_chat_cmd.handle()
async def handle_reset_chat(
    event: Event,
    matcher: Matcher,
    uid: int = Depends(get_uid),
):
    """私聊：任何人可清空自己的私聊上下文；群聊：仅等级 0 SU 可清空
    本群上下文。"""
    scene = _resolve_scene(event)
    if scene["is_group"]:
        if not _is_owner(uid):
            await matcher.finish("无权重置群聊记忆")
        group_key = _resolve_group_key(matcher, event)
        if group_key is None:
            await matcher.finish("请在群聊中使用本指令")
        if group_key == "":
            await matcher.finish(
                "本群尚未绑定真实群号（面板 mlai_group_bind），无法定位群聊上下文"
            )
        deleted = await MlaiStore.clear_temp(KIND_GROUP, group_key)
        if not deleted:
            await matcher.finish("本群暂无对话记录，无需重置")
        await matcher.finish(f"已清空本群对话记录（{deleted} 条）")
    deleted = await MlaiStore.clear_temp(KIND_USER, str(uid))
    if not deleted:
        await matcher.finish("你没有正在进行的对话记录，无需重置～")
    await matcher.finish(f"已清空你的对话记录（{deleted} 条），开始新的对话吧～")


reset_memory_cmd = on_command("重置记忆", priority=4, block=True)


@reset_memory_cmd.handle()
async def handle_reset_memory(matcher: Matcher, uid: int = Depends(get_uid)):
    deleted = await MlaiStore.clear_memories(SCOPE_USER, str(uid))
    if not deleted:
        await matcher.finish("你还没有长期记忆，无需重置～")
    await matcher.finish(f"已清空你的全部记忆（{deleted} 条），我们重新认识吧～")


def _resolve_group_key(matcher: Matcher, event: Event) -> Optional[str]:
    """解析当前群聊的存储键（官Bot 需已绑定真实群号）。失败时直接回绝。"""
    scene = _resolve_scene(event)
    if not scene["is_group"]:
        return None
    if scene["openid"]:
        bound = settings.group_bind_id(scene["openid"])
        if not bound:
            return ""
        return bound
    return scene["group_key"]


reset_group_memory_cmd = on_command("重置群聊记忆", priority=4, block=True)


@reset_group_memory_cmd.handle()
async def handle_reset_group_memory(
    event: Event,
    matcher: Matcher,
    uid: int = Depends(get_uid),
):
    if not _is_owner(uid):
        await matcher.finish("无权重置群聊记忆")
    group_key = _resolve_group_key(matcher, event)
    if group_key is None:
        await matcher.finish("请在群聊中使用本指令")
    if group_key == "":
        await matcher.finish(
            "本群尚未绑定真实群号（面板 mlai_group_bind），无法定位群聊记忆"
        )
    deleted = await MlaiStore.clear_memories(SCOPE_GROUP, group_key)
    if not deleted:
        await matcher.finish("本群聊暂无记忆，无需重置")
    await matcher.finish(f"已清空本群聊记忆（{deleted} 条）")


reset_global_memory_cmd = on_command("重置全局记忆", priority=4, block=True)


@reset_global_memory_cmd.handle()
async def handle_reset_global_memory(matcher: Matcher, uid: int = Depends(get_uid)):
    if not _is_owner(uid):
        await matcher.finish("无权重置全局记忆")
    deleted = await MlaiStore.clear_memories(SCOPE_GLOBAL, "")
    if not deleted:
        await matcher.finish("全局记忆为空，无需重置")
    await matcher.finish(f"已清空全局记忆（{deleted} 条）")


reset_all_memory_cmd = on_command("重置所有记忆", priority=4, block=True)


@reset_all_memory_cmd.handle()
async def handle_reset_all_memory(matcher: Matcher, uid: int = Depends(get_uid)):
    if not _is_owner(uid):
        await matcher.finish("无权重置所有记忆")
    deleted = await MlaiStore.clear_all_memories()
    if not deleted:
        await matcher.finish("当前没有任何记忆，无需重置")
    await matcher.finish(f"已清空全部记忆（全局/群聊/用户共 {deleted} 条）")


mlai_status_cmd = on_command("mlai状态", aliases={"AI对话状态"}, priority=4, block=True)


@mlai_status_cmd.handle()
async def handle_status(
    event: Event,
    matcher: Matcher,
    uid: int = Depends(get_uid),
):
    memory_count = await MlaiStore.count_memories(SCOPE_USER, str(uid))
    temp_count = await MlaiStore.count_temp(KIND_USER, str(uid))
    window = settings.active_window()
    max_rows = settings.temp_limit()
    rows = min(temp_count, max_rows)

    scene = _resolve_scene(event)
    switch_on = await _chat_enabled(scene, uid)
    if not scene["is_group"]:
        scene_line = "当前场景：私聊"
    elif scene["openid"]:
        bound = settings.group_bind_id(scene["openid"])
        scene_line = (
            f"当前场景：群聊（group_openid: {scene['openid']}"
            + (f"，已绑定群号 {bound}" if bound else "")
            + f"，{'在' if settings.group_whitelisted(scene) else '不在'}白名单）"
        )
    else:
        scene_line = (
            f"当前场景：群聊（群号: {scene['group_key']}，"
            f"{'在' if settings.group_whitelisted(scene) else '不在'}白名单）"
        )
    await matcher.finish(
        f"""【mlai 状态】
我的记忆：{memory_count} 条（活跃窗口 {window} 条）
我的最近对话：{rows} / {max_rows} 条
AI聊天：{'开启' if switch_on else '关闭'}（发送 启用AI聊天 / 关闭AI聊天 切换）
群聊记忆：{'开启' if settings.group_enable() else '关闭'}
冷却：{settings.user_cd()} 秒/次
{scene_line}"""
    )


async def _db_vacuum_job():
    """VACUUM 回收空闲页（临时上下文整行重写产生的页碎片不会自动
    归还操作系统，文件只涨不缩）。"""
    pages = await MlaiStore.vacuum()
    if pages:
        logger.info(f"[mlai] 数据库 VACUUM 回收 {pages} 页空闲空间")


def _register_vacuum_schedule():
    """启动时按需回收一次 + 每日 04:30 定时回收。"""
    asyncio.create_task(_db_vacuum_job())
    try:
        from nonebot_plugin_apscheduler import scheduler as aps_scheduler
    except Exception:
        logger.warning("[mlai] 未安装 apscheduler，跳过定时 VACUUM")
        return
    try:
        aps_scheduler.add_job(
            _db_vacuum_job, "cron", hour="4", minute="30",
            id="mlai_db_vacuum", replace_existing=True,
        )
    except Exception as e:
        logger.warning(f"[mlai] 定时 VACUUM 注册失败: {e}")


@driver.on_startup
async def init_mlai():
    """初始化 mlai 插件（与其他子插件共用 koinoribot.db）"""
    from pathlib import Path

    plugin_dir = Path(__file__).parent.parent.parent
    db_path = plugin_dir / "src" / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    MlaiStore.set_db_path(str(db_path))
    MlaiStore.init_database()
    _register_vacuum_schedule()
    if settings.enabled() and not settings.is_configured():
        logger.warning(
            "[mlai] 已启用但生效预设未配置接口（mlai_api_key / "
            "mlai_api_base_url），对话暂不可用"
        )
    logger.info(
        f"mlai AI 对话插件初始化完成（生效预设：{settings.preset_label()}，"
        f"活跃窗口 {settings.active_window()}，群聊域"
        f"{'开' if settings.group_enable() else '关'}）"
    )
