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

# 配置存储：建表 + 旧文件迁移 + 一次性加载进内存。
# 必须在子插件加载前完成（fishing 等插件在导入期读取配置值）。
from . import config_store
from .config_store import config as koinori_config

_PLUGIN_DIR = Path(__file__).resolve().parent
_migrated_superusers = config_store.init_config_store(
    str(_PLUGIN_DIR / "src" / "database" / "koinoribot.db")
)
config_store.ensure_passwd_file(_migrated_superusers)

# 导入核心模块
from . import uid_manager
from . import money
from . import resources
from . import nickname
from . import tools as _tools
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
    uid_manager.init_uid_database()
    money.init_money_database()
    nickname.init_nickname_database()

    # 读取官Bot AppID配置
    if koinori_config.qqbot_appid:
        _tools.set_qqbot_appid(koinori_config.qqbot_appid, koinori_config.qqbot_openid_api)
        nonebot.logger.info(f"已加载官Bot AppID: {koinori_config.qqbot_appid}")
    else:
        nonebot.logger.warning("config 表中 qqbot_appid 为空，官Bot用户昵称将显示为默认值")

    if not config_store.get_passwd_superusers():
        nonebot.logger.warning(
            "passwd.py 的 SUPERUSERS 为空：当前不存在等级为 0 的超级用户，"
            "“冰祈配置”指令将提示修改 passwd.py"
        )

    nonebot.logger.info("Koinoribot NB2 初始化完成")


# 8889 配置面板
from . import config_web


@driver.on_startup
async def start_config_panel():
    await config_web.start_config_web()


@driver.on_shutdown
async def stop_config_panel():
    await config_web.stop_config_web()


# 加载子插件
sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath("plugins").resolve())
)
