"""Explicit, isolated Project Maya-S add-on; never downloads weights on install.

Only the official mw00/project-maya GitHub Release is a source for managed code.
All commands have server-side fixed arguments. The 96.5 GB GGUF is downloaded
ONLY by an independent authenticated prepare-model action.
"""
from __future__ import annotations

import asyncio
import fcntl
from contextlib import contextmanager
from functools import wraps
import json
import os
import platform
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from app.config import data_dir

_REPO = "https://github.com/mw00/project-maya.git"
_RELEASE = "https://api.github.com/repos/mw00/project-maya/releases/latest"
_TAG = re.compile(r"^v[0-9]+[.][0-9]+[.][0-9]+$")
_UNIT = "control-deck-maya-s.service"
_ALIAS = "maya-s"
ALIAS = _ALIAS
# Settings that only change ControlDeck's lifecycle handling, not Maya's config:
# they may be changed while the model runs (as llama.cpp's idle_exclude can).
LIFECYCLE_KEYS = ("on_demand_start", "idle_exclude", "auto_start")
_PORT = 8099
_MODEL = "glm-maya-s"
# Maya rejects prompt + max_tokens + 8 > context (its CTX_SLACK, as strata --serve).
CONTEXT_SLACK = 8
# Model bytes are separate from code and deliberately never removed by uninstall.

class MayaError(RuntimeError):
    pass


def root() -> Path:
    return data_dir().resolve() / "runtimes" / "maya-s"


def source() -> Path:
    return root() / "engine"


def entrypoint() -> Path:
    return source() / "maya.sh"


def models_dir() -> Path:
    return data_dir().resolve() / "models" / "maya-s"


def _marker() -> Path:
    return root() / "managed.json"


def _ensure_alias_port_available() -> None:
    from app.models_mgmt import local_llm
    other = local_llm.alias_taken_by_other_runtime(_ALIAS, "maya-s")
    if other:
        raise MayaError(f"別のランタイム({other})がMaya-Sのモデル名を使用しています")
    other = local_llm.port_taken_by_other_runtime(_PORT, "maya-s")
    if other:
        raise MayaError(f"別のランタイム({other})がMaya-SのAPIポートを使用しています")


