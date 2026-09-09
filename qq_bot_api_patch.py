"""nonebot 适配器补丁集（QQ 群管理 API 为上游 PR #234 的向后移植）。

适配器 1.7.2（截至 2026-09 的 PyPI 最新版）缺少以下官Bot群管理接口，
上游 master 分支已在 PR #234 实现但尚未发版。本模块将这些方法按与上游
完全相同的签名补到 QQBot 类上，适配器发版后自动跳过、改用原生实现：

- GET  /v2/groups/{group_id}/info                                  获取群基本信息（白名单接口）
- GET  /v2/groups/{group_id}/restrict_chat_setting                 查询群禁言状态
- POST /v2/groups/{group_id}/restrict_chat_setting                 设置群成员禁言（op=add/update/del，最长30天）
- POST /v2/groups/{group_id}/approval_join_request/{member_openid} 审批入群申请（approve/decline）
- GET  /v2/groups/{group_id}/join_request_list                     拉取入群申请列表（join_request_id 来源）

撤回接口（DELETE /v2/groups/{group_openid}/messages/{message_id}）与
群消息全量事件 GROUP_MESSAGE_CREATE 在 1.7.2 中已原生支持，无需补丁。

另含：
- QQReplyMessage.message_type/msg_idx 可选化：修复平台合并消息（事件
  message_type=103）下发的 msg_elements 元素缺字段导致事件解析失败被丢弃。
- 回复装饰（config 群管理.at_sender / reply_quote）：官Bot 群/单聊回复自动
  附带 message_reference（线上已验证，平台不支持 bot 发 @）；OneBot V11 统一
  管理 @回复者+换行 与原生 reply_message 注入。
- apply_all()：插件加载时统一应用（koinoribot_nb2/__init__.py 调用）。

所有禁言/审批接口均要求机器人拥有群管理员身份。
本模块原位于 mlbot 根目录（git 仓库外），现随插件仓库维护。
"""

from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nonebot.compat import type_validate_python
from nonebot.drivers import Request

from nonebot.utils import escape_tag
from nonebot.adapters.qq.utils import API, exclude_none, log


# ================== 响应/请求模型（与上游 master models/qq.py 同名同构） ==================


class GroupInfoReturn(BaseModel):
    """群基本信息。注意：该接口为白名单机制，无权限的机器人调用返回错误码 11253。"""

    group_openid: str | None = None
    group_name: str | None = None
    group_finger_memo: str | None = None      # 群简介
    group_class_text: str | None = None       # 群分类
    group_tags: list[str] | None = None       # 群标签
    group_member_num: int | None = None       # 群成员人数


class GroupBotStateReturn(BaseModel):
    """机器人在群内的状态（member_role: member=普通成员, admin=管理员, owner=群主）"""

    member_openid: str | None = None
    joined_at: str | None = None
    allow_proactive_msg: bool | None = None
    recv_msg_setting: str | None = None       # all / only_mention / mention_and_context
    member_role: Literal["member", "admin", "owner"] | None = None


class MuteScheduleRule(BaseModel):
    """定时禁言规则（RFC3339 时间段）"""

    task_id: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    enabled: bool = True


class MuteRecurringRule(BaseModel):
    """周期禁言规则（weekdays: 1~7，1=周一；北京时间 HH:mm，end 小于 start 表示跨天）"""

    task_id: str | None = None
    weekdays: list[int] = []
    start_time: str | None = None
    end_time: str | None = None
    enabled: bool = True


class GlobalMuteRule(BaseModel):
    """全员禁言规则"""

    mode: Literal["none", "always", "schedule"] = "none"
    schedule_rules: list[MuteScheduleRule] | None = None
    recurring_rules: list[MuteRecurringRule] | None = None


class MemberMuteState(BaseModel):
    """禁言中的成员状态（仅返回未过期的成员）"""

    member_openid: str
    mute_expire_at: str | None = None
    username: str | None = None               # 被禁言成员的昵称
    union_openid: str | None = None


class GroupRestrictChatSettingReturn(BaseModel):
    """群禁言状态"""

    global_rule: GlobalMuteRule | None = None
    members: list[MemberMuteState] = []


