"""货币 API（/money 路径）：IP 白名单 + 密钥鉴权的资产查询/增减 HTTP 接口，供外部程序调用。"""

from __future__ import annotations

import hmac
import secrets
from typing import Optional

import nonebot
from nonebot.log import logger
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route, Router

from . import config_store, money
from .config_store import config as koinori_config
from .money import KEY_LIST, KEYWORD_LIST

# 允许外部增减的资产键：四大货币 + 井券。last_login/rp/logindays 等
# 签到状态字段禁止外部改动（会破坏签到/运势玩法数据）。
MUTABLE_KEYS = frozenset(KEY_LIST) | {"exgacha"}

_mounted = False


# ================== 鉴权 ==================


def _client_ip(request: Request) -> str:
    if not request.client:
        return ""
    return (request.client.host or "").strip()


def _extract_key(request: Request) -> str:
    key = request.headers.get("x-api-key", "").strip()
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        key = request.query_params.get("key", "").strip()
    return key


def _auth_error(request: Request) -> Optional[JSONResponse]:
    """双重校验：IP 白名单 → API 密钥。通过返回 None，否则返回错误响应。"""
    allow_hosts = [
        str(item).strip() for item in (koinori_config.money_api_allow_hosts or [])
    ]
    ip = _client_ip(request)
    if not allow_hosts:
        return JSONResponse(
            {"ok": False, "error": "IP 白名单为空，货币 API 已拒绝所有访问"}, 403
        )
    if "*" not in allow_hosts:
        # IPv4-mapped IPv6（::ffff:127.0.0.1）与普通 IPv4 同权
        normalized = ip[7:] if ip.startswith("::ffff:") else ip
        if ip not in allow_hosts and normalized not in allow_hosts:
            logger.warning(f"[money_api] 拒绝来源 IP: {ip}")
            return JSONResponse(
                {"ok": False, "error": f"来源 IP {ip} 不在白名单"}, 403
            )

    expected = str(koinori_config.money_api_key or "")
    if not expected:
        return JSONResponse(
            {"ok": False, "error": "API 密钥未配置（重启后将自动重新生成）"}, 503
        )
    provided = _extract_key(request)
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        logger.warning(f"[money_api] 密钥错误或缺失（IP {ip}）")
        return JSONResponse({"ok": False, "error": "API 密钥错误或缺失"}, 401)
    return None


# ================== 参数解析 ==================


def _json_error(message: str, status: int, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, **extra}, status_code=status)


def _parse_uid(raw) -> int:
    if isinstance(raw, bool):
        raise ValueError("uid 需为统一 UID 正整数")
    value = int(str(raw).strip())
    if value <= 0:
        raise ValueError("uid 需为统一 UID 正整数")
    return value


def _parse_amount(raw) -> int:
    if isinstance(raw, bool):
        raise ValueError("amount 需为正整数")
    value = int(raw)  # 接受 int 或纯数字字符串
    if value < 1:
        raise ValueError("amount 需为正整数")
    return value


