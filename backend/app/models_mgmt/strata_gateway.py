"""Strata model registration and on-demand server control.

Strata is explicitly installed by a settings.manage action. Models are registered
and launched separately; no catalog refresh or process startup downloads weights.
Only loopback endpoints, fixed upstream CLI arguments and managed presets are used.
"""
from __future__ import annotations

import asyncio
import hashlib
from functools import wraps
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import data_dir
from app.models_mgmt import strata

_FAMILIES = {
    "qwen": {"q2_0", "iq2_xs", "iq3_xxs", "iq3_s"},
    # Unsloth's Flash-Next GGUFs: the two sizes Strata recommends for this family.
    "unsloth": {"ud-iq4_xs", "ud-q4_k_xl"},
    "deepseek41": {"ista-das-coder", "ist-das-2.00bpw"},
}
_CONTEXTS = {"8k", "16k", "32k", "64k", "128k"}
_ALIAS = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
_MIN_PORT, _MAX_PORT = 8081, 65535


_operation_lock = threading.RLock()
_status_lock = threading.Lock()
_status_expires = 0.0
_status_rows: list[dict] = []


def _invalidate_status() -> None:
    global _status_expires
    with _status_lock:
        _status_expires = 0.0


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
        command = [str(strata.binary_path()), *args]
        if args and args[0] == "launch" and "--no-tui" in args and "--plan" not in args:
            # Keep model processes in a user unit, independent of the web worker.
            log = strata._root() / ("launch-" + uuid.uuid4().hex + ".log")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.touch(mode=0o600)
            preset = args[args.index("--save-preset") + 1]
            if not preset.startswith("cd-") or not _ALIAS.fullmatch(preset[3:]):
                raise StrataGatewayError("Strata起動設定が不正です")
            unit = "control-deck-strata-" + preset[3:]
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=40, check=False)
            runner = ["systemd-run", "--user", "--quiet", "--collect", "--unit=" + unit,
                      "--property=Type=oneshot", "--property=RemainAfterExit=yes",
                      f"--property=TimeoutStartSec={timeout}", "--property=TimeoutStopSec=30",
                      "--property=UMask=0077", "--property=StandardOutput=append:" + str(log),
                      "--property=StandardError=append:" + str(log)]
            for key in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "STRATA_HOME", "PATH"):
                runner.append(f"--setenv={key}={strata._env()[key]}")
            runner.append("--setenv=PYTHONUNBUFFERED=1")
            result = subprocess.run([*runner, *command], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=timeout + 35, check=False)
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 8192))
                output = handle.read().decode("utf-8", errors="replace")
            return subprocess.CompletedProcess(args, result.returncode, output, result.stderr)
        return subprocess.run(command, env=strata._env(), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StrataGatewayError("Strataコマンドが失敗、またはタイムアウトしました") from exc


def _running() -> list[dict]:
    """Short-lived cache keeps status subprocess calls out of normal hot paths."""
    global _status_expires, _status_rows
    if not strata.installed():
        return []
    now = time.monotonic()
    with _status_lock:
        if now < _status_expires:
            return list(_status_rows)
    rows: list[dict] = []
    try:
        result = _run(["launch", "--status", "--output", "json"], timeout=12)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                try:
                    payload = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("running"), list):
                    rows = [item for item in payload["running"] if isinstance(item, dict)]
                    break
    except StrataGatewayError:
        pass
    with _status_lock:
        _status_rows = rows
        _status_expires = time.monotonic() + 3
    return list(rows)


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
        "model_path": instance.get("model_path", ""), "n_parallel": instance.get("parallel", 1),
        "ctx_size": int(instance.get("context", "32k").removesuffix("k")) * 1024,
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


