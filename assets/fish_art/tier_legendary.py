"""传说稀有度（8 种）：星空、花瓣与光流系。

- 星野蓝鱼 长身覆星野 + 银河带      - 夜光星鱼 深空身 + 大星环
- 樱吹雪鱼 花簇身 + 飞散樱瓣        - 梦境蓝鱼 云梦身 + 月牙梦纹
- 雾隐幽鱼 灰蓝身 + 半透明雾带      - 云隙光鱼 身披云层 + 光柱
- 糖霜极光鱼 身覆双层极光带        - 雪羽流光鱼 白羽身 + 飘羽流线
"""

from .common import bubble, eye, gloss, sparkle, svg


def draw_starfield_blue_fish() -> str:
    """星野蓝鱼：修长身，一条银河斜带贯穿，密布大小星点"""
    return svg(f'''
  <path d="M214 128 Q198 88 150 86 Q92 84 54 118 Q54 150 98 172
           Q156 178 202 160 Q212 144 214 128 Z" fill="#31529E"/>
  <path d="M214 128 Q198 88 150 86 Q124 85 102 92 Q158 106 164 130
           Q160 158 102 164 Q126 174 150 172 Q202 160 214 128 Z"
        fill="#233E78" opacity="0.55"/>
  <!-- 银河斜带 -->
  <path d="M64 152 Q118 104 190 100" stroke="#8FA8E8" stroke-width="12"
        fill="none" opacity="0.35" stroke-linecap="round"/>
  <path d="M64 152 Q118 104 190 100" stroke="#C4D4FA" stroke-width="4"
        fill="none" opacity="0.6" stroke-linecap="round"/>
  <!-- 星点 -->
  {sparkle(100, 112, 12, "#FFFFFF", 0.95)}
  {sparkle(146, 142, 10, "#FFFFFF", 0.9)}
  {sparkle(172, 104, 8, "#FFFFFF", 0.9)}
  {sparkle(120, 156, 7, "#FFFFFF", 0.85)}
  <g fill="#FFFFFF" opacity="0.9">
    <circle cx="132" cy="104" r="2.6"/><circle cx="164" cy="128" r="2.4"/>
    <circle cx="88" cy="134" r="2.2"/><circle cx="150" cy="164" r="2.2"/>
  </g>
  <path d="M54 128 L16 98 Q32 128 16 158 Z" fill="#233E78"/>
  {eye(180, 118, 10)}
  <path d="M196 138 q7 6 3 13" stroke="#D8E2FA" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(122, 100, 36, 11, 0.26)}
''')


def draw_night_glow_star_fish() -> str:
    """夜光星鱼：深空墨蓝身，背部一枚发光星环，星芒四射"""
    return svg(f'''
  <ellipse cx="132" cy="134" rx="78" ry="52" fill="#232E5E"/>
  <ellipse cx="132" cy="140" rx="70" ry="44" fill="#2E3A70"/>
  <!-- 星环 -->
  <circle cx="140" cy="108" r="30" fill="none" stroke="#FFF6C0"
          stroke-width="3.6" opacity="0.85"/>
  <circle cx="140" cy="108" r="20" fill="#FFF6C0" opacity="0.22"/>
  {sparkle(140, 108, 22, "#FFF6C0", 0.98)}
  {sparkle(96, 138, 10, "#FFF6C0", 0.9)}
  {sparkle(178, 152, 8, "#FFF6C0", 0.88)}
  {sparkle(112, 164, 6, "#FFF6C0", 0.82)}
  <!-- 扇形尾 -->
  <path d="M56 134 q-26 -30 -34 -22 q8 22 8 22 q0 0 -8 22 q8 8 34 -22 z"
        fill="#1A2450"/>
  {eye(176, 128, 10)}
  <path d="M192 148 q7 6 3 13" stroke="#D8E0FA" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(104, 106, 32, 14, 0.24)}
''')


