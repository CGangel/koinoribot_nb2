# ==========================================
# 第一部分：导入需要的各种库
# ==========================================
import secrets            # 标准库，用于生成安全随机数，进行抽奖
import asyncio            # 标准库，用于实现异步锁，防止并发写数据库冲突
import datetime           # 标准库，用于获取当前时间、日期、星期几
import sqlite3            # 标准库，用于连接 koinoribot.db 数据库

from nonebot import on_command                 # NoneBot2 核心 API，用于注册指令
from nonebot.plugin import PluginMetadata       # NoneBot2 插件元数据
from nonebot.adapters import Event, Bot         # 接收事件和 Bot 实例
from nonebot.adapters import Message            # 适配器无关的消息类型，用于接收用户参数
from nonebot.params import CommandArg, Depends  # 用于依赖注入参数和 UID
from ...tools import get_uid, send_group_forward_msg, build_forward_chain  # 项目内部工具
from ...su_manager import is_su_contributor     # 项目内部工具：判断是否为 0 级 SU
from ...money import get_database_path          # 项目内部工具：获取统一数据库路径

# ==========================================
# 第二部分：注册这个插件在机器人中
# ==========================================
__plugin_meta__ = PluginMetadata(
    name="eat_what",
    description="随机决定今天吃什么，支持个人专属菜单与节日提示",
    usage="发送 吃什么帮助 查看指令；发送 今天吃什么 / 吃什么 抽选；发送 我以后想吃 <食物> 添加；发送 我以后不想吃 <食物> 删除；发送 查看菜单 查看食物",
)

# ==========================================
# 第三部分：数据库初始化与全局常量
# ==========================================
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))  # 强制使用北京时间 UTC+8
MAX_USER_FOODS = 20                                          # 普通用户专属菜单上限
_io_lock = asyncio.Lock()                                    # 全局异步锁：保证并发操作数据库时不冲突

# 公历节日字典（每年日期固定，格式：月份, 日期）
SOLAR_FESTIVALS = {
    (2, 14): ("情人节", "巧克力"),
    (10, 31): ("万圣节", "南瓜派"),
}

# 农历节日字典（按年份记录公历日期，手动维护，避免复杂的农历换算）
LUNAR_FESTIVALS_BY_YEAR = {
    2026: {(2, 17): ("春节", "饺子、年糕"), (3, 3): ("元宵节", "汤圆"), (6, 19): ("端午节", "粽子、咸鸭蛋"), (8, 19): ("七夕节", "巧果"), (9, 25): ("中秋节", "月饼、大闸蟹"), (10, 18): ("重阳节", "重阳糕")},
    2027: {(2, 6): ("春节", "饺子、年糕"), (2, 20): ("元宵节", "汤圆"), (6, 9): ("端午节", "粽子、咸鸭蛋"), (8, 8): ("七夕节", "巧果"), (9, 15): ("中秋节", "月饼、大闸蟹"), (10, 8): ("重阳节", "重阳糕")},
    2028: {(1, 26): ("春节", "饺子、年糕"), (2, 9): ("元宵节", "汤圆"), (5, 28): ("端午节", "粽子、咸鸭蛋"), (7, 27): ("七夕节", "巧果"), (10, 3): ("中秋节", "月饼、大闸蟹"), (10, 26): ("重阳节", "重阳糕")},
    2029: {(2, 13): ("春节", "饺子、年糕"), (2, 27): ("元宵节", "汤圆"), (6, 16): ("端午节", "粽子、咸鸭蛋"), (8, 15): ("七夕节", "巧果"), (9, 22): ("中秋节", "月饼、大闸蟹"), (10, 15): ("重阳节", "重阳糕")},
    2030: {(2, 3): ("春节", "饺子、年糕"), (2, 17): ("元宵节", "汤圆"), (6, 5): ("端午节", "粽子、咸鸭蛋"), (8, 4): ("七夕节", "巧果"), (9, 12): ("中秋节", "月饼、大闸蟹"), (10, 5): ("重阳节", "重阳糕")},
}

DEFAULT_BASE_FOODS = [
    "云吞", "烧烤", "麻辣烫", "兰州拉面", "黄焖鸡米饭",
    "沙县小吃", "炒饭", "盖浇饭", "米线", "饺子",
    "汉堡", "炸鸡", "意面", "芝士焗饭", "食堂", "塔斯汀"
]

