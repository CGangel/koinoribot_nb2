"""一键钓鱼（十连）结果卡片：列表表格，纯 PIL 绘制。

与钓鱼卡片（``card.py``）/水族箱卡片不同，本卡片**没有预渲染底图资产**：
版面只有色带、分隔线与文本，全部经 PIL 单一光栅化路径绘制，无「底图烙
字 + PIL 贴值」的混排，也就不需要 SVG/Chrome 构建链。

**版面（仿水族箱卡片）**：彩色渐变表头带（标题 + 计数）→ 一行列头
（级别/名称/稀有度/药水/长度/售价/幸运值/结果）→ 每杆一行的列表 →
表尾汇总。行字体颜色随该杆稀有度变化（与钓鱼卡片各稀有度专属色同款），
彩蛋行统一粉色；空军行的「鱼逃走了...」随该鱼稀有度着色。

build_line 同时给出扁平文本（``text``，文本兜底/单测用）与分列单元格
（``cells``，渲染用）。渲染失败（字体缺失、PIL 不可用等）返回 None，
由调用方回退文本列表。
"""

from pathlib import Path
from typing import Optional

from nonebot.log import logger

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / "src" / "fonts"
FONT_NAME = "HYWenHei-85W.ttf"

# 版面（1x 设计稿，内部 2x 绘制后降采样，同钓鱼卡片）
W = 820
SCALE = 2
HEADER_H = 64
COLHEAD_H = 28
ROW_H = 30
FOOT_PAD = 14
FOOT_LINE_H = 20

BG = "#FFF6E9"          # 卡片底（与钓鱼卡片一致的暖奶油）
HEADER_DARK = "#B98A5C"
TITLE_COLOR = "#FFFFFF"
LINE = "#EFDCC4"        # 分隔线
LABEL = "#9A7A5C"       # 列头/表尾（暖棕小字）
EGG_COLOR = "#C2508B"   # 彩蛋行（粉，与彩蛋卡片主题同色系）

# 行字体颜色随稀有度变化（与钓鱼卡片 RARITY_COLORS 同款）
RARITY_COLORS = {
    "普通": "#7B858C",
    "稀有": "#2E7AC4",
    "史诗": "#7A5AC8",
    "传说": "#D9862E",
    "神话": "#CE4239",
    "彩蛋": "#C2508B",
}

# 表头带：六档稀有度主题色横向渐变（普通灰→稀有蓝→史诗紫→传说橙→
# 神话红→彩蛋粉），呼应「每一行随稀有度变色」
HEADER_COLORS = [
    "#A9B3BA", "#4A90D9", "#8B6FD0", "#F6A85C", "#E4574F", "#E27FAE",
]

# 列定义：(单元格键, x 偏移, 列头文字)
COLUMNS = [
    ("tag", 20, "级别"),
    ("name", 46, "名称"),
    ("rarity", 128, "稀有度"),
    ("potion", 178, "药水"),
    ("length", 272, "长度(cm)"),
    ("price", 424, "售价(金币)"),
    ("lucky", 582, "幸运值"),
    ("result", 624, "结果"),
]

_font_cache: dict[int, "object"] = {}


