"""nonebot 适配器回复装饰补丁：官Bot/OneBot 被动回复的 @回复者与自动引用（config 面板开关统一控制）。"""

from __future__ import annotations

import functools

from nonebot.adapters.qq.utils import log


# ================== 回复装饰补丁（@回复者 + 自动引用，OneBot/官Bot） ==================


_reply_flags_logged = False


def _reply_flags() -> dict[str, bool]:
    """读取回复装饰开关（@回复者 / 自动引用）。

    补丁位于 koinoribot_nb2 包内，直接相对引用 config_store；首次读取打
    一条状态日志，便于在默认 INFO 级别下确认面板开关是否落库生效。
    """
    global _reply_flags_logged
    from .config_store import config

    flags = {
        "at_sender": bool(getattr(config, "at_sender", False)),
        "reply_quote": bool(getattr(config, "reply_quote", False)),
    }
    if not _reply_flags_logged:
        _reply_flags_logged = True
        log(
            "INFO",
            "回复装饰开关：@回复者={}，自动引用={}".format(
                "开启" if flags["at_sender"] else "关闭",
                "开启" if flags["reply_quote"] else "关闭",
            ),
        )
    return flags


def patch_qq_send_reply_quote() -> bool:
    """官Bot（QQ 适配器）被动回复装饰：自动引用 + @回复者（config 统一管理）。

    - config 群管理.reply_quote 开启时，群/单聊被动回复自动附带 reference
      段；适配器发送时会用入站消息的 msg_idx(REFIDX) 填充 message_reference，
      客户端以引用形式展示（消息头部带被引用者昵称）。入站消息无 REFIDX
      （无法确定引用目标）时不引用。
    - config 群管理.at_sender 开启时，群聊回复以官方 @某人 标签
      ``<qqbot-at-user id="member_openid" />`` + 换行开头，**以 markdown
      为载体发送**（msg_type=2）——实测群聊纯文本消息（msg_type=0）不
      解析该标签、整条显示原文，markdown 载体可正常渲染 @ 标签且无残留
      （2026-09 render_probe 探测结论）。C2C 不注入（官方 @某人 仅群聊/
      文字子频道可用）。图片与 @ 可共存于同一条 markdown：URL 型 image
      段直接以 ``![图片 #Wpx #Hpx](url)`` 内嵌；字节型 file_image 段经
      QQ 素材管理接口（qq_media）上传平台换取 ``qqbot-media://`` 引用后
      内嵌（无需公网地址；素材配额仅 100 张，消息发出即删除，平台发送
      时已转存副本、删除不影响已发出消息）。**降级**：无法内嵌时
      （视频/音频/文件段、素材上传失败等），无论引用开关是否打开，
      自动降级为引用模式（附带 message_reference 定位回复对象）。正文
      中的 markdown 元字符未做转义——QQ 自定义 markdown 对不成对的
      ``*`` ``_`` 等按原文显示，若实测出现斜体/标题等样式串扰再补转义。
    - 按钮回调回复（InteractionCreateEvent，见 qq_buttons）同样装饰：
      群场景以点击者 group_member_openid 注入 @ 标签；交互事件无
      REFIDX，引用装饰自然跳过，降级时直接原文发送。
    """
    from nonebot.adapters.qq import Bot as QQBot
    from nonebot.adapters.qq.event import (
        C2CMessageCreateEvent,
        GroupMessageCreateEvent,
        InteractionCreateEvent,
    )
    from nonebot.adapters.qq.message import Message, MessageSegment

    if getattr(QQBot.send, "_reply_quote_patched", False):
        log("DEBUG", "QQ 回复引用补丁已应用，跳过")
        return False

    original_send = QQBot.send

    @functools.wraps(original_send)
    async def send(self, event, message, **kwargs):
        prepend = []
        uploaded_media: list[str] = []
        if isinstance(
            event,
            (GroupMessageCreateEvent, C2CMessageCreateEvent, InteractionCreateEvent),
        ):
            flags = _reply_flags()
            msg = Message(message)
            # 剥离消息开头为旧 @ 格式预留的换行（与 OneBot 分支保持一致）
            if msg and msg[0].type == "text":
                stripped = msg[0].data["text"].lstrip("\n")
                if stripped != msg[0].data["text"]:
                    msg = (
                        Message([MessageSegment.text(stripped)] if stripped else [])
                        + Message(list(msg[1:]))
                    )
            # @回复者：仅群聊，markdown 载体（纯文本消息不解析标签，实测）
            at_degraded = False
            if flags["at_sender"] and isinstance(
                event, (GroupMessageCreateEvent, InteractionCreateEvent)
            ):
                if isinstance(event, InteractionCreateEvent):
                    # 按钮回调：群场景点击者是 group_member_openid
                    member_openid = (
                        event.group_member_openid
                        if event.group_openid else None
                    )
                else:
                    member_openid = getattr(
                        getattr(event, "author", None), "member_openid", None
                    )
                if member_openid:
                    tag = f'<qqbot-at-user id="{member_openid}" />'
                    if msg["markdown"]:
                        md_seg = msg["markdown"][-1]
                        md_seg.data["markdown"].content = (
                            f"{tag}\n{md_seg.data['markdown'].content}"
                        )
                    elif all(
                        seg.type in ("text", "emoji", "image", "file_image")
                        for seg in msg
                    ):
                        from .qq_media import embed_media_image

                        lines: list[str] = []
                        buf: list[str] = []

                        def _flush() -> None:
                            if buf:
                                lines.append("".join(buf))
                                buf.clear()

                        embed_failed = False
                        for seg in msg:
                            if seg.type in ("text", "emoji"):
                                buf.append(
                                    seg.data.get("text", "")
                                    if seg.type == "text"
                                    else str(seg)
                                )
                                continue
                            width = height = 0  # URL 型图片宽高未知，0 兜底
                            url = str(seg.data.get("url") or "")
                            if seg.type == "file_image":
                                content = seg.data.get("content")
                                if not isinstance(content, (bytes, bytearray)):
                                    embed_failed = True
                                    break
                                # 字节图经素材管理上传平台换取引用（无需公网地址）
                                media = await embed_media_image(self, bytes(content))
                                if media is None:
                                    embed_failed = True
                                    break
                                url, width, height, image_id = media
                                uploaded_media.append(image_id)
                            if not url:
                                embed_failed = True
                                break
                            _flush()
                            lines.append(f"![图片 #{width}px #{height}px]({url})")
                        _flush()
                        if embed_failed:
                            # 图片无法转公网 URL（未配置 ip_address 等）→ 降级引用
                            at_degraded = True
                            log("DEBUG", "官Bot@降级为引用模式（图片无法转公网URL）")
                        else:
                            body = "\n".join(lines)
                            msg = Message(
                                [MessageSegment.markdown(
                                    f"{tag}\n{body}" if body else tag
                                )]
                            )
                    else:
                        # 视频/音频/文件等 markdown 无法内嵌的富媒体 → 降级引用
                        at_degraded = True
                        log("DEBUG", "官Bot@降级为引用模式（富媒体消息）")
            # 引用：开关开启；或 @ 开启但富媒体降级时强制引用（保底定位回复对象）
            if flags["reply_quote"] or at_degraded:
                from .tools import get_qq_ref_idx

                ref_idx = get_qq_ref_idx(event)
                if ref_idx:
                    # message_id 任意非 REFIDX 值都会被适配器回填为入站消息的 REFIDX
                    prepend.append(MessageSegment.reference("0"))
                    log("DEBUG", f"官Bot引用已附带 REFIDX={ref_idx[:32]}...")
                else:
                    log("DEBUG", "官Bot引用跳过：触发消息无 msg_idx(REFIDX)")
            if prepend:
                msg = Message(prepend) + msg
            message = msg
        try:
            return await original_send(self, event, message, **kwargs)
        finally:
            # 素材配额仅 100 张：消息已处理（成功或失败）即删除本次上传素材
            if uploaded_media:
                from .qq_media import release_media_image

                for image_id in uploaded_media:
                    await release_media_image(self, image_id)

    send._reply_quote_patched = True
    QQBot.send = send
    log("INFO", "QQBot 回复装饰补丁已应用（message_reference 自动附带 + @回复者标签）")
    return True


