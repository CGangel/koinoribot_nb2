"""
猜单词插件 - English Guess

支持三种游戏模式:
- 猜英语单词 (wordle)
- 猜数字 (digitle)
- 猜日语 (tangole)

迁移自 old_bot/koinoribot/english_guess
"""

import os
import random
import re
import time
from pathlib import Path
from typing import Dict, Optional

from nonebot import on_command, get_driver
from nonebot.adapters import Event, Bot, Message
from nonebot.plugin import PluginMetadata
from nonebot.params import Depends, CommandArg
from nonebot import logger
from .guess_func import get_random_word, get_random_tango, kana_yomi_splt
from .digit_guess_func import get_random_int
from ...build_image import BuildImage
from ...utils import load_data
from ...tools import get_group_id, build_image_msg

__plugin_meta__ = PluginMetadata(
    name="english_guess",
    description="猜单词游戏 - 英语/日语/数字",
    usage="猜单词 / 猜数字 / 猜日语",
)

# ===== 配置和常量 =====
plugin_dir = Path(__file__).parent
font_bold = 'yz.ttf'
font_digit = 'HYWenHei-85W.ttf'
font_color = (109, 113, 112)
color_pink = (247, 141, 167)
color_red = (201, 38, 66)
color_light_gray = (230, 230, 230)
color_blue = (219, 238, 250)
color_green = (76, 175, 80)
color_orange = (236, 138, 100)
gap = 55

# 加载合法单词表
check_list = load_data(plugin_dir / 'data' / 'check_list.json')

# 全角字母映射（字体原因）
cap_up = {'A':'Ａ', 'B':'Ｂ', 'C':'Ｃ', 'D':'Ｄ', 'E':'Ｅ', 'F':'Ｆ', 'G':'Ｇ',
          'H':'Ｈ', 'I':'Ｉ', 'J':'Ｊ', 'K':'Ｋ', 'L':'Ｌ', 'M':'Ｍ', 'N':'Ｎ',
          'O':'Ｏ', 'P':'Ｐ', 'Q':'Ｑ', 'R':'Ｒ', 'S':'Ｓ', 'T':'Ｔ',
          'U':'Ｕ', 'V':'Ｖ', 'W':'Ｗ', 'X':'Ｘ', 'Y':'Ｙ', 'Z':'Ｚ'}

# 游戏次数配置
total_times = [0, 0, 0, 6, 6, 6, 6, 6, 6, 6]
digit_times = [0, 0, 6, 6, 6, 6, 6, 6, 6, 6]
tango_times = [0, 0, 0, 6, 6, 6, 8, 10, 12, 15]
expire_time = [0, 0, 0, 600, 600, 600, 700, 800, 900, 1050]
hint_time = [0, 0, 0, 4, 4, 4, 6, 8, 10, 12]

# 临时文件目录
temp_path = plugin_dir / 'temp'
temp_path.mkdir(exist_ok=True)


# ===== 会话管理 =====
# 替代 old_bot 的 interact 会话系统
game_sessions: Dict[str, Dict] = {}  # key: group_id, value: session_state


def find_session(group_id: str) -> Optional[Dict]:
    """查找群组的游戏会话"""
    session = game_sessions.get(group_id)
    if session and time.time() > session.get('expire_time', 0):
        del game_sessions[group_id]
        return None
    return session


def create_session(group_id: str, expire_seconds: int, **state) -> Dict:
    """创建游戏会话"""
    session = {
        'expire_time': time.time() + expire_seconds,
        'group_id': group_id,
        **state
    }
    game_sessions[group_id] = session
    return session


def close_session(group_id: str):
    """关闭游戏会话"""
    game_sessions.pop(group_id, None)


def is_expired(session: Dict) -> bool:
    """检查会话是否过期"""
    return time.time() > session.get('expire_time', 0)


WORD_LEVELS = {'四级', '六级', '专八', '八级'}


def _valid_word_length(value: str) -> int | None:
    if value.isdigit() and 3 <= int(value) <= 9:
        return int(value)
    return None


