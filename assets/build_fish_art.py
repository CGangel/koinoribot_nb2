"""为 36 种鱼各生成一张透明背景 PNG 插画（贴到钓鱼卡片左栏）。

每条鱼都有独立的绘制函数（assets/fish_art/tier_*.py），各自定义身体轮廓、
鳍型与标志装饰，互不复用骨架，以保证造型辨识度与美观度。

用法：
    python assets/build_fish_art.py             # 生成全部
    python assets/build_fish_art.py 普通 稀有    # 只生成指定稀有度
    python assets/build_fish_art.py --sheet     # 另存九宫格对比图
输出：src/img/fish/art/<species_id>.png（透明背景，256x256）
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "assets"))

from fish_art import (  # noqa: E402
    tier_epic,
    tier_legendary,
    tier_mythic,
    tier_normal,
    tier_rare,
)

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT_DIR = ROOT / "src" / "img" / "fish" / "art"
SHEET = ROOT / "fish_sheet.tmp.png"

S = 256

# 稀有度 → 该档绘制函数表（后续档位接入后合并）
TIERS = {
    "普通": tier_normal.DRAWERS,
    "稀有": tier_rare.DRAWERS,
    "史诗": tier_epic.DRAWERS,
    "传说": tier_legendary.DRAWERS,
    "神话": tier_mythic.DRAWERS,
}


def all_drawers() -> dict:
    out = {}
    for drawers in TIERS.values():
        out.update(drawers)
    return out


def _render(name: str, inner_svg: str, out: Path) -> None:
    svg_tmp = ROOT / "_art_build.tmp.svg"
    html_tmp = ROOT / "_art_build.tmp.html"
    svg_tmp.write_text(inner_svg, encoding="utf-8")
    html_tmp.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<style>html,body{margin:0;background:transparent}</style></head>'
        f'<body><img src="{svg_tmp.name}" width="{S}" height="{S}"></body></html>',
        encoding="utf-8",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--default-background-color=00000000",
         f"--window-size={S},{S}", f"--screenshot={out}",
         f"file:///{html_tmp.as_posix()}"],
        capture_output=True, check=True,
    )
    svg_tmp.unlink(missing_ok=True)
    html_tmp.unlink(missing_ok=True)

    from PIL import Image
    with Image.open(out) as img:
        img.convert("RGBA").save(out, "PNG", optimize=True)


def _sheet(drawers: dict) -> None:
    """生成对比图，便于逐条检查造型"""
    import math

    from PIL import Image, ImageDraw, ImageFont
    from config_store import _FISH_SPECIES_DEFAULT

    ids = list(drawers)
    cols, cell, pad = 6, 190, 12
    rows = math.ceil(len(ids) / cols)
    sheet = Image.new("RGB", (cols * (cell + pad) + pad,
                              rows * (cell + pad) + pad), (255, 246, 233))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(ROOT / "src" / "fonts" / "HYWenHei-85W.ttf"), 15)
    for i, sid in enumerate(ids):
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), pad + r * (cell + pad)
        art = Image.open(OUT_DIR / f"{sid}.png").convert("RGBA")
        art.thumbnail((cell, cell - 24), Image.LANCZOS)
        sheet.paste(art, (x + (cell - art.width) // 2, y + (cell - 24 - art.height) // 2), art)
        name = _FISH_SPECIES_DEFAULT.get(sid, {}).get("name", sid)
        d.text((x + cell / 2, y + cell - 18), name, font=font,
               fill=(58, 42, 28), anchor="mm")
    sheet.save(SHEET)
    print(f"对比图: {SHEET}")


def main() -> int:
    drawers = all_drawers()
    if not drawers:
        print("没有可生成的绘制函数")
        return 1

    from config_store import _FISH_SPECIES_DEFAULT
    unknown = set(drawers) - set(_FISH_SPECIES_DEFAULT)
    if unknown:
        print(f"绘制函数存在未知品种: {sorted(unknown)}")
        return 1

    total = 0
    for sid, drawer in drawers.items():
        out = OUT_DIR / f"{sid}.png"
        _render(sid, drawer(), out)
        total += out.stat().st_size
    print(f"输出 {len(drawers)} 张 → {OUT_DIR}  合计 {total / 1024:.1f} KB")
    if "--sheet" in sys.argv:
        _sheet(drawers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
