from itertools import count
from pathlib import Path

import aiohttp

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.params import CommandArg, Depends
from nonebot.plugin import PluginMetadata

from .fish_limit import FishLimitManager
from ...su_manager import is_su_contributor
from ...tools import (
    build_forward_chain,
    build_image_msg,
    get_uid,
    send_group_forward_msg,
    text_to_forward_image,
)
from ... import qq_buttons
from ...utils import FreqLimiter
from . import card as fishing_card
from . import aquarium_card
from . import fish_config as C
from .db import FishDB
from .fish_config import (
    EGG_SWEEP_TICKET_ID,
    bait_table,
    calc_sell_price,
    line_table,
    rod_table,
    species_table,
)
from .service import FishService

__plugin_meta__ = PluginMetadata(
    name="fish",
    description="新版钓鱼系统（框架） - 单抽/图鉴/水族箱养成/放生/幸运宝珠/钓鱼商店",
    usage="钓鱼 / 卖鱼 / 放生 / 查看水族箱 / 钓鱼商店 / 图鉴 等",
)

# 单抽钓鱼冷却
cast_freq = FreqLimiter(C.cast_cd())


def _find_by_name(table: dict, name: str):
    """按中文名或 id 在配置表里找条目"""
    if name in table:
        return name, table[name]
    for key, item in table.items():
        if item["name"] == name:
            return key, item
    return None, None


def _strip_quantity(text: str) -> str:
    """忽略手滑输入的数量（独立的纯数字片段）：鱼竿/鱼线/宝珠等限购商品一次只买一个"""
    return " ".join(t for t in text.split() if not t.isdigit()).strip()


def _fmt_len(value: float) -> str:
    """长度展示：1 位小数并去掉多余的 .0（16.0 → 16，23.5 → 23.5）"""
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


async def _load_art_bytes(art) -> bytes | None:
    """加载品种美术资源：http(s) URL 下载或本地路径读取；空值/失败返回 None"""
    if not art or not str(art).strip():
        return None
    source = str(art).strip()
    try:
        if source.lower().startswith(("http://", "https://")):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    source, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return await resp.read()
            return None
        # 相对路径按插件根目录（koinoribot_nb2/）解析，避免依赖运行时 CWD
        path = Path(source)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        if path.is_file():
            return path.read_bytes()
        else:
            logger.warning(f"鱼的美术资源不存在: {path}")
    except Exception as error:
        logger.warning(f"加载鱼的美术资源失败（{source}）: {error}")
    return None


async def _finish_as_forward(
    matcher, event: Event, bot: Bot, text: str, width: int = 600
) -> None:
    """长文本（帮助/商店/图鉴）以伪造合并转发发送，避免刷屏；
    官Bot 平台自动转为图片；失败降级为直接发送。
    width 为转图片时的画布宽度（双列排版需放宽，见钓鱼商店）。"""
    sent = False
    try:
        chain = await build_forward_chain(bot, [text])
        await send_group_forward_msg(event, bot, chain, width=width)
        sent = True
    except Exception as error:
        logger.warning(f"合并转发发送失败，回退纯文本: {error}")
    if sent:
        await matcher.finish()
    await matcher.finish(text)


def _is_qqbot_event(event: Event) -> bool:
    """是否官Bot 平台事件（合并转发会被渲染成图片）"""
    try:
        from nonebot.adapters.qq import Event as QQEvent
    except Exception:
        return False
    return isinstance(event, QQEvent)


def _fish_button_rows(
    release_ok: bool, auto_sell_on: bool, with_actions: bool = True,
    owner_uid: int | None = None, item_id: int | None = None,
    view_ref: int | None = None,
) -> list[list[dict]]:
    """钓鱼卡片按钮行规格（交给 qq_buttons.build_keyboard 构造）。

    第一行：卖鱼/放生（可放生时）/放入水族箱——自动处理（已卖出/已入箱）
    的鱼没有待处理项，整行跳过；data 携带归属上下文
    ``动作:归属uid:鱼实例id``，回调时非归属者或重复点击静默忽略。
    第二行：查看水族箱 + 自动处理开关（按当前状态二选一）——data 同样
    携带 ``动作:归属uid:卡片引用``（一次性令牌，与鱼实例无关），仅
    归属者对每个按钮的首次点击响应，其余静默忽略。
    """
    rows: list[list[dict]] = []
    if owner_uid and view_ref:
        view_scope = f":{owner_uid}:{view_ref}"
    else:
        view_scope = ""

    def scoped(label: str, ref_scope: str) -> dict:
        return {"label": label, "data": f"{label}{ref_scope}"}

    if with_actions:
        scope = f":{owner_uid}:{item_id}" if owner_uid and item_id else ""
        row = [scoped("卖鱼", scope)]
        if release_ok:
            row.append(scoped("放生", scope))
        row.append(scoped("放入水族箱", scope))
        rows.append(row)
    toggle = "关闭自动处理" if auto_sell_on else "开启自动处理"
    rows.append([
        {**scoped("查看水族箱", view_scope), "style": 0},
        {**scoped(toggle, view_scope), "style": 0},
    ])
    return rows


def _fish_keyboard(
    release_ok: bool, auto_sell_on: bool, with_actions: bool = True,
    owner_uid: int | None = None, item_id: int | None = None,
    view_ref: int | None = None,
):
    """官Bot 钓鱼卡片下方的按钮键盘（通用构造）"""
    return qq_buttons.build_keyboard(_fish_button_rows(
        release_ok, auto_sell_on, with_actions, owner_uid, item_id, view_ref
    ))


# 水族箱卡片批量操作按钮（官Bot）：两行排布——第一行只处理已长至最大
# 的鱼，第二行处理全部
_AQUARIUM_BUTTON_ROWS = (
    ("出售大鱼", "放生大鱼"),
    ("全部出售", "全部放生"),
)


def _aquarium_button_rows(owner_uid: int, view_ref: int) -> list[list[dict]]:
    """水族箱卡片按钮行规格：两行批量操作。

    data 携带 ``动作:归属uid:卡片引用``（一次性令牌，与钓鱼卡片第二行
    同机制）——仅归属者对每个按钮的首次点击响应，非归属者或重复点击
    静默忽略。
    """
    scope = f":{owner_uid}:{view_ref}"
    return [
        [{"label": label, "data": f"{label}{scope}"} for label in row]
        for row in _AQUARIUM_BUTTON_ROWS
    ]


def _aquarium_keyboard(owner_uid: int):
    """官Bot 水族箱卡片下方的批量操作按钮键盘"""
    return qq_buttons.build_keyboard(_aquarium_button_rows(
        owner_uid, next(_card_ref_seq)
    ))


async def _send_qqbot_card(
    bot: Bot, event: Event, uid: int, result: dict, image_bytes: bytes
) -> bool:
    """官Bot 发送钓鱼卡片：单条消息（markdown 内嵌卡片图 + 按钮键盘）
    优先；图床不可用（未配置 ip_address）时回退为图片 + 按钮两条消息。

    空军、彩蛋与自动处理（已卖出/已入箱）的卡片没有可处理项，
    第一行动作按钮跳过，只保留 查看水族箱/自动处理开关。
    """
    player = await FishService.ensure_player(uid)
    keyboard = _fish_keyboard(
        release_ok=result.get("lucky_value", 0) >= 1,
        auto_sell_on=bool(player["auto_sell"]),
        with_actions=not (
            result.get("air") or result.get("auto_sold")
            or result.get("auto_tank") or result.get("egg")
        ),
        owner_uid=uid,
        item_id=result.get("item_id"),
        view_ref=next(_card_ref_seq),
    )
    if await qq_buttons.send_image_with_keyboard(
        bot, event, image_bytes, keyboard
    ):
        return True
    # 回退：图片走富媒体消息，按钮跟一条 markdown+键盘消息
    await bot.send(event, build_image_msg(event, image_bytes))
    await qq_buttons.send_keyboard_message(bot, event, " ", keyboard)
    return True


async def _send_cast_card(
    bot: Bot, event: Event, uid: int, result: dict,
    image_bytes: bytes | None,
) -> bool:
    """发送一张钓鱼卡片（不终止本次处理）：官Bot 优先 markdown 内嵌
    卡片图 + 按钮键盘，其余平台纯图片；失败返回 False 由调用方走文本
    兜底。回溯药水的「先空军卡、再重判卡」两张卡片复用此入口。"""
    if not image_bytes:
        return False
    if _is_qqbot_event(event):
        try:
            if await _send_qqbot_card(bot, event, uid, result, image_bytes):
                return True
        except Exception as error:
            logger.warning(f"官Bot发送钓鱼卡片/按钮失败，回退纯图片: {error}")
    try:
        image_msg = build_image_msg(event, image_bytes)
    except Exception as error:
        logger.warning(f"构建钓鱼卡片消息失败，回退文本: {error}")
        return False
    try:
        await bot.send(event, image_msg)
        return True
    except Exception as error:
        logger.warning(f"发送钓鱼卡片失败，回退文本: {error}")
        return False


async def _finish_cast_card(
    cast_cmd, bot: Bot, event: Event, uid: int, result: dict,
    image_bytes: bytes | None,
) -> None:
    """发送钓鱼卡片（鱼类与彩蛋共用）：发送成功即 finish 终止本次处理；
    渲染失败/构建消息失败/发送失败均返回，由调用方走文本兜底。"""
    if await _send_cast_card(bot, event, uid, result, image_bytes):
        await cast_cmd.finish()


# 2~4 级装备的强度档位词（依次小幅/中幅/大幅）
_TIER_WORDS = {2: "小幅", 3: "中幅", 4: "大幅"}


def _tier_word(index: int, base_word: str) -> str:
    """按装备序号给出强度描述；1 级为基准，无加成 → 显示「无」"""
    word = _TIER_WORDS.get(index)
    return f"{word}{base_word}" if word else "无"