def _normalize_currency(raw) -> str:
    """资产键或中文别名 → 标准键名；不可变更的键返回空串。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.lower() in MUTABLE_KEYS:
        return text.lower()
    translated = money.translate_name(text)
    return translated if translated in MUTABLE_KEYS else ""


def _user_exists(uid: int, key: str) -> bool:
    return money.UserWallet(uid).get(key) is not None


# ================== HTTP handlers ==================


async def _handle_doc(request: Request) -> Response:
    port = getattr(nonebot.get_driver().config, "port", None) or 8080
    return HTMLResponse(_PAGE_HTML.replace("__PORT__", str(port)))


async def _handle_health(request: Request) -> Response:
    return JSONResponse({"ok": True, "service": "money-api"})


async def _handle_balance(request: Request) -> Response:
    denied = _auth_error(request)
    if denied:
        return denied

    try:
        uid = _parse_uid(request.query_params.get("uid", ""))
    except (TypeError, ValueError) as e:
        return _json_error(str(e), 400)

    raw_keys = request.query_params.get("keys", "").strip()
    if raw_keys:
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        bad = [k for k in keys if k not in KEYWORD_LIST]
        if bad:
            return _json_error(f"未知资产键: {', '.join(bad)}", 400)
    else:
        keys = list(KEY_LIST)

    values = money.UserWallet(uid).get(*keys)
    if values is None:
        return _json_error("用户不存在（uid 未入库）", 404, uid=uid)
    if not isinstance(values, tuple):
        values = (values,)
    balance = dict(zip(keys, values))
    if any(v is None for v in values):
        return _json_error("用户不存在（uid 未入库）", 404, uid=uid)
    return JSONResponse({"ok": True, "uid": uid, "balance": balance})


async def _handle_change(request: Request, direction: str) -> Response:
    denied = _auth_error(request)
    if denied:
        return denied

    try:
        body = await request.json()
    except Exception:
        return _json_error("请求体应为 JSON", 400)
    if not isinstance(body, dict):
        return _json_error("请求体应为 JSON 对象", 400)

    try:
        uid = _parse_uid(body.get("uid"))
        amount = _parse_amount(body.get("amount"))
    except (TypeError, ValueError) as e:
        return _json_error(str(e), 400)
    currency = _normalize_currency(body.get("currency"))
    if not currency:
        return _json_error(
            "currency 非法；可用: " + ", ".join(sorted(MUTABLE_KEYS)), 400
        )
    if not _user_exists(uid, currency):
        return _json_error("用户不存在（uid 未入库）", 404, uid=uid)

    wallet = money.UserWallet(uid)
    if direction == "add":
        if money.increase(uid, currency, amount) != 1:
            return _json_error("写入失败，请查看 bot 日志", 500, uid=uid)
    else:
        result = money.checked_decrease(uid, currency, amount)
        if result is None:
            return _json_error("写入失败，请查看 bot 日志", 500, uid=uid)
        if result is False:
            return _json_error(
                "余额不足",
                409,
                uid=uid,
                balance={currency: wallet.get(currency)},
            )

    logger.info(
        f"[money_api] uid={uid} {currency} {'+' if direction == 'add' else '-'}"
        f"{amount}（IP {_client_ip(request)}）"
    )
    return JSONResponse(
        {
            "ok": True,
            "uid": uid,
            "currency": currency,
            "amount": amount,
            "balance": {currency: wallet.get(currency)},
        }
    )


async def _handle_add(request: Request) -> Response:
    return await _handle_change(request, "add")


async def _handle_deduct(request: Request) -> Response:
    return await _handle_change(request, "deduct")


def build_router() -> Router:
    return Router(
        routes=[
            Route("/", _handle_doc, methods=["GET"]),
            Route("/health", _handle_health, methods=["GET"]),
            Route("/balance", _handle_balance, methods=["GET"]),
            Route("/add", _handle_add, methods=["POST"]),
            Route("/deduct", _handle_deduct, methods=["POST"]),
        ]
    )


# ================== 密钥与挂载 ==================


def ensure_api_key() -> str:
    """返回生效的 API 密钥；未配置时生成强随机密钥并写回配置库（幂等）。"""
    key = str(koinori_config.money_api_key or "").strip()
    if key:
        return key
    key = secrets.token_urlsafe(32)
    try:
        config_store.update_config({"money_api_key": key})
        logger.info(
            "[money_api] 未配置 money_api_key，已自动生成强随机密钥"
            "（配置面板「货币API」分区点「显示敏感值」可查看）"
        )
    except ValueError as e:
        logger.error(f"[money_api] 自动生成密钥写库失败: {e}")
    return str(koinori_config.money_api_key or "")


def mount_money_api() -> bool:
    """把货币 API 挂到驱动端口的 /money（幂等）。非 HTTP 服务端驱动返回 False。"""
    global _mounted
    if _mounted:
        return True
    server_app = getattr(nonebot.get_driver(), "server_app", None)
    if server_app is None:
        return False
    server_app.mount("/money", build_router())
    _mounted = True
    ensure_api_key()
    logger.info(
        "[money_api] 货币 API 已挂载: /money（默认仅允许本机调用；"
        "白名单与密钥见配置面板「货币API」分区）"
    )
    return True


# ================== 使用说明页 ==================

_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Koinoribot 货币 API</title>
<style>
  body { font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #eef1f6; color: #1c2333; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(24,34,62,.08);
          margin: 16px auto; padding: 18px 22px; max-width: 860px; }
  h1 { font-size: 18px; margin: 4px 0; } h2 { font-size: 14px; color: #6b7385; }
  code, pre { font-family: ui-monospace, Consolas, monospace; }
  code { background: #f0f3f9; border-radius: 4px; padding: 1px 6px; font-size: 13px; }
  pre { background: #f6f8fc; border: 1px solid #e3e7ef; border-radius: 8px;
        padding: 12px 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.6; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border: 1px solid #e3e7ef; padding: 7px 10px; text-align: left; }
  th { background: #f6f8fc; color: #6b7385; font-weight: 600; }
  .warn { color: #d8643f; }
</style>
</head>
<body>
<div class="card">
  <h1>Koinoribot 货币 API</h1>
  <h2>供其它插件 / 其它 bot 框架查询与增减用户资产（统一 UID）</h2>
</div>
<div class="card">
  <h2>鉴权</h2>
  <p>需同时通过 <b>IP 白名单</b> 与 <b>API 密钥</b> 校验（配置面板「货币API」分区可改，
  修改后立即生效）：</p>
  <ul>
    <li>白名单默认仅本机（127.0.0.1 / ::1）；跨机调用需把来源 IP 加入白名单或填 <code>*</code>。</li>
    <li>密钥通过请求头 <code>X-API-Key: &lt;密钥&gt;</code>、<code>Authorization: Bearer &lt;密钥&gt;</code>
        或查询参数 <code>?key=&lt;密钥&gt;</code> 传递。</li>
  </ul>
</div>
<div class="card">
  <h2>端点</h2>
  <table>
    <tr><th>方法 / 路径</th><th>说明</th></tr>
    <tr><td><code>GET /money/health</code></td><td>存活探测（无需鉴权）</td></tr>
    <tr><td><code>GET /money/balance?uid=1</code></td>
        <td>查询资产；可选 <code>&amp;keys=gold,starstone,rp,logindays,...</code> 指定字段，
            默认返回四大货币</td></tr>
    <tr><td><code>POST /money/add</code></td>
        <td>增加资产，JSON：<code>{"uid": 1, "currency": "gold", "amount": 100}</code></td></tr>
    <tr><td><code>POST /money/deduct</code></td>
        <td>扣减资产（余额不足返回 409，<span class="warn">绝不透支</span>），请求体同上</td></tr>
  </table>
  <p>currency 可用键名或中文别名：<code>gold</code>=金币、<code>luckygold</code>=幸运币、
  <code>starstone</code>=星星、<code>kirastone</code>=宝石/羽毛石、<code>exgacha</code>=井券/兑换券。
  uid 必须是已存在的统一 UID（不存在返回 404，不代建用户）。</p>
</div>
<div class="card">
  <h2>调用示例（本机）</h2>
<pre># 查询
curl "http://127.0.0.1:__PORT__/money/balance?uid=1" -H "X-API-Key: &lt;密钥&gt;"

# 加 500 金币
curl -X POST "http://127.0.0.1:__PORT__/money/add" \\
     -H "X-API-Key: &lt;密钥&gt;" -H "Content-Type: application/json" \\
     -d '{"uid": 1, "currency": "gold", "amount": 500}'

# 扣 10 星星（中文别名同样可用）
curl -X POST "http://127.0.0.1:__PORT__/money/deduct" \\
     -H "X-API-Key: &lt;密钥&gt;" -H "Content-Type: application/json" \\
     -d '{"uid": 1, "currency": "星星", "amount": 10}'</pre>
  <p>成功返回 <code>{"ok": true, ...}</code>；失败返回
  <code>{"ok": false, "error": "..."}</code>（400 参数错误 / 401 密钥错误 /
  403 IP 不在白名单 / 404 用户不存在 / 409 余额不足）。</p>
</div>
</body>
</html>
"""