def patch_onebot_send_reply_deco() -> bool:
    """OneBot V11 回复装饰：@回复者 + 换行 + 自动引用（config 统一管理）。

    - config 群管理.at_sender：开启时群聊回复以 [at, "\\n", ...正文] 开头。
      插件内散装的 at_sender=True 已全部移除，由本开关统一控制；消息开头
      残留的换行（旧 @ 格式预留）会被剥离，格式统一由补丁生成。
    - config 群管理.reply_quote：开启时注入适配器原生 ``reply_message=True``，
      send_handler 自动按 [reply, at, 正文] 顺序拼接并自带 message_id 缺失
      保护；调用方显式传 reply_message=False 时不覆盖。
    """
    from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot
    from nonebot.adapters.onebot.v11.event import GroupMessageEvent, MessageEvent
    from nonebot.adapters.onebot.v11.message import Message, MessageSegment

    if getattr(OneBotV11Bot.send, "_reply_deco_patched", False):
        log("DEBUG", "OneBot 回复装饰补丁已应用，跳过")
        return False

    original_send = OneBotV11Bot.send

    @functools.wraps(original_send)
    async def send(self, event, message, **kwargs):
        # at_sender 参数兼容保留：显式传入或配置开启均生效（群聊）
        at_sender = kwargs.pop("at_sender", False)
        flags = _reply_flags()
        if isinstance(event, MessageEvent):
            at_sender = at_sender or flags["at_sender"]
            msg = Message(message)
            # 剥离消息开头为旧 @ 格式预留的换行（保险；正常应已在插件侧清理）
            if msg and msg[0].type == "text":
                stripped = msg[0].data["text"].lstrip("\n")
                if stripped != msg[0].data["text"]:
                    msg = (
                        Message([MessageSegment.text(stripped)] if stripped else [])
                        + Message(list(msg[1:]))
                    )
            if flags["reply_quote"]:
                kwargs.setdefault("reply_message", True)
            if (
                at_sender
                and isinstance(event, GroupMessageEvent)
                and event.user_id is not None
            ):
                message = Message(
                    [MessageSegment.at(event.user_id), MessageSegment.text("\n")]
                ) + msg
            else:
                message = msg
        return await original_send(self, event, message, **kwargs)

    send._reply_deco_patched = True
    OneBotV11Bot.send = send
    log("INFO", "OneBot V11 回复装饰补丁已应用（@回复者/换行/引用统一管理）")
    return True


def apply_all() -> dict[str, bool]:
    """应用全部适配器补丁（koinoribot_nb2 插件加载时调用，均可重复调用）。"""
    return {
        "qq_send_reply_quote": patch_qq_send_reply_quote(),
        "onebot_send_reply_deco": patch_onebot_send_reply_deco(),
    }
