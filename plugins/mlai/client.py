"""mlai API 客户端：OpenAI 兼容 Chat Completions 与图片下载压缩。"""


import asyncio
import base64
import json
import re
from typing import Optional

import aiohttp
from nonebot.log import logger

from . import settings


class MlaiAPIError(Exception):
    """API 调用失败（message 为面向用户的友好文案，status 为 HTTP 状态码）"""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# 附件图片下载的大小上限（base64 后约 +33%，多数 vision 接口限 10MB 级）
_IMAGE_MAX_BYTES = 10 * 1024 * 1024

# 压缩目标：长边上限与 JPEG 质量（控制 vision 请求体积，也控制临时
# 上下文内联图片的存储与回放成本）
_IMAGE_MAX_EDGE = 1280
_IMAGE_JPEG_QUALITY = 85


def compress_image(data: bytes):
    """压到长边 ≤1280 的 JPEG，返回 (bytes, content_type)；非图片或
    压缩失败原样返回 (data, "")。动图取首帧。"""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            w, h = img.size
            edge = max(w, h)
            if edge > _IMAGE_MAX_EDGE:
                scale = _IMAGE_MAX_EDGE / edge
                img = img.resize(
                    (max(1, round(w * scale)), max(1, round(h * scale)))
                )
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_IMAGE_JPEG_QUALITY)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, ""


async def download_image_data_url(url: str, timeout: Optional[int] = None) -> str:
    """下载图片并转为 data URL（base64），供 vision 接口内联引用。

    官Bot 附件 URL 是带 rkey 签名的临时链接（会过期），必须消息到达时
    当场下载。失败/超时/超大抛 MlaiAPIError（文案可直接回复给用户）。
    """
    seconds = timeout if timeout is not None else settings.timeout()
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=max(1, int(seconds)))
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise MlaiAPIError(f"图片下载失败（HTTP {resp.status}）")
                ctype = (
                    resp.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                data = await resp.read()
    except MlaiAPIError:
        raise
    except asyncio.TimeoutError:
        raise MlaiAPIError("图片下载超时，请稍后再试")
    except aiohttp.ClientError as e:
        logger.error(f"[mlai] 图片下载网络错误: {e}")
        raise MlaiAPIError("图片下载失败，请稍后再试")
    if len(data) > _IMAGE_MAX_BYTES:
        raise MlaiAPIError("图片过大（超过 10MB），无法识别")
    data, compressed_ctype = compress_image(data)
    if compressed_ctype:
        ctype = compressed_ctype
    if not ctype.startswith("image/"):
        ctype = "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{ctype};base64,{encoded}"


def strip_think_blocks(text: str) -> str:
    """剥除正文内嵌的 <think>...</think> 思考块。"""
    return _THINK_RE.sub("", text or "").strip()


def build_request_messages(
    system_prompt: str, history: list[dict], user_text: str
) -> list[dict]:
    """组装请求 messages：系统提示 + 历史 + 本轮提问。"""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        {"role": item["role"], "content": item["content"]} for item in history
    )
    messages.append({"role": "user", "content": user_text})
    return messages


