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
import math
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
# Strata takes any "<N>k"; past the trained 256K it adds yarn scaling itself.
_CONTEXT = re.compile(r"^([1-9][0-9]{0,3})k$")
_CONTEXT_MIN_K, _CONTEXT_MAX_K = 4, 1024
LIFECYCLE_KEYS = ("idle_exclude", "auto_start")
# Default thinking for requests that name none ("" = the model's own, xhigh) and its cap (0 = none).
# Strata keeps these as its server's shared defaults (POST /settings, persisted by Strata itself):
# `launch --set` would put them into the engine's arguments, which the engine refuses for the budget.
# Applied after every start and live on save, so they can change while the model runs.
REASONING_EFFORTS = ("", "low", "medium", "xhigh")
THINKING_KEYS = ("reasoning_effort", "thinking_budget")


# Strata の `--set KEY=VALUE`（STRATA_* の環境変数かエンジン設定）。ControlDeck の既定値に
# 登録ごとの上書き（cfg["engine_settings"]）を重ねる。値に "off" を渡すと Strata 側の既定に戻る。
# 既定は「速さのために計算を省く近似」を止めるものだけ。2026-11-14 に R9700 で実測:
#  - STRATA_ROUTE_TAIL_SKIP=0: VRAM キャッシュに無いエキスパートを順位次第で飛ばす近似（Strata の既定 7、
#    答えが変わる）を止める。コーディング課題 10 問で正答は同じ 10/10、速度は約 -6%（89.8→84.0 tok/s）。
#  - STRATA_HC_Q8=0: hyper-connection の重みを読み込み時に Q8 へ量子化しない（bf16 のまま）。速度差は誤差内。
# 丸め順が変わるだけの高速化（MMQ・CPU 分担・WMMA、KL 0.002〜0.03）は残す。
DEFAULT_ENGINE_SETTINGS: dict[str, str] = {"STRATA_ROUTE_TAIL_SKIP": "0", "STRATA_HC_Q8": "0"}
_SETTING_KEY = re.compile(r"^(STRATA_[A-Z0-9_]{1,64}|[a-z][a-z0-9_.-]{0,63})$")
_SETTING_VALUE = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")


def engine_settings(cfg: dict) -> dict[str, str]:
    """起動時に --set で渡す設定。形の合わないものは渡さない（引数の注入を防ぐ）。"""
    merged = {**DEFAULT_ENGINE_SETTINGS, **{str(k): str(v) for k, v in (cfg.get("engine_settings") or {}).items()}}
    if cfg.get("kv_resident") is not None:
        merged["kv-resident"] = str(int(cfg["kv_resident"]))
    if cfg.get("ram_keep_gib") is not None:
        merged["STRATA_RAM_KEEP_GIB"] = format(float(cfg["ram_keep_gib"]), "g")
    return {k: v for k, v in merged.items() if _SETTING_KEY.fullmatch(k) and _SETTING_VALUE.fullmatch(v)}


def launch_engine_settings(cfg: dict) -> dict[str, str]:
    from app.models_mgmt.runtime_policy import vram_margin_mib

    settings = engine_settings(cfg)
    key = "reserve-mib" if cfg.get("family") == "deepseek41" else "vram-reserve-mib"
    try:
        reserve = max(vram_margin_mib(), math.ceil(float(settings.get(key, "0"))))
    except (ValueError, OverflowError):
        reserve = vram_margin_mib()
    settings[key] = str(reserve)
    return settings


def valid_context(value: str) -> bool:
    match = _CONTEXT.fullmatch(str(value))
    return bool(match) and _CONTEXT_MIN_K <= int(match[1]) <= _CONTEXT_MAX_K
