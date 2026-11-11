"""Opt-in Strata Terminal runtime, isolated from ControlDeck and existing LLMs.

Only the upstream Niko1221/Strata release asset named install.sh is accepted.
GitHub's SHA-256 asset digest must exist and match before executing it.  The
upstream installer executes as the unprivileged ControlDeck account, with a
private HOME. No model is downloaded or launched during add-on installation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import shlex
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from app.config import data_dir

_RELEASE_URL = "https://api.github.com/repos/Niko1221/Strata/releases/latest"
_ASSET = "install.sh"
_TAG_RE = re.compile(r"^v?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class StrataError(RuntimeError):
    pass


def root() -> Path:
    return (data_dir() / "runtimes" / "strata").resolve()


def _home() -> Path:
    return root() / "home"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    home = _home()
    env["HOME"] = str(home)
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["STRATA_HOME"] = str(home / ".strata")
    env["PATH"] = f"{home / '.local' / 'bin'}:{env.get('PATH', '')}"
    return env


def server_path() -> Path:
    """Used by feature registry; Strata is a CLI, not a llama-server binary."""
    return _home() / ".local" / "bin" / "strata"


def launch_script() -> Path:
    return root() / "strata-managed"


def _state_path() -> Path:
    return root() / "installed.json"


def is_installed() -> bool:
    return server_path().is_file() and os.access(server_path(), os.X_OK)


def _state() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def runtime_status() -> dict:
    installed = is_installed()
    linux = platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")
    return {
        "installed": installed,
        "managed": installed,
        "version": str(_state().get("tag") or "") if installed else "",
        "healthy": installed,
        "available": linux or installed,
        "error": "" if linux or installed else "StrataアドオンはLinux x86_64のみに対応しています",
        "detail": {
            "runtime": "strata",
            "launch_command": str(launch_script()),
            "warning": "導入後はControlDeckのTerminalから strata-managed launch を実行し、モデルを選択してください。モデルの取得や起動は自動では行いません。",
        },
    }


def _release() -> tuple[str, bytes]:
    req = urllib.request.Request(_RELEASE_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ControlDeck-Strata-Installer",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            metadata = json.loads(response.read(2_000_000))
    except Exception as exc:
        raise StrataError("Strata公式リリース情報を取得できません") from exc
    tag = str(metadata.get("tag_name") or "")
    if not _TAG_RE.fullmatch(tag):
        raise StrataError("Strataリリースタグが不正です")
    assets = [a for a in metadata.get("assets", []) if a.get("name") == _ASSET]
    if len(assets) != 1:
        raise StrataError("Strata公式install.shがリリースに見つかりません")
    asset = assets[0]
    digest = str(asset.get("digest") or "")
    url = str(asset.get("browser_download_url") or "")
    expected_url = f"https://github.com/Niko1221/Strata/releases/download/{tag}/{_ASSET}"
    if url != expected_url or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise StrataError("検証可能な公式install.shではありません（SHA-256 digest必須）")
    if not 1 <= int(asset.get("size") or 0) <= 2_000_000:
        raise StrataError("install.shの容量が想定範囲外です")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "ControlDeck-Strata-Installer"}), timeout=90,
        ) as response:
            script = response.read(2_000_001)
    except Exception as exc:
        raise StrataError("Strataインストーラの取得に失敗しました") from exc
    if len(script) > 2_000_000 or hashlib.sha256(script).hexdigest() != digest[7:].lower():
        raise StrataError("StrataインストーラのSHA-256検証に失敗しました")
    return tag, script


def _wrapper() -> None:
    script = launch_script()
    home = _home()
    data = (data_dir() / "models" / "strata").resolve()
    # Models remain outside the removable runtime area.
    data.mkdir(parents=True, exist_ok=True)
    source = (
        "#!/bin/sh\n"
        f"export HOME={shlex.quote(str(home))}\n"
        f"export XDG_DATA_HOME={shlex.quote(str(home / '.local' / 'share'))}\n"
        f"export STRATA_HOME={shlex.quote(str(home / '.strata'))}\n"
        f"export PATH={shlex.quote(str(home / '.local' / 'bin'))}:$PATH\n"
        f"exec {shlex.quote(str(server_path()))} \"$@\"\n"
    )
    script.write_text(source, encoding="utf-8")
    script.chmod(0o700)


def _install_sync() -> dict:
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise StrataError("Linux x86_64以外にはインストールできません")
    if not shutil.which("sh"):
        raise StrataError("シェルがありません")
    tag, script = _release()
    directory = root()
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    previous = str(_state().get("tag") or "")
    with tempfile.TemporaryDirectory(prefix="strata-install-", dir=directory) as tmp:
        installer = Path(tmp) / "install.sh"
        installer.write_bytes(script)
        # No shell pipeline, sudo, user-provided command, or environment override.
        try:
            result = subprocess.run(
                ["sh", str(installer)], cwd=str(home), env=_env(),
                capture_output=True, text=True, timeout=900, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StrataError("Strata導入がタイムアウトしました") from exc
    if result.returncode != 0 or not is_installed():
        # Installer output may contain tokens/keys: do not expose it to clients.
        raise StrataError("Strata公式インストーラが失敗しました。ホストのControlDeckログを確認してください")
    _wrapper()
    state = {"tag": tag, "source": "Niko1221/Strata"}
    temporary = _state_path().with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temporary, _state_path())
    return {"runtime": "strata", "version": tag, "previous_version": previous}


async def install(job=None, *, options: dict | None = None) -> dict:
    if options:
        raise StrataError("Strataの導入オプションは受け付けません")
    return await asyncio.to_thread(_install_sync)


def _update_sync() -> dict:
    if not is_installed():
        raise StrataError("Strataは未導入です")
    previous = str(_state().get("tag") or "")
    # Upstream signed feed verification/engine rollback live inside strata update.
    # --yes does NOT stop a running model. No --stop-model / --start-again.
    try:
        cp = subprocess.run(
            [str(server_path()), "update", "--yes"], env=_env(), cwd=str(_home()),
            capture_output=True, text=True, timeout=1200, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StrataError("Strata公式updaterを実行できませんでした") from exc
    if cp.returncode != 0:
        raise StrataError("Strataの更新に失敗しました。モデルが起動中の場合は停止して再実行してください")
    tag, _ = _release()
    state = {"tag": tag, "source": "Niko1221/Strata"}
    temp = _state_path().with_suffix(".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temp, _state_path())
    return {"runtime": "strata", "version": tag, "previous_version": previous}


async def update(job=None) -> dict:
    return await asyncio.to_thread(_update_sync)


async def available_update() -> dict:
    try:
        tag, _ = await asyncio.to_thread(_release)
        previous = str(_state().get("tag") or "")
        return {"installed_tag": previous, "latest_tag": tag,
                "update_available": previous != tag, "error": ""}
    except StrataError as exc:
        return {"installed_tag": str(_state().get("tag") or ""),
                "latest_tag": "", "update_available": False, "error": str(exc)}


def uninstall() -> dict:
    if not is_installed():
        return {"removed": False}
    # Refuse destructive uninstall when a model is still serving. Stop explicitly
    # from the ControlDeck Terminal before deleting the runtime.
    check = subprocess.run(
        [str(server_path()), "launch", "--status", "--output", "json"],
        env=_env(), capture_output=True, text=True, timeout=30, check=False,
    )
    if check.returncode != 0:
        raise StrataError("稼働確認に失敗しました。Strataを停止してから削除してください")
    try:
        status = json.loads(check.stdout)
    except ValueError as exc:
        raise StrataError("Strata状態を確認できません。モデル停止後に再実行してください") from exc
    if isinstance(status, dict) and (status.get("running") or status.get("serving")):
        raise StrataError("モデルを停止してからStrataを削除してください")
    # Only ControlDeck's isolated CLI / engine components. Models, presets,
    # chats, other runtimes and all user-owned external installations survive.
    for path in (server_path(), _home() / ".local" / "share" / "strata" / "terminal",
                 _home() / ".local" / "share" / "strata" / "components",
                 launch_script(), _state_path()):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
    return {"removed": True}
