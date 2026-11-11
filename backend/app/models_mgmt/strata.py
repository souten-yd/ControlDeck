"""Optional, user-space Strata Terminal integration for ControlDeck.

This feature manages only the Strata CLI installed under ControlDeck's data directory.
It does not change the active LLM runtime, download a model, run a server, or
delete the user's model library. Upstream code is executed ONLY after an explicit
authenticated add-on install action; sources are fixed server-side.
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

RELEASE_REPO = "Niko1221/Strata"
INSTALLER_URL = "https://github.com/Niko1221/Strata/releases/latest/download/install.sh"
LATEST_API = "https://api.github.com/repos/Niko1221/Strata/releases/latest"


class StrataError(RuntimeError):
    """Error safe to display in the add-on UI."""


def _root() -> Path:
    return data_dir().resolve() / "runtimes" / "strata"


def _home() -> Path:
    return _root() / "home"


def _binary() -> Path:
    return _home() / ".local" / "bin" / "strata"


def binary_path() -> Path:
    return _binary()


def _marker() -> Path:
    return _root() / "managed.json"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    home = _home()
    env.update({
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "STRATA_HOME": str(home / ".strata"),
        "PATH": str(home / ".local" / "bin") + os.pathsep + env.get("PATH", "/usr/bin:/bin"),
    })
    return env


def available() -> bool:
    return sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")


def installed() -> bool:
    return _marker().is_file() and _binary().is_file() and os.access(_binary(), os.X_OK)


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    if not installed():
        raise StrataError("ControlDeck管理のStrataは導入されていません")
    try:
        return subprocess.run([str(_binary()), *args], env=_env(), capture_output=True,
                              text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StrataError("Strataコマンドが失敗またはタイムアウトしました") from exc


def _version() -> str:
    if not installed():
        return ""
    try:
        result = _run(["--version"], timeout=15)
        if result.returncode == 0:
            return (result.stdout or result.stderr).strip().splitlines()[0][:100]
    except (StrataError, IndexError):
        pass
    return ""


def runtime_status() -> dict:
    supported = available()
    state = installed()
    return {
        "installed": state,
        "version": _version(),
        "available": supported or state,
        "reason": "" if supported else "Strata Terminalの管理導入はLinux x86_64のみ対応です",
        "cli_path": str(_binary()) if state else "",
        "models_dir": str(data_dir().resolve() / "models" / "strata"),
        "warning": "モデルの取得・起動は明示操作が必要です。既存のllama.cpp/Ollamaは変更しません。",
    }


def _download_installer() -> bytes:
    request = urllib.request.Request(INSTALLER_URL, headers={"User-Agent": "ControlDeck-Strata-Addon"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            # Prevent a wrongly published artifact from exhausting disk/RAM.
            payload = response.read(2_000_001)
    except Exception as exc:
        raise StrataError("公式Strataインストーラーを取得できません") from exc
    if len(payload) > 2_000_000 or not payload.startswith((b"#!/", b"# ")):
        raise StrataError("公式インストーラーの形式またはサイズが不正です")
    return payload


def _install() -> dict:
    if not available():
        raise StrataError("Strata Terminalの管理導入はLinux x86_64のみ対応です")
    if installed():
        raise StrataError("Strataは導入済みです。更新ボタンを使用してください")
    root = _root()
    home = _home()
    if root.is_symlink() or home.is_symlink():
        raise StrataError("Strata管理ディレクトリにシンボリックリンクは使用できません")
    home.mkdir(parents=True, exist_ok=True)
    # Upstream may suggest a model path inside HOME; point it at a separate,
    # retained model library so deleting the runtime never deletes user weights.
    models = data_dir().resolve() / "models" / "strata"
    models.mkdir(parents=True, exist_ok=True)
    default_models = home / "Documents" / "Strata" / "Models"
    default_models.parent.mkdir(parents=True, exist_ok=True)
    if not default_models.exists() and not default_models.is_symlink():
        default_models.symlink_to(models, target_is_directory=True)
    payload = _download_installer()
    with tempfile.TemporaryDirectory(prefix="strata-installer-", dir=root) as temp:
        script = Path(temp) / "install.sh"
        script.write_bytes(payload)
        try:
            result = subprocess.run(["/bin/sh", str(script)], env=_env(), cwd=root,
                                    capture_output=True, text=True, timeout=900,
                                    stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StrataError("Strataのインストールに失敗またはタイムアウトしました") from exc
    if result.returncode != 0 or not _binary().is_file():
        detail = (result.stderr or result.stdout)[-500:].strip()
        raise StrataError(f"Strataインストーラーが失敗しました: {detail[:300]}")
    _marker().write_text(json.dumps({"repo": RELEASE_REPO, "managed": True}), encoding="utf-8")
    return runtime_status()


async def install_stream(job=None) -> dict:
    if job:
        job.set_progress("Strata公式Terminalをユーザー領域へ導入中", 0, 1)
    return await asyncio.to_thread(_install)


def _update() -> dict:
    if not installed():
        raise StrataError("管理導入済みStrataがありません")
    result = _run(["update", "--yes"], timeout=1800)
    if result.returncode != 0:
        # Upstream exit 7 means an active model needs an explicit stop; never
        # interrupt existing inference as an implicit side effect of Update.
        if result.returncode == 7:
            raise StrataError("Strataモデルが稼働中です。モデルを停止してから更新してください")
        raise StrataError(f"Strata更新に失敗しました: {(result.stderr or result.stdout)[-400:]}")
    return runtime_status()


async def update_stream(job=None) -> dict:
    if job:
        job.set_progress("Strata更新を確認・適用中", 0, 1)
    return await asyncio.to_thread(_update)


def _release_status() -> dict:
    current = _version()
    state = {"installed_tag": current, "latest_tag": "", "update_available": False,
             "published_at": "", "error": ""}
    try:
        request = urllib.request.Request(LATEST_API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "ControlDeck-Strata-Addon",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            metadata = json.load(response)
        tag = str(metadata.get("tag_name") or "")
        state["latest_tag"] = tag
        state["published_at"] = str(metadata.get("published_at") or "")
        current_versions = re.findall(r"\\d+\\.\\d+\\.\\d+", current)
        latest_versions = re.findall(r"\\d+\\.\\d+\\.\\d+", tag)
        if current_versions and latest_versions:
            state["update_available"] = tuple(map(int, latest_versions[0].split("."))) > tuple(
                map(int, current_versions[0].split(".")))
    except Exception:
        state["error"] = "GitHubのリリース情報を取得できません"
    return state


async def available_update() -> dict:
    return await asyncio.to_thread(_release_status)


def uninstall() -> dict:
    if not installed():
        raise StrataError("ControlDeck管理のStrataがありません")
    result = _run(["launch", "--status", "--output", "json"], timeout=30)
    # Refuse destructive cleanup unless a reliable machine-readable status is available.
    if result.returncode != 0:
        raise StrataError("稼働状態を確認できません。Strataを停止してから削除してください")
    try:
        state = json.loads(result.stdout.splitlines()[0])
    except (ValueError, TypeError) as exc:
        raise StrataError("稼働状態を解釈できません。Strataを停止してから削除してください") from exc
    if not isinstance(state, dict) or not isinstance(state.get("running"), list):
        raise StrataError("稼働状態が不明です。Strataを停止してから削除してください")
    if state["running"]:
        raise StrataError("Strataモデルが稼働中です。停止してから削除してください")
    # Delete only the CLI and managed engine. Never delete downloaded models,
    # shared GGUF libraries, configurations or a Strata installation on PATH.
    for file in (_binary(), _marker()):
        if file.is_file() and not file.is_symlink():
            file.unlink()
    for subdir in (_home() / ".local" / "share" / "strata" / name for name in ("terminal", "components")):
        if subdir.is_dir() and not subdir.is_symlink():
            shutil.rmtree(subdir)
    return runtime_status()
