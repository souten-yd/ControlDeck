"""Python インタープリターとプロジェクト構成の自動検出。候補の提示のみを行い、実行はしない。"""
from __future__ import annotations

import glob
import subprocess
from pathlib import Path


def _version_of(python: Path) -> str | None:
    try:
        r = subprocess.run(
            [str(python), "--version"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def discover_pythons() -> list[dict]:
    candidates: list[Path] = []
    for pattern in (
        "/usr/bin/python3",
        "/usr/bin/python3.*",
        "/usr/local/bin/python3",
        "/usr/local/bin/python3.*",
        str(Path.home() / ".pyenv/versions/*/bin/python"),
        str(Path.home() / "miniconda3/envs/*/bin/python"),
        str(Path.home() / "anaconda3/envs/*/bin/python"),
    ):
        for hit in glob.glob(pattern):
            p = Path(hit)
            if p.is_file() and p not in candidates:
                candidates.append(p)
    results = []
    seen_real: set[str] = set()
    for p in candidates:
        real = str(p.resolve())
        if real in seen_real:
            continue
        seen_real.add(real)
        version = _version_of(p)
        if version:
            results.append({"path": str(p), "version": version})
    return results


ENTRY_CANDIDATES = ["main.py", "app.py", "server.py", "manage.py", "run.py"]
PROJECT_MARKERS = ["pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock", "uv.lock"]


def discover_project(path: str) -> dict:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return {"exists": False, "venvs": [], "entries": [], "markers": []}
    venvs = []
    for name in (".venv", "venv"):
        py = root / name / "bin" / "python"
        if py.is_file():
            venvs.append({"path": str(py), "version": _version_of(py)})
    entries = [str(root / e) for e in ENTRY_CANDIDATES if (root / e).is_file()]
    markers = [m for m in PROJECT_MARKERS if (root / m).is_file()]
    return {"exists": True, "venvs": venvs, "entries": entries, "markers": markers}
