"""史诗稀有度（8 种）：季节、天色与幻彩系。

- 白涟鱼 素白长身 + 水波涟漪纹   - 夏影鱼 宽叶身 + 树影斑驳
- 秋汐鱼 鳞片层叠身 + 落叶片      - 冬萤鱼 深蓝身 + 萤火光点
- 春绯鱼 花瓣身（瓣尖分开）+ 落瓣 - 霞光鱼 修长身 + 层叠霞光带
- 虹彩鱼 流线身 + 三色虹带棱面    - 暮色鱼 圆身 + 黄昏渐变 + 星点
"""

from .common import eye, gloss, mouth, scallop, svg, sparkle


def draw_white_ripple_fish() -> str:
    """白涟鱼：素白修长身，体表同心涟漪波纹，尾鳍薄纱"""
    return svg(f'''
  <path d="M212 130 Q200 92 154 90 Q96 88 58 122 Q56 152 100 172
           Q158 178 202 162 Q210 146 212 130 Z" fill="#E8F2F8"/>
  <path d="M212 130 Q200 92 154 90 Q130 89 108 96 Q160 108 166 132
           Q162 158 108 166 Q132 174 158 172 Q202 162 212 130 Z"
        fill="#CFE2EE" opacity="0.6"/>
  <!-- 涟漪 -->
  <g stroke="#B6D2E4" stroke-width="2.8" fill="none" opacity="0.85"
     stroke-linecap="round">
    <path d="M92 116 q14 -8 28 0 q14 8 28 0"/>
    <path d="M84 134 q16 -8 32 0 q16 8 32 0"/>
    <path d="M92 152 q14 -8 28 0 q14 8 28 0"/>
  </g>
  <path d="M58 130 L22 104 Q36 130 22 156 Z" fill="#CFE2EE"/>
  <path d="M58 130 L30 116 Q40 130 30 144 Z" fill="#B6D2E4" opacity="0.7"/>
  {eye(178, 122, 10)}
  <path d="M194 140 q7 6 3 13" stroke="#4E7186" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(120, 106, 38, 12, 0.5)}
''')


def draw_summer_shade_fish() -> str:
    """夏影鱼：宽大叶形身，身上叶影斑驳，尾鳍似叶尖"""
    return svg(f'''
  <path d="M204 130 Q176 84 122 84 Q72 84 54 130 Q72 176 122 176
           Q176 176 204 130 Z" fill="#4FA88A"/>
  <path d="M204 130 Q176 84 122 84 Q92 84 72 98 Q134 108 142 130
           Q134 152 72 162 Q92 176 122 176 Q176 176 204 130 Z"
        fill="#37785F" opacity="0.45"/>
  <!-- 叶影斑驳 -->
  <g fill="#2C6349" opacity="0.5">
    <ellipse cx="104" cy="112" rx="14" ry="8" transform="rotate(-24 104 112)"/>
    <ellipse cx="148" cy="140" rx="16" ry="9" transform="rotate(18 148 140)"/>
    <ellipse cx="98" cy="152" rx="12" ry="7" transform="rotate(12 98 152)"/>
  </g>
  <g fill="#8FD4B8" opacity="0.75">
    <ellipse cx="128" cy="104" rx="12" ry="7" transform="rotate(16 128 104)"/>
    <ellipse cx="116" cy="146" rx="13" ry="7" transform="rotate(-14 116 146)"/>
  </g>
  <path d="M54 130 L16 100 Q32 130 16 160 Z" fill="#37785F"/>
  {eye(172, 120, 10)}
  <path d="M188 140 q7 6 3 13" stroke="#123B2C" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(126, 102, 34, 12, 0.32)}
''')


