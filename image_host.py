"""临时图床：官Bot 图文 markdown 内嵌图片用。

QQ 官方平台的 markdown 内嵌图片要求公网可访问 URL（发送时平台会下载
转存），本地渲染的字节图（BuildImage 面板等 base64）没有 URL。本模块把
字节图**仅在内存中**短暂暴露为本服务上的 URL，不落盘。清理策略（面向
0.5G 内存小机）：平台首次抓取（即转存完成）后宽限 3 秒立即删除；从未
被抓取的条目由 10 秒 TTL 兜底；并发条目上限 64。地址基于 config 的
ip_address 与驱动端口（与 OneBot ws 端口共用，路径 /img/{token}，
不额外开防火墙端口）。未配置 ip_address 时 serve_image 返回 None，
调用方（qq_bot_api_patch）回退到降级引用路径。
"""

import asyncio
import secrets
import time
from typing import Optional

import nonebot
from nonebot import logger
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route, Router

from .config_store import config

# 平台发送消息时会立即抓取转存图片——路由被首次访问即视为转存发生，
# 短宽限后立即删除；TTL 仅兜底"从未被平台抓取"的条目（发送失败等）。
# 目标部署环境为 0.5G 内存小机，两个窗口都保持极短。
_TTL_SECONDS = 10

# 首次抓取后的保留宽限：覆盖平台瞬时重试即可（实测平台在发送后 1-2 秒
# 抓取，转存失败案例的根因是图片过大而非时序），到点即删
_GRACE_SECONDS = 3

# 内存兜底：并发条目超限时优先逐出最快过期的条目（正常流量远达不到）
_MAX_ENTRIES = 64

# 平台对 markdown 内嵌图片的转存有大小限制（文档未写明；实测数百 KB
# 正常、1-3MB 失败），超过阈值的字节图先压缩再上图床
_COMPRESS_THRESHOLD = 1024 * 1024  # 1MB
# 质量阶梯从高起：JPEG 默认 4:2:0 色度抽样会让高饱和图色彩发糊
# （实测 1024x1536 q90 仅 317KB，余量充足），统一用 4:4:4 保全色彩分辨率
_COMPRESS_QUALITIES = (95, 92, 90, 85, 80, 75, 70, 60, 50)

# token -> (图片字节, content_type, 过期时间戳)
_store: dict[str, tuple[bytes, str, float]] = {}
_mounted = False


def _compress_image(data: bytes, content_type: str) -> tuple[bytes, str, int, int]:
    """超大图压缩到平台可转存量级：先降 JPEG 质量（保尺寸），仍超限再缩尺寸。

    返回 (字节, content_type, 宽, 高)；失败时原样返回。
    """
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img.load()  # 强制载入，避免懒加载流与 optimize 二次读取冲突
        width, height = img.size
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.getchannel("A"))
            img = background
        else:
            img = img.convert("RGB")

        for quality in _COMPRESS_QUALITIES:
            buf = BytesIO()
            img.save(buf, "JPEG", quality=quality, subsampling=0)
            if buf.tell() <= _COMPRESS_THRESHOLD:
                logger.info(
                    f"[image_host] 大图压缩 {len(data)}B→{buf.tell()}B "
                    f"(JPEG q{quality} 4:4:4, {width}x{height})"
                )
                return buf.getvalue(), "image/jpeg", width, height

        # 降质量仍超限（极端噪声图等）：逐级缩尺寸
        for scale in (0.75, 0.5, 0.35):
            resized = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale)))
            )
            buf = BytesIO()
            resized.save(buf, "JPEG", quality=70, subsampling=0)
            if buf.tell() <= _COMPRESS_THRESHOLD:
                logger.info(
                    f"[image_host] 大图压缩+缩放 {len(data)}B→{buf.tell()}B "
                    f"({width}x{height}→{resized.size[0]}x{resized.size[1]})"
                )
                return (
                    buf.getvalue(),
                    "image/jpeg",
                    resized.size[0],
                    resized.size[1],
                )
        logger.warning(
            f"[image_host] 大图压缩后仍超限（{len(data)}B），按原图注册"
        )
        return data, content_type, width, height
    except Exception as e:
        logger.warning(f"[image_host] 图片压缩失败，按原图注册: {e}")
        return data, content_type, 0, 0


