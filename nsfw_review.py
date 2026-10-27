"""nsfw_review - 用户提交内容的外接 LLM 审核（昵称/图片/漂流瓶等通用）。"""


import asyncio
import base64
import io
import json
import re

import aiohttp
from nonebot.log import logger

from .config_store import config

# 审核请求超时（秒）
REVIEW_TIMEOUT = 30

# 送审图片上限：张数与单张大小（与 mlai 图片识别同规格）
_MAX_IMAGES = 3
_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_MAX_EDGE = 1280
_IMAGE_JPEG_QUALITY = 85

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

REVIEW_SYSTEM = (
    "你是内容安全审核模块，只输出一个 JSON 对象，不要输出任何其他文字、"
    "解释或思考过程。对用户提交的内容做一项判断：\n"
    '{"nsfw": true/false}\n'
    "nsfw：内容是否包含色情、暴力、政治风险、粗口、人身攻击等不适宜因素，"
    "或结合标注的内容用途判断，内容在该用途下语义是否反常、不当。"
    "命中任意因素，则标记为true。不要把中性词本身误判为不适宜。\n"
    "输入可能包含图片附件，图片内容同样适用以上判断。"
)


def review_configured() -> bool:
    """审核接口参数是否配置完整（key / base_url / model）。"""
    return all(
        [
            str(getattr(config, "nsfw_review_api_key", "") or "").strip(),
            str(getattr(config, "nsfw_review_api_base_url", "") or "").strip(),
            str(getattr(config, "nsfw_review_model", "") or "").strip(),
        ]
    )


def _compress_image(data: bytes):
    """压到长边 ≤1280 的 JPEG；非图片或失败原样返回。动图取首帧。"""
    try:
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


async def _download_image_data_url(url: str) -> str:
    """下载送审图片并转为 data URL（base64）。失败抛 RuntimeError。"""
    url = url.replace("&amp;", "&")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=REVIEW_TIMEOUT)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                ctype = (
                    resp.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                data = await resp.read()
    except asyncio.TimeoutError:
        raise RuntimeError("图片下载超时")
    except aiohttp.ClientError as e:
        raise RuntimeError(f"网络错误: {e}")
    if len(data) > _IMAGE_MAX_BYTES:
        raise RuntimeError("图片过大（超过 10MB）")
    data, compressed_ctype = _compress_image(data)
    if compressed_ctype:
        ctype = compressed_ctype
    if not ctype.startswith("image/"):
        ctype = "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{ctype};base64,{encoded}"


def _coerce_param_value(value):
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


def _build_payload(content_parts: list[dict]) -> dict:
    """组装审核请求体：自定义参数先入、核心参数后写（model/messages/
    stream 不可被覆盖）。"""
    raw = getattr(config, "nsfw_review_extra_params", None)
    payload = {
        str(key): _coerce_param_value(value)
        for key, value in (raw.items() if isinstance(raw, dict) else [])
    }
    payload.update(
        {
            "model": str(config.nsfw_review_model or "").strip(),
            "messages": [
                {"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": content_parts},
            ],
            "stream": False,
        }
    )
    return payload


async def _request_verdict(content_parts: list[dict]) -> str:
    """调用 OpenAI 兼容接口审核，返回模型回复文本。失败抛 RuntimeError。"""
    base = str(config.nsfw_review_api_base_url or "").strip().rstrip("/")
    payload = _build_payload(content_parts)
    headers = {
        "Authorization": f"Bearer {config.nsfw_review_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=REVIEW_TIMEOUT)
        ) as session:
            async with session.post(
                f"{base}/chat/completions", json=payload, headers=headers
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.error(
                        f"[nsfw_review] API HTTP {resp.status}: {body[:300]}"
                    )
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        raise RuntimeError("审核接口超时")
    except aiohttp.ClientError as e:
        raise RuntimeError(f"网络错误: {e}")
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("审核接口返回格式异常")


def _parse_verdict(text: str):
    """从回复解析 {"nsfw": bool}；无法解析返回 None。"""
    match = re.search(r"\{.*\}", _THINK_RE.sub("", text), re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0)).get("nsfw")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


async def review_user_content(
    text: str = "",
    image_urls: list[str] | None = None,
    scene: str = "",
) -> tuple[bool, str]:
    """审核用户提交的文本与图片，返回 (是否放行, 拒绝原因)。

    scene 为内容用途说明（如「用户给宠物起的名字」），注入送审文本
    开头，供模型结合语境判定（同一词在不同用途下结论不同）。
    总开关关闭时直接放行；开启但接口未配置完整时放行并告警（不阻断
    业务）。审核服务异常或解析失败时拒绝（fail-closed，同 mlai 第 1 轮
    审核的处理方式），未过审内容不会进入业务落库。
    """
    if not getattr(config, "nsfw_review_enable", False):
        return True, ""
    if not review_configured():
        logger.warning(
            "[nsfw_review] 审核开关已开启但接口未配置完整"
            "（nsfw_review_api_key / api_base_url / model），本次跳过审核"
        )
        return True, ""

    text = (text or "").strip()
    scene = (scene or "").strip()
    label = f"（内容用途：{scene}）\n" if scene else ""
    urls = [u for u in (image_urls or []) if u][:_MAX_IMAGES]
    # 文本 part 恒在最前（用途标注 + 原文或占位说明），图片 part 依次跟随
    parts: list[dict] = [
        {
            "type": "text",
            "text": f"{label}{text or '（用户上传了图片）'}",
        }
    ]
    try:
        for url in urls:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": await _download_image_data_url(url)},
                }
            )
    except RuntimeError as e:
        logger.warning(f"[nsfw_review] 送审图片获取失败: {e}")
        return False, "图片获取失败，请稍后再试"

    verdict = None
    try:
        for _ in range(2):  # 解析失败重试一次
            reply = await _request_verdict(parts)
            verdict = _parse_verdict(reply)
            if verdict is not None:
                break
    except RuntimeError as e:
        logger.error(f"[nsfw_review] 审核请求失败: {e}")
        return False, "审核服务暂时不可用，请稍后再试"
    if verdict is None:
        logger.error(f"[nsfw_review] 审核结果解析失败: {reply[:200]}")
        return False, "审核服务返回异常，请稍后再试"
    if verdict:
        return False, "内容包含不适宜信息，暂时无法提交"
    return True, ""
