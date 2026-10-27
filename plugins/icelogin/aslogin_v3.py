"""签到卡片与钱包图片生成模块（迁移自旧版）。"""

import asyncio
import io
import random
import time
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp

from ...money import money
from ...build_image import BuildImage
from .color_convert import lab2rgb

# 调试模式配置
debug_mode = 0
add_watermark = 0
debug_login_flag = 0
save_image_mode = 0
debug_background = 'Background2.jpg'
SIGN_FONT = 'HYShiGuangTiW_0.ttf'
BODY_FONT = 'yz.ttf'

def _get_background_images(srcpath: str) -> list:
    """扫描 background 目录获取所有背景图片"""
    bg_dir = os.path.join(srcpath, 'background')
    if not os.path.isdir(bg_dir):
        return []
    exts = {'.jpg', '.jpeg', '.png'}
    files = [f for f in os.listdir(bg_dir)
             if os.path.splitext(f)[1].lower() in exts]
    return files

extra_bg_list = ['Background_extra.jpg']
PURSE_NORMAL_BACKGROUNDS = [
    'purse_01.jpg',
    'purse_02.jpg',
    'purse_05.jpg',
    'purse_09.jpg',
]

# 宜忌列表
goodluck = [
    '宜 抽卡', '宜 干饭', '宜 摸鱼', '宜 刷副本', '宜 女装', '宜 打游戏',
    '宜 刷b站', '宜 看涩图', '宜 逛街', '宜 好好学习', '宜 搓麻将', '宜 工作',
    '宜 点外卖', '宜 水群', '宜 听音乐', '宜 背单词', '宜 做作业', '宜 刷抖音',
    '宜 睡觉', '宜 刷剧'
]

badluck = [
    '忌 抽卡上头', '忌 躺平', '忌 摸鱼', '忌 刷副本', '忌 女装', '忌 打游戏',
    '忌 刷b站', '忌 看涩图', '忌 逛街', '忌 学习', '忌 搓麻将', '忌 摆烂',
    '忌 点外卖', '忌 水群', '忌 听音乐', '忌 背单词', '忌 做作业', '忌 刷抖音',
    '忌 睡懒觉', '忌 刷剧', '忌 无'
]

# 生日和节日列表
birth_list = [
    "1232", "0422", "0725", "0711", "1126", "1231", "0606", "0520", "0709",
    "0320", "1221", "0614", "0120", "1218", "0221", "1104", "0101", "0504"
]

member_list = [
    "冰祈", "御子柴", "梦馨", "《灵歌少女》", "永远都在的冥蝶～", "盐的砂糖酱",
    "miku丶菈", "叶苏秋和幻心", "让我视视", "桐", "邪恶洛大人", "林克",
    "魔法少女警长", "欢饮明月", "雀", "伊伊姛志", "砂糖的盐酱", "梦月生日"
]

event_list = [
    '0101', '0129', '0212', '0214', '0308', '0401', '0405', '0410', 
    '0501',  '0504', '0601', '0619', '0701', '0707', '0721', '0801', 
    '0901', '0903', '1001', '0925','1010', '1011', '1101', '1111', 
    '1224', '1225'
]

event_name_list = [
    '元旦节', '春节', '元宵节', '情人节', '妇女节', '愚人节', '清明节', '图书馆纪念日',
    '劳动节', '青年节', '儿童节', '端午节', '建党节', '图书馆纪念日', '0721节', '建军节',
    '开学日', '抗战/反法西斯胜利纪念日', '国庆节', '中秋节', '萌节', '萝莉节','万圣节', '光棍节', 
    '平安夜', '圣诞节'
]

week_list = ["日", "一", "二", "三", "四", "五", "六"]

# 资源路径
_src_path: Optional[str] = None


def set_src_path(path: str):
    """设置资源路径"""
    global _src_path
    _src_path = path


def get_src_path() -> str:
    """获取资源路径"""
    if _src_path is None:
        plugin_dir = Path(__file__).parent.parent.parent
        return str(plugin_dir / "src" / "img" / "icelogin" / "src")
    return _src_path