def _single_wordle_option(value: str) -> tuple[int, str]:
    word_length = _valid_word_length(value)
    if word_length is not None:
        return word_length, '四级'
    if value in WORD_LEVELS:
        return 5, value
    return 5, '四级'


def _paired_wordle_options(first: str, second: str) -> tuple[int, str]:
    first_length = _valid_word_length(first)
    if first_length is not None:
        level = second if second in WORD_LEVELS else '四级'
        return first_length, level

    second_length = _valid_word_length(second)
    if second_length is not None:
        level = first if first in WORD_LEVELS else '四级'
        return second_length, level
    return 5, '四级'


def _parse_wordle_options(message: str) -> tuple[int, str]:
    parts = message.split()
    if len(parts) >= 3:
        raise ValueError

    if len(parts) == 1:
        return _single_wordle_option(parts[0])
    if len(parts) == 2:
        return _paired_wordle_options(*parts)
    return 5, '四级'


async def _send_expired_result(
    event: Event,
    bot: Bot,
    gid: str,
    message: str,
) -> None:
    close_session(gid)
    pic_path = temp_path / f'{gid}.png'
    if pic_path.exists():
        pic = BuildImage(0, 0, background=str(pic_path))
        image_message = build_image_msg(event, pic.pic2bs4())
        await bot.send(event, image_message + message)
        return
    await bot.send(event, message)


async def _finish_guess_round(
    event: Event,
    bot: Bot,
    gid: str,
    session: Dict,
    picture: BuildImage,
    is_correct: bool,
    success_message: str,
    failure_message: str,
) -> None:
    image_message = build_image_msg(event, picture.pic2bs4())
    if is_correct:
        await bot.send(event, image_message + success_message)
        close_session(gid)
        return

    session['times'] += 1
    if session['times'] == session['total_times']:
        await bot.send(event, image_message + failure_message)
        close_session(gid)
        return

    picture.save(str(temp_path / f'{gid}.png'))
    await bot.send(event, image_message)


# ===== 猜英语单词 =====
def draw_wordle_legend(bg: BuildImage) -> BuildImage:
    """绘制猜单词图例"""
    extra_h = 70
    new_bg = BuildImage(bg.w, bg.h + extra_h, color=(255, 255, 255), font_size=12)
    new_bg.paste(bg, (0, 0))
    
    start_y = bg.h + 8
    left_m = 10
    box_s = 14
    line_s = 24
    
    # 1. Gray
    new_bg.rectangle((left_m, start_y, left_m + box_s, start_y + box_s), fill=color_light_gray)
    new_bg.text((left_m + 30, start_y), "字母正确但位置不对", fill=font_color)
    
    # 2. Blue
    start_y += line_s
    new_bg.rectangle((left_m, start_y, left_m + box_s, start_y + box_s), fill=color_blue)
    new_bg.text((left_m + 30, start_y), "字母正确且位置正确", fill=font_color)
    
    return new_bg


wordle_cmd = on_command("猜单词", priority=5, block=True)

@wordle_cmd.handle()
async def handle_wordle(event: Event, bot: Bot, args: Message = CommandArg(), gid: str = Depends(get_group_id)):
    session = find_session(gid)
    if session:
        await wordle_cmd.finish(f"上一轮猜单词游戏还没结束，现在正在猜{session.get('type', '未知')}喔")
    
    # 解析参数
    msg_text = args.extract_plain_text().strip()
    try:
        word_len, level = _parse_wordle_options(msg_text)
    except ValueError:
        await wordle_cmd.finish("例如：wordle 四级 5")
    
    # 获取随机单词
    rand_word = get_random_word(word_len, level)
    
    # 创建会话
    create_session(
        gid,
        expire_seconds=expire_time[word_len],
        type='英语单词',
        length=word_len,
        word=rand_word['word'],
        pos=rand_word['pos'],
        trans=rand_word['trans'],
        word_low=rand_word['word'].lower(),
        times=0,
        total_times=total_times[word_len],
        guessed_words=[]
    )
    
    # 生成背景图片
    bg_path = plugin_dir / 'data' / 'imgs' / 'en' / f'{word_len}len.png'
    bg = BuildImage(0, 0, background=str(bg_path))
    bg = draw_wordle_legend(bg)
    bg.save(str(temp_path / f'{gid}.png'))
    
    img_msg = build_image_msg(event, bg.pic2bs4())
    await wordle_cmd.finish(img_msg + f"\n请发送【我猜是 对应单词】进行猜测~当前为{level}词库\n限时{expire_time[word_len]}秒，发送【我要提示/我不猜了】可获取提示或退出")