def _port_busy(port: int = _PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(0.5)
        return conn.connect_ex(("127.0.0.1", port)) == 0


def _prepared_marker() -> Path:
    return root() / "model-prepared.json"


def _find_config() -> Path | None:
    # Configs of the models registered beside it (instances.json) are never the first model's,
    # even when their name also says "maya-s" (MiniMax's Maya-S: maya-maya-s-hip.json).
    others = {str(row.get("config") or "") for row in _registry()}
    files = [p for p in source().glob("maya-*-hip.json")
             if re.search(r"maya-s(?![a-z0-9])", p.name.lower()) and p.name not in others]
    return files[0] if len(files) == 1 and files[0].is_file() and not files[0].is_symlink() else None


def _config() -> Path | None:
    # A partial/failed download may leave a config file; never treat it as ready.
    if not installed() or not _prepared_marker().is_file() or _prepared_marker().is_symlink():
        return None
    try:
        prepared = json.loads(_prepared_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(prepared, dict) or prepared.get("model") != _MODEL:
        return None
    return _find_config()


@contextmanager
def _operation_lock():
    directory = root()
    if directory.is_symlink():
        raise MayaError("管理ディレクトリがシンボリックリンクです")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "operation.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise MayaError("Maya操作の安全なロックを作成できません") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MayaError("Maya-Sの別の操作が実行中です") from exc
        yield
    finally:
        os.close(descriptor)


def _serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        if os.geteuid() == 0:
            raise MayaError("Maya-Sの管理操作はrootで実行できません")
        with _operation_lock():
            return fn(*args, **kwargs)
    return call


def available() -> bool:
    return sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")


def installed() -> bool:
    return (_marker().is_file() and not _marker().is_symlink()
            and entrypoint().is_file() and not source().is_symlink()
            and not entrypoint().is_symlink())


def _exec(argv: list[str], *, cwd: Path | None = None, timeout: int = 120,
          check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "MAYA_UPDATE_CHECK": "0"}
    # The image encoder's CMake guesses the ROCm root from the first clang it finds
    # (/opt/rocm-*/core-*, which has no hip-lang package) and stops configuring; the
    # engine build already uses /opt/rocm, so point the encoder at the same one.
    rocm = Path("/opt/rocm").resolve()
    if "ROCM_PATH" not in env and (rocm / "lib/cmake/hip-lang/hip-lang-config.cmake").is_file():
        env["ROCM_PATH"] = str(rocm)
    # Long-running setup can print gigabytes of progress over many hours.
    # Never accumulate an unbounded subprocess pipe in ControlDeck memory.
    long_run = timeout > 300
    try:
        result = subprocess.run(
            argv, cwd=cwd or root(), env=env, check=False,
            stdout=subprocess.DEVNULL if long_run else subprocess.PIPE,
            stderr=subprocess.DEVNULL if long_run else subprocess.PIPE,
            text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MayaError("Maya操作を実行できないか、タイムアウトしました") from exc
    if check and result.returncode:
        # Maya itself writes maya-setup.log within the engine folder.
        # Do not include arbitrary upstream output; it may contain secrets.
        raise MayaError(f"Maya操作が失敗しました（exit {result.returncode}）。maya-setup.logを確認してください")
    return result


def _latest_release() -> str:
    try:
        request = urllib.request.Request(
            _RELEASE, headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "ControlDeck-Maya-S"})
        with urllib.request.urlopen(request, timeout=20) as response:
            meta = json.load(response)
    except (OSError, ValueError) as exc:
        raise MayaError("Project MayaのGitHub Releaseを取得できません") from exc
    tag = meta.get("tag_name")
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise MayaError("Project Mayaのリリースタグが不正です")
    if meta.get("draft") or meta.get("prerelease"):
        raise MayaError("プレリリースは導入できません")
    return tag


def _running(alias: str = _ALIAS) -> bool:
    return _exec(["systemctl", "--user", "is-active", _unit(alias)], timeout=15,
                 check=False).stdout.strip() == "active"


def _any_running() -> bool:
    return _running(_ALIAS) or any(_running(row["alias"]) for row in _registry())


def _build_dir() -> Path:
    # Upstream setup compiles the HIP engine here and its config points at it.
    return source() / "build-hip"


def _built(tag: str) -> bool:
    if not (_build_dir() / "maya").is_file():
        return False
    marker = root() / "build-version"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == tag:
        return True
    # Built by the upstream setup during model preparation or update.
    return (_build_dir() / "MAYA-BUILD.json").is_file()


def runtime_status() -> dict:
    state = installed()
    tag = ""
    if state:
        try:
            item = json.loads(_marker().read_text(encoding="utf-8"))
            tag = str(item.get("tag") or "") if isinstance(item, dict) else ""
        except (OSError, ValueError):
            pass
    config = _config() if state else None
    return {
        "installed": state,
        "available": available() or state,
        "version": tag,
        "configured": bool(config),
        "built": _built(tag) if state else False,
        "settings": get_settings(),
        "running": _running() if config else False,
        "entrypoint": str(entrypoint()) if state else "",
        "models_dir": str(models_dir()),
        "error": "" if available() else "Linux x86_64のみ管理導入対応",
        "warning": "Maya-Sは約96.5GB。モデル取得は別操作で明示的に承認するまで実行しません。ROCm 7以降・R9700 HIPが必要です。",
    }


@_serialized
def _install() -> dict:
    if not available() or os.geteuid() == 0:
        raise MayaError("Linux x86_64の非rootユーザーで導入してください")
    if installed():
        raise MayaError("Maya-Sは導入済みです。更新操作を使ってください")
    target = root()
    if target.is_symlink() or source().is_symlink():
        raise MayaError("管理ディレクトリがシンボリックリンクです")
    target.mkdir(parents=True, exist_ok=True)
    if source().exists():
        raise MayaError("既存のMayaディレクトリがあります。自動上書きしません")
    tag = _latest_release()
    with tempfile.TemporaryDirectory(dir=target, prefix="maya-install-") as temporary:
        staged = Path(temporary) / "engine"
        _exec(["git", "-c", "core.hooksPath=/dev/null", "clone", "--depth", "1",
               "--single-branch", "--branch", tag, _REPO, str(staged)], cwd=target, timeout=600)
        if not (staged / "maya.sh").is_file():
            raise MayaError("公式Mayaの起動スクリプトが存在しません")
        # Do not invoke setup at this point; upstream setup may download 96.5 GB.
        staged.rename(source())
    _marker().write_text(json.dumps({"source": _REPO, "tag": tag}), encoding="utf-8")
    return runtime_status()


async def install_stream(job=None) -> dict:
    if job:
        job.set_progress("Project Mayaの正式Releaseコードを取得中。モデルはダウンロードしません", 0, 1)
    return await asyncio.to_thread(_install)


@_serialized
def _update() -> dict:
    if not installed():
        raise MayaError("管理導入したMaya-Sがありません")
    if _any_running():
        raise MayaError("Mayaを停止してから更新してください")
    tag = _latest_release()
    current = runtime_status()["version"]
    if current == tag:
        return runtime_status()
    # Source stays in place so generated local configs are retained. Never reset dirty code.
    if _exec(["git", "status", "--porcelain", "--untracked-files=no"],
             cwd=source()).stdout.strip():
        raise MayaError("Mayaのソースに未コミット変更があります。更新しません")
    previous_commit = _exec(["git", "rev-parse", "HEAD"], cwd=source()).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", previous_commit):
        raise MayaError("更新前のコミットを確認できません")
    _exec(["git", "fetch", "--depth", "1", "origin", "tag", tag], cwd=source(), timeout=600)
    keep_env = _config_env()
    changed = False
    try:
        _exec(["git", "checkout", "--detach", tag], cwd=source(), timeout=60)
        changed = True
        if _config() is not None:
            # Rebuild the managed engine explicitly, not during ControlDeck startup.
            # Upstream --yes never downloads weights unless --download-model is present;
            # it does fetch the 1.14 GB image encoder once (pictures are on).
            gpu = 0
            try:
                gpu = int(json.loads(_prepared_marker().read_text(encoding="utf-8")).get("gpu_index", 0))
            except (ValueError, OSError, AttributeError):
                pass
            _exec(["bash", str(entrypoint()), "--setup", "--backend", "hip",
                   "--gpu", str(gpu), "--model", _MODEL, "--context", "8192",
                   "--yes", "--plain", "--no-start",
                   "--models-dir", str(models_dir()), "--port", str(_PORT),
                   "--host", "127.0.0.1"], cwd=source(), timeout=3*3600)
    except MayaError:
        if changed:
            try:
                _exec(["git", "checkout", "--detach", previous_commit],
                      cwd=source(), timeout=60)
            except MayaError:
                raise MayaError("Maya更新とコード復旧に失敗しました。手動確認が必要です") from None
        raise
    _enable_fit_max_tokens(keep_env)
    _marker().write_text(json.dumps({"source": _REPO, "tag": tag}), encoding="utf-8")
    return runtime_status()


async def update_stream(job=None) -> dict:
    if job:
        job.set_progress("Project Maya正式Releaseへ更新中。モデルは再取得しません", 0, 1)
    return await asyncio.to_thread(_update)


async def available_update() -> dict:
    current = runtime_status()["version"]
    try:
        latest = await asyncio.to_thread(_latest_release)
        def ver(tag: str) -> tuple[int, int, int]:
            return tuple(map(int, tag.lstrip("v").split(".")))
        newer = bool(current and _TAG.fullmatch(current) and ver(latest) > ver(current))
        return {"installed_tag": current, "latest_tag": latest, "update_available": newer,
                "published_at": "", "error": ""}
    except MayaError as exc:
        return {"installed_tag": current, "latest_tag": "",
                "update_available": False, "published_at": "", "error": str(exc)}


def _config_env() -> dict:
    """The engine env of the current config (tuning such as MAYA_GLM_PCIE_SHARE lives there)."""
    config = _find_config()
    try:
        env = json.loads(config.read_text(encoding="utf-8")).get("env") if config else None
    except (OSError, ValueError):
        return {}
    return dict(env) if isinstance(env, dict) else {}


def _enable_fit_max_tokens(keep_env: dict | None = None) -> None:
    """Let Maya clamp an oversized output cap itself (clients size it for llama.cpp).

    `keep_env`: the env before a (re)setup. The setup writes its own env again and drops
    keys it does not know (measured tuning); those come back, the setup's values win.
    """
    config = _find_config()
    if config is None:
        return
    cfg = json.loads(config.read_text(encoding="utf-8"))
    # A (re)setup writes the card's defaults; put back what ControlDeck manages.
    effort = str(get_settings().get("reasoning_effort") or "high")
    env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
    missing = {k: v for k, v in (keep_env or {}).items() if k not in env}
    if cfg.get("fit_max_tokens") is not True or cfg.get("reasoning_effort") != effort or missing:
        cfg["fit_max_tokens"] = True
        cfg["reasoning_effort"] = effort
        if missing:
            cfg["env"] = {**env, **missing}
        config.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


@_serialized
def _prepare_model(*, gpu_index: int = 0) -> dict:
    if not 0 <= gpu_index <= 15:
        raise MayaError("GPU番号が範囲外です")
    if not installed():
        raise MayaError("先にMaya-Sランタイムを導入してください")
    if _any_running():
        raise MayaError("モデル準備の前にMayaを停止してください")
    if _config() is not None:
        raise MayaError("Maya-Sはセットアップ済みです。再設定は手動で行ってください")
    if os.geteuid() == 0:
        raise MayaError("root実行は禁止されています")
    _ensure_alias_port_available()
    models_dir().mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(models_dir()).free < 110 * 1000**3:
        raise MayaError("モデル準備には約110GB以上の空き領域が必要です")
    _exec(["bash", str(entrypoint()), "--backend", "hip", "--gpu", str(gpu_index),
           "--model", _MODEL, "--context", "8192",
           "--yes", "--plain", "--download-model", "--no-start",
           "--models-dir", str(models_dir()), "--port", str(_PORT), "--host", "127.0.0.1"],
          cwd=source(), timeout=8*3600)
    if _find_config() is None:
        raise MayaError("セットアップは終了しましたがHIP設定を確認できません")
    _enable_fit_max_tokens()
    _prepared_marker().write_text(json.dumps({
        "model": _MODEL, "gpu_index": gpu_index, "port": _PORT
    }), encoding="utf-8")
    return runtime_status()


async def prepare_model_stream(job=None, *, gpu_index: int = 0) -> dict:
    if job:
        job.set_progress("約96.5GBのMaya-Sを取得・検証、HIPエンジンを準備中", 0, 1)
    return await asyncio.to_thread(_prepare_model, gpu_index=gpu_index)


# ---- instances: the first model (alias maya-s, port 8099, its settings) and the ones registered
# beside it (instances.json). One Maya model holds the card at a time.

FAMILY_LABEL = {"glm": "GLM-5.3-Flash", "deepseek": "DeepSeek-V4.1-Flash", "minimax": "MiniMax-M3.1-Flash"}
_PORTS = range(8100, 8140)
_CONFIG_NAME = re.compile(r"^maya-[a-z0-9._-]{1,120}\.json$")
_NEW_ALIAS = re.compile(r"^maya-(glm|deepseek|minimax)-[a-z0-9-]{1,40}$")


def _registry_path() -> Path:
    return root() / "instances.json"


def _registry() -> list[dict]:
    path = _registry_path()
    if not path.is_file() or path.is_symlink():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict) and _NEW_ALIAS.fullmatch(str(row.get("alias") or ""))
            and _CONFIG_NAME.fullmatch(str(row.get("config") or ""))] if isinstance(rows, list) else []


