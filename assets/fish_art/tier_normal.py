"""普通稀有度（8 种）：食物系，圆润可爱。

各自独立造型：
- 蓝莓鱼 圆浆果球 + 果萼    - 奶油鱼 水滴身 + 旋裱花
- 草莓鱼 上宽下尖果形 + 籽  - 蜜桃鱼 双瓣桃臀 + 中缝
- 抹茶鱼 扁圆团子 + 粉粒    - 可可鱼 可可豆形 + 纵沟
- 焦糖鱼 圆角方块 + 流纹    - 布丁鱼 梯形 + 焦糖垂流
"""

from .common import bubble, eye, gloss, mouth, svg


def draw_blueberry_fish() -> str:
    return svg(f'''
  <circle cx="124" cy="134" r="62" fill="#4E6FC8"/>
  <path d="M62 134 a62 62 0 0 1 124 0 z" fill="#3C58A8" opacity="0.3"/>
  <circle cx="124" cy="134" r="62" fill="none" stroke="#33508F" stroke-width="3"/>
  <path d="M74 116 a62 62 0 0 1 100 0 a50 36 0 0 0 -100 0 z"
        fill="#FFFFFF" opacity="0.4"/>
  <g fill="#5E7F5A">
    <path d="M124 74 l-17 -13 l17 5 l17 -5 z"/>
    <path d="M107 68 l-13 -1 l11 9 z"/>
    <path d="M141 68 l13 -1 l-11 9 z"/>
  </g>
  <circle cx="124" cy="71" r="6" fill="#7D9A78"/>
  <path d="M64 134 L30 103 Q42 134 30 165 Z" fill="#3F5FB0"/>
  {eye(156, 122, 11)}
  {mouth(174, 144, "#20303C")}
  {gloss(102, 106, 27, 13, 0.38)}
''')


def draw_cream_fish() -> str:
    return svg(f'''
  <path d="M186 132 Q180 78 126 76 Q72 74 60 132 Q72 190 126 190
           Q180 188 186 132 Z" fill="#F7E8C8"/>
  <path d="M186 132 Q180 78 126 76 Q108 78 94 88 Q150 94 156 134
           Q150 178 94 180 Q108 188 126 190 Q180 188 186 132 Z"
        fill="#E6CFA0" opacity="0.5"/>
  <path d="M100 84 Q96 60 116 56 Q104 72 120 76 Q108 88 100 84 Z"
        fill="#FFF7E6" stroke="#DFC79B" stroke-width="2"/>
  <path d="M116 76 Q122 56 140 58 Q128 72 140 80 Q126 88 116 76 Z"
        fill="#FFFBF0" stroke="#DFC79B" stroke-width="2"/>
  <circle cx="124" cy="72" r="7" fill="#FFFDF8" stroke="#DFC79B" stroke-width="2"/>
  <path d="M60 132 L24 99 Q38 132 24 165 Z" fill="#E6CE9E"/>
  {eye(158, 126, 10)}
  <path d="M148 154 q-9 6 -15 4" stroke="#C9AE7C" stroke-width="2.4"
        fill="none" stroke-linecap="round"/>
  <path d="M176 144 q7 6 3 13" stroke="#B99F6E" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(100, 110, 31, 16, 0.5)}
''')


def draw_strawberry_fish() -> str:
    return svg(f'''
  <path d="M124 196 Q58 194 54 130 Q52 82 124 76 Q196 82 194 130
           Q190 194 124 196 Z" fill="#E8555F"/>
  <path d="M124 196 Q84 194 72 156 Q94 178 124 176 Q154 178 176 156
           Q164 194 124 196 Z" fill="#C93F4C" opacity="0.38"/>
  <g fill="#FFE9A8">
    <ellipse cx="92" cy="116" rx="4" ry="5.6"/><ellipse cx="124" cy="128" rx="4" ry="5.6"/>
    <ellipse cx="156" cy="116" rx="4" ry="5.6"/><ellipse cx="106" cy="156" rx="4" ry="5.6"/>
    <ellipse cx="142" cy="156" rx="4" ry="5.6"/><ellipse cx="124" cy="96" rx="4" ry="5.6"/>
  </g>
  <path d="M124 78 l-28 -13 l21 17 z" fill="#4E9E5C"/>
  <path d="M124 78 l28 -13 l-21 17 z" fill="#4E9E5C"/>
  <rect x="119" y="58" width="10" height="20" rx="4" fill="#3F8A4E"/>
  <path d="M54 130 L18 101 Q32 130 18 159 Z" fill="#D14A56"/>
  {eye(160, 124, 10)}
  <path d="M178 144 q7 6 3 13" stroke="#7A2530" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(98, 110, 28, 15, 0.32)}
''')