async def fetch_avatar(url: str) -> bytes:
    """异步获取用户头像"""
    if not url:
        return b''
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                return await r.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return b''


def _hash() -> int:
    """生成随机哈希值"""
    days = random.randint(10000000, 99999999)
    return days >> 8


def check_str_len(string: str) -> int:
    """检查字符串的显示长度"""
    len_txt = len(string)
    len_txt_utf8 = len(string.encode('utf-8'))
    return int((len_txt_utf8 - len_txt) / 2 + len_txt)


def luck_choice(which: int) -> int:
    """获取宜忌索引"""
    if which == 0:
        return random.randint(0, len(goodluck) - 1)
    elif which == 1:
        return random.randint(0, len(badluck) - 1)
    return 0


def feed_back(value: int) -> str:
    """根据人品值返回评语"""
    if value == 0:
        return "QAQ梦灵...梦灵不是故意的..."
    elif value < 20:
        return "运势很差呢,摸摸..."
    elif value < 40:
        return "运势欠佳喔,一定会好起来的！"
    elif value < 60:
        return "运势普普通通,不好也不坏噢~"
    elif value < 80:
        return "运势不错~会有什么好事发生吗?"
    elif value < 90:
        return "运势旺盛！今天是个好日子~"
    elif value <= 99:
        return "好运爆棚！一定有好事发生吧！"
    elif value == 100:
        return "100！！今天说不定能发大财！！"
    else:
        return "999！！是隐藏的999运势！！！"


def save_to_local(img: BuildImage) -> bytes:
    """将图片转为 bytes 返回，由调用方根据适配器类型构建消息段"""
    from io import BytesIO
    buf = BytesIO()
    img.mark_img.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class LoginCardState:
    festival_msg: str
    birthday_msg: str
    extra_msg: str
    total_get_msg: str
    login_flag: int
    rp: int
    info: str
    good_luck_msg: str
    bad_luck_msg: str
    date_msg: str


def _special_day_messages(flag: str, login_flag: int):
    festival_msg = ''
    birthday_msg = ''
    extra_msg = ''
    birth_flag = 0
    event_flag = 0
    if flag in birth_list:
        birth_flag = 1
        birthday_msg = f'{member_list[birth_list.index(flag)]}的生日'
        if not login_flag:
            extra_msg += '☆ 星星+120(生日)\n☆ 金币+5000(生日)\n'
    if flag in event_list:
        event_flag = 1
        festival_msg = event_name_list[event_list.index(flag)]
        if not login_flag:
            extra_msg += '☆ 星星+80(节日)\n☆ 金币+3600(节日)\n'
    return festival_msg, birthday_msg, extra_msg, birth_flag, event_flag


def _lucky_gold_for_rp(rp: int, login_flag: int) -> int:
    if login_flag:
        return 0
    if 90 <= rp <= 99:
        return max(1, min(5, rp - 90))
    if rp == 100:
        return 10
    if rp > 100:
        return 20
    return 0