def _save_registry(rows: list[dict]) -> None:
    path = _registry_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _unit(alias: str) -> str:
    return _UNIT if alias == _ALIAS else f"control-deck-{alias}.service"


def _record(alias: str) -> dict | None:
    """An instance's identity: alias, port, engine config, labels. None if not ready to run."""
    if alias == _ALIAS:
        config = _config()
        if config is None:
            return None
        return {"alias": _ALIAS, "port": _PORT, "config": config, "family": "glm", "tier": "Maya-S",
                "model_label": "glm-5.3-flash · maya-s", "legacy": True}
    row = next((r for r in _registry() if r["alias"] == alias), None)
    if row is None or not installed():
        return None
    config = source() / row["config"]
    if not config.is_file() or config.is_symlink():
        return None
    return {**row, "config": config, "legacy": False}


def _records() -> list[dict]:
    rows = [_record(_ALIAS)] + [_record(r["alias"]) for r in _registry()]
    return [row for row in rows if row is not None]


def display_name(record: dict) -> str:
    return f"{FAMILY_LABEL.get(str(record.get('family')), str(record.get('family')))} · {record.get('tier')}"


def _apply_vram_margin(record: dict) -> Path:
    """Keep the native config identity and remember the model's original reserve."""
    from app.models_mgmt.runtime_policy import with_margin_arg

    path = Path(record["config"])
    cfg = json.loads(path.read_text(encoding="utf-8"))
    family = record.get("family", "glm")
    flag = {"deepseek": "--reserve-mib", "minimax": "--reserve-gb"}.get(family, "--vram-reserve-mib")
    args = list(cfg.get("args") or [])

    def reserve_value(values: list[str]) -> str | None:
        for index, arg in enumerate(values):
            if arg.startswith(flag + "="):
                return arg.split("=", 1)[1]
            if arg == flag:
                if index + 1 >= len(values):
                    raise ValueError(f"{flag} の値がありません")
                return values[index + 1]
        return None

    original = reserve_value(args)
    managed = cfg.get("control_deck_vram_margin") or {}
    if managed.get("flag") == flag and original == managed.get("applied"):
        # Undo only our previous reserve; keep any other changes from the native UI.
        original = managed.get("original")
        restored: list[str] = []
        index = 0
        while index < len(args):
            if args[index] == flag:
                index += 2
            elif args[index].startswith(flag + "="):
                index += 1
            else:
                restored.append(args[index])
                index += 1
        args = restored + ([flag, original] if original is not None else [])
    cfg["args"] = with_margin_arg(args, flag, gib=family == "minimax")
    cfg["control_deck_vram_margin"] = {"flag": flag, "original": original, "applied": cfg["args"][-1]}
    temp = path.with_suffix(".tmp")
    temp.touch(mode=0o600, exist_ok=True)
    temp.chmod(0o600)
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