_ALIAS = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
_MIN_PORT, _MAX_PORT = 8081, 65535
_UNSET = object()


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
                  drafting: str = "auto", idle_exclude: bool = False,
                  auto_start: bool = False, reasoning_effort: str = "", thinking_budget: int = 0,
                  kv_resident: Any = _UNSET, ram_keep_gib: Any = _UNSET,
                  replace: bool = False) -> dict:
    if not strata.installed():
        raise StrataGatewayError("Strataの導入が必要です")
    if not _ALIAS.fullmatch(alias) or alias in {"auto", "strata"}:
        raise StrataGatewayError("別名は小文字英数字とハイフン（先頭英字、最大48文字）にしてください")
    if family not in _FAMILIES or model not in _FAMILIES[family]:
        raise StrataGatewayError("このモデル・量子化はStrataの管理対象外です")
    if not valid_context(context) or not (_MIN_PORT <= port <= _MAX_PORT):
        raise StrataGatewayError("コンテキストまたはポートがサポート範囲外です")
    model_path = _validate_model_path(model_path) if model_path else ""
    if kv not in {"int8", "q4_0", "k8v4", "fp16"} or vision not in {"on", "off", "gpu", "cpu"}:
        raise StrataGatewayError("KV形式または画像設定が不正です")
    if drafting not in {"auto", "mtp", "dflash2", "off"} or not 1 <= parallel <= 8:
        raise StrataGatewayError("投機デコードまたは同時実行数が不正です")
    if reasoning_effort not in REASONING_EFFORTS or not 0 <= int(thinking_budget) <= 262144:
        raise StrataGatewayError("思考の深さまたは思考の上限が不正です")
    items = _read()
    previous_config = items.get(alias, {}) if replace else {}
    kv_supplied, ram_supplied = kv_resident is not _UNSET, ram_keep_gib is not _UNSET
    if not kv_supplied:
        kv_resident = previous_config.get("kv_resident")
    if not ram_supplied:
        ram_keep_gib = previous_config.get("ram_keep_gib")
    if kv_resident is not None and (not isinstance(kv_resident, int) or not 4096 <= kv_resident <= 1048576
                                    or kv_resident % 1024):
        raise StrataGatewayError("KVのVRAM保持長は4K〜1024Kの範囲で、1024トークン単位にしてください")
    if ram_keep_gib is not None and (not isinstance(ram_keep_gib, (int, float)) or not math.isfinite(ram_keep_gib) or not 0 <= ram_keep_gib <= 256):
        raise StrataGatewayError("RAM余裕は0〜256GiBにしてください")
    if family == "deepseek41" and (kv_resident is not None or ram_keep_gib is not None):
        raise StrataGatewayError("KVのVRAM保持長とRAM余裕の指定はQwen系モデルで利用できます")
    wanted = {"family": family, "model": model, "port": port, "context": context,
              "model_path": model_path, "kv": kv, "vision": vision, "parallel": parallel,
              "drafting": drafting, "idle_exclude": bool(idle_exclude), "auto_start": bool(auto_start),
              "reasoning_effort": reasoning_effort, "thinking_budget": int(thinking_budget),
              "kv_resident": kv_resident, "ram_keep_gib": ram_keep_gib}
    if replace and alias in items:
        current = items[alias]
        changed = {key for key, value in wanted.items() if current.get(key) != value}
        legacy = dict(current.get("engine_settings") or {})
        desired_legacy = dict(legacy)
        if kv_supplied:
            desired_legacy.pop("kv-resident", None)
        if ram_supplied:
            for key in ("STRATA_RAM_KEEP_GIB", "ram-keep-gib"):
                desired_legacy.pop(key, None)
        desired_settings = engine_settings({**wanted, "engine_settings": desired_legacy})
        current_settings = engine_settings(current)
        for field, setting in (("kv_resident", "kv-resident"), ("ram_keep_gib", "STRATA_RAM_KEEP_GIB")):
            if desired_settings.get(setting) == current_settings.get(setting):
                changed.discard(field)
            else:
                changed.add(field)
        if changed <= set(LIFECYCLE_KEYS) | set(THINKING_KEYS):
            # Lifecycle and thinking defaults only: launch settings stay, fine while running.
            items[alias] = {**current, **{key: wanted[key] for key in (*LIFECYCLE_KEYS, *THINKING_KEYS)}}
            _write(items)
            if changed & set(THINKING_KEYS) and get_instance(alias)["loaded"]:
                apply_thinking(alias)
            return get_instance(alias)
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
    # 「準備済み（activated）」は重みを用意したかどうか。系列・量子化・GGUF が同じなら
    # コンテキスト長などを変えても重みはそのまま使えるので、準備済みを保つ。
    # 落とすと、要求時の自動起動と auto の候補から外れてしまう。
    previous = items.get(alias) if replace else None
    same_weights = bool(previous) and all(
        previous.get(key) == wanted[key] for key in ("family", "model", "model_path"))
    items[alias] = {**wanted, "activated": bool(same_weights and previous.get("activated"))}
    if previous and previous.get("engine_settings"):
        items[alias]["engine_settings"] = dict(previous["engine_settings"])
        if kv_supplied:
            items[alias]["engine_settings"].pop("kv-resident", None)
        if ram_supplied:
            for key in ("STRATA_RAM_KEEP_GIB", "ram-keep-gib"):
                items[alias]["engine_settings"].pop(key, None)
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
    for key, value in launch_engine_settings(cfg).items():
        args += ["--set", f"{key}={value}"]
    # Thinking defaults go through POST /settings after the start (apply_thinking); "off" clears
    # any value an earlier launch saved into the preset's engine arguments.
    args += ["--set", "reasoning-effort=off", "--set", "reasoning-budget-tokens=off"]
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
    apply_thinking(alias)
    from app.models_mgmt import large_model_lifecycle
    large_model_lifecycle.mark_used(large_model_lifecycle.STRATA, alias)
    large_model_lifecycle.follow_selection(large_model_lifecycle.STRATA)
    return True, ""


