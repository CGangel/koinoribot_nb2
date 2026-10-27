"""mlai 提示词、工具定义与解析。"""


import json
import re
from typing import Any, Optional

from nonebot.log import logger

from .service import SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_USER

JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

ROUND1_SYSTEM = (
    "你是内容审核与关键词提取模块，只输出一个 JSON 对象，不要输出任何"
    "其他文字、解释或思考过程。对用户输入做两项判断：\n"
    '{"nsfw": true, "keywords": ["关键词1", "关键词2"]}\n'
    "nsfw：输入是否包含色情、暴力、赌博、违法等不适宜内容（true/false）；\n"
    "keywords：3~8 个可用于检索记忆库的关键词（中文优先，保留专有名词与"
    "人名）。输入本身也要作为语境考虑，但不要把中性词误判为不适宜。"
    "输入可能包含图片附件，图片内容同样适用于以上两项判断。"
)

MEMORY_TOOLS_SECTION = """# 记忆维护（工具调用）
你拥有跨对话的长期记忆库，通过工具调用维护：keep_status（维持现状）、
add_memory（新增记忆）、modify_memory（修改记忆）、delete_memory（删除记忆）。
- 每次回答必须至少调用一个工具：有值得记忆的内容就调用相应工具（可多选），
  没有则调用 keep_status。
- keep_status 不可与其他记忆工具同时调用；同一条记忆每轮只能被一个工具操作。
- 记忆编号（id）在所属域内独立计数（不同域会有相同编号），修改/删除
  时必须同时提供该记忆所属的 scope；所属域见上下文中的分区标题。
- 是否值得记忆由你自行判断：稳定的、以后用得上的信息值得记，一次性闲聊不值得。
- 记忆内容用客观中立的第三人称写事实本身，不用角色口癖，不写对话过程。
- 记忆要尽可能简洁：只记事实要点（人物、事项、偏好、数字等关键词语），
  省略修饰、改动过程、背景铺垫，不追求完整句子。"""


def build_tools_spec(max_len: int) -> list[dict]:
    """四个记忆维护工具的 OpenAI tools 定义。"""
    reply_desc = (
        "给用户看的完整回复正文；content 为空时此处必须填写完整回复，"
        "仅当 content 已包含完整回复时才可留空"
    )
    scope_desc = (
        "记忆归属：user=该用户本人（默认），group=本群，global=全局"
        "（仅超级用户对话可用，无权限时选 user）"
    )
    return [
        {
            "type": "function",
            "function": {
                "name": "keep_status",
                "description": "维持现状：本轮没有值得新增/修改/删除的记忆时调用；不可与其他记忆工具同时调用",
                "parameters": {
                    "type": "object",
                    "properties": {"reply": {"type": "string", "description": reply_desc}},
                    "required": ["reply"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_memory",
                "description": "新增一条长期记忆（值得记住的新信息）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["user", "group", "global"], "description": scope_desc},
                        "content": {"type": "string", "description": f"记忆内容（≤{max_len} 字，客观第三人称事实）"},
                        "keywords": {"type": "string", "description": "3~5 个检索关键词，逗号分隔"},
                        "reply": {"type": "string", "description": reply_desc},
                    },
                    "required": ["scope", "content", "reply"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "modify_memory",
                "description": "修改一条过时记忆（id 与 scope 必须来自本次上下文列出的记忆）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "要修改的记忆编号（所属域内编号）"},
                        "scope": {"type": "string", "enum": ["user", "group", "global"], "description": "该记忆所属域（按上下文分区标题选择）"},
                        "content": {"type": "string", "description": f"更新后的内容（≤{max_len} 字）"},
                        "keywords": {"type": "string", "description": "更新后的关键词，逗号分隔（可省略沿用旧值）"},
                        "reply": {"type": "string", "description": reply_desc},
                    },
                    "required": ["id", "scope", "content", "reply"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_memory",
                "description": "删除一条过时或错误的记忆（id 与 scope 必须来自本次上下文列出的记忆）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "要删除的记忆编号（所属域内编号）"},
                        "scope": {"type": "string", "enum": ["user", "group", "global"], "description": "该记忆所属域（按上下文分区标题选择）"},
                        "reply": {"type": "string", "description": reply_desc},
                    },
                    "required": ["id", "scope", "reply"],
                },
            },
        },
    ]


def build_user_content(text: str, image_data_urls: list[str]):
    """组装用户消息 content：无图为纯文本；有图为 OpenAI vision 格式
    （文本部分在前、图片引用在后）。image_data_urls 为 data URL。"""
    if not image_data_urls:
        return text
    content: list[dict] = [{"type": "text", "text": text}]
    content.extend(
        {"type": "image_url", "image_url": {"url": url}}
        for url in image_data_urls
    )
    return content