# ===== 猜数字 =====
def draw_digit_legend(bg: BuildImage) -> BuildImage:
    """绘制猜数字图例"""
    extra_h = 110
    # 字体调整为12，避免溢出
    new_bg = BuildImage(bg.w, bg.h + extra_h, color=(255, 255, 255), font_size=12)
    new_bg.paste(bg, (0, 0))
    
    start_y = bg.h + 8
    left_m = 10
    box_s = 14
    line_s = 24
    
    # 1. Orange
    new_bg.rectangle((left_m, start_y, left_m + box_s, start_y + box_s), fill=color_orange)
    new_bg.text((left_m + 30, start_y), "数字正确但位置不对", fill=font_color)
    
    # 2. Blue
    start_y += line_s
    new_bg.rectangle((left_m, start_y, left_m + box_s, start_y + box_s), fill=color_blue)
    new_bg.text((left_m + 30, start_y), "数字正确且位置正确", fill=font_color)
    
    # 3. Green
    start_y += line_s
    new_bg.text((left_m, start_y), "绿色", fill=color_green)
    new_bg.text((left_m + 45, start_y), "整个数比正确答案小", fill=font_color)

    # 4. Red
    start_y += line_s
    new_bg.text((left_m, start_y), "红色", fill=color_red)
    new_bg.text((left_m + 45, start_y), "整个数比正确答案大", fill=font_color)
    
    return new_bg


digitle_cmd = on_command("猜数字", priority=5, block=True)

@digitle_cmd.handle()
async def handle_digitle(event: Event, bot: Bot, args: Message = CommandArg(), gid: str = Depends(get_group_id)):
    session = find_session(gid)
    if session:
        await digitle_cmd.finish(f"上一轮猜单词游戏还没结束，现在正在猜{session.get('type', '未知')}喔")
    
    # 解析参数
    msg_text = args.extract_plain_text().strip()
    length = 3
    if msg_text.isdigit() and 3 <= int(msg_text) <= 9:
        length = int(msg_text)
    
    # 获取随机数字
    rand_int = get_random_int(length)
    
    # 创建会话
    create_session(
        gid,
        expire_seconds=600,
        type='数字',
        answer=int(rand_int),
        length=length,
        times=0,
        total_times=digit_times[length]
    )
    
    # 生成背景图片
    bg_path = plugin_dir / 'data' / 'imgs' / 'dgt' / f'{length}len.png'
    bg = BuildImage(0, 0, background=str(bg_path))
    bg = draw_digit_legend(bg)
    bg.save(str(temp_path / f'{gid}.png'))
    
    img_msg = build_image_msg(event, bg.pic2bs4())
    await digitle_cmd.finish(
        img_msg + "\n请发送【我猜是 对应数字】进行猜测~\n发送【我不猜了】可退出游戏",
    )


# ===== 猜日语 =====
tangole_cmd = on_command("猜日语", priority=5, block=True)

