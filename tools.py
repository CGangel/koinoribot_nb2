"""
工具函数模块

提供 NoneBot2 依赖注入兼容的工具函数，支持 OneBot V11 和 QQ-Bot 双协议。
"""

from typing import Optional, List, Dict, Any, Union
import base64

import nonebot.adapters.onebot.v11 as onebot
from nonebot.adapters import Event, Bot
from nonebot.adapters import qq
from nonebot.log import logger
from nonebot.params import Depends
import httpx
import io
import time as _time
import textwrap
from datetime import datetime, timedelta, timezone
from .build_image import BuildImage

from .uid_manager import get_uid as get_unified_uid
from .uid_manager import get_uid_by_external_id
from .uid_manager import get_external_ids
from .nickname import get_user_nickname

BASE64_PREFIX = "base64://"

# 官Bot 禁言到期时间按北京时间（RFC3339 +08:00）生成
_QQ_TZ = timezone(timedelta(hours=8))

# ===== UID 相关 =====

def _get_platform_uid(event: Event) -> str:
    uid = event.get_user_id()
    logger.debug(f"获取平台UID：{uid}")
    return uid


def resolve_uid(event: Event, platform_uid: str) -> int:
    """根据事件和平台 UID 获取统一 UID，并绑定当前钱包上下文。"""
    uuid = None
    if isinstance(event, onebot.Event):
        uuid = get_unified_uid(platform="onebot", external_id=platform_uid)
    if isinstance(event, qq.Event):
        uuid = get_unified_uid(platform="qqbot", external_id=platform_uid)
    if uuid is None:
        raise ValueError(f"不支持的事件类型：{type(event)}")
    from .money import bind_current_uid
    bind_current_uid(uuid)
    logger.debug(f"获取统一UID：{uuid}")
    return uuid


def get_uid(event: Event, platform_uid: str = Depends(_get_platform_uid)) -> int:
    """获取统一 UID（依赖注入版本）"""
    return resolve_uid(event, platform_uid)


def get_group_id(event: Event) -> str:
    """获取群组 ID""" 
    if isinstance(event, onebot.GroupMessageEvent):
        return str(event.group_id)
    if isinstance(event, qq.Event) and hasattr(event, 'group_openid') and event.group_openid:
        return event.group_openid
    raise ValueError(f"不支持的事件类型：{type(event)}")


def get_group_id_optional(event: Event) -> Optional[str]:
    """获取群组 ID（可选，私聊返回 None）"""
    try:
        return get_group_id(event)
    except ValueError:
        return None


# ===== QQ Bot 昵称 API =====

_qqbot_appid: str = ""
_qqbot_openid_api: str = ""
_nickname_cache: dict[str, tuple[str, float]] = {}  # {openid: (nickname, timestamp)}
_NICKNAME_CACHE_TTL = 3600  # 缓存1小时


def set_qqbot_appid(appid: str, api_url: str = ""):
    """设置官Bot AppID和API地址（启动时调用）"""
    global _qqbot_appid, _qqbot_openid_api
    _qqbot_appid = appid
    _qqbot_openid_api = api_url


async def _fetch_qqbot_nickname(openid: str) -> str:
    """通过第三方 API 获取官Bot用户昵称（带缓存）。

    仅为降级路径：QQ 消息事件自带官方昵称字段（author.username），
    缺失时（非消息事件/平台未下发）才走这里。
    """
    if not _qqbot_appid or not _qqbot_openid_api:
        return ""
    
    # 检查缓存
    if openid in _nickname_cache:
        cached_name, cached_time = _nickname_cache[openid]
        if _time.time() - cached_time < _NICKNAME_CACHE_TTL:
            return cached_name
    
    # 请求 API
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _qqbot_openid_api,
                params={"appid": _qqbot_appid, "openid": openid},
                timeout=3
            )
            data = resp.json()
            if data.get("code") == 1:
                nickname = data.get("data", {}).get("nickname", "")
                if nickname:
                    _nickname_cache[openid] = (nickname, _time.time())
                    return nickname
    except Exception as e:
        logger.debug(f"获取官Bot用户昵称失败: {e}")
    
    return ""


# ===== 用户信息相关 =====

