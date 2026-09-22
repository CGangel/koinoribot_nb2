"""周期缓存清理：把 glibc 囤住的空闲内存页归还操作系统。

高流量下图片渲染（PIL 解码）、base64、图床条目等大块瞬态内存释放后，
glibc 通常仍把内存页留在 arena 里不还给系统，RSS 表现为「棘轮式」上涨
后不回落（反映历史峰值而非真实用量；已实测确认 Python 层无泄漏）。
周期调用 malloc_trim(0) 可归还这些空闲页（高压流量后每轮约 20-40MB）。

周期由配置面板「缓存清理周期」（cache_clean_interval，秒）热控制：
- >0：每周期清理一次；实际归还内存时记 INFO（无内存可还只记 DEBUG，
  避免高频周期刷屏）
- =0（默认）：关闭；关闭期间每 60 秒轮询配置，面板改值后一分钟内生效
仅 Linux（glibc）可用；其余平台启动时记一条提示后不动作。
"""

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
