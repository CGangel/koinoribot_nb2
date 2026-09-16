"""稀有稀有度（8 种）：气泡、风露与甜点系。

- 海盐鱼 结晶棱面体 + 盐晶粒     - 晨露鱼 水滴身 + 露珠
- 晴空鱼 流线身 + 云斑 + 气泡     - 风铃鱼 钟形身 + 铃舌 + 风纹
- 薄荷风鱼 柳叶身 + 叶脉          - 柠檬汽泡鱼 柠檬形 + 气泡
- 苏打气泡鱼 圆胖身 + 密集气泡     - 云朵软糖鱼 云朵波浪身
"""

from .common import bubble, eye, gloss, mouth, scallop, svg, sparkle


def draw_sea_salt_fish() -> str:
    """海盐鱼：方糖状结晶棱面的身体，表面附着盐晶粒"""
    return svg(f'''
  <path d="M60 108 L128 76 L196 108 L196 158 L128 190 L60 158 Z" fill="#CFE6F2"/>
  <path d="M60 108 L128 76 L196 108 L128 138 Z" fill="#E8F4FA"/>
  <path d="M60 108 L128 138 L128 190 L60 158 Z" fill="#B6D4E4"/>
  <path d="M196 108 L128 138 L128 190 L196 158 Z" fill="#A2C4D6"/>
  <path d="M60 108 L128 76 L196 108 L196 158 L128 190 L60 158 Z"
        fill="none" stroke="#8FB4C6" stroke-width="2.6" stroke-linejoin="round"/>
  <!-- 盐晶粒 -->
  <g fill="#FFFFFF" opacity="0.95">
    <rect x="86" y="116" width="8" height="8" rx="1.5"/>
    <rect x="146" y="122" width="7" height="7" rx="1.5"/>
    <rect x="112" y="154" width="8" height="8" rx="1.5"/>
    <rect x="156" y="150" width="6" height="6" rx="1.5"/>
  </g>
  <path d="M60 132 L26 104 Q40 132 26 160 Z" fill="#A2C4D6"/>
  {eye(162, 112, 10)}
  <path d="M178 132 q7 6 3 13" stroke="#4A6B7C" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(96, 98, 22, 9, 0.55)}
''')


def draw_morning_dew_fish() -> str:
    """晨露鱼：饱满水滴身（尖头朝后），体表叶脉与滚落露珠"""
    return svg(f'''
  <path d="M196 130 Q190 78 138 74 Q86 70 62 112 Q74 178 138 188
           Q190 184 196 130 Z" fill="#9ED8C8"/>
  <path d="M196 130 Q190 78 138 74 Q112 72 92 86 Q152 96 160 132
           Q156 176 92 180 Q118 190 138 188 Q190 184 196 130 Z"
        fill="#7CC4B2" opacity="0.5"/>
  <!-- 叶脉纹 -->
  <g stroke="#DFF5EE" stroke-width="3" fill="none" opacity="0.75"
     stroke-linecap="round">
    <path d="M96 108 q30 22 26 56"/>
    <path d="M116 118 q16 8 20 22"/>
    <path d="M112 152 q14 4 22 14"/>
  </g>
  <!-- 露珠 -->
  <circle cx="100" cy="96" r="8" fill="#E8FAF4" opacity="0.9"/>
  <circle cx="97" cy="93" r="2.4" fill="#FFFFFF"/>
  <path d="M62 128 L26 100 Q40 128 26 156 Z" fill="#78BAA8"/>
  {eye(166, 122, 10)}
  <path d="M182 142 q7 6 3 13" stroke="#33665A" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(104, 104, 30, 14, 0.42)}
''')


def draw_clear_sky_fish() -> str:
    """晴空鱼：细长流线身，身上浮着白云斑与上升气泡"""
    return svg(f'''
  <path d="M206 130 Q200 96 160 92 Q104 86 62 118 Q60 148 104 172
           Q162 178 200 164 Q208 150 206 130 Z" fill="#7FC4EE"/>
  <path d="M206 130 Q200 96 160 92 Q140 90 122 96 Q166 108 172 132
           Q168 158 122 168 Q142 174 168 172 Q204 164 206 130 Z"
        fill="#5AA3D4" opacity="0.45"/>
  <!-- 云斑 -->
  <g fill="#FFFFFF" opacity="0.92">
    <circle cx="120" cy="112" r="15"/><circle cx="142" cy="116" r="18"/>
    <circle cx="162" cy="112" r="14"/>
    <rect x="118" y="110" width="46" height="18" rx="9"/>
  </g>
  <!-- 上升气泡 -->
  {bubble(96, 160, 7, "#E4F4FE", 2.2, 0.9)}
  {bubble(84, 178, 5, "#E4F4FE", 2, 0.85)}
  {bubble(108, 142, 5.5, "#E4F4FE", 2, 0.85)}
  <path d="M62 132 L28 108 Q42 132 28 158 Z" fill="#5AA3D4"/>
  {eye(174, 128, 10)}
  <path d="M190 146 q7 6 3 13" stroke="#1D5B85" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(112, 106, 30, 12, 0.4)}
''')