async def get_sender_nickname(event: Event) -> str:
    """获取发送者昵称（自定义昵称 > 平台官方昵称 > 第三方 API 降级）"""
    try:
        platform_uid = event.get_user_id()
        uid = resolve_uid(event, platform_uid)
        custom_nickname = get_user_nickname(uid)
        if custom_nickname:
            return custom_nickname
    except Exception as e:
        logger.debug(f"获取自定义昵称失败: {e}")

    if isinstance(event, onebot.MessageEvent):
        if event.sender:
            return event.sender.nickname or event.sender.card or ""
    if isinstance(event, qq.Event):
        # 官方实现优先：QQ 消息事件的 author.username 即用户昵称
        author = getattr(event, "author", None)
        username = getattr(author, "username", None) if author else None
        if username:
            return username
        # 降级路径：第三方 API 按 openid 查询
        api_nickname = await _fetch_qqbot_nickname(event.get_user_id())
        if api_nickname:
            return api_nickname
    return ""


def _qqbot_avatar_url(event: Event, uid: Optional[int]) -> str:
    openid = event.get_user_id()
    if _qqbot_appid and openid:
        return f'https://thirdqq.qlogo.cn/qqapp/{_qqbot_appid}/{openid}/100'
    if uid is not None:
        external_ids = get_external_ids(uid)
        onebot_id = external_ids.get("onebot_id")
        if onebot_id:
            return f'https://q1.qlogo.cn/g?b=qq&nk={onebot_id}&s=640'
    author = getattr(event, "author", None)
    return getattr(author, "avatar", "") if author else ""


def get_user_avatar_url(event: Event, uid: Optional[int] = None) -> str:
    """获取用户头像 URL"""
    if isinstance(event, onebot.Event):
        user_id = event.get_user_id()
        return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'
    if isinstance(event, qq.Event):
        return _qqbot_avatar_url(event, uid)
    return ''


def is_onebot(event: Event) -> bool:
    """判断是否为 OneBot 协议"""
    return isinstance(event, onebot.Event)


def is_qqbot(event: Event) -> bool:
    """判断是否为 QQ-Bot 协议"""
    return isinstance(event, qq.Event)


# ===== 消息发送相关 =====


def _normalize_forward_nodes(messages) -> list:
    node_list = (
        list(messages)
        if isinstance(messages, onebot.Message)
        else messages
    )
    compatible_nodes = []
    for node in node_list:
        if isinstance(node, onebot.MessageSegment) and node.type == "node":
            compatible_nodes.append({
                "data": {
                    "name": node.data.get("nickname", "用户"),
                    "content": node.data.get("content", ""),
                }
            })
        elif isinstance(node, dict):
            compatible_nodes.append(node)
    return compatible_nodes


def _segment_type_and_data(segment):
    if isinstance(segment, onebot.MessageSegment):
        return segment.type, segment.data
    if isinstance(segment, dict):
        return segment.get('type'), segment.get('data', {})
    return None, {}


def _qq_image_segment(segment_data):
    file_uri = segment_data.get('file', '')
    if file_uri.startswith(BASE64_PREFIX):
        try:
            image_bytes = base64.b64decode(
                file_uri.removeprefix(BASE64_PREFIX)
            )
            return qq.MessageSegment.file_image(image_bytes)
        except (ValueError, TypeError) as error:
            logger.error(f"解析合并转发图片失败: {error}")
            return qq.MessageSegment.text("[图片解析失败]")
    if file_uri.startswith('http'):
        return qq.MessageSegment.image(file_uri)
    url = segment_data.get('url')
    if url:
        return qq.MessageSegment.image(url)
    return qq.MessageSegment.text("[不支持的图片格式]")


def _qq_message_from_content(content):
    if isinstance(content, str):
        return qq.Message([qq.MessageSegment.text(content)])
    if not isinstance(content, (onebot.Message, list)):
        return qq.Message()

    message = qq.Message()
    for segment in content:
        segment_type, segment_data = _segment_type_and_data(segment)
        if segment_type == 'text':
            text = segment_data.get('text', '')
            if text:
                message.append(qq.MessageSegment.text(text))
        elif segment_type == 'image':
            message.append(_qq_image_segment(segment_data))
    return message


async def _send_forward_nodes_individually(
    event: Event,
    bot: Bot,
    nodes: list,
):
    for node in nodes:
        content = node.get('data', {}).get('content', '')
        message = _qq_message_from_content(content)
        if message:
            await bot.send(event, message)


