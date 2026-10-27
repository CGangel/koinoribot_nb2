"""通用工具函数：数据读写、图片 Base64、红包拆分等（适配 nonebot2）。"""

import io
import os
import json
import re
import base64
from pathlib import Path
from typing import Union, Optional
import random

import aiohttp

from .build_image import BuildImage


def save_data(obj, fp: Union[str, Path]):
    """
    保存数据到 JSON 文件
    
    Args:
        obj: 要保存的数据
        fp: 文件路径
    """
    fp = Path(fp)
    fp.parent.mkdir(parents=True, exist_ok=True)
    
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_data(fp: Union[str, Path], is_list: bool = False):
    """
    加载 JSON 数据，不存在则创建
    
    Args:
        fp: 文件路径
        is_list: 默认数据类型是否为列表
    
    Returns:
        加载的数据
    """
    fp = Path(fp)
    
    if fp.exists():
        with open(fp, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        fp.parent.mkdir(parents=True, exist_ok=True)
        default_data = [] if is_list else {}
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)
        return default_data


def is_http_url(url: str) -> bool:
    """检查字符串是否为 HTTP URL"""
    regex = re.compile(
        r'(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE
    )
    return bool(regex.findall(url))


async def get_user_icon(uid: Union[int, str]):
    """
    获取用户头像
    
    Args:
        uid: QQ 号
    
    Returns:
        BuildImage 对象（如果可用）或 BytesIO
    """
    image_url = f'https://q1.qlogo.cn/g?b=qq&nk={uid}&src_uin=www.jlwz.cn&s=0'
    
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as r:
            content = await r.read()
    
    icon_file = io.BytesIO(content)
    

    return BuildImage(0, 0, background=icon_file)


async def get_net_img(url: str, proxy: Optional[str] = None):
    """
    下载网络图片
    
    Args:
        url: 图片 URL
        proxy: 代理地址（可选）
    
    Returns:
        BuildImage 对象或 BytesIO
    """
    async with aiohttp.ClientSession() as session:
        kwargs = {}
        if proxy:
            kwargs['proxy'] = proxy
        
        async with session.get(url, **kwargs) as r:
            content = await r.read()
    
    file = io.BytesIO(content)
    
    return BuildImage(0, 0, background=file)


def pic2b64(pic_path: Union[str, Path]) -> str:
    """将图片文件转换为 Base64 字符串"""
    with open(pic_path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def get_double_mean_money(total: int, num: int) -> list:
    """
    双倍均值红包算法
    
    Args:
        total: 总金额
        num: 红包个数
    
    Returns:
        每个红包的金额列表
    """
    result = []
    rest = total
    
    for i in range(num - 1):
        # 双倍均值：[1, 剩余金额/剩余人数*2]
        max_val = int(rest / (num - i) * 2)
        if max_val < 1:
            max_val = 1
        money = random.randint(1, max_val)
        result.append(money)
        rest -= money
    
    # 最后一个红包
    result.append(rest)
    
    return result


class FreqLimiter:
    """
    频率限制器
    
    用于限制用户操作频率
    """
    
    def __init__(self, default_cd: int = 0):
        """
        初始化
        
        Args:
            default_cd: 默认冷却时间（秒）
        """
        self.default_cd = default_cd
        self._cd_dict: dict[int, float] = {}
    
    def check(self, uid: int) -> bool:
        """
        检查用户是否可以操作
        
        Args:
            uid: 用户 UID
        
        Returns:
            True 表示可以操作
        """
        import time
        if uid not in self._cd_dict:
            return True
        return time.time() >= self._cd_dict[uid]
    
    def start_cd(self, uid: int, cd: Optional[int] = None):
        """
        开始冷却
        
        Args:
            uid: 用户 UID
            cd: 冷却时间（秒），None 使用默认值
        """
        import time
        if cd is None:
            cd = self.default_cd
        self._cd_dict[uid] = time.time() + cd
    
    def left_time(self, uid: int) -> float:
        """
        获取剩余冷却时间
        
        Args:
            uid: 用户 UID
        
        Returns:
            剩余秒数
        """
        import time
        if uid not in self._cd_dict:
            return 0
        return max(0, self._cd_dict[uid] - time.time())


class GroupFreqLimiter:
    """
    群组频率限制器
    
    用于限制群组操作频率
    """
    
    def __init__(self, default_cd: int = 0):
        self.default_cd = default_cd
        self._cd_dict: dict[int, float] = {}
    
    def check(self, gid: int) -> bool:
        import time
        if gid not in self._cd_dict:
            return True
        return time.time() >= self._cd_dict[gid]
    
    def start_cd(self, gid: int, cd: Optional[int] = None):
        import time
        if cd is None:
            cd = self.default_cd
        self._cd_dict[gid] = time.time() + cd
    
    def left_time(self, gid: int) -> float:
        import time
        if gid not in self._cd_dict:
            return 0
        return max(0, self._cd_dict[gid] - time.time())
