from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Literal

from app.config import data_dir

logger = logging.getLogger("control_deck.features")

FeatureAction = Literal["install", "update", "enable", "disable", "uninstall"]

# アドオン定義。npmはユーザー空間のprefix、pipは専用venvへ導入する（どちらもsudo不要）。
FEATURES: dict[str, dict] = {
    # OpenCode は v2（@opencode/cli、bin は opencode2）だけを扱う。v1（opencode-ai）と
    # v1 専用だった OMo は撤去した。
    #
    # 実行ファイルに opencode2 を選ぶのは、PATH 上の外部 v1 を「外部導入の v2」と
    # 誤検出しないため。v1 の bin は opencode だけで、opencode2 を持つのは v2 だけ。
    "opencode-v2": {
        "name": "OpenCode v2",
        "kind": "npm",
        "package": "@opencode/cli",
        "executable": "opencode2",
        "category": "opencode",
        "route_gated": True,
        "summary": "OpenCode画面とAIチャットのcodeモードで使うコーディングエージェント",
    },
    # OpenCode v2 のプラグイン。ControlDeck の管理領域へ npm で入れ、セッションの設定から
    # file:// で読ませる（OpenCode が実行時に取りに行かない）。使う/使わないと、
    # レビュー・相談に使うモデルは OpenCode 画面の設定で選ぶ。
    # 導入は検証済みの版に固定し、更新で最新版へ上げる。
    "opencode-superpowers": {
        "name": "Superpowers（OpenCode v2 プラグイン）",
        "kind": "opencode-plugin",
        "package": "superpowers@git+https://github.com/obra/superpowers.git#v7.0.2",
        "latest_package": "superpowers@git+https://github.com/obra/superpowers.git",
        "module": "superpowers",
        "route_gated": False,
        "summary": "計画→テスト駆動→実装→レビューの進め方をスキルとして使う。使うかどうかは OpenCode の設定で選ぶ",
        "requires": "opencode-v2",
    },
    "opencode-adversarial-review": {
        "name": "Adversarial Review（OpenCode v2 プラグイン）",
        "kind": "opencode-plugin",
        "package": "@capybearista/opencode-adversarial-review@2.0.2",
        "latest_package": "@capybearista/opencode-adversarial-review@latest",
        "module": "@capybearista/opencode-adversarial-review",
        "route_gated": False,
        "summary": "会話を持たない別の文脈で差分をレビューする。使うかどうかとレビューのモデルは OpenCode の設定で選ぶ",
        "requires": "opencode-v2",
    },
    "opencode-auto-advisor": {
        "name": "Auto Advisor（OpenCode v2 プラグイン）",
        "kind": "opencode-plugin",
        "package": "@capybearista/opencode-auto-advisor@2.1.1",
        "latest_package": "@capybearista/opencode-auto-advisor@latest",
        "module": "@capybearista/opencode-auto-advisor",
        "route_gated": False,
        "summary": "行き詰まった場面で別のモデルに相談する。使い方と相談先のモデルは OpenCode の設定で選ぶ",
        "requires": "opencode-v2",
    },
    # OpenCode v2 に足す MCP サーバー。プラグインと同じく管理領域へ npm で版固定して入れ、
    # 使う作業（UI 検証など）を選んだ session にだけ、専用の子エージェントの道具として載せる。
    # 主の会話には載せない（道具の説明だけで数万トークンを使うため）。
    "playwright-mcp": {
        "name": "Playwright MCP（UI 検証）",
        "kind": "opencode-mcp",
        "package": "@playwright/mcp@0.0.84",
        "latest_package": "@playwright/mcp@latest",
        "module": "@playwright/mcp",
        "route_gated": False,
        "summary": "AI が開発中の画面を実際のブラウザ（このPCの Chrome）で開き、幅を変えて操作・撮影する。OpenCode の「UI 検証」で使う",
        "requires": "opencode-v2",
    },
    # 版に合った公式文書を引く（精密調査）。問い合わせは外部（context7.com）へ出るので、
    # 導入しても OpenCode の設定で明示的にオンにするまで session には載せない。
    "context7-mcp": {
        "name": "Context7 MCP（ライブラリ文書）",
        "kind": "opencode-mcp",
        "package": "@upstash/context7-mcp@4.4.4",
        "latest_package": "@upstash/context7-mcp@latest",
        "module": "@upstash/context7-mcp",
        "route_gated": False,
        "summary": "使っているライブラリの版に合った公式文書を AI が引けるようにする。問い合わせは外部（context7.com）へ送られる",
        "requires": "opencode-v2",
    },
    # 構文で探す検索（レビューの影響範囲の洗い出し）。OpenCode の session の PATH に足す。
    "ast-grep": {
        "name": "ast-grep（構文検索・レビュー）",
        "kind": "npm",
        "package": "@ast-grep/cli@0.51.0",
        "latest_package": "@ast-grep/cli@latest",
        "executable": "ast-grep",
        "category": "opencode",
        "route_gated": False,
        "summary": "関数の呼び出し元や同じ形のコードを構文で探す。OpenCode の「レビュー」で影響範囲を洗い出すのに使う",
        "requires": "opencode-v2",
    },
    # 検索と収集（docs/PLAN-web-collector.md）。本体の venv を汚さず、機能の専用 venv に版を固定して入れる。
    # ControlDeck は取得済みの HTML を子プロセス（app/web_collector/worker.py）に渡して本文を取り出す。
    "web-extract": {
        "name": "Web 本文抽出（Trafilatura）",
        "kind": "python-lib",
        "packages": ["trafilatura==2.3.1"],
        "latest_packages": ["trafilatura"],
        "module": "trafilatura",
        "distribution": "trafilatura",
        "category": "search",
        "route_gated": False,
        "summary": "Web ページから本文・見出し・表・リンクを Markdown で取り出す。Deep Research・Workflow・特許の調査で使う。未導入なら従来の簡易な抽出",
    },
    "web-robots": {
        "name": "robots.txt 解析（Protego）",
        "kind": "python-lib",
        "packages": ["protego==0.7.0"],
        "latest_packages": ["protego"],
        "module": "protego",
        "distribution": "protego",
        "category": "search",
        "route_gated": False,
        "summary": "サイトの巡回で robots.txt（Google の仕様のワイルドカードを含む）を守る。巡回（準備中）で使う",
    },
    # ニュースの画像生成（docs/implementation-status.md 2026-11-23）。専用 venv（OpenVINO の CPU 実行だけ。torch・CUDA・ROCm は入れない）と、
    # 軽いモデル（SDXS-512 DreamShaper + TAESD、約 1.7GB。data/models/news-imagegen）。導入ボタンでモデルも取得する。
    "news-imagegen": {
        "name": "ニュースの画像生成（CPU）",
        "kind": "python-lib",
        "packages": ["openvino==2026.4.1", "tokenizers==0.23.3", "numpy==2.5.5", "pillow==13.0.0", "huggingface_hub==2.4.0"],
        "latest_packages": ["openvino", "tokenizers", "numpy", "pillow", "huggingface_hub"],
        "module": "openvino",
        "distribution": "openvino",
        "models": "news-imagegen",
        "route_gated": False,
        "summary": "画像の無いニュース記事に、見出しからイメージ画像を作る。CPU と RAM だけで動き、作り終えたらすぐ解放する（GPU は使わない）。モデル約 1.7GB も一緒に取得。設定 → ニュースで入にする",
    },
    "web-browser": {
        "name": "Web 収集のブラウザ（この PC の Chrome）",
        "kind": "system-browser",
        "category": "search",
        "route_gated": False,
        "summary": "JavaScript が要るページを、この PC の Google Chrome で開く（別のブラウザは入れない）。毎回まっさらの一時プロファイルで、いつものログイン・Cookie は使わない",
    },
    "searxng": {
        "name": "SearXNG（Web 検索エンジン）",
        "kind": "managed-script",
        "script": "scripts/setup-searxng.sh",
        "install_dir": "~/.local/share/searxng",
        "marker": "run.sh",
        "category": "search",
        "route_gated": False,
        "summary": "このPCで動くメタ検索エンジン。Deep Research・Workflow の Web 検索が使い、検索の時だけ起動する。起動・停止は Apps",
    },
    # Playwright のブラウザ。3 つの版（E2E・本体の Python・Playwright MCP）が別々の置き場を使う（app/features/playwright_browsers.py）。
    # 外部から取得するので、導入は利用者のボタン操作のときだけ（自動更新にも入れない）。
    "playwright-browsers": {
        "name": "Playwright のブラウザ（画面の試験・画面確認係・録画）",
        "kind": "playwright-browsers",
        "category": "search",
        "route_gated": False,
        "summary": "画面の試験（./deck.sh e2e）、Workflow のブラウザ操作、画面確認係の録画で使うブラウザ。版ごとに別の置き場（data/cache/ms-playwright・"
                   "ms-playwright-py・ms-playwright-mcp）へ入れ、互いを消さない。全部で約 1GB（chromium・webkit など）",
    },
    "pyinstaller": {
        "name": "アプリビルド環境（単一バイナリ）",
        "kind": "pip",
        "package": "pyinstaller",
        "executable": "pyinstaller",
        # 使うのはApp Studioの書き出し時だけなので、導入すればそのまま使える。
        "route_gated": False,
        "summary": "App Studioで、配布先にPython不要の単一バイナリを作れるようにする",
    },
    # GPU推論ランタイム。実体の取得・展開は models_mgmt 側が持ち、ここからは
    # 初期セットアップ（導入）と最新リリースへの更新だけを扱う。
    "llama-cpp": {
        "name": "llama.cpp ランタイム（Vulkan / ROCm 10）",
        "kind": "gpu-runtime",
        "executable": "llama-server",
        "route_gated": False,
        "repo": "souten-yd/llama-builder",
        "summary": "GGUFモデルを動かすローカルLLMランタイム。ROCmビルドはROCm 10系へ統一",
    },
    "maya-s": {
        "name": "Project Maya（R9700）",
        "kind": "gpu-runtime",
        "executable": "maya.sh",
        "route_gated": False,
        "repo": "mw00/project-maya",
        "summary": "Maya-S（約96.5GB）専用の独立推論エンジン。導入時はコードのみ。モデルは別途明示して取得",
    },
    "radiance": {
        "name": "Radiance（AMD GPU 推論サーバー / R9700）",
        "kind": "gpu-runtime",
        "executable": "radiance",
        "route_gated": False,
        "repo": "StillDeadcode/radiance",
        "summary": "Qwen3.8-27B などを .rad 形式で速く動かす推論サーバー。導入時に ROCm でビルド。モデルは別途用意",
    },
    "strata": {
        "name": "Strata（ローカルLLM Terminal / R9700）",
        "kind": "gpu-runtime",
        "executable": "strata",
        "route_gated": False,
        "repo": "Niko1221/Strata",
        "summary": "公式Strata Terminalを管理領域へ導入。モデル取得・起動は任意操作、既存ランタイムは変更しません",
    },
    # 音声エンジン。実体・保管庫は app.speech が持つ。使うエンジンの選択は Models の「TTS / ASR」。
    # 上流（Supertone）は 2026 に開発・サポートを終えたので、導入時にソース・重み・wheel を
    # data/archives/supertonic-3 へ丸ごと保管し、そこから入れる（2026-11-19 利用者の依頼）。
    "supertonic-3": {
        "name": "Supertonic 3（TTS・音声合成）",
        "kind": "speech-engine",
        "executable": "python",
        "route_gated": False,
        "repo": "supertone-oss-archive/supertonic",
        "summary": "31 言語の音声合成（CPU）。導入時に音声モデルも入れ、ソース一式を保管庫へ保存する。使うかは Models の TTS / ASR で選ぶ",
    },
}