def draw_wind_bell_fish() -> str:
    """风铃鱼：钟形身体（下缘外扩），下方悬铃舌，身上刻风纹"""
    return svg(f'''
  <path d="M128 72 Q182 76 186 124 Q188 152 172 166 Q128 178 84 166
           Q68 152 70 124 Q74 76 128 72 Z" fill="#B8C7E8"/>
  <path d="M128 72 Q182 76 186 124 Q188 152 172 166 Q128 178 84 166
           Q68 152 70 124 Q74 76 128 72 Z" fill="none"
        stroke="#93A5CC" stroke-width="3"/>
  <!-- 风纹 -->
  <g stroke="#E4EAF8" stroke-width="3.4" fill="none" opacity="0.8"
     stroke-linecap="round">
    <path d="M92 108 q18 -10 36 0 q18 10 36 0"/>
    <path d="M88 128 q18 -10 36 0 q18 10 38 0"/>
  </g>
  <!-- 钟舌 -->
  <path d="M128 168 l-9 12 h18 z" fill="#F2D074"/>
  <circle cx="128" cy="188" r="9" fill="#F2D074" stroke="#C9A23F" stroke-width="2"/>
  <circle cx="125" cy="185" r="3" fill="#FFF0C0"/>
  <!-- 顶环 -->
  <path d="M128 72 q0 -14 10 -18" stroke="#93A5CC" stroke-width="4"
        fill="none" stroke-linecap="round"/>
  {eye(158, 116, 10)}
  <path d="M174 136 q7 6 3 13" stroke="#4A5880" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(104, 100, 28, 14, 0.42)}
''')


def draw_mint_breeze_fish() -> str:
    """薄荷风鱼：柳叶形细长身，带主叶脉与侧脉，尾鳍细长"""
    return svg(f'''
  <path d="M214 130 Q186 88 128 88 Q74 88 52 130 Q74 172 128 172
           Q186 172 214 130 Z" fill="#9FE3C8"/>
  <path d="M214 130 Q186 88 128 88 Q96 88 76 100 Q140 104 150 130
           Q140 156 76 160 Q96 172 128 172 Q186 172 214 130 Z"
        fill="#7CD0B0" opacity="0.45"/>
  <!-- 叶脉 -->
  <path d="M72 130 L196 130" stroke="#DFF9EE" stroke-width="3.4"
        fill="none" opacity="0.85" stroke-linecap="round"/>
  <g stroke="#DFF9EE" stroke-width="2.6" fill="none" opacity="0.75"
     stroke-linecap="round">
    <path d="M104 130 q14 -16 30 -18"/><path d="M104 130 q14 16 30 18"/>
    <path d="M140 130 q14 -14 28 -16"/><path d="M140 130 q14 14 28 16"/>
  </g>
  <path d="M52 130 L14 106 Q30 130 14 154 Z" fill="#74C2A4"/>
  {eye(174, 118, 10)}
  <path d="M190 138 q7 6 3 13" stroke="#2E6B58" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(140, 108, 36, 11, 0.4)}
''')


