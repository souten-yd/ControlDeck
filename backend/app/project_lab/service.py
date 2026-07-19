"""CodeDEVのread-only discovery、manifest、artifact catalog。"""
from __future__ import annotations

import csv
import io
import itertools
import json
import mimetypes
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.project_lab import ProjectManifest
from app.workflows.redaction import redact

PROJECT_ROOT = (Path.home() / "CodeDEV").resolve()
MAX_PROJECTS = 100
MAX_ARTIFACTS = 500
MAX_MANIFEST_BYTES = 256 * 1024
MAX_INLINE_TEXT_BYTES = 256 * 1024
MAX_CSV_ROWS = 200
SKIP_DIRS = {".git", ".controldeck", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build"}
NON_ARTIFACT_NAMES = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json",
    "jsconfig.json", "composer.json", "cargo.lock", "project.json",
}
ARTIFACT_KINDS = {
    ".html": "html", ".htm": "html", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".webp": "image", ".gif": "image", ".svg": "image", ".csv": "table", ".tsv": "table",
    ".json": "json", ".md": "markdown", ".markdown": "markdown", ".pdf": "pdf",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".mp4": "video", ".webm": "video",
    ".log": "log", ".txt": "text",
}
STATIC_RESOURCE_TYPES = {".css", ".woff", ".woff2", ".ttf", ".otf", ".ico"}
SENSITIVE_NAME = re.compile(r"(^|[._-])(secret|secrets|credential|credentials|private[_-]?key|id_rsa|id_ed25519)([._-]|$)", re.I)
SENSITIVE_TEXT = re.compile(
    r"(?im)(\b(?:authorization|password|passwd|token|secret|api[_-]?token|api[_-]?key)\b[ \t]*[:=][ \t]*)([^\s,;&]+)"
)


class ProjectLabError(ValueError):
    pass


def project_root() -> Path:
    PROJECT_ROOT.mkdir(exist_ok=True)
    return PROJECT_ROOT.resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_project(project_id: str) -> Path:
    if (not project_id or len(project_id) > 128 or project_id in {".", ".."}
            or project_id.startswith(".") or any(char in project_id for char in "/\\\x00")):
        raise ProjectLabError("project IDが不正です")
    root = project_root()
    candidate = root / project_id
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ProjectLabError("projectが見つかりません") from exc
    if not resolved.is_dir() or not _inside(resolved, root):
        raise ProjectLabError("CodeDEV外のprojectは開けません")
    return resolved


def resolve_artifact(project: Path, relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/")
    if not normalized or normalized.startswith(("/", "~")) or ".." in normalized.split("/") or "\x00" in normalized:
        raise ProjectLabError("artifact pathが不正です")
    try:
        resolved = (project / normalized).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ProjectLabError("artifactが見つかりません") from exc
    if not resolved.is_file() or not _inside(resolved, project):
        raise ProjectLabError("project外のartifactは開けません")
    if ARTIFACT_KINDS.get(resolved.suffix.lower()) is None and resolved.suffix.lower() not in STATIC_RESOURCE_TYPES:
        raise ProjectLabError("このfile typeは成果物previewの対象外です")
    if SENSITIVE_NAME.search(resolved.name):
        raise ProjectLabError("秘密情報を含む可能性があるfile名はpreview対象外です")
    return resolved


def _read_manifest(project: Path) -> tuple[ProjectManifest | None, list[dict[str, str]]]:
    path = project / ".controldeck" / "project.json"
    if not path.exists():
        return None, []
    try:
        resolved = path.resolve(strict=True)
        if not _inside(resolved, project) or resolved.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProjectLabError("manifestがproject外を指すか、size上限を超えています")
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        return ProjectManifest.model_validate(raw), []
    except ValidationError as exc:
        message = "; ".join(f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors())
        return None, [{"code": "MANIFEST_INVALID", "severity": "error", "message": message}]
    except json.JSONDecodeError as exc:
        return None, [{"code": "MANIFEST_INVALID", "severity": "error", "message": f"JSON構文が不正です（line {exc.lineno}, column {exc.colno}）"}]
    except OSError:
        return None, [{"code": "MANIFEST_INVALID", "severity": "error", "message": "manifestを安全に読み込めません"}]
    except ProjectLabError as exc:
        return None, [{"code": "MANIFEST_INVALID", "severity": "error", "message": str(exc)}]


def load_manifest(project: Path) -> ProjectManifest:
    manifest, diagnostics = _read_manifest(project)
    if manifest is None:
        message = diagnostics[0]["message"] if diagnostics else ".controldeck/project.jsonがありません"
        raise ProjectLabError(message)
    return manifest


def redact_text(value: str) -> str:
    return SENSITIVE_TEXT.sub(r"\1***", value)


def _manifest_out(manifest: ProjectManifest | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    return {
        "schemaVersion": manifest.schema_version, "name": manifest.name, "description": manifest.description,
        "profiles": [{
            "id": profile.id, "label": profile.label, "type": profile.type,
            "command": profile.command, "cwd": profile.cwd,
            "environmentNames": sorted(profile.environment), "secretRefs": profile.secret_refs,
            "artifacts": profile.artifacts,
        } for profile in manifest.profiles],
    }


def _technologies(project: Path) -> list[str]:
    result: list[str] = []
    if (project / "pyproject.toml").is_file() or list(project.glob("requirements*.txt")) or any(project.glob("*.py")):
        result.append("python")
    package_path = project / "package.json"
    if package_path.is_file():
        result.append("node")
        try:
            package_text = package_path.read_text(encoding="utf-8")[:512_000].lower()
            for needle, label in (("vite", "vite"), ("next", "nextjs"), ("react", "react"), ("vue", "vue")):
                if f'"{needle}"' in package_text and label not in result:
                    result.append(label)
        except OSError:
            pass
    if (project / "CMakeLists.txt").is_file():
        result.append("cmake")
    if (project / "Cargo.toml").is_file():
        result.append("rust")
    if any(project.glob("*.sln")) or any(project.glob("*.csproj")):
        result.append("dotnet")
    if (project / "index.html").is_file():
        result.append("static-web")
    return result


def _git_summary(project: Path) -> dict[str, Any] | None:
    git_dir = project / ".git"
    if not git_dir.exists():
        return None
    branch = "detached"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            branch = head.rsplit("/", 1)[-1]
    except OSError:
        pass
    dirty: bool | None = None
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "status", "--porcelain", "--untracked-files=normal"],
            cwd=project, capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            dirty = bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"branch": branch, "dirty": dirty}