# 全局变量：标记数据库是否已初始化，实现懒加载
_db_inited = False


def _get_conn():
    """获取 koinoribot.db 的数据库连接（调用方需负责关闭）"""
    global _db_inited
    # 懒加载：只在第一次实际使用数据库时才进行初始化
    # 这能确保此时主程序已经设置了正确的数据库路径
    if not _db_inited:
        _init_db()
        _db_inited = True

    conn = sqlite3.connect(get_database_path())
    conn.row_factory = sqlite3.Row  # 设置行工厂，使查询结果支持按列名取值
    return conn


def _init_db():
    """初始化数据表，如果基础表为空则插入默认食物"""
    conn = sqlite3.connect(get_database_path())
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        # 创建用户专属食物表：uid 和 food_name 组合作为主键，保证同一个用户不会重复添加同一种食物
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eat_what_user (
                uid INTEGER,
                food_name TEXT,
                PRIMARY KEY (uid, food_name)
            )
        """)
        # 创建公共基础食物表：food_name 作为主键
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eat_what_base (
                food_name TEXT PRIMARY KEY
            )
        """)
        # 检查基础表是否为空，如果为空则插入默认食物
        cursor.execute("SELECT COUNT(*) as count FROM eat_what_base")
        if cursor.fetchone()["count"] == 0:
            cursor.executemany(
                "INSERT OR IGNORE INTO eat_what_base (food_name) VALUES (?)",
                [(food,) for food in DEFAULT_BASE_FOODS]
            )
        conn.commit()
    finally:
        conn.close()


# 内存缓存：记录用户上次抽奖和疯狂星期四触发日期（重启后会重置，无需存数据库）
_last_draw_cache = {}
_last_thursday_cache = {}


# ==========================================
# 第四部分：核心业务逻辑（触发命令）
# ==========================================

# ---------- 4.1 读出：今天吃什么 ----------
eat_cmd = on_command("今天吃什么", aliases={"吃什么"}, priority=5, block=True)

@eat_cmd.handle()
async def handle_eat(uid: int = Depends(get_uid)):
    """处理‘今天吃什么’指令，随机抽奖与防重复、节日提示。"""
    result = ""
    reply_text = ""
    trigger_thursday = False
    today = None
    weekday = None

    # 加锁：保证读取数据库、合并抽奖池、记录抽奖结果的原子性
    async with _io_lock:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            # 获取公共食物
            cursor.execute("SELECT food_name FROM eat_what_base")
            base_foods = [row["food_name"] for row in cursor.fetchall()]
            # 获取用户专属食物
            cursor.execute("SELECT food_name FROM eat_what_user WHERE uid = ?", (uid,))
            user_foods = [row["food_name"] for row in cursor.fetchall()]

            now = datetime.datetime.now(BEIJING_TZ)
            today = now.date()
            weekday = now.weekday()

            # 特殊机制：疯狂星期四（每个用户每周四只能触发一次）
            last_thursday = _last_thursday_cache.get(str(uid))
            if weekday == 3 and str(today) != last_thursday:
                _last_thursday_cache[str(uid)] = str(today)
                trigger_thursday = True
            else:
                # 合并公共菜单和用户专属菜单，组成本次抽奖池
                draw_pool = list(base_foods) + list(user_foods)
                last_draw = _last_draw_cache.get(str(uid))

                # 防重复逻辑：如果本次抽奖池中包含了上次抽到的食物，且池子大于1个，临时将其剔除
                if last_draw and last_draw in draw_pool and len(draw_pool) > 1:
                    draw_pool.remove(last_draw)

                # 兜底防护：如果剔完之后池子空了，就恢复全量池子，防止程序崩溃
                if not draw_pool:
                    draw_pool = list(base_foods) + list(user_foods)

                # 随机抽取一个结果
                result = secrets.choice(draw_pool)
                # 记录本次抽到的结果，供下一次防重复使用
                _last_draw_cache[str(uid)] = result
        finally:
            conn.close()

    # 注意：finish() 会抛出 FinishedException，必须放在锁外面执行，否则会导致死锁
    if trigger_thursday:
        await eat_cmd.finish("🍗 今天是星期四！V我50，带你吃疯狂星期四！")
        return

    reply_text = f"🎲 天意说：今天吃 {result} 吧！"

    # 公历节日检查
    solar_fest = SOLAR_FESTIVALS.get((today.month, today.day))
    if solar_fest:
        reply_text += f"\n\n🎉 今天是{solar_fest[0]}，你还可以去吃{solar_fest[1]}哦！"

    # 农历节日检查（根据当前年份加载对应的节日数据）
    lunar_year_data = LUNAR_FESTIVALS_BY_YEAR.get(today.year, {})
    lunar_fest = lunar_year_data.get((today.month, today.day))
    if lunar_fest:
        reply_text += f"\n\n🎊 今天是{lunar_fest[0]}，你还可以去吃{lunar_fest[1]}哦！"

    # 周末额外提示
    if weekday == 5 or weekday == 6:
        reply_text += "\n\n🌴 今天是周末，可以吃点好的犒劳一下自己哦！"

    await eat_cmd.finish(reply_text)