def _font(size: int):
    from PIL import ImageFont

    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(str(FONT_DIR / FONT_NAME), size)
    return _font_cache[size]


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _fmt_num(value: float) -> str:
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def build_line(result: dict) -> dict:
    """把一杆结果整理成列表行（纯函数，便于单测）。

    返回 {"kind", "text", "cells", "color"}：text 为扁平整行（文本兜底），
    cells 为分列单元格（渲染），color 为行字体色（随稀有度；彩蛋粉）。
    回溯药水的重判结果只保留最终状态（rollback_used 的杆显示「使用了
    回溯药水」）。
    """
    potion = result.get("potion_used")
    potion_text = f"使用了{potion}" if potion else "未使用药水"
    if result.get("egg"):
        egg = result["egg"]
        brief = (result.get("grant") or {}).get("brief", "")
        return {
            "kind": "egg",
            "text": f"[彩蛋] {egg['name']} 获得 {brief}",
            "cells": {"tag": "[彩蛋]", "name": egg["name"],
                      "brief": f"获得 {brief}"},
            "color": EGG_COLOR,
        }
    length = _fmt_num(result["length"])
    cap = _fmt_num(result["growth_cap"])
    price, max_price = result["sell_price"], result["grown_sell_price"]
    lucky = result.get("lucky_value") or 0
    head = f"[{result['grade']}]{result['species_name']} {result['rarity']}"
    values = (
        f"{length}(max:{cap})cm {price}(max:{max_price})金币 {lucky} "
    )
    if result.get("air"):
        status, kind = "鱼逃走了...", "air"
    elif result.get("auto_sold"):
        status, kind = "已自动出售", "sold"
    elif result.get("auto_tank"):
        status, kind = "已自动入箱", "tank"
    else:
        status, kind = "", "tank"
    return {
        "kind": kind,
        "text": f"{head} {potion_text} {values}{status}".rstrip(),
        "cells": {
            "tag": f"[{result['grade']}]",
            "name": result["species_name"],
            "rarity": result["rarity"],
            "potion": potion_text,
            "length": f"{length}(max:{cap})",
            "price": f"{price}(max:{max_price})",
            "lucky": str(lucky),
            "result": status,
        },
        "color": RARITY_COLORS.get(result["rarity"], "#3A2A1C"),
    }


def _draw_header_band(img, draw, height_px: int) -> None:
    """涂抹状渐变表头带：六档稀有度主题色先横向线性渐变打底（保饱和
    度），再把各色数团椭圆（固定伪随机布局）模糊后按 alpha 叠涂上去，
    形成颜料涂抹的观感；底部深色收边。"""

    import random as _random

    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFilter

    # 底：横向线性渐变
    grad = _Image.new("RGB", (W * SCALE, height_px))
    gdraw = _ImageDraw.Draw(grad)
    stops = len(HEADER_COLORS) - 1
    span = W * SCALE / stops
    for column in range(W * SCALE):
        index = min(stops - 1, int(column / span))
        ratio = (column - span * index) / span
        c0 = _hex_rgb(HEADER_COLORS[index])
        c1 = _hex_rgb(HEADER_COLORS[index + 1])
        gdraw.line(
            ((column, 0), (column, height_px)),
            fill=tuple(round(c0[k] + (c1[k] - c0[k]) * ratio) for k in range(3)),
        )

    # 涂抹层：色团画在独立图层，模糊后作为 alpha 遮罩半透明叠涂
    patch = _Image.new("RGB", (W * SCALE, height_px), grad.getpixel((0, 0)))
    mask = _Image.new("L", (W * SCALE, height_px), 0)
    pdraw, mdraw = _ImageDraw.Draw(patch), _ImageDraw.Draw(mask)
    rng = _random.Random(20261016)   # 固定种子：每次渲染涂抹形状一致
    segment = W * SCALE / len(HEADER_COLORS)
    for index, color in enumerate(HEADER_COLORS):
        center = (index + 0.5) * segment
        for _ in range(3):           # 每色 3 团，大小/位置带抖动地相互叠涂
            cx = int(center + rng.uniform(-0.55, 0.55) * segment)
            cy = rng.randint(0, height_px)
            rx = int(rng.uniform(0.5, 1.1) * segment)
            ry = int(rng.uniform(0.6, 1.3) * height_px)
            box = (cx - rx, cy - ry, cx + rx, cy + ry)
            pdraw.ellipse(box, fill=color)
            mdraw.ellipse(box, fill=150)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=int(24 * SCALE / 2)))
    patch = patch.filter(ImageFilter.GaussianBlur(radius=int(10 * SCALE / 2)))
    grad.paste(patch, (0, 0), mask)
    img.paste(grad, (0, 0))
    draw.rectangle(
        (0, height_px, W * SCALE, height_px + 5 * SCALE), fill=HEADER_DARK)