async def send_group_forward_msg(
    event: Event, 
    bot: Bot, 
    messages
) -> None:
    """
    发送合并转发消息
    
    Args:
        event: 事件对象
        bot: Bot 对象
        messages: 合并转发消息节点列表（onebot.Message 或 List[MessageSegment]）
    
    Note:
        QQ-Bot 不支持合并转发，会降级为普通消息依次发送
    """
    if isinstance(event, onebot.GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=messages)
        return

    compatible_nodes = _normalize_forward_nodes(messages)
    try:
        image_bytes = await _nodes_to_image(compatible_nodes)
        if image_bytes:
            await bot.send(event, qq.MessageSegment.file_image(image_bytes))
            return
    except Exception as error:
        logger.error(f"合并转发转图片失败: {error}")
    await _send_forward_nodes_individually(event, bot, compatible_nodes)


async def build_forward_node(
    bot: Bot,
    msg,
    user_id: int = 0
) -> onebot.MessageSegment:
    """
    构建合并转发消息节点
    
    Args:
        bot: Bot 对象
        msg: 消息内容（str 或消息段列表或 onebot.Message）
        user_id: 发送者 ID（0 表示使用 bot 自身）
    
    Returns:
        onebot.MessageSegment (node_custom 类型)
    """
    if not user_id:
        user_id = int(bot.self_id)
    
    try:
        user_info = await bot.get_stranger_info(user_id=user_id)
        user_name = user_info.get('nickname', '用户')
    except Exception:
        user_name = '用户'
    
    if not user_name.strip():
        user_name = '用户'
    
    # 构建 onebot.Message 内容
    if isinstance(msg, onebot.Message):
        content = msg
    elif isinstance(msg, onebot.MessageSegment):
        content = onebot.Message([msg])
    elif isinstance(msg, list):
        # msg 是消息段列表 [{"type": "text", "data": {"text": "..."}}]
        ob_msg = onebot.Message()
        for seg in msg:
            if isinstance(seg, dict):
                ob_msg.append(onebot.MessageSegment(type=seg["type"], data=seg.get("data", {})))
            elif isinstance(seg, onebot.MessageSegment):
                ob_msg.append(seg)
        content = ob_msg
    else:
        content = onebot.Message([onebot.MessageSegment.text(str(msg))])
    
    # 使用 MessageSegment.node_custom 构建节点，确保 DataclassEncoder 能正确序列化
    return onebot.MessageSegment.node_custom(
        user_id=user_id,
        nickname=user_name,
        content=content
    )


async def build_forward_chain(
    bot: Bot,
    messages: List[str],
    user_id: int = 0
) -> onebot.Message:
    """
    批量构建合并转发消息链
    
    Args:
        bot: Bot 对象
        messages: 消息内容列表
        user_id: 发送者 ID
    
    Returns:
        onebot.Message 包含的 node_custom 节点列表
    """
    chain = onebot.Message()
    for msg in messages:
        node = await build_forward_node(bot, msg, user_id)
        chain.append(node)
    return chain

# ===== 用户at相关 =====
def get_at_uid_onebot(message_segment:onebot.MessageSegment) -> str:
    """
    获取 onebot v11 的 @ 消息中的 uid

    Args:
        message_segment: onebot v11 的 @ 消息

    Returns:
        uid: 消息中的 uid

    Raises:
        ValueError: 消息不是 @ 消息
    """
    if message_segment.type == "at":
        return message_segment.data["qq"]
    raise ValueError("消息不是at消息")


def get_at_uid_qqbot(message_segment:qq.MessageSegment) -> str:
    """
    获取 qqbot 的 @ 消息中的 uid

    Args:
        message_segment: qqbot 的 @ 消息

    Returns:
        uid: 消息中的 uid

    Raises:
        ValueError: 消息不是 @ 消息
    """
    if message_segment.type == "mention_user":
        return message_segment.data["user_id"]
    raise ValueError("消息不是at消息")

def get_at_uid(message_segment:onebot.MessageSegment | qq.MessageSegment) -> Optional[int]:
    """
    获取消息中的 uid

    Args:
        message_segment: 消息

    Returns:
        uid: 消息中的 uid, None时表示没有账户

    Raises:
        ValueError: 消息不是at消息
    """

    uuid = None
    if isinstance(message_segment, onebot.MessageSegment):
        uid = get_at_uid_onebot(message_segment)
        uuid = get_uid_by_external_id(platform="onebot", external_id=uid)
    elif isinstance(message_segment, qq.MessageSegment):
        uid = get_at_uid_qqbot(message_segment)
        uuid = get_uid_by_external_id(platform="qqbot", external_id=uid)
    return uuid