def draw_cherry_blossom_blizzard_fish() -> str:
    """樱吹雪鱼：粉白鱼身，背鳍与尾鳍化作樱瓣，周身飞散花瓣"""
    return svg(f'''
  <!-- 樱瓣状尾鳍 -->
  <g fill="#F2B4CA">
    <path d="M64 132 q-30 -24 -32 -42 q24 6 32 22 z"/>
    <path d="M64 132 q-32 -6 -40 -20 q22 -8 40 2 z"/>
    <path d="M64 132 q-30 24 -32 42 q24 -6 32 -22 z"/>
    <path d="M64 132 q-32 6 -40 20 q22 8 40 -2 z"/>
  </g>
  <!-- 鱼身 -->
  <path d="M200 132 Q192 88 144 86 Q92 84 62 128 Q72 182 138 186
           Q194 184 200 132 Z" fill="#F7C6D8"/>
  <path d="M200 132 Q192 88 144 86 Q122 86 104 94 Q162 106 168 132
           Q162 162 104 172 Q122 182 138 186 Q194 184 200 132 Z"
        fill="#FBD4E2" opacity="0.7"/>
  <!-- 樱瓣状背鳍 -->
  <g fill="#F2B4CA">
    <path d="M104 92 a14 14 0 1 1 24 6 a11 11 0 1 0 -24 -6 z"/>
    <path d="M132 84 a12 12 0 1 1 20 6 a9 9 0 1 0 -20 -6 z"/>
  </g>
  <!-- 体侧樱瓣 -->
  <g fill="#FFFFFF" opacity="0.9">
    <ellipse cx="110" cy="120" rx="10" ry="6" transform="rotate(-24 110 120)"/>
    <ellipse cx="148" cy="152" rx="9" ry="5.5" transform="rotate(20 148 152)"/>
    <ellipse cx="126" cy="166" rx="8" ry="5" transform="rotate(-10 126 166)"/>
  </g>
  <!-- 飞散花瓣 -->
  <g fill="#FFD9E2" opacity="0.95">
    <ellipse cx="76" cy="88" rx="9" ry="5.5" transform="rotate(-32 76 88)"/>
    <ellipse cx="188" cy="92" rx="8" ry="5" transform="rotate(26 188 92)"/>
    <ellipse cx="192" cy="176" rx="9" ry="5.5" transform="rotate(-18 192 176)"/>
  </g>
  {eye(168, 122, 10)}
  <path d="M184 142 q7 6 3 13" stroke="#B85F80" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(114, 106, 30, 13, 0.5)}
''')


def draw_dream_blue_fish() -> str:
    """梦境蓝鱼：云朵般柔软的圆身，身侧一弯月牙与梦泡"""
    return svg(f'''
  <path d="M200 134 Q192 88 142 88 Q88 88 62 128 Q70 178 130 182
           Q190 180 200 134 Z" fill="#7FA8E8"/>
  <path d="M200 134 Q192 88 142 88 Q120 88 102 98 Q158 108 164 134
           Q158 164 102 172 Q122 180 142 180 Q190 180 200 134 Z"
        fill="#5F88C8" opacity="0.45"/>
  <!-- 月牙 -->
  <path d="M104 106 a22 22 0 1 0 6 36 a17 17 0 1 1 -6 -36 z"
        fill="#F2F6FF" opacity="0.95"/>
  <!-- 梦泡 -->
  {bubble(166, 116, 9, "#DCE8FF", 2.6, 0.9)}
  {bubble(180, 140, 6, "#DCE8FF", 2.2, 0.85)}
  {bubble(154, 164, 5, "#DCE8FF", 2, 0.8)}
  {sparkle(132, 154, 8, "#EAF1FF", 0.9)}
  <path d="M62 130 L24 102 Q40 130 24 158 Z" fill="#5F88C8"/>
  {eye(170, 130, 10)}
  <path d="M186 150 q7 6 3 13" stroke="#2E4A7A" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(112, 110, 34, 15, 0.38)}
''')


def draw_mist_hidden_fish() -> str:
    """雾隐幽鱼：灰蓝修长身，数条半透明雾带横过，尾鳍化入雾中"""
    return svg(f'''
  <path d="M214 130 Q198 92 152 90 Q96 88 58 122 Q56 152 100 172
           Q158 178 204 162 Q212 146 214 130 Z" fill="#9AA8B8"/>
  <path d="M214 130 Q198 92 152 90 Q128 89 106 96 Q160 108 166 132
           Q162 158 106 166 Q130 174 156 172 Q204 162 214 130 Z"
        fill="#7A8A9C" opacity="0.5"/>
  <!-- 雾带 -->
  <g stroke="#FFFFFF" fill="none" opacity="0.5" stroke-linecap="round">
    <path d="M62 112 q46 -14 92 -4 q30 6 52 0" stroke-width="7"/>
    <path d="M58 134 q48 -14 96 -2 q28 6 50 -2" stroke-width="8"/>
    <path d="M62 158 q46 -14 92 -2 q30 6 50 -4" stroke-width="7"/>
  </g>
  <path d="M58 130 L20 100 Q36 130 20 160 Z" fill="#7A8A9C" opacity="0.85"/>
  {eye(180, 120, 9)}
  <path d="M196 140 q6 6 2 12" stroke="#54636F" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(122, 104, 36, 11, 0.34)}
''')


