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
_PORT = 8099
_MODEL = "glm-maya-s"
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


def _port_busy() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        conn.settimeout(0.5)
        return conn.connect_ex(("127.0.0.1", _PORT)) == 0


def _prepared_marker() -> Path:
    return root() / "model-prepared.json"


def _find_config() -> Path | None:
    files = [p for p in source().glob("maya-*-hip.json")
             if re.search(r"maya-s(?![a-z0-9])", p.name.lower())]
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


def _running() -> bool:
    return _exec(["systemctl", "--user", "is-active", _UNIT], timeout=15, check=False).stdout.strip() == "active"


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
        "built": ((source() / "build" / "maya").is_file()
                  and (root() / "build-version").is_file()
                  and (root() / "build-version").read_text().strip() == tag),
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
    if _running():
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
    changed = False
    try:
        _exec(["git", "checkout", "--detach", tag], cwd=source(), timeout=60)
        changed = True
        if _config() is not None:
            # Rebuild the managed engine explicitly, not during ControlDeck startup.
            # Upstream --yes never downloads weights unless --download-model is present.
            gpu = 0
            try:
                gpu = int(json.loads(_prepared_marker().read_text(encoding="utf-8")).get("gpu_index", 0))
            except (ValueError, OSError, AttributeError):
                pass
            _exec(["bash", str(entrypoint()), "--setup", "--backend", "hip",
                   "--gpu", str(gpu), "--model", _MODEL, "--context", "8192",
                   "--no-vision", "--yes", "--plain", "--no-start",
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


@_serialized
def _prepare_model(*, gpu_index: int = 0) -> dict:
    if not 0 <= gpu_index <= 15:
        raise MayaError("GPU番号が範囲外です")
    if not installed():
        raise MayaError("先にMaya-Sランタイムを導入してください")
    if _running():
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
           "--model", _MODEL, "--context", "8192", "--no-vision",
           "--yes", "--plain", "--download-model", "--no-start",
           "--models-dir", str(models_dir()), "--port", str(_PORT), "--host", "127.0.0.1"],
          cwd=source(), timeout=8*3600)
    if _find_config() is None:
        raise MayaError("セットアップは終了しましたがHIP設定を確認できません")
    _prepared_marker().write_text(json.dumps({
        "model": _MODEL, "gpu_index": gpu_index, "port": _PORT
    }), encoding="utf-8")
    return runtime_status()


async def prepare_model_stream(job=None, *, gpu_index: int = 0) -> dict:
    if job:
        job.set_progress("約96.5GBのMaya-Sを取得・検証、HIPエンジンを準備中", 0, 1)
    return await asyncio.to_thread(_prepare_model, gpu_index=gpu_index)


@_serialized
def start_instance(alias: str = _ALIAS) -> tuple[bool, str]:
    if alias != _ALIAS or _config() is None:
        return False, "明示的なMaya-Sモデル準備が必要です"
    if _running():
        return True, ""
    try:
        _ensure_alias_port_available()
        if _port_busy():
            raise MayaError("127.0.0.1:8099が別のサーバーに使用されています")
        _exec(["systemd-run", "--user", "--collect", "--unit",
               _UNIT.removesuffix(".service"), "--working-directory", str(source()),
               "bash", str(entrypoint()), "--config", str(_config()), "--port",
               str(_PORT), "--host", "127.0.0.1"], cwd=source(), timeout=45)
    except MayaError as exc:
        return False, str(exc)
    return True, ""


@_serialized
def stop_instance(alias: str = _ALIAS) -> tuple[bool, str]:
    if alias != _ALIAS:
        return False, "Maya-Sのモデル名が不正です"
    try:
        if _running():
            _exec(["systemctl", "--user", "stop", _UNIT], timeout=600)
        return True, ""
    except MayaError as exc:
        return False, str(exc)



async def health(alias: str = _ALIAS) -> dict:
    """Only accept our loopback instance; a launched unit is not a ready model."""
    if alias != _ALIAS or not await asyncio.to_thread(lambda: _config() is not None and _running()):
        return {"ok": False, "status_code": None}
    import httpx
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{_PORT}/health")
            if response.status_code != 200:
                return {"ok": False, "status_code": response.status_code}
            # Maya reports unloaded after an idle unload. Do not claim ready.
            state = response.json()
            if not isinstance(state, dict) or state.get("status") != "ok" or state.get("unloaded") is True:
                return {"ok": False, "status_code": 200}
            # A port collision must never make an unrelated server look like our model.
            if str(state.get("model_label") or "").strip().casefold() != "glm-5.3-flash · maya-s":
                return {"ok": False, "status_code": 200}
            models = await client.get(f"http://127.0.0.1:{_PORT}/v1/models")
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