# ===== 图片生成辅助 =====


def _measure_text_width(image: BuildImage, text: str) -> float:
    try:
        return image.font.getlength(text)
    except AttributeError:
        return image.font.getsize(text)[0]


def _draw_wrapped_text(
    image: BuildImage,
    text: str,
    current_y: int,
    width: int,
    padding: int,
    font_size: int,
    line_spacing: int,
) -> int:
    max_width = width - 2 * padding
    for original_line in text.split('\n'):
        if not original_line:
            current_y += font_size + line_spacing
            continue

        current_line = ""
        for character in original_line:
            test_line = current_line + character
            if (
                _measure_text_width(image, test_line) > max_width
                and current_line
            ):
                image.text(
                    (padding, current_y),
                    current_line,
                    fill=(0, 0, 0),
                )
                current_y += font_size + line_spacing
                current_line = character
            else:
                current_line = test_line

        if current_line:
            image.text(
                (padding, current_y),
                current_line,
                fill=(0, 0, 0),
            )
            current_y += font_size + line_spacing
    return current_y


async def _node_image_data(segment_data: dict) -> bytes | None:
    file_uri = segment_data.get('file', '')
    url = segment_data.get('url', '')
    if file_uri.startswith(BASE64_PREFIX):
        try:
            return base64.b64decode(
                file_uri.removeprefix(BASE64_PREFIX)
            )
        except (ValueError, TypeError):
            return None
    if file_uri.startswith('http'):
        url = file_uri
    if not url:
        return None

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10)
        except httpx.HTTPError:
            return None
    return response.content if response.status_code == 200 else None


def _paste_node_image(
    image: BuildImage,
    image_data: bytes | None,
    current_y: int,
    width: int,
    padding: int,
    font_size: int,
    line_spacing: int,
) -> int:
    if not image_data:
        image.text((padding, current_y), "[图片]", fill=(100, 100, 100))
        return current_y + font_size + line_spacing

    try:
        from PIL import Image
        picture = Image.open(io.BytesIO(image_data))
        picture_width, picture_height = picture.size
        max_width = width - 2 * padding
        if picture_width > max_width:
            ratio = max_width / picture_width
            picture_height = int(picture_height * ratio)
            picture = picture.resize((max_width, picture_height))
        image.paste(picture, (padding, current_y))
        return current_y + picture_height + line_spacing
    except (OSError, TypeError, ValueError):
        image.text(
            (padding, current_y),
            "[图片加载失败]",
            fill=(255, 0, 0),
        )
        return current_y + font_size + line_spacing


async def _draw_node_segment(
    image: BuildImage,
    segment,
    current_y: int,
    width: int,
    padding: int,
    font_size: int,
    line_spacing: int,
) -> int:
    segment_type, segment_data = _segment_type_and_data(segment)
    if segment_type == 'text':
        text = segment_data.get('text', '')
        if text:
            return _draw_wrapped_text(
                image,
                text,
                current_y,
                width,
                padding,
                font_size,
                line_spacing,
            )
    elif segment_type == 'image':
        return _paste_node_image(
            image,
            await _node_image_data(segment_data),
            current_y,
            width,
            padding,
            font_size,
            line_spacing,
        )
    return current_y


async def _create_node_image(node: Dict[str, Any], width: int = 600, font_size: int = 20) -> BuildImage:
    """创建单个消息节点的图片"""
    data = node.get('data', {})
    content = data.get('content', [])
    padding = 10
    line_spacing = 5
    temp_height = 5000
    img = BuildImage(width, temp_height, font_size=font_size, color=(255, 255, 255))
    current_y = padding

    if isinstance(content, str):
        content = [{'type': 'text', 'data': {'text': content}}]

    for segment in content:
        current_y = await _draw_node_segment(
            img,
            segment,
            current_y,
            width,
            padding,
            font_size,
            line_spacing,
        )

    if current_y + padding < temp_height:
        img.crop((0, 0, width, current_y + padding))
    return img

async def _nodes_to_image(messages: List[Dict[str, Any]]) -> bytes:
    """将消息链转换为长图"""
    images = []
    width = 600
    
    for node in messages:
        try:
            img = await _create_node_image(node, width=width)
            images.append(img)
        except Exception as e:
            logger.warning(f"生成节点图片失败: {e}")
            continue
            
    if not images:
        return b""
        
    total_height = sum(img.h for img in images)
    final_img = BuildImage(width, total_height, color=(255, 255, 255))
    
    current_y = 0
    for img in images:
        final_img.paste(img.mark_img, (0, current_y))
        current_y += img.h
        
    output = io.BytesIO()
    final_img.mark_img.save(output, format='PNG')
    return output.getvalue()