from app.features import system_packages as _system_packages  # noqa: E402

FEATURES.update(_system_packages.FEATURES)


# 画面での分け方。導入・更新の仕組み（kind）ではなく、利用者から見た用途で分ける。
CATEGORIES = {
    "opencode": "OpenCode（開発）",
    "runtime": "LLM ランタイム（GPU）",
    "addon": "拡張アプリ",
    "speech": "音声（TTS / ASR）",
    "search": "検索と収集",
    "tools": "その他の道具",
}


def category(feature_id: str) -> str:
    spec = FEATURES[feature_id]
    if spec.get("category") in CATEGORIES:
        return str(spec["category"])
    kind = spec.get("kind")
    if kind in ("opencode-plugin", "opencode-mcp"):
        return "opencode"
    if kind == "gpu-runtime":
        return "runtime"
    if kind == "release-bundle":
        return "addon"
    if kind == "speech-engine":
        return "speech"
    return "tools"


def _release_catalog() -> dict[str, dict]:
    path = Path(__file__).with_name("trusted-catalog.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("trusted feature catalog is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("trusted feature catalog root must be an object")
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, dict)}


FEATURES.update(_release_catalog())
# package.json を導入の印にする npm の種類（実行ファイルを PATH に持たない）。
NPM_MODULE_KINDS = ("opencode-plugin", "opencode-mcp")
NPM_PACKAGES = {key: value["package"] for key, value in FEATURES.items() if value["kind"] in ("npm", *NPM_MODULE_KINDS)}
KNOWN_FEATURES = set(FEATURES)


