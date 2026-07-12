"""ワークフローノードの実行関数。

各ノードは config と context（先行ノードの出力）を受け取り、出力 dict を返す。
任意シェル実行ノードは提供しない（安全モード、要求仕様 §20.6）。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

TEMPLATE_RE = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


def render_template(text: str, context: dict[str, Any]) -> str:
    """{{nodeId.field.subfield}} を先行ノード出力で置換する。"""

    def repl(m: re.Match) -> str:
        parts = m.group(1).split(".")
        value: Any = context.get(parts[0], {}).get("output", {})
        for part in parts[1:]:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return ""
        if value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return TEMPLATE_RE.sub(repl, text)


class NodeError(RuntimeError):
    pass


def _require_app(config: dict) -> int:
    app_id = config.get("app_id")
    if not isinstance(app_id, int):
        raise NodeError("アプリが選択されていません")
    return app_id


async def _app_control(action: str, config: dict) -> dict:
    from app.applications import service as apps
    from app.applications import systemd as sd
    from app.database import SessionLocal
    from app.models import ManagedApplication

    app_id = _require_app(config)

    def run() -> dict:
        db = SessionLocal()
        try:
            app = db.get(ManagedApplication, app_id)
            if app is None:
                raise NodeError(f"アプリ id={app_id} が見つかりません")
            if action == "status":
                rt = apps.runtime_info(app)
                return {"app": app.name, "status": rt.status, "pid": rt.pid, "uptime_seconds": rt.uptime_seconds}
            if action == "start" and app.application_type != "systemd_service":
                apps.sync_unit(app)
                sd.reset_failed(app.systemd_unit_name)
            fn = {"start": sd.start, "stop": sd.stop, "restart": sd.restart}[action]
            ok, err = fn(app.systemd_unit_name)
            if not ok:
                raise NodeError(f"{action} 失敗: {err}")
            rt = apps.runtime_info(app)
            return {"app": app.name, "ok": True, "status": rt.status}
        finally:
            db.close()

    return await asyncio.to_thread(run)


async def node_app_start(config: dict, ctx: dict) -> dict:
    return await _app_control("start", config)


async def node_app_stop(config: dict, ctx: dict) -> dict:
    return await _app_control("stop", config)


async def node_app_restart(config: dict, ctx: dict) -> dict:
    return await _app_control("restart", config)


async def node_app_status(config: dict, ctx: dict) -> dict:
    return await _app_control("status", config)


async def node_http_request(config: dict, ctx: dict) -> dict:
    url = render_template(str(config.get("url", "")), ctx)
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    method = str(config.get("method", "GET")).upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "HEAD"):
        raise NodeError(f"不正なメソッド: {method}")
    timeout = min(float(config.get("timeout", 30)), 300)
    body = config.get("body")
    if isinstance(body, str) and body.strip():
        body = render_template(body, ctx)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.request(method, url, content=body if isinstance(body, str) else None)
    expect = config.get("expect_status")
    ok = (r.status_code == int(expect)) if expect else r.status_code < 400
    output = {"status_code": r.status_code, "ok": ok, "body": r.text[:4096]}
    if expect and not ok:
        raise NodeError(f"期待ステータス {expect} に対し {r.status_code}")
    return output


OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "gte": lambda a, b: _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "contains": lambda a, b: str(b) in str(a),
}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise NodeError(f"数値比較できません: {v!r}")


async def node_condition(config: dict, ctx: dict) -> dict:
    left = render_template(str(config.get("left", "")), ctx)
    right = render_template(str(config.get("right", "")), ctx)
    op = str(config.get("op", "eq"))
    if op not in OPS:
        raise NodeError(f"不正な演算子: {op}")
    result = OPS[op](left, right)
    return {"result": bool(result), "left": left, "right": right}


async def node_wait(config: dict, ctx: dict) -> dict:
    seconds = min(float(config.get("seconds", 1)), 3600)
    await asyncio.sleep(seconds)
    return {"waited_seconds": seconds}


async def node_webhook(config: dict, ctx: dict) -> dict:
    url = str(config.get("url", ""))
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    message = render_template(str(config.get("message", "")), ctx)
    fmt = config.get("format", "generic")
    payload = {
        "generic": {"message": message},
        "discord": {"content": message},
        "slack": {"text": message},
    }.get(fmt, {"message": message})
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
    return {"status_code": r.status_code, "ok": r.status_code < 400}


async def node_file_exists(config: dict, ctx: dict) -> dict:
    from app.files.service import FileAccessError, resolve

    path = render_template(str(config.get("path", "")), ctx)
    try:
        p = resolve(path)
        return {"exists": True, "is_dir": p.is_dir(), "size": p.stat().st_size}
    except FileNotFoundError:
        return {"exists": False}
    except FileAccessError as e:
        raise NodeError(str(e))


async def node_trigger(config: dict, ctx: dict) -> dict:
    return {"ok": True}


NODE_EXECUTORS = {
    "trigger": node_trigger,
    "app.start": node_app_start,
    "app.stop": node_app_stop,
    "app.restart": node_app_restart,
    "app.status": node_app_status,
    "http.request": node_http_request,
    "condition.if": node_condition,
    "util.wait": node_wait,
    "notify.webhook": node_webhook,
    "file.exists": node_file_exists,
}

# ノードごとの既定タイムアウト（秒）
NODE_TIMEOUTS = {"util.wait": 3700, "http.request": 320}
DEFAULT_NODE_TIMEOUT = 120
