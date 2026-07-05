"""
股票工具模块

处理股票数据、持仓、价格、图表生成等数据库操作
"""

import json
import os
import sqlite3
import asyncio
import random
import time
import io
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

# Matplotlib 用于替代 Plotly/Kaleido
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from nonebot import logger


# 历史数据保留时长
HISTORY_DURATION_HOURS = 24

# 股票定义 (名称: 初始价格)
STOCKS = {
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
}

# 市场事件定义
MARKET_EVENTS = {
    "利好": {
        "templates": [
            "{stock}获得新的市场投资！",
            "{stock}获得异次元政府补贴！",
            "{stock}季度财报超预期！"
        ],
        "scope": "single",
        "effect": lambda price: price * random.uniform(1.10, 1.20)
    },
    "利空": {
        "templates": [
            "{stock}产品力下降！",
            "{stock}产品发现严重缺陷！",
            "{stock}高管突然离职！"
        ],
        "scope": "single",
        "effect": lambda price: price * random.uniform(0.82, 0.90)
    },
    "大盘上涨": {
        "templates": [
            "鹰酱宣布降息，市场普涨！",
            "异次元经济复苏，投资者信心增强！",
            "魔法少女在战争中大捷，领涨大盘！"
        ],
        "scope": "all",
        "effect": lambda price: price * random.uniform(1.10, 1.15)
    },
    "大盘下跌": {
        "templates": [
            "异次元国际局势紧张，市场恐慌！",
            "经济数据不及预期，市场普跌！",
            "机构投资者大规模抛售！"
        ],
        "scope": "all",
        "effect": lambda price: price * random.uniform(0.87, 0.90)
    },
    "暴涨": {
        "templates": [
            "{stock}成为市场新宠，资金疯狂涌入！",
            "{stock}发现新资源，价值重估！"
        ],
        "scope": "single",
        "effect": lambda price: price * random.uniform(1.25, 1.40)
    },
    "暴跌": {
        "templates": [
            "{stock}被曝财务造假！",
            "{stock}主要产品被禁售！"
        ],
        "scope": "single",
        "effect": lambda price: price * random.uniform(0.63, 0.75)
    }
}

MANUAL_EVENT_TYPES = {
    "利好": "单股上涨",
    "利空": "单股下跌",
    "暴涨": "单股暴涨",
    "暴跌": "单股暴跌",
    "大盘上涨": "全局上涨",
    "大盘下跌": "全局下跌"
}

# 数据库路径
_db_path: Optional[str] = None


def set_db_path(path: str):
    """设置数据库路径"""
    global _db_path
    _db_path = path


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    if _db_path is None:
        raise RuntimeError("数据库路径未设置")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    # 启用外键约束
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def get_stock_data() -> Dict[str, dict]:
    """获取所有股票数据"""
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT stock_name, initial_price, history_data, events_data FROM stock_data')
        results = cursor.fetchall()
        conn.close()
        
        stock_data = {}
        for row in results:
            stock_data[row['stock_name']] = {
                "initial_price": row['initial_price'],
                "history": json.loads(row['history_data']),
                "events": json.loads(row['events_data'])
            }
        return stock_data
    
    loop = asyncio.get_event_loop()
    stock_data = await loop.run_in_executor(None, _query)
    
    # 检查缺失的股票
    missing = set(STOCKS.keys()) - set(stock_data.keys())
    if missing:
        for stock_name in missing:
            stock_data[stock_name] = {
                "initial_price": STOCKS[stock_name],
                "history": [],
                "events": []
            }
        await save_stock_data(stock_data)
    
    return stock_data


async def save_stock_data(data: Dict[str, dict]):
    """保存所有股票数据"""
    def _save():
        conn = _get_connection()
        cursor = conn.cursor()
        
        for stock_name, stock_info in data.items():
            initial_price = stock_info.get('initial_price', STOCKS.get(stock_name, 50.0))
            history_data = json.dumps(stock_info.get('history', []))
            events_data = json.dumps(stock_info.get('events', []))
            
            cursor.execute('''
                INSERT OR REPLACE INTO stock_data (stock_name, initial_price, history_data, events_data)
                VALUES (?, ?, ?, ?)
            ''', (stock_name, initial_price, history_data, events_data))
        
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save)