class FeatureError(RuntimeError):
    pass


def _features_root() -> Path:
    root = (data_dir() / "features").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path() -> Path:
    return _features_root() / "state.json"


def _read_state() -> dict:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    path = _state_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    invalidate_feature_cache()   # 変えた直後に古い一覧を返さない


def _known(feature_id: str) -> str:
    if feature_id not in KNOWN_FEATURES:
        raise FeatureError(f"未知のfeatureです: {feature_id}")
    return feature_id


def _feature_root(feature_id: str) -> Path:
    root = (_features_root() / _known(feature_id)).resolve()
    if not root.is_relative_to(_features_root()):
        raise FeatureError("feature pathがdata directory外です")
    return root


def _managed_executable(feature_id: str) -> Path:
    spec = FEATURES[_known(feature_id)]
    root = _feature_root(feature_id)
    if spec["kind"] == "gpu-runtime":
        # 実体は data/runtimes/ 配下。features root は使わない。
        from app.models_mgmt import llama, strata, maya_s

        if feature_id == "llama-cpp":
            return llama.server_path()
        if feature_id == "radiance":
            from app.models_mgmt import radiance

            return radiance.entrypoint()
        if feature_id == "maya-s":
            return maya_s.entrypoint()
        return strata.binary_path()
    if spec["kind"] == "release-bundle":
        from app.features import release_bundle

        selected = release_bundle.current(feature_id)
        return root / "current" / selected[0].entrypoint if selected else root / "current" / "missing"
    if spec["kind"] == "pip":
        return root / "venv" / "bin" / spec["executable"]
    if spec["kind"] == "python-lib":
        return root / "venv" / "bin" / "python"
    if spec["kind"] == "managed-script":
        from app.features import search_kinds

        return search_kinds.script_marker(spec)
    if spec["kind"] == "system-browser":
        from app.web_collector import browser

        return Path(browser.system_chrome() or "/nonexistent/google-chrome")
    if spec["kind"] in NPM_MODULE_KINDS:
        # プラグインと MCP サーバーは PATH 上の実行ファイルを持たない。導入済みの印は package.json。
        return root / "node_modules" / spec["module"] / "package.json"
    return root / "node_modules" / ".bin" / spec["executable"]


def plugin_path(feature_id: str) -> Path | None:
    """導入済み OpenCode プラグインのディレクトリ。未導入なら None。"""
    spec = FEATURES.get(feature_id)
    if not spec or spec["kind"] != "opencode-plugin":
        return None
    marker = _managed_executable(feature_id)
    return marker.parent if marker.is_file() else None


def mcp_entry(feature_id: str) -> Path | None:
    """導入済み MCP サーバーの起動スクリプト（package.json の bin）。未導入なら None。"""
    spec = FEATURES.get(feature_id)
    if not spec or spec["kind"] != "opencode-mcp":
        return None
    marker = _managed_executable(feature_id)
    try:
        meta = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    binary = meta.get("bin")
    if isinstance(binary, dict):
        binary = next(iter(binary.values()), None)
    if not isinstance(binary, str) or not binary:
        return None
    entry = (marker.parent / binary).resolve()
    # 管理領域の外を指す bin は使わない。
    if not entry.is_file() or not entry.is_relative_to(marker.parent.resolve()):
        return None
    return entry


def _python_lib_version(spec: dict, python: Path) -> str:
    """専用 venv の中のライブラリの版（import できなければ空）。"""
    code = (f"import {spec['module']}, importlib.metadata as m; print(m.version({spec['distribution']!r}))")
    try:
        result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()[:80] if result.returncode == 0 else ""


