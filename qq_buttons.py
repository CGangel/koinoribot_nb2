"""官Bot 内嵌按钮（keyboard）与按钮回调（interaction）的通用工具。

- build_keyboard(): 按行规格构造内嵌键盘（默认回调按钮：data 即回调标识）
- send_keyboard_message(): 发送 markdown + 键盘的单条消息（msg_type=2）
- image_markdown(): 字节图 → markdown 内嵌图片标签
  （经 image_host 临时图床转公网 URL，需配置 ip_address）
- send_image_with_keyboard(): 单条消息合并本地字节图与键盘；
  图床不可用返回 False，调用方回退为「富媒体图片 + 按钮键盘」两条消息
- register_interaction(): 注册按钮 data 的回调处理器。收到
  INTERACTION_CREATE 事件后统一处理：先应答（PUT /interactions/{id}，
  官方要求否则客户端一直转圈直至超时），再执行业务并被动回复处理结果
  （锚点是事件最外层的 event_id，经 Bot.send 自动按场景路由）。
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from nonebot import logger, on_notice
from nonebot.adapters import Bot, Event

from .tools import resolve_uid

# 按钮 data -> 处理器（uid, event）-> 回复（str 文本 / ImageReply 图片 / 空则不回复）
_interaction_handlers: dict[str, "InteractionHandler"] = {}


@dataclass
class ImageReply:
    """图片回复载体：本地渲染的字节图 + 可选短说明文字。

    分发器以 [text, file_image] 消息发送；官Bot 回复装饰补丁会把其转为
    markdown 内嵌图（@回复者在前），图床不可用时降级富媒体原图发送。
    """

    image: bytes
    caption: str = ""


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


def image_markdown(data: bytes) -> str | None:
    """字节图 → markdown 内嵌图片标签（经 image_host 临时图床转公网 URL）。

    图床不可用（未配置 ip_address / 无 HTTP 服务端驱动）返回 None。
    """
    from .image_host import embed_image

    embedded = embed_image(data)
    if embedded is None:
        return None
    url, width, height = embedded
    return f"![图片 #{width}px #{height}px]({url})"


async def send_image_with_keyboard(
    bot: Bot, event: Event, image: bytes, keyboard, caption: str = ""
) -> bool:
    """单条消息合并本地字节图与内嵌键盘（markdown 内嵌图 + keyboard）。

    图片经临时图床转公网 URL（平台发送时抓取转存，10 秒 TTL 足够）；
    图床不可用返回 False，调用方可回退为两条消息。
    """
    tag = image_markdown(image)
    if tag is None:
        return False
    body = f"{caption}\n{tag}" if caption else tag
    return await send_keyboard_message(bot, event, body, keyboard)


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

            segs = (
                [QQSeg.text(reply.caption)] if reply.caption else []
            ) + [QQSeg.file_image(reply.image)]
            await bot.send(event, QQMessage(segs))
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
