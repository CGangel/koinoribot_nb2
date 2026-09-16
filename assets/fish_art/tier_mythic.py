"""神话稀有度（4 种）：灵光、幻境、月辉与琉璃系。

- 精灵鱼 青碧身 + 双光环环带 + 灵光点
- 幻境星鱼 紫身 + 拖影残像 + 星芒
- 银月皇鱼 银白身 + 月牙鳞冠 + 冠珠
- 琉璃幻彩鱼 晶体感身体 + 多色琉璃棱面 + 外发光
"""

from .common import eye, gloss, sparkle, svg


def draw_spirit_fish() -> str:
    """精灵鱼：青碧圆身，身后两道金色光环节，环绕灵光点"""
    return svg(f'''
  <!-- 双光环 -->
  <ellipse cx="132" cy="134" rx="96" ry="62" fill="none"
           stroke="#FFF0A8" stroke-width="4" opacity="0.75"/>
  <ellipse cx="132" cy="134" rx="84" ry="50" fill="none"
           stroke="#FFE68A" stroke-width="2.6" opacity="0.6"/>
  <ellipse cx="132" cy="134" rx="72" ry="52" fill="#A8E8D8"/>
  <ellipse cx="132" cy="140" rx="64" ry="44" fill="#C4F2E6"/>
  <!-- 灵光纹 -->
  <g stroke="#FFFFFF" stroke-width="3" fill="none" opacity="0.7"
     stroke-linecap="round">
    <path d="M96 118 q18 -10 36 0"/><path d="M92 138 q20 -10 40 0"/>
  </g>
  {sparkle(104, 106, 13, "#FFF6C0", 0.95)}
  {sparkle(160, 146, 11, "#FFF6C0", 0.9)}
  {sparkle(170, 110, 9, "#FFF6C0", 0.9)}
  {sparkle(120, 166, 7, "#FFF6C0", 0.85)}
  <!-- 晶状尾 -->
  <path d="M60 134 L20 100 Q34 134 20 168 Z" fill="#7CCCB8"/>
  <path d="M60 134 L32 116 Q44 134 32 152 Z" fill="#A8E8D8" opacity="0.8"/>
  {eye(172, 126, 10)}
  <path d="M188 146 q7 6 3 13" stroke="#2E7563" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(110, 110, 32, 15, 0.4)}
''')


def draw_phantom_star_fish() -> str:
    """幻境星鱼：紫色身，身后两层拖影残像，体表星芒"""
    return svg(f'''
  <!-- 拖影 -->
  <ellipse cx="152" cy="128" rx="70" ry="44" fill="#B8AEE8" opacity="0.28"/>
  <ellipse cx="140" cy="132" rx="74" ry="48" fill="#9A8EDC" opacity="0.4"/>
  <ellipse cx="128" cy="134" rx="76" ry="50" fill="#7A6ED0"/>
  <ellipse cx="128" cy="140" rx="68" ry="42" fill="#958AE0"/>
  {sparkle(110, 112, 12, "#FFFFFF", 0.95)}
  {sparkle(150, 144, 10, "#FFFFFF", 0.9)}
  {sparkle(164, 110, 8, "#FFF0A8", 0.9)}
  {sparkle(98, 152, 7, "#FFF0A8", 0.85)}
  <g fill="#FFFFFF" opacity="0.85">
    <circle cx="132" cy="106" r="2.4"/><circle cx="168" cy="132" r="2.2"/>
  </g>
  <path d="M56 134 L18 104 Q32 134 18 164 Z" fill="#5A4EA8"/>
  {eye(168, 126, 10)}
  <path d="M184 146 q7 6 3 13" stroke="#E4DEF8" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(108, 112, 32, 14, 0.3)}
''')