async def get_user_portfolios() -> Dict[int, dict]:
    """获取所有用户持仓"""
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT uid, portfolio_data FROM user_portfolios')
        results = cursor.fetchall()
        conn.close()
        
        portfolios = {}
        for row in results:
            portfolios[row['uid']] = json.loads(row['portfolio_data'])
        return portfolios
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def save_user_portfolios(data: Dict[int, dict]):
    """保存所有用户持仓"""
    def _save():
        conn = _get_connection()
        cursor = conn.cursor()
        
        for uid, portfolio in data.items():
            cursor.execute('''
                INSERT OR REPLACE INTO user_portfolios (uid, portfolio_data)
                VALUES (?, ?)
            ''', (uid, json.dumps(portfolio)))
        
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _save)


async def get_user_portfolio(user_id: int) -> dict:
    """获取单个用户的持仓"""
    from nonebot import logger
    portfolios = await get_user_portfolios()
    result = portfolios.get(user_id, {})
    return result


async def update_user_portfolio(user_id: int, stock_name: str, change_amount: int) -> bool:
    """更新用户持仓 (正数买入，负数卖出)"""
    from nonebot import logger
    
    logger.info(f"[chaogu] update_user_portfolio called: user_id={user_id}, stock={stock_name}, amount={change_amount}")
    
    # 捕获外部变量到本地，确保闭包正确
    _user_id = user_id
    _stock_name = stock_name
    _change_amount = change_amount
    
    def _update():
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            
            logger.info(f"[chaogu] Querying portfolio for uid={_user_id}")
            cursor.execute('SELECT portfolio_data FROM user_portfolios WHERE uid = ?', (_user_id,))
            result = cursor.fetchone()
            
            if result:
                portfolio = json.loads(result['portfolio_data'])
                logger.info(f"[chaogu] Found existing portfolio: {portfolio}")
            else:
                portfolio = {}
                logger.info(f"[chaogu] No existing portfolio, creating new one")
            
            current = portfolio.get(_stock_name, 0)
            new_amount = current + _change_amount
            
            logger.info(f"[chaogu] Current holding: {current}, new amount: {new_amount}")
            
            if new_amount < 0:
                conn.close()
                logger.warning(f"[chaogu] new_amount < 0, returning False")
                return False
            
            if new_amount == 0:
                if _stock_name in portfolio:
                    del portfolio[_stock_name]
            else:
                portfolio[_stock_name] = new_amount
            
            logger.info(f"[chaogu] Updated portfolio: {portfolio}")
            
            cursor.execute('''
                INSERT OR REPLACE INTO user_portfolios (uid, portfolio_data)
                VALUES (?, ?)
            ''', (_user_id, json.dumps(portfolio)))
            
            conn.commit()
            logger.info(f"[chaogu] Portfolio saved successfully for uid={_user_id}")
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[chaogu] Error in _update: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _update)
    logger.info(f"[chaogu] update_user_portfolio returning: {result}")
    return result


async def get_current_stock_price(stock_name: str, stock_data: dict = None) -> Optional[float]:
    """获取指定股票的当前价格"""
    if stock_data is None:
        stock_data = await get_stock_data()
    
    if stock_name not in stock_data or not stock_data[stock_name]["history"]:
        return stock_data.get(stock_name, {}).get("initial_price")
    
    return stock_data[stock_name]["history"][-1][1]


async def get_stock_price_history(stock_name: str, stock_data: dict = None) -> List[tuple]:
    """获取指定股票过去24小时的价格历史"""
    if stock_data is None:
        stock_data = await get_stock_data()
    
    if stock_name not in stock_data:
        return []
    
    cutoff_time = time.time() - HISTORY_DURATION_HOURS * 3600
    history = stock_data[stock_name].get("history", [])
    
    return [(ts, price) for ts, price in history if ts >= cutoff_time]


def _resolve_cjk_font() -> fm.FontProperties:
    """
    查找可用的中文字体
    """
    _known_paths = [
        # Alpine font-wqy-zenhei
        '/usr/share/fonts/ttf/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc',
        # Debian/Ubuntu fonts-wqy-zenhei
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/opentype/wqy/wqy-zenhei.ttc',
        # Windows
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/msyhbd.ttc',
        # Mac
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/System/Library/Fonts/PingFang.ttc',
        '/Library/Fonts/Arial Unicode.ttf',
    ]

    for path in _known_paths:
        if os.path.isfile(path):
            fm.fontManager.addfont(path)
            prop = fm.FontProperties(fname=path)
            return prop

    # 尝试从 Matplotlib 已注册字体中按名称查找
    _name_fallbacks = [
        'WenQuanYi Zen Hei', 'WenQuanYi Micro Hei',
        'SimHei', 'Microsoft YaHei', 'Arial Unicode MS',
    ]
    for name in _name_fallbacks:
        try:
            found = fm.findfont(name, fallback_to_default=False)
            if found and found != fm.findfont('sans-serif', fallback_to_default=False):
                logger.info(f"[stock_utils] Using registered CJK font: {name}")
                prop = fm.FontProperties(family=name)
                return prop
        except Exception:
            continue

    logger.warning("[stock_utils] No CJK font found, chart text may show as tofu")
    return fm.FontProperties()