def draw_peach_fish() -> str:
    return svg(f'''
  <path d="M126 76 Q196 86 192 138 Q188 190 126 190 Q64 190 60 138
           Q56 86 126 76 Z" fill="#F79C86"/>
  <path d="M126 80 Q150 94 152 138 Q152 180 126 188 Q100 180 100 138
           Q102 94 126 80 Z" fill="#EF8A72" opacity="0.5"/>
  <path d="M126 82 Q124 130 126 186" stroke="#DE7059" stroke-width="3.2"
        fill="none" opacity="0.55"/>
  <path d="M126 76 q-5 -15 7 -22" stroke="#7A9A5A" stroke-width="5"
        fill="none" stroke-linecap="round"/>
  <path d="M132 60 q21 -11 31 4 q-19 7 -31 -4 z" fill="#6FA85C"/>
  <path d="M60 136 L24 106 Q38 136 24 166 Z" fill="#E57F68"/>
  {eye(158, 128, 10)}
  <path d="M176 148 q7 6 3 13" stroke="#9A4A38" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(100, 114, 30, 16, 0.34)}
''')


def draw_matcha_fish() -> str:
    return svg(f'''
  <ellipse cx="128" cy="132" rx="78" ry="52" fill="#7CAE58"/>
  <ellipse cx="128" cy="138" rx="72" ry="44" fill="#96C874"/>
  <g fill="#5E8F42" opacity="0.72">
    <circle cx="94" cy="116" r="3"/><circle cx="124" cy="110" r="2.6"/>
    <circle cx="154" cy="118" r="3"/><circle cx="108" cy="150" r="2.6"/>
    <circle cx="144" cy="152" r="3"/><circle cx="170" cy="138" r="2.4"/>
    <circle cx="84" cy="140" r="2.4"/>
  </g>
  <path d="M54 132 L26 111 Q37 132 26 153 Z" fill="#6E9E4C"/>
  {eye(162, 124, 10)}
  <path d="M180 142 q7 6 3 13" stroke="#2F4F22" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(100, 108, 35, 15, 0.32)}
''')


def draw_cocoa_fish() -> str:
    return svg(f'''
  <ellipse cx="126" cy="132" rx="54" ry="64" fill="#8B5E3C"/>
  <ellipse cx="126" cy="132" rx="54" ry="64" fill="none"
           stroke="#6E4526" stroke-width="3"/>
  <path d="M126 72 Q113 132 126 192" stroke="#6E4526" stroke-width="4"
        fill="none" opacity="0.65"/>
  <path d="M126 74 Q141 132 126 190" stroke="#A5754E" stroke-width="3"
        fill="none" opacity="0.5"/>
  <path d="M76 132 q-20 -22 -32 -17 q11 17 11 17 q0 0 -11 17 q12 5 32 -17 z"
        fill="#6B452A"/>
  {eye(150, 120, 10)}
  <path d="M166 148 q6 6 2 12" stroke="#3A2414" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(106, 102, 20, 23, 0.3)}
''')


def draw_caramel_fish() -> str:
    return svg(f'''
  <rect x="60" y="80" width="136" height="104" rx="42" fill="#C9863A"/>
  <rect x="60" y="80" width="136" height="104" rx="42" fill="none"
        stroke="#A66A26" stroke-width="3"/>
  <path d="M60 124 q30 -28 68 -28 q38 0 68 28 q-30 -10 -68 -10 q-38 0 -68 10 z"
        fill="#8E5218" opacity="0.5"/>
  <g stroke="#E7B973" stroke-width="5" fill="none" opacity="0.7"
     stroke-linecap="round">
    <path d="M82 136 q16 -12 32 0 q16 12 32 0"/>
    <path d="M88 158 q16 -12 32 0 q16 12 30 0"/>
  </g>
  <path d="M60 132 L25 105 Q40 132 25 159 Z" fill="#A96F28"/>
  {eye(160, 120, 10)}
  <path d="M176 146 q7 6 3 13" stroke="#5E3A12" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(98, 102, 27, 12, 0.4)}
''')


def draw_pudding_fish() -> str:
    return svg(f'''
  <path d="M90 94 Q128 84 166 94 L184 178 Q128 194 72 178 Z" fill="#F4C64E"/>
  <path d="M90 94 Q128 84 166 94 L184 178 Q128 194 72 178 Z" fill="none"
        stroke="#D8A32A" stroke-width="3"/>
  <path d="M86 98 Q128 80 170 98 Q168 116 152 112 Q142 130 128 112
           Q114 130 104 112 Q88 116 86 98 Z" fill="#B4762A"/>
  <path d="M102 118 q4 22 -1 30" stroke="#B4762A" stroke-width="7"
        fill="none" stroke-linecap="round"/>
  <path d="M152 116 q-4 17 1 23" stroke="#B4762A" stroke-width="6"
        fill="none" stroke-linecap="round"/>
  <path d="M76 150 L42 127 Q55 154 42 181 Z" fill="#D8A32A"/>
  {eye(156, 130, 10)}
  <path d="M174 152 q7 6 3 13" stroke="#7A5210" stroke-width="2.6"
        fill="none" stroke-linecap="round"/>
  {gloss(102, 128, 26, 14, 0.32)}
''')


DRAWERS = {
    "blueberry_fish": draw_blueberry_fish,
    "cream_fish": draw_cream_fish,
    "strawberry_fish": draw_strawberry_fish,
    "peach_fish": draw_peach_fish,
    "matcha_fish": draw_matcha_fish,
    "cocoa_fish": draw_cocoa_fish,
    "caramel_fish": draw_caramel_fish,
    "pudding_fish": draw_pudding_fish,
}
