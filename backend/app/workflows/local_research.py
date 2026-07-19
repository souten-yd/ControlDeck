"""Deep Research向けローカルコードプロジェクトの有限・読み取り専用アダプター。"""
from __future__ import annotations

import os
from pathlib import Path

from app.files import service as files
from app.workflows.code_structure import repository_structure_summary
from app.workflows.github_research import _select_files


SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build",
    "coverage", ".next", ".cache", ".pytest_cache", "__pycache__",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "secret.key",
}


def _safe_candidates(root: Path, *, max_entries: int = 5000) -> tuple[list[dict], bool]:
    entries: list[dict] = []
    truncated = False
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not (Path(current) / name).is_symlink()]
        for filename in filenames:
            path = Path(current) / filename
            relative = path.relative_to(root)
            if path.is_symlink() or filename.casefold() in SENSITIVE_NAMES:
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                size = resolved.stat().st_size
            except (OSError, ValueError):
                continue
            entries.append({"path": relative.as_posix(), "type": "blob", "size": size})
            if len(entries) >= max_entries:
                truncated = True
                return entries, truncated
    return entries, truncated


def inspect_local_project(project_path: str, query: str, *, max_files: int = 12) -> dict:
    """許可root配下のprojectを実行せず、主要テキストファイルと静的構造だけ収集する。"""
    root = files.resolve(project_path)
    if not root.is_dir():
        raise NotADirectoryError(f"コードプロジェクトではありません: {project_path}")
    entries, truncated = _safe_candidates(root)
    selected = _select_files(entries, query, max(1, min(max_files, 24)))
    sources: list[dict] = []
    errors: list[str] = []
    for entry in selected:
        relative = str(entry["path"])
        try:
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
            content = path.read_text(encoding="utf-8", errors="replace")[:12_000]
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {type(exc).__name__}")
            continue
        sources.append({
            "title": f"local project: {relative}",
            "url": "",
            "source": "Local code",
            "kind": "document",
            "snippet": content,
            "meta": {"path": relative, "project": root.name},
        })
    sources.insert(0, {
        "title": f"local project: {root.name} structure",
        "url": "",
        "source": "Local project tree",
        "kind": "report",
        "snippet": (
            f"project: {root.name}\nfiles indexed: {len(entries)}\ntruncated: {str(truncated).lower()}\n"
            + "\n".join(str(item["path"]) for item in entries[:800])
        )[:12_000],
        "meta": {"project": root.name},
    })
    code_sources = [source for source in sources if source["source"] == "Local code"]
    if code_sources:
        sources.append({
            "title": f"local project: {root.name} static symbol and integration index",
            "url": "",
            "source": "Local static analysis",
            "kind": "report",
            "snippet": repository_structure_summary(code_sources),
            "meta": {"project": root.name},
        })
    if truncated:
        errors.append(f"ローカルproject索引上限 {len(entries)}件へ到達")
    return {"sources": sources, "errors": errors, "files_selected": len(code_sources), "truncated": truncated}