def executable(feature_id: str) -> Path | None:
    spec = FEATURES[_known(feature_id)]
    if spec["kind"] in ("playwright-browsers", "system-packages"):
        return None   # 実行ファイルを持たない（置き場・OS のパッケージを状態で見る）
    if spec["kind"] == "speech-engine":
        from app.speech import supertonic

        return supertonic.python_path() if supertonic.ready() else None
    if spec["kind"] == "gpu-runtime":
        # PATH 上の同名バイナリを拾わない。ControlDeck が導入した実体だけを扱う。
        managed = _managed_executable(feature_id)
        return managed.resolve() if managed.is_file() and os.access(managed, os.X_OK) else None
    if spec["kind"] == "release-bundle":
        managed = _managed_executable(feature_id)
        return managed.resolve() if managed.is_file() and os.access(managed, os.X_OK) else None
    if spec["kind"] in NPM_MODULE_KINDS:
        # 管理領域に入れたものだけを扱う（外部の同名パッケージは拾わない）。
        managed = _managed_executable(feature_id)
        return managed.resolve() if managed.is_file() else None
    if spec["kind"] in ("managed-script", "system-browser"):
        managed = _managed_executable(feature_id)
        return managed if managed.is_file() else None
    if spec["kind"] == "python-lib":
        # venv の python は symlink。resolve すると venv の外の python を指すので解決しない。
        managed = _managed_executable(feature_id)
        return managed if managed.is_file() and _python_lib_version(spec, managed) else None
    binary_name = spec["executable"]
    managed = _managed_executable(feature_id)
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed.resolve()
    saved = str(_read_state().get(feature_id, {}).get("external_executable") or "")
    if saved:
        candidate = Path(saved).expanduser().resolve()
        if candidate.name == binary_name and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    external = shutil.which(binary_name)
    return Path(external).resolve() if external else None


def _gpu_runtime_status(feature_id: str, spec: dict) -> dict:
    """GPUランタイムはバイナリの --version ではなく、ランタイム側の状態を正とする。"""
    from app.features import gpu_runtime

    detail = gpu_runtime.status(feature_id)
    binary = executable(feature_id)
    return {
        "id": feature_id,
        "name": spec["name"],
        "summary": spec["summary"],
        "kind": spec["kind"],
        "category": category(feature_id),
        "route_gated": spec["route_gated"],
        "requires": spec.get("requires", ""),
        "preview": bool(spec.get("preview", False)),
        "requires_installed": True,
        "repo": str(spec.get("repo", "")),
        "available": bool(detail["available"]),
        "installed": bool(detail["installed"]),
        "managed": bool(detail["managed"]),
        # route_gated ではないので、導入＝利用可能。有効化の一手間を求めない。
        "enabled": bool(detail["installed"] and detail["healthy"]),
        "requested_enabled": True,
        "version": detail["version"],
        "health": "healthy" if detail["healthy"] else ("error" if detail["installed"] else "not-installed"),
        "error": detail["error"],
        "executable": str(binary) if binary else "",
        "runtime": detail["detail"],
    }


def _speech_engine_status(feature_id: str, spec: dict) -> dict:
    """音声エンジンは app.speech の状態を正とする（導入＝利用可能。有効化の一手間を求めない）。"""
    from app.speech import supertonic

    detail = supertonic.status()
    archive = detail["archive"]
    # 導入の健全性は「入っているか・保管庫が揃っているか」で見る。1 回の合成の失敗（読めない文字など）は導入の壊れではない
    # ので出さない（2026-11-19 本番: ☀️ の読み上げ失敗で「修復が必要」と出て再導入を促していた）。合成の失敗は自己診断に出す
    error = archive["error"] if detail["installed"] else ""
    return {
        "id": feature_id,
        "name": spec["name"],
        "summary": spec["summary"],
        "kind": spec["kind"],
        "category": category(feature_id),
        "route_gated": False,
        "requires": "",
        "preview": False,
        "requires_installed": True,
        "repo": str(spec.get("repo", "")),
        "available": True,
        "installed": bool(detail["installed"]),
        # 上流が終わっているので「更新」は無い。管理導入ではあるが更新ボタンは出さない（画面側で kind を見る）
        "managed": bool(detail["installed"]),
        "enabled": bool(detail["installed"]),
        "requested_enabled": True,
        "version": "1.3.1" if detail["installed"] else "",
        "health": "healthy" if detail["installed"] and not error else (
            "error" if detail["installed"] else "not-installed"),
        "error": error if detail["installed"] else "",
        "executable": str(supertonic.python_path()) if detail["installed"] else "",
        "archive": archive,
    }


def _plain_status(feature_id: str, spec: dict, **fields) -> dict:
    """バイナリを持たない種類（Playwright のブラウザ・OS のパッケージ）の状態の共通の形。"""
    installed = bool(fields.pop("installed"))
    error = str(fields.pop("error", ""))
    return {
        "id": feature_id, "name": spec["name"], "summary": spec["summary"], "kind": spec["kind"],
        "category": category(feature_id), "route_gated": False, "requires": "", "preview": False,
        "requires_installed": True, "available": bool(fields.pop("available", True)), "installed": installed,
        "managed": bool(fields.pop("managed", False)), "enabled": installed, "requested_enabled": True,
        "version": "", "health": "healthy" if installed else "not-installed", "error": error, "executable": "",
        **fields,
    }


