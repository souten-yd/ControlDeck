"""「検索と収集」の機能の導入の仕組みのうち、pip・npm 以外のもの（docs/PLAN-web-collector.md §3）。

- `managed-script`: 既存の導入スクリプト（例 `scripts/setup-searxng.sh`）を機能と拡張から動かす。導入＝スクリプト、
  更新＝スクリプトに `update`。消すのは Apps と導入先を巻き込むので、機能と拡張からはアンインストールさせない。
- Web 収集のブラウザは別に入れない（この PC の Chrome を使う。種類 `system-browser`、`app/web_collector/browser.py`）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _home(spec: dict) -> Path:
    return Path(os.path.expanduser(spec["install_dir"]))


# ---- managed-script ----

def script_marker(spec: dict) -> Path:
    return _home(spec) / spec["marker"]


def script_version(spec: dict) -> str:
    src = _home(spec) / "src"
    try:
        out = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%cs %h"], capture_output=True, text=True,
                             timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()[:40] if out.returncode == 0 else ""


def script_install(spec: dict, *, latest: bool) -> subprocess.CompletedProcess[str]:
    script = (REPO_ROOT / spec["script"]).resolve()
    if not script.is_relative_to(REPO_ROOT) or not script.is_file():
        raise FileNotFoundError(f"導入スクリプトがありません: {spec['script']}")
    args = ["bash", str(script), *(["update"] if latest else [])]
    return subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=1800, check=False)
