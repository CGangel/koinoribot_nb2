"""官Bot 内嵌按钮（keyboard）与按钮回调（interaction）的通用工具。"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from nonebot import logger, on_notice
from nonebot.adapters import Bot, Event

from .tools import resolve_uid

# 按钮 data -> 处理器（uid, event）-> 回复（str 文本 / ImageReply 图片 / 空则不回复）
_interaction_handlers: dict[str, "InteractionHandler"] = {}


@dataclass
class ImageReply:
    """图片回复载体：本地渲染的字节图 + 可选短说明文字 + 可选按钮键盘。

    分发器以 [text, file_image] 消息发送；官Bot 回复装饰补丁会把其转为
    markdown 内嵌图（@回复者在前），素材上传失败时降级富媒体原图发送。
    携带 keyboard 时优先单条合并发送（markdown 内嵌图 + 键盘），素材
    上传失败回退「富媒体图片 + 按钮键盘」两条消息。
    """

    image: bytes
    caption: str = ""
    keyboard: Any = None


InteractionHandler = Callable[[int, Any], Awaitable[Union[str, ImageReply, None]]]


def build_keyboard(rows: list[list[dict]]):
    """按行规格构造官Bot 内嵌键盘。

    规格字段：label（必填，≤10 字符）；data（回调标识，默认 label）；
    style（0 灰线框 / 1 蓝线框，默认 1）；action_type（默认 1 回调按钮）；
    permission（默认 2 群内所有人可点）；visited_label（点击后文字，默认同 label）。
    """
    from nonebot.adapters.qq.models import (
        Action,
        Button,
        InlineKeyboard,
        InlineKeyboardRow,
        MessageKeyboard,
        Permission,
        RenderData,
    )

    def _button(spec: dict) -> Button:
        label = spec["label"]
        return Button(
            render_data=RenderData(
                label=label,
                visited_label=spec.get("visited_label", label),
                style=spec.get("style", 1),
            ),
            action=Action(
                type=spec.get("action_type", 1),
                permission=Permission(type=spec.get("permission", 2)),
                data=spec.get("data", label),
            ),
        )

    return MessageKeyboard(content=InlineKeyboard(rows=[
        InlineKeyboardRow(buttons=[_button(s) for s in row]) for row in rows
    ]))


async def send_keyboard_message(bot: Bot, event: Event, markdown: str, keyboard) -> bool:
    """发送 markdown + 内嵌键盘的单条消息；失败返回 False。"""
    try:
        from nonebot.adapters.qq.message import Message as QQMessage
        from nonebot.adapters.qq.message import MessageSegment as QQSeg

        await bot.send(event, QQMessage([
            QQSeg.markdown(markdown), QQSeg.keyboard(keyboard),
        ]))
        return True
    except Exception as error:
        logger.warning(f"[qq_buttons] 发送按钮消息失败: {error}")
        return False


async def send_image_with_keyboard(
    bot: Bot, event: Event, image: bytes, keyboard, caption: str = ""
) -> bool:
    """单条消息合并本地字节图与内嵌键盘（markdown 内嵌图 + keyboard）。

    字节图经 QQ 素材管理（qq_media）上传平台换 qqbot-media:// 引用
    （无需公网图床；素材配额仅 100 张，消息发出即删除，平台发送时已
    转存副本）；上传失败返回 False，调用方可回退为两条消息。
    """
    from .qq_media import embed_media_image, release_media_image

    media = await embed_media_image(bot, image)
    if media is None:
        return False
    url, width, height, uploaded_id = media
    tag = f"![图片 #{width}px #{height}px]({url})"
    body = f"{caption}\n{tag}" if caption else tag
    try:
        return await send_keyboard_message(bot, event, body, keyboard)
    finally:
        await release_media_image(bot, uploaded_id)


def register_interaction(data: str, handler: InteractionHandler) -> None:
    """注册按钮回调处理器：handler(uid, event) 返回 str 文本、ImageReply
    图片或空（空则应答后不回复）。"""
    _interaction_handlers[data] = handler


def interaction_registered(data: str) -> bool:
    """按钮 data 是否已注册回调处理器"""
    return data in _interaction_handlers


async def dispatch_interaction(bot: Bot, event) -> None:
    """应答并分发一次按钮回调（INTERACTION_CREATE）。

    官方要求收到事件后立即调用 PUT /interactions/{id} 应答，否则客户端
    会一直转圈直至超时——应答必须先于一切业务逻辑（含 data 校验、
    身份解析），即使后续失败也已止住转圈。业务结果走 Bot.send 被动
    回复：适配器对 INTERACTION_CREATE 会用事件最外层 id
    （event.event_id）作锚点并按群/频道/C2C 自动选接口。

    data 查找支持冒号上下文：先精确匹配整串 data，未命中再取首个
    「:」前的动作名匹配（如 ``卖鱼:10086:456`` 命中注册的 ``卖鱼``，
    处理器可从 event.data.resolved.button_data 解析上下文参数）。
    """
    # 第一件事：应答回调（code 0）
    try:
        await bot.put_interaction(interaction_id=event.id, code=0)
    except Exception as error:
        logger.warning(f"[qq_buttons] 应答回调失败: {error}")

    resolved = event.data.resolved if event.data else None
    data = (resolved.button_data or "").strip() if resolved else ""
    handler = _interaction_handlers.get(data)
    if handler is None and ":" in data:
        handler = _interaction_handlers.get(data.split(":", 1)[0])
    if handler is None:
        if data:
            logger.debug(f"[qq_buttons] 未注册的按钮回调 data={data!r}")
        return

    try:
        uid = resolve_uid(event, event.get_user_id())
    except Exception as error:
        logger.warning(f"[qq_buttons] 按钮回调无法解析点击者身份: {error}")
        return

    try:
        reply = await handler(uid, event)
    except Exception as error:
        logger.warning(f"[qq_buttons] 按钮回调 data={data!r} 处理失败: {error}")
        return
    if not reply:
        return

    try:
        # 被动回复锚点必须是事件最外层的 id（官方文档：event_id
        # 「从事件最外层的id获取」）；交互 id（d.id）只用于上面的 PUT
        # 应答，拿它当 event_id 会被平台拒绝（40034025 event_id 无效）。
        # Bot.send 对 INTERACTION_CREATE 自动按场景路由（群/频道/C2C）
        # 并使用正确的 event_id 锚点。
        if isinstance(reply, ImageReply):
            from nonebot.adapters.qq.message import Message as QQMessage
            from nonebot.adapters.qq.message import MessageSegment as QQSeg

            # 携带键盘：优先单条合并（markdown 内嵌图 + 键盘），
            # 图床不可用回退「富媒体图片 + 按钮键盘」两条消息
            if reply.keyboard is not None and await send_image_with_keyboard(
                bot, event, reply.image, reply.keyboard, reply.caption
            ):
                return
            segs = (
                [QQSeg.text(reply.caption)] if reply.caption else []
            ) + [QQSeg.file_image(reply.image)]
            await bot.send(event, QQMessage(segs))
            if reply.keyboard is not None:
                await send_keyboard_message(bot, event, " ", reply.keyboard)
        else:
            await bot.send(event, reply)
    except Exception as error:
        logger.warning(f"[qq_buttons] 按钮回调回复失败: {error}")


_interaction_notice = on_notice(priority=5, block=False)


@_interaction_notice.handle()
async def _handle_interaction_notice(bot: Bot, event: Event) -> None:
    """官Bot 按钮回调统一入口：只处理已注册的按钮 data，其余事件原样放行"""
    try:
        from nonebot.adapters.qq.event import InteractionCreateEvent
    except Exception:
        return
    if not isinstance(event, InteractionCreateEvent):
        return
    await dispatch_interaction(bot, event)
