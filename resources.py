"""资源管理：图片资源加载、Base64 转换等。"""

import os
import base64
from pathlib import Path
from typing import Optional, Union
from PIL import Image

# 导入 nonebot2 的消息段
try:
    from nonebot.adapters.onebot.v11 import MessageSegment as OneBotMessageSegment
    ONEBOT_AVAILABLE = True
except ImportError:
    ONEBOT_AVAILABLE = False
    OneBotMessageSegment = None

# 资源目录路径
_res_dir: Optional[Path] = None
_img_path: Optional[Path] = None
_user_path: Optional[Path] = None


def set_resource_dir(res_dir: Union[str, Path]):
    """设置资源目录"""
    global _res_dir, _img_path, _user_path
    _res_dir = Path(res_dir)
    _img_path = _res_dir / "img"
    _user_path = _res_dir / "database"
    
    # 确保目录存在
    _img_path.mkdir(parents=True, exist_ok=True)
    _user_path.mkdir(parents=True, exist_ok=True)


def get_res_dir() -> Path:
    """获取资源目录"""
    if _res_dir is None:
        raise RuntimeError("资源目录未设置，请先调用 set_resource_dir()")
    return _res_dir


def get_img_path() -> Path:
    """获取图片目录"""
    if _img_path is None:
        raise RuntimeError("资源目录未设置")
    return _img_path


def get_user_path() -> Path:
    """获取用户数据目录"""
    if _user_path is None:
        raise RuntimeError("资源目录未设置")
    return _user_path


class ResImg:
    """图片资源类"""
    
    def __init__(self, path: Union[str, Path]):
        """
        初始化图片资源
        
        Args:
            path: 相对于资源目录的路径
        """
        self._rel_path = Path(path)
    
    @property
    def path(self) -> Path:
        """获取完整路径"""
        return get_res_dir() / self._rel_path
    
    @property
    def exist(self) -> bool:
        """检查文件是否存在"""
        return self.path.exists()
    
    def open(self) -> Image.Image:
        """打开图片"""
        if not self.exist:
            raise FileNotFoundError(f"图片资源不存在: {self.path}")
        return Image.open(self.path)
    
    @property
    def base64(self) -> str:
        """获取 Base64 编码"""
        return pic2b64(self.path)
    
    @property
    def cqcode(self) -> str:
        """获取 CQ 码（兼容旧版）"""
        if ONEBOT_AVAILABLE:
            return OneBotMessageSegment.image(f"base64://{self.base64}")
        return f"[图片: {self._rel_path}]"
    
    def to_segment(self):
        """转换为消息段"""
        if ONEBOT_AVAILABLE:
            return OneBotMessageSegment.image(f"base64://{self.base64}")
        return None


def get(path: str, *paths: str) -> ResImg:
    """
    获取图片资源（兼容旧版 API）
    
    Args:
        path: 相对于 img 目录的路径
        *paths: 额外路径组件
    
    Returns:
        ResImg 对象
    """
    full_path = Path("img") / path
    for p in paths:
        full_path = full_path / p
    return ResImg(full_path)


def pic2b64(path: Union[str, Path]) -> str:
    """
    将图片转换为 Base64 编码
    
    Args:
        path: 图片路径
    
    Returns:
        Base64 编码字符串
    """
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def b64_to_segment(b64_str: str):
    """
    将 Base64 字符串转换为消息段
    
    Args:
        b64_str: Base64 编码的图片
    
    Returns:
        消息段对象
    """
    if ONEBOT_AVAILABLE:
        return OneBotMessageSegment.image(f"base64://{b64_str}")
    return None


def image_to_base64(img: Image.Image, format: str = "PNG") -> str:
    """
    将 PIL Image 转换为 Base64
    
    Args:
        img: PIL Image 对象
        format: 图片格式
    
    Returns:
        Base64 编码字符串
    """
    import io
    buffer = io.BytesIO()
    img.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode()


def check_path_exists(path: Union[str, Path]) -> bool:
    """检查并创建路径"""
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return False
    return True
