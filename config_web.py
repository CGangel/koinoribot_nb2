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
  :root {
    --bg: #eef1f6; --card: #fff; --ink: #1c2333; --sub: #6b7385;
    --line: #e3e7ef; --accent: #3b7ddd; --accent-ink: #fff;
    --ok: #2e9e5b; --warn: #d8643f; --chip: #f0f3f9;
    --radius: 12px; --shadow: 0 1px 3px rgba(24, 34, 62, .08);
  }
  * { box-sizing: border-box; }
  body { font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: var(--bg); color: var(--ink); }
  .card { background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow);
          margin: 16px auto; padding: 18px 20px; }
  #login-card { max-width: 380px; margin-top: 12vh; text-align: center; }
  #login-card h1 { font-size: 18px; margin: 4px 0 2px; }
  #login-card .sub { color: var(--sub); font-size: 12px; margin-bottom: 6px; }
  .logo { width: 44px; height: 44px; border-radius: 12px; background: linear-gradient(135deg,#5b9cf5,#3b7ddd);
          color: #fff; font-size: 22px; line-height: 44px; margin: 0 auto 8px; }
  .pwdwrap { position: relative; margin: 10px 0 14px; }
  .pwdwrap input { width: 100%; padding: 10px 44px 10px 12px; border: 1px solid var(--line);
                   border-radius: 8px; font-size: 14px; outline: none; }
  .pwdwrap input:focus { border-color: var(--accent); }
  .pwdwrap button { position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
                    border: 0; background: none; cursor: pointer; color: var(--sub); font-size: 15px; }
  button { border: 0; border-radius: 8px; cursor: pointer; font-size: 13px; }
  .btn { padding: 9px 22px; background: var(--accent); color: var(--accent-ink); }
  .btn:disabled { opacity: .45; cursor: default; }
  .btn.sec { background: var(--chip); color: var(--ink); }

  #wrap { max-width: 860px; padding: 0 12px 90px; }
  .topbar { position: sticky; top: 10px; z-index: 20; display: flex; gap: 10px;
            align-items: center; flex-wrap: wrap; padding: 12px 18px; }
  .topbar h1 { font-size: 16px; margin: 0; flex: 1; }
  .topbar h1 .tag { font-size: 11px; color: var(--sub); font-weight: normal; margin-left: 8px; }
  #msg { font-size: 12px; color: var(--warn); word-break: break-all; }

  .section h2 { font-size: 13px; color: var(--sub); font-weight: 600; letter-spacing: .5px;
                margin: 4px 2px 10px; }
  .field { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin: 8px 0;
           transition: border-color .15s, box-shadow .15s; }
  .field.changed { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(59,125,221,.14); }
  .field .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 7px; }
  .field .name { font-family: ui-monospace, Consolas, monospace; font-size: 13px; font-weight: 600; }
  .chip { font-size: 10px; color: var(--sub); background: var(--chip); border-radius: 5px; padding: 1px 7px; }
  .chip.secret { color: var(--warn); background: #fdeeea; }
  .field .desc { font-size: 11.5px; color: var(--sub); margin-top: 3px; line-height: 1.5; }

  input[type=text], input[type=number] { padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 8px; font-size: 13px; font-family: ui-monospace, Consolas, monospace;
    outline: none; min-width: 0; width: 100%; transition: border-color .15s; }
  input:focus { border-color: var(--accent); }

  .switch { position: relative; display: inline-block; width: 46px; height: 25px; flex: none; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch .track { position: absolute; inset: 0; background: #cdd4e0; border-radius: 999px;
                   transition: .18s; cursor: pointer; }
  .switch .track:before { content: ""; position: absolute; height: 19px; width: 19px; left: 3px;
                          top: 3px; background: #fff; border-radius: 50%; transition: .18s;
                          box-shadow: 0 1px 2px rgba(0,0,0,.2); }
  .switch input:checked + .track { background: var(--ok); }
  .switch input:checked + .track:before { transform: translateX(21px); }
  .boolrow { display: flex; align-items: center; gap: 10px; }
  .boolrow .state { font-size: 12px; color: var(--sub); min-width: 3em; }
  .boolrow .state.on { color: var(--ok); font-weight: 600; }

  .items .row { display: flex; gap: 6px; align-items: center; margin: 6px 0; }
  .items .row input { flex: 1; }
  .kv .row { display: grid; grid-template-columns: minmax(90px, 38%) auto minmax(90px, 1fr) 30px;
             gap: 6px; align-items: center; margin: 6px 0; }
  .kv .arrow { color: var(--sub); font-size: 12px; }
  .del { width: 28px; height: 28px; border-radius: 7px; background: var(--chip); color: var(--sub);
         font-size: 14px; flex: none; }
  .del:hover { background: #fdeeea; color: var(--warn); }
  .add { margin-top: 7px; padding: 6px 14px; background: var(--chip); color: var(--accent);
         font-weight: 600; }
  .add:hover { background: #e3ecfb; }
  .empty-tip { font-size: 12px; color: var(--sub); padding: 4px 2px; }

  .savebar { position: fixed; left: 12px; right: 12px; bottom: 0; z-index: 30;
             display: flex; align-items: center; gap: 12px; padding: 12px 18px;
             border-radius: var(--radius) var(--radius) 0 0; }
  .savebar .count { font-size: 13px; color: var(--sub); flex: 1; }
  .savebar .count b { color: var(--accent); font-size: 16px; margin: 0 3px; }
  .locked { display: none; }
  @media (max-width: 640px) {
    .kv .row { grid-template-columns: 1fr 1fr; }
    .kv .arrow { display: none; }
  }
</style>
</head>
<body>

<div class="card locked" id="login-card">
  <div class="logo">祈</div>
  <h1>Koinoribot 配置面板</h1>
  <div class="sub">访问密码见 koinoribot_nb2/passwd.py 的 PANEL_PASSWORD</div>
  <div class="pwdwrap">
    <input type="password" id="pwd" placeholder="面板密码" autocomplete="current-password">
    <button type="button" id="pwdEye" title="显示/隐藏密码">👁</button>
  </div>
  <div><button class="btn" style="width:100%" onclick="login()">登 录</button></div>
  <div id="msg" style="margin-top:10px"></div>
</div>

<div id="wrap" class="locked">
  <div class="topbar card">
    <h1>Koinoribot 配置面板<span class="tag">修改保存后立即生效（写入数据库并同步内存）</span></h1>
    <button class="btn sec" id="revealBtn" onclick="toggleSecret()">显示敏感值</button>
    <button class="btn sec" onclick="load()">重置</button>
    <button class="btn sec" onclick="logout()">退出</button>
  </div>
  <div id="sections"></div>
  <div id="msg2" class="card locked" style="padding:10px 18px"></div>
</div>

<div class="savebar card locked" id="savebar">
  <span class="count" id="count">未修改</span>
  <button class="btn sec" onclick="load()">放弃修改</button>
  <button class="btn" id="saveBtn" onclick="save()">保存修改</button>
</div>

<script>
let data = {sections: {}};
let revealed = false;
let fieldMeta = {};   // key -> field（类型/原始值/打码标记）
const byId = k => document.getElementById('f_' + k);

function show(on) {
  document.getElementById('login-card').classList.toggle('locked', on);
  document.getElementById('wrap').classList.toggle('locked', !on);
  document.getElementById('savebar').classList.toggle('locked', !on);
}

function flash(msg, isErr) {
  const el = document.getElementById('msg2');
  el.classList.remove('locked');
  el.style.color = isErr ? 'var(--warn)' : 'var(--ok)';
  el.textContent = msg;
  setTimeout(() => el.classList.add('locked'), 5000);
}

async function login() {
  const msg = document.getElementById('msg');
  const r = await fetch('/login', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password: document.getElementById('pwd').value})});
  const j = await r.json().catch(() => ({}));
  if (r.ok) { load(); } else { msg.textContent = j.error || '登录失败'; }
}

document.getElementById('pwdEye').onclick = () => {
  const el = document.getElementById('pwd');
  el.type = el.type === 'password' ? 'text' : 'password';
};
document.getElementById('pwd').addEventListener('keydown', e => {
  if (e.key === 'Enter') login();
});

function toggleSecret() { load(!revealed); }

async function load(reveal) {
  if (reveal === undefined) reveal = revealed;
  const r = await fetch('/api/config' + (reveal ? '?reveal=1' : ''));
  if (r.status === 401) { show(false); return; }
  data = await r.json();
  revealed = reveal;
  fieldMeta = {};
  for (const fields of Object.values(data.sections)) for (const f of fields) fieldMeta[f.key] = f;
  document.getElementById('revealBtn').textContent = revealed ? '隐藏敏感值' : '显示敏感值';
  render();
  show(true);
}

// ================== 渲染 ==================

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function numberTypeOf(items) {
  return items.length && items.every(x => typeof x === 'number') ? 'number' : 'string';
}

function render() {
  const root = document.getElementById('sections');
  root.innerHTML = '';
  for (const [section, fields] of Object.entries(data.sections)) {
    const card = el('div', 'card section');
    card.appendChild(el('h2', null, section));
    for (const f of fields) card.appendChild(renderField(f));
    root.appendChild(card);
  }
  refreshDirty();
}

function renderField(f) {
  const box = el('div', 'field');
  box.id = 'box_' + f.key;

  const head = el('div', 'head');
  head.appendChild(el('span', 'name', f.key));
  head.appendChild(el('span', 'chip' + (f.masked ? ' secret' : ''),
                      f.type + (f.masked ? ' · 已打码' : '')));
  box.appendChild(head);
  if (f.desc) box.appendChild(el('div', 'desc', f.desc));

  if (f.type === 'bool') box.appendChild(renderBool(f));
  else if (f.type === 'list') box.appendChild(renderList(f));
  else if (f.type === 'dict') box.appendChild(renderDict(f));
  else box.appendChild(renderScalar(f));
  return box;
}

function renderBool(f) {
  const row = el('div', 'boolrow');
  const sw = el('label', 'switch');
  const input = document.createElement('input');
  input.type = 'checkbox'; input.id = 'f_' + f.key; input.checked = !!f.value;
  const track = el('span', 'track');
  sw.append(input, track);
  const state = el('span', 'state' + (f.value ? ' on' : ''), f.value ? '已开启' : '已关闭');
  input.addEventListener('change', () => {
    state.textContent = input.checked ? '已开启' : '已关闭';
    state.classList.toggle('on', input.checked);
    refreshDirty();
  });
  row.append(sw, state);
  return row;
}

function renderScalar(f) {
  const input = document.createElement('input');
  input.id = 'f_' + f.key;
  input.type = f.type === 'int' || f.type === 'float' ? 'number' : 'text';
  if (f.type === 'float') input.step = 'any';
  if (f.type === 'int') input.step = '1';
  if (f.masked) { input.placeholder = f.value; input.value = ''; }
  else input.value = String(f.value);
  input.addEventListener('input', refreshDirty);
  return input;
}

function renderList(f) {
  const wrap = el('div', 'items');
  wrap.id = 'f_' + f.key;
  const defType = numberTypeOf(f.value || []);

  function addRow(value, etype) {
    const row = el('div', 'row');
    const input = document.createElement('input');
    input.type = 'text';
    input.dataset.etype = etype;
    if (value !== undefined) input.value = String(value);
    const del = el('button', 'del', '✕');
    del.onclick = () => { row.remove(); refreshDirty(); };
    row.append(input, del);
    wrap.appendChild(row);
    input.addEventListener('input', refreshDirty);
    return input;
  }

  const items = Array.isArray(f.value) ? f.value : [];
  if (!items.length) wrap.appendChild(el('div', 'empty-tip', '（空列表）'));
  items.forEach(v => addRow(v, typeof v === 'number' ? 'number' : 'string'));
  const add = el('button', 'add', '+ 添加一项');
  add.onclick = () => {
    const tip = wrap.querySelector('.empty-tip');
    if (tip) tip.remove();
    addRow(undefined, defType).focus();
    refreshDirty();
  };
  wrap.appendChild(add);
  return wrap;
}

function renderDict(f) {
  const wrap = el('div', 'kv');
  wrap.id = 'f_' + f.key;
  const entries = f.value && typeof f.value === 'object' ? Object.entries(f.value) : [];
  const defType = numberTypeOf(entries.map(e => e[1]));

  function addRow(k, v, etype) {
    const row = el('div', 'row');
    const keyIn = document.createElement('input');
    keyIn.type = 'text'; keyIn.dataset.role = 'k'; keyIn.placeholder = '键';
    const valIn = document.createElement('input');
    valIn.type = 'text'; valIn.dataset.role = 'v'; valIn.dataset.etype = etype;
    valIn.placeholder = '值';
    if (k !== undefined) keyIn.value = String(k);
    if (v !== undefined && v !== '') valIn.value = String(v);
    const del = el('button', 'del', '✕');
    del.onclick = () => { row.remove(); refreshDirty(); };
    row.append(keyIn, el('span', 'arrow', '→'), valIn, del);
    wrap.appendChild(row);
    keyIn.addEventListener('input', refreshDirty);
    valIn.addEventListener('input', refreshDirty);
  }

  if (!entries.length) wrap.appendChild(el('div', 'empty-tip', '（空）'));
  entries.forEach(([k, v]) => addRow(k, v, typeof v === 'number' ? 'number' : 'string'));
  const add = el('button', 'add', '+ 添加键值对');
  add.onclick = () => {
    const tip = wrap.querySelector('.empty-tip');
    if (tip) tip.remove();
    addRow(undefined, undefined, defType);
    const rows = wrap.querySelectorAll('.row');
    if (rows.length) rows[rows.length - 1].querySelector('[data-role=k]').focus();
    refreshDirty();
  };
  wrap.appendChild(add);
  return wrap;
}

// ================== 采集与保存 ==================

function coerce(text, etype) { return etype === 'number' ? Number(text) : text; }

function collect(f) {
  if (f.type === 'bool') return byId(f.key).checked;
  if (f.type === 'list') {
    return Array.from(byId(f.key).querySelectorAll('.row input')).map(
      input => coerce(input.value, input.dataset.etype));
  }
  if (f.type === 'dict') {
    const obj = {};
    byId(f.key).querySelectorAll('.row').forEach(row => {
      const k = row.querySelector('[data-role=k]').value.trim();
      if (!k) return;
      const vIn = row.querySelector('[data-role=v]');
      obj[k] = coerce(vIn.value, vIn.dataset.etype);
    });
    return obj;
  }
  const input = byId(f.key);
  if (f.type === 'int') return parseInt(input.value, 10);
  if (f.type === 'float') return parseFloat(input.value);
  return input.value;
}

function isChanged(f) {
  // 打码字符串：占位符即打码值，输入框留空表示未修改
  if (f.masked && f.type === 'str') return byId(f.key).value !== '';
  return JSON.stringify(collect(f)) !== JSON.stringify(f.value);
}

function changedKeys() {
  return Object.values(fieldMeta).filter(isChanged).map(f => f.key);
}

function refreshDirty() {
  const keys = changedKeys();
  Object.values(fieldMeta).forEach(f => {
    const box = document.getElementById('box_' + f.key);
    if (box) box.classList.toggle('changed', keys.includes(f.key));
  });
  const count = document.getElementById('count');
  const btn = document.getElementById('saveBtn');
  if (!keys.length) { count.textContent = '未修改'; btn.disabled = true; }
  else { count.innerHTML = '已修改 <b>' + keys.length + '</b> 项'; btn.disabled = false; }
}

async function save() {
  const updates = {};
  for (const f of Object.values(fieldMeta)) {
    if (!isChanged(f)) continue;
    updates[f.key] = collect(f);
  }
  if (!Object.keys(updates).length) { flash('没有修改'); return; }
  const r = await fetch('/api/config', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({updates})});
  const j = await r.json().catch(() => ({}));
  if (r.ok) { flash('已更新: ' + j.updated.join(', ')); load(); }
  else flash(j.error || '保存失败', true);
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
