"""临时图床：官Bot 图文 markdown 内嵌图片用。

QQ 官方平台的 markdown 内嵌图片要求公网可访问 URL（发送时平台会下载
转存），本地渲染的字节图（BuildImage 面板等 base64）没有 URL。本模块把
字节图短暂暴露为本服务上的 URL（默认 10 分钟过期），地址基于 config
的 ip_address 与驱动端口（与 OneBot ws 端口共用，路径 /img/{token}，
不额外开防火墙端口）。未配置 ip_address 时 serve_image 返回 None，
调用方（qq_bot_api_patch）回退到降级引用路径。
"""

import secrets
import time
from typing import Optional

import nonebot
from nonebot import logger
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route, Router

from .config_store import config

# 平台发送时会转存，URL 只需发送瞬间可达；过期仅供兜底清理
_TTL_SECONDS = 600

# token -> (图片字节, content_type, 过期时间戳)
_store: dict[str, tuple[bytes, str, float]] = {}
_mounted = False


def _sweep(now: float) -> None:
    for token in [t for t, (_, _, exp) in _store.items() if exp < now]:
        _store.pop(token, None)


async def _handle_image(request: Request) -> Response:
    entry = _store.get(request.path_params["token"])
    if entry is None:
        return PlainTextResponse("图片不存在或已过期", status_code=404)
    data, content_type, _ = entry
    _sweep(time.time())
    return Response(content=data, media_type=content_type)


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
    _sweep(now)
    token = secrets.token_urlsafe(16)
    _store[token] = (data, content_type, now + _TTL_SECONDS)
    return f"{base}/img/{token}"