class SetMemberMuteState(BaseModel):
    """设置成员禁言请求项。

    op=add/update 只能操作普通成员，不能操作群主、管理员、机器人；
    单次请求 members 不能超过 20 个；最大禁言时长 30 天。
    """

    op: Literal["add", "update", "del"]
    member_openid: str
    mute_expire_at: str | datetime | timedelta | None = None

    @field_validator("mute_expire_at", mode="before")
    @classmethod
    def _normalize_expire(cls, v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, timedelta):
            return (datetime.now(timezone.utc) + v).isoformat()
        return v


class ReviewQA(BaseModel):
    """入群验证问答（管理员设置的问题 + 申请人填写的答案）"""

    question: str | None = None
    answer: str | None = None


class VerifyInfo(BaseModel):
    """用户入群验证方式信息"""

    method: str | None = None            # verify_message / admin_review_qa
    verify_message: str | None = None
    review_qa_list: list[ReviewQA] | None = None


class JoinRequest(BaseModel):
    """入群申请"""

    model_config = ConfigDict(extra="ignore")

    join_request_id: str | None = None   # 申请 ID，审批接口需回传
    risk_tips: str | None = None
    union_openid: str | None = None
    member_openid: str | None = None
    username: str | None = None          # 申请人昵称
    apply_at: str | None = None          # 申请时间，RFC3339
    apply_source: Literal["self_apply", "invited"] | None = None
    invited_by: str | None = None        # 邀请人 openid（仅 invited 时有效）
    bot: bool | None = None
    verify_info: VerifyInfo | None = None


class JoinRequestListReturn(BaseModel):
    # 官方返回字段名为 list，与内建类型冲突，改用别名映射（两种名字均可填充）
    model_config = ConfigDict(populate_by_name=True)

    requests: list[JoinRequest] = Field(default=[], alias="list")
    next_cursor: str | None = None       # 下一页游标；空串表示末页


class AutoApproved(BaseModel):
    """自动审批通过的扩展信息（仅下行事件携带）"""

    strategy_id: str | None = None       # 自动审批通过的策略 ID


# ================== Bot 方法实现（签名与上游 master 一致） ==================


@API
async def get_group_info(self, *, group_id: str) -> GroupInfoReturn:
    request = Request(
        "GET",
        self.adapter.get_api_base().joinpath("v2", "groups", group_id, "info"),
    )
    return type_validate_python(GroupInfoReturn, await self._request(request))


@API
async def get_group_bot_state(self, *, group_id: str) -> GroupBotStateReturn:
    request = Request(
        "GET",
        self.adapter.get_api_base().joinpath("v2", "groups", group_id, "bot_state"),
    )
    return type_validate_python(GroupBotStateReturn, await self._request(request))


@API
async def get_group_mute_setting(self, *, group_id: str) -> GroupRestrictChatSettingReturn:
    request = Request(
        "GET",
        self.adapter.get_api_base().joinpath(
            "v2", "groups", group_id, "restrict_chat_setting"
        ),
    )
    return type_validate_python(
        GroupRestrictChatSettingReturn, await self._request(request)
    )


@API
async def set_group_members_mute(
    self, *, group_id: str, members: list[SetMemberMuteState]
) -> None:
    request = Request(
        "POST",
        self.adapter.get_api_base().joinpath(
            "v2", "groups", group_id, "restrict_chat_setting"
        ),
        json={
            "members": [
                m.model_dump(mode="json", exclude_none=True) for m in members
            ]
        },
    )
    await self._request(request)