@tangole_cmd.handle()
async def handle_tangole(event: Event, bot: Bot, args: Message = CommandArg(), gid: str = Depends(get_group_id)):
    session = find_session(gid)
    if session:
        await tangole_cmd.finish(f"上一轮猜单词游戏还没结束，现在正在猜{session.get('type', '未知')}喔")
    
    # 解析参数
    msg_text = args.extract_plain_text().strip()
    level = 'all'
    if msg_text and msg_text.lower() in ['n45', 'n3', 'n2', 'n1', 'n4', 'n5']:
        level = msg_text.lower()
    
    trans = {'n45': 'N4N5', 'n3': 'N3', 'n2': 'N2', 'n1': 'N1', 'all': '全体'}
    
    # 选择合适长度的单词
    kana = ''
    yomi = ''
    rand_tango = None
    while True:
        rand_tango = get_random_tango(level)
        kana, yomi = kana_yomi_splt(random.choice(rand_tango['kana']))
        if len(kana) < 3 or len(kana) > 6:
            logger.debug(f'选到了{kana}，长度为{len(kana)}，将重新选择')
            continue
        break
    
    length = len(kana)
    
    # 创建会话
    create_session(
        gid,
        expire_seconds=300,
        type='日语单词',
        jpword=rand_tango['jpword'],
        kana=kana,
        yomi=f'[{yomi}]',
        mean=rand_tango['mean'],
        sample=rand_tango['sample'],
        times=0,
        length=length,
        total_times=4
    )
    
    # 生成背景图片
    bg_path = plugin_dir / 'data' / 'japanese' / f'jp_{length}len.png'
    bg = BuildImage(0, 0, background=str(bg_path))
    bg.save(str(temp_path / f'{gid}.png'))
    
    img_msg = build_image_msg(event, bg.pic2bs4())
    await tangole_cmd.finish(
        img_msg + f"\n请发送【我猜是 对应假名】进行猜测~当前为{trans[level]}日语词库，词义为【{rand_tango['mean']}】\n限时5分钟，发送【我要提示/我不猜了】可获取提示或退出",
    )

# ===== 退出游戏 =====
quit_cmd = on_command("我不猜了", priority=5, block=True)

@quit_cmd.handle()
async def handle_quit(event: Event, bot: Bot, gid: str = Depends(get_group_id)):
    session = find_session(gid)
    if not session:
        #await quit_cmd.finish("当前没有进行中的猜单词游戏喔~")
        return
    
    game_type = session.get('type')
    close_session(gid)
    if game_type == '英语单词':
        word = session['word']
        pos = session['pos']
        trans = session['trans']
        await quit_cmd.finish(f'已退出~\n这个单词是{word}\n{pos}{trans}')
    elif game_type == '数字':
        answer = session['answer']
        await quit_cmd.finish(f'已退出~\n这个数字是{answer}')
    elif game_type == '日语单词':
        kana = session['kana']
        yomi = session['yomi']
        mean = session['mean']
        sample = f"\n{session.get('sample', '')}" if session.get('sample') else ''
        await quit_cmd.finish(f'已退出~\n正确答案是{kana}\n{yomi}{mean}{sample}')
    



# ===== 获取提示 =====
hint_cmd = on_command("我要提示", priority=5, block=True)

@hint_cmd.handle()
async def handle_hint(event: Event, bot: Bot, gid: str = Depends(get_group_id)):
    session = find_session(gid)
    if not session:
        #await hint_cmd.finish("当前没有进行中的猜单词游戏喔~")
        return
    
    game_type = session.get('type')
    
    if game_type == '英语单词':
        trans = session['trans']
        if session['times'] < 4:
            await hint_cmd.finish(f"还需要猜{5 - session['times']}次才能获取提示喔")
        else:
            await hint_cmd.finish(f'这个单词的意思是：{trans}')
    elif game_type == '数字':
        await hint_cmd.finish('猜数字真的有提示的必要嘛?🔍')
    elif game_type == '日语单词':
        mean = session['mean']
        await hint_cmd.finish(f'这个单词的意思是：{mean}')


# ===== 游戏响应处理 =====
guess_handler = on_command("我猜是", priority=5, block=True)

@guess_handler.handle()
async def handle_guess(event: Event, bot: Bot, args: Message = CommandArg(), gid: str = Depends(get_group_id)):
    session = find_session(gid)
    if not session:
        #await guess_handler.finish("当前没有进行中的猜单词游戏喔~")
        return
    
    message = args.extract_plain_text().strip()
    if not message:
        await guess_handler.finish("请在'我猜是'后面加上你的答案喔~")
    
    game_type = session.get('type', '')
    
    if game_type == '英语单词':
        await handle_english_guess(event, bot, gid, session, message)
    elif game_type == '数字':
        await handle_digit_guess(event, bot, gid, session, message)
    elif game_type == '日语单词':
        await handle_japanese_guess(event, bot, gid, session, message)


