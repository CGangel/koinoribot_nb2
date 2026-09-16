"""生成钓鱼卡片的暖色卡通底图（SVG → PNG → 量化压缩）。

底图**不含任何文字**（只有装饰与面板），固定标签与动态数值一律在固定
坐标用 PIL 粘贴（同 icelogin 签到卡）。这样标签与数值经同一光栅化路径
绘制，字重/抗锯齿完全一致——若把标签烙进底图（浏览器渲染）再粘贴值
（PIL 渲染），同一字体会因两边抗锯齿差异看起来像两套字体。

**单一布局 + 5 个稀有度主题 + 空军变体**：布局只有一套（四个信息面板 +
三个选项按钮），主题仅改上方横幅的配色（其余底色/面板完全一致）。某栏
无需显示时不粘贴该栏文字即可（如售价 <1万 的鱼不显示幸运值、不显示放生）。
主题按鱼的稀有度选择：普通-灰白、稀有-蓝、史诗-紫、传说-橙、神话-红。

**空军变体**（``fishing_card_<key>_air.png``）与正常底图同布局，仅左、右
栏的装饰不同：左栏不画鱼线（鱼已挣脱），改为鱼左侧的几道波浪线；右栏
不画三个选项按钮（空军无从选择，渲染时改贴固定文案）。中栏信息面板与
「今日剩余钓鱼次数」框保持不变，卡片因此与正常钓获大致相同。

本脚本导出粘贴槽位表 ``assets/fishing_card_slots.json``：``labels``
固定标签、``slots`` 动态值、``themes`` 各主题的底图与文字色、``air``
空军专用文案（固定文本与坐标）、``options`` 选项文案。坐标以 820x470
的 1 倍设计稿为准，``anchor`` 为 PIL 文本锚点（含 ``m`` 表示以 y 为垂直
中心）；底图为 2 倍图，粘贴时 x/y/字号整体乘以 ``scale``。

用法：
    python assets/build_fishing_card.py            # 仅重新生成底图
    python assets/build_fishing_card.py --demo     # 另存贴好示例值的预览
输出：src/img/fish/fishing_card_<theme>[_air].png、assets/fishing_card_slots.json
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "src" / "fonts" / "HYWenHei-85W.ttf"   # 圆润黑体（中英风格统一）
IMG_DIR = ROOT / "src" / "img" / "fish"
OUT_SVG = ROOT / "assets" / "fishing_card.svg"
OUT_SLOTS = ROOT / "assets" / "fishing_card_slots.json"
DEMO_PNG = ROOT / "card_preview.tmp.png"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ---- 画布与配色（暖色卡通）----
W, H = 820, 480
SCALE = 2                  # 渲染倍率（底图清晰度）
CREAM = "#FFF6E9"          # 卡片底（所有主题一致）
CREAM_SOFT = "#FFFCF6"     # 内容面板底（所有主题一致）
LABEL = "#9A7A5C"          # 标签文字（暖棕）
LINE = "#EFDCC4"           # 分隔线
DASH = "#D7B18C"           # 缩略图占位虚线
WAVE = "#9DB4C0"           # 空军变体：鱼左侧的波浪线（灰蓝，示意水面）
PANEL_LINE = "#F2E4D2"     # 面板描边
BTN_FILL = "#FFF1DC"       # 按钮底
BTN_LINE = "#F3CE96"       # 按钮描边

NAME_COLOR = "#3A2A1C"
SUB_COLOR = "#96785C"
RARITY_COLOR = "#7A4FD0"          # 兜底（未知档位）
# 各稀有度专属色：与卡片主题横幅同色系，但加深以保证奶油底上的可读性
RARITY_COLORS = {
    "普通": "#7B858C",   # 灰白
    "稀有": "#2E7AC4",   # 蓝
    "史诗": "#7A5AC8",   # 紫
    "传说": "#D9862E",   # 橙
    "神话": "#CE4239",   # 红
}
# 各等级专属色：D→X 由冷到暖递进，与稀有度色系区分
GRADE_COLORS = {
    "D": "#8A94A0",      # 灰
    "C": "#3FA76A",      # 绿
    "B": "#2E86C8",      # 蓝
    "A": "#8B5AD0",      # 紫
    "S": "#E08A2A",      # 橙
    "X": "#D6402F",      # 红
}
GRADE_COLOR = "#1D6FB8"
VALUE_COLOR = "#3A2A1C"
OPT_COLORS = ["#D6832A", "#4AA86E", "#C67828"]
BADGE_COLOR = "#2F9E63"
GROWN_COLOR = "#2F9E63"      # 成长后售价/幸运值（收益色）
ORB_COLOR = "#C08A2E"          # 幸运宝珠信息（暖金棕）

# 稀有度主题：仅影响上方横幅（header/dark）与横幅上的文字色（title/tag）
THEMES = {
    "普通": {"key": "normal", "header": "#A9B3BA", "dark": "#98A2A9",
             "title": "#33404A", "tag": "#4A5862"},
    "稀有": {"key": "rare", "header": "#4A90D9", "dark": "#3C7CC0",
             "title": "#FFFFFF", "tag": "#DCEDFB"},
    "史诗": {"key": "epic", "header": "#8B6FD0", "dark": "#7859C0",
             "title": "#FFFFFF", "tag": "#E8DFFA"},
    "传说": {"key": "legendary", "header": "#F6A85C", "dark": "#EF9440",
             "title": "#FFFFFF", "tag": "#FFF1DC"},
    "神话": {"key": "mythic", "header": "#E4574F", "dark": "#D0483F",
             "title": "#FFFFFF", "tag": "#FBDDD9"},
}
DEFAULT_THEME = "传说"      # 未知稀有度回落

# 缩略图粘贴区（1x 设计稿坐标 x, y, w, h）：底图不留框。
# 顶端 y=188 与鱼线末端重合（渲染时鱼图上缘对齐顶端 → 鱼挂在鱼线上）；
# 水平居中于左栏（左栏 0..240，中心 120）；尺寸小于左栏以留出呼吸感
ART_BOX = (40, 182, 160, 102)

# 空军变体：鱼左侧的波浪线（x, y, 宽度；1x）。鱼头朝右游走，波纹留在
# 身后（左栏 0..240，缩略图区自 x=40 起，故波浪线落在左侧留白内）
AIR_WAVES = [(6, 208, 28), (4, 232, 34), (8, 256, 24)]

# 空军专用文案（PIL 粘贴，非底图）。右栏无选项按钮，改为两行固定文案；
# 左栏缩略图下方（原记录徽标位）改为固定文案，用暖赭红区别于好消息的绿
AIR_BADGE = {"x": 122, "y": 408, "anchor": "mm", "size": 12,
             "color": "#B85C42", "text": "鱼线被挣断，鱼逃走了..."}
AIR_TEXTS = [
    {"x": 738, "y": 231, "anchor": "mm", "size": 15,
     "color": LABEL, "text": "很可惜呢，"},
    {"x": 738, "y": 262, "anchor": "mm", "size": 15,
     "color": OPT_COLORS[0], "text": "下次再试试吧"},
]

# 中栏面板（x, y, w, h）
PANELS = [
    (248, 158, 150, 50),   # 稀有度
    (248, 214, 150, 50),   # 等级
    (248, 270, 150, 50),   # 售价
    (248, 326, 150, 50),   # 幸运值
    (248, 384, 150, 82),   # 可成长至（两行）
]
# 装备面板：与左侧信息列等高（158..466），容纳 鱼竿/鱼线/鱼饵/幸运宝珠 四行；
# 右沿 648 与右分界线（658）留 10px 间距，不压线
GEAR_PANEL = (408, 158, 240, 308)
GEAR_ROWS = 4

# 选项按钮中心（1x）与文案（右栏宽度减半）
OPTION_POS = [(738, 187), (738, 247), (738, 307)]
OPTION_W = 140
OPTION_TEXTS = ["卖鱼", "放生", "放入水族箱"]
OPTION_COLOR = {"卖鱼": OPT_COLORS[0], "放生": OPT_COLORS[1], "放入水族箱": OPT_COLORS[2]}

# 固定标签（PIL 粘贴）：key -> (x, y, 字号, 颜色, 文本, 关联字段)
# 关联字段为空时不绘制该标签（如售价 <1万 时无幸运值）
LABELS = {
    "card_tag":  (26, 42, 12, "", "钓 鱼 收 获", None),
    "info_hdr":  (248, 144, 12, LABEL, "鱼 的 信 息", None),
    "gear_hdr":  (408, 144, 12, LABEL, "当 前 装 备", None),
    "opt_hdr":   (668, 144, 12, LABEL, "请 选 择", None),
    "lb_rarity": (260, 183, 13, LABEL, "稀有度", "rarity"),
    "lb_grade":  (260, 239, 13, LABEL, "等级", "grade"),
    "lb_price":  (260, 295, 13, LABEL, "售价", "price"),
    "lb_lucky":  (260, 351, 13, LABEL, "幸运值", "lucky"),
    "lb_grown":  (260, 410, 13, LABEL, "可成长至", "grown_len"),
    "lb_rod":    (422, 204, 13, LABEL, "鱼竿", "gear_rod"),
    "lb_line":   (422, 276, 13, LABEL, "鱼线", "gear_line"),
    "lb_bait":   (422, 348, 13, LABEL, "鱼饵", "gear_bait"),
    "lb_orb":    (422, 420, 13, LABEL, "宝珠", "gear_orb"),
    "lb_remain": (668, 372, 13, LABEL, "今日剩余钓鱼次数", "remaining"),
}

# 动态值槽位：x/y 为 1 倍设计稿坐标，anchor 含 m 表示 y 为垂直中心
SLOTS = {
    "title":      {"x": 26,  "y": 80,  "anchor": "lm", "size": 23, "color": ""},
    "name":       {"x": 122, "y": 356, "anchor": "mm", "size": 21, "color": NAME_COLOR},
    "length":     {"x": 122, "y": 386, "anchor": "mm", "size": 14, "color": SUB_COLOR},
    "badge":      {"x": 122, "y": 408, "anchor": "mm", "size": 12, "color": BADGE_COLOR},
    # 第二条记录（等级与长度同时刷新时各占一行）
    "badge2":     {"x": 122, "y": 428, "anchor": "mm", "size": 12, "color": BADGE_COLOR},
    "rarity":     {"x": 386, "y": 183, "anchor": "rm", "size": 14, "color": RARITY_COLOR},
    "grade":      {"x": 386, "y": 239, "anchor": "rm", "size": 14, "color": GRADE_COLOR},
    "price":      {"x": 386, "y": 295, "anchor": "rm", "size": 15, "color": VALUE_COLOR},
    "lucky":      {"x": 386, "y": 351, "anchor": "rm", "size": 15, "color": VALUE_COLOR},
    "grown_len":  {"x": 260, "y": 432, "anchor": "lm", "size": 15, "color": VALUE_COLOR},
    "grown_info": {"x": 260, "y": 458, "anchor": "lm", "size": 12, "color": GROWN_COLOR},
    "gear_rod":   {"x": 468, "y": 204, "anchor": "lm", "size": 13, "color": VALUE_COLOR},
    "gear_line":  {"x": 468, "y": 276, "anchor": "lm", "size": 13, "color": VALUE_COLOR},
    "gear_bait":  {"x": 468, "y": 348, "anchor": "lm", "size": 13, "color": VALUE_COLOR},
    "gear_orb":   {"x": 468, "y": 420, "anchor": "lm", "size": 13, "color": ORB_COLOR},
    "remaining":  {"x": 680, "y": 414, "anchor": "lm", "size": 15, "color": VALUE_COLOR},
}


def _svg(theme: dict, air: bool = False) -> str:
    """底图 SVG：仅装饰（面板/按钮/虚线框），不含任何文字。

    air=True 生成空军变体：左栏不画鱼线、改画鱼左侧波浪线，右栏不画
    选项按钮（空军无从选择，渲染时在该区域贴固定文案）。
    """
    panels = "\n    ".join(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12"'
        f' fill="{CREAM_SOFT}" stroke="{PANEL_LINE}" stroke-width="1.5"/>'
        for x, y, w, h in PANELS
    )
    buttons = "\n    ".join(
        f'<rect x="{OPTION_POS[0][0] - OPTION_W // 2}" y="{y - 25}"'
        f' width="{OPTION_W}" height="50" rx="14"'
        f' fill="{BTN_FILL}" stroke="{BTN_LINE}" stroke-width="1.5"/>'
        for _, y in OPTION_POS
    )
    gx, gy, gw, gh = GEAR_PANEL
    gear_panel = (
        f'<rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="12"'
        f' fill="{CREAM_SOFT}" stroke="{PANEL_LINE}" stroke-width="1.5"/>'
    )
    row_h = gh / GEAR_ROWS
    gear_lines = "".join(
        f'<line x1="{gx + 14}" y1="{gy + row_h * i:.0f}"'
        f' x2="{gx + gw - 14}" y2="{gy + row_h * i:.0f}"'
        f' stroke="{LINE}" stroke-width="1.5"/>'
        for i in range(1, GEAR_ROWS)
    )
    if air:
        # 鱼已挣脱：不画鱼线，改画其左侧的波浪线（鱼游走后的水面搅动）
        left_art = "\n    ".join(
            f'<path d="M{x},{y} q{w // 4},-8 {w // 2},0 t{w // 2},0"'
            f' fill="none" stroke="{WAVE}" stroke-width="2.5"'
            f' stroke-linecap="round"/>'
            for x, y, w in AIR_WAVES
        )
        right_col = ""
    else:
        # 鱼线：自顶端垂下的虚线 + 顶端小圆（渲染时鱼图上缘对齐线末端）
        left_art = (
            f'<line x1="122" y1="118" x2="122" y2="206"'
            f' stroke="{DASH}" stroke-width="2"/>\n    '
            f'<circle cx="122" cy="118" r="4" fill="{DASH}"/>'
        )
        right_col = buttons
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <!-- 无边框模式：底图铺满整幅，无外圈描边、无投影、无圆角 -->
  <rect x="0" y="0" width="{W}" height="{H}" fill="{CREAM}"/>

  <g>
    <!-- 顶栏：仅此处的配色随稀有度主题变化 -->
    <rect x="0" y="0" width="{W}" height="102" fill="{theme['header']}"/>
    <rect x="0" y="102" width="{W}" height="7" fill="{theme['dark']}"/>
    <circle cx="{W-96}" cy="30" r="20" fill="#FFFFFF" opacity="0.14"/>
    <circle cx="{W-44}" cy="62" r="12" fill="#FFFFFF" opacity="0.16"/>
    <circle cx="{W-142}" cy="66" r="7" fill="#FFFFFF" opacity="0.16"/>

    <!-- 三栏分隔线 -->
    <line x1="240" y1="128" x2="240" y2="468" stroke="{LINE}" stroke-width="2"/>
    <line x1="658" y1="128" x2="658" y2="468" stroke="{LINE}" stroke-width="2"/>

    <!-- ===== 左栏：鱼线 / 空军波浪线（缩略图由渲染时粘贴，底图不留框） ===== -->
    {left_art}

    <!-- ===== 中栏：信息面板（某栏不显示时把该栏文字留空即可） ===== -->
    {panels}
    {gear_panel}
    {gear_lines}

    <!-- ===== 右栏（半宽）：选项按钮 + 剩余次数框 ===== -->
    {right_col}
    <line x1="{OPTION_POS[0][0] - OPTION_W // 2}" y1="356"
          x2="{OPTION_POS[0][0] + OPTION_W // 2}" y2="356"
          stroke="{LINE}" stroke-width="2"/>
    <rect x="{OPTION_POS[0][0] - OPTION_W // 2}" y="390" width="{OPTION_W}"
          height="48" rx="12"
          fill="{CREAM_SOFT}" stroke="{PANEL_LINE}" stroke-width="1.5"/>
  </g>
</svg>'''


def _render(svg: str, out: Path) -> None:
    """SVG → PNG（Chrome 无头）→ 量化压缩"""
    svg_tmp = ROOT / "_card_build.tmp.svg"
    html_tmp = ROOT / "_card_build.tmp.html"
    svg_tmp.write_text(svg, encoding="utf-8")
    html_tmp.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        f'<body style="margin:0"><img src="{svg_tmp.name}" '
        f'width="{W}" height="{H}"></body></html>',
        encoding="utf-8",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--force-device-scale-factor={SCALE}", f"--window-size={W},{H}",
         f"--screenshot={out}", f"file:///{html_tmp.as_posix()}"],
        capture_output=True, check=True,
    )
    svg_tmp.unlink(missing_ok=True)
    html_tmp.unlink(missing_ok=True)

    # 量化压缩：画面以平涂暖色为主，调色板 PNG 体积小且肉眼无损
    from PIL import Image

    with Image.open(out) as img:
        img.convert("P", palette=Image.ADAPTIVE, colors=128).save(
            out, "PNG", optimize=True
        )


def _build_bases() -> None:
    # 设计稿留档用「传说」（橙色）主题
    OUT_SVG.write_text(_svg(THEMES[DEFAULT_THEME]), encoding="utf-8")
    for rarity, theme in THEMES.items():
        _render(_svg(theme), IMG_DIR / f"fishing_card_{theme['key']}.png")
        _render(
            _svg(theme, air=True),
            IMG_DIR / f"fishing_card_{theme['key']}_air.png",
        )


def _write_slots() -> None:
    OUT_SLOTS.write_text(
        json.dumps(
            {
                "canvas": {"w": W, "h": H},
                "scale": SCALE,
                "font": FONT.name,
                "art_box": ART_BOX,
                "default_theme": DEFAULT_THEME,
                "labels": {
                    key: {"x": x, "y": y, "size": size, "color": color,
                          "text": text, "field": field, "anchor": "lm"}
                    for key, (x, y, size, color, text, field) in LABELS.items()
                },
                "slots": SLOTS,
                # 按字段「值」选色（rarity/grade 每个档位一个颜色）
                "value_colors": {
                    "rarity": RARITY_COLORS,
                    "grade": GRADE_COLORS,
                },
                "themes": {
                    rarity: {
                        "image": f"fishing_card_{theme['key']}.png",
                        "air_image": f"fishing_card_{theme['key']}_air.png",
                        "title_color": theme["title"],
                        "tag_color": theme["tag"],
                    }
                    for rarity, theme in THEMES.items()
                },
                # 空军卡片：左栏缩略图下方的固定文案 + 右栏两行固定文案
                # （标题含鱼名，由渲染时按钓中品种生成）
                "air": {
                    "badge": AIR_BADGE,
                    "texts": AIR_TEXTS,
                },
                "options": [
                    {"x": x, "y": y, "anchor": "mm", "text": text,
                     "color": OPTION_COLOR[text]}
                    for (x, y), text in zip(OPTION_POS, OPTION_TEXTS)
                ],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def _demo(rarity: str = "传说") -> None:
    """演示：按槽位表把标签 + 示例值贴在指定主题底图上"""
    from PIL import Image, ImageDraw, ImageFont

    spec = json.loads(OUT_SLOTS.read_text(encoding="utf-8"))
    scale = spec["scale"]
    theme = spec["themes"][rarity]
    cache: dict[int, ImageFont.FreeTypeFont] = {}

    def font(size: int) -> ImageFont.FreeTypeFont:
        px = int(size * scale)
        if px not in cache:
            cache[px] = ImageFont.truetype(str(FONT), px)
        return cache[px]

    img = Image.open(IMG_DIR / theme["image"]).convert("RGB")
    draw = ImageDraw.Draw(img)

    def put(x, y, text, size, color, anchor):
        draw.text((int(x * scale), int(y * scale)), text, fill=color,
                  font=font(size), anchor=anchor)

    values = {
        "title": "你钓到了【暮色鱼】。",
        "name": "暮色鱼",
        "length": "186.4cm（A）",
        "badge": "图鉴已解锁！",
        "rarity": rarity,
        "grade": "A",
        "price": "25310 金币",
        "lucky": "1",
        "grown_len": "279.6cm",
        "grown_info": "40260金币 · 1幸运值",
        "remaining": "7 / 10",
        "orb_energy": "宝珠能量 63/90",
        "orb_info": "幸运宝珠 63/90",
    }
    for key, item in spec["labels"].items():
        field = item["field"]
        if field and not values.get(field):
            continue
        color = item["color"]
        if key == "card_tag":
            color = theme["tag_color"]
        put(item["x"], item["y"], item["text"], item["size"], color,
            item["anchor"])
    value_colors = spec.get("value_colors", {})
    for key, slot in spec["slots"].items():
        text = values.get(key)
        if not text:
            continue
        color = value_colors.get(key, {}).get(text) or slot["color"]             or theme["title_color"]
        put(slot["x"], slot["y"], text, slot["size"], color, slot["anchor"])
    for opt in spec["options"]:
        put(opt["x"], opt["y"], opt["text"], 15, opt["color"], opt["anchor"])
    img.save(DEMO_PNG)
    print(f"预览: {DEMO_PNG}（{rarity}）")


def main() -> None:
    _build_bases()
    _write_slots()
    for rarity, theme in THEMES.items():
        p = IMG_DIR / f"fishing_card_{theme['key']}.png"
        print(f"输出: {p}  {p.stat().st_size/1024:.1f} KB"
              f"  ({W*SCALE}x{H*SCALE})  [{rarity}]")
    print(f"槽位: {OUT_SLOTS}")
    if "--demo" in sys.argv:
        idx = sys.argv.index("--demo")
        rarity = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else DEFAULT_THEME
        _demo(rarity if rarity in THEMES else DEFAULT_THEME)


if __name__ == "__main__":
    sys.exit(main())