def _artifact_candidates(project: Path, manifest: ProjectManifest | None) -> list[Path]:
    candidates: set[Path] = set()
    for current, directories, files in os.walk(project, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(project).parts)
        directories[:] = [name for name in directories if name not in SKIP_DIRS and not name.startswith(".") and depth < 6]
        for name in files:
            if len(candidates) >= MAX_ARTIFACTS:
                break
            path = current_path / name
            if path.name.lower() not in NON_ARTIFACT_NAMES and ARTIFACT_KINDS.get(path.suffix.lower()) and not SENSITIVE_NAME.search(path.name):
                candidates.add(path)
    if manifest:
        for profile in manifest.profiles:
            for pattern in profile.artifacts:
                for path in itertools.islice(project.glob(pattern), MAX_ARTIFACTS):
                    if len(candidates) >= MAX_ARTIFACTS:
                        break
                    if (path.is_file() and path.name.lower() not in NON_ARTIFACT_NAMES
                            and ARTIFACT_KINDS.get(path.suffix.lower()) and not SENSITIVE_NAME.search(path.name)):
                        candidates.add(path)
    return sorted(candidates, key=lambda item: item.relative_to(project).as_posix())[:MAX_ARTIFACTS]


def _text_preview(path: Path, kind: str) -> tuple[str | None, Any | None]:
    try:
        if path.stat().st_size > MAX_INLINE_TEXT_BYTES:
            return None, None
        text = path.read_text(encoding="utf-8", errors="replace")
        if kind == "json":
            structured = redact(json.loads(text))
            return json.dumps(structured, ensure_ascii=False, indent=2), structured
        if kind == "table":
            dialect = "excel-tab" if path.suffix.lower() == ".tsv" else "excel"
            rows = list(csv.reader(io.StringIO(text), dialect=dialect))[:MAX_CSV_ROWS + 1]
            headers = rows[0] if rows else []
            sensitive_columns = {index for index, header in enumerate(headers) if SENSITIVE_TEXT.search(f"{header}=value")}
            data_rows = [["***" if index in sensitive_columns else cell for index, cell in enumerate(row)] for row in rows[1:MAX_CSV_ROWS + 1]]
            return None, {"headers": headers, "rows": data_rows, "truncated": len(rows) > MAX_CSV_ROWS}
        return redact_text(text[:MAX_INLINE_TEXT_BYTES]), None
    except (OSError, json.JSONDecodeError, csv.Error):
        return None, None


def artifact_info(project: Path, path: Path, *, include_preview: bool = False) -> dict[str, Any] | None:
    try:
        resolved = path.resolve(strict=True)
        if not _inside(resolved, project) or not resolved.is_file():
            return None
        stat = resolved.stat()
    except OSError:
        return None
    kind = ARTIFACT_KINDS.get(resolved.suffix.lower())
    if not kind:
        return None
    relative = resolved.relative_to(project).as_posix()
    text, structured = _text_preview(resolved, kind) if include_preview else (None, None)
    return {
        "path": relative, "name": resolved.name, "kind": kind,
        "mimeType": mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
        "size": stat.st_size, "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "previewText": text, "structuredPreview": structured,
    }


def project_detail(project_id: str) -> dict[str, Any]:
    project = resolve_project(project_id)
    manifest, diagnostics = _read_manifest(project)
    artifacts = [item for path in _artifact_candidates(project, manifest) if (item := artifact_info(project, path)) is not None]
    stat = project.stat()
    return {
        "id": project_id,
        "name": manifest.name if manifest else project.name,
        "description": manifest.description if manifest else "",
        "path": str(project),
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "technologies": _technologies(project),
        "git": _git_summary(project),
        "manifest": _manifest_out(manifest),
        "diagnostics": diagnostics,
        "artifacts": artifacts,
        "capabilities": {"discovery": True, "artifactPreview": True, "execution": True, "webProxy": True, "llmEvaluation": False},
    }


def list_projects() -> list[dict[str, Any]]:
    root = project_root()
    rows: list[dict[str, Any]] = []
    for candidate in root.iterdir():
        if len(rows) >= MAX_PROJECTS or candidate.name.startswith(".") or not candidate.is_dir():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not _inside(resolved, root):
                continue
            manifest, diagnostics = _read_manifest(resolved)
            stat = resolved.stat()
            rows.append({
                "id": candidate.name, "name": manifest.name if manifest else candidate.name,
                "description": manifest.description if manifest else "",
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "technologies": _technologies(resolved), "git": _git_summary(resolved),
                "diagnostics": diagnostics,
                "capabilities": {"discovery": True, "artifactPreview": True, "execution": True, "webProxy": True, "llmEvaluation": False},
                "artifactCount": len(_artifact_candidates(resolved, manifest)),
                "profileCount": len(manifest.profiles) if manifest else 0,
            })
        except (OSError, ProjectLabError):
            continue
    return sorted(rows, key=lambda row: row["modifiedAt"], reverse=True)