# ---------- 4.2 写入：添加食物 ----------
add_cmd = on_command("我以后想吃", aliases={"添加食物"}, priority=4, block=True)


def _parse_foods(text: str) -> list[str]:
    """辅助函数：将用户输入的各种标点符号统一替换为空格，再切分成列表。"""
    for sep in ("，", ",", "、", "；", ";", "|", " "):
        text = text.replace(sep, " ")
    return [item.strip() for item in text.split() if item.strip()]


@add_cmd.handle()
async def handle_add(args: Message = CommandArg(), uid: int = Depends(get_uid)):
    """处理‘我以后想吃’指令，添加食物到个人专属抽奖池。"""
    raw_text = args.extract_plain_text().strip()

    if not raw_text:
        await add_cmd.finish("🍽 请告诉我你想吃什么，例如：我以后想吃 烤冷面")

    new_foods = _parse_foods(raw_text)

    if len(new_foods) > 1:
        await add_cmd.finish("⚠️ 一次只能添加一个食物哦，请分开添加~")

    food_name = new_foods[0]

    if len(food_name) > 5:
        await add_cmd.finish("📏 食物名称太长，暂不支持输入（最多5个字）")

    reply_text = ""
    # 加锁：保证数据库读写安全，防止并发冲突
    async with _io_lock:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            # 检查是否在公共基础表中
            cursor.execute("SELECT 1 FROM eat_what_base WHERE food_name = ?", (food_name,))
            if cursor.fetchone():
                reply_text = "🚫 此为基础食物，不可更改哦~"
            else:
                # 检查是否已在用户专属表中
                cursor.execute("SELECT 1 FROM eat_what_user WHERE uid = ? AND food_name = ?", (uid, food_name))
                if cursor.fetchone():
                    reply_text = f"🍔 你的菜单里已经有【{food_name}】啦！"
                else:
                    # 查询当前用户已有的食物数量
                    cursor.execute("SELECT COUNT(*) as count FROM eat_what_user WHERE uid = ?", (uid,))
                    current_count = cursor.fetchone()["count"]

                    # 权限判断核心逻辑：普通用户达到 20 个上限后就无法再添加，管理员不受限制
                    if not is_su_contributor(uid) and current_count >= MAX_USER_FOODS:
                        reply_text = "⚠️ 你的专属菜单已达普通用户上限（20个），如需继续添加请联系管理员哦~"
                    else:
                        # 写入数据并提交
                        cursor.execute("INSERT INTO eat_what_user (uid, food_name) VALUES (?, ?)", (uid, food_name))
                        conn.commit()
                        reply_text = f"✅ 已成功添加：【{food_name}】！现在你的专属菜单共有 {current_count + 1} 种食物啦。"
        finally:
            conn.close()

    await add_cmd.finish(reply_text)


# ---------- 4.3 删除：不想吃什么 ----------
del_cmd = on_command("我以后不想吃", aliases={"删除食物"}, priority=4, block=True)