def embed_image(data: bytes, content_type: str = "image/png") -> Optional[tuple[str, int, int]]:
    """字节图 → (公网 URL, 宽, 高)，供 markdown 内嵌；无法暴露时返回 None。

    超过转存大小阈值的图片自动压缩（JPEG），返回的宽高与压缩结果一致，
    调用方直接用于 ``![图片 #Wpx #Hpx](url)`` 标签。
    """
    from .tools import get_image_meta

    if len(data) > _COMPRESS_THRESHOLD:
        data, content_type, width, height = _compress_image(data, content_type)
    else:
        width, height, _ = get_image_meta(data)
    url = serve_image(data, content_type)
    if url is None:
        return None
    return url, width, height

# 已排定延迟删除的 token 与其任务引用（防任务被 GC）
_scheduled: set[str] = set()
_delete_tasks: set = set()


def _sweep(now: float) -> None:
    for token in [t for t, (_, _, exp) in _store.items() if exp < now]:
        _store.pop(token, None)
    overflow = len(_store) - _MAX_ENTRIES
    if overflow > 0:
        for token, _ in sorted(
            _store.items(), key=lambda kv: kv[1][2]
        )[:overflow]:
            _store.pop(token, None)


async def _handle_image(request: Request) -> Response:
    entry = _store.get(request.path_params["token"])
    if entry is None:
        return PlainTextResponse("图片不存在或已过期", status_code=404)
    data, content_type, _ = entry
    _sweep(time.time())
    _schedule_delete(request.path_params["token"])
    return Response(content=data, media_type=content_type)


def _schedule_delete(token: str) -> None:
    """平台首次抓取（= 转存发生）后，宽限数秒即删除该条目。"""

    async def _delete_later() -> None:
        await asyncio.sleep(_GRACE_SECONDS)
        _scheduled.discard(token)
        _store.pop(token, None)

    if token in _scheduled:
        return
    _scheduled.add(token)
    task = asyncio.get_running_loop().create_task(_delete_later())
    _delete_tasks.add(task)
    task.add_done_callback(_delete_tasks.discard)


def build_router() -> Router:
    return Router(routes=[Route("/{token}", _handle_image, methods=["GET"])])


def mount_image_host() -> bool:
    """把 /img 挂到驱动端口（幂等）。非 HTTP 服务端驱动（如测试环境）返回 False。"""
    global _mounted
    if _mounted:
        return True
    server_app = getattr(nonebot.get_driver(), "server_app", None)
    if server_app is None:
        return False
    server_app.mount("/img", build_router())
    _mounted = True
    return True


def public_base_url() -> Optional[str]:
    """服务公网地址（http://ip_address:驱动端口）；未配置 ip_address 或端口未知时为 None。"""
    ip = str(getattr(config, "ip_address", "") or "").strip()
    if not ip:
        return None
    port = getattr(nonebot.get_driver().config, "port", None)
    if not port:
        return None
    return f"http://{ip}:{port}"


def serve_image(data: bytes, content_type: str = "image/png") -> Optional[str]:
    """注册图片字节并返回短暂有效的公网 URL；无法暴露（未配置公网地址/挂载失败）返回 None。"""
    base = public_base_url()
    if base is None or not data:
        return None
    if not _mounted:
        try:
            if not mount_image_host():
                return None
        except Exception as e:
            logger.warning(f"[image_host] 图床路由挂载失败: {e}")
            return None
    now = time.time()
    token = secrets.token_urlsafe(16)
    _store[token] = (data, content_type, now + _TTL_SECONDS)
    _sweep(now)  # 插入后收敛：过期清理 + 并发上限逐出（含本次注册）
    return f"{base}/img/{token}"
