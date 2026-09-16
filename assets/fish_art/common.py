"""钓鱼卡片用鱼插画的公共构件与绘制入口。

每条鱼一个独立函数（见 common / tier_*.py 的 draw_*），各自定义身体轮廓、
鳍型与标志装饰，互不复用骨架，保证造型辨识度。本模块提供：

- 复用构件：eye / gloss / sparkle / bubble / scallop / fin 等
- svg(inner)：包成 256x256 透明底 SVG
- ALL_DRAWERS：species_id -> 绘制函数（由 tier 模块汇总）
"""

S = 256


# ===== 复用构件 =====

def eye(cx, cy, r=9, iris="#20303C"):
    """眼白 + 眼珠 + 高光"""
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFFFFF"/>'
        f'<circle cx="{cx + r * 0.26:.0f}" cy="{cy + r * 0.12:.0f}"'
        f' r="{r * 0.56:.1f}" fill="{iris}"/>'
        f'<circle cx="{cx + r * 0.5:.0f}" cy="{cy - r * 0.36:.0f}"'
        f' r="{r * 0.2:.1f}" fill="#FFFFFF"/>'
    )


def gloss(cx, cy, rx, ry, op=0.4):
    """体表柔和高光"""
    return (f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}"'
            f' fill="#FFFFFF" opacity="{op}"/>')


def sparkle(cx, cy, r, color="#FFFFFF", op=0.95):
    """四芒星"""
    k = round(r * 0.26, 1)
    return (
        f'<path d="M{cx} {cy - r} L{cx + k} {cy - k} L{cx + r} {cy}'
        f' L{cx + k} {cy + k} L{cx} {cy + r} L{cx - k} {cy + k}'
        f' L{cx - r} {cy} L{cx - k} {cy - k} Z" fill="{color}" opacity="{op}"/>'
    )


def bubble(cx, cy, r, color="#FFFFFF", w=2.4, op=0.9):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none"'
            f' stroke="{color}" stroke-width="{w}" opacity="{op}"/>')


def scallop(cx, cy, r, n=8, color="#FFFFFF", op=0.9):
    """花瓣/波浪圈（云朵软糖、樱吹雪等用）"""
    import math
    out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        out.append(f'<circle cx="{cx + r * math.cos(a):.1f}"'
                   f' cy="{cy + r * math.sin(a):.1f}" r="{r * 0.55:.1f}"'
                   f' fill="{color}" opacity="{op}"/>')
    return "".join(out)


def mouth(cx, cy, color="#20303C", w=2.6):
    return (f'<path d="M{cx} {cy} q7 6 3 13" stroke="{color}" stroke-width="{w}"'
            f' fill="none" stroke-linecap="round"/>')


def svg(inner, defs=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}"'
            f' viewBox="0 0 {S} {S}"><defs>{defs}</defs>{inner}</svg>')


def gradient(gid, c1, c2, vertical=True):
    x2, y2 = ("0", "1") if vertical else ("1", "0")
    return (f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">'
            f'<stop offset="0" stop-color="{c1}"/>'
            f'<stop offset="1" stop-color="{c2}"/></linearGradient>')


def soft_gloss():
    """可复用的顶部光泽渐变定义"""
    return ('<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.5"/>'
            '<stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0.05"/>'
            '<stop offset="1" stop-color="#000000" stop-opacity="0.08"/>'
            '</linearGradient>')