def build_image_msg(event: Event, image_data: Union[bytes, str]):
    """
    根据适配器类型构建图片消息段

    Args:
        event: 事件对象，用于判断适配器类型
        image_data: 图片数据，可以是 bytes（原始图片）或 str（base64 编码字符串）

    Returns:
        对应适配器的图片消息段
    """
    if isinstance(image_data, str):
        # base64 字符串，先解码为 bytes
        image_bytes = base64.b64decode(image_data)
        b64_str = image_data
    else:
        image_bytes = image_data
        b64_str = base64.b64encode(image_bytes).decode()

    if isinstance(event, qq.Event):
        from nonebot.adapters.qq import MessageSegment as QQMsgSeg
        return QQMsgSeg.file_image(image_bytes)
    else:
        from nonebot.adapters.onebot.v11 import MessageSegment as OBMsgSeg
        return OBMsgSeg.image(f"{BASE64_PREFIX}{b64_str}")


# ===== 群管理 API=====



def _load_qq_set_mute_state():
    """加载官Bot禁言请求模型：优先本地补丁，适配器升级后回退原生模型。"""
    try:
        from qq_bot_api_patch import SetMemberMuteState
    except ImportError:
        from nonebot.adapters.qq.models.qq import SetMemberMuteState
    return SetMemberMuteState


async def recall_message(
    bot: Bot,
    *,
    message_id,
    group_openid: str = "",
    openid: str = "",
) -> bool:
    """撤回消息（失败返回 False 并记录日志）。

    - OneBot：delete_msg，群聊/私聊通用。
    - QQBot 群聊：delete_group_message，需 group_openid；发出 2 分钟内有效，
      官Bot为群管理员时可撤回普通成员的消息（message_id 取群消息事件的 id）。
    - QQBot 单聊：delete_c2c_message，需 openid。
    """
    try:
        if isinstance(bot, onebot.Bot):
            await bot.delete_msg(message_id=message_id)
            return True
        if isinstance(bot, qq.Bot):
            if group_openid:
                await bot.delete_group_message(
                    group_openid=group_openid, message_id=str(message_id)
                )
            elif openid:
                await bot.delete_c2c_message(
                    openid=openid, message_id=str(message_id)
                )
            else:
                raise ValueError("QQBot 撤回需要 group_openid（群聊）或 openid（单聊）")
            return True
        raise ValueError(f"不支持的协议: {type(bot)}")
    except Exception as e:
        logger.warning(f"撤回消息失败: {e}")
        return False


async def get_group_info(
    bot: Bot, *, group_id=None, group_openid: str = ""
) -> Optional[Dict[str, Any]]:
    """获取群信息，归一化返回 {group_name, member_count, raw}，失败返回 None。

    - OneBot：get_group_info，需 group_id。
    - QQBot：需 group_openid；该接口为白名单机制，无权限时返回 None。
    """
    try:
        if isinstance(bot, onebot.Bot):
            if group_id is None:
                raise ValueError("OneBot 获取群信息需要 group_id")
            raw = await bot.get_group_info(group_id=int(group_id))
            return {
                "group_name": raw.get("group_name"),
                "member_count": raw.get("member_count"),
                "raw": raw,
            }
        if isinstance(bot, qq.Bot):
            if not group_openid:
                raise ValueError("QQBot 获取群信息需要 group_openid")
            info = await bot.get_group_info(group_id=group_openid)
            return {
                "group_name": info.group_name,
                "member_count": info.group_member_num,
                "raw": info.model_dump(),
            }
        raise ValueError(f"不支持的协议: {type(bot)}")
    except Exception as e:
        logger.warning(f"获取群信息失败: {e}")
        return None


