"""
超级用户注册插件 - su_register

提供 superusers 表的创建和 SU 注册功能。
本文件不上传 git 仓库。

命令:
    注册su 激活码 - 使用激活码注册为 level 1 或 level 2 SU
"""

import aiohttp

from nonebot import get_driver, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg, Depends
from nonebot.plugin import PluginMetadata

from ...tools import get_uid
from ...su_manager import (
    init_superusers_table,
    is_su,
    get_su_level,
    register_su,
    SU_LEVEL_TRUSTED,
)

__plugin_meta__ = PluginMetadata(
    name="su_register",
    description="超级用户注册管理",
    usage="注册su 激活码",
)


async def _validate_activation_code(code: str, uid: int) -> int | None:
    """
    验证激活码是否有效。

    通过远程服务获取正确的激活码进行比对。
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://127.0.0.1:5000/verify",
                params={"uid": uid, "key": code},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("valid"):
                        return data.get("level")
                    return None
                else:
                    logger.error(f"[su_register] 验证激活码失败，HTTP状态码: {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"[su_register] 验证激活码异常: {e}")
        return None


# ===== 启动时初始化表 =====
driver = get_driver()


@driver.on_startup
async def init_su_register():
    """初始化 SU 注册插件"""
    init_superusers_table()
    logger.info("[su_register] SU 注册插件初始化完成")


# ===== 注册su 命令 =====
register_su_cmd = on_command("注册su", priority=5, block=True)


@register_su_cmd.handle()
async def handle_register_su(
    event: Event,
    bot: Bot,
    uid: int = Depends(get_uid),
    args: Message = CommandArg()
):
    """处理 SU 注册命令"""
    # 检查是否已经是 SU
    if is_su(uid):
        current_level = get_su_level(uid)
        await register_su_cmd.finish(
            f"你已经是 SU 用户了（权限等级: {current_level}）",
        )

    # 解析激活码
    activation_code = args.extract_plain_text().strip()
    if not activation_code:
        await register_su_cmd.finish(
            "请提供激活码！\n用法: 注册su 激活码",
        )

    # 验证激活码
    activation_level = await _validate_activation_code(activation_code, uid)
    if activation_level is None:
        await register_su_cmd.finish(
            "激活码无效！（使用 注册激活码 可以获取你的激活码）",
        )

    # 注册为验证服务返回的 SU 等级
    if register_su(uid, activation_level, activation_code):
        if activation_level == SU_LEVEL_TRUSTED:
            success_msg = (
                "✅ SU 注册成功！\n"
                f"权限等级: {activation_level}\n"
                "注意: trusted 用户不参与排行榜。"
            )
        else:
            success_msg = (
                "✅ SU 注册成功！\n"
                f"权限等级: {activation_level}\n"
                "恭喜通关！"
            )
        await register_su_cmd.finish(
            success_msg,
        )
    else:
        await register_su_cmd.finish(
            "注册失败，请稍后再试或联系管理员。",
        )
