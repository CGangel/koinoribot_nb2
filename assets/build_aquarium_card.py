"""生成水族箱卡片的底图（SVG → PNG）与粘贴槽位表。

与 ``build_fishing_card.py`` 同一套路：底图**不含任何文字**，固定标签与动态
数值一律在固定坐标用 PIL 粘贴（字重/抗锯齿一致），坐标/字号/列宽/配色集中
在 ``assets/aquarium_card_slots.json``。

**布局（单列）**：表头带（标题 + 槽位计数）+ 一行字段名（名称/稀有度/长度/
售价/幸运值/成长所需时间）+ N 条数据行（每行一条鱼，竖向排序，最多 20 条）
+ 表尾提示带（4 行）。单列使卡片接近方形，不像双列那样扁长。

底图由「表头带 / 字段名条 / 一条数据行条 / 表尾带」四段组成：渲染时按实际
条数纵向重复行条，**不预留空行**，图片高度随条目数增长。

列宽由字体实测宽度算出（字段名与最宽数据样例取较宽者），导出到槽位表的
``columns``；成品量化用的调色板策略见 ``palette``（见渲染模块说明）。

用法：
    python assets/build_aquarium_card.py
输出：src/img/fish/aquarium_card.png、assets/aquarium_card_slots.json
"""

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "src" / "fonts" / "HYWenHei-85W.ttf"
IMG_DIR = ROOT / "src" / "img" / "fish"
OUT_SVG = ROOT / "assets" / "aquarium_card.svg"
OUT_SLOTS = ROOT / "assets" / "aquarium_card_slots.json"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

SCALE = 2                  # 渲染倍率

# ---- 尺寸（1 倍设计稿）----
HEADER_H = 62              # 表头带（水蓝横幅：标题 + 槽位计数）
TITLE_H = 22               # 字段名条
ROW_H = 20                 # 单条数据行（紧凑行距）
FOOTER_H = 74              # 表尾带（提示文字，最多四行）
MARGIN = 16
BLOCK_PAD = 10             # 表格左右留白
COL_GAP = 12               # 字段之间的间隔
MAX_ROWS = 20              # 槽位上限（单列最多 20 行）

DATA_SIZE = 10             # 数据字号
HEAD_SIZE = 11             # 字段名字号

# ---- 配色（暖色卡通，与钓鱼卡片同系；表头为水族箱专属水蓝）----
CREAM = "#FFF6E9"          # 卡片底
ROW_BG = "#FFFCF6"         # 数据行底（文字抗锯齿的混色基准）
TITLE_BG = "#F6EEE0"       # 字段名条底
SEP = "#EFDCC4"            # 行分隔线
HEADER = "#4FA3B8"         # 表头水蓝
HEADER_DARK = "#4293A8"
HEADER_TITLE = "#FFFFFF"
HEADER_COUNT = "#EAF6F9"

LABEL = "#9A7A5C"          # 字段名/次要文字
NAME_COLOR = "#3A2A1C"     # 鱼名
SUB_COLOR = "#96785C"      # 长度/幸运等次要数值
OPT_COLOR = "#D6832A"      # 售价（强调）
GROWN_COLOR = "#2F9E63"    # 已长至最大（收益色）

# 稀有度/等级专属色：与钓鱼卡片一致（史诗=紫、神话=X=红）
RARITY_COLORS = {
    "普通": "#7B858C", "稀有": "#2E7AC4", "史诗": "#7A5AC8",
    "传说": "#D9862E", "神话": "#CE4239",
}
GRADE_COLORS = {
    "D": "#8A94A0", "C": "#3FA76A", "B": "#2E86C8",
    "A": "#8B5AD0", "S": "#E08A2A", "X": "#D6402F",
}

# 列定义：(key, 字段名, 最宽数据样例)。列宽取「字段名」与「样例」实测较宽者；
# 长度/售价在字段名里标注单位（数据行只写数字，避免每行重复单位挤占宽度）
COLUMNS = [
    ("name", "名称", "[X]琉璃幻彩鱼"),
    ("rarity", "稀有度", "神话"),
    ("length", "长度(cm)", "1200.5(max: 1800.8)"),
    ("price", "售价(金币)", "999999(max: 999999)"),
    ("lucky", "幸运值", "10(max: 10)"),
    ("time", "成长所需时间", "148天0时"),
]


def _column_layout() -> tuple[dict, int]:
    """按字体实测宽度算出各列 x 偏移（相对表格内容起点）与内容总宽"""
    from PIL import ImageFont

    data_font = ImageFont.truetype(str(FONT), DATA_SIZE)
    head_font = ImageFont.truetype(str(FONT), HEAD_SIZE)

    offsets: dict[str, int] = {}
    x = 0
    for key, label, sample in COLUMNS:
        offsets[key] = int(round(x))
        width = max(
            head_font.getlength(label), data_font.getlength(sample)
        )
        x += width + COL_GAP
    return offsets, int(math.ceil(x - COL_GAP))