def _playwright_browsers_status(feature_id: str, spec: dict) -> dict:
    from app.features import playwright_browsers

    detail = playwright_browsers.overview()
    return _plain_status(
        feature_id, spec, installed=detail["complete"], available=detail["available"],
        # 更新ボタンの入口。比べられる版が 1 つでもあれば、足りない分を入れ直せる
        managed=detail["available"], components=detail["components"],
        error=("足りない: " + " / ".join(detail["missing"])) if detail["missing"] else "",
    )


def _system_packages_status(feature_id: str, spec: dict) -> dict:
    from app.features import system_packages

    missing = system_packages.missing_packages(feature_id)
    return _plain_status(
        feature_id, spec, installed=not missing, missing_packages=missing,
        # 画面はこれをコピーさせる。ボタンで sudo は実行しない
        install_command=system_packages.install_command(feature_id),
    )


def status(feature_id: str) -> dict:
    spec = FEATURES[_known(feature_id)]
    if spec["kind"] == "gpu-runtime":
        return _gpu_runtime_status(feature_id, spec)
    if spec["kind"] == "speech-engine":
        return _speech_engine_status(feature_id, spec)
    if spec["kind"] == "playwright-browsers":
        return _playwright_browsers_status(feature_id, spec)
    if spec["kind"] == "system-packages":
        return _system_packages_status(feature_id, spec)
    state = _read_state().get(feature_id, {})
    binary = executable(feature_id)
    # この PC の Chrome は ControlDeck の管理ではない（更新・削除のボタンを出さない）
    managed = _managed_executable(feature_id).is_file() and spec["kind"] != "system-browser"
    version = ""
    healthy = False
    error = ""
    release_enabled: bool | None = None
    if spec["kind"] == "release-bundle":
        from app.features import release_bundle
        from app.addons import registry as addon_registry

        selected = release_bundle.current(feature_id)
        managed = selected is not None
        binary = _managed_executable(feature_id) if selected else None
        installed = selected is not None
        version = selected[0].version if selected else ""
        healthy, error = release_bundle.health(feature_id, selected[0]) if selected else (False, "")
        if selected:
            try:
                release_enabled = bool(addon_registry.status(str(spec["addon_id"]))["enabled"])
            except addon_registry.AddonRegistryError:
                release_enabled = False
    elif spec["kind"] in NPM_MODULE_KINDS and binary is not None:
        try:
            meta = json.loads(binary.read_text(encoding="utf-8"))
            version = str(meta.get("version") or "")[:80]
            healthy = True
        except (OSError, json.JSONDecodeError):
            error = "プラグインの package.json を読めません"
    elif binary is not None and spec["kind"] == "managed-script":
        from app.features import search_kinds

        version = search_kinds.script_version(spec)
        healthy = True
    elif binary is not None and spec["kind"] == "system-browser":
        from app.web_collector import browser

        version = browser.chrome_version()
        healthy = bool(version)
        if not healthy:
            error = "Chrome の版を確かめられません"
    elif binary is not None and spec["kind"] == "python-lib":
        version = _python_lib_version(spec, binary)
        healthy = bool(version)
        if not healthy:
            error = f"{spec['module']} を読み込めません。再導入してください"
        elif spec.get("models"):
            from app.news import imagegen

            if not imagegen.models_ready():
                healthy = False
                error = "モデルが揃っていません。「導入」を押すと取得し直します"
    elif binary is not None:
        try:
            result = subprocess.run(
                [str(binary), "--version"], capture_output=True, text=True, timeout=10, check=False,
            )
            healthy = result.returncode == 0
            lines = (result.stdout or result.stderr).strip().splitlines()
            version = lines[0][:80] if healthy and lines else ""
            if not healthy:
                error = "version確認に失敗しました"
        except (OSError, subprocess.TimeoutExpired):
            error = "実行ファイルを起動できません"
    installed = binary is not None if spec["kind"] != "release-bundle" else installed
    toolchain = (
        True if spec["kind"] == "release-bundle"
        else shutil.which("npm") if spec["kind"] in ("npm", *NPM_MODULE_KINDS)
        else shutil.which("git") if spec["kind"] == "managed-script" else shutil.which("python3")
    )
    # route_gatedでないアドオンは、導入＝利用可能（有効化の一手間と再読み込みを求めない）。
    enabled = (
        release_enabled is True and installed and healthy
        if spec["kind"] == "release-bundle"
        else (installed and healthy) if not spec["route_gated"]
        else bool(state.get("enabled")) and installed and healthy
    )
    return {
        "id": feature_id,
        "name": spec["name"],
        "summary": spec["summary"],
        "kind": spec["kind"],
        "category": category(feature_id),
        "route_gated": spec["route_gated"],
        # 依存アドオン。未導入なら UI で導入順を案内する。
        "requires": spec.get("requires", ""),
        "preview": bool(spec.get("preview", False)),
        "requires_installed": (
            True if not spec.get("requires")
            else executable(spec["requires"]) is not None
        ),
        "available": toolchain is not None or installed,
        "installed": installed,
        "managed": managed,
        "enabled": enabled,
        "requested_enabled": release_enabled if release_enabled is not None else (
            bool(state.get("enabled")) or not spec["route_gated"]
        ),
        "version": version,
        "health": "healthy" if healthy else ("error" if installed else "not-installed"),
        "error": error,
        "executable": str(binary) if binary else "",
    }


# 一覧の結果を少しだけ覚えておく。
#
# status() は feature ごとに systemctl を起動して健全性を確かめる。7 件で
# subprocess が 10 回、実測 430ms かかっていた。/api/v1/meta がこれを呼び、
# 画面は meta を待ってから描き始めるので、開くたびに 0.4 秒何も出ない時間が
# できていた。携帯では往復のぶん更に伸びる。
#
# 状態が変わるのは導入・有効化・無効化・更新のときだけで、そのときは
# _write_state から捨てる。取りこぼしても数秒で消えるので、古い値が居座らない。
_LIST_CACHE_TTL_SECONDS = 5.0
_list_cache: tuple[float, list[dict]] | None = None


