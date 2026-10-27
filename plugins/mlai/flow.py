"""mlai 两轮对话编排：计价、审核、上下文采集、记忆操作执行。"""


import json
import math
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

from nonebot.log import logger

from . import settings
from .client import download_image_data_url
from .prompts import (
    ROUND1_SYSTEM,
    build_round2_system,
    build_tools_spec,
    build_user_content,
    parse_round1,
    parse_tool_message,
    render_context_block,
    sanitize_terms,
)
from .service import (
    KIND_GROUP,
    KIND_USER,
    ROLE_ASSISTANT,
    ROLE_USER,
    SCOPE_GLOBAL,
    SCOPE_GROUP,
    SCOPE_USER,
    MlaiStore,
)

# chat_fn: 与 client.chat_completion_message 同签名
# （(messages, *, tools, tool_choice) -> {content, tool_calls}），
# 注入以便测试替换
ChatFn = Callable[..., Awaitable[dict]]

# 多轮工具循环上限：模型只发工具调用不给正文时，最多回填工具结果
# 续答的轮数（防止循环失控推高成本；续答请求仅在尾部追加消息，仍
# 命中前缀缓存）
TOOL_LOOP_MAX_ROUNDS = 3

# 单条消息最多识别的图片数
MAX_IMAGES = 3

# 计价时每张图片固定折算的字符数（base64 不计入字符统计）
IMAGE_PRICE_CHARS = 100


def _spend_starstone_default(uid: int, cost: int) -> bool:
    """默认扣费：原子足额扣减星星（consume 语义，绝不透支）。

    返回 True=已扣减；False=余额不足或用户资产行不存在（一律静默）。"""
    from ...money import checked_decrease

    return bool(checked_decrease(uid, "starstone", cost))


def _refund_starstone_default(uid: int, cost: int) -> bool:
    """默认退款：原路加回星星（refund 语义）。"""
    from ...money import increase

    return increase(uid, "starstone", cost) == 1


def allowed_scopes(is_group: bool, is_su: bool) -> set[str]:
    """本次对话允许修改的记忆域：用户记忆仅本人、群聊记忆仅群内（且
    群聊域开启——关闭时群记忆不进上下文，也不可写）、全局仅 SU。"""
    scopes = {SCOPE_USER}
    if is_group:
        scopes.add(SCOPE_GROUP)
    if is_su:
        scopes.add(SCOPE_GLOBAL)
    return scopes


async def gather_context(
    store: type[MlaiStore],
    scene: dict,
    uid: int,
    terms: list[str],
    exclude_seq: Optional[int] = None,
) -> dict:
    """采集第 2 轮上下文素材（活跃/检索记忆、临时上下文、可见 id 集）。

    临时上下文：群聊一律用本群行（入口白名单已保证），私聊用用户行；
    exclude_seq 起的条目（当前消息）不回放——它以末条用户消息参与请求。"""
    is_group = scene["is_group"]
    group_on = is_group and settings.group_enable()
    window = settings.active_window()
    per_scope = settings.archive_per_scope()

    targets = [(SCOPE_GLOBAL, ""), (SCOPE_USER, str(uid))]
    if group_on:
        targets.append((SCOPE_GROUP, scene["group_key"]))

    active: dict[str, list[dict]] = {}
    retrieved: dict[str, list[dict]] = {}
    # 本轮可见记忆编号（按域分组：id 域内独立编号，跨域会撞号）
    allowed_ids: dict[str, set[int]] = {
        s: set() for s in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_USER)
    }
    for scope, key in targets:
        memories = await store.active_memories(scope, key, window)
        active[scope] = memories
        allowed_ids[scope].update(m["id"] for m in memories)
        hits = await store.search_archived(scope, key, terms, per_scope, window)
        retrieved[scope] = hits
        allowed_ids[scope].update(m["id"] for m in hits)

    if is_group:
        temp = await store.get_temp(
            KIND_GROUP, scene["group_key"], settings.temp_limit(), exclude_seq
        )
    else:
        temp = await store.get_temp(
            KIND_USER, str(uid), settings.temp_limit(), exclude_seq
        )
    return {
        "active": active,
        "retrieved": retrieved,
        "allowed_ids": allowed_ids,
        "temp": temp,
        "group_on": group_on,
    }


def _fmt_time(ms: int) -> str:
    """[年 月-日 时:分] 时间标注（24 小时制），历史行与当前提问统一。"""
    return time.strftime("%Y %m-%d %H:%M", time.localtime(ms / 1000))


