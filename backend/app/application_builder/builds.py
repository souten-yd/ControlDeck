"""Durable, systemd-isolated build orchestration for generated application source."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import stat
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_builder.source_generator import SourceBundle
from app.config import application_builds_dir, get_config
from app.models import ApplicationBuild, ApplicationBuildArtifact
from app.project_lab.service import redact_text

ACTIVE_STATES = {"queued", "preparing", "generating", "restoring", "building", "testing", "canceling"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "timed_out", "interrupted"}
MAX_CONCURRENT_BUILDS = 2
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_START_LOCK = threading.Lock()


class ApplicationBuildError(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_parts(value: str) -> tuple[str, ...]:
    """Return a portable, strictly relative artifact path."""
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ApplicationBuildError("Build artifact path is invalid")
    return pure.parts


def _has_symlink(path: Path, root: Path) -> bool:
    """Check each raw component before resolve() can hide a symlink."""
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


def _tools() -> tuple[str, str, str]:
    paths = tuple(shutil.which(name) for name in ("systemd-run", "systemctl", "journalctl"))
    if any(path is None for path in paths):
        raise ApplicationBuildError("systemd user build tools are unavailable")
    return paths[0] or "", paths[1] or "", paths[2] or ""


def dotnet_sdk_path() -> Path | None:
    configured = os.environ.get("CONTROL_DECK_DOTNET") or get_config().application_builder.dotnet_path
    raw = configured or shutil.which("dotnet")
    if not raw:
        return None
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except OSError:
        return None
    return path if path.name == "dotnet" and path.is_file() and os.access(path, os.X_OK) else None


def _worker_python_launcher() -> Path:
    """Return the validated launcher without erasing virtualenv semantics.

    Resolving ``.venv/bin/python`` to ``/usr/bin/python`` before exec makes the
    interpreter ignore pyvenv.cfg and its installed packages. We still resolve
    and validate the target and parent, but execute the vetted launcher path.
    """
    # The supported test entrypoint invokes ../.venv/bin/python from backend,
    # so sys.executable can contain a lexical "..". Normalize it without
    # resolve(): resolving the launcher symlink would erase virtualenv startup
    # semantics and make the worker lose installed packages.
    launcher = Path(os.path.abspath(sys.executable))
    if not launcher.is_absolute() or any(part == ".." for part in launcher.parts):
        raise ApplicationBuildError("Build worker Python launcher is invalid")
    try:
        resolved = launcher.resolve(strict=True)
        prefix_bin = (Path(sys.prefix).resolve(strict=True) / "bin").resolve(strict=True)
        launcher_parent = launcher.parent.resolve(strict=True)
    except OSError as exc:
        raise ApplicationBuildError("Build worker Python launcher is unavailable") from exc
    if launcher_parent != prefix_bin or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ApplicationBuildError("Build worker Python launcher is outside the active environment")
    if not launcher.name.startswith("python") or not resolved.name.startswith("python"):
        raise ApplicationBuildError("Build worker Python launcher is not an interpreter")
    return launcher


def build_capability() -> dict[str, Any]:
    sdk = dotnet_sdk_path()
    systemd = all(shutil.which(name) for name in ("systemd-run", "systemctl", "journalctl"))
    return {
        "available": bool(sdk and systemd), "sdk": "dotnet", "sdkPath": str(sdk) if sdk else None,
        "systemdUser": bool(systemd), "network": "denied", "maxConcurrent": MAX_CONCURRENT_BUILDS,
        "note": "Build runs in a resource-limited systemd user unit. Package restore is offline and succeeds only for SDK or locally cached dependencies.",
    }


def _safe_extract(bundle: SourceBundle, destination: Path) -> None:
    if len(bundle.archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ApplicationBuildError("Generated source archive exceeds the 64 MiB build input limit")
    total = 0
    with zipfile.ZipFile(BytesIO(bundle.archive_bytes)) as archive:
        infos = archive.infolist()
        if len(infos) > 500:
            raise ApplicationBuildError("Generated source archive contains too many files")
        seen: set[str] = set()
        for info in infos:
            pure = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if pure.is_absolute() or not pure.parts or ".." in pure.parts or "" in pure.parts:
                raise ApplicationBuildError("Generated source archive contains an unsafe path")
            normalized = pure.as_posix()
            if normalized in seen or info.flag_bits & 0x1:
                raise ApplicationBuildError("Generated source archive contains a duplicate or encrypted entry")
            seen.add(normalized)
            if mode and not stat.S_ISREG(mode):
                raise ApplicationBuildError("Generated source archive contains a non-regular entry")
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise ApplicationBuildError("Generated source exceeds the 128 MiB extraction limit")
            target = (destination / Path(*pure.parts)).resolve(strict=False)
            if not _inside(target, destination):
                raise ApplicationBuildError("Generated source escaped the build root")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o700)
            with archive.open(info) as source, target.open("wb") as output:
                written = 0
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > info.file_size or total - info.file_size + written > MAX_EXTRACTED_BYTES:
                        raise ApplicationBuildError("Generated source exceeded its declared extraction limit")
                    output.write(chunk)
            target.chmod(0o600)


def _write_initial_state(root: Path) -> None:
    payload = {"schemaVersion": 1, "phase": "queued", "updatedAt": datetime.now(timezone.utc).isoformat()}
    (root / "state.json").write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _state(row: ApplicationBuild) -> dict[str, Any]:
    try:
        root = build_root(row)
        payload = json.loads((root / "state.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError, ApplicationBuildError):
        return {}


def build_root(row: ApplicationBuild) -> Path:
    owner = application_builds_dir().resolve(strict=True)
    raw = Path(row.build_root)
    if raw.is_symlink():
        raise ApplicationBuildError("Build root is unavailable or outside the application-owned directory")
    root = raw.resolve(strict=True)
    if not root.is_dir() or not _inside(root, owner):
        raise ApplicationBuildError("Build root is unavailable or outside the application-owned directory")
    return root


def _show(unit_name: str) -> dict[str, str] | None:
    _, systemctl, _ = _tools()
    result = subprocess.run(
        [systemctl, "--user", "show", f"{unit_name}.service", "--no-pager",
         "--property=LoadState", "--property=ActiveState", "--property=SubState",
         "--property=Result", "--property=ExecMainStatus"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode != 0:
        return None
    return {key: value for line in result.stdout.splitlines() if "=" in line for key, value in [line.split("=", 1)]}


def _unit_name(row: ApplicationBuild) -> str:
    expected = f"control-deck-application-build-{row.id}"
    if not row.id or row.unit_name != expected:
        raise ApplicationBuildError("Build unit does not match the durable build record")
    return expected


def _checksum(path: Path) -> str:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _finalize_artifacts(db: Session, row: ApplicationBuild) -> None:
    if db.execute(select(ApplicationBuildArtifact.id).where(ApplicationBuildArtifact.build_id == row.id)).first():
        return
    try:
        root = build_root(row)
    except ApplicationBuildError:
        return
    candidates = [root / "source.zip"]
    candidates.extend(sorted((root / "source").glob("*/src/*/bin/Release/net8.0/*")))
    for path in candidates[:50]:
        if _has_symlink(path, root):
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_file() or not _inside(resolved, root):
            continue
        relative = resolved.relative_to(root).as_posix()
        db.add(ApplicationBuildArtifact(
            build_id=row.id, path=relative,
            kind="source" if relative == "source.zip" else "binary",
            mime_type=mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
            size=resolved.stat().st_size, checksum=_checksum(resolved),
        ))


def refresh_build(db: Session, row: ApplicationBuild) -> ApplicationBuild:
    if row.status in TERMINAL_STATES:
        return row
    state = _state(row)
    phase = str(state.get("phase") or "")
    try:
        unit = _show(_unit_name(row)) if row.unit_name else None
    except ApplicationBuildError:
        unit = None
    if unit and unit.get("ActiveState") in {"active", "activating", "reloading"} and unit.get("SubState") != "exited":
        if phase in ACTIVE_STATES:
            row.status = phase
        db.commit()
        return row
    if phase == "completed":
        row.status, row.result, row.exit_code = "completed", "success", 0
    elif phase == "failed":
        row.status, row.result = "failed", str(state.get("result") or "worker-failed")
        row.error_redacted = redact_text(str(state.get("error") or "Build failed")[:4000])
        row.exit_code = int(state.get("exitCode") or 1)
    elif unit is None or unit.get("LoadState") == "not-found":
        row.status, row.result, row.error_redacted = "interrupted", "unit-not-found", "Build unit state is unavailable"
    else:
        result = unit.get("Result", "")
        try:
            row.exit_code = int(unit.get("ExecMainStatus", "1"))
        except ValueError:
            row.exit_code = None
        if result == "timeout":
            row.status, row.result = "timed_out", "timeout"
        elif row.status == "canceling" or result in {"signal", "canceled"}:
            row.status, row.result = "cancelled", "cancelled"
        else:
            row.status, row.result = "failed", result or "unit-failed"
            row.error_redacted = "Build unit exited before writing a successful terminal state"
    row.finished_at = datetime.now(timezone.utc)
    _finalize_artifacts(db, row)
    db.commit()
    return row


def start_build(
    db: Session, *, project_id: int, target_id: str, framework: str, timeout_seconds: int,
    bundle: SourceBundle, created_by: int | None,
) -> ApplicationBuild:
    # FastAPI sync handlers run in a thread pool. Keep the capacity check and
    # durable row/unit creation in one process-wide critical section.
    with _START_LOCK:
        return _start_build_locked(
            db, project_id=project_id, target_id=target_id, framework=framework,
            timeout_seconds=timeout_seconds, bundle=bundle, created_by=created_by,
        )


def _start_build_locked(
    db: Session, *, project_id: int, target_id: str, framework: str, timeout_seconds: int,
    bundle: SourceBundle, created_by: int | None,
) -> ApplicationBuild:
    sdk = dotnet_sdk_path()
    if sdk is None:
        raise ApplicationBuildError(".NET SDK is unavailable; install an allowlisted dotnet SDK first")
    systemd_run, _, _ = _tools()
    for active in db.execute(select(ApplicationBuild).where(ApplicationBuild.status.in_(ACTIVE_STATES))).scalars().all():
        refresh_build(db, active)
    active = db.execute(select(ApplicationBuild).where(ApplicationBuild.status.in_(ACTIVE_STATES))).scalars().all()
    if len(active) >= MAX_CONCURRENT_BUILDS:
        raise ApplicationBuildError("Application build concurrency limit has been reached")
    if any(item.project_id == project_id for item in active):
        raise ApplicationBuildError("This Application Project already has an active build")
    row = ApplicationBuild(
        project_id=project_id, target_id=target_id, framework=framework, status="preparing",
        source_checksum=bundle.source_checksum, archive_checksum=bundle.archive_checksum,
        generator_json=json.dumps(bundle.manifest.get("generator") or {}, sort_keys=True),
        sdk_path=str(sdk), timeout_seconds=timeout_seconds, created_by=created_by,
    )
    owner = application_builds_dir().resolve(strict=True)
    db.add(row)
    db.flush()
    root = owner / f"build-{row.id}"
    row.build_root = str(root)
    row.unit_name = f"control-deck-application-build-{row.id}"
    db.commit()
    db.refresh(row)
    try:
        root.mkdir(mode=0o700)
        if root.resolve(strict=True) != root:
            raise ApplicationBuildError("Build root resolved outside its durable path")
        row.status = "generating"; db.commit()
        (root / "source.zip").write_bytes(bundle.archive_bytes)
        (root / "source.zip").chmod(0o600)
        source = root / "source"; source.mkdir(mode=0o700)
        _safe_extract(bundle, source)
        _write_initial_state(root)
        python = _worker_python_launcher()
        backend_root = Path(__file__).resolve(strict=True).parents[2]
        argv = [
            systemd_run, "--user", "--quiet", f"--unit={row.unit_name}",
            "--property=Type=exec", f"--property=WorkingDirectory={root}", "--property=RemainAfterExit=yes",
            f"--property=RuntimeMaxSec={timeout_seconds}s", "--property=TimeoutStopSec=10s",
            "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=ProtectSystem=strict",
            "--property=ProtectHome=read-only", f"--property=ReadWritePaths={root}",
            "--property=IPAddressDeny=any", "--property=RestrictAddressFamilies=AF_UNIX",
            "--property=MemoryMax=2G", "--property=TasksMax=128", "--property=CPUQuota=200%",
            "--property=UMask=0077", "--property=LockPersonality=yes",
            "--property=LogRateLimitIntervalSec=30s", "--property=LogRateLimitBurst=2000",
            f"--setenv=PYTHONPATH={backend_root}",
        ]
        config = os.environ.get("CONTROL_DECK_CONFIG")
        if config:
            argv.append(f"--setenv=CONTROL_DECK_CONFIG={Path(config).resolve(strict=True)}")
        argv.extend([
            str(python), "-m", "app.application_builder.build_worker",
            "--root", str(root), "--dotnet", str(sdk),
            "--require-network-denied",
        ])
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        if result.returncode != 0:
            raise ApplicationBuildError(redact_text((result.stderr or result.stdout or "systemd-run failed")[:4000]))
        row.status = "queued"; row.started_at = datetime.now(timezone.utc); db.commit()
        return row
    except Exception as exc:
        row.status, row.result = "failed", "start-failed"
        row.error_redacted = redact_text(str(exc)[:4000]); row.finished_at = datetime.now(timezone.utc)
        db.commit()
        if isinstance(exc, ApplicationBuildError):
            raise
        raise ApplicationBuildError(row.error_redacted) from exc


def cancel_build(db: Session, row: ApplicationBuild) -> ApplicationBuild:
    refresh_build(db, row)
    if row.status not in ACTIVE_STATES:
        raise ApplicationBuildError("This build has already finished")
    _, systemctl, _ = _tools()
    unit_name = _unit_name(row)
    row.status = "canceling"; db.commit()
    result = subprocess.run(
        [systemctl, "--user", "stop", f"{unit_name}.service"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode != 0:
        row.status = "failed"; row.error_redacted = "Build unit could not be stopped"; db.commit()
        raise ApplicationBuildError(row.error_redacted)
    row.status, row.result, row.finished_at = "cancelled", "cancelled", datetime.now(timezone.utc)
    _finalize_artifacts(db, row); db.commit()
    return row


def build_logs(row: ApplicationBuild) -> str:
    try:
        _, _, journalctl = _tools()
        unit_name = _unit_name(row)
    except ApplicationBuildError:
        return "Build journal is unavailable on this host."
    result = subprocess.run(
        [journalctl, "--user", "--unit", f"{unit_name}.service", "--output=cat", "--no-pager", "--lines=2000"],
        capture_output=True, timeout=10, check=False,
    )
    return redact_text(result.stdout[-MAX_LOG_BYTES:].decode("utf-8", errors="replace"))


def build_out(db: Session, row: ApplicationBuild, *, include_logs: bool = False) -> dict[str, Any]:
    refresh_build(db, row)
    artifacts = db.execute(select(ApplicationBuildArtifact).where(
        ApplicationBuildArtifact.build_id == row.id,
    ).order_by(ApplicationBuildArtifact.id)).scalars().all()
    return {
        "id": row.id, "projectId": row.project_id, "targetId": row.target_id, "framework": row.framework,
        "status": row.status, "sourceChecksum": row.source_checksum, "archiveChecksum": row.archive_checksum,
        "generator": json.loads(row.generator_json or "{}"), "sdk": row.sdk_name,
        "timeoutSeconds": row.timeout_seconds, "result": row.result, "exitCode": row.exit_code,
        "error": row.error_redacted, "createdAt": row.created_at.isoformat(),
        "startedAt": row.started_at.isoformat() if row.started_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
        "isolation": {"systemdUser": True, "network": "denied", "memoryMax": "2G", "tasksMax": 128, "cpuQuota": "200%"},
        "artifacts": [{
            "id": item.id, "path": item.path, "kind": item.kind, "mimeType": item.mime_type,
            "size": item.size, "checksum": item.checksum,
        } for item in artifacts],
        **({"logs": build_logs(row)} if include_logs else {}),
    }


def artifact_path(row: ApplicationBuild, artifact: ApplicationBuildArtifact) -> Path:
    root = build_root(row)
    raw = root.joinpath(*_relative_parts(artifact.path))
    if _has_symlink(raw, root):
        raise ApplicationBuildError("Build artifact is unavailable")
    path = raw.resolve(strict=True)
    if not path.is_file() or not _inside(path, root):
        raise ApplicationBuildError("Build artifact is unavailable")
    return path


def delete_build(db: Session, row: ApplicationBuild) -> None:
    refresh_build(db, row)
    if row.status in ACTIVE_STATES:
        raise ApplicationBuildError("Cancel the active build before deleting it")
    root: Path | None = None
    if row.build_root:
        owner = application_builds_dir().resolve(strict=True)
        raw = Path(row.build_root)
        if not raw.is_absolute() or raw.parent != owner or raw.name != f"build-{row.id}" or raw.is_symlink():
            raise ApplicationBuildError("Build root does not match the durable build record")
        if raw.exists():
            root = build_root(row)
            if root != raw:
                raise ApplicationBuildError("Build root does not match the durable build record")
    artifacts = db.execute(select(ApplicationBuildArtifact).where(
        ApplicationBuildArtifact.build_id == row.id,
    )).scalars().all()
    for artifact in artifacts:
        db.delete(artifact)
    # Relationship orderingを暗黙に期待せず、FK childを先に確定する。
    db.flush()
    expected_unit = f"control-deck-application-build-{row.id}"
    if row.unit_name == expected_unit:
        try:
            _, systemctl, _ = _tools()
            subprocess.run(
                [systemctl, "--user", "stop", f"{expected_unit}.service"],
                capture_output=True, timeout=10, check=False,
            )
            subprocess.run(
                [systemctl, "--user", "reset-failed", f"{expected_unit}.service"],
                capture_output=True, timeout=10, check=False,
            )
        except (ApplicationBuildError, subprocess.SubprocessError):
            pass
    if root is not None:
        shutil.rmtree(root)
    db.delete(row)
    db.commit()
