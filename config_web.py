"""8889 配置面板（aiohttp）与“冰祈配置”指令。

- 面板列出 config 表全部配置项（按分区分组），修改保存时走
  config_store.update_config：校验 → 写库 → 原地更新内存。
- 密码取自 passwd.py 的 PANEL_PASSWORD；未配置时拒绝一切登录。
- 防爆破：同一 IP 在 10 分钟窗口内密码错 5 次即锁定 15 分钟；
  会话 cookie 有效期 12 小时，仅保存在内存（重启后需重新登录）。
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time
from typing import Optional

import aiohttp
from aiohttp import web

import nonebot
from nonebot.adapters import Bot, Event
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import Depends

from . import config_store, su_manager
from .config_store import config as koinori_config
from .tools import get_uid

CONFIG_WEB_HOST = "0.0.0.0"
CONFIG_WEB_PORT = 8889

SESSION_COOKIE = "koinori_cfg_session"
SESSION_TTL = 12 * 3600

# 防爆破参数：10 分钟内错 5 次 → 锁 15 分钟
FAIL_WINDOW = 600
FAIL_THRESHOLD = 5
LOCK_DURATION = 900

_runner: Optional[web.AppRunner] = None
_site: Optional[web.TCPSite] = None

_sessions: dict[str, float] = {}  # token -> 过期时间戳
_fail_counts: dict[str, list[float]] = {}  # ip -> 窗口内失败时间戳
_locked: dict[str, float] = {}  # ip -> 解锁时间戳


# ================== 会话与防爆破 ==================


def _client_ip(request: web.Request) -> str:
    return request.remote or "unknown"


def _prune_expired(now: float) -> None:
    for token in [t for t, exp in _sessions.items() if exp < now]:
        _sessions.pop(token, None)


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL
    return token


def _valid_session(request: web.Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    expiry = _sessions.get(token)
    return bool(token and expiry and expiry > time.time())


def _is_locked(ip: str) -> bool:
    until = _locked.get(ip)
    if until and until > time.time():
        return True
    _locked.pop(ip, None)
    return False


def _register_failure(ip: str) -> Optional[int]:
    """记录一次密码错误。返回剩余尝试次数；达到阈值时当场锁定并返回 None。"""
    now = time.time()
    fails = [t for t in _fail_counts.get(ip, []) if now - t < FAIL_WINDOW]
    fails.append(now)
    _fail_counts[ip] = fails
    if len(fails) >= FAIL_THRESHOLD:
        _locked[ip] = now + LOCK_DURATION
        _fail_counts.pop(ip, None)
        logger.warning(
            f"[config_web] IP {ip} 密码错误达 {FAIL_THRESHOLD} 次，锁定 {LOCK_DURATION // 60} 分钟"
        )
        return None
    return FAIL_THRESHOLD - len(fails)


def _clear_failures(ip: str) -> None:
    _fail_counts.pop(ip, None)
    _locked.pop(ip, None)


def check_password(password: str) -> bool:
    """常量时间比较面板密码；未配置密码时一律拒绝。"""
    expected = config_store.get_panel_password()
    if not expected:
        return False
    return hmac.compare_digest(password.encode(), expected.encode())


# ================== HTTP handlers ==================


async def _handle_login(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    ip = _client_ip(request)
    if _is_locked(ip):
        retry_after = int(_locked.get(ip, 0) - time.time()) + 1
        return web.json_response(
            {"ok": False, "error": f"尝试过于频繁，已锁定，请 {retry_after} 秒后再试"},
            status=429,
            headers={"Retry-After": str(retry_after)},
        )

    try:
        if request.content_type == "application/json":
            body = await request.json()
        else:
            body = await request.post()
        password = str(body.get("password", ""))
    except Exception:
        return web.json_response({"ok": False, "error": "请求格式错误"}, status=400)

    # 小延迟抵抗高频爆破
    await asyncio.sleep(0.2)

    if not check_password(password):
        remaining = _register_failure(ip)
        if remaining is None:
            retry_after = int(_locked.get(ip, 0) - time.time()) + 1
            return web.json_response(
                {"ok": False, "error": f"尝试过于频繁，已锁定，请 {retry_after} 秒后再试"},
                status=429,
                headers={"Retry-After": str(retry_after)},
            )
        return web.json_response(
            {"ok": False, "error": f"密码错误，剩余尝试次数 {remaining}"}, status=401
        )

    _clear_failures(ip)
    token = _new_session()
    response = web.json_response({"ok": True})
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="Lax"
    )
    logger.info(f"[config_web] IP {ip} 登录配置面板成功")
    return response


async def _handle_logout(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    token = request.cookies.get(SESSION_COOKIE, "")
    _sessions.pop(token, None)
    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE)
    return response


async def _handle_get_config(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    if not _valid_session(request):
        return web.json_response({"ok": False, "error": "未登录"}, status=401)
    reveal = request.query.get("reveal") == "1"
    data = config_store.dump_for_panel(reveal=reveal)
    return web.json_response({"ok": True, **data})


async def _handle_post_config(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    if not _valid_session(request):
        return web.json_response({"ok": False, "error": "未登录"}, status=401)
    try:
        body = await request.json()
        updates = body.get("updates")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("updates 应为非空对象")
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": f"请求格式错误: {e}"}, status=400
        )

    try:
        changed = config_store.update_config(updates)
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)

    logger.info(f"[config_web] 配置已更新: {changed}")
    return web.json_response({"ok": True, "updated": changed})


async def _handle_health(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    return web.json_response({"ok": True})


async def _handle_index(request: web.Request) -> web.Response:  # NOSONAR - aiohttp requires an async handler
    return web.Response(
        text=_PAGE_HTML, content_type="text/html", charset="utf-8"
    )


def create_config_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/login", _handle_login)
    app.router.add_post("/logout", _handle_logout)
    app.router.add_get("/api/config", _handle_get_config)
    app.router.add_post("/api/config", _handle_post_config)
    return app


async def start_config_web() -> None:
    global _runner, _site
    if _runner is not None:
        return

    runner = web.AppRunner(create_config_web_app())
    await runner.setup()
    site = web.TCPSite(runner, CONFIG_WEB_HOST, CONFIG_WEB_PORT)
    try:
        await site.start()
    except OSError as e:
        await runner.cleanup()
        logger.error(
            f"[config_web] 配置面板启动失败，端口 {CONFIG_WEB_PORT} 可能已被占用: {e}"
        )
        return

    _runner, _site = runner, site
    if not config_store.get_panel_password():
        logger.warning("[config_web] passwd.py 未配置 PANEL_PASSWORD，面板将拒绝登录")
    logger.info(
        f"[config_web] 配置面板已启动: http://{CONFIG_WEB_HOST}:{CONFIG_WEB_PORT}/"
    )


async def stop_config_web() -> None:
    global _runner, _site
    runner, _runner, _site = _runner, None, None
    if runner is not None:
        await runner.cleanup()
        _sessions.clear()
        logger.info("[config_web] 配置面板已停止")


# ================== “冰祈配置”指令 ==================


def panel_base_url() -> str:
    host = (
        koinori_config.ip_address
        if koinori_config.public_bot and koinori_config.ip_address
        else "127.0.0.1"
    )
    return f"http://{host}:{CONFIG_WEB_PORT}/"


def owner_level_exists() -> bool:
    """是否存在等级 0 的 su：passwd.SUPERUSERS 或数据库 superusers 表。"""
    if config_store.get_passwd_superusers():
        return True
    try:
        return any(
            su_manager.get_su_level(uid) == su_manager.SU_LEVEL_CONTRIBUTOR
            for uid in su_manager.get_all_su_uids()
        )
    except Exception:
        return False


def panel_access_reply(uid: int) -> str:
    """根据统一 UID 生成“冰祈配置”的回复文案。"""
    level = su_manager.get_su_level(uid)
    if level == su_manager.SU_LEVEL_CONTRIBUTOR:
        return f"配置面板：{panel_base_url()}"
    if not owner_level_exists():
        return (
            "当前不存在等级为 0 的超级用户，无法使用配置面板。\n"
            "请在 koinoribot_nb2/passwd.py 的 SUPERUSERS 中添加你的统一 UID（等级 0），"
            "或将数据库 superusers 表中对应 uid 的 level 设为 0，然后重启 bot。"
        )
    return "仅等级为 0 的超级用户可以使用该指令哦"


panel_cmd = nonebot.on_command("冰祈配置", priority=5, block=True)


@panel_cmd.handle()
async def handle_panel_command(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    uid: int = Depends(get_uid),
):
    await matcher.finish(panel_access_reply(uid))


# ================== 面板页面 ==================

_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Koinoribot 配置面板</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #f5f6f8; color: #222; }
  .card { background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
          max-width: 780px; margin: 24px auto; padding: 20px 24px; }
  h1 { font-size: 20px; } h2 { font-size: 15px; margin: 18px 0 8px; color: #444;
          border-bottom: 1px solid #eee; padding-bottom: 4px; }
  .row { display: flex; align-items: center; gap: 10px; margin: 6px 0; flex-wrap: wrap; }
  .row label { flex: 1 1 100%; min-width: 0; font-family: monospace; font-size: 13px;
               word-break: break-all; }
  .row input { flex: 1 1 100%; min-width: 0; width: 100%; padding: 5px 8px;
               border: 1px solid #ccc; border-radius: 6px;
               font-family: monospace; font-size: 13px; }
  @media (min-width: 640px) {
    .row label { flex: 0 0 260px; }
    .row input { flex: 1 1 auto; width: auto; }
  }
  .tag { font-size: 11px; color: #888; }
  .desc { display: block; font-family: system-ui, sans-serif; font-size: 11px;
          color: #999; margin-top: 2px; }
  .hint { font-size: 12px; color: #888; margin: 8px 0; word-break: break-all; }
  #bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  button { padding: 6px 16px; border: 0; border-radius: 6px; background: #3b7ddd;
           color: #fff; cursor: pointer; }
  button.sec { background: #aaa; }
  #msg { font-size: 13px; margin-left: 8px; word-break: break-all; }
  .locked { display: none; }
</style>
</head>
<body>
<div class="card" id="login">
  <h1>Koinoribot 配置面板 · 登录</h1>
  <div class="row"><label>密码</label><input type="password" id="pwd"></div>
  <div class="hint">访问密码见 koinoribot_nb2/passwd.py 的 PANEL_PASSWORD</div>
  <div id="bar"><button onclick="login()">登录</button><span id="msg"></span></div>
</div>
<div class="card locked" id="panel">
  <h1>Koinoribot 配置面板 <span class="tag">修改保存后立即生效（写入数据库并同步内存）</span></h1>
  <div id="bar">
    <button onclick="save()">保存修改</button>
    <button class="sec" onclick="load(true)">显示敏感值</button>
    <button class="sec" onclick="logout()">退出</button>
    <span id="msg"></span>
  </div>
  <div id="sections"></div>
</div>
<script>
let data = {sections: {}};

async function login() {
  const msg = document.getElementById('msg');
  const r = await fetch('/login', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password: document.getElementById('pwd').value})});
  const j = await r.json().catch(() => ({}));
  if (r.ok) { load(); } else { msg.textContent = j.error || '登录失败'; }
}

async function load(reveal) {
  const r = await fetch('/api/config' + (reveal ? '?reveal=1' : ''));
  if (r.status === 401) { show(false); return; }
  data = await r.json(); render(); show(true);
}

function show(on) {
  document.getElementById('login').classList.toggle('locked', on);
  document.getElementById('panel').classList.toggle('locked', !on);
}

function render() {
  const root = document.getElementById('sections'); root.innerHTML = '';
    for (const [section, fields] of Object.entries(data.sections)) {
    const h = document.createElement('h2'); h.textContent = section; root.appendChild(h);
    for (const f of fields) {
      const row = document.createElement('div'); row.className = 'row';
      const label = document.createElement('label');
      label.appendChild(document.createTextNode(f.key + ' '));
      const tag = document.createElement('span'); tag.className = 'tag';
      tag.textContent = f.type + (f.masked ? ' ·已打码' : '');
      label.appendChild(tag);
      if (f.desc) {
        const desc = document.createElement('span'); desc.className = 'desc';
        desc.textContent = f.desc;
        label.appendChild(desc);
      }
      const input = document.createElement('input');
      input.id = 'f_' + f.key;
      input.dataset.type = f.type;
      let value = (f.type === 'list' || f.type === 'dict')
        ? JSON.stringify(f.value, null, 0) : String(f.value);
      input.value = value;
      row.appendChild(label); row.appendChild(input); root.appendChild(row);
    }
  }
}

async function save() {
  const msg = document.getElementById('msg');
  const updates = {};
  const current = await (await fetch('/api/config')).json();
  const currentValues = {};
  for (const fields of Object.values(current.sections))
    for (const f of fields) currentValues[f.key] = f.value;
  for (const [section, fields] of Object.entries(data.sections)) {
    for (const f of fields) {
      const el = document.getElementById('f_' + f.key);
      if (!el) continue;
      let v = el.value;
      if (f.type === 'int') v = parseInt(v, 10);
      else if (f.type === 'float') v = parseFloat(v);
      else if (f.type === 'bool') v = (v === 'true' || v === 'True' || v === '1');
      else if (f.type === 'list' || f.type === 'dict') {
        try { v = JSON.parse(v); } catch (e) { msg.textContent = section + '/' + f.key + ' 不是合法 JSON'; return; }
      }
      const orig = currentValues[f.key];
      const origStr = (f.type === 'list' || f.type === 'dict') ? JSON.stringify(orig) : String(orig);
      const nowStr = (f.type === 'list' || f.type === 'dict') ? JSON.stringify(v) : String(v);
      if (f.masked || nowStr !== origStr) updates[f.key] = v;
    }
  }
  if (!Object.keys(updates).length) { msg.textContent = '没有修改'; return; }
  const r = await fetch('/api/config', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({updates})});
  const j = await r.json().catch(() => ({}));
  msg.textContent = r.ok ? ('已更新: ' + j.updated.join(', ')) : (j.error || '保存失败');
  if (r.ok) load();
}

async function logout() {
  await fetch('/logout', {method: 'POST'});
  show(false);
}

load();
</script>
</body>
</html>
"""