def _valid_id(raw) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def parse_tool_message(message: dict) -> tuple[str, list[dict]]:
    """解析第 2 轮助手消息 → (用户可见回复, 记忆操作列表)。

    - 回复正文取 content；required 模式下 content 可能为空，回落到
      工具参数里的 reply（取第一个非空）；两者皆空返回空串，由 flow
      兜底（不带工具重试一次拿纯回复）。
    - 组合规则过滤：keep_status 与其他并存时忽略 keep_status；
      同一记忆 id 每轮只保留第一个操作；非法条目丢弃。
    - 未携带任何工具调用（auto 降级后模型未调用）视为维持现状。
    """
    reply = str(message.get("content") or "").strip()
    ops: list[dict] = []
    keep_called = False
    for call in message.get("tool_calls") or []:
        name = str(call.get("name", "") or "")
        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        if not reply:
            tool_reply = str(args.get("reply") or "").strip()
            if tool_reply:
                reply = tool_reply
        if name == "keep_status":
            keep_called = True
        elif name == "add_memory":
            scope = str(args.get("scope", "") or "").strip().lower()
            content = str(args.get("content", "") or "").strip()
            if scope in ("user", "group", "global") and content:
                ops.append({
                    "op": "add", "scope": scope, "content": content,
                    "keywords": str(args.get("keywords", "") or "").strip(),
                })
        elif name == "modify_memory":
            scope = str(args.get("scope", "") or "").strip().lower()
            mem_id = _valid_id(args.get("id"))
            content = str(args.get("content", "") or "").strip()
            if mem_id is not None and scope in ("user", "group", "global") and content:
                ops.append({
                    "op": "modify", "scope": scope, "id": mem_id,
                    "content": content,
                    "keywords": str(args.get("keywords", "") or "").strip(),
                })
        elif name == "delete_memory":
            scope = str(args.get("scope", "") or "").strip().lower()
            mem_id = _valid_id(args.get("id"))
            if mem_id is not None and scope in ("user", "group", "global"):
                ops.append({"op": "delete", "scope": scope, "id": mem_id})

    if keep_called and ops:
        logger.info("[mlai] keep_status 与其他记忆工具同时调用，忽略 keep_status")
    filtered: list[dict] = []
    seen_ids: set[tuple[str, int]] = set()
    for op in ops:
        if op["op"] in ("modify", "delete"):
            key = (op["scope"], op["id"])
            if key in seen_ids:
                logger.info(f"[mlai] 记忆 {op['scope']}#{op['id']} 被多个工具操作，仅保留第一个")
                continue
            seen_ids.add(key)
        filtered.append(op)
    return reply, filtered


def _parse_json_payload(text: str, prefer: str = "object") -> Any:
    """从模型输出中提取 JSON 值（容忍代码围栏与前后杂文）。"""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    patterns = (
        [JSON_OBJECT_RE, JSON_ARRAY_RE]
        if prefer == "object"
        else [JSON_ARRAY_RE, JSON_OBJECT_RE]
    )
    for pattern in patterns:
        match = pattern.search(cleaned)
        if match is None:
            continue
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return None


def parse_round1(text: str) -> tuple[Optional[bool], list[str]]:
    """解析第 1 轮输出 → (nsfw 标记, 关键词列表)；解析失败返回 (None, [])。"""
    data = _parse_json_payload(text)
    if not isinstance(data, dict):
        return None, []
    nsfw = data.get("nsfw")
    if not isinstance(nsfw, bool):
        return None, []
    raw_keywords = data.get("keywords")
    if not isinstance(raw_keywords, list):
        raw_keywords = []
    keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
    return nsfw, keywords[:8]


def sanitize_terms(keywords: list[str], max_terms: int = 8) -> list[str]:
    """检索词清理：去 LIKE 通配符与空白、去重限长。"""
    seen: set[str] = set()
    terms: list[str] = []
    for raw in keywords:
        term = str(raw or "").strip().replace("%", "").replace("_", "")[:32]
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def render_memory_line(mem: dict) -> str:
    """单条记忆的展示行：[id] 内容（关键词: ...）"""
    keywords = str(mem.get("keywords", "") or "").strip()
    suffix = f"（关键词: {keywords}）" if keywords else ""
    return f"[{mem['id']}] {mem['content']}{suffix}"


def render_context_block(ctx: dict) -> str:
    """把记忆素材渲染为并入末尾用户消息的参考材料块。

    system 保持「人设 + 工具说明」稳定以命中前缀缓存，逐轮变化的
    活跃/检索记忆集中到请求尾部（本块）。无任何记忆时返回空串。
    """
    parts: list[str] = []
    for scope, title in (
        (SCOPE_GLOBAL, "全局活跃记忆"),
        (SCOPE_GROUP, "本群活跃记忆"),
        (SCOPE_USER, "该用户活跃记忆"),
    ):
        memories = ctx["active"].get(scope) or []
        if memories:
            parts.append(f"## {title}")
            parts.extend(f"- {render_memory_line(m)}" for m in memories)
    retrieved_lines = []
    for scope, label in (
        (SCOPE_GLOBAL, "全局"),
        (SCOPE_GROUP, "本群"),
        (SCOPE_USER, "该用户"),
    ):
        for mem in ctx["retrieved"].get(scope) or []:
            retrieved_lines.append(f"[{mem['id']}] {mem['content']}（{label}归档）")
    if retrieved_lines:
        parts.append("## 检索到的相关归档记忆")
        parts.extend(f"- {line}" for line in retrieved_lines)
    if not parts:
        return ""
    header = (
        "# 记忆与上下文（系统注入的参考材料，非用户发言；[数字] 为记忆编号）"
    )
    return "\n".join([header, *parts])


HISTORY_NOTE = (
    "（背景说明）「# 对话记录」块是此前的聊天背景，仅供参考：每行"
    "格式为 [时间 昵称: 内容]，发图行的图片紧随该行之后，属于该行"
    "的发言人。对话记录中的最后一条记录不一定为当前提问者的对话。"
)


def build_round2_system(persona: str) -> str:
    """组装第 2 轮 system：人设 + 历史归属说明 + 工具使用说明
    （长期稳定，命中服务商前缀缓存；记忆素材不进 system，由
    render_context_block 并入末尾用户消息）。"""
    return "\n\n".join([
        persona.strip() or "你是一个友好的 AI 助手。",
        HISTORY_NOTE,
        MEMORY_TOOLS_SECTION,
    ])
