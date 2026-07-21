"""Trusted worker executed only inside an Application Builder systemd user unit."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import application_builds_dir

MAX_CAPTURE_CHARS = 16_000
MAX_EMITTED_BYTES = 1024 * 1024
_emitted_bytes = 0
_log_truncated = False


class BuildWorkerError(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_symlink(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _state_path(root: Path) -> Path:
    return root / "state.json"


def _write_state(root: Path, phase: str, **extra: Any) -> None:
    payload = {
        "schemaVersion": 1,
        "phase": phase,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    temporary = root / ".state.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(_state_path(root))


def _resolve_root(value: str) -> Path:
    owner = application_builds_dir().resolve(strict=True)
    raw = Path(value)
    if raw.is_symlink():
        raise BuildWorkerError("Build root cannot be a symbolic link")
    root = raw.resolve(strict=True)
    if not root.is_dir() or not _inside(root, owner):
        raise BuildWorkerError("Build root is outside the application-owned directory")
    return root


def _resolve_dotnet(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    if path.name != "dotnet" or not path.is_file() or not os.access(path, os.X_OK):
        raise BuildWorkerError("The selected SDK executable is not an allowlisted dotnet binary")
    return path


def _verify_network_denied() -> None:
    """Fail closed unless the transient unit blocks both Internet families."""
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        probe.close()
        label = "IPv4" if family == socket.AF_INET else "IPv6"
        raise BuildWorkerError(f"Build network isolation is not enforced for {label}")


def _sdk_environment(root: Path) -> dict[str, str]:
    """Return the complete minimal environment inherited by generated code."""
    home = root / ".build-home"
    temporary = root / ".tmp"
    dotnet_home = root / ".dotnet-home"
    packages = root / ".nuget" / "packages"
    for directory in (home, temporary, dotnet_home, packages):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    return {
        "HOME": str(home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": str(temporary),
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_CLI_HOME": str(dotnet_home),
        "NUGET_PACKAGES": str(packages),
    }


def _project_files(root: Path) -> tuple[Path, Path]:
    source_raw = root / "source"
    if source_raw.is_symlink():
        raise BuildWorkerError("Generated source root is invalid")
    source = source_raw.resolve(strict=True)
    if not _inside(source, root):
        raise BuildWorkerError("Generated source root is invalid")
    tests = sorted(source.glob("*/tests/*.GeneratedTests/*.csproj"))
    if len(tests) != 1:
        raise BuildWorkerError("Generated source must contain exactly one self-test project")
    test_raw = tests[0]
    test_project = test_raw.resolve(strict=True)
    if not _inside(test_project, source) or _has_symlink(test_raw, source):
        raise BuildWorkerError("Generated self-test project escaped the source root")
    application_projects = sorted(source.glob("*/src/*/*.csproj"))
    if len(application_projects) != 1:
        raise BuildWorkerError("Generated source must contain exactly one application project")
    application_raw = application_projects[0]
    application_project = application_raw.resolve(strict=True)
    if not _inside(application_project, source) or _has_symlink(application_raw, source):
        raise BuildWorkerError("Generated application project escaped the source root")
    return application_project, test_project


def _run(argv: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    global _emitted_bytes, _log_truncated
    process = subprocess.Popen(
        argv, cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
        env=environment,
    )
    captured = ""
    assert process.stdout is not None
    for line in process.stdout:
        encoded = line.encode("utf-8", errors="replace")
        remaining = MAX_EMITTED_BYTES - _emitted_bytes
        if remaining > 0:
            emitted = encoded[:remaining].decode("utf-8", errors="ignore")
            print(emitted, end="", flush=True)
            _emitted_bytes += len(emitted.encode("utf-8"))
        elif not _log_truncated:
            print("\n[Control Deck: build log truncated at 1 MiB]\n", flush=True)
            _log_truncated = True
        captured = (captured + line)[-MAX_CAPTURE_CHARS:]
    code = process.wait()
    if code != 0:
        raise BuildWorkerError(f"SDK command failed with exit code {code}: {captured[-2000:]}")
    return captured


def run(root_value: str, dotnet_value: str, *, require_network_denied: bool = False) -> None:
    root = _resolve_root(root_value)
    dotnet = _resolve_dotnet(dotnet_value)
    if require_network_denied:
        _verify_network_denied()
    application_project, test_project = _project_files(root)
    environment = _sdk_environment(root)
    _write_state(root, "restoring")
    _run([
        str(dotnet), "restore", str(test_project), "--nologo",
        "--ignore-failed-sources", "--disable-parallel",
    ], cwd=test_project.parent, environment=environment)
    _write_state(root, "building")
    _run([
        str(dotnet), "build", str(test_project), "--nologo", "--configuration", "Release", "--no-restore",
        "--warnaserror", "--disable-build-servers",
    ], cwd=test_project.parent, environment=environment)
    _write_state(root, "testing")
    _run([
        str(dotnet), "run", "--project", str(test_project), "--configuration", "Release",
        "--no-build", "--no-restore",
    ], cwd=test_project.parent, environment=environment)
    _write_state(root, "completed", result="success", exitCode=0, applicationProject=str(application_project.relative_to(root)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--dotnet", required=True)
    parser.add_argument("--require-network-denied", action="store_true")
    args = parser.parse_args()
    try:
        run(args.root, args.dotnet, require_network_denied=args.require_network_denied)
    except Exception as exc:  # worker must leave a durable terminal state
        try:
            root = _resolve_root(args.root)
            _write_state(root, "failed", result="worker-failed", exitCode=1, error=str(exc)[:2000])
        except Exception:
            pass
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