def _gear_block(item: dict, kind: str, owned: bool, index: int) -> list[str]:
    """鱼竿/鱼线的详情行（购买货币见商品配置、修理固定金币；初始装备不可购买）"""
    if kind == "rod":
        strength = _tier_word(index, "增加大鱼概率")
    else:
        strength = _tier_word(index, "减少空军概率")
    blocks = [f"{index}.{item['name']}", f"  属性：{strength}"]
    if item["durability"] is None:
        blocks.append("  耐久：无（不会损坏）")
        price = "初始装备"
    else:
        blocks.append(
            f"  耐久：{item['durability']}（修复 {item['repair_price']} 金币）"
        )
        price = ("已购买" if owned
                 else f"{item['price']} {C.currency_name(item.get('currency'))}")
    blocks.append(f"  售价：{price}")
    return blocks


def _bait_block(item: dict, bait_id: str, count: int, using: bool,
                index: int) -> list[str]:
    """鱼饵的详情行（一次性；计价货币见商品配置）"""
    blocks = [f"{index}.{item['name']}", f"  属性：{_tier_word(index, '增加稀有度')}"]
    if item.get("unlimited"):
        price = "初始装备（无限使用）"
    else:
        mark = "（使用中）" if using else ""
        blocks.append(f"  库存：×{count}")
        price = f"{item['price']} {C.currency_name(item.get('currency'))}/个{mark}"
    blocks.append(f"  售价：{price}")
    return blocks


def _orb_block(orb: dict, owned: bool) -> list[str]:
    """幸运宝珠的详情行（限购一次；计价货币可配置）"""
    price = "已购买" if owned else (
        f"{orb['price']} {C.currency_name(orb.get('currency'))}"
    )
    return [
        f"1.{orb['name']}",
        "  属性：幸运暴击（能量满时触发）",
        f"  售价：{price}",
    ]


def _pump_block(pump: dict, owned: bool) -> list[str]:
    """氧气泵的详情行（限购一次，购买后被动生效；计价货币可配置）"""
    price = "已购买" if owned else (
        f"{pump['price']} {C.currency_name(pump.get('currency'))}"
    )
    return [
        f"1.{pump['name']}",
        f"  属性：水族箱成长速率 +{pump['growth_bonus']:g}%",
        f"  售价：{price}",
    ]


# ===== 钓鱼帮助 =====
fish_help_cmd = on_command("钓鱼帮助", priority=5, block=True)

HELP_TEXT = """【钓鱼玩法】
==钓鱼==
  钓鱼 —— 钓一条鱼
  一键钓鱼/十连钓鱼 —— 消耗10次钓鱼次数连钓10杆，钓到的鱼自动处理
  卖鱼 —— 换金币，鱼越大卖得越贵
  放生 —— 获取等同于幸运值的幸运币
  放入水族箱 —— 养大了再卖
  我的背包 —— 查看自己的装备、鱼饵、药水与道具
  换鱼竿/换鱼线/换鱼饵/换药水 <名称> —— 更换为对应的装备或药水
  修理鱼竿/修理鱼线 —— 消耗金币修复装备
  开启自动处理/关闭自动处理 —— 自动卖出养大也不值钱的鱼，其余自动放入水族箱



==图鉴==
  鱼类图鉴 —— 呼出图鉴
  查看图鉴奖励 —— 查看 30 档图鉴奖励与领取状态
  领取图鉴奖励 —— 一次性领取全部达标的图鉴奖励


==水族箱==
  查看水族箱 —— 呼出水族箱面板
  水族箱卖出 <编号> —— 出售对应位置的鱼
  水族箱放生 <编号> —— 放生对应位置的鱼
  出售大鱼 —— 一键卖出箱里已长至最大的鱼
  放生大鱼 —— 批量放生箱里已长至最大的鱼换取幸运币
  全部出售 —— 卖出水族箱中全部的鱼
  全部放生 —— 放生全部可放生的鱼（售价达到1万）换取幸运币
  扩展水族箱 —— 扩展水族箱的槽位


==钓鱼商店==
  买鱼竿/买鱼线/买鱼饵 <名称> —— 购买对应的物品
  买幸运宝珠 —— 购买并装备幸运宝珠
  买氧气泵 —— 水族箱成长速率 +100%
  升级幸运宝珠 —— 消耗幸运币升级宝珠
鱼竿鱼线有耐久，用完要修理（修理固定消耗金币）
鱼饵是消耗品，需要定期补充
非消耗品只能买一次，买后自动装备
幸运宝珠：每钓起一条鱼攒 1 点能量，攒满后下一竿触发幸运暴击


幸运转盘和宠物技能有机会赢回钓鱼次数~"""


@fish_help_cmd.handle()
async def handle_fish_help(bot: Bot, event: Event):
    await _finish_as_forward(fish_help_cmd, event, bot, HELP_TEXT)


# ===== 单抽钓鱼 =====
cast_cmd = on_command("钓鱼", aliases={"🎣"}, priority=5, block=True)

# 幸运暴击的标题前缀（只提示触发，不展示具体加成数值）
LUCKY_CAST_PREFIX = "幸运暴击！"