async def mute_group_member(
    bot: Bot,
    *,
    group_id=None,
    group_openid: str = "",
    user_id=None,
    member_openid: str = "",
    duration: int = 600,
) -> bool:
    """禁言群成员 duration 秒（duration<=0 解除禁言），失败返回 False 并记录日志。

    - OneBot：set_group_ban，需 group_id + user_id。
    - QQBot：set_group_members_mute，需 group_openid + member_openid；
      最长 30 天，且不能禁言群主/管理员/机器人。
    """
    try:
        if isinstance(bot, onebot.Bot):
            if group_id is None or user_id is None:
                raise ValueError("OneBot 禁言需要 group_id 和 user_id")
            await bot.set_group_ban(
                group_id=int(group_id), user_id=int(user_id), duration=int(duration)
            )
            return True
        if isinstance(bot, qq.Bot):
            if not group_openid or not member_openid:
                raise ValueError("QQBot 禁言需要 group_openid 和 member_openid")
            SetMemberMuteState = _load_qq_set_mute_state()
            if duration > 0:
                expire = datetime.now(_QQ_TZ) + timedelta(seconds=duration)
                member = SetMemberMuteState(
                    op="add", member_openid=member_openid, mute_expire_at=expire
                )
            else:
                member = SetMemberMuteState(op="del", member_openid=member_openid)
            await bot.set_group_members_mute(group_id=group_openid, members=[member])
            return True
        raise ValueError(f"不支持的协议: {type(bot)}")
    except Exception as e:
        logger.warning(f"禁言群成员失败: {e}")
        return False


async def get_group_mute_setting(
    bot: Bot, *, group_openid: str = ""
) -> Optional[Dict[str, Any]]:
    """查询群禁言状态，归一化返回 {mode, members, raw}。

    仅 QQBot 支持（需群管理员），OneBot 无对应接口，返回 None。
    members 为当前仍在禁言中的成员：[{member_openid, username, mute_expire_at}]。
    """
    if not (isinstance(bot, qq.Bot) and group_openid):
        return None
    try:
        setting = await bot.get_group_mute_setting(group_id=group_openid)
        return {
            "mode": setting.global_rule.mode if setting.global_rule else None,
            "members": [m.model_dump() for m in setting.members],
            "raw": setting.model_dump(),
        }
    except Exception as e:
        logger.warning(f"查询群禁言状态失败: {e}")
        return None


async def get_group_join_requests(
    bot: Bot,
    *,
    group_openid: str = "",
    cursor: str = "",
    limit: int = 0,
) -> Optional[Dict[str, Any]]:
    """拉取入群申请列表（仅 QQBot 支持，需群管理员；OneBot 无对应接口返回 None）。

    返回归一化 {requests, next_cursor, raw}：
    - requests: [{join_request_id, member_openid, username, apply_source,
      verify_info, ...}]，join_request_id 供 approve_group_join_request 回传。
    - next_cursor: 下一页游标，空串表示末页（limit 默认 20、最大 50）。
    """
    if not (isinstance(bot, qq.Bot) and group_openid):
        return None
    try:
        result = await bot.get_group_join_request_list(
            group_id=group_openid, cursor=cursor or None, limit=limit or None
        )
        return {
            "requests": [r.model_dump() for r in result.requests],
            "next_cursor": result.next_cursor or "",
            "raw": result.model_dump(),
        }
    except Exception as e:
        logger.warning(f"拉取入群申请列表失败: {e}")
        return None


async def approve_group_join_request(
    bot: Bot,
    approve: bool,
    *,
    flag: str = "",
    sub_type: str = "add",
    group_openid: str = "",
    member_openid: str = "",
    join_request_id: str = "",
    reject_reason: str = "",
    add_to_member_blacklist: bool = False,
) -> bool:
    """审批入群申请（approve=True 同意，False 拒绝），失败返回 False 并记录日志。

    - OneBot：set_group_add_request，需 flag（请求事件携带）与 sub_type（add/invite）。
    - QQBot：approval_join_request，需 group_openid + member_openid，
      可附 join_request_id（入群申请列表/入群申请事件提供）。
    """
    try:
        if isinstance(bot, onebot.Bot):
            if not flag:
                raise ValueError("OneBot 审批入群需要 flag")
            await bot.set_group_add_request(
                flag=str(flag), sub_type=sub_type, approve=approve
            )
            return True
        if isinstance(bot, qq.Bot):
            if not group_openid or not member_openid:
                raise ValueError("QQBot 审批入群需要 group_openid 和 member_openid")
            await bot.approval_join_request(
                group_id=group_openid,
                member_openid=member_openid,
                op="approve" if approve else "decline",
                join_request_id=join_request_id or None,
                reject_reason=reject_reason if not approve else None,
                add_to_member_blacklist=add_to_member_blacklist if not approve else False,
            )
            return True
        raise ValueError(f"不支持的协议: {type(bot)}")
    except Exception as e:
        logger.warning(f"审批入群申请失败: {e}")
        return False
