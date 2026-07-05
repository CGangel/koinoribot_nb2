"""
炒股插件 - chaogu

完整迁移自旧版 koinoribot
功能：股票交易、行情查看、持仓管理、市场事件、幸运游戏
"""

import math
import random
import time
import asyncio
import gc
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict

from nonebot import on_command, on_regex, get_driver, require
from nonebot.rule import CommandRule
from nonebot.exception import FinishedException
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Event, Bot, Message
from nonebot.params import Depends, RegexGroup, CommandArg
from nonebot import logger

from ...money import money
from ...koinori_config import config
from ...tools import get_uid, send_group_forward_msg, build_forward_chain, get_at_uid, build_image_msg
from ..fishing.util import DatabaseManager as FishingDB
from ...nickname import get_user_nickname
from ..ai_draw import add_free_draw_count

from .stock_utils import (
    set_db_path,
    STOCKS, MARKET_EVENTS, MANUAL_EVENT_TYPES, HISTORY_DURATION_HOURS,
    get_stock_data, save_stock_data,
    get_user_portfolios, save_user_portfolios,
    get_user_portfolio, update_user_portfolio,
    get_current_stock_price, get_stock_price_history,
    generate_stock_chart,
    # 幸运游戏相关
    update_gamble_record, get_all_gamble_record, get_user_gamble_record,
    check_daily_gamble_limit, record_gamble_today,
    # 转盘相关
    MAX_TURNS_PER_DAY, check_turntable_limit, record_turntable_spin,
    # 低保相关
    check_daily_prek, record_daily_prek
)

# uid_manager 用于 QQ号转UID
from ... import uid_manager
# 宠物相关
from ..chongwu.pet import get_user_pet, add_user_item
# SU 权限管理
from ...su_manager import (
    is_su, is_su_contributor, get_all_su_uids, check_su_permission,
    record_su_usage,
)
__plugin_meta__ = PluginMetadata(
    name="chaogu",
    description="股票市场系统 - 完整版（含幸运游戏、转盘、低保）",
    usage="股票列表 / 买入 / 卖出 / 我的股仓 / 幸运游戏 / 幸运大转盘 / 领低保 等",
)

# 事件触发概率配置
EVENT_PROBABILITY = 0.9999
EVENT_COOLDOWN = 3500

# ===== 豪赌游戏配置 =====
MAX_GAMBLE_ROUNDS = 5

# 赌博状态管理 (内存中)
# key: uid, value: {'round': int, 'confirmed': bool, 'active': bool, 'win': float, 'start_gold': int, 'gold': int}
gambling_sessions: Dict[int, dict] = {}

# ===== 股票帮助 =====
stock_help_cmd = on_command("股票帮助", aliases={"炒股帮助"}, priority=5, block=True)

# 炒股帮助内容（迁移自old_bot完整版）
help_chaogu = '''炒股游戏帮助：

温馨提醒：股市有风险，切莫上头。

**指令列表：**
1.  股票列表：查看所有股票的名字和实时价格
2.  买入 [股票名称] [具体数量]：例如：买入 萝莉股 10
3.  卖出 [股票名称] [具体数量]：例如：卖出 萝莉股 10
4.  我的股仓：查看自己现在持有的股票
5.  [股票名称]走势：查看某一股票的价格折线图走势。
    例如：萝莉股走势
6.  市场动态：查看最近市场上的事件，可能利好或利空
7.  幸运转盘：每日抽奖
8.  幸运游戏：暴富or破产
9.  领低保：领取每日低保
10. 金币排行榜：查看有哪些有元人
11. 游戏记录：查看自己的幸运游戏记录
12. 豪赌榜：查看幸运游戏排行榜（正向）
13. 戒赌榜：查看幸运游戏排行榜（反向）

初始股票价格：
    "萝莉股": 50.0,
    "猫娘股": 60.0,
    "魔法少女股": 70.0,
    "梦月股": 250.0,
    "梦馨股": 100.0,
    "高达股": 40.0,
    "雾月股": 120.0,
    "傲娇股": 60.0,
    "病娇股": 30.0,
    "梦灵股": 120.0,
    "铃音股": 110.0,
    "音祈股": 500.0,
    "梦铃股": 250.0,
    "姐妹股": 250.0,
    "橘馨股": 250.0,
    "白芷股": 250.0,
    "雾织股": 250.0,
    "筑梦股": 250.0,
    "摇篮股": 250.0,
    "筑梦摇篮股": 500.0,
'''

@stock_help_cmd.handle()
async def handle_stock_help(event: Event, bot: Bot):
    # 构建转发消息链
    chain = await build_forward_chain(bot, [help_chaogu])
    # 发送转发消息
    await send_group_forward_msg(event, bot, chain)


# ===== 股票列表 =====
stock_list_cmd = on_command("股票列表", priority=5, block=True)

@stock_list_cmd.handle()
async def handle_stock_list(event: Event, bot: Bot):
    stock_data = await get_stock_data()
    
    if not stock_data:
        await stock_list_cmd.finish("暂时无法获取股市数据，请稍后再试。")
    
    lines = ["📈 当前股市行情概览:"]
    
    # 按初始价格排序
    stock_list = []
    for stock_name, data in stock_data.items():
        initial_price = data["initial_price"]
        current_price = await get_current_stock_price(stock_name, stock_data)
        stock_list.append((stock_name, initial_price, current_price))
    
    stock_list.sort(key=lambda x: x[1])
    
    for stock_name, initial_price, current_price in stock_list:
        if current_price is not None:
            history = stock_data[stock_name].get("history", [])
            
            if len(history) > 1:
                prev_price = history[-2][1]
                change_percent = (current_price - prev_price) / prev_price * 100
            else:
                change_percent = (current_price - initial_price) / initial_price * 100
            
            symbol = "↑" if change_percent >= 0 else "↓"
            lines.append(
                f"◽ {stock_name}: {current_price:.2f}金币 "
                f"(初始{initial_price:.2f}) [{symbol}{abs(change_percent):.1f}%]"
            )
        else:
            lines.append(f"◽ {stock_name}: 价格未知 (初始: {initial_price:.2f})")
    
    # 使用转发消息发送
    chain = await build_forward_chain(bot, ["\n".join(lines)])
    await send_group_forward_msg(event, bot, chain)


stock_trend_cmd = on_regex(r'^(.+股)走势$', priority=5, block=True)

