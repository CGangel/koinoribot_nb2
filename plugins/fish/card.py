"""钓鱼响应卡片渲染（暖色卡通底图 + PIL 粘贴文本）。

底图与槽位表由 ``assets/build_fishing_card.py`` 生成，见
``assets/fishing_card_slots.json``。本模块只负责「把一次钓获的数据贴到
底图上」，是唯一读取槽位表的地方；坐标以槽位表的 1 倍设计稿为准，绘制
时统一乘以 ``scale``。

底图为单一布局 + 5 个稀有度主题（仅上方横幅配色不同）：普通-灰白、
稀有-蓝、史诗-紫、传说-橙、神话-红。某栏无需显示时（如售价 <1万 的鱼
无幸运值、无放生）不粘贴该栏文字即可，其余栏位不受影响。

空军（鱼逃走）用同布局的**空军底图变体**：左栏不画鱼线、改画鱼左侧的
波浪线，右栏不画选项按钮（改为固定文案“很可惜呢，下次再试试吧”）；
中栏信息与正常钓获一致，左栏缩略图下方原记录徽标位改贴固定文案。
统一由 ``air=True`` 切换。

渲染失败（缺底图/字体、PIL 不可用等）返回 None，由调用方回退到文本。
"""

import json
from pathlib import Path
from typing import Optional

from nonebot.log import logger

ROOT = Path(__file__).resolve().parents[2]
SLOTS_PATH = ROOT / "assets" / "fishing_card_slots.json"
IMG_DIR = ROOT / "src" / "img" / "fish"
FONT_DIR = ROOT / "src" / "fonts"

_spec: Optional[dict] = None
_font_cache: dict[int, "object"] = {}
_base_cache: dict[str, "object"] = {}