def _validate_model_path(value: str) -> str:
    from app.files import service as files

    try:
        path = files.resolve(value)
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise StrataGatewayError("既存モデルはGGUFファイルを指定してください")
        with path.open("rb") as handle:
            if handle.read(4) != b"GGUF":
                raise StrataGatewayError("GGUF形式のモデルではありません")
        shard = re.fullmatch(r"(.+)-([0-9]{5})-of-([0-9]{5})\.gguf", path.name)
        if shard:
            if int(shard[2]) != 1:
                raise StrataGatewayError("分割GGUFは最初のファイル（00001）を指定してください")
            total = int(shard[3])
            if not 1 <= total <= 999:
                raise StrataGatewayError("GGUFの分割数が不正です")
            for index in range(1, total + 1):
                part = files.resolve(str(path.with_name(f"{shard[1]}-{index:05d}-of-{total:05d}.gguf")))
                if not part.is_file():
                    raise StrataGatewayError("分割GGUFファイルが不足しています")
    except (OSError, ValueError) as exc:
        raise StrataGatewayError("既存モデルを読み込めません。許可されたパスと分割ファイルを確認してください") from exc
    return str(path)


def _api_ready(port: int) -> bool:
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{port}/health")
            response.raise_for_status()
            status = response.json()
            if not isinstance(status, dict) or status.get("loaded") is not True:
                return False
            response = client.get(f"http://127.0.0.1:{port}/v1/models")
            response.raise_for_status()
            payload = response.json()
            return isinstance(payload, dict) and any(
                isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]
                for row in payload.get("data", [])
            )
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def _exclusive_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not _operation_lock.acquire(blocking=False):
            raise StrataGatewayError("Strataのモデル操作が実行中です")
        try:
            return function(*args, **kwargs)
        finally:
            _operation_lock.release()
    return wrapped


@_exclusive_operation
def save_instance(alias: str, *, family: str, model: str,
                  port: int, context: str = "32k", model_path: str = "",
                  kv: str = "int8", vision: str = "on", parallel: int = 1,
                  drafting: str = "auto", replace: bool = False) -> dict:
    if not strata.installed():
        raise StrataGatewayError("Strataの導入が必要です")
    if not _ALIAS.fullmatch(alias) or alias in {"auto", "strata"}:
        raise StrataGatewayError("別名は小文字英数字とハイフン（先頭英字、最大48文字）にしてください")
    if family not in _FAMILIES or model not in _FAMILIES[family]:
        raise StrataGatewayError("このモデル・量子化はStrataの管理対象外です")
    if context not in _CONTEXTS or not (_MIN_PORT <= port <= _MAX_PORT):
        raise StrataGatewayError("コンテキストまたはポートがサポート範囲外です")
    model_path = _validate_model_path(model_path) if model_path else ""
    if kv not in {"int8", "q4_0", "k8v4", "fp16"} or vision not in {"on", "off", "gpu", "cpu"}:
        raise StrataGatewayError("KV形式または画像設定が不正です")
    if drafting not in {"auto", "mtp", "dflash2", "off"} or not 1 <= parallel <= 8:
        raise StrataGatewayError("投機デコードまたは同時実行数が不正です")
    items = _read()
    if replace:
        if alias not in items:
            raise KeyError(alias)
        if any(row.get("profile") == "launch-cd-" + alias for row in _running()):
            raise StrataGatewayError("設定を変更する前にStrataモデルを停止してください")
    elif alias in items:
        raise StrataGatewayError("同じ別名は既に登録されています")
    if any(item.get("port") == port for key, item in items.items() if key != alias):
        raise StrataGatewayError("Strata内でポートが重複しています")
    from app.models_mgmt import local_llm
    if not replace and local_llm.find(alias) is not None:
        raise StrataGatewayError("同じ別名を使用する別のLLMがあります")
    if local_llm.port_taken_by_other_runtime(port, "strata"):
        raise StrataGatewayError("別のローカルランタイムがポートを使用しています")
    # Do not auto-start or download weights at registration time.
    items[alias] = {"family": family, "model": model, "port": port,
                    "context": context, "activated": False, "model_path": model_path,
                    "kv": kv, "vision": vision, "parallel": parallel, "drafting": drafting}
    _write(items)
    return get_instance(alias)


