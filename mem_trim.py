"""周期 malloc_trim：把 glibc 囤住的空闲内存页归还系统，缓解 RSS 棘轮上涨（仅 Linux，周期可配置）。"""

import asyncio
import ctypes

from nonebot import get_driver, logger

from .config_store import config

# 关闭态的配置轮询间隔（秒）：面板从 0 改为正值后最迟此时长生效
_POLL_WHEN_DISABLED = 60

# 低于该归还量（KB）只记 DEBUG，避免高频周期下刷 INFO 日志
_QUIET_BELOW_KB = 1024


def _rss_kb() -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def _trim_once(libc) -> None:
    before = _rss_kb()
    libc.malloc_trim(0)
    after = _rss_kb()
    if before < 0 or after < 0:
        return
    freed_kb = before - after
    if freed_kb >= _QUIET_BELOW_KB:
        logger.info(
            f"[mem_trim] 缓存清理：{before // 1024}MB → {after // 1024}MB"
            f"（归还 {freed_kb / 1024:.0f}MB）"
        )
    else:
        logger.debug(f"[mem_trim] 缓存清理：无可归还内存（{before // 1024}MB）")


async def _loop() -> None:
    try:
        libc = ctypes.CDLL("libc.so.6")
    except OSError:
        logger.info("[mem_trim] 当前平台无 glibc malloc_trim，缓存清理不可用")
        return
    while True:
        interval = int(getattr(config, "cache_clean_interval", 0) or 0)
        if interval <= 0:
            await asyncio.sleep(_POLL_WHEN_DISABLED)
            continue
        await asyncio.sleep(interval)
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _trim_once, libc
            )
        except Exception as error:
            logger.warning(f"[mem_trim] 缓存清理执行失败: {error}")


@get_driver().on_startup
async def _start_trim_loop() -> None:
    asyncio.get_running_loop().create_task(_loop())
    logger.info("[mem_trim] 周期缓存清理已就绪（「缓存清理周期」=0 时关闭）")