@API
async def get_group_join_request_list(
    self,
    *,
    group_id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> JoinRequestListReturn:
    request = Request(
        "GET",
        self.adapter.get_api_base().joinpath(
            "v2", "groups", group_id, "join_request_list"
        ),
        params=exclude_none({"cursor": cursor, "limit": limit}),
    )
    return type_validate_python(
        JoinRequestListReturn, await self._request(request)
    )


@API
async def approval_join_request(
    self,
    *,
    group_id: str,
    member_openid: str,
    op: Literal["approve", "decline"],
    join_request_id: str | None = None,
    reject_reason: str | None = None,
    add_to_member_blacklist: bool | None = None,
) -> None:
    request = Request(
        "POST",
        self.adapter.get_api_base().joinpath(
            "v2", "groups", group_id, "approval_join_request", member_openid
        ),
        json=exclude_none(
            {
                "op": op,
                "join_request_id": join_request_id,
                "reject_reason": reject_reason,
                "add_to_member_blacklist": add_to_member_blacklist,
            }
        ),
    )
    await self._request(request)


_IMPLEMENTATIONS = {
    "get_group_info": get_group_info,
    "get_group_bot_state": get_group_bot_state,
    "get_group_mute_setting": get_group_mute_setting,
    "set_group_members_mute": set_group_members_mute,
    "get_group_join_request_list": get_group_join_request_list,
    "approval_join_request": approval_join_request,
}


def patch_qq_bot_group_admin_apis() -> dict[str, bool]:
    """把群管理 API 补到 nonebot.adapters.qq.Bot 上。

    适配器已原生提供（升级到含 PR #234 的版本后）的方法自动跳过，不覆盖。
    返回 {方法名: 是否由本补丁添加}。

    API 是依赖 ``__set_name__`` 的描述符，class-body 之外 setattr 不会触发它，
    因此手动补 ``name``，使实例访问 ``bot.xxx()`` 与原生方法一样走
    ``call_api``（含 CallingAPI hook 与调用日志）。
    """
    from nonebot.adapters.qq import Bot as QQBot
    from nonebot.adapters.qq.utils import API

    status: dict[str, bool] = {}
    for name, func in _IMPLEMENTATIONS.items():
        if hasattr(QQBot, name):
            status[name] = False
        else:
            setattr(QQBot, name, func)
            if isinstance(func, API) and not hasattr(func, "name"):
                func.name = name
            status[name] = True
    patched = [name for name, added in status.items() if added]
    if patched:
        log("INFO", f"QQBot 群管理 API 补丁已应用: {patched}")
    else:
        log("DEBUG", "QQBot 群管理 API 已由适配器原生提供，跳过补丁")
    return status


# ================== GROUP_JOIN_REQUEST 事件补丁（上游 PR #234 未发版） ==================


class _GroupJoinRequestEventType(str, Enum):
    GROUP_JOIN_REQUEST = "GROUP_JOIN_REQUEST"


def _build_group_join_request_event():
    """构造 GroupJoinRequestEvent 事件类（字段与官方文档/上游 PR #234 一致）。"""
    from nonebot.adapters.qq.event import NoticeEvent

    class GroupJoinRequestEvent(NoticeEvent, JoinRequest):
        """用户申请加群事件（Intent GROUP_MEMBER_EVENT 1<<24，仅群管理员可收到）"""

        __type__ = _GroupJoinRequestEventType.GROUP_JOIN_REQUEST

        group_openid: str
        auto_approved: AutoApproved | None = None

        def get_user_id(self) -> str:
            return self.member_openid or ""

        def get_session_id(self) -> str:
            return f"group_{self.group_openid}_{self.member_openid}"

        def get_event_description(self) -> str:
            return escape_tag(
                f"JoinRequest {self.join_request_id} from "
                f"{self.member_openid}@[Group:{self.group_openid}]"
            )

    return GroupJoinRequestEvent


GroupJoinRequestEvent = _build_group_join_request_event()


def patch_qq_group_join_request_event() -> bool:
    """把 GROUP_JOIN_REQUEST 事件类注册进适配器事件表（上游发版前的本地补丁）。

    适配器原生提供该事件类后自动跳过。注意：接收推送还需订阅
    GROUP_MEMBER_EVENT(1<<24) intent——在 .env 的 QQ_BOTS.intent 中
    设置 "group_members": true。
    """
    from nonebot.adapters.qq.event import EVENT_CLASSES

    if "GROUP_JOIN_REQUEST" in EVENT_CLASSES:
        log("DEBUG", "GROUP_JOIN_REQUEST 事件已由适配器原生提供，跳过补丁")
        return False

    EVENT_CLASSES["GROUP_JOIN_REQUEST"] = GroupJoinRequestEvent
    log("INFO", "QQBot GROUP_JOIN_REQUEST 事件补丁已注册")
    return True


# ================== QQReplyMessage 可选字段补丁（上游 master 修复的向后移植） ==================


def patch_qq_reply_message_optional_fields() -> bool:
    """把 ``QQReplyMessage.message_type/msg_idx`` 改为可选字段。

    平台对合并消息（事件 message_type=103）下发的 msg_elements 元素可能只
    携带 content 而缺少 message_type/msg_idx。1.7.2 的模型将两者声明为必填，
    导致整条 GROUP_MESSAGE_CREATE 事件 ValidationError、消息被直接丢弃。
    上游 master 已改为 ``int | None = None`` / ``str | None = None``，
    此处发版前等价移植；适配器发版后字段已可选，自动跳过。

    适配器内部对 msg_elements 仅按 ``element.msg_idx == ref_msg_idx`` 比对
    寻找被引用消息，缺失时为 None、不命中即跳过，改为可选不影响其逻辑。
    """
    from nonebot.adapters.qq.models.qq import QQReplyMessage

    fields = QQReplyMessage.model_fields
    required = [
        field
        for field in (fields.get("message_type"), fields.get("msg_idx"))
        if field is not None and field.is_required()
    ]
    if not required:
        log("DEBUG", "QQReplyMessage 字段已可选（适配器已修复），跳过补丁")
        return False

    for field in required:
        field.annotation = Optional[field.annotation]
        field.default = None
    QQReplyMessage.model_rebuild(force=True)

    # 引用该模型的类在构建时内嵌了旧的 QQReplyMessage core schema，需一并重建
    rebuild_targets: list[type] = []
    try:
        from nonebot.adapters.qq.models.qq import (
            GroupQQMessage,
            QQMessage,
            UserQQMessage,
        )

        rebuild_targets += [QQMessage, UserQQMessage, GroupQQMessage]
    except ImportError:
        pass
    try:
        from nonebot.adapters.qq import event as qq_event

        rebuild_targets += [
            cls
            for attr in (
                "QQMessageEvent",
                "C2CMessageCreateEvent",
                "GroupMessageCreateEvent",
                "GroupAtMessageCreateEvent",
            )
            if (cls := getattr(qq_event, attr, None)) is not None
        ]
    except ImportError:
        pass
    for cls in rebuild_targets:
        try:
            cls.model_rebuild(force=True)
        except Exception as e:  # 单个类重建失败不阻塞，其余类仍能正常解析
            log("WARNING", f"重建 {cls.__module__}.{cls.__name__} 失败: {e!r}")

    log("INFO", "QQReplyMessage.message_type/msg_idx 已改为可选（合并消息事件解析补丁）")
    return True


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
      image_host 图床（基于 config.ip_address，与 ws 共用驱动端口）转
      公网 URL 后内嵌。**降级**：无法内嵌时（视频/音频/文件段、图片字节
      无法暴露公网 URL——未配置 ip_address 等），无论引用开关是否打开，
      自动降级为引用模式（附带 message_reference 定位回复对象）。正文
      中的 markdown 元字符未做转义——QQ 自定义 markdown 对不成对的
      ``*`` ``_`` 等按原文显示，若实测出现斜体/标题等样式串扰再补转义。
    """
    from nonebot.adapters.qq import Bot as QQBot
    from nonebot.adapters.qq.event import (
        C2CMessageCreateEvent,
        GroupMessageCreateEvent,
    )
    from nonebot.adapters.qq.message import Message, MessageSegment

    if getattr(QQBot.send, "_reply_quote_patched", False):
        log("DEBUG", "QQ 回复引用补丁已应用，跳过")
        return False

    original_send = QQBot.send

    @functools.wraps(original_send)
    async def send(self, event, message, **kwargs):
        prepend = []
        if isinstance(event, (GroupMessageCreateEvent, C2CMessageCreateEvent)):
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
            if flags["at_sender"] and isinstance(event, GroupMessageCreateEvent):
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
                        from .image_host import embed_image

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
                                # 字节图：经图床转公网 URL（超限自动压缩，宽高同步）
                                embedded = embed_image(bytes(content))
                                if embedded is None:
                                    embed_failed = True
                                    break
                                url, width, height = embedded
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
        return await original_send(self, event, message, **kwargs)

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
        "qq_group_admin_apis": patch_qq_bot_group_admin_apis(),
        "qq_group_join_request_event": patch_qq_group_join_request_event(),
        "qq_reply_message_optional_fields": patch_qq_reply_message_optional_fields(),
        "qq_send_reply_quote": patch_qq_send_reply_quote(),
        "onebot_send_reply_deco": patch_onebot_send_reply_deco(),
    }
