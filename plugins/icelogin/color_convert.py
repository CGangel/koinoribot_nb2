"""颜色转换工具（纯 Python，无第三方依赖）

用于将 LAB 颜色空间转换为 RGB。原先依赖 scikit-image
（skimage.color.lab2rgb），已按同一算法链路重写：
Lab(D65 参考白，与 skimage 默认一致) → XYZ(D65) → 线性 sRGB → gamma。
移除 scikit-image 依赖后输出与原实现一致（逐通道差 ≤1，舍入级）。
"""

# CIE LAB → XYZ，D65 参考白（2° 视场，skimage lab2xyz 的默认参数）
_XN, _YN, _ZN = 0.95047, 1.0, 1.08883

# XYZ(D65) → 线性 sRGB
_XYZ_TO_LINEAR_SRGB = (
    (3.2406, -1.5372, -0.4986),
    (-0.9689, 1.8758, 0.0415),
    (0.0557, -0.2040, 1.0570),
)


def _finv(t: float) -> float:
    t3 = t * t * t
    return t3 if t3 > 0.008856 else (116 * t - 16) / 903.3


def _gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def lab2rgb(lightness: float, a: float, b: float) -> tuple:
    """将 LAB 颜色转换为 RGB（与 skimage.color.lab2rgb 同算法链路）。"""
    fy = (lightness + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    y = _YN * (_finv(fy) if lightness > 8 else lightness / 903.3)
    xyz = (_XN * _finv(fx), y, _ZN * _finv(fz))

    channels = []
    for row in _XYZ_TO_LINEAR_SRGB:
        linear = max(0.0, sum(m * v for m, v in zip(row, xyz)))
        scaled = _gamma(linear) * 255
        channels.append(int(max(0, min(255, round(scaled)))))
    return tuple(channels)
