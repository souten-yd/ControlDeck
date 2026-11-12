"""Read-only identity checks for opt-in Host-local generation provenance.

Call from a thread, after Host admission and ensure_ready, then again before
reporting success. This proves process/file-metadata continuity, not a digest
of model weights. No path, process ID, alias, argv or credential is public.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil

from app.models_mgmt.ai_gateway import AITarget, AITargetUnavailable, require_local_target

REVISION_KIND = 'host_process_file_metadata_v1'


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _file(path: str | Path, *, gguf: bool = False) -> tuple[str, int, int, int, int, int]:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file() or (gguf and resolved.suffix.lower() != '.gguf'):
        raise AITargetUnavailable('local generation file cannot be verified')
    stat = resolved.stat()
    return str(resolved), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass(frozen=True)
class ConfiguredIdentity:
    model_ref: str
    files: tuple[tuple[str, tuple[str, int, int, int, int, int]], ...]
    alias: str
    port: int


def _configured(target: AITarget) -> ConfiguredIdentity:
    """Snapshot the selected managed files without starting or stopping anything."""
    from app.models_mgmt import llama

    from app.models_mgmt import strata_gateway
    if target.model in strata_gateway._read():
        from app.models_mgmt import strata_provenance
        return strata_provenance.configured(target)
    require_local_target(target)
    instance = llama.get_instance(target.model)
    files = {'--model': _file(instance['model_path'], gguf=True), '--executable': _file(llama.server_path())}
    if instance.get('mmproj_path'):
        files['--mmproj'] = _file(instance['mmproj_path'], gguf=True)
    parts = llama.parse_spec_types(str(instance.get('spec_type', 'none')))
    if any(part in llama.SPEC_TYPES_NEEDING_DRAFT_MODEL for part in parts):
        files['--spec-draft-model'] = _file(instance.get('spec_draft_model_path', ''), gguf=True)
    port = urlsplit(target.base_url).port
    if type(port) is not int or port != instance.get('port', 8080):
        raise AITargetUnavailable('local generation endpoint changed')
    ordered = tuple(sorted(files.items()))
    return ConfiguredIdentity(_digest([REVISION_KIND, ordered]), ordered, target.model, port)


def configured(target: AITarget) -> ConfiguredIdentity:
    try:
        return _configured(target)
    except (OSError, KeyError, ValueError, TypeError, RuntimeError) as exc:
        raise AITargetUnavailable('local generation configuration cannot be verified') from exc


def _argument(argv: list[str], flag: str) -> str | None:
    positions = [i for i, value in enumerate(argv) if value == flag]
    if not positions:
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise AITargetUnavailable('local generation arguments cannot be verified')
    return argv[positions[0] + 1]


def capture(target: AITarget, expected: ConfiguredIdentity | None = None) -> dict[str, str]:
    """Attest the ready unit, serving PID and files; fail closed on drift."""
    from app.applications import systemd
    from app.models_mgmt import llama

    try:
        from app.models_mgmt import strata_gateway
        if target.model in strata_gateway._read():
            from app.models_mgmt import strata_provenance
            return strata_provenance.capture(target, expected)
        current = configured(target)
        if expected is not None and current != expected:
            raise AITargetUnavailable('local generation configuration changed')
        state = systemd.query_status(llama.unit_name(target.model))
        pid = state.get('pid')
        if state.get('status') != 'RUNNING' or type(pid) is not int or pid <= 0:
            raise AITargetUnavailable('local generation process unavailable')
        process = psutil.Process(pid)
        started = process.create_time()
        argv = process.cmdline()
        if (process.uids().real != os.getuid() or not 1 <= len(argv) <= 512
                or sum(len(a) for a in argv) > 1024 * 1024):
            raise AITargetUnavailable('local generation process identity mismatch')
        files = dict(current.files)
        if str(Path(process.exe()).resolve(strict=True)) != files['--executable'][0]:
            raise AITargetUnavailable('local generation binary changed')
        if (_argument(argv, '--alias') != current.alias or _argument(argv, '--port') != str(current.port)
                or _argument(argv, '--host') != '127.0.0.1'):
            raise AITargetUnavailable('local generation process endpoint mismatch')
        for flag in ('--model', '--mmproj', '--spec-draft-model'):
            argument = _argument(argv, flag)
            if flag in files:
                if argument is None or str(Path(argument).resolve(strict=True)) != files[flag][0]:
                    raise AITargetUnavailable('local generation process model mismatch')
            elif argument is not None:
                raise AITargetUnavailable('unexpected local generation model input')
        # A same-path replacement/in-place edit after process startup cannot
        # establish which weights are already resident in GPU memory.
        if any(max(metadata[-2:]) > int(started * 1_000_000_000) for metadata in files.values()):
            raise AITargetUnavailable('local generation files changed after process startup')
        listening = any(c.status == psutil.CONN_LISTEN and c.laddr.ip == '127.0.0.1'
                        and c.laddr.port == current.port for c in process.net_connections(kind='tcp'))
        if not listening:
            raise AITargetUnavailable('local generation process does not own the endpoint')
        if configured(target) != current or psutil.Process(pid).create_time() != started:
            raise AITargetUnavailable('local generation identity changed during inspection')
        return {'execution_policy': 'local_only', 'revision_kind': REVISION_KIND,
                'model_ref': current.model_ref, 'runtime_ref': _digest([pid, started, argv, current.model_ref])}
    except (OSError, KeyError, ValueError, TypeError, RuntimeError, psutil.Error) as exc:
        raise AITargetUnavailable('local generation provenance is unavailable') from exc