@_serialized
def start_instance(alias: str = _ALIAS) -> tuple[bool, str]:
    record = _record(alias)
    if record is None:
        return False, "明示的なMayaモデル準備が必要です"
    if _running(alias):
        return True, ""
    try:
        if record["legacy"]:
            _ensure_alias_port_available()
        # One Maya model fills the card: stop the other one first.
        for other in _records():
            if other["alias"] != alias and _running(other["alias"]):
                _exec(["systemctl", "--user", "stop", _unit(other["alias"])], timeout=600)
        if _port_busy(record["port"]):
            raise MayaError(f"127.0.0.1:{record['port']}が別のサーバーに使用されています")
        launch_config = _apply_vram_margin(record)
        _exec(["systemd-run", "--user", "--collect", "--unit",
               _unit(alias).removesuffix(".service"), "--working-directory", str(source()),
               "bash", str(entrypoint()), "--config", str(launch_config), "--port",
               str(record["port"]), "--host", "127.0.0.1"], cwd=source(), timeout=45)
    except (MayaError, OSError, ValueError) as exc:
        return False, str(exc)
    from app.models_mgmt import large_model_lifecycle
    large_model_lifecycle.mark_used(large_model_lifecycle.MAYA, alias)
    large_model_lifecycle.follow_selection(large_model_lifecycle.MAYA)
    return True, ""


@_serialized
def stop_instance(alias: str | None = None) -> tuple[bool, str]:
    """Stop one Maya model, or (no alias) whichever is running."""
    if alias is not None and alias != _ALIAS and not any(r["alias"] == alias for r in _registry()):
        return False, "Mayaのモデル名が不正です"
    try:
        for name in [alias] if alias is not None else [_ALIAS] + [r["alias"] for r in _registry()]:
            if _running(name):
                _exec(["systemctl", "--user", "stop", _unit(name)], timeout=600)
        return True, ""
    except MayaError as exc:
        return False, str(exc)