def thinking_defaults(cfg: dict) -> dict | None:
    """Strata's shared defaults for this registration, or None to clear them."""
    defaults: dict = {}
    effort = str(cfg.get("reasoning_effort") or "")
    if effort:
        # Strata's /settings names the deepest level "high" (the template's xhigh).
        defaults["reasoning_effort"] = "high" if effort == "xhigh" else effort
    budget = int(cfg.get("thinking_budget") or 0)
    if budget > 0:
        defaults["reasoning_budget_tokens"] = budget
    return defaults or None


def apply_thinking(alias: str) -> bool:
    """Send the thinking defaults to the running server. Never raises (a start must not fail on it)."""
    cfg = _read().get(alias)
    if not cfg:
        return False
    try:
        with httpx.Client(timeout=10, trust_env=False) as client:
            res = client.post(f"http://127.0.0.1:{cfg['port']}/settings",
                              json={"defaults": thinking_defaults(cfg)})
            return res.status_code == 200
    except httpx.HTTPError:
        return False


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
    from app.models_mgmt import large_model_lifecycle

    if (await health(alias))["ok"]:
        large_model_lifecycle.mark_used(large_model_lifecycle.STRATA, alias)
        return True
    cfg = _read().get(alias) or {}
    if cfg.get("activated") and not (await asyncio.to_thread(get_instance, alias))["loaded"]:
        # Maya-S and Strata cannot both be resident; the requested model wins.
        try:
            await large_model_lifecycle.release_for(large_model_lifecycle.STRATA)
        except RuntimeError:
            return False
    ok, _error = await asyncio.to_thread(_start, alias, explicit=False)
    if not ok:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if (await health(alias))["ok"]:
            large_model_lifecycle.mark_used(large_model_lifecycle.STRATA, alias)
            return True
        await asyncio.sleep(2)
    return False


def residency_key(instance: dict) -> str:
    return "strata:" + hashlib.sha256(
        (instance.get("alias", "") + ":" + instance.get("model", "")).encode("utf-8")
    ).hexdigest()[:24]


def mark_used_by_base_url(base_url: str) -> str | None:
    from urllib.parse import urlsplit
    from app.models_mgmt import large_model_lifecycle

    try:
        port = urlsplit(base_url).port
    except ValueError:
        return None
    alias = next((key for key, item in _read().items() if item.get("port") == port), None)
    if alias:
        large_model_lifecycle.mark_used(large_model_lifecycle.STRATA, alias)
    return alias
