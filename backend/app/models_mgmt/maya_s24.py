"""Explicit, isolated Project Maya-S24 add-on; never downloads weights on install.

Only the official mw00/project-maya GitHub Release is a source for managed code.
All commands have server-side fixed arguments. The 94.7 GB GGUF is downloaded
ONLY by an independent authenticated prepare-model action.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
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
_UNIT = "control-deck-maya-s24.service"
_ALIAS = "maya-s24"
_PORT = 8099
_MODEL = "glm-maya-s24"
# Model bytes are separate from code and deliberately never removed by uninstall.

class MayaError(RuntimeError):
    pass


def root() -> Path:
    return data_dir().resolve() / "runtimes" / "maya-s24"


def source() -> Path:
    return root() / "engine"


def entrypoint() -> Path:
    return source() / "maya.sh"


def models_dir() -> Path:
    return data_dir().resolve() / "models" / "maya-s24"


def _marker() -> Path:
    return root() / "managed.json"


def _config() -> Path | None:
    if not installed():
        return None
    # Upstream HIP setup emits maya-<quant>-hip.json; avoid taking any other quant.
    files = [p for p in source().glob("maya-*-hip.json") if "s24" in p.name.lower()]
    return files[0] if len(files) == 1 and files[0].is_file() and not files[0].is_symlink() else None


def available() -> bool:
    return sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")


def installed() -> bool:
    return (_marker().is_file() and not _marker().is_symlink()
            and entrypoint().is_file() and not source().is_symlink()
            and not entrypoint().is_symlink())


def _exec(argv: list[str], *, cwd: Path | None = None, timeout: int = 120,
          check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "MAYA_UPDATE_CHECK": "0"}
    try:
        result = subprocess.run(argv, cwd=cwd or root(), env=env, check=False,
                                capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MayaError("Maya操作を実行できないか、タイムアウトしました") from exc
    if check and result.returncode:
        # Do not display arbitrary upstream installer output; may contain secrets.
        raise MayaError(f"Maya操作が失敗しました（exit {result.returncode}）")
    return result


def _latest_release() -> str:
    try:
        request = urllib.request.Request(
            _RELEASE, headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "ControlDeck-Maya-S24"})
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
        "running": _running() if config else False,
        "entrypoint": str(entrypoint()) if state else "",
        "models_dir": str(models_dir()),
        "error": "" if available() else "Linux x86_64のみ管理導入対応",
        "warning": "Maya-S24は約94.7GB。モデル取得は別操作で明示的に承認するまで実行しません。ROCm 7以降・R9700 HIPが必要です。",
    }


def _install() -> dict:
    if not available() or os.geteuid() == 0:
        raise MayaError("Linux x86_64の非rootユーザーで導入してください")
    if installed():
        raise MayaError("Maya-S24は導入済みです。更新操作を使ってください")
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
        # Do not invoke setup at this point; upstream setup may download 95 GB.
        staged.rename(source())
    _marker().write_text(json.dumps({"source": _REPO, "tag": tag}), encoding="utf-8")
    return runtime_status()


async def install_stream(job=None) -> dict:
    if job:
        job.set_progress("Project Mayaの正式Releaseコードを取得中。モデルはダウンロードしません", 0, 1)
    return await asyncio.to_thread(_install)


def _update() -> dict:
    if not installed():
        raise MayaError("管理導入したMaya-S24がありません")
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
    _exec(["git", "fetch", "--depth", "1", "origin", "tag", tag], cwd=source(), timeout=600)
    _exec(["git", "checkout", "--detach", tag], cwd=source(), timeout=60)
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


def _prepare_model(*, gpu_index: int = 0) -> dict:
    if not 0 <= gpu_index <= 15:
        raise MayaError("GPU番号が範囲外です")
    if not installed():
        raise MayaError("先にMaya-S24ランタイムを導入してください")
    if _running():
        raise MayaError("モデル準備の前にMayaを停止してください")
    if _config() is not None:
        raise MayaError("Maya-S24はセットアップ済みです。再設定は手動で行ってください")
    if os.geteuid() == 0:
        raise MayaError("root実行は禁止されています")
    models_dir().mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(models_dir()).free < 110 * 1000**3:
        raise MayaError("モデル準備には約110GB以上の空き領域が必要です")
    _exec(["bash", str(entrypoint()), "--backend", "hip", "--gpu", str(gpu_index),
           "--model", _MODEL, "--context", "8192", "--no-vision",
           "--yes", "--plain", "--download-model", "--no-start",
           "--models-dir", str(models_dir()), "--port", str(_PORT), "--host", "127.0.0.1"],
          cwd=source(), timeout=8*3600)
    if _config() is None:
        raise MayaError("セットアップは終了しましたがHIP設定を確認できません")
    return runtime_status()


async def prepare_model_stream(job=None, *, gpu_index: int = 0) -> dict:
    if job:
        job.set_progress("約94.7GBのMaya-S24を取得・検証、HIPエンジンを準備中", 0, 1)
    return await asyncio.to_thread(_prepare_model, gpu_index=gpu_index)


def start_instance(alias: str = _ALIAS) -> tuple[bool, str]:
    if alias != _ALIAS or _config() is None:
        return False, "明示的なMaya-S24モデル準備が必要です"
    if _running():
        return True, ""
    try:
        _exec(["systemd-run", "--user", "--collect", "--unit",
               _UNIT.removesuffix(".service"), "--working-directory", str(source()),
               "bash", str(entrypoint()), "--config", str(_config()), "--port",
               str(_PORT), "--host", "127.0.0.1"], cwd=source(), timeout=45)
    except MayaError as exc:
        return False, str(exc)
    return True, ""


def stop_instance(alias: str = _ALIAS) -> tuple[bool, str]:
    if alias != _ALIAS:
        return False, "Maya-S24のモデル名が不正です"
    try:
        if _running():
            _exec(["systemctl", "--user", "stop", _UNIT], timeout=600)
        return True, ""
    except MayaError as exc:
        return False, str(exc)



async def health(alias: str = _ALIAS) -> dict:
    """Only accept our loopback instance; a launched unit is not a ready model."""
    if alias != _ALIAS or _config() is None or not _running():
        return {"ok": False, "status_code": None}
    import httpx
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.get(f"http://127.0.0.1:{_PORT}/health")
            if response.status_code != 200:
                return {"ok": False, "status_code": response.status_code}
            # Maya reports unloaded after an idle unload. Do not claim ready.
            state = response.json()
            if isinstance(state, dict) and state.get("status") in ("unloaded", "loading", "error"):
                return {"ok": False, "status_code": 200}
            return {"ok": isinstance(state, dict), "status_code": 200}
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
    return {"alias": _ALIAS, "runtime": "maya-s24", "role": "llm",
            "model": _MODEL, "port": _PORT,
            "base_url": f"http://127.0.0.1:{_PORT}/v1",
            "loaded": _running(), "model_path": "",
            "configured": True, "n_parallel": 1}


def list_instances() -> list[dict]:
    return [get_instance(_ALIAS)] if _config() is not None else []


def residency_key(instance: dict) -> str:
    return "maya-s24:glm-5.3-flash"


async def ensure_ready(alias: str, *, timeout_seconds: int = 420) -> bool:
    # No autonomous start or model download; startup must be explicit.
    return alias == _ALIAS and (await health(alias))["ok"]


def mark_used_by_base_url(_base_url: str) -> None:
    return None

def uninstall() -> dict:
    if not installed():
        raise MayaError("ControlDeck管理のMaya-S24がありません")
    if _running():
        raise MayaError("モデル稼働中は削除できません。停止してください")
    target = root()
    if target.is_symlink() or source().is_symlink() or not source().is_dir():
        raise MayaError("削除先にシンボリックリンクが含まれています")
    # Remove managed code only, preserve models_dir, secrets, global Maya installs.
    shutil.rmtree(source())
    _marker().unlink(missing_ok=True)
    return runtime_status()