async def health(alias: str = _ALIAS) -> dict:
    """Only accept our loopback instance; a launched unit is not a ready model."""
    record = await asyncio.to_thread(_record, alias)
    if record is None or not await asyncio.to_thread(_running, alias):
        return {"ok": False, "status_code": None}
    import httpx
    port = int(record["port"])
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{port}/health")
            if response.status_code != 200:
                return {"ok": False, "status_code": response.status_code}
            # Maya reports unloaded after an idle unload. Do not claim ready.
            state = response.json()
            if not isinstance(state, dict) or state.get("status") != "ok" or state.get("unloaded") is True:
                return {"ok": False, "status_code": 200}
            # A port collision must never make an unrelated server look like our model.
            label = str(state.get("model_label") or "").strip().casefold()
            if record["legacy"]:
                if label != record["model_label"]:
                    return {"ok": False, "status_code": 200}
            elif label and str(record.get("tier") or "").casefold() not in label:
                return {"ok": False, "status_code": 200}
            models = await client.get(f"http://127.0.0.1:{port}/v1/models")
            if models.status_code != 200:
                return {"ok": False, "status_code": models.status_code}
            payload = models.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            loaded = any(
                isinstance(m, dict) and m.get("status", {}).get("value") == "loaded"
                for m in (data if isinstance(data, list) else [])
            )
            return {"ok": loaded, "status_code": 200}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "status_code": None}


async def prompt_room(messages: list, *, port: int = _PORT) -> tuple[int, int] | None:
    """(prompt tokens, context) counted by Maya's own chat template; None if unknown.

    Maya never truncates: prompt + max_tokens over its context is rejected with 400.
    """
    import httpx
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
            response = await client.post(f"http://127.0.0.1:{port}/api/tokens",
                                         json={"messages": messages})
        if response.status_code != 200:
            return None
        counted = response.json()
        return int(counted["tokens"]), int(counted["max_context"]) - CONTEXT_SLACK
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


async def wait_ready(*, timeout_seconds: int = 900, alias: str = _ALIAS) -> bool:
    import time
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if (await health(alias))["ok"]:
            return True
        await asyncio.sleep(3)
    return False


def _config_context(config: Path) -> int:
    try:
        args = json.loads(config.read_text(encoding="utf-8")).get("args") or []
        return int(args[args.index("--max-context") + 1])
    except (OSError, ValueError, IndexError, TypeError):
        return 0


def _config_vision(config: Path | None) -> bool:
    """Maya reads pictures only when its config names an image encoder ("vision")."""
    try:
        return bool(config and isinstance(json.loads(config.read_text(encoding="utf-8")).get("vision"), dict))
    except (OSError, ValueError):
        return False


def get_instance(alias: str) -> dict:
    record = _record(alias)
    if record is None:
        raise KeyError(alias)
    if record["legacy"]:
        return {"alias": _ALIAS, "runtime": "maya-s", "role": "llm",
                "model": _MODEL, "port": _PORT,
                "base_url": f"http://127.0.0.1:{_PORT}/v1",
                "loaded": _running(), "model_path": get_settings()["model_path"],
                "ctx_size": get_settings()["context"],
                "configured": True, "n_parallel": 1, "vision": _vision_configured(),
                "family": "glm", "tier": "Maya-S", "display_name": display_name(record)}
    return {"alias": alias, "runtime": "maya-s", "role": "llm", "model": record["setup_id"],
            "port": int(record["port"]), "base_url": f"http://127.0.0.1:{record['port']}/v1",
            "loaded": _running(alias), "model_path": str(record.get("model_dir") or ""),
            "ctx_size": _config_context(record["config"]), "configured": True, "n_parallel": 1,
            "vision": _config_vision(record["config"]), "family": record["family"], "tier": record["tier"],
            "size_gb": record.get("size_gb"), "display_name": display_name(record)}


def _vision_configured() -> bool:
    return _config_vision(_config())


def list_instances() -> list[dict]:
    return [get_instance(record["alias"]) for record in _records()]


def residency_key(instance: dict) -> str:
    if instance.get("alias", _ALIAS) == _ALIAS:
        return "maya-s:glm-5.3-flash"
    return f"maya-s:{instance.get('family')}:{str(instance.get('tier') or '').lower()}"


def is_running() -> bool:
    return any(_running(record["alias"]) for record in _records())


def running_alias() -> str:
    """The Maya model now running, else the first one (the default target)."""
    for record in _records():
        if _running(record["alias"]):
            return record["alias"]
    return _ALIAS