def draw_lemon_soda_fish() -> str:
    """柠檬汽泡鱼：椭圆柠檬身 + 两端尖突，表面气泡与柠檬白瓤纹"""
    return svg(f'''
  <ellipse cx="130" cy="132" rx="70" ry="48" fill="#F2E062"/>
  <ellipse cx="130" cy="132" rx="70" ry="48" fill="none"
           stroke="#D6C13A" stroke-width="3"/>
  <ellipse cx="130" cy="138" rx="58" ry="36" fill="#F8EE9C"/>
  <!-- 两端尖突 -->
  <path d="M60 132 l-14 -9 l14 9 l-14 9 z" fill="#D6C13A"/>
  <!-- 白瓤瓣纹 -->
  <g stroke="#FFFFFF" stroke-width="2.4" fill="none" opacity="0.8"
     stroke-linecap="round">
    <path d="M130 96 L130 168"/><path d="M108 104 L118 160"/>
    <path d="M152 104 L142 160"/>
  </g>
  <!-- 气泡 -->
  {bubble(102, 118, 8, "#FFFFFF", 2.6, 0.95)}
  {bubble(138, 148, 6, "#FFFFFF", 2.4, 0.9)}
  {bubble(158, 112, 5, "#FFFFFF", 2.2, 0.9)}
  <path d="M60 132 L26 106 Q40 132 26 158 Z" fill="#D6C13A"/>
  {eye(168, 124, 10)}
  <path d="M184 142 q7 6 3 13" stroke="#7A6B14" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(104, 110, 30, 13, 0.4)}
''')


def draw_soda_bubble_fish() -> str:
    """苏打气泡鱼：圆胖球身，布满大小气泡，尾鳍扇形"""
    return svg(f'''
  <ellipse cx="132" cy="132" rx="72" ry="58" fill="#8FD6E8"/>
  <ellipse cx="132" cy="140" rx="64" ry="48" fill="#A8E2F0"/>
  <!-- 气泡群 -->
  {bubble(96, 108, 12, "#FFFFFF", 2.8, 0.95)}
  {bubble(140, 100, 9, "#FFFFFF", 2.6, 0.95)}
  {bubble(168, 130, 8, "#FFFFFF", 2.4, 0.9)}
  {bubble(112, 156, 10, "#FFFFFF", 2.6, 0.9)}
  {bubble(152, 160, 7, "#FFFFFF", 2.4, 0.88)}
  {bubble(84, 138, 6, "#FFFFFF", 2.2, 0.85)}
  <!-- 扇形尾 -->
  <path d="M60 132 q-26 -30 -34 -22 q8 22 8 22 q0 0 -8 22 q8 8 34 -22 z"
        fill="#63B4CC"/>
  {eye(170, 124, 11)}
  <path d="M186 146 q7 6 3 13" stroke="#1F6C82" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(104, 104, 32, 16, 0.42)}
''')


def draw_cloud_marshmallow_fish() -> str:
    """云朵软糖鱼：云朵波浪轮廓的胖身，撒糖霜小点，圆尾"""
    return svg(f'''
  <path d="M76 148 Q60 148 60 128 Q60 108 80 106 Q82 84 106 80
           Q122 60 146 70 Q170 62 182 82 Q204 86 204 108 Q204 128 188 132
           Q192 148 176 156 Q166 176 142 172 Q120 184 100 168
           Q80 168 76 148 Z" fill="#F4E3F2"/>
  <path d="M76 148 Q60 148 60 128 Q60 108 80 106 Q82 84 106 80
           Q122 60 146 70 Q170 62 182 82 Q204 86 204 108 Q204 128 188 132
           Q192 148 176 156 Q166 176 142 172 Q120 184 100 168
           Q80 168 76 148 Z" fill="none" stroke="#DCC2DC" stroke-width="2.6"/>
  <!-- 糖霜 -->
  <g fill="#FFFFFF" opacity="0.95">
    <circle cx="104" cy="116" r="3.4"/><circle cx="134" cy="104" r="3"/>
    <circle cx="160" cy="118" r="3.2"/><circle cx="122" cy="146" r="3.4"/>
    <circle cx="156" cy="148" r="3"/><circle cx="88" cy="138" r="2.6"/>
  </g>
  <!-- 圆尾 -->
  <circle cx="46" cy="128" r="26" fill="#DCC2DC"/>
  {eye(168, 118, 10)}
  <path d="M184 136 q7 6 3 13" stroke="#7A5C78" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(112, 108, 34, 16, 0.44)}
''')


DRAWERS = {
    "sea_salt_fish": draw_sea_salt_fish,
    "morning_dew_fish": draw_morning_dew_fish,
    "clear_sky_fish": draw_clear_sky_fish,
    "wind_bell_fish": draw_wind_bell_fish,
    "mint_breeze_fish": draw_mint_breeze_fish,
    "lemon_soda_fish": draw_lemon_soda_fish,
    "soda_bubble_fish": draw_soda_bubble_fish,
    "cloud_marshmallow_fish": draw_cloud_marshmallow_fish,
}
