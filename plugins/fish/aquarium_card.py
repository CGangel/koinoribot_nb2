"""水族箱卡片渲染：单列表格（底图 + PIL 贴字，同钓鱼卡片 ``card.py``）。

布局与钓鱼卡片同一套路：几何装饰全部来自预渲染底图
（``src/img/fish/aquarium_card.png``，由 ``assets/build_aquarium_card.py``
生成），坐标/字号/列宽与配色集中在 ``assets/aquarium_card_slots.json``，
本模块只把文本贴到固定坐标上——单一光栅化路径，字重/抗锯齿一致。

**版面（单列）**：表头带（标题 + 槽位计数）+ 一行字段名（槽位/名称/稀有度/
长度/售价/幸运值/成长所需时间）+ N 条数据行（每行一条鱼，竖向排序，最多
20 条）+ 表尾提示带（4 行：出售/放生单条、批量指令、氧气泵、扩展与占用概况）。
首列槽位编号即 水族箱卖出/水族箱放生 使用的编号（与文本面板一致）。
底图分「表头带 / 字段名条 / 一条数据行条 / 表尾带」四段，渲染时按条数纵向
重复行条，**不预留空行**；单列使卡片接近方形，不像双列那样扁长。

**配色保真**：小字号彩色文字（如史诗紫、神话/X 红）像素占比极低，若用按
面积的自适应量化会被合并掉（实测「史诗」变青蓝、「神话」变棕）。故量化用
**显式调色板**：背景自适应取 ``palette.bg_colors`` 色，再把每种文字色及其
向行底渐隐的 ``palette.fade_steps`` 档一并写入调色板，彩色文字得以保真。
"""

import json
from pathlib import Path
from typing import Optional

from nonebot.log import logger

ROOT = Path(__file__).resolve().parents[2]
SLOTS_PATH = ROOT / "assets" / "aquarium_card_slots.json"
IMG_DIR = ROOT / "src" / "img" / "fish"
FONT_DIR = ROOT / "src" / "fonts"

# 槽位上限（= 单列最多行数），与 fish_config.AQUARIUM_MAX_SLOTS 一致
CAPACITY = 20

_spec: Optional[dict] = None
_base_cache: dict[str, "object"] = {}
_font_cache: dict[int, "object"] = {}


def _load_spec() -> Optional[dict]:
    global _spec
    if _spec is None:
        try:
            _spec = json.loads(SLOTS_PATH.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(f"读取水族箱卡片槽位表失败: {error}")
            return None
    return _spec


def _font(size: int):
    from PIL import ImageFont

    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(
            str(FONT_DIR / _spec["font"]), size
        )
    return _font_cache[size]


def _base(image_name: str):
    from PIL import Image

    if image_name not in _base_cache:
        _base_cache[image_name] = Image.open(IMG_DIR / image_name).convert("RGB")
    return _base_cache[image_name]


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _text_colors(spec: dict) -> set[str]:
    """槽位表里出现的全部文字颜色（含表头/表尾），用于构造量化调色板"""
    found: set[str] = set()

    def collect(node) -> None:
        if isinstance(node, str):
            if node.startswith("#") and len(node) == 7:
                found.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for key in ("colors", "header", "footer"):
        collect(spec.get(key))
    return found


def _quantize(img, spec: dict, Image):
    """量化成品：背景自适应取色 + 文字色及其渐隐档写入显式调色板。

    直接 ADAPTIVE 量化会按像素面积分配颜色，小字彩色被合并（偏色）；
    显式调色板让文字色始终可用，仅牺牲少许背景色阶。
    """
    cfg = spec.get("palette") or {}
    bg_colors = int(cfg.get("bg_colors", 0) or 0)
    if bg_colors <= 0:
        return img.convert("P", palette=Image.ADAPTIVE, colors=64)

    palette = list(
        img.convert("P", palette=Image.ADAPTIVE, colors=bg_colors)
        .getpalette()[: bg_colors * 3]
    )
    base = _hex_rgb(spec.get("row_bg", "#FFFCF6"))
    steps = max(2, int(cfg.get("fade_steps", 4)))
    for color in sorted(_text_colors(spec)):
        fg = _hex_rgb(color)
        for index in range(steps):
            ratio = index / (steps - 1)          # 0=原色，1=完全融入行底
            palette += [
                round(fg[channel] * (1 - ratio) + base[channel] * ratio)
                for channel in range(3)
            ]
    palette += [0] * (768 - len(palette))
    palette_img = Image.new("P", (1, 1))
    palette_img.putpalette(palette)
    return img.quantize(palette=palette_img, dither=Image.Dither.NONE)


def _fmt_num(value: float) -> str:
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def fmt_duration(seconds: float) -> str:
    """成长剩余时长：≥1 天用「N天N时」，≥1 时「N时N分」，其余「N分」；
    不足 1 分显示「<1分」，已长满（0）显示「已长至最大」"""
    if seconds <= 0:
        return "已长至最大"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}天{hours}时"
    if hours:
        return f"{hours}时{minutes}分"
    if minutes:
        return f"{minutes}分"
    return "<1分"


