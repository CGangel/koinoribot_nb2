"""mlhelp - 冰祈帮助：全部面向用户的功能总览与各系统帮助指引。"""


from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata

from ...config_store import config
from ...tools import build_forward_chain, send_group_forward_msg

__plugin_meta__ = PluginMetadata(
    name="mlhelp",
    description="冰祈帮助 - 全功能总览与各系统帮助指引",
    usage="冰祈帮助 / {bot_name}帮助",
)


def _bot_name() -> str:
    return str(getattr(config, "bot_name", "梦灵") or "").strip() or "梦灵"


# 别名按加载时的 bot_name 固定（bot_name 改名后需重启更新别名）
mlhelp_cmd = on_command(
    "冰祈帮助", aliases={f"{_bot_name()}帮助"}, priority=4, block=True
)


def _help_text() -> str:
    name = _bot_name()
    return f"""【冰祈帮助】
{name}是一个多功能bot，各系统只做概括，详细玩法发送对应的帮助指令～

==AI 系列==
  AI聊天 —— 与{name}自然对话，支持图片识别与长期记忆（消耗星星）；
    发送 聊天帮助 查看
  冰祈画图 <描述> —— AI 生成图片（消耗金币，有每日次数限制）
  冰祈修图 <描述> + 图片 —— 按描述编辑图片

==日常==
  签到 / 我的钱包 —— 每日签到领奖励，查看资产
  转账 —— 给其他用户转账（转账uid/转账qq）
  领低保 —— 金币见底时的保障金
  金币排行榜 —— 查看金币排行
  发红包 / 抢红包 —— 群红包玩法
  晚安 —— 花 50 金币说晚安
  鲁迅说过 —— 一句鲁迅名言

==钓鱼==
  钓鱼、渔具养成、水族箱、图鉴与商店，发送 钓鱼帮助 查看

==宠物==
  扭蛋、领养、投喂、玩耍、进化与技能，发送 宠物帮助 查看
  （宠物技能详情发送 技能帮助）

==修仙==
  修炼、突破与渡劫飞升体系，发送 飞升帮助 查看

==炒股==
  股票交易、行情与持仓，发送 股票帮助 查看

==漂流瓶==
  扔/捡/评论漂流瓶，与有缘人隔空交流，发送 漂流瓶帮助 查看

==小游戏==
  猜单词 / 猜数字 / 猜日语 / 24点 —— 休闲小游戏
  今天吃什么 / 我以后想吃 <食物> —— 选择困难症救星，发送 吃什么帮助 查看

==个人==
  冰祈请叫我 <称呼> —— 自定义{name}对你的称呼
  上传头像 / 上传签到图片 —— 自定义签到卡片样式
  我的uid / 绑定账号 / 解绑账号 —— 跨平台账号管理"""


@mlhelp_cmd.handle()
async def handle_mlhelp(bot: Bot, event: Event, matcher: Matcher):
    text = _help_text()
    sent = False
    try:
        chain = await build_forward_chain(bot, [text])
        await send_group_forward_msg(
            event, bot, chain, width=1000, two_column=True
        )
        sent = True
    except Exception as error:
        logger.warning(f"[mlhelp] 合并转发发送失败，回退纯文本: {error}")
    if sent:
        await matcher.finish()
    await matcher.finish(text)