def _load_spec() -> Optional[dict]:
    global _spec
    if _spec is None:
        try:
            _spec = json.loads(SLOTS_PATH.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(f"读取钓鱼卡片槽位表失败: {error}")
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
        _base_cache[image_name] = Image.open(
            IMG_DIR / image_name
        ).convert("RGB")
    return _base_cache[image_name]


def _fit_art(art_bytes: bytes, box: tuple[int, int, int, int], scale: int):
    """把鱼的插画等比缩放进粘贴区，水平居中、顶端对齐。
    顶端对齐使鱼上缘紧贴鱼线末端（鱼像挂在鱼线上），避免宽扁的鱼
    与鱼线之间出现空隙。"""
    from PIL import Image

    import io

    with Image.open(io.BytesIO(art_bytes)) as art:
        art = art.convert("RGBA")
        # 裁掉插画四周的透明留白，让鱼身尽量占满粘贴区
        bbox = art.split()[3].getbbox()
        if bbox:
            art = art.crop(bbox)
        bx, by, bw, bh = (v * scale for v in box)
        # 等比缩放到尽量占满粘贴区（thumbnail 只缩小不放大，故显式算比例）
        factor = min(bw / art.width, bh / art.height)
        art = art.resize(
            (max(1, round(art.width * factor)), max(1, round(art.height * factor))),
            Image.LANCZOS,
        )
        x = bx + (bw - art.width) // 2
        y = by                            # 顶端对齐：上缘贴住鱼线末端
        return art, (x, y)


def _gear_text(item: dict, with_left: bool = True) -> str:
    """装备一行：名称（剩余/上限 或 ∞）"""
    if not item:
        return ""
    name = item.get("name", "")
    if not with_left:
        return name
    left = item.get("left")
    if left is None:
        return f"{name}（无耐久）"
    if left == "∞":
        return f"{name}（无限）"
    # 鱼竿/鱼线显示耐久 已用/上限，鱼饵显示剩余个数
    if item.get("max") is None:
        return f"{name}（剩余 {left} 个）"
    return f"{name}（{left}/{item['max']}）"


def build_fields(
    result: dict, remaining, total, badge: str = "", orb: dict = None,
    gear: dict = None, badge2: str = "",
) -> dict:
    """把 do_cast 结果整理成卡片字段（值为 None/空则跳过该槽位）"""
    grown_info = f"{result['grown_sell_price']}金币"
    if result["grown_lucky_value"] >= 1:
        grown_info += f" · {result['grown_lucky_value']}幸运值"
    air = bool(result.get("air"))
    fields = {
        # 空军：鱼已挣脱，标题点明结局（品种仍在缩略图下方显示）
        "title": (
            f"鱼线被挣断了，{result['species_name']}逃走了。"
            if air else f"你钓到了【{result['species_name']}】。"
        ),
        "name": result["species_name"],
        "length": f"{_fmt_len(result['length'])}cm（{result['grade']}）",
        "badge": badge,
        "badge2": badge2,
        "rarity": result["rarity"],
        "grade": result["grade"],
        "price": f"{result['sell_price']} 金币",
        # 幸运值 <1（售价 <1万）：该栏与放生选项都不显示
        "lucky": str(result["lucky_value"]) if result["lucky_value"] >= 1 else "",
        "grown_len": f"{_fmt_len(result['growth_cap'])}cm",
        "grown_info": grown_info,
        "remaining": "∞" if total is None else f"{remaining} / {total}",
        # 自动处理的鱼：右侧选项区改为已自动售出/已自动入箱
        "auto_sold": bool(result.get("auto_sold")),
        "auto_tank": bool(result.get("auto_tank")),
        "air": air,
        "gear_rod": "",
        "gear_line": "",
        "gear_bait": "",
        "gear_orb": "",
    }
    if gear:
        rod, line, bait = gear.get("rod"), gear.get("line"), gear.get("bait")
        fields["gear_rod"] = _gear_text(rod)
        fields["gear_line"] = _gear_text(line)
        fields["gear_bait"] = _gear_text(bait)
    # 幸运宝珠（装备区第四行）：本体暴击 > 能量已满 > 能量积攒 > 未拥有
    if not orb:
        fields["gear_orb"] = "未拥有"
    elif orb.get("lucky_cast"):
        fields["gear_orb"] = (
            f"本次触发幸运暴击！"
        )
    elif orb.get("just_full"):
        fields["gear_orb"] = f"能量已满，下次将触发幸运暴击"
    else:
        fields["gear_orb"] = f"Lv.{orb['level']} 能量 {orb['energy']}/{orb['cap']}"
    return fields


def _fmt_len(value: float) -> str:
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def render(
    fields: dict,
    rarity: str,
    art_bytes: Optional[bytes] = None,
    air: Optional[bool] = None,
) -> Optional[bytes]:
    """按字段与稀有度主题渲染卡片 PNG；失败返回 None。

    air 为 True 时用空军底图变体（无鱼线/无选项按钮）并贴空军固定文案；
    默认取 ``fields["air"]``（build_fields 已写入），便于调用方直接透传。
    """
    import io

    from PIL import Image, ImageDraw

    spec = _load_spec()
    if spec is None:
        return None
    if air is None:
        air = bool(fields.get("air"))
    themes = spec["themes"]
    theme = themes.get(rarity) or themes[spec["default_theme"]]
    try:
        scale = spec["scale"]
        img = _base(theme["air_image" if air else "image"]).copy()
        draw = ImageDraw.Draw(img)
        art_box = spec["art_box"]
        if art_bytes:
            # 底图不留缩略图框：鱼图等比缩放后水平居中、底边对齐粘贴区底边，
            # 靠左栏中线（鱼线末端）对齐
            art, pos = _fit_art(art_bytes, tuple(art_box), scale)
            img.paste(art, pos, art)

        def put(x, y, text, size, color, anchor):
            draw.text((int(x * scale), int(y * scale)), text, fill=color,
                      font=_font(int(size * scale)), anchor=anchor)

        # 固定标签：关联字段为空（该栏不显示）时跳过；
        # 自动处理（已自动售出/已自动入箱）与空军时“请选择”表头不显示
        auto_state = (
            "已自动售出" if fields.get("auto_sold")
            else ("已放入水族箱" if fields.get("auto_tank") else "")
        )
        for key, item in spec["labels"].items():
            if (auto_state or air) and key == "opt_hdr":
                continue
            field = item.get("field")
            if field and not fields.get(field):
                continue
            color = theme["tag_color"] if key == "card_tag" else item["color"]
            put(item["x"], item["y"], item["text"], item["size"], color,
                item["anchor"])

        # 空军：缩略图下方原记录徽标位改贴固定文案，跳过图鉴/记录徽标
        if air:
            item = spec["air"]["badge"]
            put(item["x"], item["y"], item["text"], item["size"],
                item["color"], item["anchor"])

        # 动态值：空值（该栏不显示）时跳过
        # 稀有度/等级按各自档位取专属色（value_colors）
        value_colors = spec.get("value_colors", {})
        for key, slot in spec["slots"].items():
            if air and key in ("badge", "badge2"):
                continue
            text = fields.get(key)
            if not text:
                continue
            color = (
                value_colors.get(key, {}).get(text)
                or slot["color"]
                or theme["title_color"]
            )
            put(slot["x"], slot["y"], text, slot["size"], color, slot["anchor"])

        # 选项区：空军时无选项（右栏底图也不画按钮），改贴固定文案；
        # 自动处理（已自动售出/已自动入箱）时右侧整块改为对应文案；
        # 放生仅在可放生（幸运值 ≥1）时出现，出现时略微上移，
        # 下方附一行小号绿字“获得N枚幸运币”
        if air:
            for item in spec["air"]["texts"]:
                put(item["x"], item["y"], item["text"], item["size"],
                    item["color"], item["anchor"])
        elif auto_state:
            sold = spec["options"][0]
            middle = spec["options"][1]
            put(middle["x"], middle["y"], auto_state, 15,
                sold["color"], sold["anchor"])
        else:
            for opt in spec["options"]:
                if opt["text"] == "放生" and not fields.get("lucky"):
                    continue
                if opt["text"] == "放生":
                    y = opt["y"] - 9
                    put(opt["x"], y, opt["text"], 15, opt["color"], opt["anchor"])
                    put(opt["x"], y + 18, f"获得{fields['lucky']}枚幸运币",
                        11, opt["color"], opt["anchor"])
                    continue
                put(opt["x"], opt["y"], opt["text"], 15, opt["color"], opt["anchor"])

        # 体积控制：底图为 128 色 palette（~14KB），但贴上全彩插画与抗锯齿
        # 文字后真彩 PNG 会膨胀到 ~140KB。故先降采样到 1 倍设计尺寸
        # （2x 渲染再加抗锯齿降采样，比直接 1x 绘制更锐利），再量化为
        # 128 色调色板 → 约 32KB，画质无肉眼损失。
        canvas = spec["canvas"]
        out = img.resize((int(canvas["w"]), int(canvas["h"])), Image.LANCZOS)
        buf = io.BytesIO()
        out.convert("P", palette=Image.ADAPTIVE, colors=128).save(
            buf, format="PNG", optimize=True
        )
        return buf.getvalue()
    except Exception as error:
        logger.warning(f"渲染钓鱼卡片失败: {error}")
        return None