def build_row(
    slot: int, *, name: str, rarity: str, grade: str, length: float,
    cap: float, price: int, max_price: int, lucky: int, max_lucky: int,
    growth_per_day: float,
) -> dict:
    """把一条鱼整理成行文本（纯函数，便于单测与调用方复用）。

    首列 slot 为水族箱槽位编号（卖出/放生指令同款，1 起）；数值列统一
    ``当前值(max: 上限)``；末列为按当前成长速率算出的剩余时长。
    """
    maxed = length >= cap - 1e-6
    remain_seconds = (
        0.0 if maxed or growth_per_day <= 0
        else max(0.0, cap - length) / growth_per_day * 86400
    )
    return {
        "slot": str(slot),
        "tag": f"[{grade}]",
        "name": name,
        "rarity": rarity,
        "length": f"{_fmt_num(length)}(max: {_fmt_num(cap)})",
        "price": f"{price}(max: {max_price})",
        "lucky": f"{lucky}(max: {max_lucky})",
        "time": fmt_duration(remain_seconds),
        "grade": grade,
        "maxed": maxed,
    }


def _draw_data_row(draw, put, spec: dict, row: dict, top: float) -> None:
    """画一条数据行：字段按列偏移贴字，等级/稀有度/数值各取专属色"""
    columns = spec["columns"]
    colors = spec["colors"]
    base_x = spec["block_x"] + spec["block_pad"]
    baseline = top + spec["row_h"] / 2
    size = spec["data_size"]

    def cell(key: str, text: str, color: str) -> None:
        put(base_x + columns[key], baseline, text, size, color, "lm")

    # 槽位编号列：弱化色，读作行号
    cell("slot", row["slot"], colors["label"])
    # 名称列：[等级] 用等级专属色，鱼名用正文色（两段拼接）
    name_x = base_x + columns["name"]
    put(name_x, baseline, row["tag"], size,
        colors["grade"].get(row["grade"], colors["label"]), "lm")
    put(name_x + _font(size).getlength(row["tag"]), baseline,
        row["name"], size, colors["name"], "lm")

    cell("rarity", row["rarity"],
         colors["rarity"].get(row["rarity"], colors["label"]))
    cell("length", row["length"], colors["sub"])
    cell("price", row["price"], colors["price"])
    cell("lucky", row["lucky"], colors["sub"])
    cell("time", row["time"],
         colors["grown"] if row["maxed"] else colors["sub"])