async def ensure_ready(alias: str, *, timeout_seconds: int = 420, start: bool | None = None) -> bool:
    """Ready for a request. Starts the prepared model when "要求時に自動起動" is on.

    Never downloads or prepares weights: only an already prepared model starts.
    `start` overrides the setting (autostart passes True).
    """
    from app.models_mgmt import large_model_lifecycle

    if await asyncio.to_thread(_record, alias) is None:
        return False
    if (await health(alias))["ok"]:
        large_model_lifecycle.mark_used(large_model_lifecycle.MAYA, alias)
        return True
    allowed = get_settings().get("on_demand_start", True) if start is None else start
    if not allowed:
        return False
    if not await asyncio.to_thread(_running, alias):
        try:
            await large_model_lifecycle.release_for(large_model_lifecycle.MAYA)
        except RuntimeError:
            return False
        try:
            ok, _reason = await asyncio.to_thread(start_instance, alias)
        except MayaError:
            ok = True  # another request holds the operation lock and is starting it
        if not ok:
            return False
    if not await wait_ready(timeout_seconds=timeout_seconds, alias=alias):
        return False
    large_model_lifecycle.mark_used(large_model_lifecycle.MAYA, alias)
    return True


def mark_used_by_base_url(_base_url: str) -> None:
    return None

@_serialized
def uninstall() -> dict:
    if not installed():
        raise MayaError("ControlDeck管理のMaya-Sがありません")
    if _any_running():
        raise MayaError("モデル稼働中は削除できません。停止してください")
    target = root()
    if target.is_symlink() or source().is_symlink() or not source().is_dir():
        raise MayaError("削除先にシンボリックリンクが含まれています")
    # Remove managed code only, preserve models_dir, secrets, global Maya installs.
    shutil.rmtree(source())
    _prepared_marker().unlink(missing_ok=True)
    _marker().unlink(missing_ok=True)
    return runtime_status()


def get_settings() -> dict:
    defaults = {"model_path": "", "context": 8192, "gpu_index": 0,
                "parallel": 1, "ram_headroom_gb": 16, "drafting": "on",
                "on_demand_start": True, "idle_exclude": False, "auto_start": False,
                # Maya's cap on the thinking; the answer starts past it.
                "thinking_budget": 32768,
                # The depth a request gets when it names none. The GLM card's default is max
                # (about 30 min per coding answer on an R9700); high gave the same passing
                # code in a quarter of the time, so it is the default for coding here.
                "reasoning_effort": "high"}
    path = root() / "settings.json"
    if path.is_file() and not path.is_symlink():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    return defaults


