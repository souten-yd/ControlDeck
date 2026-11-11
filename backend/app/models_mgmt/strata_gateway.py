"""Strata model registration and on-demand server control.

Strata is explicitly installed by a settings.manage action. Models are registered
and launched separately; no catalog refresh or process startup downloads weights.
Only loopback endpoints, fixed upstream CLI arguments and managed presets are used.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import data_dir
from app.models_mgmt import strata

_FAMILIES = {
    "qwen": {"q2_0", "iq2_xs", "iq3_xxs", "iq3_s"},
    "deepseek41": {"ista-das-coder", "ist-das-2.00bpw"},
}
_CONTEXTS = {"8k", "16k", "32k", "64k", "128k"}
_ALIAS = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
_MIN_PORT, _MAX_PORT = 8081, 65535


class StrataGatewayError(RuntimeError):
    pass


def _path() -> Path:
    return data_dir().resolve() / "runtimes" / "strata" / "gateway-models.json"


def _read() -> dict[str, dict]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value for key, value in raw.items()
        if isinstance(key, str) and _ALIAS.fullmatch(key) and isinstance(value, dict)
    }


def _write(items: dict[str, dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _run(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    if not strata.installed():
        raise StrataGatewayError("Strataは未導入です。設定→オプション機能から導入してください")
    try:
        return subprocess.run([str(strata.binary_path()), *args], env=strata._env(),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StrataGatewayError("Strataコマンドが失敗、またはタイムアウトしました") from exc


def _running() -> list[dict]:
    if not strata.installed():
        return []
    try:
        result = _run(["launch", "--status", "--output", "json"], timeout=12)
        if result.returncode:
            return []
        payload = json.loads(result.stdout.splitlines()[0])
        rows = payload.get("running", [])
        return rows if isinstance(rows, list) else []
    except (StrataGatewayError, ValueError, TypeError):
        return []


def _present(instance: dict, *, running: list[dict]) -> dict:
    alias = instance["alias"]
    active = next(
        (row for row in running
         if isinstance(row, dict) and row.get("profile") == f"launch-cd-{alias}"
         and int(row.get("port") or 0) == instance["port"]),
        None,
    )
    return {
        **instance,
        "runtime": "strata", "role": "llm",
        "base_url": f"http://127.0.0.1:{instance['port']}/v1",
        "loaded": bool(active and active.get("state") == "ready"),
        "runtime_status": str(active.get("state", "STOPPED")).upper() if active else "STOPPED",
        "model_path": "", "n_parallel": 1,
    }


def list_instances() -> list[dict]:
    items = _read()
    rows = _running()
    return [_present({"alias": alias, **cfg}, running=rows) for alias, cfg in items.items()]


def get_instance(alias: str) -> dict:
    cfg = _read().get(alias)
    if cfg is None:
        raise KeyError(f"Strataモデル {alias} は未登録です")
    return _present({"alias": alias, **cfg}, running=_running())


def save_instance(alias: str, *, family: str, model: str,
                  port: int, context: str = "32k") -> dict:
    if not strata.installed():
        raise StrataGatewayError("Strataの導入が必要です")
    if not _ALIAS.fullmatch(alias) or alias in {"auto", "strata"}:
        raise StrataGatewayError("別名は小文字英数字とハイフン（先頭英字、最大48文字）にしてください")
    if family not in _FAMILIES or model not in _FAMILIES[family]:
        raise StrataGatewayError("このモデル・量子化はStrataの管理対象外です")
    if context not in _CONTEXTS or not (_MIN_PORT <= port <= _MAX_PORT):
        raise StrataGatewayError("コンテキストまたはポートがサポート範囲外です")
    items = _read()
    if alias in items:
        raise StrataGatewayError("同じ別名は既に登録されています")
    if any(item.get("port") == port for item in items.values()):
        raise StrataGatewayError("Strata内でポートが重複しています")
    from app.models_mgmt import local_llm
    if local_llm.find(alias) is not None:
        raise StrataGatewayError("同じ別名を使用する別のLLMがあります")
    if local_llm.port_taken_by_other_runtime(port, "strata"):
        raise StrataGatewayError("別のローカルランタイムがポートを使用しています")
    # Do not auto-start or download weights at registration time.
    items[alias] = {"family": family, "model": model, "port": port,
                    "context": context, "activated": False}
    _write(items)
    return get_instance(alias)


def delete_instance(alias: str) -> dict:
    items = _read()
    if alias not in items:
        raise KeyError(alias)
    if get_instance(alias)["loaded"]:
        raise StrataGatewayError("稼働中のStrataモデルを先に停止してください")
    items.pop(alias)
    _write(items)
    return {"alias": alias, "config_deleted": True, "model_files_deleted": False}


def _start(alias: str, *, explicit: bool) -> tuple[bool, str]:
    cfg = _read().get(alias)
    if not cfg:
        return False, "モデル設定が見つかりません"
    if not explicit and not cfg.get("activated"):
        return False, "初回ダウンロードにはモデル画面からの明示的な起動操作が必要です"
    if get_instance(alias)["loaded"]:
        return True, ""
    # No --restart: another running Strata server is never silently evicted.
    # Saved preset names tie a running endpoint to this registered alias.
    args = [
        "launch", "--family", cfg["family"], "--model", cfg["model"],
        "--context", cfg["context"], "--access", "local", "--port", str(cfg["port"]),
        "--data-dir", str(data_dir().resolve() / "models" / "strata"),
        "--save-preset", "cd-" + alias, "--no-tui", "--yes", "--output", "json",
    ]
    try:
        result = _run(args, timeout=7200 if explicit else 420)
    except StrataGatewayError as exc:
        return False, str(exc)
    if result.returncode:
        return False, (result.stderr or result.stdout)[-700:].strip() or "Strataの起動に失敗しました"
    if explicit:
        items = _read()
        if alias in items:
            items[alias]["activated"] = True
            _write(items)
    return True, ""


def start_instance(alias: str) -> tuple[bool, str]:
    return _start(alias, explicit=True)


def stop_instance(alias: str) -> tuple[bool, str]:
    if alias not in _read():
        return False, "Strataモデルが見つかりません"
    result = _run(["launch", "--stop", "--preset", "cd-" + alias], timeout=240)
    if result.returncode:
        return False, (result.stderr or result.stdout)[-400:].strip() or "停止に失敗しました"
    return True, ""


async def health(alias: str) -> dict:
    cfg = _read().get(alias)
    if not cfg or not strata.installed():
        return {"ok": False, "status_code": None}
    try:
        if not get_instance(alias)["loaded"]:
            return {"ok": False, "status_code": None}
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            res = await client.get(f"http://127.0.0.1:{cfg['port']}/health")
            if res.status_code != 200:
                return {"ok": False, "status_code": res.status_code}
            status = res.json()
            return {"ok": bool(status.get("loaded", True)), "status_code": 200}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"ok": False, "status_code": None}


async def ensure_ready(alias: str, *, timeout_seconds: int = 420) -> bool:
    if (await health(alias))["ok"]:
        return True
    ok, _error = await asyncio.to_thread(_start, alias, explicit=False)
    if not ok:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if (await health(alias))["ok"]:
            return True
        await asyncio.sleep(2)
    return False


def residency_key(instance: dict) -> str:
    return "strata:" + hashlib.sha256(
        (instance.get("alias", "") + ":" + instance.get("model", "")).encode("utf-8")
    ).hexdigest()[:24]


def mark_used_by_base_url(base_url: str) -> str | None:
    # Strata's idle state is managed upstream, do not invent a new policy.
    return None