@del_cmd.handle()
async def handle_del(args: Message = CommandArg(), uid: int = Depends(get_uid)):
    """处理‘我以后不想吃’指令，从个人专属池中删除食物。"""
    raw_text = args.extract_plain_text().strip()

    if not raw_text:
        await del_cmd.finish("🗑 请告诉我你不想吃什么，例如：我以后不想吃 烤冷面")

    new_foods = _parse_foods(raw_text)

    if len(new_foods) > 1:
        await del_cmd.finish("⚠️ 一次只能删除一个食物哦，请分开删除~")

    food_name = new_foods[0]

    reply_text = ""
    async with _io_lock:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            # 拦截基础食物删除
            cursor.execute("SELECT 1 FROM eat_what_base WHERE food_name = ?", (food_name,))
            if cursor.fetchone():
                reply_text = "🚫 此为基础食物，不可更改哦~"
            else:
                # 检查用户专属菜单里是否有这个食物
                cursor.execute("SELECT 1 FROM eat_what_user WHERE uid = ? AND food_name = ?", (uid, food_name))
                if not cursor.fetchone():
                    reply_text = f"❓ 你的专属菜单里没有【{food_name}】，不需要删除哦~"
                else:
                    # 执行删除
                    cursor.execute("DELETE FROM eat_what_user WHERE uid = ? AND food_name = ?", (uid, food_name))
                    conn.commit()
                    # 查询剩余食物数量
                    cursor.execute("SELECT COUNT(*) as count FROM eat_what_user WHERE uid = ?", (uid,))
                    remaining = cursor.fetchone()["count"]
                    reply_text = f"✅ 已成功删除：【{food_name}】！现在你的专属菜单还有 {remaining} 种食物。"
        finally:
            conn.close()

    await del_cmd.finish(reply_text)


# ---------- 4.4 读取：查看菜单（改为合并转发） ----------
view_cmd = on_command("查看菜单", aliases={"菜单"}, priority=4, block=True)


@view_cmd.handle()
async def handle_view(bot: Bot, event: Event, uid: int = Depends(get_uid)):
    """查看公共菜单和用户专属菜单，以合并转发形式展示。"""
    conn = _get_conn()
    try:
        cursor = conn.cursor()
        # 获取公共菜单（按字母顺序排序）
        cursor.execute("SELECT food_name FROM eat_what_base ORDER BY food_name")
        base_foods = [row["food_name"] for row in cursor.fetchall()]
        # 获取用户专属菜单
        cursor.execute("SELECT food_name FROM eat_what_user WHERE uid = ? ORDER BY food_name", (uid,))
        user_foods = [row["food_name"] for row in cursor.fetchall()]
    finally:
        conn.close()

    base_text = "、".join(base_foods) if base_foods else "暂无"
    user_text = "、".join(user_foods) if user_foods else "暂无（你可以发送“我以后想吃 <食物>”来添加哦）"

    # 构造合并转发的节点列表
    msg_list = [
        f"📋 【公共菜单】\n🍽 {base_text}",
        f"🍱 【你的专属菜单】\n🥘 {user_text}"
    ]

    try:
        # 尝试使用合并转发（防止长文本刷屏）
        chain = await build_forward_chain(bot, msg_list, user_id=int(bot.self_id))
        await send_group_forward_msg(event, bot, chain)
    except Exception:
        # 如果合并转发失败（例如官方QQ机器人限制），降级为普通文本发送
        fallback_text = "\n".join(msg_list)
        await view_cmd.finish(fallback_text)


# ---------- 4.5 帮助菜单（合并转发与兜底降级） ----------
help_cmd = on_command("吃什么帮助", aliases={"吃饭帮助", "吃什么菜单"}, priority=4, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: Event):
    """发送‘吃什么帮助’时，以合并转发形式展示纯文本指令指南。"""
    msg_list = [
        "📖 【吃什么插件使用指南】\n"
        "━━━━━━━━━━━━━━\n"
        "🔸 吃什么 / 今天吃什么\n"
        "  随机帮你决定今天吃什么\n\n"
        "🔸 我以后想吃 <食物名>\n"
        "  添加你个人专属的食物到抽奖池，最多5个字，普通用户上限20个。\n\n"
        "🔸 我以后不想吃 <食物名>\n"
        "  从你的专属抽奖池中删除指定食物\n\n"
        "清空我的菜单/清空专属菜单\n"
        "一键清空你个人的专属菜单（删除所有专属食物）\n\n"
        "🔸 查看菜单 / 菜单\n"
        "  查看公共菜单和你的专属菜单\n\n"
        "🔸 吃什么帮助\n"
        "  显示这条帮助信息\n"
    ]

    try:
        # 尝试使用合并转发（防止长文本刷屏）
        chain = await build_forward_chain(bot, msg_list, user_id=int(bot.self_id))
        await send_group_forward_msg(event, bot, chain)
    except Exception:
        # 如果合并转发失败（例如官方QQ机器人限制），降级为普通文本发送
        fallback_text = "\n".join(msg_list)
        await help_cmd.finish(fallback_text)