def _history_text(m: dict) -> str:
    """历史记录行：[时间] + 明确发言人归属（多人群聊防混淆——
    图片尤其要标清是谁发的）。bot 消息只标时间不加昵称。"""
    ts = f"[{_fmt_time(m['time'])}]" if m.get("time") else ""
    if m.get("role") != ROLE_USER:
        return f"{ts}{m['content']}"
    name = str(m.get("name") or "").strip()
    content = m["content"]
    if m.get("images"):
        if content == "（图片）":
            return f"{ts}「{name or '某人'}」发来一张图片"
        return f"{ts}「{name or '某人'}」发送了图片并说：{content}"
    if name:
        return f"{ts}{name}: {content}"
    return f"{ts}{content}"


_TOOL_ACTION = {
    "keep_status": "keep",
    "add_memory": "add",
    "modify_memory": "modify",
    "delete_memory": "delete",
}


def _question_line(speaker: str, text: str, now_text: str) -> str:
    """当前提问行：[时间]昵称: 内容（时间戳与历史行同格式）。"""
    question = f"{speaker}: {text}" if speaker else text
    return f"[{now_text}]{question}"


def _assemble_round2(
    system: str, ctx: dict, question_line: str, image_parts: list[str]
) -> list[dict]:
    """组装第 2 轮请求消息（缓存友好布局；计价与实际请求共用）。

    结构：system（人设+说明，长期稳定）→ 对话记录块（独立 user 消息 +
    固定确认语，图片紧贴所属行）→ 末条 user 消息（记忆材料 + 提问行 +
    当前图片）。历史与当前输入结构性隔离，防止模型把记录中他人发言/
    图片当成当前提问者，服务商前缀缓存照常命中。"""
    messages = [{"role": "system", "content": system}]
    if ctx["temp"]:
        record_parts: list[dict] = [{
            "type": "text",
            "text": "# 对话记录（背景参考）",
        }]
        for m in ctx["temp"]:
            record_parts.append({"type": "text", "text": _history_text(m)})
            for url in m.get("images") or []:
                record_parts.append(
                    {"type": "image_url", "image_url": {"url": url}}
                )
        messages.append({"role": "user", "content": record_parts})
        messages.append({"role": "assistant", "content": "（已阅读以上对话记录）"})
    context_block = render_context_block(ctx)
    user_parts = [context_block] if context_block else []
    user_parts.append(question_line)
    messages.append({
        "role": "user",
        "content": build_user_content("\n\n".join(user_parts), image_parts),
    })
    return messages


def _text_chars(messages: list[dict]) -> int:
    """计价字符数：全部消息的文本内容总长（图片 data URL 不计入）。"""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(str(part.get("text") or ""))
    return total


def _assistant_tool_message(message: dict) -> dict:
    """把模型带工具调用的助手消息还原为可回传的 assistant 消息。"""
    calls = [
        {
            "id": str(call.get("id", "") or ""),
            "type": "function",
            "function": {
                "name": str(call.get("name", "") or ""),
                "arguments": json.dumps(
                    call.get("arguments") or {}, ensure_ascii=False
                ),
            },
        }
        for call in message.get("tool_calls") or []
    ]
    return {
        "role": "assistant",
        "content": str(message.get("content") or ""),
        "tool_calls": calls,
    }


def _tool_result_messages(message: dict, ops: list, results: list) -> list[dict]:
    """把本轮工具调用的执行结果回填为 tool 消息（多轮续答用）。

    解析层丢弃的调用回执忽略原因；keep_status 回执维持现状；其余按
    apply_ops 的结果顺序对应（超出操作上限的回执「已处理」）。
    """
    remaining = list(ops)
    pending = list(results)
    messages = []
    for call in message.get("tool_calls") or []:
        name = str(call.get("name", "") or "")
        action = _TOOL_ACTION.get(name)
        if action == "keep":
            content = "已维持现状"
        elif action is None:
            content = "未知工具，已忽略"
        else:
            matched = next(
                (op for op in remaining if op["op"] == action), None
            )
            if matched is None:
                content = "操作已忽略（参数不完整或与其他操作冲突）"
            else:
                remaining.remove(matched)
                content = pending.pop(0) if pending else "已处理"
        messages.append({
            "role": "tool",
            "tool_call_id": str(call.get("id", "") or ""),
            "name": name,
            "content": content,
        })
    return messages