@cast_cmd.handle()
async def handle_cast(
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    if not cast_freq.check(uid):
        await cast_cmd.finish(
            f"鱼竿还在收线中，休息一会吧~({int(cast_freq.left_time(uid))}s)"
        )

    # 每日钓鱼次数（等级 0 的 SU 不受限制；宠物技能/幸运转盘可加当日上限）
    limited = not is_su_contributor(uid)
    if limited and not FishLimitManager.check_and_update_fish_limit(uid, 1):
        used, cap = FishLimitManager.get_user_fish_count_today(uid)
        await cast_cmd.finish(
            f"今日钓鱼次数已用完（{used}/{cap}），明天再来吧~\n"
            "（幸运转盘、宠物技能有机会获得额外次数）"
        )

    result = await FishService.do_cast(uid)
    if not result["ok"]:
        # 未实际抛竿（待处理鱼获/渔具损坏等），退还刚扣的当日次数
        if limited:
            FishLimitManager.refund_today_count(uid)
        if result["reason"] == "pending":
            await cast_cmd.finish(
                f"你钓到的【{result['species_name']}】还没有处理！"
                "发送 卖鱼 / 放生 / 放入水族箱 处理后再钓鱼~"
            )
        if result["reason"] == "bait":
            bait = bait_table().get(result["bait_id"], {})
            await cast_cmd.finish(
                f"{bait.get('name', '鱼饵')}用完了，发送 买鱼饵 补充或 换鱼饵 切换~"
            )
        if result["reason"] == "rod_broken":
            await cast_cmd.finish(
                f"{result['gear_name']}的耐久耗尽了！发送 修理鱼竿 修复，"
                "或 换鱼竿 <其它鱼竿的名字>"
            )
        if result["reason"] == "line_broken":
            await cast_cmd.finish(
                f"{result['gear_name']}断掉了！发送 修理鱼线 修复，"
                "或 换鱼线 <其它鱼线的名字>"
            )
        await cast_cmd.finish("钓鱼失败了...")

    # 冷却时长每次触发时现读配置（面板热更新即时生效）
    cast_freq.start_cd(uid, C.cast_cd())

    # 彩蛋钓获：同样走卡片（彩蛋底图变体，右栏信息块贴发放结果，
    # 无操作按钮）；单品配了美术则贴图，否则底图补简笔彩蛋
    if result.get("egg"):
        egg = result["egg"]
        badge = (
            "图鉴已解锁！"
            if (result.get("collection") or {}).get("new_unlock") else ""
        )
        # 今日剩余次数（等级 0 的 SU 不限次）
        if limited:
            used, cap = FishLimitManager.get_user_fish_count_today(uid)
            remaining, total = max(0, cap - used), cap
        else:
            remaining, total = None, None
        fields = fishing_card.build_egg_fields(
            result, remaining, total, badge,
            result.get("orb"), result.get("gear"),
        )
        image_bytes = fishing_card.render(
            fields, "彩蛋", await _load_art_bytes(egg.get("art"))
        )
        await _finish_cast_card(cast_cmd, bot, event, uid, result, image_bytes)
        # 卡片渲染/发送失败时的文本兜底
        lines = [
            f"你钓上来一个彩蛋——【{egg['name']}】！",
            "品质: 彩蛋",
        ]
        if badge:
            lines.append(badge)
        lines.append((result.get("grant") or {}).get("text", ""))
        if limited:
            used, cap = FishLimitManager.get_user_fish_count_today(uid)
            lines.append(f"今日剩余次数: {max(0, cap - used)}/{cap}")
        await cast_cmd.finish("\n".join(lines))

    # 今日剩余次数（等级 0 的 SU 不限次；回溯前后同一数值，先算好复用）
    if limited:
        used, cap = FishLimitManager.get_user_fish_count_today(uid)
        remaining, total = max(0, cap - used), cap
    else:
        remaining, total = None, None

    # 回溯药水：空军时先发空军卡片（不终止），再按原有空军概率重判一次，
    # 用重判结果走后续卡片流程（第二张卡片；不额外消耗次数/CD/鱼饵/耐久）
    if result["air"] and result.get("rollback"):
        fields = fishing_card.build_fields(
            result, remaining, total, "", result.get("orb"), result.get("gear")
        )
        image_bytes = fishing_card.render(
            fields, result["rarity"],
            await _load_art_bytes(
                species_table().get(result["species_id"], {}).get("art", "")),
        )
        if not await _send_cast_card(bot, event, uid, result, image_bytes):
            # 第一张（空军）卡片发送失败的文本兜底
            await cast_cmd.send("\n".join([
                f"鱼线被挣断了，{result['species_name']}逃走了。",
                f"长度: {_fmt_len(result['length'])}cm({result['grade']})",
                f"品质: {result['rarity']}",
                "鱼线被挣断，鱼逃走了...",
                "很可惜呢，下次再试试吧",
            ]))
        result = await FishService.rollback_cast(uid, result)

    # 幸运宝珠：幸运暴击前缀行 + 能量行
    orb = result.get("orb")
    lucky_prefix = []
    orb_lines = []
    if orb:
        if orb["lucky_cast"]:
            lucky_prefix.append(LUCKY_CAST_PREFIX)
        if orb["just_full"]:
            orb_lines.append("幸运宝珠能量已满，下次钓鱼将触发幸运暴击！")
        else:
            orb_lines.append(f"幸运宝珠能量: {orb['energy']}/{orb['cap']}")

    art = species_table().get(result["species_id"], {}).get("art", "")

    # 回溯药水的重判结果：显目提示（标题前缀 + 徽标 + 文本兜底行），
    # 让玩家意识到这一杆是回溯药水的功劳
    rewound = bool(result.get("rollback_used"))
    rewound_prefix = ["时空回溯！"] if rewound else []
    rewound_badge = "回溯药水生效！" if rewound else ""

    # ===== 渲染卡片（空军与成功钓获同一套，空军用底图变体与固定文案） =====
    collection = result.get("collection") or {}
    badge, badge2 = "", ""
    if collection.get("new_unlock"):
        badge = "图鉴已解锁！"
    else:
        # 等级与长度可能同时刷新，各占一行分别显示
        records = []
        if collection.get("grade_improved"):
            records.append(
                f"记录已更新：{collection['old_grade']}"
                f"→{collection['new_grade']}！"
            )
        if collection.get("length_improved"):
            records.append(
                f"记录已更新：{_fmt_len(collection['old_length'])}cm"
                f"→{_fmt_len(collection['new_length'])}cm！"
            )
        badge = records[0] if records else ""
        badge2 = records[1] if len(records) > 1 else ""
    if rewound and not badge:
        badge = rewound_badge        # 空军卡不渲染徽标位，由标题前缀兜底

    fields = fishing_card.build_fields(
        result, remaining, total, badge, orb, result.get("gear"), badge2
    )
    # 幸运暴击/时空回溯并入标题（卡片无独立槽位）；按事件顺序排列：
    # 幸运暴击！时空回溯！你钓到了【xx】。
    if rewound:
        fields["title"] = rewound_prefix[0] + fields["title"]
    if lucky_prefix:
        fields["title"] = lucky_prefix[0] + fields["title"]
    image_bytes = fishing_card.render(
        fields, result["rarity"], await _load_art_bytes(art)
    )
    await _finish_cast_card(cast_cmd, bot, event, uid, result, image_bytes)

    # 卡片渲染/发送失败时的文本兜底
    if result["air"]:
        # 空军：无售价/成长栏，结尾同卡片固定文案
        await cast_cmd.finish("\n".join([
            *lucky_prefix,
            *(["回溯药水生效，时空倒流重判了一次——仍被挣脱..."]
              if rewound else []),
            f"鱼线被挣断了，{result['species_name']}逃走了。",
            f"长度: {_fmt_len(result['length'])}cm({result['grade']})",
            f"品质: {result['rarity']}",
            "鱼线被挣断，鱼逃走了...",
            "很可惜呢，下次再试试吧",
        ]))

    fish_lines = [
        f"长度: {_fmt_len(result['length'])}cm({result['grade']})",
        f"售价: {result['sell_price']}金币",
    ]
    if result["lucky_value"] >= 1:
        fish_lines.append(f"幸运值: {result['lucky_value']}")
    growth_line = f"可成长至: {_fmt_len(result['growth_cap'])}cm"
    if result["grown_lucky_value"] >= 1:
        growth_line += (
            f"({result['grown_sell_price']}金币/{result['grown_lucky_value']}幸运值)"
        )
    fish_lines.append(growth_line)
    fish_lines.append(f"品质: {result['rarity']}")
    lines = [
        *lucky_prefix,
        *(["回溯药水生效，时空倒流重判了一次！"] if rewound else []),
        f"你钓到了【{result['species_name']}】。",
        *fish_lines,
    ]
    if badge:
        lines.append(badge)
    lines.extend(orb_lines)
    if result.get("auto_sold"):
        lines.append("已自动售出")
    elif result.get("auto_tank"):
        lines.append("已自动放入水族箱")
    else:
        options = ["卖鱼", "放入水族箱"]
        if result["lucky_value"] >= 1:
            options.insert(1, "放生")
        lines.append("请选择 " + "/".join(options))
    await cast_cmd.finish("\n".join(lines))


# ===== 一键钓鱼（十连）：一次性消耗 10 次钓鱼次数，钓到的鱼强制自动处理 =====
SWEEP_COUNT = 10

sweep_cmd = on_command("一键钓鱼", aliases={"十连钓鱼"}, priority=5, block=True)


async def _sweep_gear_blocker(uid: int, player: dict) -> str:
    """十连前检查鱼竿/鱼线耐久 ≥10（10 杆磨损 10 点；初始渔具无耐久不限）。

    返回拒绝文案；耐久足够（或未装备商店渔具）返回空串。
    """
    for kind, item_id, fallback in (
        ("rod", player.get("rod_item_id"), "鱼竿"),
        ("line", player.get("line_item_id"), "鱼线"),
    ):
        if item_id is None:
            continue    # 初始渔具无实例、无耐久上限
        instance = await FishDB.get_gear(item_id, uid)
        if instance is None:
            continue    # 实例丢失：do_cast 会回落初始渔具
        if instance["durability"] < SWEEP_COUNT:
            table = rod_table() if kind == "rod" else line_table()
            name = table.get(instance["gear_id"], {}).get("name", fallback)
            return (
                f"{name}耐久不足（{instance['durability']}/{SWEEP_COUNT}）——"
                "十连会磨损10点耐久，先修理再来~"
            )
    return ""


@sweep_cmd.handle()
async def handle_sweep(
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    from . import sweep_card

    if not cast_freq.check(uid):
        await sweep_cmd.finish(
            f"鱼竿还在收线中，休息一会吧~({int(cast_freq.left_time(uid))}s)"
        )
    # 扫荡劵：十连的入场券（钓到彩蛋「钓鱼扫荡劵」获得）
    if await FishDB.get_prop_count(uid, EGG_SWEEP_TICKET_ID) < 1:
        await sweep_cmd.finish(
            "没有钓鱼扫荡劵~钓鱼时有几率从彩蛋钓到（发送 我的背包 查看）"
        )
    # 待处理鱼获会卡住后续杆（一键不经过待处理区，先清掉）
    pending = await FishDB.get_pending_item(uid)
    if pending is not None:
        await sweep_cmd.finish(
            "还有一条鱼没处理！发送 卖鱼 / 放生 / 放入水族箱 处理后再来~"
        )
    # 水族箱空余栏位 ≥10（最坏情况 10 条鱼全部入箱）
    player = await FishService.ensure_player(uid)
    free_slots = player["slots"] - len(
        await FishDB.list_fish_items(uid, place="aquarium"))
    if free_slots < SWEEP_COUNT:
        await sweep_cmd.finish(
            f"水族箱空余栏位不足（{free_slots}/{SWEEP_COUNT}）——"
            "十连的鱼最多全部入箱，先清理或扩展水族箱再来~"
        )
    # 鱼竿/鱼线耐久 ≥10（10 杆磨损 10 点；初始渔具无耐久不限）
    gear_block = await _sweep_gear_blocker(uid, player)
    if gear_block:
        await sweep_cmd.finish(gear_block)
    # 每日钓鱼次数 ≥10（等级 0 的 SU 不限次）
    limited = not is_su_contributor(uid)
    if limited:
        used, cap = FishLimitManager.get_user_fish_count_today(uid)
        if cap - used < SWEEP_COUNT:
            await sweep_cmd.finish(
                f"今日钓鱼次数不足（剩余 {max(0, cap - used)}/{SWEEP_COUNT}），"
                "明天再来吧~"
            )

    # 前置条件齐备：消耗一张扫荡劵
    await FishDB.consume_prop(uid, EGG_SWEEP_TICKET_ID, 1)

    results: list[dict] = []
    stopped = ""
    for _ in range(SWEEP_COUNT):
        if limited and not FishLimitManager.check_and_update_fish_limit(uid, 1):
            stopped = "今日钓鱼次数用完，本次十连中断"
            break
        result = await FishService.do_cast(uid, force_auto=True)
        if not result["ok"]:
            if limited:
                FishLimitManager.refund_today_count(uid)   # 未实际抛竿，退还
            reason = result.get("reason")
            if reason == "bait":
                bait = bait_table().get(result.get("bait_id"), {})
                stopped = f"{bait.get('name', '鱼饵')}用完了，本次十连中断"
            elif reason == "rod_broken":
                stopped = f"{result['gear_name']}耐久耗尽，本次十连中断"
            elif reason == "line_broken":
                stopped = f"{result['gear_name']}断掉了，本次十连中断"
            else:
                stopped = "钓鱼失败，本次十连中断"
            break
        if result.get("rollback"):
            # 回溯药水：不发空军卡，直接消耗重判，列表只保留重判结果
            result = await FishService.rollback_cast(uid, result)
        results.append(result)

    if results:
        cast_freq.start_cd(uid, C.cast_cd())

    lines = [sweep_card.build_line(result) for result in results]
    sold = [r for r in results if r.get("auto_sold")]
    tanked = [r for r in results if r.get("auto_tank")]
    airs = [r for r in results if r.get("air")]
    eggs = [r for r in results if r.get("egg")]
    summary = "·".join(part for part in (
        f"售出 {len(sold)} 条（{sum(r['sell_price'] for r in sold)}金币）"
        if sold else "",
        f"入箱 {len(tanked)} 条" if tanked else "",
        f"空军 {len(airs)} 次" if airs else "",
        f"彩蛋 {len(eggs)} 个" if eggs else "",
    ) if part) or "空手而归..."
    footer = [summary]
    if limited:
        used, cap = FishLimitManager.get_user_fish_count_today(uid)
        footer.append(f"今日剩余次数: {max(0, cap - used)}/{cap}")
    if stopped:
        footer.append(stopped)

    image_bytes = sweep_card.render(lines, footer, SWEEP_COUNT)
    if image_bytes is not None:
        try:
            image_msg = build_image_msg(event, image_bytes)
        except Exception as error:
            logger.warning(f"构建十连卡片消息失败，回退文本: {error}")
            image_msg = None
        if image_msg is not None:
            await sweep_cmd.finish(image_msg)
    await sweep_cmd.finish(
        "\n".join([*[line["text"] for line in lines], *footer]))


# ===== 自动处理（旧称自动出售，指令别名保留兼容）=====
AUTO_ON_TEXT = (
    "已开启自动处理：成长到最大后售价仍低于{threshold}金币的鱼自动卖出，"
    "其余自动放入水族箱（水族箱满时交由你处理）~"
)
AUTO_OFF_TEXT = "已关闭自动处理，钓到的鱼会进入待处理区~"

auto_sell_on_cmd = on_command(
    "开启自动处理", aliases={"开启自动出售"}, priority=5, block=True
)


@auto_sell_on_cmd.handle()
async def handle_auto_sell_on(uid: int = Depends(get_uid)) -> None:
    result = await FishService.set_auto_sell(uid, True)
    await auto_sell_on_cmd.finish(AUTO_ON_TEXT.format(threshold=result["threshold"]))


auto_sell_off_cmd = on_command(
    "关闭自动处理", aliases={"关闭自动出售"}, priority=5, block=True
)


@auto_sell_off_cmd.handle()
async def handle_auto_sell_off(uid: int = Depends(get_uid)) -> None:
    await FishService.set_auto_sell(uid, False)
    await auto_sell_off_cmd.finish(AUTO_OFF_TEXT)


# ===== 官Bot 按钮回调（interaction，经 qq_buttons 通用分发）=====
_FISH_BUTTON_ACTIONS = {
    "卖鱼", "放生", "放入水族箱", "查看水族箱",
    "开启自动处理", "关闭自动处理",
    # 水族箱卡片批量操作按钮
    "出售大鱼", "放生大鱼", "全部放生", "全部出售",
}


# ===== 官Bot 钓鱼卡片按钮（interaction 回调） =====

# 一次性令牌动作（不消费待处理鱼获）。「只响应一次」由内存令牌表实现：
# 动作:归属uid:卡片引用 首次点击生效后即失效——钓鱼卡片第二行（查看
# 水族箱/自动处理开关）与水族箱卡片批量操作按钮（出售/放生大鱼、全部
# 出售/放生）共用该机制，非归属者点击与重复点击均静默忽略。
_ONE_SHOT_BUTTON_ACTIONS = {
    "查看水族箱",
    "开启自动处理", "关闭自动处理",
    "出售大鱼", "放生大鱼", "全部放生", "全部出售",
}

# 卡片引用号（自动处理的卡片没有 pending item id，用进程内自增号）
_card_ref_seq = count(1)

# 已消费的一次性令牌（有序 dict 便于超限淘汰最早条目；正常流量远达不到）
_one_shot_used: dict[str, None] = {}
_ONE_SHOT_LIMIT = 1024


def _consume_one_shot(action: str, owner_uid: int, ref: int) -> bool:
    """消费一张卡片一个按钮的一次性令牌；重复消费返回 False（静默忽略）"""
    key = f"{action}:{owner_uid}:{ref}"
    if key in _one_shot_used:
        return False
    _one_shot_used[key] = None
    if len(_one_shot_used) > _ONE_SHOT_LIMIT:
        for stale in list(_one_shot_used)[: _ONE_SHOT_LIMIT // 2]:
            _one_shot_used.pop(stale)
    return True


async def _list_button_reply(text: str):
    """长列表按钮回复：渲染为伪造转发同款长图（与指令路径观感一致），
    渲染失败降级纯文本。"""
    try:
        image = await text_to_forward_image(text, width=600)
        if image:
            return qq_buttons.ImageReply(image)
    except Exception as error:
        logger.warning(f"按钮长图渲染失败，回退纯文本: {error}")
    return text


async def _run_fish_button(
    uid: int, action: str, scope: tuple[int, int] | None = None
):
    """执行钓鱼/水族箱卡片按钮动作，返回回复（短文本或长图 ImageReply）。

    scope = (归属uid, 鱼实例id或卡片引用) 来自卡片按钮 data：
    第一行（卖鱼/放生/放入水族箱）按待处理鱼校验——非归属者点击、或该鱼
    已被处理（重复点击）时静默忽略（返回空）；其余按钮（查看水族箱/自动
    处理开关/水族箱批量操作）按一次性令牌校验——仅归属者对每个按钮的
    首次点击响应，重复点击或旧卡片（无归属上下文）静默忽略。
    """
    if action in _ONE_SHOT_BUTTON_ACTIONS:
        if scope is None:
            return ""
        owner_uid, ref = scope
        if uid != owner_uid or not _consume_one_shot(action, owner_uid, ref):
            return ""
    elif scope is not None:
        owner_uid, item_id = scope
        if uid != owner_uid:
            return ""
        pending = await FishDB.get_pending_item(uid)
        if pending is None or pending["id"] != item_id:
            return ""

    if action == "卖鱼":
        result = await FishService.sell_pending(uid)
        if not result["ok"]:
            if result["reason"] == "no_pending":
                return "现在没有待处理的鱼获"
            return "卖出失败..."
        return (
            f"卖出了{result['grade']}级{result['species_name']}"
            f"({result['length']}cm)\n获得 {result['price']} 金币~"
        )
    if action == "放生":
        result = await FishService.release_pending(uid)
        if not result["ok"]:
            if result["reason"] == "no_pending":
                return "现在没有待处理的鱼获"
            if result["reason"] == "no_lucky":
                return (
                    f"{result['species_name']}售价不足1万（{result['price']}金币），"
                    "无法通过放生获得幸运币，请选择 卖鱼 或 放入水族箱"
                )
            return "放生失败..."
        return (
            f"放生了 {result['grade']}级{result['species_name']}"
            f"({result['length']}cm)，获得 {result['lucky_value']} 幸运币~"
        )
    if action == "放入水族箱":
        result = await FishService.put_in_aquarium(uid)
        if not result["ok"]:
            if result["reason"] == "no_pending":
                return "现在没有待处理的鱼获"
            if result["reason"] == "full":
                return "水族箱槽位满了，可发送 扩展水族箱 增加槽位"
            return "放入失败..."
        return (
            f"{result['species_name']}搬进了水族箱(槽位 #{result['slot']})~"
            "它会在箱里慢慢长大"
        )
    if action == "开启自动处理":
        result = await FishService.set_auto_sell(uid, True)
        return AUTO_ON_TEXT.format(threshold=result["threshold"])
    if action == "关闭自动处理":
        await FishService.set_auto_sell(uid, False)
        return AUTO_OFF_TEXT
    if action == "查看水族箱":
        image_bytes = await _aquarium_image(uid)
        if image_bytes:
            # 呼出的新卡片同样携带批量操作按钮（新卡片引用 = 新令牌组）
            return qq_buttons.ImageReply(
                image_bytes, keyboard=_aquarium_keyboard(uid)
            )
        return await _list_button_reply(await _aquarium_text(uid))
    if action == "出售大鱼":
        return _sell_grown_text(
            await FishService.sell_aquarium_batch(uid, only_grown=True)
        )
    if action == "放生大鱼":
        return _release_grown_text(
            await FishService.release_aquarium_all(uid, only_grown=True)
        )
    if action == "全部出售":
        return _sell_all_text(await FishService.sell_aquarium_batch(uid))
    if action == "全部放生":
        return _release_all_text(await FishService.release_aquarium_all(uid))
    return ""


def _parse_button_scope(event) -> tuple[int, int] | None:
    """从按钮 data 解析归属上下文 ``动作:归属uid:鱼实例id/卡片引用`` 后半段"""
    try:
        raw = event.data.resolved.button_data or ""
        parts = raw.split(":", 1)
        if len(parts) != 2 or not parts[1]:
            return None
        owner, _, item = parts[1].partition(":")
        if owner.isdigit() and item.isdigit():
            return int(owner), int(item)
    except Exception:
        pass
    return None


def _register_fish_button_interactions() -> None:
    """把钓鱼卡片按钮的回调注册到通用分发器（qq_buttons 统一应答与回复）。

    所有按钮均按 data 冒号前缀注册（携带归属上下文），回调时统一从
    event.data.resolved.button_data 解析 ``动作:归属uid:引用`` 做归属
    与一次性校验，非法点击静默忽略。
    """
    for action in sorted(_FISH_BUTTON_ACTIONS):
        async def _handler(uid: int, event, _action: str = action):
            return await _run_fish_button(
                uid, _action, _parse_button_scope(event)
            )

        qq_buttons.register_interaction(action, _handler)


_register_fish_button_interactions()


# ===== 钓获处理：卖鱼 / 放生 / 放入水族箱 =====
sell_cmd = on_command("卖鱼", aliases={"出售鱼获"}, priority=5, block=True)


@sell_cmd.handle()
async def handle_sell(uid: int = Depends(get_uid)) -> None:
    result = await FishService.sell_pending(uid)
    if not result["ok"]:
        if result["reason"] == "no_pending":
            await sell_cmd.finish(
                "现在没有待处理的鱼获"
            )
        await sell_cmd.finish("卖出失败...")

    await sell_cmd.finish(
        f"卖出了{result['grade']}级{result['species_name']}"
        f"({result['length']}cm)\n获得 {result['price']} 金币~"
    )


release_cmd = on_command("放生", priority=5, block=True)


@release_cmd.handle()
async def handle_release(uid: int = Depends(get_uid)) -> None:
    result = await FishService.release_pending(uid)
    if not result["ok"]:
        if result["reason"] == "no_pending":
            await release_cmd.finish(
                "现在没有待处理的鱼获"
            )
        if result["reason"] == "no_lucky":
            await release_cmd.finish(
                f"{result['species_name']}售价不足1万（{result['price']}金币），"
                "无法通过放生获得幸运币，请选择 卖鱼 或 放入水族箱"
            )
        await release_cmd.finish("放生失败...")

    await release_cmd.finish(
        f"放生了 {result['grade']}级{result['species_name']}"
        f"({result['length']}cm)，获得 {result['lucky_value']} 幸运币~"
    )


put_in_cmd = on_command("放入水族箱", aliases={"入箱"}, priority=5, block=True)


@put_in_cmd.handle()
async def handle_put_in(uid: int = Depends(get_uid)) -> None:
    result = await FishService.put_in_aquarium(uid)
    if not result["ok"]:
        if result["reason"] == "no_pending":
            await put_in_cmd.finish(
                "现在没有待处理的鱼获"
            )
        if result["reason"] == "full":
            await put_in_cmd.finish(
                "水族箱槽位满了，可发送 扩展水族箱 增加槽位"
            )
        await put_in_cmd.finish("放入失败...")

    await put_in_cmd.finish(
        f"{result['species_name']}搬进了水族箱(槽位 #{result['slot']})~"
        "它会在箱里慢慢长大"
    )


# ===== 图鉴 =====
collection_cmd = on_command(
    "图鉴", aliases={"鱼类图鉴", "钓鱼图鉴", "鱼图鉴", "查看图鉴"}, priority=5, block=True
)

# 图鉴中解锁前隐藏名称的稀有档位
HIDDEN_RARITIES = ("传说", "神话")


def build_collection_text(
    species: dict, rows: list, two_column: bool = False, tail: str = "",
    egg_items: dict | None = None,
) -> str:
    """图鉴全文：===稀有度=== 分组，组间空行。

    每行「鱼名 级别 长度cm」（图鉴口径的历史最大）或「鱼名 未钓到」；
    传说/神话档的鱼在解锁前不显示名称（??? 未钓到）。
    species 需已按稀有度排序（species_table() 的输出即如此）。

    egg_items（彩蛋单品目录，egg_table() 的输出）非空时追加 ===彩蛋===
    分组，行「彩蛋名 已钓到 / ??? 未钓到」——彩蛋只记是否钓到（无次数/
    级别/长度口径），未钓到时与传说/神话同样隐藏名称。

    tail 为末尾附加文本（如图鉴奖励领取提醒）；two_column=True 时排进
    右列末尾（官Bot 转图片时提醒贴近右栏底部，不占左栏图鉴内容）。

    two_column=True 时排成左右两列（官Bot 转图片时图片更矮）：
    左列 普通/稀有/史诗，右列 传说/神话/彩蛋；行内以制表符分列，由官Bot
    图片渲染器把右列固定画在画布中线，实现像素级对齐。
    """
    stats = {row["species_id"]: row for row in rows}
    sections: dict[str, list[str]] = {}
    current_rarity = None
    for sid, sp in species.items():
        if sp["rarity"] != current_rarity:
            current_rarity = sp["rarity"]
            sections.setdefault(current_rarity, []).append(
                f"==={current_rarity}==="
            )
        row = stats.get(sid)
        if row is None:
            name = "???" if current_rarity in HIDDEN_RARITIES else sp["name"]
            sections[current_rarity].append(f"{name} 未钓到")
        else:
            sections[current_rarity].append(
                f"{sp['name']} {row['max_grade']}级 {_fmt_len(row['max_length'])}cm"
            )

    if egg_items:
        egg_lines = ["===彩蛋==="]
        for egg_id, egg in egg_items.items():
            if egg_id in stats:
                egg_lines.append(f"{egg['name']} 已钓到")
            else:
                egg_lines.append("??? 未钓到")
        sections["彩蛋"] = egg_lines

    tail_lines = [line for line in tail.split("\n") if line.strip()] if tail else []
    if not two_column:
        text = "\n\n".join("\n".join(s) for s in sections.values())
        if tail_lines:
            text += "\n\n" + "\n".join(tail_lines)
        return text

    left = (
        sections.get("普通", []) + [""]
        + sections.get("稀有", []) + [""]
        + sections.get("史诗", [])
    )
    # 彩蛋排在神话下方（右列末段），领取提醒再缀于其后
    right = sections.get("传说", []) + [""] + sections.get("神话", [])
    if "彩蛋" in sections:
        right = right + [""] + sections["彩蛋"]
    if tail_lines:
        right = right + [""] + tail_lines
    # 左右列以制表符分隔：官Bot 图片渲染器把右列固定画在画布中线，
    # 像素级对齐（渲染字体非严格等宽，空格补位无法精确对位）
    return "\n".join(
        (left[i] if i < len(left) else "") + "\t"
        + (right[i] if i < len(right) else "")
        for i in range(max(len(left), len(right)))
    )


@collection_cmd.handle()
async def handle_collection(
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    rows = await FishDB.get_collection(uid)
    # 有可领取的图鉴奖励时在末尾提醒（官Bot 双列模式下排进右列末尾）
    tail = ""
    claimable = await FishService.claimable_reward_count(uid)
    if claimable:
        tail = (
            f"你有 {claimable} 档图鉴奖励可领取！\n"
            "发送 查看图鉴奖励 查看\n"
            "发送 领取图鉴奖励 一次性领取"
        )
    text = build_collection_text(
        species_table(), rows, two_column=_is_qqbot_event(event), tail=tail,
        egg_items=C.egg_table(),
    )
    await _finish_as_forward(collection_cmd, event, bot, text)


# ===== 图鉴奖励 =====
# 每个稀有度 6 档（全收集且全部级别 ≥ D/C/B/A/S/X），共 30 档；
# 奖励目录可在配置面板热更新（fish_collection_rewards）。
reward_view_cmd = on_command(
    "查看图鉴奖励", aliases={"图鉴奖励"}, priority=5, block=True
)

claim_reward_cmd = on_command(
    "领取图鉴奖励", aliases={"领取图鉴奖励"}, priority=5, block=True
)


def build_reward_list_text(
    states: dict, collected: dict, two_column: bool = False
) -> str:
    """图鉴奖励列表：===稀有度=== 分组，档位行「D：奖励内容（状态）」。

    状态标注：可领取 / 已领取（未达成不标注）；传说/神话在对应稀有度
    未全收集（未达 D 档）时奖励内容显示 ???。
    two_column=True 时左右两列（左 普通/稀有/史诗，右 传说/神话），
    制表符分列交由官Bot 图片渲染器对齐（同图鉴）。
    """
    rewards = C.collection_rewards()
    sections: dict[str, list[str]] = {}
    for rarity in C.RARITY_ORDER:
        lines = [f"==={rarity}==="]
        hide = rarity in HIDDEN_RARITIES and not collected.get(rarity)
        for grade in C.GRADE_ORDER:
            if hide:
                content = "???"
            else:
                items = rewards.get(f"{rarity}:{grade}", [])
                content = "、".join(
                    t for t in (C.reward_item_display(i) for i in items) if t
                ) or "无"
            state = states[rarity][grade]
            mark = {"claimable": "（可领取）",
                    "claimed": "（已领取）"}.get(state, "")
            lines.append(f"{grade}：{content}{mark}")
        sections[rarity] = lines

    if not two_column:
        return "\n\n".join("\n".join(lines) for lines in sections.values())

    left = (
        sections["普通"] + [""] + sections["稀有"] + [""] + sections["史诗"]
    )
    right = sections["传说"] + [""] + sections["神话"]
    return "\n".join(
        (left[i] if i < len(left) else "") + "\t"
        + (right[i] if i < len(right) else "")
        for i in range(max(len(left), len(right)))
    )


@reward_view_cmd.handle()
async def handle_reward_view(
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    info = await FishService.collection_reward_states(uid)
    text = build_reward_list_text(
        info["states"], info["collected"], two_column=_is_qqbot_event(event)
    )
    text = (
        "每个稀有度全收集且全部级别达标即解锁对应档位奖励：\n" + text
        + "\n\n发送 领取图鉴奖励 一次性领取全部可领取档位"
    )
    await _finish_as_forward(reward_view_cmd, event, bot, text, width=1000)


@claim_reward_cmd.handle()
async def handle_claim_reward(uid: int = Depends(get_uid)) -> None:
    result = await FishService.claim_collection_rewards(uid)
    if result["count"] == 0:
        await claim_reward_cmd.finish(
            "当前没有可领取的图鉴奖励~\n"
            "发送 查看图鉴奖励 查看各档位的解锁条件"
        )
    lines = [f"领取了 {result['count']} 档图鉴奖励："]
    for item in result["results"]:
        lines.append(f"【{item['rarity']}·{item['grade']}档】{item['text']}")
    await claim_reward_cmd.finish("\n".join(lines))


# ===== 水族箱 =====
aquarium_cmd = on_command(
    "查看水族箱", aliases={"水族箱", "我的水族箱"}, priority=5, block=True
)


def _fmt_slot_line(slot: int, item: dict) -> str:
    """水族箱单条：第一行为当前长度/售价/幸运值，
    第二行为成长上限三件套（已长满则只显示已长至最大）"""
    species = species_table().get(item["species_id"], {})
    name = species.get("name", item["species_id"])
    price = calc_sell_price(item["species_id"], item["length"])
    lucky = C.lucky_value_of_price(price)
    head = (
        f"#{slot} {item['grade']}级{name}({species.get('rarity', '')}) "
        f"{_fmt_len(item['length'])}cm 售价{price}金币 幸运值{lucky}"
    )
    cap = C.growth_cap(item["caught_length"])
    if C.is_fully_grown(item["length"], item["caught_length"]):
        return f"{head}\n已长至最大"
    max_price = calc_sell_price(item["species_id"], cap)
    max_lucky = C.lucky_value_of_price(max_price)
    return (
        f"{head}\n最高可长至: {_fmt_len(cap)}cm  最高售价: {max_price}金币  "
        f"最高幸运值: {max_lucky}"
    )


async def _aquarium_text(uid: int) -> str:
    """水族箱面板文本（指令与官Bot按钮共用）"""
    player = await FishService.ensure_player(uid)
    items = await FishService.list_aquarium(uid)

    lines = [f"你的水族箱（{len(items)}/{player['slots']} 槽位）：\n"]
    if not items:
        lines.append("空空如也，钓到鱼后选择 放入水族箱 就能开始养成~")
    else:
        for slot, item in enumerate(items, start=1):
            lines.append(_fmt_slot_line(slot, item))
        lines.append(
            "\n鱼在箱里会自己慢慢长大，无需照料"
            "\n发送 水族箱卖出/水族箱放生 <槽位编号> 可出售/放生对应的鱼"
            "\n发送 出售大鱼/放生大鱼/全部出售/全部放生 可批量处理在箱的鱼"
            "\n放生将获得等同于幸运值的幸运币"
        )
        if player["pump_owned"]:
            lines.append(
                f"\n氧气泵运转中：成长速率 +{C.pump_info()['growth_bonus']:g}%"
            )
    if player["slots"] < C.aquarium()["max_slots"]:
        next_price = C.slot_price(player["slots"] + 1)
        lines.append(
            f"\n发送 扩展水族箱 增加 1 个槽位\n下一次扩展需要 {next_price}金币"
            f"\n上限{C.aquarium()['max_slots']}个槽位"
        )
    return "\n".join(lines)


async def _aquarium_image(uid: int) -> bytes | None:
    """渲染水族箱卡片（4 列 × 5 行网格）；失败返回 None（调用方回退文本）"""
    player = await FishService.ensure_player(uid)
    items = await FishService.list_aquarium(uid)
    cfg = C.aquarium()

    rows: list[dict] = []
    growth_per_day = (
        cfg["daily_growth_cm"]
        * C.growth_rate_multiplier(player["pump_owned"])
    )
    for item in items:
        species = species_table().get(item["species_id"], {})
        cap = C.growth_cap(item["caught_length"])
        price = calc_sell_price(item["species_id"], item["length"])
        max_price = calc_sell_price(item["species_id"], cap)
        rows.append(aquarium_card.build_row(
            len(rows) + 1,
            name=species.get("name", item["species_id"]),
            rarity=species.get("rarity", ""),
            grade=item["grade"],
            length=item["length"],
            cap=cap,
            price=price,
            max_price=max_price,
            lucky=C.lucky_value_of_price(price),
            max_lucky=C.lucky_value_of_price(max_price),
            growth_per_day=growth_per_day,
        ))

    if rows:
        hints = [
            "发送 水族箱卖出/水族箱放生 <槽位编号> 出售/放生单条",
            "发送 出售大鱼/放生大鱼/全部出售/全部放生 批量处理在箱的鱼",
        ]
    else:
        hints = ["空空如也，钓到鱼后选择 放入水族箱 就能开始养成~"]
    next_price = (
        C.slot_price(player["slots"] + 1)
        if player["slots"] < cfg["max_slots"] else None
    )
    try:
        return aquarium_card.render(
            rows,
            used=len(rows),
            slots=player["slots"],
            max_slots=cfg["max_slots"],
            pump_bonus=(
                C.pump_info()["growth_bonus"] if player["pump_owned"] else 0.0
            ),
            next_price=next_price,
            hints=hints,
        )
    except Exception as error:
        logger.warning(f"渲染水族箱卡片失败: {error}")
        return None


@aquarium_cmd.handle()
async def handle_aquarium(
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    image_bytes = await _aquarium_image(uid)
    if image_bytes is not None and _is_qqbot_event(event):
        # 官Bot：卡片图 + 批量操作按钮键盘，单条合并（markdown 内嵌图）
        # 优先；图床不可用回退「富媒体图片 + 按钮键盘」两条消息
        keyboard = _aquarium_keyboard(uid)
        try:
            if await qq_buttons.send_image_with_keyboard(
                bot, event, image_bytes, keyboard
            ):
                return
            await bot.send(event, build_image_msg(event, image_bytes))
            await qq_buttons.send_keyboard_message(bot, event, " ", keyboard)
            return
        except FinishedException:
            raise                  # finish 的正常终止信号，放行
        except Exception as error:
            logger.warning(f"发送水族箱卡片失败，回退文本: {error}")
    elif image_bytes is not None:
        try:
            await aquarium_cmd.finish(build_image_msg(event, image_bytes))
        except FinishedException:
            raise                  # finish 的正常终止信号，放行
        except Exception as error:
            logger.warning(f"发送水族箱卡片失败，回退文本: {error}")
    await _finish_as_forward(aquarium_cmd, event, bot, await _aquarium_text(uid))


tank_sell_cmd = on_command("水族箱卖出", priority=5, block=True)


@tank_sell_cmd.handle()
async def handle_tank_sell(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    arg = args.extract_plain_text().strip()
    if not arg.isdigit():
        await tank_sell_cmd.finish("用法: 水族箱卖出 <槽位编号>（编号见 查看水族箱）")

    result = await FishService.sell_aquarium(uid, int(arg))
    if not result["ok"]:
        if result["reason"] == "not_found":
            await tank_sell_cmd.finish("该槽位没有鱼，发送 查看水族箱 核对编号")
        await tank_sell_cmd.finish("卖出失败...")

    await tank_sell_cmd.finish(
        f"水族箱 #{result['slot']} 的{result['species_name']}"
        f"已养至 {result['length']}cm，卖出获得 {result['price']} 金币~"
    )


tank_release_cmd = on_command("水族箱放生", priority=5, block=True)


@tank_release_cmd.handle()
async def handle_tank_release(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    arg = args.extract_plain_text().strip()
    if not arg.isdigit():
        await tank_release_cmd.finish("用法: 水族箱放生 <槽位编号>（编号见 查看水族箱）")

    result = await FishService.release_aquarium(uid, int(arg))
    if not result["ok"]:
        if result["reason"] == "not_found":
            await tank_release_cmd.finish("该槽位没有鱼，发送 查看水族箱 核对编号")
        if result["reason"] == "no_lucky":
            await tank_release_cmd.finish(
                f"{result['species_name']}当前售价不足1万（{result['price']}金币），"
                "无法通过放生获得幸运币，可发送 水族箱卖出 卖掉它"
            )
        await tank_release_cmd.finish("放生失败...")

    await tank_release_cmd.finish(
        f"水族箱 #{result['slot']} 的{result['species_name']}"
        f"已养至 {result['length']}cm，放生获得 {result['lucky_value']} 幸运币~"
    )


def _sell_grown_text(result: dict) -> str:
    """出售大鱼 的回复文本（指令与官Bot按钮共用）"""
    if result["count"] == 0:
        return "水族箱里没有已长至最大的鱼~（鱼在箱里会自己慢慢长大）"
    tail = (
        f"\n其余 {result['kept']} 条尚未长至最大，仍留在箱里"
        if result["kept"] else ""
    )
    return (
        f"售出 {result['count']} 条已长至最大的鱼，"
        f"共获得 {result['total']} 金币~{tail}"
    )


def _release_grown_text(result: dict) -> str:
    """放生大鱼 的回复文本（指令与官Bot按钮共用）"""
    if result["count"] == 0:
        if result["skipped"]:
            return (
                "已长至最大的鱼售价都不足1万，无法通过放生获得幸运币~\n"
                "可发送 出售大鱼 卖掉它们"
            )
        return "水族箱里没有可放生的已长至最大的鱼~（鱼在箱里会自己慢慢长大）"
    tails = []
    if result["skipped"]:
        tails.append(
            f"\n另有 {result['skipped']} 条已长至最大但售价不足1万，无法放生"
        )
    if result["kept"]:
        tails.append(f"\n其余 {result['kept']} 条尚未长至最大，仍留在箱里")
    return (
        f"放生 {result['count']} 条已长至最大的鱼，"
        f"共获得 {result['total_lucky']} 幸运币~" + "".join(tails)
    )


def _sell_all_text(result: dict) -> str:
    """全部出售 的回复文本（指令与官Bot按钮共用）"""
    if result["count"] == 0:
        return "水族箱空空如也，没有可出售的鱼~"
    return f"售出水族箱全部 {result['count']} 条鱼，共获得 {result['total']} 金币~"


def _release_all_text(result: dict) -> str:
    """全部放生 的回复文本（指令与官Bot按钮共用）"""
    if result["count"] == 0:
        return "水族箱里没有可放生的鱼（售价达到1万金币才可放生）~"
    tail = (
        f"\n另有 {result['skipped']} 条售价不足1万，仍留在箱里"
        if result["skipped"] else ""
    )
    return f"放生 {result['count']} 条鱼，共获得 {result['total_lucky']} 幸运币~{tail}"


sell_grown_cmd = on_command("出售大鱼", priority=5, block=True)


@sell_grown_cmd.handle()
async def handle_sell_grown(uid: int = Depends(get_uid)) -> None:
    result = await FishService.sell_aquarium_batch(uid, only_grown=True)
    await sell_grown_cmd.finish(_sell_grown_text(result))


sell_all_cmd = on_command("全部出售", priority=5, block=True)


@sell_all_cmd.handle()
async def handle_sell_all(uid: int = Depends(get_uid)) -> None:
    result = await FishService.sell_aquarium_batch(uid)
    await sell_all_cmd.finish(_sell_all_text(result))


release_all_cmd = on_command("全部放生", priority=5, block=True)


@release_all_cmd.handle()
async def handle_release_all(uid: int = Depends(get_uid)) -> None:
    result = await FishService.release_aquarium_all(uid)
    await release_all_cmd.finish(_release_all_text(result))


release_grown_cmd = on_command("放生大鱼", priority=5, block=True)


@release_grown_cmd.handle()
async def handle_release_grown(uid: int = Depends(get_uid)) -> None:
    """放生全部已长至最大的可放生鱼（售价达到1万才有幸运值）"""
    result = await FishService.release_aquarium_all(uid, only_grown=True)
    await release_grown_cmd.finish(_release_grown_text(result))


expand_cmd = on_command("扩展水族箱", priority=5, block=True)


@expand_cmd.handle()
async def handle_expand(uid: int = Depends(get_uid)) -> None:
    result = await FishService.expand_slots(uid)
    if not result["ok"]:
        if result["reason"] == "max":
            await expand_cmd.finish(
                f"水族箱槽位已达上限（{C.aquarium()['max_slots']}）"
            )
        if result["reason"] == "no_gold":
            await expand_cmd.finish(
                f"金币不足（需要 {result['cost']} 金币）"
            )
        await expand_cmd.finish("扩展失败...")

    await expand_cmd.finish(
        f"水族箱扩展成功！当前槽位：{result['slots']}（花费 {result['cost']} 金币）"
    )


# ===== 钓鱼商店 =====
shop_cmd = on_command("钓鱼商店", aliases={"渔具商店"}, priority=5, block=True)


@shop_cmd.handle()
async def handle_shop(
    bot: Bot,
    event: Event,
    uid: int = Depends(get_uid),
) -> None:
    player = await FishService.ensure_player(uid)
    owned_ids = {g["gear_id"] for g in await FishDB.list_gear(uid)}

    # 商品块紧凑拼接（块间不补空行）；左右两列的逐行配对由制表符 join 保证
    def pack(blocks: list[list[str]]) -> list[str]:
        out: list[str] = []
        for block in blocks:
            out.extend(block)
        return out

    # 左列：鱼竿 → 鱼线；右列：鱼饵 → 幸运宝珠
    left = ["==鱼竿=="] + pack([
        _gear_block(rod, "rod", rod_id in owned_ids, idx)
        for idx, (rod_id, rod) in enumerate(rod_table().items(), start=1)
    ])
    left += ["", "==鱼线=="] + pack([
        _gear_block(gear, "line", line_id in owned_ids, idx)
        for idx, (line_id, gear) in enumerate(line_table().items(), start=1)
    ])

    right = ["==鱼饵=="] + pack([
        _bait_block(bait, bait_id, await FishDB.get_bait_count(uid, bait_id),
                    bait_id == player["bait_id"], idx)
        for idx, (bait_id, bait) in enumerate(bait_table().items(), start=1)
    ])
    right += ["", "==幸运宝珠=="] + _orb_block(
        C.orb_info(), player["orb_owned"]
    )
    right += ["", "==水族箱=="] + _pump_block(
        C.pump_info(), player["pump_owned"]
    )

    # 制表符分列：渲染器把右列固定画在画布中线
    height = max(len(left), len(right))
    left += [""] * (height - len(left))
    right += [""] * (height - len(right))
    lines = [
        "买鱼竿/买鱼线/买鱼饵/买幸运宝珠/买氧气泵 <名称>（计价货币见各商品售价）",
        "换鱼竿/换鱼线/装备鱼饵 <名称> 、升级幸运宝珠",
        "",
    ]
    lines += [f"{a}	{b}".rstrip() for a, b in zip(left, right)]
    await _finish_as_forward(shop_cmd, event, bot, "\n".join(lines), width=1000)


# ===== 渔具状态 =====
gear_cmd = on_command("我的渔具", aliases={"钓鱼背包", "我的背包", "背包"}, priority=5, block=True)


async def _gear_list_text(uid: int, two_column: bool = False) -> str:
    """钓鱼背包文本（指令用）。

    以 ==种类== 显式分区：装备中 / 渔具库（拥有的渔具实例）/ 鱼饵 /
    药水 / 道具。two_column=True（官Bot 转图片）时排成左右两列——
    左列 装备中+渔具库，右列 鱼饵+药水+道具，行内以制表符分列，
    由官Bot 图片渲染器把右列固定画在画布中线（与钓鱼商店同机制）。
    """
    player = await FishService.ensure_player(uid)
    rods, lines, baits = rod_table(), line_table(), bait_table()

    async def _gear_text(gear_item_id, kind):
        table = rods if kind == "rod" else lines
        basic_id = C.DEFAULT_ROD if kind == "rod" else C.DEFAULT_LINE
        if gear_item_id is None:
            return f"{table[basic_id]['name']}（无限耐久）"
        instance = await FishDB.get_gear(gear_item_id, uid)
        if instance is None:
            return "（装备实例丢失，可重新装备）"
        entry = table.get(instance["gear_id"])
        if entry is None:
            return "（未知装备）"
        state = "" if instance["durability"] > 0 else "【已损坏，需修复】"
        return (
            f"{entry['name']} 耐久{instance['durability']}/{entry['durability']}"
            f"{state}"
        )

    # ==装备中==
    equipped = [
        "==装备中==",
        f"鱼竿：{await _gear_text(player['rod_item_id'], 'rod')}",
        f"鱼线：{await _gear_text(player['line_item_id'], 'line')}",
    ]
    if player["orb_owned"]:
        cap = C.orb_energy_cap(player["orb_level"])
        mark = "（使用中）" if player["orb_equipped"] else "（未装备）"
        equipped.append(
            f"幸运宝珠：Lv.{player['orb_level']} "
            f"能量{player['orb_energy']}/{cap}{mark}"
        )
    else:
        equipped.append("幸运宝珠：未拥有（钓鱼商店可购买）")
    potions = C.potion_table()
    potion_id = player.get("potion_id")
    if potion_id and potion_id in potions:
        left = await FishDB.get_prop_count(uid, potion_id)
        equipped.append(f"药水：{potions[potion_id]['name']}（使用中，剩{left}瓶）")
    else:
        equipped.append("药水：未装备")

    # ==渔具库==（拥有的渔具实例；初始渔具不落库）
    owned = await FishDB.list_gear(uid)
    gear_lib = ["==渔具库=="]
    for g in owned:
        table = rods if g["kind"] == "rod" else lines
        entry = table.get(g["gear_id"])
        name = entry["name"] if entry else g["gear_id"]
        state = "" if g["durability"] > 0 else "【已损坏】"
        kind_name = "竿" if g["kind"] == "rod" else "线"
        gear_lib.append(f"{name}（{kind_name}） 耐久{g['durability']}{state}")
    if len(gear_lib) == 1:
        gear_lib.append("（空）")

    # ==鱼饵==
    bait_lines = ["==鱼饵=="]
    has_bait = False
    for bid, info in baits.items():
        if info.get("unlimited"):
            mark = "（使用中）" if bid == player["bait_id"] else ""
            bait_lines.append(f"{info['name']} ∞{mark}")
            has_bait = True
        else:
            count = await FishDB.get_bait_count(uid, bid)
            if count > 0:
                mark = "（使用中）" if bid == player["bait_id"] else ""
                bait_lines.append(f"{info['name']} ×{count}{mark}")
                has_bait = True
    if not has_bait:
        bait_lines.append("（空）")

    # ==药水==（有则显示；使用中的带标注）
    props = await FishDB.list_props(uid)
    potion_lines = ["==药水=="]
    shown_potion = False
    for pid in C.POTION_ORDER:
        count = props.get(pid, 0)
        if count > 0:
            mark = "（使用中）" if pid == potion_id else ""
            potion_lines.append(f"{potions[pid]['name']} ×{count}{mark}")
            shown_potion = True
    if not shown_potion:
        potion_lines.append("（空）")

    # ==道具==（钓鱼背包中非药水的道具，如钓鱼扫荡劵）
    egg_names = {k: v["name"] for k, v in C.egg_table().items()}
    prop_lines = ["==道具=="]
    shown_prop = False
    for pid, count in props.items():
        if pid in potions:
            continue
        prop_lines.append(f"{egg_names.get(pid, pid)} ×{count}")
        shown_prop = True
    if not shown_prop:
        prop_lines.append("（空）")

    hints = ["发送 换鱼竿/换鱼线/装备药水 <名称> 可更换装备"]

    if not two_column:
        sections = [equipped, gear_lib, bait_lines, potion_lines, prop_lines]
        return "\n\n".join("\n".join(s) for s in sections) + "\n\n" + hints[0]

    left = equipped + [""] + gear_lib
    right = bait_lines + [""] + potion_lines + [""] + prop_lines + [""] + hints
    height = max(len(left), len(right))
    left += [""] * (height - len(left))
    right += [""] * (height - len(right))
    # 左右列以制表符分隔：官Bot 图片渲染器把右列固定画在画布中线
    return "\n".join(f"{a}\t{b}".rstrip("\t") for a, b in zip(left, right))


@gear_cmd.handle()
async def handle_gear(
    bot: Bot, event: Event, uid: int = Depends(get_uid)
) -> None:
    two_column = _is_qqbot_event(event)
    text = await _gear_list_text(uid, two_column=two_column)
    await _finish_as_forward(
        gear_cmd, event, bot, text, width=1000 if two_column else 600
    )


# ===== 鱼竿 =====
buy_rod_cmd = on_command("买鱼竿", aliases={"购买鱼竿"}, priority=5, block=True)


@buy_rod_cmd.handle()
async def handle_buy_rod(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    name = _strip_quantity(args.extract_plain_text())
    if not name:
        await buy_rod_cmd.finish("用法: 买鱼竿/购买鱼竿 <名称>（见 钓鱼商店）")

    rod_id, _ = _find_by_name(rod_table(), name)
    result = await FishService.buy_gear(uid, "rod", rod_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await buy_rod_cmd.finish("没有这种鱼竿，发送 钓鱼商店 查看列表")
        if result["reason"] == "basic":
            await buy_rod_cmd.finish(f"{result['name']}是初始鱼竿，无需购买")
        if result["reason"] == "owned":
            await buy_rod_cmd.finish(f"已拥有{result['name']}，无需重复购买")
        if result["reason"] == "no_currency":
            await buy_rod_cmd.finish(
                f"购买失败，{C.currency_name(result['currency'])}不足喔..."
                f"（需要 {result['cost']} {C.currency_name(result['currency'])}）"
            )
        await buy_rod_cmd.finish("购买失败...")

    await buy_rod_cmd.finish(f"买下了{result['name']}，已自动装备~")


equip_rod_cmd = on_command("装备鱼竿", aliases={"换鱼竿"}, priority=5, block=True)


@equip_rod_cmd.handle()
async def handle_equip_rod(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    name = args.extract_plain_text().strip()
    if not name:
        await equip_rod_cmd.finish("用法: 装备鱼竿/换鱼竿 <名称>")

    rod_id, _ = _find_by_name(rod_table(), name)
    result = await FishService.equip_gear(uid, "rod", rod_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await equip_rod_cmd.finish("没有这种鱼竿")
        if result["reason"] == "not_owned":
            await equip_rod_cmd.finish("你还没有这根鱼竿，先去 钓鱼商店 看看吧")
        await equip_rod_cmd.finish("装备失败...")

    await equip_rod_cmd.finish(f"已装备{result['name']}！")


repair_rod_cmd = on_command("修理鱼竿", priority=5, block=True)


@repair_rod_cmd.handle()
async def handle_repair_rod(uid: int = Depends(get_uid)) -> None:
    result = await FishService.repair_gear(uid, "rod")
    if not result["ok"]:
        if result["reason"] == "basic":
            await repair_rod_cmd.finish("初始鱼竿没有耐久，无需修复~")
        if result["reason"] == "no_need":
            await repair_rod_cmd.finish(f"{result['name']}耐久完好，无需修复")
        if result["reason"] == "no_gold":
            await repair_rod_cmd.finish(f"金币不足（需要 {result['cost']} 金币）")
        await repair_rod_cmd.finish("修复失败...")

    await repair_rod_cmd.finish(
        f"{result['name']}修复完成！耐久恢复至 {result['durability']}"
        f"（花费 {result['cost']} 金币）"
    )


# ===== 鱼线 =====
buy_line_cmd = on_command("买鱼线", aliases={"购买鱼线"}, priority=5, block=True)


@buy_line_cmd.handle()
async def handle_buy_line(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    name = _strip_quantity(args.extract_plain_text())
    if not name:
        await buy_line_cmd.finish("用法: 买鱼线/购买鱼线 <名称>（见 钓鱼商店）")

    line_id, _ = _find_by_name(line_table(), name)
    result = await FishService.buy_gear(uid, "line", line_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await buy_line_cmd.finish("没有这种鱼线，发送 钓鱼商店 查看列表")
        if result["reason"] == "basic":
            await buy_line_cmd.finish(f"{result['name']}是初始鱼线，无需购买")
        if result["reason"] == "owned":
            await buy_line_cmd.finish(f"已拥有{result['name']}，无需重复购买")
        if result["reason"] == "no_currency":
            await buy_line_cmd.finish(
                f"购买失败，{C.currency_name(result['currency'])}不足喔..."
                f"（需要 {result['cost']} {C.currency_name(result['currency'])}）"
            )
        await buy_line_cmd.finish("购买失败...")

    await buy_line_cmd.finish(f"买下了{result['name']}，已自动装备~")


equip_line_cmd = on_command("装备鱼线", aliases={"换鱼线"}, priority=5, block=True)


@equip_line_cmd.handle()
async def handle_equip_line(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    name = args.extract_plain_text().strip()
    if not name:
        await equip_line_cmd.finish("用法: 装备鱼线/换鱼线 <名称>")

    line_id, _ = _find_by_name(line_table(), name)
    result = await FishService.equip_gear(uid, "line", line_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await equip_line_cmd.finish("没有这种鱼线")
        if result["reason"] == "not_owned":
            await equip_line_cmd.finish("你还没有这种鱼线，先去 钓鱼商店 看看吧")
        await equip_line_cmd.finish("装备失败...")

    await equip_line_cmd.finish(f"已装备{result['name']}！")


repair_line_cmd = on_command("修理鱼线", priority=5, block=True)


@repair_line_cmd.handle()
async def handle_repair_line(uid: int = Depends(get_uid)) -> None:
    result = await FishService.repair_gear(uid, "line")
    if not result["ok"]:
        if result["reason"] == "basic":
            await repair_line_cmd.finish("初始鱼线没有耐久，无需修复~")
        if result["reason"] == "no_need":
            await repair_line_cmd.finish(f"{result['name']}耐久完好，无需修复")
        if result["reason"] == "no_gold":
            await repair_line_cmd.finish(f"金币不足（需要 {result['cost']} 金币）")
        await repair_line_cmd.finish("修复失败...")

    await repair_line_cmd.finish(
        f"{result['name']}修复完成！耐久恢复至 {result['durability']}"
        f"（花费 {result['cost']} 金币）"
    )


# ===== 鱼饵 =====
buy_bait_cmd = on_command("买鱼饵", aliases={"购买鱼饵"}, priority=5, block=True)


@buy_bait_cmd.handle()
async def handle_buy_bait(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    parts = args.extract_plain_text().split()
    if not parts:
        await buy_bait_cmd.finish(
            "用法: 买鱼饵/购买鱼饵 <名称> [数量]（见 钓鱼商店；初始鱼饵无限无需购买）"
        )

    bait_id, _ = _find_by_name(bait_table(), parts[0])
    if bait_id is None:
        await buy_bait_cmd.finish("没有这种鱼饵，发送 钓鱼商店 查看列表")
    num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1

    result = await FishService.buy_bait(uid, bait_id, num)
    if not result["ok"]:
        if result["reason"] == "bad_args":
            if result.get("sub") == "num":
                await buy_bait_cmd.finish("数量必须大于0")
            await buy_bait_cmd.finish("初始鱼饵无限使用，无需购买~")
        if result["reason"] == "no_currency":
            await buy_bait_cmd.finish(
                f"购买失败，{C.currency_name(result['currency'])}不足喔..."
                f"（需要 {result['cost']} {C.currency_name(result['currency'])}）"
            )
        await buy_bait_cmd.finish("购买失败...")

    await buy_bait_cmd.finish(
        f"购买了{result['name']} ×{result['num']}"
        f"（{C.currency_name(result['currency'])}-{result['cost']}）"
    )


equip_bait_cmd = on_command("装备鱼饵", aliases={"换鱼饵"}, priority=5, block=True)


@equip_bait_cmd.handle()
async def handle_equip_bait(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    name = args.extract_plain_text().strip()
    if not name:
        await equip_bait_cmd.finish("用法: 换鱼饵 <名称>")

    bait_id, _ = _find_by_name(bait_table(), name)
    result = await FishService.equip_bait(uid, bait_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await equip_bait_cmd.finish("没有这种鱼饵")
        await equip_bait_cmd.finish("装备失败...")

    hint = "" if bait_id == C.DEFAULT_BAIT else "（库存不足时记得先购买）"
    await equip_bait_cmd.finish(f"已切换为{result['name']}{hint}")


# ===== 药水（彩蛋「药水包」掉落；同时只能装备一种，用完自动卸下） =====
equip_potion_cmd = on_command("装备药水", aliases={"换药水"}, priority=5, block=True)


@equip_potion_cmd.handle()
async def handle_equip_potion(
    args: Message = CommandArg(),
    uid: int = Depends(get_uid),
) -> None:
    name = args.extract_plain_text().strip()
    if not name:
        await equip_potion_cmd.finish(
            "用法: 装备药水/换药水 <药水名称>（药水可从彩蛋「药水包」中获得）"
        )

    potion_id, _ = _find_by_name(C.potion_table(), name)
    result = await FishService.equip_potion(uid, potion_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            await equip_potion_cmd.finish(
                "没有这种药水（可从彩蛋「药水包」中获得：大鱼/稀有度/回溯药水）"
            )
        if result["reason"] == "not_owned":
            await equip_potion_cmd.finish(
                f"你还没有{result['name']}，先钓一个药水包吧~"
            )
        await equip_potion_cmd.finish("装备失败...")

    effect = C.potion_table().get(potion_id, {}).get("desc", "")
    await equip_potion_cmd.finish(
        f"已装备{result['name']}（效果：{effect}，剩余 {result['count']} 瓶，"
        "用完后自动卸下）\n"
        "同时只能装备一种药水，发送 装备药水 <名称> 可随时更换"
    )


# ===== 幸运宝珠 =====
buy_orb_cmd = on_command("买幸运宝珠", aliases={"购买幸运宝珠"}, priority=5, block=True)


@buy_orb_cmd.handle()
async def handle_buy_orb(uid: int = Depends(get_uid)) -> None:
    result = await FishService.buy_orb(uid)
    if not result["ok"]:
        if result["reason"] == "owned":
            await buy_orb_cmd.finish(f"已拥有{result['name']}，无需重复购买")
        if result["reason"] == "no_currency":
            await buy_orb_cmd.finish(
                f"购买失败，{C.currency_name(result['currency'])}不足喔..."
                f"（需要 {result['cost']} {C.currency_name(result['currency'])}）"
            )
        await buy_orb_cmd.finish("购买失败...")

    await buy_orb_cmd.finish(
        f"买下了{result['name']}！已自动装备~"
        f"当前 Lv.{result['level']}，能量上限 {result['energy_cap']}，"
        "能量满后下次钓鱼触发幸运暴击"
    )


buy_pump_cmd = on_command("买氧气泵", aliases={"购买氧气泵"}, priority=5, block=True)


@buy_pump_cmd.handle()
async def handle_buy_pump(uid: int = Depends(get_uid)) -> None:
    result = await FishService.buy_pump(uid)
    if not result["ok"]:
        if result["reason"] == "owned":
            await buy_pump_cmd.finish(f"已拥有{result['name']}，无需重复购买")
        if result["reason"] == "no_currency":
            await buy_pump_cmd.finish(
                f"购买失败，{C.currency_name(result['currency'])}不足喔..."
                f"（需要 {result['cost']} {C.currency_name(result['currency'])}）"
            )
        await buy_pump_cmd.finish("购买失败...")

    await buy_pump_cmd.finish(
        f"买下了{result['name']}！水族箱成长速率 +{result['growth_bonus']:g}%"
        "（购买即生效，无需装备）"
    )


upgrade_orb_cmd = on_command("升级幸运宝珠", aliases={"升级宝珠"}, priority=5, block=True)


@upgrade_orb_cmd.handle()
async def handle_upgrade_orb(uid: int = Depends(get_uid)) -> None:
    result = await FishService.upgrade_orb(uid)
    if not result["ok"]:
        if result["reason"] == "not_owned":
            await upgrade_orb_cmd.finish(
                "你还没有幸运宝珠，发送 买幸运宝珠 购买~"
            )
        if result["reason"] == "max":
            await upgrade_orb_cmd.finish(
                f"幸运宝珠已满级（Lv.{result['level']}）"
            )
        if result["reason"] == "no_luckygold":
            await upgrade_orb_cmd.finish(
                f"幸运币不足（需要 {result['cost']} 幸运币）"
            )
        await upgrade_orb_cmd.finish("升级失败...")

    await upgrade_orb_cmd.finish(
        f"幸运宝珠升级成功！Lv.{result['level']}，"
        f"能量上限 {result['energy_cap']}（花费 {result['cost']} 幸运币，"
        "每升 1 级能量上限 -10）"
    )


# ===== 初始化 =====
driver = get_driver()


@driver.on_startup
async def init_fish():
    """初始化新版钓鱼插件"""
    from pathlib import Path

    plugin_dir = Path(__file__).parent.parent.parent
    db_path = plugin_dir / "src" / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    FishDB.set_db_path(str(db_path))
    FishDB.init_fish_database()
    # 每日钓鱼次数与 chongwu / chaogu 共用 fish_limit 表（同一数据库）
    FishLimitManager.set_db_path(str(db_path))
    FishLimitManager.init_database()
    # 永久增益按图鉴奖励领取记录校正（须在 FishDB 建表之后，确保记录表已存在）
    FishLimitManager.reconcile_perm_bonus()
    logger.info("Fish 插件初始化完成")