def invalidate_feature_cache() -> None:
    global _list_cache
    _list_cache = None


def list_features() -> list[dict]:
    global _list_cache
    now = time.monotonic()
    if _list_cache is not None and now - _list_cache[0] < _LIST_CACHE_TTL_SECONDS:
        return [dict(item) for item in _list_cache[1]]
    values = [status(feature_id) for feature_id in sorted(KNOWN_FEATURES)]
    _list_cache = (now, [dict(item) for item in values])
    return values


def is_enabled(feature_id: str) -> bool:
    try:
        return bool(status(feature_id)["enabled"])
    except FeatureError:
        return False


# OpenCode は v2 だけ。ルート・ノード・カタログの登録はこれで判断する。
OPENCODE_FEATURES = ("opencode-v2",)


def opencode_enabled() -> bool:
    return any(is_enabled(feature_id) for feature_id in OPENCODE_FEATURES)


def _npm_install(feature_id: str, package: str) -> subprocess.CompletedProcess[str]:
    root = _feature_root(feature_id)
    npm = shutil.which("npm")
    if npm is None:
        raise FeatureError("npmが必要です")
    root.mkdir(parents=True, exist_ok=True)
    # Playwright 系はブラウザ本体を取りに行かない。このPCの Chrome を使う。
    env = {**os.environ, "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}
    return subprocess.run(
        [npm, "install", "--prefix", str(root), "--no-fund", "--no-audit", package],
        capture_output=True, text=True, timeout=600, check=False, env=env,
    )


def _pip_install(feature_id: str, package: str | list[str]) -> subprocess.CompletedProcess[str]:
    """アドオン専用venvへ導入する。Control Deck本体のvenvは汚さない。"""
    root = _feature_root(feature_id)
    python = shutil.which("python3")
    if python is None:
        raise FeatureError("python3が必要です")
    venv = root / "venv"
    if not (venv / "bin" / "pip").is_file():
        root.mkdir(parents=True, exist_ok=True)
        created = subprocess.run(
            [python, "-m", "venv", str(venv)], capture_output=True, text=True, timeout=300, check=False,
        )
        if created.returncode != 0:
            return created
    return subprocess.run(
        [str(venv / "bin" / "pip"), "install", "--upgrade", "--disable-pip-version-check",
         *([package] if isinstance(package, str) else package)],
        capture_output=True, text=True, timeout=900, check=False,
    )


def _install_package(feature_id: str, *, latest: bool) -> subprocess.CompletedProcess[str]:
    spec = FEATURES[_known(feature_id)]
    if spec["kind"] == "python-lib":
        return _pip_install(feature_id, spec["latest_packages"] if latest else spec["packages"])
    if spec["kind"] == "system-browser":
        raise FeatureError("この PC の Chrome を使います（ControlDeck からは入れません。OS のパッケージで導入・更新してください）")
    if spec["kind"] == "managed-script":
        from app.features import search_kinds

        try:
            return search_kinds.script_install(spec, latest=latest)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FeatureError(f"{spec['name']}の導入に失敗しました: {exc}") from exc
    package = spec["package"]
    if spec["kind"] == "pip":
        return _pip_install(feature_id, package)
    if spec["kind"] in NPM_MODULE_KINDS:
        # 版を固定した指定に @latest は付けられない。更新用の指定を別に持つ。
        return _npm_install(feature_id, spec["latest_package"] if latest else package)
    if latest and spec.get("latest_package"):
        return _npm_install(feature_id, spec["latest_package"])
    return _npm_install(feature_id, f"{package}@latest" if latest else package)


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    """失敗の理由（出力の最後の行）。利用者が直せるように短く添える。"""
    lines = [line for line in (result.stderr or result.stdout or "").strip().splitlines() if line.strip()]
    return f"（{lines[-1][-200:]}）" if lines else ""


def install(feature_id: str) -> dict:
    name = FEATURES[_known(feature_id)]["name"]
    if FEATURES[feature_id]["kind"] in ("gpu-runtime", "speech-engine"):
        # 取得がネットワークI/Oのため、routerがasyncのまま gpu_runtime / speech を直接呼ぶ。
        raise FeatureError(f"{name}の導入は非同期ジョブから実行してください")
    if FEATURES[feature_id]["kind"] == "system-packages":
        from app.features import system_packages

        raise FeatureError(f"{name}は sudo が要るのでボタンでは入れられません。ターミナルで {system_packages.guide(feature_id)}")
    if FEATURES[feature_id]["kind"] == "playwright-browsers":
        return _install_playwright_browsers()
    required = FEATURES[feature_id].get("requires")
    if required and not status(required)["installed"]:
        raise FeatureError(f"先に{FEATURES[required]['name']}を導入してください")
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.features import release_bundle

        try:
            release_bundle.install(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        return status(feature_id)
    result = _install_package(feature_id, latest=False)
    if result.returncode != 0 or not _managed_executable(feature_id).is_file():
        raise FeatureError(f"{name}の管理導入に失敗しました{_tail(result)}")
    if FEATURES[feature_id].get("models"):
        _download_models(feature_id)
    _autoconfigure(feature_id)
    return status(feature_id)


def _download_models(feature_id: str) -> None:
    """機能に付くモデルを、専用 venv の python（台本の download）で取得する（Hugging Face の公式だけ。取得済みは飛ばす）。"""
    from app.news import imagegen, imagegen_worker

    name = FEATURES[feature_id]["name"]
    python = _managed_executable(feature_id)
    models = imagegen.models_dir()
    try:
        result = subprocess.run(
            [str(python), "-I", str(Path(imagegen_worker.__file__).resolve()), "download", str(models)],
            capture_output=True, text=True, timeout=3600, check=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(data_dir()), "HF_HUB_DISABLE_TELEMETRY": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeatureError(f"{name}のモデルを取得できませんでした: {exc}") from exc
    if result.returncode != 0 or not imagegen.models_ready(models):
        raise FeatureError(f"{name}のモデルの取得に失敗しました{_tail(result)}")


def _install_playwright_browsers() -> dict:
    """足りない分を、それぞれの版・それぞれの置き場へ入れる（導入と更新で同じ）。外部から取得する。"""
    from app.features import playwright_browsers

    if not playwright_browsers.overview()["available"]:
        raise FeatureError("入れる先の Playwright が見つかりません（画面の依存・本体の playwright・Playwright MCP のどれも未導入）")
    try:
        playwright_browsers.install_missing()
    except playwright_browsers.PlaywrightBrowsersError as exc:
        invalidate_feature_cache()
        raise FeatureError(str(exc)) from exc
    invalidate_feature_cache()
    result = status("playwright-browsers")
    if not result["installed"]:
        raise FeatureError(f"導入しましたが、まだ足りないものがあります（{result['error']}）")
    return result


def _autoconfigure(feature_id: str) -> None:
    """導入直後に通信できる状態へ整える。

    接続先やAPIキーを手で入れさせない。失敗しても導入自体は成功扱いにする
    （後から設定画面で直せるため、ここで導入を巻き戻す方が不便）。
    """
    try:
        if feature_id == "opencode-v2":
            from app.integrations.opencode.provider import autoconfigure

            autoconfigure()
    except Exception:  # noqa: BLE001 - 自動設定の失敗で導入を失敗にしない
        logger.exception("%sの自動設定に失敗しました", feature_id)


def update(feature_id: str) -> dict:
    """管理導入のランタイムを最新版へ更新する。外部PATH上の実体には触れない。"""
    name = FEATURES[_known(feature_id)]["name"]
    if FEATURES[feature_id]["kind"] == "gpu-runtime":
        raise FeatureError(f"{name}の更新は非同期ジョブから実行してください")
    if FEATURES[feature_id]["kind"] == "speech-engine":
        raise FeatureError(f"{name}は開発が終了しているため更新はありません（保管庫の版を使います）")
    if FEATURES[feature_id]["kind"] == "playwright-browsers":
        previous = status(feature_id)["version"]
        return {**_install_playwright_browsers(), "previous_version": previous}
    if FEATURES[feature_id]["kind"] == "system-packages":
        raise FeatureError(f"{name}は OS のパッケージです。ターミナルで sudo apt update && sudo apt upgrade してください")
    if not _managed_executable(feature_id).is_file():
        raise FeatureError(f"Control Deckが導入した{name}のみ更新できます")
    previous = status(feature_id)["version"]
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.features import release_bundle

        try:
            result = release_bundle.install(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        return {**status(feature_id), "previous_version": result["previous_version"] or previous}
    result = _install_package(feature_id, latest=True)
    if result.returncode != 0 or not _managed_executable(feature_id).is_file():
        raise FeatureError(f"{name}の更新に失敗しました{_tail(result)}")
    return {**status(feature_id), "previous_version": previous}


def enable(feature_id: str) -> dict:
    current = status(feature_id)
    if FEATURES[feature_id]["kind"] in ("gpu-runtime", "speech-engine"):
        # route_gated ではないので導入＝有効。切り替えるべき状態を持たない。
        return current
    if FEATURES[feature_id]["kind"] == "release-bundle" and current["installed"]:
        from app.features import release_bundle

        try:
            release_bundle.ensure_running(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        current = status(feature_id)
    if not current["installed"] or current["health"] != "healthy":
        raise FeatureError(f"正常な{FEATURES[feature_id]['name']}を先に導入してください")
    state = _read_state()
    remembered = "" if current["managed"] else current["executable"]
    state[feature_id] = {
        **state.get(feature_id, {}), "enabled": True,
        "external_executable": remembered,
    }
    _write_state(state)
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.addons import registry as addon_registry

        try:
            # Repairing an already enabled service must preserve its capability grants.
            if not current["requested_enabled"]:
                addon_registry.set_enabled(str(FEATURES[feature_id]["addon_id"]), True)
        except addon_registry.AddonRegistryError as exc:
            raise FeatureError(str(exc)) from exc
    # 外部PATHの実体を有効化した場合は install を通らないので、ここでも整える。
    _autoconfigure(feature_id)
    return status(feature_id)


# 本体へ統合して不要になった機能。導入済みなら起動時に片付ける（サービス・Add-on の登録・実行ファイル）。
# 利用者のデータ（data_dir/feature-data/<id>）は消さない。2026-11-21 利用者「PatentBuddy のアドオンは不要。
# アドオンと機能追加画面の導入項目を一式削除し、Patent 機能を付ける」。
RETIRED_FEATURES: dict[str, dict] = {
    "patent-buddy": {"addon_id": "patent-buddy", "kind": "release-bundle", "replaced_by": "Patent 画面（/patents）"},
}
RETIRED_MARK = ".retired"


def retire_removed_features() -> list[str]:
    """RETIRED_FEATURES のうち導入の跡が残っているものを外す。済んだら印を置き、次からは何もしない。"""
    from app.features import release_bundle

    done: list[str] = []
    for feature_id, spec in RETIRED_FEATURES.items():
        root = _features_root() / feature_id
        if not root.exists() or (root / RETIRED_MARK).exists():
            continue
        try:
            release_bundle.uninstall(feature_id, spec)
            state = _read_state()
            if state.pop(feature_id, None) is not None:
                _write_state(state)
            (root / RETIRED_MARK).write_text(json.dumps({"retired_at": time.time(),
                                                         "replaced_by": spec["replaced_by"]}, ensure_ascii=False))
            logger.info("統合済みの機能を片付けました: %s（%s へ）", feature_id, spec["replaced_by"])
            done.append(feature_id)
        except Exception:
            # 片付けに失敗しても Host の起動は止めない。次回の起動でやり直す
            logger.exception("統合済みの機能の片付けに失敗しました: %s", feature_id)
    return done


def restore_enabled_services() -> None:
    """One startup pass over installed, opted-in services; run in a worker thread.

    The Host owns boot restoration so the data mount and registry are available
    before services start. Add-on enabled state is authoritative; never enable an
    opted-out Add-on or re-grant permissions here.
    """
    from app.addons import registry as addon_registry
    from app.features import release_bundle

    retire_removed_features()
    try:
        from app.patents import store as patent_store

        patent_store.interrupt_unfinished()
        from app.patents import judge as patent_judge

        patent_judge.interrupt_unfinished()
        from app.patents import survey as patent_survey

        patent_survey.interrupt_unfinished()
    except Exception:
        logger.exception("特許の AI 支援の中断の記録に失敗しました")

    try:
        from app.knowledge import essence as knowledge_essence

        knowledge_essence.interrupt_unfinished()
    except Exception:
        logger.exception("知識エッセンスの中断の記録に失敗しました")

    # 作業場の立て直しは箱（podman）を起こすので遅い。サービスの起動復旧を待たせないよう別のスレッドで
    import threading

    def restore_workspaces() -> None:
        try:
            from app.maintenance import workspaces

            workspaces.restore_interrupted()
        except Exception:
            logger.exception("開発の作業場の起動時の立て直し（index.lock・中断の記録・未保存の保存）に失敗しました")

    threading.Thread(target=restore_workspaces, name="workspace-restore", daemon=True).start()

    for feature_id, spec in list(FEATURES.items()):
        if spec["kind"] != "release-bundle":
            continue
        try:
            if release_bundle.current(feature_id) is None:
                continue
            if not addon_registry.status(str(spec["addon_id"]))["enabled"]:
                continue
            release_bundle.ensure_running(feature_id, spec)
            logger.info("有効なfeatureのサービスを起動確認しました: %s", feature_id)
        except Exception:
            # One broken package must not prevent the Host or other Add-ons from starting.
            logger.exception("featureサービスの起動復旧に失敗しました: %s", feature_id)
        finally:
            invalidate_feature_cache()


def disable(feature_id: str) -> dict:
    _known(feature_id)
    if FEATURES[feature_id]["kind"] in ("gpu-runtime", "speech-engine"):
        return status(feature_id)
    state = _read_state()
    state[feature_id] = {**state.get(feature_id, {}), "enabled": False}
    _write_state(state)
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.addons import registry as addon_registry

        try:
            addon_registry.set_enabled(str(FEATURES[feature_id]["addon_id"]), False)
        except addon_registry.AddonRegistryError as exc:
            raise FeatureError(str(exc)) from exc
    return status(feature_id)


def uninstall(feature_id: str) -> dict:
    if FEATURES[_known(feature_id)]["kind"] in ("playwright-browsers", "system-packages"):
        # ブラウザの置き場は E2E・箱・Workflow が共有で使う。OS のパッケージは ControlDeck の管理ではない
        raise FeatureError(f"{FEATURES[feature_id]['name']}は機能と拡張からはアンインストールできません")
    disable(feature_id)
    if FEATURES[_known(feature_id)]["kind"] == "speech-engine":
        from app.speech import supertonic

        supertonic.uninstall()   # 実行環境だけ。保管庫は残す
        return status(feature_id)
    if FEATURES[_known(feature_id)]["kind"] == "gpu-runtime":
        from app.features import gpu_runtime

        try:
            gpu_runtime.uninstall(feature_id)
        except gpu_runtime.GpuRuntimeError as exc:
            raise FeatureError(str(exc)) from exc
        return status(feature_id)
    if FEATURES[_known(feature_id)]["kind"] == "release-bundle":
        from app.features import release_bundle

        try:
            release_bundle.uninstall(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        return status(feature_id)
    if FEATURES[feature_id]["kind"] == "system-browser":
        raise FeatureError("この PC の Chrome は ControlDeck からは消しません")
    if FEATURES[feature_id]["kind"] == "managed-script":
        # 導入先（例 ~/.local/share/searxng）と Apps の登録を巻き込むので、ここからは消さない
        raise FeatureError(f"{FEATURES[feature_id]['name']}は機能と拡張からはアンインストールできません（Apps から管理）")
    if FEATURES[feature_id].get("models"):
        from app.news import imagegen

        imagegen.remove_models()
    root = _feature_root(feature_id)
    if root.exists():
        # 管理prefixだけを削除。PATH上の外部OpenCodeと~/.config/~/.local/shareには触れない。
        if not root.is_relative_to(_features_root()):
            raise FeatureError("削除対象がfeature directory外です")
        shutil.rmtree(root)
    return status(feature_id)


def apply(action: FeatureAction, feature_id: str) -> dict:
    operations = {"install": install, "update": update, "enable": enable,
                  "disable": disable, "uninstall": uninstall}
    return operations[action](feature_id)