@_serialized
def save_settings(settings: dict) -> dict:
    if not installed():
        raise MayaError("先にMaya-Sを導入してください")
    previous = get_settings()
    value = {**previous, **dict(settings)}
    changed = {key for key in value if value.get(key) != previous.get(key)}
    if changed <= set(LIFECYCLE_KEYS) and (changed or _running()):
        # Lifecycle flags only: no Maya config change, fine while running.
        (root() / "settings.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        return runtime_status()
    if _running():
        raise MayaError("Maya-Sを停止してから設定を変更してください")
    if value.get("model_path"):
        from app.files.service import resolve
        path = resolve(value["model_path"])
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise MayaError("既存GGUFの先頭ファイルを指定してください")
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise MayaError("GGUFファイルではありません")
        value["model_path"] = str(path)
    config = _config()
    if config and value.get("model_path") == previous.get("model_path"):
        cfg = json.loads(config.read_text(encoding="utf-8"))
        args = cfg.get("args", [])
        for flag, setting in (("--max-context", "context"), ("--parallel", "parallel")):
            if flag in args:
                args[args.index(flag) + 1] = str(value[setting])
            else:
                args.extend([flag, str(value[setting])])
        cfg["args"] = args
        cfg["fit_max_tokens"] = True
        cfg["gpu"] = [value["gpu_index"]]
        # Maya's own cap on the thinking; past it the answer starts.
        cfg["thinking_budget"] = int(value["thinking_budget"])
        cfg["reasoning_effort"] = str(value["reasoning_effort"])
        cfg.setdefault("env", {})["MAYA_GLM_RAM_HEADROOM_GB"] = str(value["ram_headroom_gb"])
        cfg["env"]["MAYA_GLM_MTP_DECODE"] = "1" if value["drafting"] == "on" else "0"
        config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    elif config:
        _prepared_marker().unlink(missing_ok=True)
    (root() / "settings.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    return runtime_status()


@_serialized
def build_engine() -> dict:
    """Compile code only. Never invoke the model installer or inference engine."""
    if not installed() or _any_running():
        raise MayaError("導入済み・停止中のMaya-Sが必要です")
    rocm = Path("/opt/rocm").resolve()
    compiler = rocm / "llvm/bin/clang++"
    if not compiler.is_file():
        raise MayaError("/opt/rocmのHIPコンパイラーが必要です")
    build = _build_dir()
    commands = [
        ["cmake", "-S", str(source()), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
         "-DSTRATA_ENABLE_HIP=ON", "-DSTRATA_ENABLE_CUDA=OFF", "-DSTRATA_PREFILL_MMQ=ON",
         "-DSTRATA_NATIVE_EXPERTS=ON", "-DSTRATA_BUILD_TESTS=OFF",
         "-DCMAKE_HIP_ARCHITECTURES=gfx1201", f"-DCMAKE_HIP_COMPILER={compiler}",
         f"-DCMAKE_HIP_COMPILER_ROCM_ROOT={rocm}", f"-DCMAKE_PREFIX_PATH={rocm}"],
        ["cmake", "--build", str(build), "--target", "maya", "-j", "1"],
    ]
    with (root() / "build.log").open("w", encoding="utf-8") as log:
        for command in commands:
            try:
                result = subprocess.run(command, cwd=source(), stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=3*3600,
                                        env={**os.environ, "MAYA_UPDATE_CHECK": "0"}, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise MayaError("HIP構築を完了できません。build.logを確認してください") from exc
            if result.returncode:
                raise MayaError("HIP構築に失敗しました。build.logを確認してください")
    (root() / "build-version").write_text(runtime_status()["version"], encoding="utf-8")
    return runtime_status()


@_serialized
def prepare_existing() -> dict:
    """Prepare a user-supplied GGUF without downloading weights or running a benchmark."""
    if not installed() or _any_running():
        raise MayaError("導入済み・停止中のMaya-Sが必要です")
    value = get_settings()
    from app.files.service import resolve
    path = resolve(value["model_path"])
    if not value["model_path"] or not path.is_file() or path.suffix.lower() != ".gguf":
        raise MayaError("詳細設定で既存GGUFを指定してください")
    _ensure_alias_port_available()
    keep_env = _config_env()
    _prepared_marker().unlink(missing_ok=True)
    _exec(["bash", str(entrypoint()), "--setup", "--backend", "hip",
           "--gguf-dir", str(path), "--gpu", str(value["gpu_index"]),
           "--context", str(value["context"]), "--parallel", str(value["parallel"]),
           "--env", f"MAYA_GLM_RAM_HEADROOM_GB={value['ram_headroom_gb']}",
           "--drafting", value["drafting"], "--no-speed-calibration",
           "--yes", "--plain", "--no-start",
           "--models-dir", str(models_dir()), "--port", str(_PORT), "--host", "127.0.0.1"],
          cwd=source(), timeout=8*3600)
    if _find_config() is None:
        raise MayaError("Maya-SのHIP設定を確認できません。他のモデル形式には対応していません")
    _enable_fit_max_tokens(keep_env)
    _prepared_marker().write_text(json.dumps({"model": _MODEL, "gpu_index": value["gpu_index"],
                                            "port": _PORT}), encoding="utf-8")
    return runtime_status()


# ---- registering more Maya models from files already on disk (never downloads)

_PROBE = Path(__file__).with_name("maya_probe.py")
# Every way the setup could reach the network goes to a closed port: a file it would have to
# download makes the setup fail instead (the owner downloads by hand, with the shown command).
_OFFLINE_ENV = {**{key: "http://127.0.0.1:9" for key in (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")},
    "NO_PROXY": "", "no_proxy": "", "HF_HUB_OFFLINE": "1", "PIP_NO_INDEX": "1", "MAYA_UPDATE_CHECK": "0"}


def memory() -> dict:
    """RAM and the largest card's VRAM in GB: what a Maya model's files are compared with."""
    ram = vram = 0.0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                ram = int(line.split()[1]) * 1024 / 1e9
    except (OSError, ValueError, IndexError):
        pass
    for path in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        try:
            vram = max(vram, int(path.read_text(encoding="utf-8").strip()) / 1e9)
        except (OSError, ValueError):
            continue
    return {"ram_gb": round(ram, 1), "vram_gb": round(vram, 1)}


def _alias_for(family: str, tier: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", tier.lower()).strip("-").removeprefix("maya-") or "model"
    return f"maya-{family}-{slug}"


def _folder(folder: str) -> Path:
    from app.files.service import resolve
    path = resolve(folder)
    if not path.is_dir():
        raise MayaError("フォルダを指定してください")
    return path


def _probe(folder: Path | None) -> dict:
    python = source() / ".venv" / "bin" / "python"
    if not installed() or not python.is_file():
        raise MayaError("先にMayaを導入してください")
    try:
        result = subprocess.run([str(python), "-I", str(_PROBE), str(source()), str(folder) if folder else "-"],
                                cwd=source(), env={**os.environ, **_OFFLINE_ENV}, capture_output=True, text=True,
                                timeout=180, check=False, stdin=subprocess.DEVNULL)
        data = json.loads(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        raise MayaError("Mayaの量子化一覧を読めません") from exc
    if not isinstance(data, dict) or not isinstance(data.get("quants"), list):
        raise MayaError("Mayaの量子化一覧を読めません")
    return data


def catalog(folder: str = "") -> dict:
    """Maya's quants, and (with a folder) whether their files are there. Reads only."""
    path = _folder(folder) if folder else None
    data = _probe(path)
    registered = {"glm-maya-s": _ALIAS} if _config() is not None else {}
    registered.update({row["setup_id"]: row["alias"] for row in _registry()})
    rows = []
    for item in data["quants"]:
        files = item.get("files") or []
        missing = [f for f in files if f.get("required") and not f.get("present")]
        item.update(
            alias=_alias_for(item["family"], item["tier"]), registered=registered.get(item["setup_id"], ""),
            missing_gb=round(sum(int(f.get("size") or 0) for f in missing) / 1e9, 1),
            ready=bool(path and files and not missing and not item.get("error") and not item.get("blockers")
                       and not item.get("folder_error") and item.get("published", True)))
        rows.append(item)
    return {"folder": str(path) if path else "", "memory": memory(), "build": data.get("build") or {},
            "version": data.get("version", ""), "quants": rows}


def _free_port() -> int:
    from app.models_mgmt import local_llm
    used = {int(row.get("port") or 0) for row in _registry()}
    for port in _PORTS:
        if port in used or local_llm.port_taken_by_other_runtime(port, "maya-s") or _port_busy(port):
            continue
        return port
    raise MayaError("Mayaに使える空きポートがありません（8100〜8139）")


def _snapshot() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in source().glob("maya-*.json") if p.is_file() and not p.is_symlink()}


@_serialized
def register(setup_id: str, folder: str, *, context: int = 65536, gpu_index: int = 0) -> dict:
    """Set up one Maya quant from files already in `folder`, beside the first model. No download."""
    if _any_running():
        raise MayaError("Mayaのモデルを停止してから登録してください")
    if not 1024 <= int(context) <= 1048576 or not 0 <= int(gpu_index) <= 15:
        raise MayaError("コンテキストかGPU番号が範囲外です")
    item = next((q for q in catalog(folder)["quants"] if q["setup_id"] == setup_id), None)
    if item is None:
        raise MayaError("Mayaの量子化が見つかりません")
    if item["registered"]:
        raise MayaError(f"{display_name(item)} は登録済みです（{item['registered']}）")
    if not item["ready"]:
        why = item.get("folder_error") or "; ".join(item.get("blockers") or []) or item.get("error") or \
            f"ファイルが足りません（あと約{item['missing_gb']}GB）。先に取得してください: {item.get('hf', '')}"
        raise MayaError(why)
    alias = item["alias"]
    from app.models_mgmt import local_llm
    if local_llm.alias_taken_by_other_runtime(alias, "maya-s"):
        raise MayaError(f"モデル名 {alias} は別のランタイムが使っています")
    port = _free_port()
    model_dir = Path(item["model_dir"])
    argv = ["bash", str(entrypoint()), "--setup", "--backend", "hip", "--gpu", str(int(gpu_index)),
            "--context", str(int(context)), "--parallel", "1", "--no-speed-calibration",
            "--yes", "--plain", "--no-start", "--port", str(port), "--host", "127.0.0.1"]
    if item["family"] == "glm":
        argv += ["--gguf-dir", str(model_dir), "--models-dir", str(models_dir())]
        vision = [f for f in item.get("files") or [] if f.get("what") == "vision"]
        if not vision or not all(f.get("present") for f in vision):
            argv.append("--no-vision")
    else:
        # Their vision towers come from other repos: text only until the owner adds them.
        argv += ["--model", setup_id, "--model-dir", str(model_dir), "--models-dir", str(item["models_dir"]),
                 "--no-vision"]
    before = _snapshot()
    owned = {name for name in before if name in {r["config"] for r in _registry()}}
    first = _find_config()
    if first is not None:
        owned.add(first.name)
    log = root() / f"register-{alias}.log"
    try:
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(argv, cwd=source(), env={**os.environ, **_OFFLINE_ENV},
                                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=8 * 3600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MayaError(f"Mayaのセットアップを実行できませんでした（{log.name}）") from exc
    after = _snapshot()
    # The other models' configs are theirs: put back anything the setup rewrote.
    clobbered = [name for name in owned if after.get(name) != before.get(name)]
    for name in clobbered:
        (source() / name).write_bytes(before[name])
    changed = [name for name in after if name not in owned and after[name] != before.get(name)]
    if result.returncode or clobbered or not changed:
        for name in changed:
            if name not in before:
                (source() / name).unlink(missing_ok=True)
        if clobbered:
            raise MayaError("このモデルの設定は既存のMayaモデルと同じ設定ファイルを使うため登録できません")
        raise MayaError(f"Mayaのセットアップに失敗しました（exit {result.returncode}）。{log} を確認してください")
    name = max(changed, key=lambda n: (source() / n).stat().st_mtime)
    config = source() / name
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["fit_max_tokens"] = True
    cfg["port"] = port
    config.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    rows = [r for r in _registry() if r["alias"] != alias]
    rows.append({"alias": alias, "setup_id": setup_id, "family": item["family"], "tier": item["tier"],
                 "config": name, "port": port, "model_dir": str(model_dir), "size_gb": item.get("size_gb"),
                 "created_at": int(__import__("time").time())})
    _save_registry(rows)
    return get_instance(alias)


async def register_stream(job=None, **kwargs) -> dict:
    if job:
        job.set_progress("Mayaのモデルを手元のファイルから準備中（ダウンロードしません）", 0, 1)
    return await asyncio.to_thread(register, **kwargs)


@_serialized
def unregister(alias: str) -> dict:
    """Forget a registered Maya model: its engine config only. The model files stay."""
    if alias == _ALIAS:
        raise MayaError("最初のMayaモデルはここでは削除できません")
    row = next((r for r in _registry() if r["alias"] == alias), None)
    if row is None:
        raise MayaError("登録されたMayaモデルが見つかりません")
    if _running(alias):
        raise MayaError("モデルを停止してから削除してください")
    config = source() / row["config"]
    if config.is_file() and not config.is_symlink():
        config.unlink()
    _save_registry([r for r in _registry() if r["alias"] != alias])
    return {"alias": alias, "removed": True}