def draw_autumn_tide_fish() -> str:
    """秋汐鱼：鳞片层叠的椭圆身，背上一片落叶，暖橙配色"""
    return svg(f'''
  <ellipse cx="130" cy="132" rx="74" ry="50" fill="#D98A3C"/>
  <ellipse cx="130" cy="138" rx="66" ry="42" fill="#E8A055"/>
  <!-- 层叠鳞片 -->
  <g fill="none" stroke="#B0672A" stroke-width="2.4" opacity="0.7">
    <path d="M86 118 q10 -9 20 0"/><path d="M106 118 q10 -9 20 0"/>
    <path d="M126 118 q10 -9 20 0"/><path d="M146 118 q10 -9 20 0"/>
    <path d="M76 136 q10 -9 20 0"/><path d="M96 136 q10 -9 20 0"/>
    <path d="M116 136 q10 -9 20 0"/><path d="M136 136 q10 -9 20 0"/>
    <path d="M156 136 q10 -9 20 0"/>
    <path d="M86 154 q10 -9 20 0"/><path d="M106 154 q10 -9 20 0"/>
    <path d="M126 154 q10 -9 20 0"/><path d="M146 154 q10 -9 20 0"/>
  </g>
  <!-- 落叶 -->
  <path d="M138 76 q22 -10 34 6 q-14 16 -34 6 q-10 -6 0 -12 z" fill="#C25A2A"/>
  <path d="M138 82 L172 82" stroke="#8F3E1A" stroke-width="2"
        fill="none" opacity="0.7"/>
  <path d="M58 132 L22 106 Q36 132 22 158 Z" fill="#B0672A"/>
  {eye(176, 124, 10)}
  <path d="M192 144 q7 6 3 13" stroke="#6B3410" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(104, 112, 32, 14, 0.36)}
''')


def draw_winter_firefly_fish() -> str:
    """冬萤鱼：深蓝夜空色身体，腹部一串萤火微光，尾鳍细长"""
    return svg(f'''
  <path d="M208 128 Q196 90 152 88 Q96 86 60 120 Q58 150 100 170
           Q156 176 198 160 Q206 144 208 128 Z" fill="#4E5E96"/>
  <path d="M208 128 Q196 90 152 88 Q128 87 106 94 Q158 106 164 130
           Q160 156 106 164 Q130 172 156 170 Q198 160 208 128 Z"
        fill="#3A4874" opacity="0.6"/>
  <!-- 萤火微光 -->
  {sparkle(104, 118, 11, "#FFF0A8", 0.95)}
  {sparkle(140, 144, 9, "#FFF0A8", 0.9)}
  {sparkle(170, 112, 7, "#FFF0A8", 0.88)}
  {sparkle(120, 156, 6, "#FFF0A8", 0.8)}
  <g fill="#FFF6C0" opacity="0.85">
    <circle cx="104" cy="118" r="3.2"/><circle cx="140" cy="144" r="2.8"/>
    <circle cx="170" cy="112" r="2.4"/>
  </g>
  <path d="M60 128 L22 100 Q38 128 22 156 Z" fill="#3A4874"/>
  {eye(178, 120, 10)}
  <path d="M194 140 q7 6 3 13" stroke="#C8D4F0" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(120, 104, 34, 12, 0.28)}
''')


def draw_spring_crimson_fish() -> str:
    """春绯鱼：绯色鱼身，背鳍与尾鳍化作花瓣形，体侧点缀花瓣纹"""
    return svg(f'''
  <!-- 花瓣状背鳍 -->
  <g fill="#F9A9BC">
    <path d="M104 96 q-2 -30 22 -34 q4 22 -8 34 z"/>
    <path d="M128 90 q4 -30 28 -28 q0 22 -14 32 z"/>
  </g>
  <!-- 花瓣状尾鳍 -->
  <g fill="#EE7C96">
    <path d="M62 132 q-28 -22 -30 -38 q22 4 30 20 z"/>
    <path d="M62 132 q-30 -4 -38 -18 q20 -6 38 4 z"/>
    <path d="M62 132 q-28 22 -30 38 q22 -4 30 -20 z"/>
    <path d="M62 132 q-30 4 -38 18 q20 6 38 -4 z"/>
  </g>
  <!-- 鱼身 -->
  <path d="M198 132 Q190 88 142 86 Q90 84 62 126 Q72 180 136 184
           Q192 182 198 132 Z" fill="#E8748C"/>
  <path d="M198 132 Q190 88 142 86 Q120 86 102 94 Q160 106 166 132
           Q160 162 102 172 Q120 182 136 184 Q192 182 198 132 Z"
        fill="#F7A6BA" opacity="0.55"/>
  <!-- 体侧花瓣纹 -->
  <g fill="#FFD9E2" opacity="0.85">
    <ellipse cx="108" cy="118" rx="11" ry="7" transform="rotate(-22 108 118)"/>
    <ellipse cx="146" cy="150" rx="10" ry="6" transform="rotate(20 146 150)"/>
  </g>
  {eye(166, 120, 10)}
  <path d="M182 140 q7 6 3 13" stroke="#A83E58" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(112, 106, 30, 13, 0.4)}
''')