def _svg(width: int, block_x: int, block_w: int) -> str:
    """底图 SVG：表头带 + 字段名条 + 一条数据行条 + 表尾带（无文字）"""
    y_title = HEADER_H
    y_row = HEADER_H + TITLE_H
    y_footer = y_row + ROW_H
    height = y_footer + FOOTER_H
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="{CREAM}"/>

  <!-- ===== 表头带：水蓝横幅 + 底部深色条 + 装饰圆 ===== -->
  <rect x="0" y="0" width="{width}" height="{HEADER_H}" fill="{HEADER}"/>
  <rect x="0" y="{HEADER_H}" width="{width}" height="5" fill="{HEADER_DARK}"/>
  <circle cx="{width - 70}" cy="20" r="15" fill="#FFFFFF" opacity="0.14"/>
  <circle cx="{width - 34}" cy="44" r="9" fill="#FFFFFF" opacity="0.16"/>
  <circle cx="{width - 106}" cy="46" r="6" fill="#FFFFFF" opacity="0.16"/>

  <!-- ===== 字段名条 ===== -->
  <rect x="{block_x}" y="{y_title}" width="{block_w}" height="{TITLE_H}"
        fill="{TITLE_BG}"/>
  <line x1="{block_x}" y1="{y_title + TITLE_H - 1}"
        x2="{block_x + block_w}" y2="{y_title + TITLE_H - 1}"
        stroke="{SEP}" stroke-width="1"/>

  <!-- ===== 一条数据行条（渲染时按条数纵向重复） ===== -->
  <rect x="{block_x}" y="{y_row}" width="{block_w}" height="{ROW_H}"
        fill="{ROW_BG}"/>
  <line x1="{block_x}" y1="{y_row + ROW_H - 1}"
        x2="{block_x + block_w}" y2="{y_row + ROW_H - 1}"
        stroke="{SEP}" stroke-width="1"/>
</svg>'''


def _render(svg: str, out: Path, width: int, height: int) -> None:
    """SVG → PNG（Chrome 无头）。底图不量化：渲染时还要贴字再统一量化。"""
    svg_tmp = ROOT / "_aq_build.tmp.svg"
    html_tmp = ROOT / "_aq_build.tmp.html"
    svg_tmp.write_text(svg, encoding="utf-8")
    html_tmp.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        f'<body style="margin:0"><img src="{svg_tmp.name}" '
        f'width="{width}" height="{height}"></body></html>',
        encoding="utf-8",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--force-device-scale-factor={SCALE}", f"--window-size={width},{height}",
         f"--screenshot={out}", f"file:///{html_tmp.as_posix()}"],
        capture_output=True, check=True,
    )
    svg_tmp.unlink(missing_ok=True)
    html_tmp.unlink(missing_ok=True)


def _write_slots(columns: dict, block_w: int, width: int) -> None:
    OUT_SLOTS.write_text(
        json.dumps(
            {
                "image": "aquarium_card.png",
                "scale": SCALE,
                "font": FONT.name,
                "w": width,
                "block_x": MARGIN,
                "block_w": block_w,
                "header_h": HEADER_H,
                "title_h": TITLE_H,
                "row_h": ROW_H,
                "footer_h": FOOTER_H,
                "max_rows": MAX_ROWS,
                "block_pad": BLOCK_PAD,
                "row_bg": ROW_BG,      # 文字抗锯齿混色基准（量化调色板用）
                # 成品量化：背景自适应取色 + 文字色及其向行底渐隐的档位一并
                # 写入调色板（否则小字彩色像素会被按面积的自适应量化吞掉，
                # 出现「史诗变青蓝、神话变棕」这类偏色）
                "palette": {
                    "bg_colors": 26,
                    "fade_steps": 4,
                },
                "columns": columns,
                "column_order": [key for key, _, _ in COLUMNS],
                "column_labels": {key: label for key, label, _ in COLUMNS},
                "data_size": DATA_SIZE,
                "head_size": HEAD_SIZE,
                "header": {
                    "title": {"x": 24, "size": 20, "color": HEADER_TITLE},
                    "count": {"size": 14, "color": HEADER_COUNT, "x_pad": 24},
                },
                "footer": {
                    "x": 20,
                    "lines": [
                        {"dy": 15, "size": 10, "color": LABEL},
                        {"dy": 30, "size": 10, "color": LABEL},
                        {"dy": 45, "size": 10, "color": LABEL},
                        {"dy": 60, "size": 10, "color": LABEL},
                    ],
                },
                "colors": {
                    "rarity": RARITY_COLORS,
                    "grade": GRADE_COLORS,
                    "name": NAME_COLOR,
                    "sub": SUB_COLOR,
                    "price": OPT_COLOR,
                    "label": LABEL,
                    "grown": GROWN_COLOR,
                },
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    columns, content_w = _column_layout()
    block_w = content_w + BLOCK_PAD * 2
    width = MARGIN * 2 + block_w
    height = HEADER_H + TITLE_H + ROW_H + FOOTER_H

    svg = _svg(width, MARGIN, block_w)
    OUT_SVG.write_text(svg, encoding="utf-8")       # 设计稿留档
    out = IMG_DIR / "aquarium_card.png"
    _render(svg, out, width, height)
    print(f"输出: {out.name}  {out.stat().st_size/1024:.1f} KB"
          f"  ({width*SCALE}x{height*SCALE})")

    _write_slots(columns, block_w, width)
    print(f"表格宽: {width}  内容宽: {content_w}  列偏移: {columns}")
    print(f"槽位: {OUT_SLOTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