def generate_stock_chart(stock_name: str, history: List[tuple], stock_data: Dict = None) -> Optional[io.BytesIO]:
    """
    使用 Matplotlib 生成股票历史价格图表的 PNG 图片。
    改用 Matplotlib 以避免 Kaleido 在 Windows 上的卡死问题。

    Args:
        stock_name: 股票名称
        history: 价格历史列表 [(timestamp, price), ...]
        stock_data: 股票数据字典，用于获取事件信息

    Returns:
        包含PNG图片的BytesIO对象，失败返回None
    """
    if not history:
        return None

    try:

        timestamps, prices = zip(*history)
        dates = [datetime.fromtimestamp(ts) for ts in timestamps]

        current_price = history[-1][1]
        initial_price = STOCKS.get(stock_name, 0)

        # 计算时间范围
        now = datetime.now()
        start_time = now - timedelta(hours=HISTORY_DURATION_HOURS)
        end_time = now + timedelta(hours=3)

        # 在创建 Figure 之前查找并注册中文字体
        font_prop = _resolve_cjk_font()
        plt.rcParams['axes.unicode_minus'] = False

        # 创建 Figure 和 Axes 对象 (避免使用 plt.xxx 的全局状态，线程安全)
        fig = Figure(figsize=(10, 6), dpi=100)
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        # 绘制价格折线
        ax.plot(dates, prices, marker='o', markersize=4, linestyle='-', label='价格')

        # 标记事件
        if stock_data and stock_name in stock_data and "events" in stock_data[stock_name]:
            for event in stock_data[stock_name]["events"]:
                event_time = datetime.fromtimestamp(event["time"])
                if event_time >= start_time:
                    # 垂直虚线
                    ax.axvline(x=event_time, color='orange', linestyle='--', alpha=0.7, linewidth=1)
                    # 标注文本
                    ax.annotate(
                        event["type"],
                        xy=(event_time, event["old_price"]),
                        xytext=(0, -30),
                        textcoords='offset points',
                        arrowprops=dict(facecolor='black', arrowstyle='->'),
                        ha='center',
                        fontsize=9,
                        fontproperties=font_prop
                    )

        # 设置标题和标签
        ax.set_title(
            f'{stock_name} 过去{HISTORY_DURATION_HOURS}小时价格走势\n(初始价格: {initial_price:.2f}金币 最高上涨至初始价格的2倍)',
            fontsize=12,
            fontproperties=font_prop
        )
        ax.set_xlabel('时间', fontproperties=font_prop)
        ax.set_ylabel('价格 (金币)', fontproperties=font_prop)

        # 设置X轴时间格式
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.set_xlim(start_time, end_time)
        
        # 网格
        ax.grid(True, linestyle=':', alpha=0.6)

        # 标注当前价格
        ax.annotate(
            f'当前: {current_price:.2f}',
            xy=(dates[-1], current_price),
            xytext=(30, -30),
            textcoords='offset points',
            arrowprops=dict(facecolor='black', arrowstyle='->'),
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="black", alpha=0.8),
            fontproperties=font_prop
        )

        # 渲染到内存
        buf = io.BytesIO()
        canvas.print_png(buf)
        buf.seek(0)
        
        return buf

    except Exception as e:
        logger.error(f"Error generating Matplotlib chart for {stock_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


# ===== 豪赌记录相关函数 =====

async def update_gamble_record(uid: int, change_amount: int) -> bool:
    """更新豪赌记录 (正数增加increase_record，负数增加reduce_record)"""
    
    
    def _update():
        conn = _get_connection()
        cursor = conn.cursor()
        
        if change_amount >= 0:
            # 正数：增加increase_record
            cursor.execute('''
                INSERT INTO gamble_record (uid, increase_record, reduce_record) 
                VALUES (?, ?, 0)
                ON CONFLICT(uid) DO UPDATE SET 
                increase_record = increase_record + excluded.increase_record
            ''', (uid, change_amount))
        else:
            # 负数：增加reduce_record（取绝对值）
            cursor.execute('''
                INSERT INTO gamble_record (uid, increase_record, reduce_record) 
                VALUES (?, 0, ?)
                ON CONFLICT(uid) DO UPDATE SET 
                reduce_record = reduce_record + excluded.reduce_record
            ''', (uid, abs(change_amount)))
        
        conn.commit()
        conn.close()
        return True
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _update)