def _wordle_states(guess: str, target: str) -> list[str | None]:
    remaining = target
    states = []
    for index, alphabet in enumerate(guess):
        if alphabet == remaining[index]:
            remaining_chars = list(remaining)
            remaining_chars[index] = '*'
            remaining = ''.join(remaining_chars)
            states.append('correct')
        elif alphabet in remaining:
            remaining = remaining.replace(alphabet, '*', 1)
            states.append('present')
        else:
            states.append(None)
    return states


def _draw_english_guess(
    gid: str,
    message: str,
    target: str,
    times: int,
) -> tuple[BuildImage, bool]:
    states = _wordle_states(message, target)
    picture = BuildImage(0, 0, background=str(temp_path / f'{gid}.png'))
    for index, alphabet_value in enumerate(message):
        alphabet = BuildImage(
            0,
            0,
            plain_text=cap_up[alphabet_value.upper()],
            font=font_bold,
            font_size=28,
            font_color=font_color,
            is_alpha=True,
        )
        if states[index] == 'correct':
            picture.rectangle(
                (
                    15 + index * gap,
                    15 + times * gap,
                    54 + index * gap,
                    54 + times * gap,
                ),
                fill=color_blue,
            )
        elif states[index] == 'present':
            picture.rectangle(
                (
                    15 + index * gap,
                    15 + times * gap,
                    54 + index * gap,
                    54 + times * gap,
                ),
                fill=color_light_gray,
            )
        picture.paste(
            alphabet,
            (
                35 + index * gap - int(alphabet.w / 2),
                32 + times * gap - int(alphabet.h / 2),
            ),
            alpha=True,
        )
    return picture, all(state == 'correct' for state in states)


def _digit_states(message: str, answer: str) -> list[str | None]:
    states = [None] * len(message)
    remaining = list(answer)
    for index, digit in enumerate(message):
        if digit == remaining[index]:
            states[index] = 'correct'
            remaining[index] = None
    for index, digit in enumerate(message):
        if states[index] is None and digit in remaining:
            states[index] = 'present'
            remaining.remove(digit)
    return states


def _draw_digit_guess(
    gid: str,
    message: str,
    answer: int,
    times: int,
) -> tuple[BuildImage, bool]:
    states = _digit_states(message, str(answer))
    picture = BuildImage(0, 0, background=str(temp_path / f'{gid}.png'))
    numeric_guess = int(message)
    digit_color = (
        color_red
        if numeric_guess > answer
        else color_green if numeric_guess < answer else font_color
    )
    for index, digit_value in enumerate(message):
        digit = BuildImage(
            0,
            0,
            plain_text=digit_value,
            font=font_bold,
            font_size=30,
            font_color=digit_color,
            is_alpha=True,
        )
        if states[index] == 'correct':
            picture.rectangle(
                (
                    15 + index * gap,
                    15 + times * gap,
                    54 + index * gap,
                    54 + times * gap,
                ),
                fill=color_blue,
            )
        elif states[index] == 'present':
            picture.rectangle(
                (
                    15 + index * gap,
                    15 + times * gap,
                    54 + index * gap,
                    54 + times * gap,
                ),
                fill=color_orange,
            )
        picture.paste(
            digit,
            (
                34 + index * gap - int(digit.w / 2),
                32 + times * gap - int(digit.h / 2),
            ),
            alpha=True,
        )
    return picture, all(state == 'correct' for state in states)