@stock_trend_cmd.handle()
async def handle_stock_trend(event: Event, bot: Bot, groups: tuple = RegexGroup()):
    chart_buf = b64_str = None
    try:
        # 使用 RegexGroup 获取匹配到的股票名称
        if not groups or len(groups) < 1:
            logger.warning("股票走势: 正则匹配组为空")
            await stock_trend_cmd.finish("无法解析股票名称，请检查指令格式。")
        
        stock_name = groups[0]
        logger.info(f"股票走势: 开始处理 {stock_name}")
        
        if stock_name not in STOCKS:
            await stock_trend_cmd.finish(f"未知股票: {stock_name}。可用的股票有: {', '.join(STOCKS.keys())}")
        
        stock_data = await get_stock_data()
        logger.info(f"股票走势: 已获取股票数据")
        
        history = await get_stock_price_history(stock_name, stock_data)
        logger.info(f"股票走势: {stock_name} 历史记录条数={len(history) if history else 0}")
        
        if not history:
            initial_price = stock_data[stock_name]["initial_price"]
            await stock_trend_cmd.finish(f"{stock_name} 暂时还没有价格历史记录。初始价格为 {initial_price:.2f} 金币。")
        
        # 在线程池中生成图表
        logger.info(f"股票走势: 开始生成图表")
        loop = asyncio.get_running_loop()
        try:
            chart_buf = await asyncio.wait_for(
                loop.run_in_executor(
                    None, generate_stock_chart, stock_name, history, stock_data
                ),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            logger.error(f"股票走势: 图表生成超时 (10s)")
            chart_buf = None
        except Exception as e:
            logger.error(f"股票走势: 图表生成出错: {e}")
            chart_buf = None
        
        logger.info(f"股票走势: 图表生成完成，结果={chart_buf is not None}")
        
        if chart_buf:
            # 转换为图片消息段并发送
            image_bytes = chart_buf.getvalue()
            img_msg = build_image_msg(event, image_bytes)
            logger.info(f"股票走势: 发送图片")
            await stock_trend_cmd.finish(img_msg)
        else:
            # 图表生成失败，发送文字版
            current_price = history[-1][1]
            initial_price = stock_data[stock_name]["initial_price"]
            min_price = min(p for _, p in history)
            max_price = max(p for _, p in history)
            
            if len(history) > 1:
                first_price = history[0][1]
                change = (current_price - first_price) / first_price * 100
            else:
                change = (current_price - initial_price) / initial_price * 100
            
            symbol = "📈" if change >= 0 else "📉"
            
            msg = f"""{symbol} 【{stock_name}】走势

💰 当前价格: {current_price:.2f}金币
📊 初始价格: {initial_price:.2f}金币
📈 最高价格: {max_price:.2f}金币
📉 最低价格: {min_price:.2f}金币
{'↑' if change >= 0 else '↓'} 涨跌幅: {change:+.2f}%
⏰ 数据点数: {len(history)}个

（图表生成失败，显示文字版）"""
            
            await stock_trend_cmd.finish(msg)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"股票走势处理异常: {e}")
        import traceback
        traceback.print_exc()
        await stock_trend_cmd.finish(f"处理股票走势时发生错误: {e}")
    finally:
        if chart_buf:
            chart_buf.close()
        del chart_buf, b64_str
        gc.collect()


# ===== 买入股票 =====
buy_stock_cmd = on_command("买入", priority=5, block=True)