def draw_sunset_glow_fish() -> str:
    """霞光鱼：修长身，体侧三道层叠霞光带（橙红黄），尾鳍宽大"""
    return svg(f'''
  <path d="M210 130 Q200 94 156 92 Q100 88 60 120 Q58 152 102 172
           Q158 178 202 162 Q208 146 210 130 Z" fill="#F08A4B"/>
  <path d="M210 130 Q200 94 156 92 Q130 91 108 98 Q160 110 166 132
           Q162 158 108 166 Q132 174 156 172 Q202 162 210 130 Z"
        fill="#D06A2E" opacity="0.45"/>
  <!-- 霞光带 -->
  <g stroke-linecap="round" fill="none">
    <path d="M84 108 q40 -8 92 -4" stroke="#FFE0B8" stroke-width="8" opacity="0.85"/>
    <path d="M78 128 q46 -8 100 -2" stroke="#FFC98C" stroke-width="9" opacity="0.8"/>
    <path d="M84 150 q40 -6 92 -2" stroke="#F7A860" stroke-width="8" opacity="0.75"/>
  </g>
  <path d="M60 130 L18 98 Q36 130 18 162 Z" fill="#D06A2E"/>
  <path d="M60 130 L30 114 Q42 130 30 146 Z" fill="#E89050" opacity="0.8"/>
  {eye(176, 122, 10)}
  <path d="M192 142 q7 6 3 13" stroke="#8A3F10" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(124, 106, 34, 11, 0.4)}
''')


def draw_iridescent_fish() -> str:
    """虹彩鱼：流线身覆三片虹彩棱面（粉/蓝/黄），尾鳍展开"""
    return svg(f'''
  <path d="M206 130 Q196 92 152 90 Q96 88 58 120 Q56 152 100 172
           Q156 178 198 162 Q204 146 206 130 Z" fill="#7AD0C4"/>
  <path d="M206 130 Q196 92 152 90 Q130 89 110 96 Q158 108 164 132
           Q160 158 110 166 Q132 174 154 172 Q198 162 206 130 Z"
        fill="#5FB8AE" opacity="0.4"/>
  <!-- 虹彩棱面 -->
  <path d="M92 104 L150 96 L176 122 L112 128 Z" fill="#F2A8D0" opacity="0.7"/>
  <path d="M86 130 L150 126 L172 152 L104 158 Z" fill="#A8D8F2" opacity="0.7"/>
  <path d="M104 158 L160 154 L172 168 L118 172 Z" fill="#F2E0A8" opacity="0.72"/>
  <path d="M58 130 L20 102 Q36 130 20 158 Z" fill="#4FA8A0"/>
  {eye(176, 120, 10)}
  <path d="M192 140 q7 6 3 13" stroke="#25635C" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(118, 104, 32, 11, 0.45)}
''')


def draw_twilight_fish() -> str:
    """暮色鱼：圆润身，体表黄昏紫渐变，点缀初现的星点，尾鳍柔和"""
    return svg(f'''
  <defs>
    <linearGradient id="dusk" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#8A72C4"/>
      <stop offset="0.55" stop-color="#6E5AA8"/>
      <stop offset="1" stop-color="#4E3E80"/>
    </linearGradient>
  </defs>
  <ellipse cx="130" cy="132" rx="76" ry="56" fill="url(#dusk)"/>
  <ellipse cx="130" cy="132" rx="76" ry="56" fill="none"
           stroke="#4E3E80" stroke-width="2.6"/>
  <!-- 地平线微光 -->
  <path d="M62 150 q34 -14 68 -8 q34 6 68 -6" stroke="#F2B07A"
        stroke-width="4" fill="none" opacity="0.5" stroke-linecap="round"/>
  {sparkle(102, 112, 10, "#F0E8FF", 0.9)}
  {sparkle(146, 106, 7, "#F0E8FF", 0.85)}
  {sparkle(160, 148, 6, "#F0E8FF", 0.8)}
  <path d="M58 130 L20 102 Q36 130 20 158 Z" fill="#4E3E80"/>
  {eye(176, 122, 10)}
  <path d="M192 142 q7 6 3 13" stroke="#D8CCF0" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(106, 106, 34, 15, 0.3)}
''')


DRAWERS = {
    "white_ripple_fish": draw_white_ripple_fish,
    "summer_shade_fish": draw_summer_shade_fish,
    "autumn_tide_fish": draw_autumn_tide_fish,
    "winter_firefly_fish": draw_winter_firefly_fish,
    "spring_crimson_fish": draw_spring_crimson_fish,
    "sunset_glow_fish": draw_sunset_glow_fish,
    "iridescent_fish": draw_iridescent_fish,
    "twilight_fish": draw_twilight_fish,
}
