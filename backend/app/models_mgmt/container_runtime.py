"""GPU runtimes that run as one podman container per model in a systemd user unit.

Radiance and Sly-Radiance share everything that does not depend on the engine: where podman is,
whether an image exists, pulling it, the podman arguments that hand the GPU to a container, the
unit that supervises it, the log, the lock that serialises install/update/uninstall, and the
health / readiness checks. Each runtime module keeps what is its own (registrations, launch
arguments, release source) and passes its error class and label so messages stay specific.
Nothing goes through a shell: every command is an argument list.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Callable

import httpx

# Runtimes whose models are containers holding the whole card (one at a time, like Strata / Maya).
CONTAINER_RUNTIMES = ("radiance", "sly-radiance")


def podman() -> str | None:
    return shutil.which("podman")


def gpu_available() -> bool:
    return (sys.platform == "linux" and platform.machine().lower() in ("x86_64", "amd64")
            and podman() is not None and Path("/dev/kfd").exists())


def image_exists(ref: str) -> bool:
    binary = podman()
    if binary is None:
        return False
    try:
        return subprocess.run([binary, "image", "exists", ref], capture_output=True,
                              timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_checked(argv: list[str], error: type[RuntimeError], label: str, *, timeout: int = 120):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise error(f"{label}操作を実行できないか、タイムアウトしました") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
        raise error(f"{label}操作が失敗しました（exit {result.returncode}）: {detail[0][:200]}")
    return result


def pull(ref: str, error: type[RuntimeError], label: str) -> str:
    """Pull an image and return its digest."""
    binary = podman()
    if binary is None:
        raise error("podman が必要です")
    # No --quiet: podman 4.9 panics with it when the image is already present.
    run_checked([binary, "pull", ref], error, label, timeout=3600)
    return run_checked([binary, "image", "inspect", ref, "--format", "{{.Digest}}"],
                       error, label).stdout.strip()


def remove_image(ref: str) -> None:
    subprocess.run([podman() or "podman", "rmi", ref], capture_output=True, timeout=120, check=False)


def read_marker(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_marker(path: Path, image: str, tag: str, digest: str) -> None:
    path.write_text(json.dumps({"image": image, "tag": tag, "digest": digest}), encoding="utf-8")


def version_key(pattern: re.Pattern[str], tag: str) -> tuple[int, int, int]:
    match = pattern.fullmatch(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else (-1, -1, -1)


@contextmanager
def operation_lock(directory: Path, error: type[RuntimeError], label: str):
    if directory.is_symlink():
        raise error("管理ディレクトリがシンボリックリンクです")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory / "operation.lock", flags, 0o600)
    except OSError as exc:
        raise error(f"{label}操作のロックを作成できません") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise error(f"{label}の別の操作が実行中です") from exc
        yield
    finally:
        os.close(descriptor)


def serialized(error: type[RuntimeError], label: str, directory: Callable[[], Path]):
    """Decorator: one install/update/uninstall at a time, never as root. `directory` is looked up per call."""
    def decorate(fn):
        @wraps(fn)
        def call(*args, **kwargs):
            if os.geteuid() == 0:
                raise error(f"{label}の管理操作はrootで実行できません")
            with operation_lock(directory(), error, label):
                return fn(*args, **kwargs)
        return call
    return decorate


def unit_name(prefix: str, alias: str) -> str:
    return f"control-deck-{prefix}-" + re.sub(r"[^a-z0-9-]", "-", alias)


def container_name(prefix: str, alias: str) -> str:
    return f"cd-{prefix}-" + re.sub(r"[^a-z0-9-]", "-", alias)


def unit_running(unit: str) -> bool:
    try:
        result = subprocess.run(["systemctl", "--user", "is-active", unit],
                                capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in ("active", "activating")


def gpu_run_args(binary: str, name: str, mounts: list[str], image: str, tail: list[str], *,
                 env: dict[str, str] | None = None, ipc_host: bool = False) -> list[str]:
    """`podman run` with the GPU handed over, on the host network (the caller binds 127.0.0.1).

    ipc_host: vLLM's workers share memory through /dev/shm, which its README asks to be the host's.
    """
    argv = [binary, "run", "--rm", "--replace", "--name", name, "--network", "host", "--init",
            "--ulimit", "host", "--device", "/dev/kfd", "--device", "/dev/dri",
            "--group-add", "keep-groups", "--security-opt", "seccomp=unconfined",
            "--stop-timeout", "30"]
    if ipc_host:
        argv.append("--ipc=host")
    for source in mounts:
        argv += ["-v", source]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    return [*argv, image, *tail]


def start_unit(unit: str, log: Path, argv: list[str], label: str) -> tuple[bool, str]:
    log.parent.mkdir(parents=True, exist_ok=True)
    runner = ["systemd-run", "--user", "--quiet", "--collect", "--unit=" + unit,
              "--property=UMask=0077", "--property=TimeoutStopSec=60",
              "--property=StandardOutput=append:" + str(log),
              "--property=StandardError=append:" + str(log)]
    try:
        result = subprocess.run([*runner, *argv], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=40, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, f"{label}を起動できませんでした"
    if result.returncode:
        return False, (result.stderr or result.stdout)[-400:].strip() or f"{label}を起動できませんでした"
    return True, ""


def stop_unit(unit: str, container: str, label: str) -> tuple[bool, str]:
    try:
        if podman():
            subprocess.run([podman(), "stop", "--ignore", "-t", "30", container],
                           capture_output=True, timeout=60, check=False)
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True,
                       timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, f"{label}モデルを停止できませんでした"
    return (False, "停止を確認できません") if unit_running(unit) else (True, "")


async def http_health(port: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            res = await client.get(f"http://127.0.0.1:{port}/v1/models")
            if res.status_code != 200:
                return {"ok": False, "status_code": res.status_code}
            data = res.json().get("data")
            return {"ok": isinstance(data, list) and bool(data), "status_code": 200}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {"ok": False, "status_code": None}


async def wait_ready(alias: str, health: Callable, running: Callable[[str], bool], *,
                     timeout_seconds: int = 900) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if (await health(alias))["ok"]:
            return True
        if not await asyncio.to_thread(running, alias):
            return False  # the unit exited: a startup error, the log says why
        await asyncio.sleep(2)
    return False


async def ensure_ready(runtime: str, alias: str, *, config: Callable[[str], dict | None],
                       health: Callable, running: Callable[[str], bool], start: Callable,
                       timeout_seconds: int = 900, start_allowed: bool | None = None) -> bool:
    """Ready for a request; starts a registered model when "要求時に自動起動" is on."""
    from app.models_mgmt import large_model_lifecycle

    if (await health(alias))["ok"]:
        large_model_lifecycle.mark_used(runtime, alias)
        return True
    cfg = config(alias)
    if not cfg:
        return False
    allowed = cfg.get("on_demand_start", True) if start_allowed is None else start_allowed
    if not allowed:
        return False
    if not await asyncio.to_thread(running, alias):
        try:
            await large_model_lifecycle.release_for(runtime)
        except RuntimeError:
            return False
        ok, _reason = await asyncio.to_thread(start, alias)
        if not ok:
            return False
    if not await wait_ready(alias, health, running, timeout_seconds=timeout_seconds):
        return False
    large_model_lifecycle.mark_used(runtime, alias)
    return True


def residency_key(runtime: str, instance: dict) -> str:
    return runtime + ":" + hashlib.sha256(
        (str(instance.get("alias", "")) + ":" + str(instance.get("model_path", ""))).encode("utf-8")
    ).hexdigest()[:24]


def log_tail(path: Path, lines: int = 60) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 32768))
            return "\n".join(handle.read().decode("utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def register(runtime: str, alias: str, wanted: dict, *, replace: bool, read: Callable[[], dict],
             write: Callable[[dict], None], running: Callable[[str], bool],
             error: type[RuntimeError], label: str, lifecycle_keys: tuple[str, ...]) -> None:
    """Add or replace one registration (the caller holds its registry lock).

    A replace that changes only the lifecycle flags is allowed while the model runs; any other
    change needs it stopped. Aliases and ports stay unique across every local runtime.
    """
    from app.models_mgmt import local_llm

    port = int(wanted["port"])
    items = read()
    if replace and alias in items:
        changed = {key for key, value in wanted.items() if items[alias].get(key) != value}
        if changed <= set(lifecycle_keys):
            items[alias] = {**items[alias], **{key: wanted[key] for key in lifecycle_keys}}
            write(items)
            return
        if running(alias):
            raise error(f"設定を変更する前に{label}モデルを停止してください")
    elif replace:
        raise KeyError(alias)
    elif alias in items:
        raise error("同じ別名は既に登録されています")
    if any(item.get("port") == port for key, item in items.items() if key != alias):
        raise error(f"{label}内でポートが重複しています")
    if not replace and local_llm.find(alias) is not None:
        raise error("同じ別名を使用する別のLLMがあります")
    if local_llm.port_taken_by_other_runtime(port, runtime):
        raise error("別のローカルランタイムがポートを使用しています")
    items[alias] = wanted
    write(items)


def unregister(alias: str, *, read: Callable[[], dict], write: Callable[[dict], None],
               running: Callable[[str], bool], error: type[RuntimeError], label: str) -> dict:
    items = read()
    if alias not in items:
        raise KeyError(alias)
    if running(alias):
        raise error(f"稼働中の{label}モデルを先に停止してください")
    items.pop(alias)
    write(items)
    return {"alias": alias, "config_deleted": True, "model_files_deleted": False}