# ---------- 4.6 管理员指令：修改公共基础池 ----------
mod_base_cmd = on_command("修改公共菜单", aliases={"修改基础食物"}, priority=4, block=True)


@mod_base_cmd.handle()
async def handle_mod_base(args: Message = CommandArg(), uid: int = Depends(get_uid)):
    """仅限 level 0 的 SU 使用，用于修改公共基础食物池。"""
    # 权限拦截：必须是 0 级 SU
    if not is_su_contributor(uid):
        await mod_base_cmd.finish("⛔ 权限不足，仅限权限等级为 0 的 SU 使用。")

    raw_text = args.extract_plain_text().strip()
    if not raw_text:
        await mod_base_cmd.finish("📝 格式：修改公共菜单 <添加/删除> <食物名>")

    parts = raw_text.split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in ("添加", "删除"):
        await mod_base_cmd.finish("❌ 格式错误，示例：修改公共菜单 添加 云吞")

    action, food_name = parts[0], parts[1].strip()

    if len(food_name) > 5:
        await mod_base_cmd.finish("📏 食物名称太长，暂不支持输入（最多5个字）")

    reply_text = ""
    async with _io_lock:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            if action == "添加":
                # 插入或忽略（不存在则插入，存在则跳过，防止主键冲突报错）
                cursor.execute("INSERT OR IGNORE INTO eat_what_base (food_name) VALUES (?)", (food_name,))
                conn.commit()
                reply_text = f"✅ 已成功添加公共基础食物：{food_name}"
            else:
                # 执行删除并检查是否真的删除了东西
                cursor.execute("DELETE FROM eat_what_base WHERE food_name = ?", (food_name,))
                conn.commit()
                if cursor.rowcount > 0:
                    reply_text = f"✅ 已成功删除公共基础食物：{food_name}"
                else:
                    reply_text = f"❓ 公共基础食物中没有 {food_name}，无需删除"
        finally:
            conn.close()

    await mod_base_cmd.finish(reply_text)
    # ---------- 4.7 用户指令：清空我的专属菜单 ----------
clear_my_cmd = on_command("清空我的菜单", aliases={"清空专属菜单"}, priority=4, block=True)


@clear_my_cmd.handle()
async def handle_clear_my(uid: int = Depends(get_uid)):
    """处理‘清空我的菜单’指令，一键删除当前用户的所有专属食物。"""
    reply_text = ""
    async with _io_lock:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            # 执行删除，并拿到删除的行数
            cursor.execute("DELETE FROM eat_what_user WHERE uid = ?", (uid,))
            conn.commit()
            affected = cursor.rowcount

            if affected > 0:
                reply_text = f"✅ 已成功清空你的专属菜单！本次共删除了 {affected} 种食物。"
            else:
                reply_text = "❓ 你的专属菜单本来就是空的哦，不需要清理~"
        finally:
            conn.close()

    await clear_my_cmd.finish(reply_text)


# ---------- 4.8 管理员指令：清空所有人的专属菜单 ----------
clear_all_cmd = on_command("清空全部菜单", aliases={"清空所有人菜单"}, priority=4, block=True)


@clear_all_cmd.handle()
async def handle_clear_all(uid: int = Depends(get_uid)):
    """仅限 level 0 的 SU 使用，一键清空所有用户的专属食物。"""
    # 权限拦截：必须是 0 级 SU
    if not is_su_contributor(uid):
        await clear_all_cmd.finish("⛔ 权限不足，仅限权限等级为 0 的 SU 使用。")

    reply_text = ""
    async with _io_lock:
        conn = _get_conn()
        try:
            cursor = conn.cursor()
            # 清空整张用户表
            cursor.execute("DELETE FROM eat_what_user")
            conn.commit()
            affected = cursor.rowcount

            if affected > 0:
                reply_text = f"✅ 已成功清空所有用户的专属菜单！本次共删除了 {affected} 条食物记录。"
            else:
                reply_text = "❓ 当前没有任何用户的专属菜单有数据，无需清理~"
        finally:
            conn.close()

    await clear_all_cmd.finish(reply_text)