@buy_stock_cmd.handle()
async def handle_buy_stock(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    # 检查是否在赌博中
    if uid in gambling_sessions and gambling_sessions[uid].get('active', False):
        await buy_stock_cmd.finish("⚠️ 你正在进行幸运游戏，无法进行股票交易。请先完成游戏或'见好就收'。", at_sender=True)
    
    # 解析参数
    arg_text = args.extract_plain_text().strip()
    parts = arg_text.split()
    
    if not parts or len(parts) < 2:
        await buy_stock_cmd.finish("无法解析购买指令，请检查格式。(例：买入 萝莉股 10)", at_sender=True)
    
    stock_name = parts[0]
    try:
        amount_to_buy = int(parts[1])
    except ValueError:
        await buy_stock_cmd.finish("购买数量必须是正整数", at_sender=True)
    
    if amount_to_buy <= 0:
        await buy_stock_cmd.finish("购买数量必须是正整数", at_sender=True)
    
    if stock_name not in STOCKS:
        await buy_stock_cmd.finish(f"未知股票: {stock_name}", at_sender=True)
    
    # 检查持仓限制
    user_portfolio = await get_user_portfolio(uid)
    current_holding = user_portfolio.get(stock_name, 0)
    
    max_type = getattr(config, 'maxtype', 5)
    max_count = getattr(config, 'maxcount', 10000)
    
    if len(user_portfolio) >= max_type and stock_name not in user_portfolio:
        await buy_stock_cmd.finish(
            f"每位用户最多持有{max_type}种不同股票，您已持有{len(user_portfolio)}种。", at_sender=True)
    
    if current_holding >= max_count:
        await buy_stock_cmd.finish(
            f"每种股票持有上限为{max_count}股，请先卖出部分。", at_sender=True)
    
    if current_holding + amount_to_buy > max_count:
        amount_to_buy = max_count - current_holding
    
    current_price = await get_current_stock_price(stock_name)
    if current_price is None:
        await buy_stock_cmd.finish(f"{stock_name} 当前无法交易", at_sender=True)
    
    # 计算成本
    base_cost = current_price * amount_to_buy
    fee = math.ceil(base_cost * 0.01)
    total_cost = math.ceil(base_cost) + fee
    
    user_gold = money.gold
    if user_gold < total_cost:
        await buy_stock_cmd.finish(
            f"金币不足！购买{amount_to_buy}股{stock_name}需要{total_cost}金币"
            f"（含{fee}手续费），您只有{user_gold}金币。", at_sender=True)
    
    # 执行购买
    money.gold -= total_cost
    if await update_user_portfolio(uid, stock_name, amount_to_buy):
        await buy_stock_cmd.finish(
            f"✅ 购买成功！\n"
            f"股票: {stock_name}\n"
            f"数量: {amount_to_buy}股\n"
            f"单价: {current_price:.2f}金币\n"
            f"费用: {total_cost}金币（含{fee}手续费）", at_sender=True)

    money.gold += total_cost
    await buy_stock_cmd.finish("购买失败，金币已退回。", at_sender=True)


# ===== 卖出股票 =====
sell_stock_cmd = on_command("卖出", priority=5, block=True)

@sell_stock_cmd.handle()
async def handle_sell_stock(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    # 检查是否在赌博中
    if uid in gambling_sessions and gambling_sessions[uid].get('active', False):
        await sell_stock_cmd.finish("⚠️ 你正在进行幸运游戏，无法进行股票交易。请先完成游戏或'见好就收'。", at_sender=True)
    
    arg_text = args.extract_plain_text().strip()
    parts = arg_text.split()
    
    if not parts:
        await sell_stock_cmd.finish("无法解析卖出指令，请检查格式。(例：卖出 萝莉股 10)", at_sender=True)
    
    stock_name = parts[0]
    amount_to_sell = 9999
    
    if len(parts) > 1:
        try:
            amount_to_sell = int(parts[1])
        except ValueError:
            pass  # 默认为全部卖出
    
    if stock_name not in STOCKS:
        await sell_stock_cmd.finish(f"未知股票: {stock_name}", at_sender=True)
    
    user_portfolio = await get_user_portfolio(uid)
    current_holding = user_portfolio.get(stock_name, 0)
    
    if current_holding == 0:
        await sell_stock_cmd.finish(f"您没有持有{stock_name}", at_sender=True)
    
    if current_holding < amount_to_sell:
        amount_to_sell = current_holding
    
    current_price = await get_current_stock_price(stock_name)
    if current_price is None:
        await sell_stock_cmd.finish(f"{stock_name} 当前无法交易", at_sender=True)
    
    # 计算收入
    base_earnings = current_price * amount_to_sell
    fee = math.floor(base_earnings * 0.02)
    total_earnings = math.floor(base_earnings) - fee
    
    # 执行出售
    if await update_user_portfolio(uid, stock_name, -amount_to_sell):
        money.gold += total_earnings
        await sell_stock_cmd.finish(
            f"✅ 卖出成功！\n"
            f"股票: {stock_name}\n"
            f"数量: {amount_to_sell}股\n"
            f"单价: {current_price:.2f}金币\n"
            f"收入: {total_earnings}金币（扣除{fee}手续费）", at_sender=True)
    else:
        await sell_stock_cmd.finish("卖出失败，更新持仓时发生错误。", at_sender=True)


# ===== 我的股仓 =====
my_portfolio_cmd = on_command("我的股仓", priority=5, block=True)

@my_portfolio_cmd.handle()
async def handle_my_portfolio(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    user_portfolio = await get_user_portfolio(uid)
    
    if not user_portfolio:
        await my_portfolio_cmd.finish("您的股仓是空的，快去买点股票吧！", at_sender=True)
    
    stock_data = await get_stock_data()
    
    lines = ["您的股仓详情:"]
    total_value = 0.0
    
    for stock_name, amount in user_portfolio.items():
        current_price = await get_current_stock_price(stock_name, stock_data)
        if current_price is None:
            current_price = stock_data.get(stock_name, {}).get("initial_price", 0)
        
        value = current_price * amount
        total_value += value
        lines.append(f"• {stock_name}: {amount}股 × {current_price:.2f} = {value:.2f}金币")
    
    lines.append(f"\n📊 股仓总价值: {total_value:.2f}金币")
    
    await my_portfolio_cmd.finish("\n".join(lines), at_sender=True)


# ===== 市场动态 =====
market_events_cmd = on_command("市场动态", priority=5, block=True)

@market_events_cmd.handle()
async def handle_market_events(event: Event, bot: Bot):
    stock_data = await get_stock_data()
    
    # 收集所有事件
    all_events = []
    for stock_name, data in stock_data.items():
        for evt in data.get("events", []):
            evt["stock"] = stock_name
            all_events.append(evt)
    
    all_events.sort(key=lambda x: x["time"], reverse=True)
    
    if not all_events:
        await market_events_cmd.finish("近期没有重大市场事件发生。")
    
    recent_events = all_events[:5]
    
    lines = ["📢 最新市场动态:"]
    for evt in recent_events:
        event_time = datetime.fromtimestamp(evt["time"]).strftime('%m-%d %H:%M')
        
        if evt.get("scope") == "global":
            lines.append(f"【{event_time}】{evt['message']}\n  影响范围: 所有股票")
        else:
            if evt.get("old_price") and evt.get("new_price"):
                change_percent = (evt["new_price"] - evt["old_price"]) / evt["old_price"] * 100
                change_dir = "↑" if change_percent >= 0 else "↓"
                lines.append(
                    f"【{event_time}】{evt['message']}\n"
                    f"  {evt['stock']}价格: {evt['old_price']:.2f} → {evt['new_price']:.2f} "
                    f"({change_dir}{abs(change_percent):.1f}%)"
                )
            else:
                lines.append(f"【{event_time}】{evt.get('message', '未知事件')}")
    
    # 使用转发消息发送
    chain = await build_forward_chain(bot, ["\n\n".join(lines)])
    await send_group_forward_msg(event, bot, chain)


# ===== 股价更新定时任务 =====
async def hourly_price_update():
    """定时更新所有股票价格"""
    try:
        logger.info("Running hourly stock price update...")
        stock_data = await get_stock_data()
        current_time = time.time()
        cutoff_time = current_time - HISTORY_DURATION_HOURS * 3600
        
        changed = False
        event_triggered = False
        affected_stocks = []
        
        # 获取最后事件时间
        try:
            last_event_time = max([
                max([e["time"] for e in stock.get("events", [])], default=0)
                for stock in stock_data.values()
            ], default=0)
        except:
            last_event_time = 0
        
        can_trigger_event = (current_time - last_event_time) >= EVENT_COOLDOWN
        
        # 决定是否触发事件
        if can_trigger_event and random.random() < EVENT_PROBABILITY:
            event_type = random.choice(list(MARKET_EVENTS.keys()))
            event_info = MARKET_EVENTS[event_type]
            event_triggered = True
            
            if event_info["scope"] == "single":
                affected_stocks = [random.choice(list(STOCKS.keys()))]
            else:
                affected_stocks = list(STOCKS.keys())
            
            # 应用事件影响
            for stock_name in affected_stocks:
                if stock_name not in stock_data:
                    continue
                
                if stock_data[stock_name]["history"]:
                    current_price = stock_data[stock_name]["history"][-1][1]
                else:
                    current_price = stock_data[stock_name]["initial_price"]
                
                new_price = event_info["effect"](current_price)
                new_price = max(stock_data[stock_name]["initial_price"] * 0.01,
                               min(new_price, stock_data[stock_name]["initial_price"] * 2.00))
                new_price = round(new_price, 2)
                
                template = random.choice(event_info["templates"])
                event_message = template.format(stock=stock_name)
                
                stock_data[stock_name]["events"].append({
                    "time": current_time,
                    "type": event_type,
                    "message": event_message,
                    "old_price": current_price,
                    "new_price": new_price
                })
                stock_data[stock_name]["events"] = stock_data[stock_name]["events"][-10:]
                stock_data[stock_name]["history"].append((current_time, new_price))
                changed = True
        
        # 正常价格波动
        for name, data in stock_data.items():
            if event_triggered and name in affected_stocks:
                continue
            
            initial_price = data["initial_price"]
            history = data.get("history", [])
            
            # 清理旧数据
            history = [(ts, price) for ts, price in history if ts >= cutoff_time]
            
            if not history:
                current_price = initial_price
            else:
                current_price = history[-1][1]
            
            # 随机波动
            change_percent = random.uniform(-0.05, 0.05)
            regression_factor = 0.03
            change_percent += regression_factor * (initial_price - current_price) / current_price
            
            new_price = current_price * (1 + change_percent)
            new_price = max(initial_price * 0.01, min(new_price, initial_price * 2.00))
            new_price = round(new_price, 2)
            
            history.append((current_time, new_price))
            stock_data[name]["history"] = history
            changed = True
        
        if changed:
            await save_stock_data(stock_data)
            logger.info("Stock prices updated and saved.")
    except Exception as e:
        logger.error(f"股价更新失败: {e}")


# ===== 初始化 =====
driver = get_driver()

@driver.on_startup
async def init_chaogu():
    """初始化炒股插件"""
    from pathlib import Path
    plugin_dir = Path(__file__).parent.parent.parent
    db_path = plugin_dir / "src" / "database" / "koinoribot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    set_db_path(str(db_path))
    
    # 初始化股票数据
    stock_data = await get_stock_data()
    for name, initial_price in STOCKS.items():
        if name not in stock_data:
            stock_data[name] = {
                "initial_price": initial_price,
                "history": [],
                "events": []
            }
    await save_stock_data(stock_data)
    
    logger.info("Chaogu 炒股插件初始化完成")


# 定时任务（使用 APScheduler）
try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler
    scheduler.add_job(hourly_price_update, "cron", hour="*", minute="0", id="stock_price_update")
    logger.info("股价更新定时任务已注册")
except Exception as e:
    logger.warning(f"定时任务注册失败: {e}，需要手动安装 nonebot_plugin_apscheduler")


# ===== 幸运游戏（一场豪赌）=====

def get_gamble_win_probability(gold: int, uid: int) -> float:
    """根据金币数量计算获胜概率"""
    if gold < 10000:
        win = 0.90
    elif gold < 50000:
        win = 0.70
    elif gold < 100000:
        win = 0.60
    elif gold < 1000000:
        win = 0.50
    elif gold < 10000000:
        win = 0.30
    else:
        win = 0.10
    gambling_sessions[uid]['win'] = win
    return win


async def perform_gamble_round(uid: int) -> dict:
    """执行一轮赌博并更新虚拟金币"""
    old_gold = gambling_sessions[uid]['gold']
    if old_gold is None or old_gold <= 0:
        return {"success": False, "message": "你没有金币可以用来幸运游戏。"}
    
    # 计算胜率
    get_gamble_win_probability(old_gold, uid)
    win_probability = gambling_sessions[uid]['win']
    if is_su(uid):
        win_probability += 0.5
    
    win = random.random() < win_probability
    
    # 计算新金币
    if win:
        new_gold = int(old_gold * 2)
        gambling_sessions[uid]['gold'] = new_gold
        outcome = "胜利"
        multiplier = 2
    else:
        new_gold = max(1, int(old_gold * 0.01))
        gambling_sessions[uid]['gold'] = new_gold
        outcome = "失败"
        multiplier = 0.01
    
    # 更新胜率
    get_gamble_win_probability(new_gold, uid)
    
    return {
        "success": True,
        "outcome": outcome,
        "old_gold": old_gold,
        "new_gold": new_gold,
        "multiplier": multiplier
    }


async def gold_change_record(uid: int, start_gold: int, final_gold: int) -> str:
    """计算并应用金币变化，返回结果消息"""
    change = final_gold - start_gold
    wallet = money.of(uid)
    await update_gamble_record(uid, change)
    if change > 0:
        wallet.gold += change
        record = f"\n最终金币变化：+{change}"
    else:
        change = change * -1
        wallet.gold -= change
        record = f"\n最终金币变化：-{change}"
    return record


# ===== 幸运游戏 =====
gamble_start_old_cmd = on_command("一场豪赌", priority=5, block=True)
@gamble_start_old_cmd.handle()
async def handle_start_gamble_old(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    await gamble_start_old_cmd.finish("由于不可抗力，本功能已更名为 幸运游戏", at_sender=True)

gamble_start_cmd = on_command("幸运游戏", priority=5, block=True)
@gamble_start_cmd.handle()
async def handle_start_gamble(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    # 检查是否已在游戏中
    if uid in gambling_sessions and gambling_sessions[uid].get('active', False):
        await gamble_start_cmd.finish("你正在进行幸运游戏，请先完成或使用 '见好就收' 结束当前游戏。", at_sender=True)
    
    # 检查每日限制
    
    if not await check_daily_gamble_limit(uid) and not is_su(uid):
        await gamble_start_cmd.finish("你今天已经赌过了，明天再来吧！人生的大起大落可经不起天天折腾哦。", at_sender=True)
    
    # 获取当前金币
    gold = money.gold
    luckygold = money.luckygold
    
    if gold <= 0:
        await gamble_start_cmd.finish("欠债/失信用户，禁止游戏。", at_sender=True)
    
    # 初始化会话状态
    gambling_sessions[uid] = {
        'round': 0,
        'confirmed': False,
        'active': False,
        'win': 0,
        'start_gold': gold,
        'gold': gold
    }
    
    get_gamble_win_probability(gold, uid)
    win = gambling_sessions[uid]['win'] * 100
    
    rules = f"""\n🎲 幸运游戏 规则 🎲：
1. 连续{MAX_GAMBLE_ROUNDS}轮幸运游戏，每一轮消耗1枚幸运币，你所持有的【全部金币】都有几率翻倍，或者骤减。
2. 你可以在任何一轮结束后选择 '见好就收' 带着当前金币离场。
3. 若任意一轮失败，则立即结束并离场。
【警告】：当前金币已被记录，幸运游戏过程中，通过幸运游戏以外的途径增减的金币，将不影响游戏结果。
你当前持有 {gold} 枚金币
你当前持有 {luckygold} 枚幸运币
当前获胜概率: {win}%
发送 确认 继续。
发送 算了 取消。"""
    await gamble_start_cmd.finish(rules, at_sender=True)


# ===== 确认开始幸运游戏 =====
gamble_confirm_cmd = on_command("确认", priority=5, block=True)

@gamble_confirm_cmd.handle()
async def handle_confirm_gamble(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    # 检查用户是否处于待确认状态
    if uid not in gambling_sessions or gambling_sessions[uid].get('confirmed', False):
        return  # 不在等待确认状态，忽略
    
    gold = money.gold
    luckygold = money.luckygold
    start_gold = gambling_sessions[uid]['start_gold']
    
    if gold != start_gold:
        await gamble_confirm_cmd.finish(f"检测到钱包金币发生了改变: \n{start_gold}金币 → {gold}金币\n本次会话作废，请重新开局。", at_sender=True)
        del gambling_sessions[uid]
    
    if luckygold < 1:
        del gambling_sessions[uid]
        await gamble_confirm_cmd.finish("你没有足够的幸运币参与幸运游戏。", at_sender=True)
    
    money.luckygold -= 1
    
    # 标记确认，激活会话
    gambling_sessions[uid]['confirmed'] = True
    gambling_sessions[uid]['active'] = True
    gambling_sessions[uid]['round'] = 1
    
    await record_gamble_today(uid)
    
    result = await perform_gamble_round(uid)
    
    if not result["success"]:
        del gambling_sessions[uid]
        await gamble_confirm_cmd.finish(f"幸运游戏失败：{result['message']}", at_sender=True)
    
    win = gambling_sessions[uid]['win'] * 100
    
    if result['outcome'] == "胜利":
        message = f"""\n第1轮结果:【{result['outcome']}】
金币变化：{result['old_gold']} -> {result['new_gold']} (x{result['multiplier']})"""
        message += f"\n发送 '继续' 进行第 {gambling_sessions[uid]['round'] + 1} 轮，或发送 '见好就收' 离场。"
        message += f"\n当前获胜概率: {win}%"
        await gamble_confirm_cmd.finish(message, at_sender=True)
    else:
        start_gold = gambling_sessions[uid]['start_gold']
        final_gold = gambling_sessions[uid]['gold']
        record = await gold_change_record(uid, start_gold, final_gold)
        del gambling_sessions[uid]
        message = f"""\n第1轮结果:【{result['outcome']}】
金币变化：{result['old_gold']} -> {result['new_gold']} (x{result['multiplier']})"""
        message += f"\n\n本局失败，已强制离场。"
        message += record
        await gamble_confirm_cmd.finish(message, at_sender=True)


# ===== 继续豪赌 =====
gamble_continue_cmd = on_command("继续", priority=5, block=True)

@gamble_continue_cmd.handle()
async def handle_continue_gamble(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    # 检查用户是否在活跃的游戏中
    if uid not in gambling_sessions or not gambling_sessions[uid].get('active', False):
        return  # 不在游戏中，忽略
    
    current_round = gambling_sessions[uid]['round']
    luckygold = money.luckygold
    
    if luckygold < 1:
        await gamble_continue_cmd.finish("你没有足够的幸运币继续。发送 见好就收 可以退出游戏~", at_sender=True)
    
    money.luckygold -= 1
    
    # 进入下一轮
    next_round = current_round + 1
    gambling_sessions[uid]['round'] = next_round
    
    result = await perform_gamble_round(uid)
    
    if not result["success"]:
        del gambling_sessions[uid]
        await gamble_continue_cmd.finish(f"幸运游戏失败：{result['message']}", at_sender=True)
    
    win = gambling_sessions[uid]['win'] * 100
    
    message = f"""\n第 {next_round} 轮结果：【{result['outcome']}】
金币变化：{result['old_gold']} -> {result['new_gold']} (x{result['multiplier']})"""
    
    if gambling_sessions[uid]['round'] >= MAX_GAMBLE_ROUNDS:
        message += f"\n你已完成全部 {MAX_GAMBLE_ROUNDS} 轮幸运游戏，游戏结束！"
        start_gold = gambling_sessions[uid]['start_gold']
        final_gold = gambling_sessions[uid]['gold']
        record = await gold_change_record(uid, start_gold, final_gold)
        message += record
        del gambling_sessions[uid]
    elif result['outcome'] == "胜利":
        message += f"\n发送 '继续' 进行第 {gambling_sessions[uid]['round'] + 1} 轮，或发送 '见好就收' 离场。"
        message += f"\n当前获胜概率: {win}%"
    else:
        start_gold = gambling_sessions[uid]['start_gold']
        final_gold = gambling_sessions[uid]['gold']
        record = await gold_change_record(uid, start_gold, final_gold)
        del gambling_sessions[uid]
        message += f"\n\n本局失败，已强制离场。"
        message += record
    
    await gamble_continue_cmd.finish(message, at_sender=True)


# ===== 见好就收/算了 =====
gamble_stop_cmd = on_command("见好就收", aliases={"算了"}, priority=5, block=True)

@gamble_stop_cmd.handle()
async def handle_stop_gamble(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    if uid not in gambling_sessions:
        return  # 不在游戏中，忽略
    
    current_round = gambling_sessions[uid].get('round', 0)
    confirmed = gambling_sessions[uid].get('confirmed', False)
    
    if not confirmed:
        # 在规则确认阶段取消
        del gambling_sessions[uid]
        await gamble_stop_cmd.finish("好吧，谨慎总是好的。游戏已取消。", at_sender=True)
    elif current_round > 0:
        # 赌了几轮后收手
        start_gold = gambling_sessions[uid]['start_gold']
        final_gold = gambling_sessions[uid]['gold']
        record = await gold_change_record(uid, start_gold, final_gold)
        del gambling_sessions[uid]
        await gamble_stop_cmd.finish(f"明智的选择！你在第 {current_round} 轮后选择离场。" + record, at_sender=True)
    else:
        del gambling_sessions[uid]
        await gamble_stop_cmd.finish("游戏已结束。", at_sender=True)


# ===== 豪赌榜 =====
gamble_ranking_cmd = on_command("豪赌榜", aliases={"游戏榜"}, priority=5, block=True)

@gamble_ranking_cmd.handle()
async def handle_gamble_ranking(event: Event, bot: Bot):
    """显示盈利排行榜"""
    all_records = await get_all_gamble_record()
    
    
    su_uids = get_all_su_uids()
    user_net_gains = []
    for uid, records in all_records.items():
        if uid not in su_uids:
            net_gain = records['increase_record'] - records['reduce_record']
            if net_gain > 0:
                user_net_gains.append((uid, net_gain))
    
    sorted_users = sorted(user_net_gains, key=lambda x: x[1], reverse=True)
    
    msg = "梦灵的零花钱都给了谁：\n"
    for i, (uid, net_gain) in enumerate(sorted_users[:10], 1):
        owner_name = get_user_nickname(int(uid)) or f"UID{uid}"
        msg += f"{i}. {owner_name} 累计取走: {net_gain}金币\n"
    
    if len(sorted_users) == 0:
        msg += "暂无零花钱记录"
    
    msg += "\n\n\n使用 冰祈请叫我 可以修改自己的昵称哦~"
    chain = await build_forward_chain(bot, [msg])
    await send_group_forward_msg(event, bot, chain)


# ===== 戒赌榜 =====
gamble_loss_ranking_cmd = on_command("戒赌榜", aliases={"零花钱贡献榜"}, priority=5, block=True)

@gamble_loss_ranking_cmd.handle()
async def handle_gamble_loss_ranking(event: Event, bot: Bot):
    """显示亏损排行榜"""
    all_records = await get_all_gamble_record()
    
    
    su_uids = get_all_su_uids()
    user_contributions = []
    for uid, records in all_records.items():
        if uid not in su_uids:
            net_contribution = records['reduce_record'] - records['increase_record']
            if net_contribution > 0:
                user_contributions.append((uid, net_contribution))
    
    sorted_users = sorted(user_contributions, key=lambda x: x[1], reverse=True)
    
    msg = "梦灵的零花钱来源：\n"
    for i, (uid, net_contribution) in enumerate(sorted_users[:10], 1):
        owner_name = get_user_nickname(int(uid)) or f"UID{uid}"
        msg += f"{i}. {owner_name} 累计存入: {net_contribution}金币\n"
    
    if len(sorted_users) == 0:
        msg += "暂无零花钱记录"
    
    msg += "\n\n\n使用 冰祈请叫我 可以修改自己的昵称哦~"
    chain = await build_forward_chain(bot, [msg])
    await send_group_forward_msg(event, bot, chain)


# ===== 豪赌记录 =====
gamble_record_cmd = on_command("豪赌记录", aliases={"游戏记录"}, priority=5, block=True)

@gamble_record_cmd.handle()
async def handle_gamble_record(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    """显示个人豪赌记录"""
    user_record = await get_user_gamble_record(uid)
    increase_record = user_record['increase_record']
    reduce_record = user_record['reduce_record']
    
    msg = f"\n你已累计将{reduce_record}金币『暂存』在梦灵酱的钱包里；"
    msg += f"\n你已累计从梦灵酱的钱包里拿走了{increase_record}金币。"
    
    if increase_record < reduce_record:
        loss = reduce_record - increase_record
        msg += f'\n\n"唔...一共送给人家{loss}金币的零花钱呢...谢谢你~"'
    elif increase_record > reduce_record:
        win = increase_record - reduce_record
        msg += f'\n\n"唔...从人家钱包里拿走了{win}金币的零花钱呢...坏蛋！"'
    
    await gamble_record_cmd.finish(msg, at_sender=True)


# ===== 幸运大转盘 =====

# 奖品概率配置
PRIZE_CONFIG = {
    '杂鱼': {'weight': 25, 'multiplier': 0.1, 'fish_add': 0.1, 'special_chance': 0.75, 'special_prizes': ["钱包金币-1%"]},
    '普通': {'weight': 50, 'multiplier': 1, 'fish_add': 1, 'special_chance': 0.0, 'special_prizes': []},
    '稀有': {'weight': 20, 'multiplier': 5, 'fish_add': 3, 'special_chance': 0.5, 'special_prizes': ["高级料理", "玩具球", "能量饮料", "普通扭蛋", "遗忘药水", "免费画图次数+1"]},
    '史诗': {'weight': 4, 'multiplier': 20, 'fish_add': 5, 'special_chance': 0.5, 'special_prizes': ["豪华料理", "高级扭蛋", "时之泪", "最初的契约", "技能药水", "免费画图次数+2"]},
    '传说': {'weight': 1, 'multiplier': 100, 'fish_add': 10, 'special_chance': 0.5, 'special_prizes': ["奶油蛋糕", "豪华蛋糕", "传说扭蛋", "誓约戒指", "钱包金币翻倍", "免费画图次数+3"]},
}

TIERS = list(PRIZE_CONFIG.keys())
WEIGHTS = [details['weight'] for details in PRIZE_CONFIG.values()]

# 基础奖品配置
PRIZES = {
    "gold": {"amount": 100, "chinese": "金币"},
    "starstone": {"amount": 100, "chinese": "星星"},
    "luckygold": {"amount": 0.05, "chinese": "幸运币"},
}

FREE_DRAW_SPECIAL_PRIZES = {
    "免费画图次数+1": 1,
    "免费画图次数+2": 2,
    "免费画图次数+3": 3,
}


def draw_prize() -> str:
    """根据权重随机抽取一个奖品档位"""
    return random.choices(TIERS, weights=WEIGHTS, k=1)[0]


async def _give_wallet_gold_double(uid: int, special_prize: str) -> str:
    wallet = money.of(uid)
    user_gold = wallet.gold
    wallet.gold += user_gold
    return special_prize


async def _give_wallet_gold_deduct(uid: int, special_prize: str) -> str:
    wallet = money.of(uid)
    user_gold = wallet.gold
    deduct = max(1, int(user_gold * 0.01))
    wallet.gold -= deduct
    return special_prize


async def _give_free_draw_prize(uid: int, special_prize: str) -> str:
    amount = FREE_DRAW_SPECIAL_PRIZES[special_prize]
    await add_free_draw_count(uid, amount)
    return f"免费画图次数 *{amount}"


async def _give_item_prize(uid: int, special_prize: str) -> str:
    await add_user_item(uid, special_prize)
    return special_prize


SPECIAL_PRIZE_HANDLERS = {
    "钱包金币翻倍": _give_wallet_gold_double,
    "钱包金币-1%": _give_wallet_gold_deduct,
    **{prize: _give_free_draw_prize for prize in FREE_DRAW_SPECIAL_PRIZES},
}


async def give_special_prize(uid: int, special_prize: str) -> str:
    handler = SPECIAL_PRIZE_HANDLERS.get(special_prize, _give_item_prize)
    return await handler(uid, special_prize)


async def give_prize(uid: int, prize_tier: str) -> str:
    """处理奖品发放逻辑"""
    prize_config = PRIZE_CONFIG[prize_tier]
    
    # 决定是发放特殊奖品还是普通奖品
    if random.random() < prize_config['special_chance'] and prize_config['special_prizes']:
        special_prize = random.choice(prize_config['special_prizes'])
        return await give_special_prize(uid, special_prize)
    else:
        # 发放普通资源奖品
        prize_name = random.choice(list(PRIZES.keys()))
        prize_info = PRIZES[prize_name]
        prize_amount = max(1, int(prize_info["amount"] * random.randint(5, 20) * prize_config['multiplier']))
        money.of(uid)[prize_name] += prize_amount
        return f"{prize_info['chinese']} *{prize_amount}"


async def fish_count_prize(uid: int, prize_tier: str) -> Optional[int]:
    """根据奖品档位计算额外钓鱼次数奖励"""
    prize_config = PRIZE_CONFIG[prize_tier]
    count = max(100, int(random.randint(5, 10) * prize_config['fish_add'] * 100))
    add_count = count * -1  # 负数表示增加钓鱼次数上限
    if FishingDB.check_and_update_fish_limit(uid, add_count):
        return count
    else:
        return None


turntable_cmd = on_command("幸运转盘", aliases={"幸运大转盘"}, priority=5, block=True)

@turntable_cmd.handle()
async def handle_turntable(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    """处理幸运大转盘游戏逻辑"""
    gold = money.gold
    if gold <= 0:
        await turntable_cmd.finish("欠债/失信用户，禁止游戏。", at_sender=True)
    
    # 检查每日次数
    
    unlimited_turntable = is_su_contributor(uid)
    can_spin = (await check_turntable_limit(uid))[0]
    if not can_spin and not unlimited_turntable:
        await turntable_cmd.finish(f"您今天的 {MAX_TURNS_PER_DAY} 次机会已经用完啦，明天再来吧！", at_sender=True)
    
    # 检查幸运币
    lucky_coins = money.luckygold
    if lucky_coins < 1:
        await turntable_cmd.finish("您的幸运币不足，无法启动转盘哦。", at_sender=True)
    
    money.luckygold -= 1
    if not unlimited_turntable:
        remaining_turns = await record_turntable_spin(uid)
    
    # 抽取奖品
    prize_tier = draw_prize()
    prize_description = await give_prize(uid, prize_tier)
    fish_count = await fish_count_prize(uid, prize_tier)
    
    result_message = f"指针停在了【{prize_tier}】区域！"
    result_message += f"\n您获得了：{prize_description}"
    result_message += f"\n额外奖励：钓鱼次数+{fish_count}"
    if unlimited_turntable:
        result_message += "\n剩余次数：无限制。"
    else:
        result_message += f"\n您今天还剩下 {remaining_turns} 次机会。"
    
    await turntable_cmd.finish(result_message, at_sender=True)


# ===== 领低保 =====
dibao_cmd = on_command("领低保", priority=5, block=True)

@dibao_cmd.handle()
async def handle_dibao(event: Event, bot: Bot, uid: int = Depends(get_uid)):
    """领取低保"""
    dibao_amount = getattr(config, 'dibao', 3000)
    if dibao_amount == 0:
        await dibao_cmd.finish("低保功能维护中，请稍候再试。", at_sender=True)
    
    # 检查今天是否已领
    if not await check_daily_prek(uid):
        await dibao_cmd.finish("你今天已经领过了，明天再来吧。", at_sender=True)
    
    # 检查是否在赌博中
    if uid in gambling_sessions and gambling_sessions[uid].get('active', False):
        await dibao_cmd.finish("赌徒不能领取低保哦~", at_sender=True)
    
    # 检查股票持仓
    user_portfolio = await get_user_portfolio(uid)
    if user_portfolio:
        stock_names = ", ".join(user_portfolio.keys())
        await dibao_cmd.finish(f"检测到你偷偷藏了股票({stock_names})，这么富还想骗低保？", at_sender=True)
    
    # 检查金币
    user_gold = money.gold
    if user_gold > 4999:
        await dibao_cmd.finish("这么富，还想骗低保？", at_sender=True)
    if user_gold < 0:
        await dibao_cmd.finish("欠债/失信用户，禁止操作。", at_sender=True)
    
    # 记录领取
    await record_daily_prek(uid)
    
    # 发放低保
    pet = await get_user_pet(uid)
    if pet and not pet["runaway"]:
        money.gold += dibao_amount + 3000
        await dibao_cmd.finish(f"已领取{dibao_amount+3000}金币（含宠物补贴）。\n你现在有{user_gold + dibao_amount+3000}金币", at_sender=True)
    else:
        money.gold += dibao_amount
        await dibao_cmd.finish(f"已领取{dibao_amount}金币。\n你现在有{user_gold + dibao_amount}金币", at_sender=True)


# ===== 转账功能 (uid/qq/at 三种模式) =====
TRANSFER_FEE_RATE = getattr(config, 'transfer_fee', 0.1)
MIN_REST = getattr(config, 'min_rest', 1000)

# 转账uid [目标uid] [金额]
transfer_uid_cmd = on_command("转账uid", priority=5, block=True)

@transfer_uid_cmd.handle()
async def handle_transfer_uid(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    """通过UID转账"""
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await transfer_uid_cmd.finish("格式：转账uid [目标uid] [金额]", at_sender=True)
    
    try:
        target_uid = int(parts[0])
        amount = int(parts[1])
    except ValueError:
        await transfer_uid_cmd.finish("UID和金额必须是整数！", at_sender=True)
    
    # 检查UID是否存在
    if not uid_manager.is_uid_exists(target_uid):
        await transfer_uid_cmd.finish(f"找不到 UID:{target_uid} 对应的账户", at_sender=True)
    
    await _do_transfer(transfer_uid_cmd, uid, target_uid, amount)


# 转账qq [目标QQ号] [金额]
transfer_qq_cmd = on_command("转账qq", priority=5, block=True)

@transfer_qq_cmd.handle()
async def handle_transfer_qq(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    """通过QQ号转账"""
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await transfer_qq_cmd.finish("格式：转账qq [目标QQ号] [金额]", at_sender=True)
    
    target_qq = parts[0]
    try:
        amount = int(parts[1])
    except ValueError:
        await transfer_qq_cmd.finish("金额必须是数字！", at_sender=True)
    
    # QQ号转UID（不自动创建）
    target_uid = uid_manager.get_uid_by_external_id("onebot", target_qq)
    if target_uid is None:
        await transfer_qq_cmd.finish(f"找不到QQ号 {target_qq} 对应的账户", at_sender=True)
    
    await _do_transfer(transfer_qq_cmd, uid, target_uid, amount)

# 转账 at [金额]
transfer_at_cmd = on_command("转账", priority=5, block=True)
@transfer_at_cmd.handle()
async def handle_transfer_at(uid: int = Depends(get_uid), args: Message = CommandArg()):
    """通过at转账"""
    target_uid = None
    try:
        if len(args) > 0:
            target_uid = get_at_uid(args[0])
    except (ValueError, IndexError, Exception):
        pass
    
    # 如果通过at找到了uid，通过at逻辑处理
    if target_uid is not None:
        try:
            # 尝试提取金额，兼容旧逻辑但也支持 extract_plain_text
            text = args.extract_plain_text().strip()
            if not text:
                 # 如果 extract_plain_text 为空（例如只发了 At），尝试检查 args[1]
                 if len(args) > 1 and args[1].type == "text":
                     amount = int(args[1].data["text"].strip())
                 else:
                     raise ValueError
            else:
                 amount = int(text)
        except (ValueError, IndexError):
            await transfer_at_cmd.finish("金额必须是数字！", at_sender=True)
        
        await _do_transfer(transfer_at_cmd, uid, target_uid, amount)
        return

    # Fallback: 尝试 QQ 号逻辑
    parts = args.extract_plain_text().strip().split()
    if len(parts) >= 2:
        target_qq = parts[0]
        try:
            amount = int(parts[1])
        except ValueError:
            await transfer_at_cmd.finish("金额必须是数字！", at_sender=True)
        
        target_uid = uid_manager.get_uid_by_external_id("onebot", target_qq)
        if target_uid is None:
            await transfer_at_cmd.finish(f"找不到at对应的账户，且找不到QQ号 {target_qq} 对应的账户", at_sender=True)
            
        await _do_transfer(transfer_at_cmd, uid, target_uid, amount)
    else:
        await transfer_at_cmd.finish("格式：转账 [at/QQ] [金额]", at_sender=True)

async def _do_transfer(cmd, sender_uid: int, target_uid: int, amount: int):
    """执行转账逻辑"""
    blackusers = getattr(config, 'BLACKUSERS', [])
    if sender_uid in blackusers:
        await cmd.finish('\n操作失败，账户被冻结，请联系管理员寻求帮助。', at_sender=True)
    
    # SU 转账金额限制
    allowed, reason = check_su_permission(sender_uid, 'transfer', amount=amount)
    if not allowed:
        await cmd.finish(f'\n{reason}', at_sender=True)
    
    if sender_uid == target_uid:
        await cmd.finish('\n无法给自己转账', at_sender=True)
    
    if sender_uid in gambling_sessions and gambling_sessions[sender_uid].get('active', False):
        await cmd.finish("你正处于幸运游戏过程中，不能转账哦~", at_sender=True)
    
    if target_uid in gambling_sessions and gambling_sessions[target_uid].get('active', False):
        await cmd.finish("对方正处于幸运游戏过程中，不能转账哦~", at_sender=True)
    
    if amount < 20:
        await cmd.finish('错误金额，最低转账20金币', at_sender=True)
    
    # 计算手续费
    fee = int(amount * TRANSFER_FEE_RATE)
    total_amount = amount + fee
    
    # 检查余额
    sender_wallet = money.of(sender_uid)
    target_wallet = money.of(target_uid)
    gold = sender_wallet.gold
    if gold < total_amount:
        await cmd.finish(f'\n余额不足，本次转账需要 {total_amount} 金币，包含 {fee} 金币手续费。\n你当前只有 {gold} 金币', at_sender=True)
    
    restgold = gold - total_amount
    if restgold < MIN_REST:
        await cmd.finish(f'\n禁止转账，如果转账，则你将仅剩{restgold}金币。\n请确保转账后剩余金币大于{MIN_REST}。', at_sender=True)
    
    # 执行转账
    sender_wallet.gold -= total_amount
    target_wallet.gold += amount
    
    record_su_usage(sender_uid, 'transfer', amount)
    await cmd.finish(f'\n转账成功，已向 UID:{target_uid} 转账 {amount} 金币，手续费 {fee} 金币\n你当前还剩 {restgold} 金币', at_sender=True)


# ===== 管理员打款功能 (uid/qq 两种模式) =====

# 打款uid [目标uid] [金额]
admin_add_uid_cmd = on_command("打款uid", priority=5, block=True)

@admin_add_uid_cmd.handle()
async def handle_admin_add_uid(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    """管理员通过UID打款"""
    
    if not is_su(uid):
        await admin_add_uid_cmd.finish('权限不足', at_sender=True)
    
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await admin_add_uid_cmd.finish("格式：打款uid [目标uid] [金额]", at_sender=True)
    
    try:
        target_uid = int(parts[0])
        amount = int(parts[1])
    except ValueError:
        await admin_add_uid_cmd.finish("UID和金额必须是数字！", at_sender=True)
    
    if not uid_manager.is_uid_exists(target_uid):
        await admin_add_uid_cmd.finish(f"找不到 UID:{target_uid} 对应的账户", at_sender=True)
    
    # SU 打款权限检查
    allowed, reason = check_su_permission(uid, 'payment', target_uid=target_uid, amount=amount)
    if not allowed:
        await admin_add_uid_cmd.finish(f'\n{reason}', at_sender=True)
    
    money.of(target_uid).gold += amount
    await admin_add_uid_cmd.finish(f'已向 UID:{target_uid} 打款 {amount} 金币', at_sender=True)


# 打款qq [目标QQ号] [金额]
admin_add_qq_cmd = on_command("打款qq", priority=5, block=True)

@admin_add_qq_cmd.handle()
async def handle_admin_add_qq(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    """管理员通过QQ号打款"""
    
    if not is_su(uid):
        await admin_add_qq_cmd.finish('权限不足', at_sender=True)
    
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await admin_add_qq_cmd.finish("格式：打款qq [目标QQ号] [金额]", at_sender=True)
    
    target_qq = parts[0]
    try:
        amount = int(parts[1])
    except ValueError:
        await admin_add_qq_cmd.finish("金额必须是数字！", at_sender=True)
    
    # QQ号转UID
    target_uid = uid_manager.get_uid_by_external_id("onebot", target_qq)
    if target_uid is None:
        await admin_add_qq_cmd.finish(f"找不到QQ号 {target_qq} 对应的账户", at_sender=True)
    
    # SU 打款权限检查
    allowed, reason = check_su_permission(uid, 'payment', target_uid=target_uid, amount=amount)
    if not allowed:
        await admin_add_qq_cmd.finish(f'\n{reason}', at_sender=True)
    
    money.of(target_uid).gold += amount
    await admin_add_qq_cmd.finish(f'已向 QQ:{target_qq} (UID:{target_uid}) 打款 {amount} 金币', at_sender=True)


# ===== 管理员扣款功能 (uid/qq 两种模式) =====

# 扣款uid [目标uid] [金额]
admin_reduce_uid_cmd = on_command("扣款uid", priority=5, block=True)

@admin_reduce_uid_cmd.handle()
async def handle_admin_reduce_uid(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    """管理员通过UID扣款"""
    
    if not is_su(uid):
        await admin_reduce_uid_cmd.finish('权限不足', at_sender=True)
    
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await admin_reduce_uid_cmd.finish("格式：扣款uid [目标uid] [金额]", at_sender=True)
    
    try:
        target_uid = int(parts[0])
        amount = int(parts[1])
    except ValueError:
        await admin_reduce_uid_cmd.finish("UID和金额必须是数字！", at_sender=True)

    if amount <= 0:
        await admin_reduce_uid_cmd.finish("金额必须是正整数！", at_sender=True)
    
    if not uid_manager.is_uid_exists(target_uid):
        await admin_reduce_uid_cmd.finish(f"找不到 UID:{target_uid} 对应的账户", at_sender=True)

    if not is_su_contributor(uid) and target_uid != uid:
        await admin_reduce_uid_cmd.finish('权限不足', at_sender=True)
    
    target_wallet = money.of(target_uid)
    target_gold = target_wallet.gold
    
    deduct_amount = min(amount, max(target_gold, 0))
    target_wallet.gold -= deduct_amount
    await admin_reduce_uid_cmd.finish(f'已从 UID:{target_uid} 扣款 {deduct_amount} 金币', at_sender=True)


# 扣款qq [目标QQ号] [金额]
admin_reduce_qq_cmd = on_command("扣款qq", priority=5, block=True)

@admin_reduce_qq_cmd.handle()
async def handle_admin_reduce_qq(event: Event, bot: Bot, uid: int = Depends(get_uid), args: Message = CommandArg()):
    """管理员通过QQ号扣款"""
    
    if not is_su(uid):
        await admin_reduce_qq_cmd.finish('权限不足', at_sender=True)
    
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await admin_reduce_qq_cmd.finish("格式：扣款qq [目标QQ号] [金额]", at_sender=True)
    
    target_qq = parts[0]
    try:
        amount = int(parts[1])
    except ValueError:
        await admin_reduce_qq_cmd.finish("金额必须是数字！", at_sender=True)

    if amount <= 0:
        await admin_reduce_qq_cmd.finish("金额必须是正整数！", at_sender=True)
    
    # QQ号转UID
    target_uid = uid_manager.get_uid_by_external_id("onebot", target_qq)
    if target_uid is None:
        await admin_reduce_qq_cmd.finish(f"找不到QQ号 {target_qq} 对应的账户", at_sender=True)

    if not is_su_contributor(uid) and target_uid != uid:
        await admin_reduce_qq_cmd.finish('权限不足', at_sender=True)
    
    target_wallet = money.of(target_uid)
    target_gold = target_wallet.gold
    
    deduct_amount = min(amount, max(target_gold, 0))
    target_wallet.gold -= deduct_amount
    await admin_reduce_qq_cmd.finish(f'已从 QQ:{target_qq} (UID:{target_uid}) 扣款 {deduct_amount} 金币', at_sender=True)
