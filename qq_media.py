"""QQ 官方素材管理：字节图上传平台换取 qqbot-media:// 引用，供官Bot markdown 内嵌（无需公网图床）。

流程（官方 20261021 新增的 /v2/media-images 三步）：prepare 申请预签名
URL → PUT 直传图片字节 → complete 换取 ``qqbot-media://image/v1/<token>``
引用，markdown 中直接引用，发送时由平台转存副本。限制：单图 ≤10MB、
png/jpeg/gif/webp、单机器人有效素材 100 张（删除可释放）、素材 90 天
未被引用自动失效。

发送成功后应立即删除素材（平台发送时已转存副本，删除不影响已发出
消息），避免动态图片（每次内容不同的画图/钓鱼图等）耗尽 100 张配额。
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Optional

import nonebot
from nonebot import logger
from nonebot.adapters.qq.utils import API
from nonebot.drivers import Request

# 平台单图上限；超过先压缩再上传
_MAX_UPLOAD_SIZE = 10 * 1024 * 1024

# content_type → 文件扩展名（prepare 的 file_name 扩展名须与 content_type 一致）
_EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


# ================== Bot API 补充（适配器未提供素材接口，缺失时挂到 Bot 上） ==================


@API
async def prepare_media_image(
    self,
    *,
    file_name: str,
    file_size: int,
    content_type: str,
    md5: str,
    sha1: str,
) -> dict:
    request = Request(
        "POST",
        self.adapter.get_api_base().joinpath("v2", "media-images", "prepare"),
        json={
            "file_name": file_name,
            "file_size": file_size,
            "content_type": content_type,
            "md5": md5,
            "sha1": sha1,
        },
    )
    return await self._request(request)


@API
async def complete_media_image(self, *, upload_id: str, md5: str) -> dict:
    request = Request(
        "POST",
        self.adapter.get_api_base().joinpath("v2", "media-images", "complete"),
        json={"upload_id": upload_id, "md5": md5},
    )
    return await self._request(request)


@API
async def delete_media_image(self, *, image_id: str) -> None:
    request = Request(
        "DELETE",
        self.adapter.get_api_base().joinpath(
            "v2", "media-images", image_id, "delete"
        ),
    )
    await self._request(request)


_MEDIA_APIS = {
    "prepare_media_image": prepare_media_image,
    "complete_media_image": complete_media_image,
    "delete_media_image": delete_media_image,
}

_ensured = False


def ensure_qq_media_apis() -> None:
    """把素材管理 API 挂到 nonebot.adapters.qq.Bot 上（幂等；适配器原生提供后自动跳过）。"""
    global _ensured
    if _ensured:
        return
    from nonebot.adapters.qq import Bot as QQBot

    added = []
    for name, func in _MEDIA_APIS.items():
        if not hasattr(QQBot, name):
            setattr(QQBot, name, func)
            # API 是依赖 __set_name__ 的描述符，class-body 之外 setattr 需手动补 name
            if isinstance(func, API) and not hasattr(func, "name"):
                func.name = name
            added.append(name)
    if added:
        logger.debug(f"[qq_media] 素材管理 API 已挂载: {added}")
    _ensured = True


# ================== 图片整理与上传 ==================


def _normalize_image(data: bytes) -> tuple[bytes, str, int, int]:
    """整理为平台可收的图片：支持的格式直传，不支持/超限的经 PIL 转码。

    返回 (字节, content_type, 宽, 高)；无法处理时按原图 png 兜底（上传
    多半失败，由调用方降级）。
    """
    import filetype as _ft

    from .tools import get_image_meta

    width, height, _ = get_image_meta(data)
    guess = _ft.guess(data)
    content_type = guess.mime if guess and guess.mime in _EXT_BY_TYPE else ""
    if content_type and len(data) <= _MAX_UPLOAD_SIZE:
        return data, content_type, width, height
    try:
        from PIL import Image

        img = Image.open(BytesIO(data))
        img.load()  # 强制载入，避免懒加载流与二次保存冲突
        width, height = img.size
        if not content_type:
            # 平台不支持的格式（BMP 等）转 PNG
            buf = BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue(), "image/png", width, height
        # 超 10MB：JPEG 质量阶梯（保尺寸），仍超再逐级缩尺寸
        if img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        ):
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.getchannel("A"))
            img = background
        else:
            img = img.convert("RGB")
        for quality in (95, 90, 85, 80, 70, 60, 50):
            buf = BytesIO()
            img.save(buf, "JPEG", quality=quality, subsampling=0)
            if buf.tell() <= _MAX_UPLOAD_SIZE:
                logger.info(
                    f"[qq_media] 大图压缩 {len(data)}B→{buf.tell()}B (JPEG q{quality})"
                )
                return buf.getvalue(), "image/jpeg", width, height
        for scale in (0.75, 0.5, 0.35):
            resized = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale)))
            )
            buf = BytesIO()
            resized.save(buf, "JPEG", quality=70, subsampling=0)
            if buf.tell() <= _MAX_UPLOAD_SIZE:
                return buf.getvalue(), "image/jpeg", resized.size[0], resized.size[1]
        logger.warning(f"[qq_media] 压缩后仍超 10MB（{len(data)}B），按原图上传")
        return data, content_type, width, height
    except Exception as e:
        logger.warning(f"[qq_media] 图片整理失败: {e}")
        return data, "image/png", width, height


async def embed_media_image(bot, data: bytes) -> Optional[tuple[str, int, int, str]]:
    """字节图 → (qqbot-media 引用, 宽, 高, image_id)；失败返回 None。

    上传素材占单机器人 100 张配额，调用方发送消息后应调用
    release_media_image 释放（平台发送时已转存副本，删除不影响已发出消息）。
    """
    if not data:
        return None
    try:
        ensure_qq_media_apis()
        payload, content_type, width, height = _normalize_image(data)
        md5 = hashlib.md5(payload).hexdigest()
        sha1 = hashlib.sha1(payload).hexdigest()
        prepare = await bot.prepare_media_image(
            file_name=f"image{_EXT_BY_TYPE[content_type]}",
            file_size=len(payload),
            content_type=content_type,
            md5=md5,
            sha1=sha1,
        )
        upload_id = prepare.get("upload_id") or ""
        presigned = prepare.get("presigned_url") or ""
        if not upload_id or not presigned:
            logger.warning(f"[qq_media] prepare 响应缺少 upload_id/presigned_url: {prepare}")
            return None
        # 直传走预签名 URL：不得携带平台鉴权头，Content-Type 须与 prepare 声明完全一致
        response = await nonebot.get_driver().request(
            Request(
                "PUT",
                presigned,
                content=payload,
                headers={"Content-Type": content_type},
            )
        )
        if response.status_code >= 300:
            logger.warning(f"[qq_media] 直传失败 HTTP {response.status_code}")
            return None
        # complete 回传的 md5 取 COS 响应 ETag（即文件 MD5），缺失时用本地计算值
        etag = (response.headers.get("etag") or f'"{md5}"').strip('"')
        completed = await bot.complete_media_image(
            upload_id=upload_id, md5=etag
        )
        url = completed.get("url") or ""
        image_id = completed.get("image_id") or ""
        if not url.startswith("qqbot-media://") or not image_id:
            logger.warning(f"[qq_media] complete 响应异常: {completed}")
            return None
        logger.debug(f"[qq_media] 素材上传成功 image_id={image_id}")
        return url, width, height, image_id
    except Exception as e:
        logger.warning(f"[qq_media] 素材上传失败: {e}")
        return None


async def release_media_image(bot, image_id: str) -> bool:
    """删除素材释放配额；失败仅记日志（素材 90 天未被引用也会自动失效）。"""
    if not image_id:
        return False
    try:
        ensure_qq_media_apis()
        await bot.delete_media_image(image_id=image_id)
        return True
    except Exception as e:
        logger.warning(f"[qq_media] 素材删除失败（image_id={image_id}）: {e}")
        return False