def coerce_param_value(value):
    """自定义参数的类型规范化：面板键值对以文本存储，请求前按内容还原
    true/false → 布尔、数字串 → 数值，其余（如 "max"）保持字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                continue
        return text
    return value


def extra_request_params() -> dict:
    """读取生效预设的自定义请求参数（规范化类型后返回副本）。"""
    return {
        str(key): coerce_param_value(value)
        for key, value in settings.extra_params().items()
    }


async def chat_completion(
    messages: list[dict],
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """调用 Chat Completions，返回助手的最终回答文本。"""
    message = await chat_completion_message(
        messages,
        api_key=api_key, base_url=base_url, model=model, timeout=timeout,
    )
    if not message["content"]:
        raise MlaiAPIError("AI 返回了空回复")
    return message["content"]


# 已验证可用的工具调用模式缓存：(base_url, model) → "required"/"auto"/None。
# DeepSeek 思考模式等场景不支持 tool_choice=required，首次 4xx 降级成功后
# 记住可用模式，后续请求直接使用，不再重复失败重试；换服务商/模型自动失效。
_tool_choice_cache: dict[tuple[str, str], Optional[str]] = {}


def _downgrade_chain(mode: Optional[str]) -> list[Optional[str]]:
    """从 mode 开始的降级链：required → auto → 无工具。"""
    if mode == "required":
        return ["required", "auto", None]
    if mode == "auto":
        return ["auto", None]
    return [None]


async def chat_completion_message(
    messages: list[dict],
    *,
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
) -> dict:
    """调用 Chat Completions，返回完整助手消息 {content, tool_calls}。

    失败抛 MlaiAPIError（文案可直接回复给用户），原始错误落日志。
    携带 tools 时若服务商拒绝（HTTP 4xx，如 DeepSeek 思考模式不支持
    tool_choice=required），自动降级：required → auto → 不带 tools
    （该轮不维护记忆），成功后缓存可用模式。
    """
    key = api_key if api_key is not None else settings.api_key()
    base = (
        base_url if base_url is not None else settings.api_base_url()
    )
    base = str(base or "").strip().rstrip("/")
    name = model if model is not None else settings.model()

    requested: Optional[str] = None
    chain: list[Optional[str]] = [None]
    cache_key: Optional[tuple[str, str]] = None
    if tools is not None:
        requested = tool_choice or "auto"
        cache_key = (base, name)
        # 已缓存该服务商可用的模式则直接从它开始（None = 不支持工具）
        start: Optional[str] = requested
        if cache_key in _tool_choice_cache:
            start = _tool_choice_cache[cache_key]
        chain = _downgrade_chain(start)

    last_error: Optional[MlaiAPIError] = None
    for mode in chain:
        try:
            message = await _request_chat(
                messages,
                tools=tools if mode is not None else None,
                tool_choice=mode,
                api_key=api_key, base_url=base_url, model=model,
                timeout=timeout,
            )
        except MlaiAPIError as e:
            last_error = e
            if tools is not None and e.status in (400, 404, 422):
                logger.warning(
                    f"[mlai] 工具调用模式不被支持（tool_choice={mode}），自动降级重试: {e}"
                )
                continue
            raise
        if cache_key is not None:
            _tool_choice_cache[cache_key] = mode
        return message
    assert last_error is not None
    raise last_error


async def _request_chat(
    messages: list[dict],
    *,
    tools: Optional[list[dict]],
    tool_choice: Optional[str],
    api_key: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
    timeout: Optional[int],
) -> dict:
    key = api_key if api_key is not None else settings.api_key()
    base = (
        base_url if base_url is not None else settings.api_base_url()
    )
    base = str(base or "").strip().rstrip("/")
    name = model if model is not None else settings.model()
    seconds = timeout if timeout is not None else settings.timeout()
    if not key or not base:
        raise MlaiAPIError("AI 对话接口未配置（mlai_api_key / mlai_api_base_url）")

    # 自定义参数先入、核心参数后写：model/messages/stream 不可被覆盖；
    # tools/tool_choice 最后加入，同样不可被自定义参数覆盖
    payload = dict(extra_request_params())
    payload.update({"model": name, "messages": messages, "stream": False})
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    client_timeout = aiohttp.ClientTimeout(total=max(1, int(seconds)))

    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(
                f"{base}/chat/completions", json=payload, headers=headers
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error(
                        f"[mlai] API HTTP {resp.status}: {body[:300]}"
                    )
                    raise MlaiAPIError(f"AI 接口返回 HTTP {resp.status}", resp.status)
                data = await resp.json(content_type=None)
    except MlaiAPIError:
        raise
    except asyncio.TimeoutError:
        raise MlaiAPIError("AI 响应超时，请稍后再试")
    except aiohttp.ClientError as e:
        logger.error(f"[mlai] 网络错误: {e}")
        raise MlaiAPIError("AI 接口网络错误，请稍后再试")

    try:
        assistant = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        logger.error(f"[mlai] 响应格式异常: {str(data)[:300]}")
        raise MlaiAPIError("AI 返回了无法解析的响应")

    tool_calls = []
    for call in assistant.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            logger.warning(
                f"[mlai] 工具参数解析失败: {str(function.get('arguments'))[:200]}"
            )
            arguments = {}
        tool_calls.append({
            "id": call.get("id", "") or "",
            "name": function.get("name", ""),
            "arguments": arguments,
        })
    return {
        "content": strip_think_blocks(assistant.get("content") or ""),
        "tool_calls": tool_calls,
    }