@_exclusive_operation
def delete_instance(alias: str) -> dict:
    items = _read()
    if alias not in items:
        raise KeyError(alias)
    if any(row.get("profile") == "launch-cd-" + alias for row in _running()):
        raise StrataGatewayError("稼働中のStrataモデルを先に停止してください")
    items.pop(alias)
    _write(items)
    return {"alias": alias, "config_deleted": True, "model_files_deleted": False}


def _start(alias: str, *, explicit: bool) -> tuple[bool, str]:
    if not _operation_lock.acquire(blocking=False):
        return False, "Strataのモデル操作が実行中です"
    try:
        return _start_locked(alias, explicit=explicit)
    finally:
        _operation_lock.release()


def _start_locked(alias: str, *, explicit: bool) -> tuple[bool, str]:
    cfg = _read().get(alias)
    if not cfg:
        return False, "モデル設定が見つかりません"
    if not explicit and not cfg.get("activated"):
        return False, "初回ダウンロードにはモデル画面からの明示的な起動操作が必要です"
    if get_instance(alias)["loaded"] and _api_ready(cfg["port"]):
        if explicit and not cfg.get("activated"):
            items = _read()
            items[alias]["activated"] = True
            _write(items)
        return True, ""
    # No --restart: another running Strata server is never silently evicted.
    # Saved preset names tie a running endpoint to this registered alias.
    args = [
        "launch", "--family", cfg["family"], "--model", cfg["model"],
        "--context", cfg["context"], "--access", "local", "--port", str(cfg["port"]),
        "--data-dir", str(data_dir().resolve() / "models" / "strata"),
        "--save-preset", "cd-" + alias, "--no-tui", "--yes", "--output", "json",
        "--kv", cfg.get("kv", "int8"), "--vision", cfg.get("vision", "on"),
        "--parallel", str(cfg.get("parallel", 1)), "--drafting", cfg.get("drafting", "auto"),
    ]
    if cfg.get("model_path"):
        try:
            args += ["--model-path", _validate_model_path(cfg["model_path"])]
        except StrataGatewayError as exc:
            return False, str(exc)
    try:
        result = _run(args, timeout=7200 if explicit else 420)
    except StrataGatewayError as exc:
        return False, str(exc)
    if result.returncode:
        return False, (result.stderr or result.stdout)[-700:].strip() or "Strataの起動に失敗しました"
    _invalidate_status()
    if not get_instance(alias)["loaded"] or not _api_ready(cfg["port"]):
        return False, "Strataコマンドは終了しましたが、モデルAPIの起動を確認できません。取得のみで終了した可能性があります"
    if explicit:
        items = _read()
        if alias in items:
            items[alias]["activated"] = True
            _write(items)
    return True, ""


def start_instance(alias: str) -> tuple[bool, str]:
    return _start(alias, explicit=True)


@_exclusive_operation
def stop_instance(alias: str) -> tuple[bool, str]:
    if alias not in _read():
        return False, "Strataモデルが見つかりません"
    result = _run(["launch", "--stop", "--preset", "cd-" + alias], timeout=240)
    if result.returncode:
        return False, (result.stderr or result.stdout)[-400:].strip() or "停止に失敗しました"
    subprocess.run(["systemctl", "--user", "stop", "control-deck-strata-" + alias],
                   capture_output=True, timeout=40, check=False)
    _invalidate_status()
    return True, ""


async def health(alias: str) -> dict:
    cfg = _read().get(alias)
    if not cfg or not strata.installed():
        return {"ok": False, "status_code": None}
    try:
        if not (await asyncio.to_thread(get_instance, alias))["loaded"]:
            return {"ok": False, "status_code": None}
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            res = await client.get(f"http://127.0.0.1:{cfg['port']}/health")
            if res.status_code != 200:
                return {"ok": False, "status_code": res.status_code}
            status = res.json()
            return {"ok": status.get("loaded") is True, "status_code": 200}
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