async def apply_ops(
    ops: list[dict],
    ctx: dict,
    scene: dict,
    uid: int,
    is_su: bool,
    store: type[MlaiStore],
) -> list[str]:
    """执行记忆操作（硬校验），返回结果摘要（仅日志用）。"""
    results: list[str] = []
    scopes = allowed_scopes(
        scene["is_group"] and settings.group_enable(), is_su
    )
    max_len = settings.memory_max_len()
    for op in ops[: settings.ops_limit()]:
        action = op["op"]
        try:
            if action == "add":
                scope = op["scope"]
                if scope not in scopes:
                    results.append(f"add {scope}: 拒绝（无该域权限）")
                    continue
                content = op["content"]
                if len(content) > max_len:
                    results.append(f"add {scope}: 拒绝（超 {max_len} 字）")
                    continue
                scope_key = scene["group_key"] if scope == SCOPE_GROUP else (
                    "" if scope == SCOPE_GLOBAL else str(uid)
                )
                mem_id = await store.add_memory(
                    scope, scope_key, content, op.get("keywords", "")
                )
                if mem_id is None:
                    results.append(f"add {scope}: 跳过（内容已存在）")
                else:
                    results.append(f"add {scope}#{mem_id}: {content[:50]}")
            elif action in ("modify", "delete"):
                # id 域内独立编号：必须连同所属域一起定位；存储键由当前
                # 场景决定（群域=本群，用户域=本人），跨群/跨人不可达
                scope = op["scope"]
                mem_id = op["id"]
                if scope not in scopes:
                    results.append(f"{action} {scope}#{mem_id}: 拒绝（无该域权限）")
                    continue
                if mem_id not in (ctx["allowed_ids"].get(scope) or set()):
                    results.append(f"{action} {scope}#{mem_id}: 拒绝（非本轮可见记忆）")
                    continue
                if scope == SCOPE_GROUP:
                    mem_key = str(scene["group_key"])
                elif scope == SCOPE_USER:
                    mem_key = str(uid)
                else:
                    mem_key = ""
                mem = await store.get_memory(scope, mem_key, mem_id)
                if mem is None:
                    results.append(f"{action} {scope}#{mem_id}: 拒绝（记忆不存在）")
                    continue
                if action == "delete":
                    ok = await store.delete_memory(scope, mem_key, mem_id)
                    results.append(f"delete {scope}#{mem_id}: {'成功' if ok else '失败'}")
                else:
                    content = op["content"]
                    if len(content) > max_len:
                        results.append(f"modify {scope}#{mem_id}: 拒绝（超 {max_len} 字）")
                        continue
                    keywords = op.get("keywords")
                    if keywords is None:
                        keywords = mem.get("keywords", "")
                    ok = await store.update_memory(scope, mem_key, mem_id, content, keywords)
                    results.append(f"modify {scope}#{mem_id}: {'成功' if ok else '失败'}")
        except Exception as e:  # 单条操作异常不影响其余
            logger.error(f"[mlai] 记忆操作异常 {op}: {e}")
            results.append(f"{action}: 异常")
    if results:
        logger.info(f"[mlai] 记忆操作 uid={uid}: {'; '.join(results)}")
    return results