def _draw_japanese_guess(
    gid: str,
    message: str,
    kana: str,
    times: int,
) -> tuple[BuildImage, bool]:
    picture = BuildImage(0, 0, background=str(temp_path / f'{gid}.png'))
    for index, kana_value in enumerate(message):
        hiragana = BuildImage(
            0,
            0,
            plain_text=kana_value,
            font=font_bold,
            font_size=28,
            font_color=font_color,
            is_alpha=True,
        )
        if kana_value == kana[index]:
            fill = color_blue
        elif kana_value in kana:
            fill = color_light_gray
        else:
            fill = None
        if fill:
            picture.rectangle(
                (
                    15 + index * gap,
                    15 + times * gap,
                    54 + index * gap,
                    54 + times * gap,
                ),
                fill=fill,
            )
        picture.paste(
            hiragana,
            (
                35 + index * gap - int(hiragana.w / 2),
                33 + times * gap - int(hiragana.h / 2),
            ),
            alpha=True,
        )
    return picture, message == kana


async def handle_english_guess(event: Event, bot: Bot, gid: str, session: Dict, message: str):
    """处理英语单词猜测"""
    # 检查输入是否为纯字母
    if re.fullmatch(r'[A-Za-z]+', message) is None:
        return
    
    word = session['word']
    word_low = session['word_low']
    pos = session['pos']
    trans = session['trans']
    length = session['length']
    times = session['times']
    
    # 检查是否过期
    if is_expired(session):
        await _send_expired_result(
            event,
            bot,
            gid,
            f"时间已过，正确答案是{word}，{pos}{trans}",
        )
        return
    
    message = message.lower()
    
    # 长度检查
    if len(message) != length:
        await bot.send(event, f'要猜的单词长度为{length}喔')
        return
    
    # 特殊命令处理
    if length >= len(check_list) or message not in check_list[length]:
        await bot.send(event, '这个单词不对喔')
        return

    picture, is_correct = _draw_english_guess(
        gid,
        message,
        word_low,
        times,
    )
    await _finish_guess_round(
        event,
        bot,
        gid,
        session,
        picture,
        is_correct,
        f'\n你猜出了这个单词！\n{word}\n{pos}{trans}',
        f'\n次数用完了，没有人猜对...\n{word}\n{pos}{trans}',
    )


async def handle_digit_guess(event: Event, bot: Bot, gid: str, session: Dict, message: str):
    """处理数字猜测"""
    answer = session['answer']
    length = session['length']
    times = session['times']
    
    # 检查是否过期
    if is_expired(session):
        await _send_expired_result(
            event,
            bot,
            gid,
            f'时间已过，正确答案是{answer}',
        )
        return
    
    # 检查输入
    if not message.isdigit():
        return
    
    if len(message) == 1:
        return
    
    if len(message) != length:
        await bot.send(event, f'要猜的数字为{length}位数喔')
        return
    
    picture, is_correct = _draw_digit_guess(
        gid,
        message,
        answer,
        times,
    )
    await _finish_guess_round(
        event,
        bot,
        gid,
        session,
        picture,
        is_correct,
        f'\n你猜出了这个数字！\n它是{answer}',
        f'\n次数用完了，没有人猜对...\n它是{answer}',
    )


async def handle_japanese_guess(event: Event, bot: Bot, gid: str, session: Dict, message: str):
    """处理日语单词猜测"""
    kana = session['kana']
    jpword = session['jpword']
    yomi = session['yomi']
    mean = session['mean']
    sample = f"\n{session['sample']}" if session['sample'] else ''
    length = session['length']
    times = session['times']
    
    # 检查是否过期
    if is_expired(session):
        await _send_expired_result(
            event,
            bot,
            gid,
            f'时间已过，正确答案是{kana}\n{yomi}{mean}\n{sample}',
        )
        return
    
    # 检查输入是否为假名
    rematch = re.findall(r'([\u3040-\u3098]+)', message)
    if len(rematch) != 1:
        return
    
    if len(rematch[0]) != length:
        await bot.send(event, f'要猜的单词为{length}个纯假名喔')
        return
    
    picture, is_correct = _draw_japanese_guess(
        gid,
        message,
        kana,
        times,
    )
    await _finish_guess_round(
        event,
        bot,
        gid,
        session,
        picture,
        is_correct,
        f'\n你拼出了这个单词！\n{jpword}({kana})\n{yomi}{mean}{sample}',
        f'\n次数用完了，没有人答对...\n\n{jpword}({kana})\n{yomi}{mean}{sample}',
    )