def _build_login_card_state(uid: int, wallet, current_time) -> LoginCardState:
    days = int(time.strftime("%d", current_time))
    months = int(time.strftime("%m", current_time))
    week = int(time.strftime("%w", current_time))
    login_flag = 1 if int(f'{months}0{days}') == wallet.last_login else 0
    flag = f'{months:02d}{days:02d}'
    (
        festival_msg,
        birthday_msg,
        extra_msg,
        birth_flag,
        event_flag,
    ) = _special_day_messages(flag, login_flag)

    if not login_flag:
        wallet.logindays += 1

    rp_hash = wallet.rp if login_flag else _hash()
    rp = rp_hash % 101
    info = feed_back(rp)
    good_todo_index = wallet.goodluck if login_flag else luck_choice(0)
    bad_todo_index = wallet.badluck if login_flag else luck_choice(1)
    if good_todo_index == bad_todo_index:
        bad_todo_index = (bad_todo_index + 1) % len(badluck)
    good_luck_msg = goodluck[good_todo_index % len(goodluck)]
    bad_luck_msg = badluck[bad_todo_index % len(badluck)]

    lucky_gold = _lucky_gold_for_rp(rp, login_flag)
    lucky_gold_msg = ''
    if lucky_gold:
        extra_msg += f'☆ 幸运币+{lucky_gold} (人品)\n'
        lucky_gold_msg = f'☆ 幸运币+{lucky_gold}\n'
        wallet.luckygold += lucky_gold

    gold = 100 + rp
    logindays = wallet.logindays
    star_add = min(logindays, 100)
    gold_add = min(
        random.randint(50 + logindays // 2 * 5, 100 + logindays * 5),
        2500,
    )
    date_msg = f'{months}月{days}日星期{week_list[week]}   已签到{logindays}天'
    total_get_msg = ''
    if not login_flag:
        star_reward = rp * 5 // 20 + birth_flag * 120 + event_flag * 80 + star_add
        gold += birth_flag * 5000 + event_flag * 3600 + gold_add
        wallet.starstone += star_reward
        wallet.gold += gold
        wallet.last_login = int(f'{months}0{days}')
        wallet.rp = rp_hash
        wallet.goodluck = good_todo_index
        wallet.badluck = bad_todo_index
        total_get_msg = f'☆ 星星+{star_reward}\n☆ 金币+{gold}\n{lucky_gold_msg}'
        if logindays >= 10:
            extra_msg += f'☆ 星星+{star_add} (累签)\n☆ 金币+{gold_add} (累签)\n'

    if uid == 80000000:
        rp = -1
        info = '  Vanitas vanitatum,Et omnia vanitas.'
        good_luck_msg = '宜 取消匿名'
        bad_luck_msg = '忌 匿名'
        date_msg = f'{months}月{days}日星期{week_list[week]}'
        total_get_msg = '△ 无'
        extra_msg = ''
        login_flag = 0

    return LoginCardState(
        festival_msg=festival_msg,
        birthday_msg=birthday_msg,
        extra_msg=extra_msg,
        total_get_msg=total_get_msg,
        login_flag=login_flag,
        rp=rp,
        info=info,
        good_luck_msg=good_luck_msg,
        bad_luck_msg=bad_luck_msg,
        date_msg=date_msg,
    )


def _normal_login_background(srcpath: str, uid: int) -> BuildImage:
    background_list = _get_background_images(srcpath)
    if uid == 80000000:
        background_name = extra_bg_list[0]
    else:
        background_name = (
            random.choice(background_list)
            if background_list
            else extra_bg_list[0]
        )
    image_file = os.path.join(srcpath, 'background', background_name)
    if not os.path.exists(image_file):
        image_file = (
            os.path.join(srcpath, 'background', background_list[0])
            if background_list
            else None
        )
    return BuildImage(
        0,
        0,
        font_size=30,
        background=image_file,
        font=SIGN_FONT,
    )


def _custom_login_background(
    srcpath: str,
    custom_bg_path: str,
) -> BuildImage:
    background = BuildImage(
        0,
        0,
        font_size=30,
        background=os.path.join(srcpath, 'whiteboard.jpg'),
        font=SIGN_FONT,
    )
    try:
        custom_background = BuildImage(
            0,
            0,
            font_size=30,
            background=custom_bg_path,
            font=SIGN_FONT,
        )
        if custom_background.w > custom_background.h * 16 / 9:
            custom_background.resize(ratio=540 / custom_background.h)
        else:
            custom_background.resize(ratio=960 / custom_background.w)
        background.paste(
            custom_background,
            (
                int(480 - custom_background.w / 2),
                int(270 - custom_background.h / 2),
            ),
            True,
        )
    except (OSError, TypeError, ValueError):
        pass
    mask_path = os.path.join(srcpath, 'login_background_custom.png')
    if os.path.exists(mask_path):
        mask = BuildImage(
            0,
            0,
            font_size=30,
            background=mask_path,
            font=SIGN_FONT,
        )
        background.paste(mask, (0, 0), True)
    return background


def _build_login_background(srcpath: str, uid: int):
    custom_bg_path = os.path.join(srcpath, 'customize', f'{uid}.jpg')
    if os.path.exists(custom_bg_path):
        return _custom_login_background(srcpath, custom_bg_path), 2, True
    return _normal_login_background(srcpath, uid), 0, False


def _paste_avatar_source(
    background: BuildImage,
    source,
    size: int,
    position: tuple[int, int],
) -> bool:
    try:
        icon = BuildImage(0, 0, background=source)
        width, _ = icon.size
        icon.resize(ratio=size / width)
        icon.circle()
        background.paste(icon, position, True)
        return True
    except Exception:
        return False


async def _paste_user_avatar(
    background: BuildImage,
    srcpath: str,
    uid: int,
    avatar_url: str,
    size: int,
    position: tuple[int, int],
) -> bool:
    custom_avatar_path = os.path.join(srcpath, 'avatar', f'{uid}.png')
    if os.path.exists(custom_avatar_path) and _paste_avatar_source(
        background,
        custom_avatar_path,
        size,
        position,
    ):
        return False

    if avatar_url:
        profile_image = await fetch_avatar(avatar_url)
        if profile_image and _paste_avatar_source(
            background,
            io.BytesIO(profile_image),
            size,
            position,
        ):
            return False

    default_avatar_path = os.path.join(srcpath, 'default_avatar.jpg')
    if os.path.exists(default_avatar_path):
        return _paste_avatar_source(
            background,
            default_avatar_path,
            size,
            position,
        )
    return False


def _trim_display_name(name: str, max_length: int = 20) -> str:
    if check_str_len(name) < max_length:
        return name
    result = ''
    for character in name:
        result += character
        if check_str_len(result) >= max_length:
            break
    return result


def _draw_login_identity(
    background: BuildImage,
    uid: int,
    username: str,
    qqname: str,
    nick_flag: int,
    state: LoginCardState,
    border: int,
):
    date_text = BuildImage(
        0,
        0,
        plain_text=state.date_msg,
        font_size=30,
        font='nyan.ttf',
        font_color=(77, 83, 149),
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    background.paste(date_text, (23, 473), True)
    uid_text = BuildImage(
        0,
        0,
        plain_text=f'UID：{uid}',
        font_size=18,
        font=BODY_FONT,
        font_color=(77, 83, 149),
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    background.paste(uid_text, (23, 510), True)
    name_text = BuildImage(
        0,
        0,
        plain_text=_trim_display_name(qqname),
        font_size=30,
        font=BODY_FONT,
        font_color=(77, 83, 149),
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    nickname_message = f"欢迎回来，{username}~" if nick_flag else "欢迎回来~"
    if uid == 80000000:
        nickname_message = "......"
    nickname_text = BuildImage(
        0,
        0,
        plain_text=nickname_message,
        font_size=25,
        font=BODY_FONT,
        font_color=(77, 83, 149),
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    background.paste(name_text, (190, 30), True)
    background.paste(nickname_text, (190, 75), True)


def _festival_message(festival_msg: str, birthday_msg: str) -> str:
    if festival_msg and birthday_msg:
        return f'今天是{festival_msg}和{birthday_msg}~'
    if festival_msg:
        return f'今天是{festival_msg}~'
    if birthday_msg:
        return f'今天是{birthday_msg}~'
    return ''


def _rp_color(rp: int):
    if rp < 0:
        return (244, 93, 129)
    if rp == 0:
        return (100, 100, 100)
    if rp <= 100:
        return lab2rgb(86, 80 - rp * 1.6, 4)
    return (68, 118, 244)


def _draw_login_fortune(
    background: BuildImage,
    state: LoginCardState,
    border: int,
):
    festival = _festival_message(state.festival_msg, state.birthday_msg)
    if festival:
        festival_image = BuildImage(
            0,
            0,
            plain_text=festival,
            font_size=30,
            font=SIGN_FONT,
            font_color=(77, 83, 149),
            stroke_width=border,
            stroke_fill=(255, 255, 255),
        )
        width, _ = festival_image.size
        background.paste(festival_image, (int(215 - width / 2), 157), True)

    background.text(
        (49, 259),
        state.good_luck_msg,
        (77, 83, 149),
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    background.text(
        (261, 259),
        state.bad_luck_msg,
        (77, 83, 149),
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    color = _rp_color(state.rp)
    rp_number = BuildImage(
        0,
        0,
        plain_text=str(state.rp),
        font_size=60,
        font='nyan.ttf',
        font_color=color,
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    rp_width, _ = rp_number.size
    background.paste(rp_number, (int(215 - rp_width / 2), 356), True)
    info_image = BuildImage(
        0,
        0,
        plain_text=state.info,
        font_size=25,
        font=SIGN_FONT,
        font_color=color,
        stroke_width=border,
        stroke_fill=(255, 255, 255),
    )
    info_width, _ = info_image.size
    background.paste(info_image, (int(215 - info_width / 2), 424), True)


def _draw_login_rewards(
    background: BuildImage,
    srcpath: str,
    state: LoginCardState,
    has_custom_bg: bool,
):
    if state.login_flag:
        flag_name = (
            'login_flag_custom.png'
            if has_custom_bg
            else 'login_flag.png'
        )
        login_flag_icon_file = os.path.join(srcpath, flag_name)
        if os.path.exists(login_flag_icon_file):
            login_flag_image = BuildImage(
                0,
                0,
                background=login_flag_icon_file,
            )
            background.paste(login_flag_image, (373, 174), True)
        return

    background.text(
        (590, 70),
        state.extra_msg,
        (77, 83, 149),
        stroke_width=2,
        stroke_fill=(255, 255, 255, 1),
    )
    if state.extra_msg:
        bonus_icon_file = os.path.join(srcpath, 'extra_bonus.png')
        if os.path.exists(bonus_icon_file):
            bonus_icon = BuildImage(0, 0, background=bonus_icon_file)
            background.paste(bonus_icon, (551, 7), True)
    total_icon_file = os.path.join(srcpath, 'text_block_new.png')
    if os.path.exists(total_icon_file):
        total_background = BuildImage(0, 0, background=total_icon_file)
        background.paste(total_background, (553, 255), True)
    background.text(
        (618, 338),
        state.total_get_msg,
        (77, 83, 149),
        stroke_width=2,
        stroke_fill=(255, 255, 255, 1),
    )


async def as_login_v3(uid: int, username: str, qqname: str, nick_flag: int, avatar_url: str = ''):
    """
    生成签到卡片

    Args:
        uid: 用户 UID
        username: 用户昵称
        qqname: QQ 昵称
        nick_flag: 是否显示昵称
        avatar_url: 用户头像 URL
    
    Returns:
        Base64 编码的图片 CQ 码
    """
    srcpath = get_src_path()
    state = _build_login_card_state(
        uid,
        money.of(uid),
        time.localtime(time.time()),
    )
    background, border, has_custom_bg = _build_login_background(srcpath, uid)
    default_avatar = await _paste_user_avatar(
        background,
        srcpath,
        uid,
        avatar_url,
        100,
        (23, 23),
    )
    if default_avatar:
        tip_text = BuildImage(
            0,
            0,
            plain_text="当前为默认头像，可发送 上传头像[图片] 修改",
            font_size=11,
            font=BODY_FONT,
            font_color=(77, 83, 149),
            stroke_width=border,
            stroke_fill=(255, 255, 255),
        )
        background.paste(tip_text, (10, 136), True)

    _draw_login_identity(
        background,
        uid,
        username,
        qqname,
        nick_flag,
        state,
        border,
    )
    _draw_login_fortune(background, state, border)
    _draw_login_rewards(background, srcpath, state, has_custom_bg)
    return save_to_local(background)


def get_user_gold_rank_str(current_user_id: int) -> str:
    """获取用户金币排名字符串"""
    all_gold_data = money.all('gold')
    if not all_gold_data:
        return ""
    
    ranked_list = [(uid, gold) for uid, gold in all_gold_data.items()]
    if not ranked_list:
        return ""
    
    ranked_list.sort(key=lambda item: item[1], reverse=True)
    
    user_rank = -1
    total_ranked_users = len(ranked_list)
    for i, (uid, gold) in enumerate(ranked_list):
        if uid == current_user_id:
            user_rank = i + 1
            break
    
    if user_rank == -1:
        return "(未参与排名)"
    
    if user_rank <= 50:
        return f"(位于第{user_rank}名)"
    else:
        percentage = (user_rank / total_ranked_users) * 100
        return f"(位于前{percentage:.0f}%)"


def _star_purse_backgrounds(starstone: int) -> list[str]:
    backgrounds = []
    if starstone > 15000:
        backgrounds.append('purse_04.jpg')
    if 25000 < starstone < 100000 and random.randint(1, 101) >= 50:
        backgrounds.append('purse_10.jpg')
    if starstone >= 100000:
        backgrounds.append('purse_14.jpg')
    if starstone < 4000:
        backgrounds.append('purse_13.jpg')
    return backgrounds


def _gold_purse_backgrounds(gold: int) -> list[str]:
    backgrounds = []
    if gold >= 40000:
        backgrounds.append('purse_22.jpg')
    elif gold >= 30000:
        backgrounds.append('purse_21.jpg')
    elif gold >= 20000:
        backgrounds.append('purse_20.jpg')
    elif gold >= 10000:
        backgrounds.append('purse_15.jpg')
    if 1000 < gold < 10000 and random.randint(1, 101) >= 50:
        backgrounds.append('purse_06.jpg')
    if gold < 75:
        backgrounds.append('purse_07.jpg')
    return backgrounds


def _lucky_purse_backgrounds(lucky_gold: int) -> list[str]:
    backgrounds = []
    if 20 < lucky_gold < 100 and random.randint(1, 101) >= 50:
        backgrounds.append('purse_08.jpg')
    if lucky_gold >= 100:
        backgrounds.append('purse_19.jpg')
    return backgrounds


def _random_purse_background(bonus_point: int) -> str | None:
    if bonus_point > 95:
        return 'purse_16.jpg'
    if bonus_point < 6 or 47 < bonus_point < 53:
        return 'purse_17.jpg'
    return None


def _purse_background_choices(
    starstone: int,
    gold: int,
    lucky_gold: int,
    hour: int,
) -> list[str]:
    choices = PURSE_NORMAL_BACKGROUNDS[:]
    if gold > 300:
        choices.append('purse_03.jpg')
    choices.extend(_star_purse_backgrounds(starstone))
    choices.extend(_gold_purse_backgrounds(gold))
    choices.extend(_lucky_purse_backgrounds(lucky_gold))
    random_background = _random_purse_background(random.randint(1, 101))
    if random_background:
        choices.append(random_background)
    if 1 < hour < 5:
        choices.append('purse_12.jpg')
    return choices


def _purse_display_values(uid: int, wallet):
    if uid == 80000000:
        return (
            'purse_anony.jpg',
            (255, 255, 255),
            '???',
            '???',
            '???',
            '???',
        )
    choices = _purse_background_choices(
        wallet.starstone,
        wallet.gold,
        wallet.luckygold,
        datetime.now().hour,
    )
    return (
        random.choice(choices),
        (116, 88, 86),
        wallet.starstone,
        wallet.gold,
        wallet.luckygold,
        wallet.kirastone,
    )


def _draw_purse_details(
    background: BuildImage,
    uid: int,
    user_name: str,
    font_color,
    starstone,
    gold,
    lucky_gold,
    kirastone,
):
    display_name = user_name[:9] + '...' if len(user_name) >= 10 else user_name
    name_text = BuildImage(
        0,
        0,
        plain_text=f'{display_name}的钱包',
        font_size=35,
        font=BODY_FONT,
        font_color=font_color,
    )
    background.paste(img=name_text, pos=(122, 25), alpha=True)

    star_text = BuildImage(
        0,
        0,
        plain_text=f'星星 {starstone}颗',
        font_size=30,
        font=BODY_FONT,
        font_color=font_color,
    )
    background.paste(img=star_text, pos=(160, 128), alpha=True)
    gold_text = BuildImage(
        0,
        0,
        plain_text=f'金币 {gold}枚',
        font_size=30,
        font=BODY_FONT,
        font_color=font_color,
    )
    background.paste(img=gold_text, pos=(280, 187), alpha=True)

    rank = get_user_gold_rank_str(uid)
    if rank:
        rank_text = BuildImage(
            0,
            0,
            plain_text=rank,
            font_size=13,
            font=BODY_FONT,
            font_color=font_color,
        )
        background.paste(
            img=rank_text,
            pos=(350, 190 + gold_text.h),
            alpha=True,
        )

    lucky_text = BuildImage(
        0,
        0,
        plain_text=f'幸运币 {lucky_gold}枚',
        font_size=30,
        font=BODY_FONT,
        font_color=font_color,
    )
    background.paste(img=lucky_text, pos=(100, 245), alpha=True)
    kirastone_text = BuildImage(
        0,
        0,
        plain_text=f'宝石 {kirastone}颗',
        font_size=30,
        font=BODY_FONT,
        font_color=font_color,
    )
    background.paste(img=kirastone_text, pos=(420, 78), alpha=True)
    uid_text = BuildImage(
        0,
        0,
        plain_text=f'UID：{uid}',
        font_size=16,
        font=BODY_FONT,
        font_color=font_color,
    )
    background.paste(img=uid_text, pos=(20, 290), alpha=True)


async def get_purse(uid: int, user_name: str, avatar_url: str = ''):
    """
    生成钱包图片
    
    Args:
        uid: 用户 UID
        user_name: 用户名
        avatar_url: 用户头像 URL
    
    Returns:
        Base64 编码的图片 CQ 码
    """
    srcpath = get_src_path()
    wallet = money.of(uid)
    (
        background_name,
        font_color,
        starstone,
        gold,
        lucky_gold,
        kirastone,
    ) = _purse_display_values(uid, wallet)
    image_file = os.path.join(srcpath, background_name)
    if not os.path.exists(image_file):
        image_file = os.path.join(srcpath, PURSE_NORMAL_BACKGROUNDS[0])

    background = BuildImage(
        0,
        0,
        font_size=30,
        background=image_file,
        font=BODY_FONT,
    )
    await _paste_user_avatar(
        background,
        srcpath,
        uid,
        avatar_url,
        80,
        (20, 18),
    )
    _draw_purse_details(
        background,
        uid,
        user_name,
        font_color,
        starstone,
        gold,
        lucky_gold,
        kirastone,
    )
    return save_to_local(background)


async def dl_save_image(url: str, uid: int):
    """下载并保存自定义背景图并自动裁剪调整为960x540"""
    from PIL import Image
    import io
    srcpath = get_src_path()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            content = await r.read()
            
            img = Image.open(io.BytesIO(content)).convert("RGB")
            w, h = img.size
            
            # 目标比例 960:540 即 16:9
            target_ratio = 960 / 540
            current_ratio = w / h
            
            if current_ratio > target_ratio:
                # 宽度过宽，居中截取宽度
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                right = left + new_w
                top = 0
                bottom = h
            else:
                # 高度过高，居中截取高度
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                bottom = top + new_h
                left = 0
                right = w
                
            # 居中裁剪
            img = img.crop((left, top, right, bottom))
            
            # 统一缩小/放大为 960x540
            if hasattr(Image, 'Resampling'):
                resample_method = Image.Resampling.LANCZOS
            else:
                resample_method = Image.ANTIALIAS
            img = img.resize((960, 540), resample_method)
            
            save_path = os.path.join(srcpath, f'customize/{uid}.jpg')
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            img.save(save_path, format="JPEG", quality=95)


def del_custom_bg(uid: int):
    """删除自定义背景图"""
    srcpath = get_src_path()
    custom_path = os.path.join(srcpath, f'customize/{uid}.jpg')
    if os.path.exists(custom_path):
        os.remove(custom_path)
