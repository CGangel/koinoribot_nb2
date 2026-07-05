"""
Koinoribot NB2 - 主插件入口

从旧版 hoshinobot/nonebot1.8 迁移的 koinoribot
支持 OneBot V11 和 QQ-Bot 双协议
"""

from pathlib import Path
import nonebot
from nonebot import get_plugin_config, get_driver
from nonebot.plugin import PluginMetadata

from .config import Config
from ._config_bootstrap import ensure_koinori_config

_koinori_config_path, _koinori_config_created = ensure_koinori_config()

# 导入核心模块
from . import uid_manager
from . import money
from . import resources
from . import nickname
from .koinori_config import config as koinori_config
from . import tools as _tools
from .db import run_migrations

__plugin_meta__ = PluginMetadata(
    name="koinoribot_nb2",
    description="Koinoribot NoneBot2 版本 - 集成多种娱乐功能",
    usage="签到、钓鱼、宠物、炒股、红包等功能",
    config=Config,
)

# 获取配置
config = get_plugin_config(Config)

# 获取驱动器
driver = get_driver()


@driver.on_startup
async def init_koinoribot():
    """初始化 koinoribot"""
    # 设置资源目录
    plugin_dir = Path(__file__).parent
    src_dir = plugin_dir / "src"
    resources.set_resource_dir(src_dir)
    
    # 设置数据库路径
    db_path = src_dir / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    uid_manager.set_database_path(str(db_path))
    money.set_database_path(str(db_path))
    nickname.set_db_path(str(db_path))
    
    # 初始化数据库
    run_migrations()
    
    # 读取官Bot AppID配置
    if koinori_config.qqbot_appid:
        _tools.set_qqbot_appid(koinori_config.qqbot_appid, koinori_config.qqbot_openid_api)
        nonebot.logger.info(f"已加载官Bot AppID: {koinori_config.qqbot_appid}")
    else:
        nonebot.logger.warning("koinori_config 中 qqbot_appid 为空，官Bot用户昵称将显示为默认值")

    if _koinori_config_created:
        nonebot.logger.info(f"已从模板创建配置文件: {_koinori_config_path}")
    
    nonebot.logger.info("Koinoribot NB2 初始化完成")


# 加载子插件
sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath("plugins").resolve())
)
