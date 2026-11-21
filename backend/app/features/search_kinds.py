"""「検索と収集」の機能の導入の仕組みのうち、pip・npm 以外の 2 種類（docs/PLAN-web-collector.md §3）。

- `managed-script`: 既存の導入スクリプト（例 `scripts/setup-searxng.sh`）を機能と拡張から動かす。導入＝スクリプト、
  更新＝スクリプトに `update`。消すのは Apps と導入先を巻き込むので、機能と拡張からはアンインストールさせない。
- `playwright-browser`: Web 収集のブラウザ（W2）。本体の venv の Playwright で、版の合った Chromium（headless shell）を
  機能の専用フォルダ（`PLAYWRIGHT_BROWSERS_PATH`）に入れる。利用者の Chrome とプロファイル・ログインを共有しない。
"""
from __future__ import annotations

import os
import subprocess
import sys
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


# ---- playwright-browser ----

def browsers_dir(root: Path) -> Path:
    return root / "browsers"


def browser_marker(root: Path) -> Path:
    """導入済みの headless shell の実行ファイル（無ければ存在しないパス）。版ごとのフォルダ名に番号が入る。"""
    found = sorted(browsers_dir(root).glob("chromium_headless_shell-*/chrome-*/chrome-headless-shell"))
    found += sorted(browsers_dir(root).glob("chromium_headless_shell-*/chrome-linux/headless_shell"))
    return found[-1] if found else browsers_dir(root) / "missing"


def browser_version(marker: Path) -> str:
    for part in marker.parts:
        if part.startswith("chromium_headless_shell-"):
            return "build " + part.rsplit("-", 1)[1]
    return ""


def browser_install(root: Path) -> subprocess.CompletedProcess[str]:
    """本体の Playwright に合う版を入れる（Playwright の更新は ControlDeck 本体の更新で行う）。"""
    browsers_dir(root).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir(root))}
    return subprocess.run([sys.executable, "-m", "playwright", "install", "chromium-headless-shell"], env=env,
                          capture_output=True, text=True, timeout=1800, check=False)