async def run_dialogue(
    *,
    text: str,
    uid: int,
    is_su: bool,
    scene: dict,
    chat_fn: ChatFn,
    store: type[MlaiStore] = MlaiStore,
    images: Optional[list[str]] = None,
    speaker: str = "",
    spend_fn: Callable[[int, int], bool] = _spend_starstone_default,
    refund_fn: Callable[[int, int], bool] = _refund_starstone_default,
) -> dict:
    """完整两轮对话流程。

    images 为消息中的图片附件 URL（官Bot 临时链接），进入流程即下载为
    data URL 并注入两轮的用户消息（vision 格式）；纯图片消息文本以
    占位符替代。speaker 为当前发言人昵称（进上下文记录与本次请求）。
    spend_fn / refund_fn 为计价扣费/退款函数（默认原子扣退星星），注入
    以便测试替换。计价 = 文本字符数 + 每张图片固定 100 字，向上取整；
    NSFW 未过审或任何原因导致第 2 轮未正常完成时已扣星星原路退回。

    返回：
      {"status": "ok", "reply", "ops_results"}        正常完成
      {"status": "silent"}                            星星不足，静默终止
      {"status": "refused"}                            NSFW 硬截断
      {"status": "unbound_group", "openid"}            官Bot群聊未绑定群号
      {"status": "error", "message"}                   审核解析失败/接口错误
    """
    if scene["is_group"] and settings.group_enable() and scene.get("openid"):
        bound = settings.group_bind_id(scene["openid"])
        if not bound:
            return {"status": "unbound_group", "openid": scene["openid"]}
        scene = {**scene, "group_key": bound}

    image_parts: list[str] = []
    if images:
        if not text:
            text = "（用户发送了图片）"
        for url in images[:MAX_IMAGES]:
            image_parts.append(await download_image_data_url(url))

    # 临时上下文行选择：群聊一律本群行（入口白名单已保证），私聊用户行
    if scene["is_group"]:
        temp_kind, temp_owner = KIND_GROUP, scene["group_key"]
    else:
        temp_kind, temp_owner = KIND_USER, str(uid)

    # 聊天计价（第 1 轮审核前）：先组装审核前可得的第 2 轮完整输入
    # （当前消息此时尚未落库、R1 关键词尚未产生，归档检索为空——即
    # 与最终请求的差异仅在归档检索条目）；文本字符数 + 每张图片固定
    # 100 字，向上取整扣星星；不足则静默终止，组装内容全部丢弃
    # （不落库、不回复）
    spent = 0
    if settings.starstone_enable():
        pre_ctx = await gather_context(store, scene, uid, [])
        price_messages = _assemble_round2(
            build_round2_system(settings.system_prompt()),
            pre_ctx,
            _question_line(
                speaker, text, datetime.now().strftime("%Y %m-%d %H:%M")
            ),
            image_parts,
        )
        chars = (
            _text_chars(price_messages)
            + IMAGE_PRICE_CHARS * len(image_parts)
        )
        cost = math.ceil(chars / settings.starstone_chars())
        if not spend_fn(uid, cost):
            return {"status": "silent"}
        spent = cost

    async def _rounds() -> dict:
        r1_message = None
        for _ in range(2):  # 解析失败重试一次
            r1_message = await chat_fn(
                [
                    {"role": "system", "content": ROUND1_SYSTEM},
                    {
                        "role": "user",
                        "content": build_user_content(text, image_parts),
                    },
                ]
            )
            nsfw, keywords = parse_round1(r1_message.get("content") or "")
            if nsfw is not None:
                break
        else:
            # 审核解析失败：消息不入上下文（未过审内容一律不落库）
            return {"status": "error", "message": "审核模块返回异常，请稍后再试"}
        if nsfw is True:
            # NSFW 硬截断：未过审消息与拒绝回复都不入上下文，防止内容
            # 残留在历史里跨消息逃逸审核
            return {"status": "refused"}

        # 过审后才把当前消息写入上下文（旁听/对话统一记录），seq 供读取时
        # 排除自身——当前消息以末条用户消息参与请求，不重复进历史
        cur_seq = await store.append_message(
            temp_kind, temp_owner, ROLE_USER, speaker, text,
            settings.temp_limit(), images=image_parts,
        )

        ctx = await gather_context(
            store, scene, uid, sanitize_terms(keywords), exclude_seq=cur_seq
        )
        system = build_round2_system(settings.system_prompt())
        messages = _assemble_round2(
            system,
            ctx,
            _question_line(
                speaker, text, datetime.now().strftime("%Y %m-%d %H:%M")
            ),
            image_parts,
        )

        # 第 2 轮携带记忆维护工具；tool_choice 默认 auto（部分服务商如
        # DeepSeek 思考模式不支持 required，auto 已足够可靠），服务商
        # 完全不支持 tools 时由 client 自动降级为纯对话。模型只发起工具
        # 调用未给正文时，回填工具执行结果让其继续作答（多轮工具循环）
        ops_results: list[str] = []
        reply = ""
        for _ in range(TOOL_LOOP_MAX_ROUNDS):
            message = await chat_fn(
                messages, tools=build_tools_spec(settings.memory_max_len()),
                tool_choice="auto",
            )
            reply, ops = parse_tool_message(message)
            round_results: list[str] = []
            if ops:
                round_results = await apply_ops(ops, ctx, scene, uid, is_su, store)
                ops_results.extend(round_results)
            if reply:
                break
            if not (message.get("tool_calls") or []):
                break  # 既无正文也无工具调用：跳出走无工具兜底
            messages.append(_assistant_tool_message(message))
            messages.extend(_tool_result_messages(message, ops, round_results))
        if not reply:
            # 兜底：不带工具再要一次纯回复（无工具可调，模型必然走正文）
            message = await chat_fn(messages)
            reply, ops = parse_tool_message(message)
            if ops:
                ops_results.extend(
                    await apply_ops(ops, ctx, scene, uid, is_su, store)
                )
            reply = reply or "……"

        # bot 回复落库（昵称固定为触发词；官Bot 平台不上报自身消息，由
        # 发送侧自记）
        await store.append_message(
            temp_kind, temp_owner, ROLE_ASSISTANT, settings.bot_name(),
            reply, settings.temp_limit(),
        )
        return {"status": "ok", "reply": reply, "ops_results": ops_results}

    try:
        result = await _rounds()
    except BaseException:
        # 任何原因（接口异常等）导致流程未正常完成：已扣星星原路退回
        if spent:
            refund_fn(uid, spent)
        raise
    if spent and result.get("status") != "ok":
        # NSFW 未过审 / 审核解析失败：第 2 轮未正常进行，原路退回
        refund_fn(uid, spent)
    return result