def draw_silver_moon_emperor_fish() -> str:
    """银月皇鱼：银白威严身，额顶三枚月牙鳞冠，颔下珠饰"""
    return svg(f'''
  <ellipse cx="130" cy="136" rx="78" ry="54" fill="#C8D0DC"/>
  <ellipse cx="130" cy="142" rx="70" ry="46" fill="#E0E6EE"/>
  <ellipse cx="130" cy="136" rx="78" ry="54" fill="none"
           stroke="#A0AAB8" stroke-width="3"/>
  <!-- 月牙鳞纹 -->
  <g fill="none" stroke="#A8B2C0" stroke-width="2.6" opacity="0.8">
    <path d="M92 132 a10 10 0 0 1 -12 14"/>
    <path d="M114 128 a10 10 0 0 1 -12 14"/>
    <path d="M136 128 a10 10 0 0 1 -12 14"/>
    <path d="M158 132 a10 10 0 0 1 -12 14"/>
    <path d="M104 156 a9 9 0 0 1 -11 12"/>
    <path d="M126 156 a9 9 0 0 1 -11 12"/>
    <path d="M148 156 a9 9 0 0 1 -11 12"/>
  </g>
  <!-- 月牙冠 -->
  <path d="M108 80 a20 20 0 1 0 4 32 a15 15 0 1 1 -4 -32 z"
        fill="#F6E8A8" stroke="#D8C46E" stroke-width="2"/>
  <path d="M142 76 a17 17 0 1 0 4 27 a13 13 0 1 1 -4 -27 z"
        fill="#FBF2C8" stroke="#D8C46E" stroke-width="2"/>
  <!-- 珠饰 -->
  <circle cx="128" cy="190" r="6" fill="#F6E8A8" stroke="#D8C46E" stroke-width="2"/>
  <path d="M54 136 L18 110 Q32 136 18 162 Z" fill="#A0AAB8"/>
  {eye(170, 128, 10, "#2A3440")}
  <path d="M186 148 q7 6 3 13" stroke="#4A5460" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(108, 114, 34, 15, 0.45)}
''')


def draw_glass_fantasy_fish() -> str:
    """琉璃幻彩鱼：外发光晶体身，覆多色琉璃棱面，边缘透亮"""
    return svg(f'''
  <ellipse cx="132" cy="134" rx="102" ry="66" fill="none"
           stroke="#E8FAFF" stroke-width="3" opacity="0.55"/>
  <path d="M210 130 Q198 92 152 90 Q94 88 58 122 Q56 154 100 174
           Q158 180 202 162 Q210 146 210 130 Z" fill="#B8E8F2"/>
  <path d="M210 130 Q198 92 152 90 Q128 89 106 96 Q160 108 166 132
           Q162 158 106 166 Q130 174 154 172 Q202 162 210 130 Z"
        fill="#8CCCD8" opacity="0.42"/>
  <!-- 琉璃棱面 -->
  <path d="M90 106 L148 98 L176 122 L110 128 Z" fill="#F2B8E8" opacity="0.72"/>
  <path d="M84 132 L150 128 L172 154 L102 160 Z" fill="#B8F2D8" opacity="0.72"/>
  <path d="M104 160 L158 156 L170 170 L116 174 Z" fill="#F2E8A8" opacity="0.75"/>
  <path d="M96 104 L150 116" stroke="#FFFFFF" stroke-width="2.4"
        fill="none" opacity="0.7"/>
  <path d="M90 132 L152 146" stroke="#FFFFFF" stroke-width="2.4"
        fill="none" opacity="0.65"/>
  <!-- 高光棱点 -->
  {sparkle(122, 108, 9, "#FFFFFF", 0.95)}
  {sparkle(176, 136, 7, "#FFFFFF", 0.9)}
  <path d="M58 130 L20 100 Q34 130 20 160 Z" fill="#8CCCD8"/>
  {eye(180, 120, 10)}
  <path d="M196 140 q7 6 3 13" stroke="#2E7C90" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(124, 102, 34, 11, 0.5)}
''')


DRAWERS = {
    "spirit_fish": draw_spirit_fish,
    "phantom_star_fish": draw_phantom_star_fish,
    "silver_moon_emperor_fish": draw_silver_moon_emperor_fish,
    "glass_fantasy_fish": draw_glass_fantasy_fish,
}