def render(lines: list[dict], footer: list[str], count: int = 10) -> Optional[bytes]:
    """渲染十连结果卡片 PNG；失败返回 None（调用方回退文本列表）。

    lines 为 build_line 的输出（一杆一行）；footer 为表尾汇总行（如
    「售出 6 条（共 152000金币）·入箱 3 ·空军 1 ·彩蛋 0」「今日剩余
    次数: 0/10」）；count 为计划杆数（表头计数 N/count）。
    """
    try:
        from PIL import Image, ImageDraw

        footer = footer[:3]
        body_h = ROW_H * max(1, len(lines))
        height = HEADER_H + COLHEAD_H + body_h + FOOT_PAD + \
            FOOT_LINE_H * len(footer) + FOOT_PAD
        img = Image.new("RGB", (W * SCALE, height * SCALE), BG)
        draw = ImageDraw.Draw(img)

        def put(x, y, text, size, color, anchor):
            draw.text((int(x * SCALE), int(y * SCALE)), text, fill=color,
                      font=_font(int(size * SCALE)), anchor=anchor)

        # 涂抹状渐变表头带 + 标题（描边保证浅色区可读）+ 计数
        _draw_header_band(img, draw, int((HEADER_H - 5) * SCALE))
        title_font = _font(20 * SCALE)
        draw.text(
            (int(24 * SCALE), int(HEADER_H / 2 * SCALE)), "十 连 钓 鱼",
            fill=TITLE_COLOR, font=title_font, anchor="lm",
            stroke_width=SCALE, stroke_fill="#7A4E20",
        )
        put(W - 24, HEADER_H / 2, f"{len(lines)} / {count} 杆", 13,
            "#FFF6E9", "rm")

        # 列头
        head_top = HEADER_H
        for key, x, label in COLUMNS:
            put(x, head_top + COLHEAD_H / 2, label, 11, LABEL, "lm")

        # 数据行：整行字体色随稀有度（彩蛋粉），行间细分隔线。
        # 名称列按标签实测宽度避让（[彩蛋] 比 [S] 宽，防重叠；短标签
        # 仍对齐到列头位置）
        for index, line in enumerate(lines):
            top = HEADER_H + COLHEAD_H + index * ROW_H
            baseline = top + ROW_H / 2
            cells = line["cells"]
            color = line.get("color", "#3A2A1C")
            tag_x = COLUMNS[0][1]
            row_font = _font(13 * SCALE)
            name_x = max(
                COLUMNS[1][1],
                tag_x + row_font.getlength(cells["tag"]) / SCALE + 6,
            )
            put(tag_x, baseline, cells["tag"], 13, color, "lm")
            put(name_x, baseline, cells["name"], 13, color, "lm")
            if line.get("kind") == "egg":
                put(COLUMNS[3][1], baseline, cells["brief"], 13, color, "lm")
            else:
                for key, x, _label in COLUMNS[2:]:
                    put(x, baseline, cells.get(key, ""), 13, color, "lm")
            if index + 1 < len(lines):
                draw.line(
                    (int(20 * SCALE), int((top + ROW_H) * SCALE),
                     int((W - 20) * SCALE), int((top + ROW_H) * SCALE)),
                    fill=LINE, width=SCALE)

        # 表尾汇总
        foot_top = HEADER_H + COLHEAD_H + body_h + FOOT_PAD
        for index, text in enumerate(footer):
            put(24, foot_top + FOOT_LINE_H * index + FOOT_LINE_H / 2,
                text, 11, LABEL, "lm")

        out = img.resize((W, height), Image.LANCZOS)
        import io

        buf = io.BytesIO()
        out.convert("P", palette=Image.ADAPTIVE, colors=128).save(
            buf, format="PNG", optimize=True
        )
        return buf.getvalue()
    except Exception as error:
        logger.warning(f"渲染十连钓鱼卡片失败: {error}")
        return None