def render(
    rows: list[dict],
    used: int,
    slots: int,
    max_slots: int = CAPACITY,
    pump_bonus: float = 0.0,
    next_price: Optional[int] = None,
    hints: Optional[list[str]] = None,
) -> Optional[bytes]:
    """渲染水族箱卡片 PNG；失败返回 None（调用方回退文本面板）。

    rows 为 build_row 的输出（按入箱顺序，最多 CAPACITY 条）；used/slots 用于
    表头计数；pump_bonus > 0 时表尾显示氧气泵加速；next_price 给出下一槽位
    价格（slots < max_slots 时显示扩展提示）；hints 为表尾补充提示行。
    """
    import io

    from PIL import Image, ImageDraw

    spec = _load_spec()
    if spec is None:
        return None
    try:
        scale = spec["scale"]
        rows = rows[: int(spec.get("max_rows", CAPACITY))]
        width = spec["w"]
        block_x, block_w = spec["block_x"], spec["block_w"]
        header_h, title_h = spec["header_h"], spec["title_h"]
        row_h, footer_h = spec["row_h"], spec["footer_h"]

        rows_top = header_h + (title_h if rows else 0)
        footer_top = rows_top + row_h * len(rows)
        height = footer_top + footer_h

        base = _base(spec["image"])

        def _crop(x: int, y: int, w: int, h: int):
            return base.crop(
                (int(x * scale), int(y * scale),
                 int((x + w) * scale), int((y + h) * scale))
            )

        # 拼装：表头带 + 字段名条 + N 条数据行 + 表尾带（高度随条数增长）
        canvas = Image.new("RGB", (width * scale, height * scale),
                           _hex_rgb(spec.get("row_bg", "#FFFCF6")))
        canvas.paste(_crop(0, 0, width, header_h), (0, 0))
        if rows:
            canvas.paste(
                _crop(block_x, header_h, block_w, title_h),
                (int(block_x * scale), int(header_h * scale)),
            )
            for index in range(len(rows)):
                canvas.paste(
                    _crop(block_x, header_h + title_h, block_w, row_h),
                    (int(block_x * scale),
                     int((header_h + title_h + index * row_h) * scale)),
                )
        canvas.paste(
            _crop(0, header_h + title_h + row_h, width, footer_h),
            (0, int(footer_top * scale)),
        )

        draw = ImageDraw.Draw(canvas)

        def put(x, y, text, size, color, anchor):
            draw.text((int(x * scale), int(y * scale)), text, fill=color,
                      font=_font(int(size * scale)), anchor=anchor)

        # 表头：标题 + 槽位计数
        head = spec["header"]
        put(head["title"]["x"], header_h / 2, "我 的 水 族 箱",
            head["title"]["size"], head["title"]["color"], "lm")
        put(width - head["count"]["x_pad"], header_h / 2,
            f"{used} / {slots} 槽位", head["count"]["size"],
            head["count"]["color"], "rm")

        # 字段名 + 数据行（竖向排序，单列）
        if rows:
            head_x = block_x + spec["block_pad"]
            for key in spec["column_order"]:
                put(head_x + spec["columns"][key], header_h + title_h / 2,
                    spec["column_labels"][key], spec["head_size"],
                    spec["colors"]["label"], "lm")
            for index, row in enumerate(rows):
                _draw_data_row(
                    draw, put, spec, row,
                    header_h + title_h + index * row_h,
                )

        # 表尾：单条出售/放生 → 批量指令 → 氧气泵 → 扩展与占用概况
        lines = list(hints or [])
        if pump_bonus:
            lines.append(f"氧气泵运转中：成长速率 +{pump_bonus:g}%")
        space = max(0, slots - used)
        locked = max(0, max_slots - slots)
        summary = "，".join(
            part for part in (
                f"空槽位 {space}" if space else "",
                f"未解锁 {locked}" if locked else "",
            ) if part
        )
        if next_price is not None and slots < max_slots:
            tail = f"；{summary}" if summary else ""
            lines.append(f"发送 扩展水族箱 增加槽位（下一次 {next_price}金币）{tail}")
        elif summary:
            lines.append(summary)
        footer = spec["footer"]
        for item, text in zip(footer["lines"], lines[: len(footer["lines"])]):
            put(footer["x"], footer_top + item["dy"], text, item["size"],
                item["color"], "lm")

        out = canvas.resize((width, height), Image.LANCZOS)
        out = _quantize(out, spec, Image)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as error:
        logger.warning(f"渲染水族箱卡片失败: {error}")
        return None