async def wait_ready(*, timeout_seconds: int = 900) -> bool:
    import time
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if (await health())["ok"]:
            return True
        await asyncio.sleep(3)
    return False


def get_instance(alias: str) -> dict:
    if alias != _ALIAS or _config() is None:
        raise KeyError(alias)
    return {"alias": _ALIAS, "runtime": "maya-s", "role": "llm",
            "model": _MODEL, "port": _PORT,
            "base_url": f"http://127.0.0.1:{_PORT}/v1",
            "loaded": _running(), "model_path": get_settings()["model_path"],
            "ctx_size": get_settings()["context"],
            "configured": True, "n_parallel": 1}


def list_instances() -> list[dict]:
    return [get_instance(_ALIAS)] if _config() is not None else []


def residency_key(instance: dict) -> str:
    return "maya-s:glm-5.3-flash"


async def ensure_ready(alias: str, *, timeout_seconds: int = 420) -> bool:
    # No autonomous start or model download; startup must be explicit.
    return alias == _ALIAS and (await health(alias))["ok"]


def mark_used_by_base_url(_base_url: str) -> None:
    return None

@_serialized
def uninstall() -> dict:
    if not installed():
        raise MayaError("ControlDeck管理のMaya-Sがありません")
    if _running():
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
                "parallel": 1, "ram_headroom_gb": 16, "drafting": "on"}
    path = root() / "settings.json"
    if path.is_file() and not path.is_symlink():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    return defaults


@_serialized
def save_settings(settings: dict) -> dict:
    if not installed():
        raise MayaError("先にMaya-Sを導入してください")
    if _running():
        raise MayaError("Maya-Sを停止してから設定を変更してください")
    value = dict(settings)
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
    previous = get_settings()
    if config and value.get("model_path") == previous.get("model_path"):
        cfg = json.loads(config.read_text(encoding="utf-8"))
        args = cfg.get("args", [])
        for flag, setting in (("--max-context", "context"), ("--parallel", "parallel")):
            if flag in args:
                args[args.index(flag) + 1] = str(value[setting])
            else:
                args.extend([flag, str(value[setting])])
        cfg["args"] = args
        cfg["gpu"] = [value["gpu_index"]]
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
    if not installed() or _running():
        raise MayaError("導入済み・停止中のMaya-Sが必要です")
    rocm = Path("/opt/rocm").resolve()
    compiler = rocm / "llvm/bin/clang++"
    if not compiler.is_file():
        raise MayaError("/opt/rocmのHIPコンパイラーが必要です")
    build = source() / "build"
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
    if not installed() or _running():
        raise MayaError("導入済み・停止中のMaya-Sが必要です")
    value = get_settings()
    from app.files.service import resolve
    path = resolve(value["model_path"])
    if not value["model_path"] or not path.is_file() or path.suffix.lower() != ".gguf":
        raise MayaError("詳細設定で既存GGUFを指定してください")
    _ensure_alias_port_available()
    _prepared_marker().unlink(missing_ok=True)
    _exec(["bash", str(entrypoint()), "--setup", "--backend", "hip",
           "--gguf-dir", str(path), "--gpu", str(value["gpu_index"]),
           "--context", str(value["context"]), "--parallel", str(value["parallel"]),
           "--env", f"MAYA_GLM_RAM_HEADROOM_GB={value['ram_headroom_gb']}",
           "--drafting", value["drafting"], "--no-speed-calibration",
           "--no-vision", "--yes", "--plain", "--no-start",
           "--models-dir", str(models_dir()), "--port", str(_PORT), "--host", "127.0.0.1"],
          cwd=source(), timeout=8*3600)
    if _find_config() is None:
        raise MayaError("Maya-SのHIP設定を確認できません。他のモデル形式には対応していません")
    _prepared_marker().write_text(json.dumps({"model": _MODEL, "gpu_index": value["gpu_index"],
                                            "port": _PORT}), encoding="utf-8")
    return runtime_status()