async def get_all_gamble_record() -> Dict[int, dict]:
    """获取所有用户的豪赌记录"""
    
    
    def _query():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT uid, increase_record, reduce_record FROM gamble_record')
        results = cursor.fetchall()
        conn.close()
        
        return {
            row['uid']: {
                'increase_record': row['increase_record'],
                'reduce_record': row['reduce_record']
            } for row in results
        }
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


async def get_user_gamble_record(uid: int) -> dict:
    """获取单个用户的豪赌记录"""
    all_records = await get_all_gamble_record()
    return all_records.get(uid, {'increase_record': 0, 'reduce_record': 0})


async def check_daily_gamble_limit(uid: int) -> bool:
    """检查用户今天是否还可以赌博（True=可以，False=今天已赌过）"""
    from datetime import date
    
    
    def _check():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT last_gamble_date FROM daily_gamble_limits WHERE uid = ?', (uid,))
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return True  # 从未赌过
        
        today_str = date.today().isoformat()
        return result['last_gamble_date'] != today_str
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check)


async def record_gamble_today(uid: int):
    """记录用户今天进行了赌博"""
    from datetime import date
    
    
    def _record():
        conn = _get_connection()
        cursor = conn.cursor()
        
        today_str = date.today().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO daily_gamble_limits (uid, last_gamble_date)
            VALUES (?, ?)
        ''', (uid, today_str))
        
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _record)


# ===== 转盘次数限制相关函数 =====

MAX_TURNS_PER_DAY = 5

async def check_turntable_limit(uid: int) -> tuple[bool, int]:
    """检查用户今天是否还可以转盘（True=可以，False=次数用完）
    返回: (can_spin, remaining_turns)
    """
    from datetime import date
    
    
    def _check():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT last_date, turn_count FROM daily_turntable_limits WHERE uid = ?', (uid,))
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return (True, MAX_TURNS_PER_DAY)
        
        today_str = date.today().isoformat()
        if result['last_date'] != today_str:
            return (True, MAX_TURNS_PER_DAY)
        
        turns_today = result['turn_count']
        remaining = MAX_TURNS_PER_DAY - turns_today
        return (remaining > 0, remaining)
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check)


async def record_turntable_spin(uid: int) -> int:
    """记录用户今天转了一次转盘，返回剩余次数"""
    from datetime import date
    
    
    def _record():
        conn = _get_connection()
        cursor = conn.cursor()
        
        today_str = date.today().isoformat()
        
        # 获取当前次数
        cursor.execute('SELECT last_date, turn_count FROM daily_turntable_limits WHERE uid = ?', (uid,))
        result = cursor.fetchone()
        
        if result is None or result['last_date'] != today_str:
            new_count = 1
        else:
            new_count = result['turn_count'] + 1
        
        cursor.execute('''
            INSERT OR REPLACE INTO daily_turntable_limits (uid, last_date, turn_count)
            VALUES (?, ?, ?)
        ''', (uid, today_str, new_count))
        
        conn.commit()
        conn.close()
        return MAX_TURNS_PER_DAY - new_count
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _record)


# ===== 低保领取记录相关函数 =====

async def check_daily_prek(uid: int) -> bool:
    """检查用户今天是否已领低保（True=可以领，False=已领过）"""
    from datetime import date
    
    
    def _check():
        conn = _get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT last_prek_date FROM daily_prek WHERE uid = ?', (uid,))
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return True
        
        today_str = date.today().isoformat()
        return result['last_prek_date'] != today_str
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check)


async def record_daily_prek(uid: int):
    """记录用户今天领了低保"""
    from datetime import date
    
    
    def _record():
        conn = _get_connection()
        cursor = conn.cursor()
        
        today_str = date.today().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO daily_prek (uid, last_prek_date)
            VALUES (?, ?)
        ''', (uid, today_str))
        
        conn.commit()
        conn.close()
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _record)
