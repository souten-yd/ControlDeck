"""Playwright のブラウザ（機能と拡張の項目「Playwright のブラウザ」）。

3 つの版の Playwright がそれぞれ別のブラウザの置き場を使う（2026-11-23 の事故: 同じ置き場を共有していたとき、
Playwright MCP（playwright-core 1.66）で `install ffmpeg` したら、その版が知らない版のブラウザ（E2E の chromium-1228 など）が
片付けられて、E2E が動かなくなった。`playwright install` は自分の版が知らない版のブラウザを消す）。

| 用途 | 版 | 置き場（data/cache/ 以下） | 入れるもの |
|---|---|---|---|
| E2E（`./deck.sh e2e`・箱の E2E） | frontend の @playwright/test | ms-playwright | chromium・chromium-headless-shell・webkit・ffmpeg |
| Workflow のブラウザ操作ノード | 本体の venv の playwright | ms-playwright-py | chromium |
| Playwright MCP（箱の画面確認係・録画） | data/features/playwright-mcp の playwright-core | ms-playwright-mcp | ffmpeg（録画用） |

導入は利用者が機能と拡張で押したときだけ走る（外部から取得するので自動では走らせない）。
状態は「置き場の中身」と「それぞれの版が求める revision（browsers.json）」を比べて決める。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

E2E_BROWSERS = ("chromium", "chromium-headless-shell", "webkit", "ffmpeg")
PY_BROWSERS = ("chromium",)
MCP_BROWSERS = ("ffmpeg",)

E2E_STORE = "ms-playwright"
PY_STORE = "ms-playwright-py"
MCP_STORE = "ms-playwright-mcp"

FEATURE_NAME = "Playwright のブラウザ（画面の試験・画面確認係・録画）"
GUIDE = f"設定 → 機能と拡張 → {FEATURE_NAME} を導入してください"


class PlaywrightBrowsersError(RuntimeError):
    pass


def _data() -> Path:
    from app.config import data_dir

    return Path(data_dir())


def store_path(store: str, data: Path | None = None) -> Path:
    return (data if data is not None else _data()).resolve() / "cache" / store


def python_browsers_path() -> Path:
    """本体の Python の playwright（Workflow のブラウザ操作）が使う置き場。"""
    return store_path(PY_STORE)


def mcp_browsers_path() -> Path:
    """Playwright MCP が使う置き場（箱から読み取り専用で見える）。"""
    return store_path(MCP_STORE)


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    store: Path
    browsers: tuple[str, ...]
    core: Path | None        # その版の playwright-core（cli.js と browsers.json の場所）。無ければ None
    browsers_json: Path | None
    argv: tuple[str, ...]    # 導入のコマンド（shell は使わない）


def _python_driver() -> Path | None:
    try:
        import importlib.util

        spec = importlib.util.find_spec("playwright")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations))) / "driver" / "package"


def components(data: Path | None = None, *, repo_root: Path | None = None,
               python_driver: Path | None | str = "auto") -> list[Component]:
    root = repo_root or REPO_ROOT
    node = shutil.which("node") or "node"
    frontend = root / "frontend" / "node_modules" / "playwright-core"
    mcp = (data if data is not None else _data()).resolve() / "features" / "playwright-mcp" / "node_modules" / "playwright-core"
    driver = _python_driver() if python_driver == "auto" else python_driver

    def node_core(core: Path, names: tuple[str, ...]) -> tuple[Path | None, Path | None, tuple[str, ...]]:
        if (core / "browsers.json").is_file() and (core / "cli.js").is_file():
            return core, core / "browsers.json", (node, str(core / "cli.js"), "install", *names)
        return None, None, ()

    e2e_core, e2e_json, e2e_argv = node_core(frontend, E2E_BROWSERS)
    mcp_core, mcp_json, mcp_argv = node_core(mcp, MCP_BROWSERS)
    py_json = driver / "browsers.json" if driver and (driver / "browsers.json").is_file() else None
    return [
        Component("e2e", "画面の試験（E2E）", store_path(E2E_STORE, data), E2E_BROWSERS, e2e_core, e2e_json, e2e_argv),
        Component("python", "Workflow のブラウザ操作", store_path(PY_STORE, data), PY_BROWSERS, None, py_json,
                  (sys.executable, "-m", "playwright", "install", *PY_BROWSERS) if py_json else ()),
        Component("mcp", "画面確認係の録画（Playwright MCP）", store_path(MCP_STORE, data), MCP_BROWSERS, mcp_core, mcp_json,
                  mcp_argv),
    ]


def _required(component: Component) -> dict[str, str]:
    """その版が求める revision（名前 → revision）。"""
    try:
        listed = json.loads(component.browsers_json.read_text(encoding="utf-8"))["browsers"]  # type: ignore[union-attr]
    except (OSError, ValueError, KeyError, AttributeError):
        return {}
    return {str(item["name"]): str(item["revision"]) for item in listed
            if isinstance(item, dict) and item.get("name") in component.browsers and item.get("revision")}


def _present(store: Path, name: str, revision: str) -> bool:
    # Playwright は導入が終わったブラウザのフォルダに INSTALLATION_COMPLETE を置く。フォルダ名は名前の - を _ にする。
    return (store / f"{name.replace('-', '_')}-{revision}" / "INSTALLATION_COMPLETE").is_file()


def component_status(component: Component) -> dict:
    """applicable=False: その版が入っていない（frontend の依存・MCP・Python の playwright が無い）ので飛ばす。"""
    required = _required(component)
    applicable = bool(required) and bool(component.argv)
    present = [f"{n}-{r}" for n, r in required.items() if _present(component.store, n, r)]
    missing = [f"{n}-{r}" for n, r in required.items() if not _present(component.store, n, r)]
    return {"id": component.id, "label": component.label, "applicable": applicable, "store": str(component.store),
            "present": present, "missing": missing if applicable else []}


def overview(data: Path | None = None, **kwargs) -> dict:
    items = [component_status(c) for c in components(data, **kwargs)]
    active = [i for i in items if i["applicable"]]
    missing = [f"{i['label']}: {', '.join(i['missing'])}" for i in active if i["missing"]]
    return {"components": items, "available": bool(active), "missing": missing,
            "complete": bool(active) and not missing,
            "any_present": any(i["present"] for i in items)}


def e2e_missing(data: Path | None = None, *, store: Path | None = None, **kwargs) -> list[str]:
    """E2E のブラウザで足りないもの。frontend の依存が無い（比べられない）ときは空。
    store: 利用者が PLAYWRIGHT_BROWSERS_PATH で置き場を変えているとき、その置き場で比べる。"""
    component = components(data, **kwargs)[0]
    if store is not None:
        component = Component(**{**component.__dict__, "store": store})
    return component_status(component)["missing"]


def _env(store: Path) -> dict[str, str]:
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(store)}
    env.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)
    return env


def install_missing(data: Path | None = None, *, run=subprocess.run, **kwargs) -> list[str]:
    """足りない分を、それぞれの版・それぞれの置き場へ入れる（導入も更新も同じ）。入れた部品の id を返す。

    外部から取得する。呼ぶのは利用者のボタン操作のジョブだけ。argv で実行し shell は使わない。"""
    done: list[str] = []
    for component in components(data, **kwargs):
        status = component_status(component)
        if not status["applicable"] or not status["missing"]:
            continue
        component.store.mkdir(parents=True, exist_ok=True)
        try:
            result = run(list(component.argv), env=_env(component.store), capture_output=True, text=True,
                         timeout=1800, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PlaywrightBrowsersError(f"{component.label}のブラウザの導入に失敗しました（{exc}）") from exc
        if result.returncode != 0:
            tail = [ln for ln in (result.stderr or result.stdout or "").strip().splitlines() if ln.strip()]
            raise PlaywrightBrowsersError(
                f"{component.label}のブラウザの導入に失敗しました" + (f"（{tail[-1][-200:]}）" if tail else ""))
        done.append(component.id)
    return done


def main(argv: list[str]) -> int:
    """`python -m app.features.playwright_browsers e2e-check <data_dir> [置き場]`: deck.sh e2e が使う。足りなければ 1。"""
    if len(argv) >= 3 and argv[1] == "e2e-check":
        missing = e2e_missing(Path(argv[2]), store=Path(argv[3]) if len(argv) > 3 and argv[3] else None)
        if missing:
            print(f"E2E のブラウザが足りません（{', '.join(missing)}）。{GUIDE}", file=sys.stderr)
            return 1
        return 0
    print("使い方: e2e-check <data_dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