def draw_cloudbreak_light_fish() -> str:
    """云隙光鱼：暖金修长身，身后斜射数道光柱，背脊一道云缝亮线"""
    return svg(f'''
  <!-- 云隙光柱（置于鱼身后方） -->
  <g fill="#FFF6D8" opacity="0.6">
    <path d="M126 58 l16 0 l-26 70 l-15 0 z"/>
    <path d="M158 62 l14 0 l-22 62 l-13 0 z"/>
  </g>
  <!-- 鱼身 -->
  <path d="M206 132 Q196 92 150 90 Q96 88 58 122 Q56 154 100 174
           Q158 180 200 162 Q206 146 206 132 Z" fill="#F2D8A8"/>
  <path d="M206 132 Q196 92 150 90 Q126 89 106 96 Q158 108 164 132
           Q160 158 106 166 Q130 174 154 172 Q200 162 206 132 Z"
        fill="#DCB878" opacity="0.5"/>
  <!-- 背脊云缝 -->
  <path d="M96 104 q26 -14 52 -6 q24 8 44 -2" stroke="#FFFFFF"
        stroke-width="6" fill="none" opacity="0.72" stroke-linecap="round"/>
  <!-- 尾鳍云化 -->
  <path d="M58 130 q-30 -16 -42 -30 q12 -4 22 2 q-12 -12 -8 -26
           q20 12 32 28 q12 14 18 28 z" fill="#DCB878"/>
  <path d="M58 130 q-30 16 -42 30 q12 4 22 -2 q-12 12 -8 26
           q20 -12 32 -28 q12 -14 18 -28 z" fill="#E8C88E"/>
  {eye(176, 122, 10)}
  <path d="M192 142 q7 6 3 13" stroke="#8A6420" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(120, 108, 34, 12, 0.42)}
''')


def draw_frost_aurora_fish() -> str:
    """糖霜极光鱼：青蓝身覆两层极光带（青绿/粉紫），体表撒霜点"""
    return svg(f'''
  <path d="M210 130 Q198 90 152 88 Q94 86 56 120 Q54 152 98 172
           Q156 178 202 160 Q210 144 210 130 Z" fill="#8FD8E8"/>
  <path d="M210 130 Q198 90 152 88 Q128 87 106 94 Q160 106 166 130
           Q162 158 106 166 Q130 174 154 172 Q202 160 210 130 Z"
        fill="#68B8CC" opacity="0.42"/>
  <!-- 极光带 -->
  <path d="M64 108 q42 -26 86 -6 q30 14 52 -4" stroke="#B8F0D8"
        stroke-width="8" fill="none" opacity="0.8" stroke-linecap="round"/>
  <path d="M60 138 q42 -24 88 -4 q28 12 50 -6" stroke="#E8C4F0"
        stroke-width="7" fill="none" opacity="0.72" stroke-linecap="round"/>
  <path d="M68 164 q42 -22 86 -2 q28 12 48 -4" stroke="#FFFFFF"
        stroke-width="5" fill="none" opacity="0.6" stroke-linecap="round"/>
  <!-- 霜点 -->
  <g fill="#FFFFFF" opacity="0.92">
    <circle cx="104" cy="128" r="2.6"/><circle cx="140" cy="152" r="2.4"/>
    <circle cx="166" cy="120" r="2.2"/>
  </g>
  <path d="M56 130 L18 100 Q34 130 18 160 Z" fill="#68B8CC"/>
  {eye(178, 120, 10)}
  <path d="M194 140 q7 6 3 13" stroke="#2E7C90" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(122, 102, 34, 11, 0.4)}
''')


def draw_snow_feather_streamer_fish() -> str:
    """雪羽流光鱼：雪白修长身，覆羽片流线，尾鳍化作飘羽"""
    return svg(f'''
  <path d="M212 130 Q198 92 152 90 Q96 88 58 122 Q56 152 100 172
           Q158 178 204 162 Q212 146 212 130 Z" fill="#E4EEF8"/>
  <path d="M212 130 Q198 92 152 90 Q128 89 106 96 Q160 108 166 132
           Q162 158 106 166 Q130 174 156 172 Q204 162 212 130 Z"
        fill="#C0D6EC" opacity="0.5"/>
  <!-- 羽片流线 -->
  <g stroke="#FFFFFF" stroke-width="3.4" fill="none" opacity="0.95"
     stroke-linecap="round">
    <path d="M90 116 q30 -12 62 -6"/><path d="M84 132 q34 -12 70 -4"/>
    <path d="M90 150 q30 -10 62 -4"/>
  </g>
  <g stroke="#A8C4E0" stroke-width="2.2" fill="none" opacity="0.7"
     stroke-linecap="round">
    <path d="M100 108 q22 10 34 30"/><path d="M150 152 q18 -8 28 -24"/>
  </g>
  <!-- 飘羽尾 -->
  <path d="M58 130 q-30 -18 -40 -34 q22 6 26 22 q-14 -4 -22 -16
           q10 26 36 46 z" fill="#C0D6EC"/>
  {eye(178, 120, 10)}
  <path d="M194 140 q7 6 3 13" stroke="#5C7E9E" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(124, 104, 36, 11, 0.5)}
''')


DRAWERS = {
    "starfield_blue_fish": draw_starfield_blue_fish,
    "night_glow_star_fish": draw_night_glow_star_fish,
    "cherry_blossom_blizzard_fish": draw_cherry_blossom_blizzard_fish,
    "dream_blue_fish": draw_dream_blue_fish,
    "mist_hidden_fish": draw_mist_hidden_fish,
    "cloudbreak_light_fish": draw_cloudbreak_light_fish,
    "frost_aurora_fish": draw_frost_aurora_fish,
    "snow_feather_streamer_fish": draw_snow_feather_streamer_fish,
}
