from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from app.config import data_dir
from app.features import registry
from app.files import service as files
from app.integrations.opencode import roles as roles_mod
from app.jobs.service import Job

OPERATIONS = {"analyze", "implement", "fix", "test", "review"}
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_SETTINGS = {
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "llama3.2",
    "project_path": "",
    # OpenCode が使う既定のモデル（`provider/モデル`。Models の「フロンティアモデル」で選ぶ）。空＝従来どおり
    # （作業場は箱の順番・ホストは model）。会話でモデルを選んでいれば、そちらが先（model_choice.py）。
    "default_model": "",
    # ControlDeck の OpenAI 互換ゲートウェイ経由にするか。
    # 経由すると KV の受け入れ制御（混雑時の待機・枯渇時の再試行）が効く。
    "use_gateway": True,
    # 使う OpenCode の系列。v1 を撤去したので常に "v2"（@opencode/cli）。
    "runtime": "v2",
    # 開発ワークフローの拡張（ControlDeck から起動した session にだけ効く）。
    "superpowers": False,
    "adversarial_review": False,
    # 自動レビュー: コードを変えたら、完了の前に子エージェント reviewer で確かめ、行き詰まったら
    # advisor() で相談する、という手順を session の指示に足す（auto-review.md）。
    "auto_review": True,
    # Auto Advisor: off / observe（判断だけ記録）/ active（必要な場面で相談）。v2 のみ。
    "auto_advisor": "off",
    # レビュー・相談に使うモデル（ゲートウェイの名前）。空ならセッションと同じモデル。
    # 別のモデルを指定すると、ゲートウェイが要求に合わせて載せ替える。
    # 旧設定。最終検証（roles.final_check）の指定として読む。roles を保存したら空にする（移行）。
    "review_model": "",
    # 旧設定。読むときに roles.advise へ移す（保存で空になる）。
    "advisor_model": "",
    # 役割ごとのモデル（roles.py）。{plan, build, research, quick_check, final_check, evaluate, advise}、空＝既定。
    "roles": {},
    # 開発担当（実装を任せる子エージェント developer）。モデルは役割「開発」（roles.build）と同じ。
    # 役割には外部モデル（cloud:<プロバイダ>/<モデル>）も選べるが、
    # 利用者が OpenCode からの外部送信を許可し、そのモデルを有効にしているときだけ使う。
    "developer_agent": False,
    # 旧設定。読むときに roles.build が空なら build へ移す（保存で空になる）。
    "developer_model": "",
    # UI 検証（Playwright MCP）。導入済みなら、子エージェント ui-verifier の道具として載せる。
    "ui_verify": True,
    # UI 検証のブラウザに外部サイトへの通信を許すか。既定は手元（loopback）だけ。
    "ui_verify_allow_external": False,
    # 作業場の画面の記録（撮影・動画）を、取り込み（PR）・マージの後に自動で消すか。容量を取るので任意（既定は切）。
    "workspace_media_auto_delete": False,
    # 精密調査（ControlDeck の Deep Research を道具として渡す）。外部検索は別に許可が要る。
    "research_tool": True,
    "research_external": False,
    # OpenCode をどこで動かすか。box: プロジェクトの箱（podman、書けるのはプロジェクトだけ）、
    # host: これまでどおり利用者と同じ権限で。箱が使えない（podman が無い）ときは host。
    "isolation": "box",
    # 開発の作業場: data のある所のディスクの空きの下限（GiB）。下回ると新しい作業場を作らない・
    # ターンの終わりの自動保存を飛ばす（会話に「保存できません」が出る）。下限の 2 倍未満は注意。
    "workspace_min_free_gib": 15,
    # Context7（ライブラリの公式文書）。問い合わせが外部へ出るので既定はオフ。
    "context7": False,
    # この PC で設定済みの gh（GitHub CLI）を session から使わせるか。AI に利用者の GitHub の
    # 権限（repo・workflow など）を渡すことになるので既定はオフ。
    "github_cli": False,
    # AI アシスタントから使うときの権限モード（plugins/controldeck-permission）。
    # auto: 編集・読むだけ・テスト/ビルドは自動、危ない操作と判断できないものは確認。
    # ask: 判断させる操作（shell・edit・write・webfetch・execute）はすべて確認。
    "chat_permission_mode": "auto",
}
CHAT_PERMISSION_MODES = ("auto", "ask")

# 拡張とそれを持つアドオン（設定 → オプション機能で導入・更新・削除する）。
# OpenCode には管理領域のパスを file:// で渡し、実行時に取りに行かせない。
WORKFLOW_FEATURES = {
    "superpowers": "opencode-superpowers",
    "adversarial_review": "opencode-adversarial-review",
    "auto_advisor": "opencode-auto-advisor",
}
AUTO_ADVISOR_MODES = ("off", "observe", "active")
# 作業の道具（MCP サーバー）とそれを持つオプション機能。
WORKFLOW_MCP_FEATURES = {
    "ui_verify": "playwright-mcp",
    "context7": "context7-mcp",
}
# レビューの影響範囲を構文で探す道具（MCP ではなく session の PATH に足す CLI）。
REVIEW_TOOL_FEATURE = "ast-grep"
CONTEXT7_SERVER = "context7"
# レビューの子エージェントはファイルを変えない。
REVIEWER_DENIED = ("edit", "write", "patch")
# UI 検証の子エージェントが持つ道具。OpenCode は MCP の道具名に server 名を前置する。
UI_VERIFY_SERVER = "playwright"
UI_VERIFY_TOOLS = f"{UI_VERIFY_SERVER}_*"
# ブラウザでコードを任意に走らせる道具は使わせない（検証には要らない）。
UI_VERIFY_DENIED = (f"{UI_VERIFY_SERVER}_browser_run_code_unsafe",)
UI_VERIFY_VIEWPORT = "1280x800"

# OpenCode ランタイムと、それを持つアドオン（feature）の対応。v2 だけ。
RUNTIME_FEATURES = {"v2": "opencode-v2"}


class CodeAgentError(RuntimeError):
    pass


def _integration_dir() -> Path:
    root = (data_dir() / "integrations" / "opencode").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_path() -> Path:
    return _integration_dir() / "settings.json"


def get_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            settings.update({key: raw[key] for key in settings if key in raw})
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return _migrate_role_models(settings)


def _migrate_role_models(settings: dict) -> dict:
    """古い設定の developer_model・advisor_model を、役割の表（roles）へ移す（読むときに移す。保存で空になる）。

    developer_model は roles.build が空のときだけ build へ（選んである開発のモデルを上書きしない）。
    advisor_model は roles.advise が空のときだけ advise へ。古い設定は読むたびに同じ結果になるので動き続ける。
    """
    stored = settings.get("roles") if isinstance(settings.get("roles"), dict) else {}
    migrated = dict(stored)
    for old, role in (("developer_model", "build"), ("advisor_model", "advise")):
        value = str(settings.get(old) or "").strip()
        if value and not str(migrated.get(role) or "").strip():
            migrated[role] = value
        settings[old] = ""
    settings["roles"] = migrated
    return settings


def active_runtime(preferred: str = "") -> str:
    """実際に使う OpenCode の系列。v1 を撤去したので常に v2。

    古い設定や呼び出し側が "v1" を渡しても v2 で動かす。
    """
    return "v2"


def active_binary(preferred: str = "") -> tuple[str, Path]:
    """(系列, 実行ファイル)。使えない場合は CodeAgentError を投げる。"""
    runtime = active_runtime(preferred)
    feature_id = RUNTIME_FEATURES[runtime]
    if not registry.is_enabled(feature_id):
        raise CodeAgentError("OpenCode featureが有効ではありません")
    binary = registry.executable(feature_id)
    if binary is None:
        raise CodeAgentError("OpenCodeを利用できません")
    return runtime, binary


def runtime_states() -> dict[str, dict]:
    """系列ごとのアドオン状態。画面の切り替え UI が導入済みかを判断するために使う。"""
    return {name: registry.status(feature_id) for name, feature_id in RUNTIME_FEATURES.items()}


def gateway_base_url() -> str:
    """ControlDeck ゲートウェイの base_url。判定・組み立てはgatewayへ委譲する。"""
    from app.models_mgmt import gateway

    return gateway.base_url()


def is_gateway_url(base_url: str) -> bool:
    from app.models_mgmt import gateway

    return gateway.is_gateway_url(base_url)


def gateway_client_header() -> str:
    from app.models_mgmt import gateway

    return gateway.CLIENT_HEADER


def resolve_backend_port() -> int | None:
    """OpenCode が最終的に到達する llama.cpp のポート。

    ゲートウェイ経由だと base_url は ControlDeck のポートになるため、
    そのままではアイドル判定（どのモデルが使われているか）が引けない。
    ゲートウェイの転送先まで解決して実ポートを返す。
    """
    from urllib.parse import urlsplit

    settings = get_settings()
    base_url = str(settings.get("base_url") or "")
    if is_gateway_url(base_url):
        try:
            from app.models_mgmt.gateway import resolve_endpoint

            return resolve_endpoint(str(settings.get("model") or ""))[1]
        except Exception:  # noqa: BLE001 - 解決できなければ不明として扱う
            return None
    try:
        parsed = urlsplit(base_url)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            return None
        return parsed.port
    except ValueError:
        return None


def autoconfigure(*, model: str = "") -> dict:
    """導入直後に通信できる状態へ自動設定する。

    ユーザーが base_url や APIキーを手で入れなくても使えるようにする。
    ゲートウェイのAPIキーは未発行なら発行し、OpenCode の runtime config へ渡る
    形で保存する。
    """
    from app.models_mgmt import gateway, local_llm

    target = model
    if not target:
        # 特定モデルを名指しせず、転送先の判断はゲートウェイへ任せる。停止中の別モデルを
        # 起こさずに、そのとき動いているモデルへ流れる。どのローカルモデルかも
        # ゲートウェイ側の解決に委ねる（OpenCode はモデル名しか知らなくてよい）。
        target = gateway.AUTO_MODEL if local_llm.llm_instances() else ""
    gateway.get_api_key(create=True)  # 未発行なら発行
    patch = {"base_url": gateway_base_url(), "use_gateway": True}
    if target:
        patch["model"] = target
    return save_settings(patch)


def save_workflow(patch: dict) -> dict:
    """開発ワークフロー拡張の有効/無効と、レビュー・相談に使うモデルを保存する。"""
    from app.models_mgmt import local_llm

    settings = get_settings()
    for key in ("superpowers", "adversarial_review", "ui_verify", "ui_verify_allow_external",
                "research_tool", "research_external", "context7", "github_cli", "developer_agent",
                "auto_review", "workspace_media_auto_delete"):
        if key in patch:
            settings[key] = bool(patch[key])
    if "isolation" in patch:
        if patch["isolation"] not in ("box", "host"):
            raise ValueError("isolation は box / host のどちらかです")
        settings["isolation"] = patch["isolation"]
    if "auto_advisor" in patch:
        if patch["auto_advisor"] not in AUTO_ADVISOR_MODES:
            raise ValueError("Auto Advisor は off / observe / active のいずれかです")
        settings["auto_advisor"] = patch["auto_advisor"]
    if "chat_permission_mode" in patch:
        if patch["chat_permission_mode"] not in CHAT_PERMISSION_MODES:
            raise ValueError("権限モードは auto / ask のどちらかです")
        settings["chat_permission_mode"] = patch["chat_permission_mode"]
    known = {str(item.get("alias")) for item in local_llm.llm_instances()} | {"auto"}

    def check_model(value: str, saved: tuple[str, ...] = ()) -> str:
        """役割に選ぶモデルの検査。保存済みの値をそのまま送り返してきただけなら通す（外部送信の許可を後から
        外しても、ほかの項目の保存まで止めない。使えない外部モデルは session を作るときに外す）。"""
        value = str(value or "").strip()
        if value and value not in saved:
            if value.startswith(CLOUD_PREFIX):
                if not _cloud_usable(value):
                    raise ValueError(
                        "この外部モデルは選べません。Models → フロンティア（外部）でモデルを有効にし、"
                        "OpenCode からの外部への送信を許可してください")
            elif value not in known:
                raise ValueError(f"登録されていないモデルです: {value}")
        return value

    saved_roles = dict(settings.get("roles") or {})
    clean = dict(saved_roles)
    if "roles" in patch:
        incoming = patch["roles"] if isinstance(patch["roles"], dict) else {}
        # 旧 review_model で動いていた利用者の画面は、最終検証の欄に旧値を出す。送り返されたものは通す。
        legacy = str(settings.get("review_model") or "")
        clean = {}
        for role in roles_mod.ROLES:
            value = check_model(incoming.get(role), (str(saved_roles.get(role) or ""),
                                                      legacy if role == "final_check" else ""))
            if value:
                clean[role] = value
        settings["review_model"] = ""   # 最終検証へ移行済み
    # 古い API の値（review_model・advisor_model・developer_model）は役割へ移す（壊さない）。
    #   review_model → 旧設定のまま保つ（最終検証として読む）、advisor_model → advise（画面が advise を送ってこなければ上書き）、
    #   developer_model → build（空のときだけ。選んである開発のモデルを上書きしない）
    incoming_roles = patch["roles"] if isinstance(patch.get("roles"), dict) else {}
    if "review_model" in patch and "roles" not in patch:
        # 旧設定のまま保つ（最終検証の指定として読まれる。roles を保存したら空になる）
        settings["review_model"] = check_model(patch["review_model"], (str(settings.get("review_model") or ""),))
    if "advisor_model" in patch and "advise" not in incoming_roles:
        value = check_model(patch["advisor_model"], (str(saved_roles.get("advise") or ""),))
        if value:
            clean["advise"] = value
        else:
            clean.pop("advise", None)
    if "developer_model" in patch:
        value = check_model(patch["developer_model"], (str(saved_roles.get("build") or ""),))
        if value and not clean.get("build"):
            clean["build"] = value
    if clean != saved_roles or "roles" in patch:
        settings["roles"] = clean
    settings["developer_model"] = ""
    settings["advisor_model"] = ""
    if "roles" in patch:
        # 役割の保存では検査を通す（作る設定でも同じ検査を通る）
        role_resolution(settings, str(settings.get("model") or ""), strict=True)
    path = _settings_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return settings


def role_resolution(settings: dict, session_model: str, *, strict: bool = False,
                    local_only: bool = False) -> "roles_mod.Resolution":
    """設定の roles（と旧 review_model）から、役割ごとの実際のモデルを決める（roles.resolve）。

    使えなくなった外部モデル（許可を外した等）は未設定と同じに扱う。秘密の会話のスコープの中は local_only。"""
    from app.security import offline

    stored = settings.get("roles") if isinstance(settings.get("roles"), dict) else {}
    usable = {role: ("" if str(value or "").startswith(CLOUD_PREFIX) and not _cloud_usable(str(value))
                     else str(value or "")) for role, value in stored.items()}
    legacy = role_model(settings, "review_model")
    # ホストの AI アシスタントでは、既定で外部へコードを送らない（外部は役割で明示したときだけ。
    # 2026-11-22 Opus の監査。作業場は利用者が了承したモデルの順番があるので sandbox 側で外部を既定にする）
    return roles_mod.resolve(
        usable, session_model=session_model, first_external="",
        legacy_review=legacy, local_only=local_only or offline.in_confidential_scope(), strict=strict)


CLOUD_PREFIX = "cloud:"


def _cloud_usable(model: str) -> bool:
    """その外部モデルを、いま OpenCode から使ってよいか（送信の許可・有効・選ばれている）。"""
    return any(item["id"] == model for item in _cloud_models_for_opencode())


def role_model(settings: dict, key: str) -> str:
    """役割（レビュー・相談・開発担当）に使うモデル。空ならセッションと同じモデル。

    外部モデルは、利用者が許可を外したりモデルを外したりした時点で使わない。設定に名前が
    残っていても session には載せず、セッションと同じ（ローカルの）モデルへ戻す。
    """
    value = str(settings.get(key) or "")
    if value.startswith(CLOUD_PREFIX) and not _cloud_usable(value):
        return ""
    return value


def cloud_choices() -> dict:
    """画面向け: 役割に選べる外部モデルと、許可が無くて選べない状態かどうか。"""
    allowed = [{"id": item["id"], "name": item["name"], "provider": item["provider"]}
               for item in _cloud_models_for_opencode()]
    blocked = False
    if not allowed:
        try:
            from app.models_mgmt import cloud

            blocked = bool(cloud.enabled_models())
        except Exception:  # noqa: BLE001 - 外部モデルの都合で設定画面を止めない
            blocked = False
    return {"cloud_models": allowed, "cloud_blocked": blocked}


def _developer_agent(model: str | None) -> dict:
    return {
        "mode": "subagent",
        "description": (
            "開発担当。決まった方針に沿って、まとまった実装（機能の追加・修正・テスト）を自分の文脈で行い、"
            "変えたファイル・実行した確認・残った課題を短く返す。"
            "何を作るか、対象のファイルや範囲、完了の条件、守ること（触らない場所）を渡す。"
        ),
        "prompt": _agent_prompt("developer"),
        **({"model": f"controldeck/{model}"} if model else {}),
    }


def workflow_plugin_specs() -> dict[str, str]:
    """拡張ごとの OpenCode プラグイン指定（file://…）。未導入の拡張は含めない。"""
    specs: dict[str, str] = {}
    for key, feature_id in WORKFLOW_FEATURES.items():
        path = registry.plugin_path(feature_id)
        if path is not None:
            specs[key] = path.as_uri()
    return specs


def workflow_installed() -> dict[str, bool]:
    """画面向け: 拡張ごとにプラグイン・MCP サーバーが導入済みか。"""
    specs = workflow_plugin_specs()
    return {**{key: key in specs for key in WORKFLOW_FEATURES},
            **{key: registry.mcp_entry(feature) is not None for key, feature in WORKFLOW_MCP_FEATURES.items()}}


def ui_verify_dir(safe_job_id: str) -> Path:
    """UI 検証の画面写真の置き場（session ごと）。ControlDeck が持つ領域で、プロジェクトは汚さない。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", safe_job_id)[:64] or "session"
    return _integration_dir() / "ui-verify" / safe


def _blocked_origins() -> list[str]:
    """ControlDeck 本体。検証用のブラウザに管理画面を触らせない。"""
    from app.config import get_config

    port = int(get_config().server.port)
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"]


LOOPBACK_ORIGINS = (
    "http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*",
    "https://localhost:*", "https://127.0.0.1:*",
)


def _browser_executable() -> str:
    """このPCの Chrome。Playwright のブラウザを別に取りに行かない。"""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def ui_verify_server(safe_job_id: str) -> dict | None:
    """UI 検証の MCP サーバー（Playwright MCP）。使わない・未導入・ブラウザが無いなら None。"""
    settings = get_settings()
    if not settings.get("ui_verify"):
        return None
    entry = registry.mcp_entry(WORKFLOW_MCP_FEATURES["ui_verify"])
    node = shutil.which("node")
    browser = _browser_executable()
    if entry is None or not node or not browser:
        return None
    output = ui_verify_dir(safe_job_id)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        node, str(entry),
        "--browser", "chrome", "--executable-path", browser,
        # 使い捨てのプロファイル。利用者の Chrome のログイン状態や履歴を持ち込まない。
        "--headless", "--isolated",
        "--viewport-size", UI_VERIFY_VIEWPORT,
        "--output-dir", str(output),
        # 操作テストの動画（browser_start_video / browser_stop_video）。作業場では出力先が repo/.controldeck/media になる
        "--caps=devtools",
        "--console-level", "warning",
        "--blocked-origins", ";".join(_blocked_origins()),
    ]
    if not settings.get("ui_verify_allow_external"):
        # 安全境界ではない（Playwright MCP 自身の注意書き）。外へ出さないための既定。
        command += ["--allowed-origins", ";".join(LOOPBACK_ORIGINS)]
    # 録画（browser_start_video）が要る ffmpeg は、この MCP の版専用の置き場から読む。E2E・本体の Python の置き場とは
    # 別（同じ置き場だと、版の違う playwright install が互いのブラウザを消す。2026-11-23）。
    from app.features import playwright_browsers

    return {"type": "local", "command": command, "enabled": True, "timeout": 600_000,
            "environment": {"PLAYWRIGHT_BROWSERS_PATH": str(playwright_browsers.mcp_browsers_path())}}


def _agent_prompt(name: str) -> str:
    path = Path(__file__).with_name("agents") / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _ui_verifier_prompt() -> str:
    return _agent_prompt("ui-verifier")


def context7_server() -> dict | None:
    """Context7 の MCP サーバー。設定でオンにし、導入済みのときだけ。"""
    if not get_settings().get("context7"):
        return None
    entry = registry.mcp_entry(WORKFLOW_MCP_FEATURES["context7"])
    node = shutil.which("node")
    if entry is None or not node:
        return None
    return {"type": "local", "command": [node, str(entry)], "enabled": True, "timeout": 120_000}


def github_cli_config_dir() -> Path | None:
    """session に渡す gh の設定の場所。使わない設定・未設定なら None。"""
    if not get_settings().get("github_cli"):
        return None
    configured = os.environ.get("GH_CONFIG_DIR")
    if configured:
        path = Path(configured).expanduser()
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base).expanduser() / "gh"
    return path if (path / "hosts.yml").is_file() else None


def github_cli_status() -> dict:
    """画面向け: この PC の gh が使える状態か（token は読まない）。"""
    gh = shutil.which("gh")
    base = os.environ.get("GH_CONFIG_DIR") or str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "gh")
    return {"installed": bool(gh), "configured": (Path(base).expanduser() / "hosts.yml").is_file()}


def review_tool_bin() -> Path | None:
    """管理領域に入れた ast-grep の置き場（session の PATH の先頭に足す）。"""
    managed = registry._managed_executable(REVIEW_TOOL_FEATURE)
    if not managed.is_file() or not os.access(managed, os.X_OK):
        return None
    return managed.parent


def _reviewer_agent(model: str | None = None) -> dict:
    has_ast_grep = review_tool_bin() is not None
    return {
        **({"model": f"controldeck/{model}"} if model else {}),
        "mode": "subagent",
        "description": (
            "コードレビューの係。差分を読み、変えた関数・設定の呼び出し側まで影響を追い"
            + ("（ast-grep で構文検索）" if has_ast_grep else "")
            + "、テストが変更を確かめているかを見て、指摘を重大度順に ControlDeck へ報告する。ファイルは変更しない。"
            "レビューする範囲（ブランチ・コミット・ファイル）と気になる点を渡す。"
        ),
        "prompt": _agent_prompt("reviewer"),
        "tools": {name: False for name in REVIEWER_DENIED},
    }


def _role_agent(role: str, model: str | None) -> dict:
    """役割の子エージェント（調査・簡易検証・評価）。道具は主（build）と同じ範囲で、編集はしない。"""
    descriptions = {
        "research": (
            "調査の係。コードや資料を読む・探す。どこに何があるか、どう動いているか、関係する決まりは何かを調べ、"
            "根拠（ファイルと行）つきで短く返す。ファイルは変更しない。調べたいことと、探す範囲を渡す。"
        ),
        "quick_check": (
            "簡易検証の係。変更の直後に、関係するテストと関門（型・lint・dev-check など）を流し、"
            "通ったか・落ちた項目・直すべき所を返す。ファイルは変更しない。変えたファイルと流すコマンドを渡す。"
            "最終の確認は reviewer が行う。これは作業の合間の確かめ。"
        ),
        "evaluate": (
            "評価の係。完成した物（差分・画面・出力）を、利用者の目的に照らして評価する。"
            "目的を満たした点・足りない点・次に直す点を、根拠つきで返す。ファイルは変更しない。目的と評価する物を渡す。"
        ),
    }
    denied = {name: False for name in REVIEWER_DENIED}
    heavy = {pattern: False for patterns in _SUBAGENT_TOOLS.values() for pattern in patterns}
    return {
        "mode": "subagent",
        "description": descriptions[role],
        "prompt": _agent_prompt(roles_mod.SUBAGENT_NAMES[role]),
        "tools": {**heavy, **denied},
        **({"model": f"controldeck/{model}"} if model else {}),
    }


def _workflow(runtime: str, model: str, *, local_only: bool = False) -> dict:
    """有効な拡張から、足すプラグイン・モデル・agent 設定・Auto Advisor 設定を作る。"""
    settings = get_settings()
    specs = workflow_plugin_specs()
    plugins: list[str] = []
    models: list[str] = []
    agents: dict[str, Any] = {}
    advisor: dict | None = None
    if settings.get("superpowers") and specs.get("superpowers"):
        plugins.append(specs["superpowers"])
    # 役割ごとのモデル（roles.py）。秘密なら全部ローカル。どの組み合わせでもよい（制限しない）。
    resolved = role_resolution(settings, model, local_only=local_only)
    for note in resolved.notes:
        logger.info("OpenCode の役割: %s", note)
    role_models = resolved.models
    # 全ての役割にモデルを明示する（空だと子エージェントは呼んだ側のモデルを引き継ぐ。計画から調査を呼ぶと
    # 調査が計画のモデルで動いてしまう）。主のエージェントは chat_server.switch_agent がセッションのモデルも切り替える
    for name in ("plan", "build"):
        agents[name] = {"model": f"controldeck/{role_models[name]}"}
    for role in roles_mod.SUBAGENT_NAMES:
        agents[roles_mod.SUBAGENT_NAMES[role]] = _role_agent(role, role_models[role])
    models.extend(m for m in role_models.values() if m != model)
    review = role_models["final_check"]
    reviewer_model = review   # 同じモデルでも明示する（空だと呼んだ側のモデルを引き継ぐ）
    if settings.get("adversarial_review") and specs.get("adversarial_review"):
        plugins.append(specs["adversarial_review"])
        for name in ("adversarial-reviewer", "constructive-reviewer"):
            agents[name] = {"model": f"controldeck/{review}"}
    mode = str(settings.get("auto_advisor") or "off")
    if mode != "off" and specs.get("auto_advisor"):
        plugins.append(specs["auto_advisor"])
        target = role_models["advise"]   # 役割「相談」（空ならセッションと同じ。秘密ならローカル）
        if target != model:
            models.append(target)
        advisor = {
            "advisor": {"models": [f"controldeck/{target}"]},
            # 判断（毎回の要否判定）はセッションと同じモデルで行う。別のモデルに
            # すると判定のたびに載せ替えが起きる。
            "routing": {"mode": mode, "models": [f"controldeck/{model}"]},
        }
    if settings.get("developer_agent"):
        # 開発担当は役割「開発」（build）のモデルで動く（別のモデルの選択は持たない）
        agents["developer"] = _developer_agent(role_models["build"])
    return {"plugins": plugins, "models": list(dict.fromkeys(models)), "agents": agents, "advisor": advisor,
            "reviewer_model": reviewer_model, "auto_review": bool(settings.get("auto_review", True)),
            "roles": resolved}


def save_settings(patch: dict) -> dict:
    settings = get_settings()
    settings.update({key: patch[key] for key in settings if key in patch})
    settings["base_url"] = str(settings["base_url"]).strip().rstrip("/")
    settings["model"] = str(settings["model"]).strip()
    if not settings["base_url"].startswith(("http://", "https://")):
        raise ValueError("LLM endpointはhttp(s) URLで指定してください")
    if not settings["model"] or len(settings["model"]) > 200:
        raise ValueError("modelを指定してください")
    settings["default_model"] = str(settings.get("default_model") or "").strip()
    if len(settings["default_model"]) > 200 or "/" not in settings["default_model"] and settings["default_model"]:
        raise ValueError("OpenCode の既定のモデルは provider/モデル の形で指定してください")
    if settings["default_model"].startswith("controldeck/") and not settings["default_model"].split("/", 1)[1].startswith("cloud:"):
        settings["model"] = settings["default_model"].split("/", 1)[1]   # ホストの OpenCode・TUI もそろえる
    # 系列は v2 だけ。古い画面や設定が v1 を送ってきても v2 に揃える。
    settings["runtime"] = "v2"
    project = str(settings.get("project_path") or "")
    if project:
        resolved = files.resolve(project)
        if not resolved.is_dir():
            raise ValueError("project pathはディレクトリを指定してください")
        settings["project_path"] = str(resolved)
    path = _settings_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return settings


def _api_key_for(base_url: str) -> str:
    """ゲートウェイ宛なら発行済みAPIキー、直結なら従来どおりダミー。"""
    if not is_gateway_url(base_url):
        return "sk-no-key"
    from app.models_mgmt import gateway

    return gateway.get_api_key(create=True) or "sk-no-key"


def _addon_asset_roots() -> list[Path]:
    """Add-on が作った成果物の置き場。

    MediaForge も SonicForge も、生成したものを feature-data の下へ置いて
    asset_id で返す。OpenCode はそのパスを読みに行くので、ここが閉じていると
    生成のたびに確認が入る。置き場の形は Add-on ごとに違うので、両方見る。

    開けるのは assets の下だけにする。feature-data ごと開けると、モデルの重みや
    実行状態まで一緒に読めてしまう。知らない形の Add-on は従来どおり確認する。
    """
    from app.config import data_dir

    base = data_dir() / "feature-data"
    if not base.is_dir():
        return []
    roots: list[Path] = []
    for feature in sorted(base.iterdir()):
        if not feature.is_dir():
            continue
        for candidate in (feature / "assets", feature / "data" / "assets"):
            if candidate.is_dir():
                roots.append(candidate)
    return roots


def _mcp_result_dir() -> Path:
    """長すぎる Add-on tool の結果を、削る前に落としておく場所。

    削った側だけを会話へ載せる。全部を載せると、1 件で文脈を食い尽くしうる
    （実測で会話の 48.1% が道具の結果だった）。落とした先はここに置き、
    必要になったら grep で読ませる。
    """
    root = (data_dir() / "integrations" / "opencode" / "tool-results").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


# OS の処理を止める道具。CodeDEV で制作する仕事に要らない。
#
# 2026-09-12、OpenCode が「古いサーバーが別ディレクトリを提供している」と判断して
# `kill 3080550` を実行した。3080550 は Control Deck 本体で、40 分止まった。同じ
# 利用者で走っている以上 signal を撃てること自体は塞げないので、撃つ道具を渡さない。
#
# 命令の先頭だけを見ても足りない。事故のときの命令は
# `kill 3080550 2>/dev/null; sleep 1; cd ... && ...` という一続きで、区切りの後ろに
# 隠れる形もある。そこで区切り文字を明示して、その直後に来る場合も拾う。
#
# 単純に `*<名前>*` としないのは、語を含むだけの path（`src/myservice`）まで拒んで
# しまうためである。区切りを挟むことで「命令として置かれたもの」に絞る。
#
# 副作用として `git commit -m "kill switch の修正"` のように文中に空白付きで含む
# ものも拒まれる。言い換えれば通るので、取りこぼすより良いと判断する。
_FORBIDDEN_COMMANDS = (
    "kill", "pkill", "killall", "skill", "fuser",
    "systemctl", "service", "shutdown", "reboot", "halt",
)
# gh で、token を表に出す・取り返しのつかない操作。gh を使わせる設定でも禁じる。
# git の credential helper（gh auth git-credential）は git が直接呼ぶので、この規則に当たらない。
_FORBIDDEN_GH_COMMANDS = (
    "gh auth", "gh repo delete", "gh repo archive", "gh secret", "gh variable",
    "gh ssh-key", "gh gpg-key", "gh api",
)
# 先頭（区切り無し）と、区切りの直後。
_COMMAND_SEPARATORS = ("", " ", ";", "&", "|", "(", "\t")


# deck.sh のうち、本番の ControlDeck を止める・作り替えるもの。OpenCode からは使わせない。
# 本番の ControlDeck を再起動すると、その OpenCode 自身が LLM を使っているゲートウェイも止まる。
# 開発と確認はサンドボックスとミラー（./deck.sh sandbox）、検証用インスタンス（./deck.sh e2e）で行う。
_FORBIDDEN_DECK_COMMANDS = (
    "start", "service", "stop", "restore", "database", "admin", "passwd", "reset-totp",
    "enable-desktop", "disable-desktop", "feature", "plugin", "update-prepare", "searxng",
)


def _forbidden_commands() -> dict[str, str]:
    """止める道具を禁じる規則を組む。

    背後で server を起こしたいなら `timeout` を付けて前面で走らせる。後始末の
    ために他人の process を探して撃つ、という形にしない。
    """
    rules: dict[str, str] = {}
    for name in (*_FORBIDDEN_COMMANDS, *_FORBIDDEN_GH_COMMANDS):
        for separator in _COMMAND_SEPARATORS:
            head = f"*{separator}" if separator else ""
            rules[f"{head}{name}"] = "deny"
            rules[f"{head}{name} *"] = "deny"
    # 引数なしの ./deck.sh は起動（再起動）になる。区切りの後ろでも同じ。
    for script in ("deck.sh", "./deck.sh", "bash deck.sh", "bash ./deck.sh"):
        for separator in _COMMAND_SEPARATORS:
            head = f"*{separator}" if separator else ""
            rules[f"{head}{script}"] = "deny"
            for tail in (";*", " ;*", " &*", " |*", "&*", "|*"):
                rules[f"{head}{script}{tail}"] = "deny"
    for sub in _FORBIDDEN_DECK_COMMANDS:
        rules[f"*deck.sh {sub}"] = "deny"
        rules[f"*deck.sh {sub} *"] = "deny"
        rules[f"*deck.sh {sub};*"] = "deny"
    return rules


def _allowed_directories() -> dict[str, str]:
    """確認なしで読ませてよい、プロジェクト外のディレクトリ。"""
    from app.terminals import attachments

    allowed: dict[str, str] = {}
    for root in [
        codedev_root(), attachments.store.root, _mcp_result_dir(), *_addon_asset_roots(),
    ]:
        allowed[f"{root}/*"] = "allow"
        allowed[f"{root}/**"] = "allow"
    return allowed


logger = logging.getLogger("control_deck.opencode")


def _skill_paths() -> list[str]:
    """有効なスキルの置き場。取れなければ何も渡さない。

    スキルは補助であって、これが読めないことで OpenCode が起動しないのは
    本末転倒である。
    """
    try:
        from app.skills import registry as skills

        return skills.enabled_paths()
    except Exception:  # noqa: BLE001 - スキルの都合で session を止めない
        logger.exception("有効なskillの一覧を取得できませんでした")
        return []


# 1回の応答（思考を含む）に使える長さは窓の半分。固定の上限は置かない。
# 以前は全モデル一律 8,192 で、思考を出力に数えるモデル（GLM-5.3-Flash など）は
# 難しい課題で思考の途中で切れていた。半分を入力側に残すのは、OpenCode が
# limit.input - compaction.reserved を超えたら会話を畳むため。窓を広げれば両方増える。
OPENCODE_OUTPUT_SHARE = 2
# 畳む作業そのものに要る余地。要約を書く一往復ぶん。
OPENCODE_COMPACTION_RESERVED = 4096


# 主の会話から外して、専門の子へ委ねる道具。
#
# OpenCode は MCP の道具名を書き換えて見せる（server 名を前に付け、記号を _ に
# する）。この server 名は下の payload["mcp"] で決めているので、前置きは既知である。
#
# 実測（道具 24 個で 26,692 トークン）:
#   3D  media.scene.* / media.job.*  15,583 tok  58%  コードを書く会話では一度も使わない
#   音  sonic.*                       5,651 tok  21%  たまに使う
#   絵  media.generate 系             5,236 tok  20%  よく使う。一発で終わることが多い
#
# 絵は主に残す。子を起こす往復のほうが、載せておく 5,236 トークンより高くつく。
# 3D と音を外すと、主は 26,692 → 5,458 トークン（-80%）になり、圧縮を挟んだ
# 直後の余裕が 13,346 → 36,590 トークンへ広がる。
#
# 子の中の往復（場面を何度も直す、音を何本も作る）は主の文脈に入らない。実測で
# 会話の 48.1% は道具の結果だったので、こちらの効きのほうが大きい。
_SUBAGENT_TOOLS = {
    "sculptor": ("controldeck_addons_media_scene_*", "controldeck_addons_media_job_*"),
    "sound": ("controldeck_addons_sonic_*",),
}


def _delegated_agents() -> dict[str, Any]:
    """主から重い道具を外し、専門の子に持たせる。

    子は自分の文脈を持つ。主へ返るのは結論だけで、途中の往復は入らない。
    子の文脈が短いことには、もう一つ効きめがある——MCP の生成は GPU を要求し、
    その度に言語モデルが降ろされる。戻すときの読み直しは文脈の長さに比例するので、
    短い子ほど安く戻る。
    """
    agents: dict[str, Any] = {}
    build_tools: dict[str, bool] = {}
    for name, patterns in _SUBAGENT_TOOLS.items():
        for pattern in patterns:
            build_tools[pattern] = False
        agents[name] = {
            "mode": "subagent",
            "description": _SUBAGENT_DESCRIPTIONS[name],
            "tools": {pattern: True for pattern in patterns},
        }
    agents["build"] = {"tools": build_tools}
    agents["plan"] = {"tools": dict(build_tools)}   # 重い道具はプランの主の会話にも載せない
    return agents


_SUBAGENT_DESCRIPTIONS = {
    "sculptor": (
        "3D の場面を作る係。場面を作る・直す・材質を貼る・書き出す。"
        "何を置いてどう見せたいかを渡すと、出来た場面の id と書き出した資産を返す。"
    ),
    "sound": (
        "音を作る係。台詞の読み上げ、効果音、環境音、音楽、書き起こし。"
        "キャラクターの声は先に作る必要があるので、声の用意もこの係が行う。"
    ),
}


def _window_of(instance: dict) -> int:
    from app.models_mgmt.compaction import window_of

    return window_of(instance)


def _compaction_enabled() -> bool:
    try:
        from app.models_mgmt.runtime_policy import get_policy

        return bool(get_policy().compaction.enabled)
    except Exception:  # noqa: BLE001 - 設定が読めなくても session は止めない
        return True


def _local_aliases() -> list[str]:
    """ゲートウェイで使えるローカルのモデルの名前（登録順）。読めなければ空。"""
    try:
        from app.models_mgmt import local_llm

        return [str(item["alias"]) for item in local_llm.list_instances() if item.get("alias")]
    except Exception:  # noqa: BLE001 - 一覧が読めなくても session は止めない
        logger.exception("ローカルのモデルの一覧を読めませんでした")
        return []


def _cloud_models_for_opencode() -> list[dict]:
    try:
        from app.models_mgmt import cloud

        if not cloud.status()["egress"].get("opencode"):
            return []
        return cloud.enabled_models()
    except Exception:  # noqa: BLE001 - 外部モデルの都合で session を止めない
        logger.exception("外部モデルの一覧を読めませんでした")
        return []


def _model_limits(model: str) -> dict[str, int] | None:
    """そのモデルの窓の大きさを OpenCode へ伝える。

    伝えないと自動圧縮が先回りでは一度も動かない。OpenCode の判定は
    `limit.context === 0` をそのまま「窓を知らない」と読んで諦める作りで、
    残るのは溢れてから畳む後追いの経路だけになる。実測（2026-09-12、919
    session）: 圧縮 38 件のうち 33 件が `overflow` 付き＝モデルが断ってからの
    後追いで、先回りは 0 件だった。そのたびに 1 往復を捨てていた。

    `model` は gateway の仮想モデル `auto` であることが多い。これは実在の
    instance ではないので alias では引けず、以前の実装はここで None を返して
    いた。転送先は request ごとに gateway が決める（起動中を優先、無ければ
    登録順）ので、候補のうち最も狭い窓に合わせる。広いほうに合わせると、狭い
    モデルへ回った回だけ溢れが戻る。

    input も併せて宣言する。OpenCode は input がある場合だけ compaction.reserved
    を見る作りで、context だけだと予備は「返答の上限」（既定 32,000）に固定される。
    """
    try:
        from app.models_mgmt import local_llm

        match = next(
            (i for i in local_llm.list_instances() if str(i.get("alias") or "") == model), None)
        if match is not None:
            context = _window_of(match)
        else:
            # gateway.resolve_instance と同じ候補集合（embedding/reranker は除く）。
            # auto は選択中ランタイムのモデルへ回るので、そのランタイムの窓で決める。
            from app.models_mgmt import gateway

            candidates = local_llm.llm_instances()
            selected = gateway._selected_runtime()
            mine = [i for i in candidates if str(i.get("runtime") or "") == selected]
            windows = [w for w in map(_window_of, mine or candidates) if w > 0]
            context = min(windows) if windows else 0
        if context <= 0:
            return None
        output = max(1024, context // OPENCODE_OUTPUT_SHARE)
        # 発火点は OpenCode の式で input - reserved。共通の圧縮設定（窓に応じた閾値）に合わせる。
        # 圧縮を切っているときは従来どおり「窓 - 返答」を宣言する（溢れたときの後追いだけ残る）。
        from app.models_mgmt import compaction

        fire = compaction.threshold(context)
        limit_input = fire + OPENCODE_COMPACTION_RESERVED if fire else context - output
        return {"context": context, "output": output, "input": min(context, limit_input)}
    except Exception:  # noqa: BLE001 - 窓の大きさが取れないことで session を止めない
        logger.exception("モデルの窓の大きさを取得できませんでした")
    return None


def _runtime_config(
    job_id: str,
    base_url: str,
    model: str,
    *,
    owner_user_id: int | None = None,
    project_id: str | None = None,
    runtime: str = "v2",
    chat_server: bool = False,
    extra_mcp: dict | None = None,
) -> Path:
    """OpenCode v2 のセッション設定を書き、XDG_CONFIG_HOME にするディレクトリを返す。

    `extra_mcp` は設定の mcp へ足すサーバー（{名前: 指定}。Nexarch のチームリーダーの nexarch_team）。

    `runtime` は旧呼び出し互換のためだけに受け取る（v1 は撤去済みで常に v2）。
    `chat_server` は AI アシスタント用の常駐サーバー（chat_server.py）の設定にする:
    権限を判断させる操作を ask にし、ControlDeck の権限プラグインを読ませる。
    """
    safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "", job_id)[:24]
    payload = _config_payload(safe_job_id, base_url, model, owner_user_id=owner_user_id, project_id=project_id,
                              with_local_models=chat_server)
    if chat_server:
        payload["_chat_server"] = True
    if extra_mcp:
        payload.setdefault("mcp", {}).update(extra_mcp)
    return _write_v2_config(safe_job_id, payload)


# 権限プラグイン（依存なし）。ControlDeck のコードと一緒に配る（npm で取りに行かない）。
PERMISSION_PLUGIN = Path(__file__).resolve().parent / "plugins" / "controldeck-permission"
# 権限プラグインに判断させる操作。v2 は規則の既定が allow（shell も聞かずに通る。2.3.0 実測）
# なので、ここで ask にしてプラグインの evaluate へ回す。後に一致した規則が勝つので、
# 禁止リスト（deny）より前に置く（禁止は緩まない）。
CHAT_SERVER_ASK_ACTIONS = ("shell", "edit", "write", "webfetch", "execute")


def _apply_chat_server(v2: dict, plugin: Path | None = None) -> dict:
    """AI アシスタント用サーバーの設定に、ask の規則と権限プラグインを足す。

    plugin は権限プラグインの場所（作業場の箱は本番のコードが見えないので、箱の中へ写したものを渡す）。"""
    plugin = plugin or PERMISSION_PLUGIN
    asks = [{"action": action, "resource": "*", "effect": "ask"} for action in CHAT_SERVER_ASK_ACTIONS]
    # question（利用者へ質問して答えを待つ道具）は禁止する。画面に答える欄が無く、答えが届かないまま
    # 実行が止まる（2026-11-21 実機: 質問の最中に「途中で伝える」を押すと、伝えた文は質問の答えにならず
    # 何も進まなくなった）。確認したいことは本文で聞かせる。deny は最後に置く（後に一致した規則が勝つ）
    v2["permissions"] = [*asks, *(v2.get("permissions") or []),
                         {"action": "question", "resource": "*", "effect": "deny"}]
    # アクションリスト（todowrite）。v2 では実験機能で既定は切（2026-11-18 利用者「いつ終わるのかわからん」）
    v2["experimental"] = {**(v2.get("experimental") or {}), "todo": True}
    plugins = [p for p in (v2.get("plugins") or []) if p != plugin.as_uri()]
    v2["plugins"] = [plugin.as_uri(), *plugins]
    return v2


def _config_payload(
    safe_job_id: str,
    base_url: str,
    model: str,
    *,
    owner_user_id: int | None = None,
    project_id: str | None = None,
    local_only: bool = False,
    with_local_models: bool = False,
) -> dict:
    """設定の中身（書き込み前）。形は旧来の OpenCode 形式で、_v2_payload が v2 へ読み替える。

    with_local_models: 登録済みのローカルのモデルを全て並べる（AI アシスタントの会話サーバー用。会話で選んだ
    モデルへ OpenCode のセッションを切り替えられるように）。"""
    runtime = "v2"
    from app.security import offline

    local_only = local_only or offline.in_confidential_scope()   # 秘密は全役割をローカルへ（外部のモデルも並べない）
    limits = _model_limits(model)
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"controldeck/{model}",
        "provider": {
            "controldeck": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Control Deck LLM",
                "options": {
                    "baseURL": base_url.rstrip("/"),
                    "apiKey": _api_key_for(base_url),
                    # ゲートウェイがOpenCodeを識別するための印。停止中のモデルを
                    # OpenCodeが起こすときは投機デコード優先で常駐させる。
                    "headers": {gateway_client_header(): "opencode"},
                },
                # 画像を送らせるには attachment と modalities の両方が要る。
                # attachment だけだと OpenCode は画像を text に落として送り、モデルは
                # 「画像入力に対応していない」と答える。実際に画像を載せるかは
                # modalities.input の image で決まる。転送先が VLM でない場合は
                # 転送先が拒否する（送る側では判断できない）。
                "models": {
                    model: {
                        "name": model,
                        "attachment": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                        **({"limit": limits} if limits else {}),
                    }
                },
            }
        },
        # 古い道具の出力を捨てる。会話も判断も壊さず、完了した tool の出力だけが
        # 対象で、直近 40,000 トークン分は残る（OpenCode 側の既定）。
        #
        # 既定は false である。切ったままだと、忘れる仕組みが一つも動かない。
        # 実測: この環境の 793 session のうち圧縮が起きたのは 19 件で、うち 17 件は
        # 「モデルが断ってから畳んだ」後追いだった。先回りの圧縮は一度も発火して
        # いない。唐揚げダンジョンの session は 262,142 tokens で打ち止めている。
        #
        # 剪定が狙うのは、その打ち止めの中身そのものである。頂点の内訳を数えると
        # 道具の結果が 48%、推論が 20%、画像は 7%、人が読む本文は 2% だった。
        # 頂点 100k を超えた 13 session で見積もると、平均 51.7% が空く。
        # 窓が尽きる手前で自動的に畳む。
        #
        # 既定は auto: true だが、limit.context が 0 の間は一度も発火しない。
        # 窓の大きさは _model_limits で宣言した。発火点は OpenCode の式で
        # limit.input - reserved になるので、ctx 65,536 ならこうなる:
        #
        #   窓        65,536
        #   返答       8,192   常に空けておく
        #   入力      57,344   = 窓 - 返答
        #   予備       4,096   畳む作業そのものの分
        #   発火      53,248   入力がここに届いたら畳む（窓の 81%）
        #
        # 畳んだ後に残す直近は OpenCode の既定に任せる（発火点の 25% を 15,000 で
        # 頭打ちにしたもの）。窓が 65,536 だった頃は道具定義 26,702 トークンに
        # 押されて畳んだ直後の余裕が 13,000 しか無く、10,000 へ絞っていた。
        # 窓が 131,072 になり、重い道具を子へ委ねて主が 5,458 トークンになった
        # いまは、既定でも次の発火まで 97,000 トークンある。残る文脈のほうが
        # 価値が高い。
        #
        # 剪定（prune）は別物で、完了した道具の出力だけを捨てる。直近 40,000
        # トークン分は守られる（OpenCode 側の固定値で、設定では変えられない）。
        "compaction": {
            "auto": _compaction_enabled(),
            "prune": True,
            "reserved": OPENCODE_COMPACTION_RESERVED,
        },
        # CodeDEV 配下は毎回聞かない。別プロジェクトを参照するだけで確認が入ると
        # 手が止まるためで、CodeDEV の外は既定どおり確認する。
        # ターミナルから送った画像の置き場も開ける。利用者が自分で送ったものであり、
        # ここが閉じているとパスを渡しても読めない。
        # `*` は階層を跨がない照合系もあるので、直下と再帰の両方を挙げておく。
        "permission": {
            "external_directory": _allowed_directories(),
            "bash": _forbidden_commands(),
        },
        "agent": _delegated_agents(),
    }
    # 導入済みで有効なスキルだけ読ませる。利用者の ~/.claude や
    # ~/.config/opencode へは書かない——そこは利用者自身のもので、こちらが
    # 足したり消したりしてよい場所ではない。ControlDeck から起動した session
    # にだけ効くので、無効化はこの一覧から外すだけで済む。
    workflow = _workflow(runtime, model, local_only=local_only)
    # 外部のフロンティアモデル。OpenCode からの外部送信を利用者が許可したときだけ並べる
    # （許可はゲートウェイでも呼び出しごとに確かめる）。docs/PLAN-frontier-models.md
    for item in ([] if local_only else _cloud_models_for_opencode()):   # 秘密では外部のモデルを並べない
        payload["provider"]["controldeck"]["models"].setdefault(item["id"], {
            "name": f"{item['name']}（外部）", "attachment": True,
            "modalities": {"input": ["text", "image"], "output": ["text"]},
        })
    # 会話で選んだローカルのモデルへ切り替えられるよう、いま登録されている全てを並べる（無いと
    # 「Model unavailable」で止まる）。起動中のサーバーには、あとから足した分は次の起動から入る。
    for alias in (_local_aliases() if with_local_models else []):
        payload["provider"]["controldeck"]["models"].setdefault(alias, {
            "name": alias, "attachment": True, "modalities": {"input": ["text", "image"], "output": ["text"]},
            **({"limit": alias_limits} if (alias_limits := _model_limits(alias)) else {}),
        })
    for extra in workflow["models"]:
        if extra.startswith(CLOUD_PREFIX):
            # 外部モデルは上で並べ済み（名前に「外部」と付く）。ローカル用の項目で上書きしない。
            continue
        payload["provider"]["controldeck"]["models"][extra] = {
            "name": extra, "attachment": True,
            "modalities": {"input": ["text", "image"], "output": ["text"]},
            **({"limit": extra_limits} if (extra_limits := _model_limits(extra)) else {}),
        }
    if workflow["plugins"]:
        payload["plugin"] = workflow["plugins"]
    for agent_name, spec in workflow["agents"].items():
        payload["agent"].setdefault(agent_name, {}).update(spec)   # build・plan は道具の設定へ model を足す
    if workflow["advisor"]:
        payload["_auto_advisor"] = workflow["advisor"]
    skill_paths = _skill_paths()
    if skill_paths:
        payload["skills"] = {"paths": skill_paths}
    # 文脈の使い方を書いたものを system prompt へ足す。449 トークン。
    #
    # 実測の内訳では道具の結果が 48.1% を占め、その中身は全文読みと、絞らずに
    # 受け取ったコマンドの出力だった。どちらも「読む前に当たりを付ける」だけで
    # 桁が変わる。プロジェクト側の AGENTS.md は残したまま、こちらを足す。
    notes = Path(__file__).with_name("agent-notes.md")
    instructions = [str(notes)] if notes.exists() else []
    review_notes = Path(__file__).with_name("auto-review.md")
    if workflow.get("auto_review") and review_notes.exists():
        # 完了の前に reviewer、行き詰まったら advisor()。拡張は道具を足すだけなので、
        # 「いつ使うか」をここで決めて自動で回るようにする。
        instructions.append(str(review_notes))
    if instructions:
        payload["instructions"] = instructions
    payload["agent"]["reviewer"] = _reviewer_agent(workflow.get("reviewer_model"))
    docs_server = context7_server()
    browser = ui_verify_server(safe_job_id)
    if browser is not None:
        payload["agent"]["ui-verifier"] = {
            "mode": "subagent",
            "description": (
                "UI 検証の係。開発中の画面（localhost の URL）を実ブラウザで開き、幅 320/390/768/1280 と"
                "ダークで撮影・操作して、問題を重大度つきで ControlDeck へ報告する。コードは直さない。"
                "検証したい URL と、確かめたい流れを渡す。"
            ),
            "prompt": _ui_verifier_prompt(),
            "tools": {UI_VERIFY_TOOLS: True, **{name: False for name in UI_VERIFY_DENIED}},
        }
        payload["agent"].setdefault("build", {}).setdefault("tools", {})[UI_VERIFY_TOOLS] = False
        payload["agent"].setdefault("plan", {}).setdefault("tools", {})[UI_VERIFY_TOOLS] = False
    if owner_user_id is not None:
        from app.addons.agent_mcp import MCP_CLIENT_TIMEOUT_MS, issue_opencode_token
        from app.config import get_config

        bridge = Path(__file__).with_name("addon_mcp_bridge.py").resolve()
        token = issue_opencode_token(owner_user_id, safe_job_id, project_id=project_id)
        payload["mcp"] = {
            "controldeck_addons": {
                "type": "local",
                "command": [sys.executable, str(bridge)],
                "enabled": True,
                "timeout": MCP_CLIENT_TIMEOUT_MS,
                "environment": {
                    "CONTROL_DECK_ADDON_MCP_URL": (
                        f"http://127.0.0.1:{get_config().server.port}/api/v1/addons/agent-mcp"
                    ),
                    "CONTROL_DECK_ADDON_MCP_TOKEN": token,
                    "CONTROL_DECK_ADDON_MCP_RESULT_DIR": str(_mcp_result_dir()),
                },
            }
        }
    if browser is not None:
        payload.setdefault("mcp", {})[UI_VERIFY_SERVER] = browser
    if docs_server is not None:
        # 道具は 2 つで小さいので主にも持たせる（版を確かめながら書く場面は主にある）。
        payload.setdefault("mcp", {})[CONTEXT7_SERVER] = docs_server
    return payload


# ---- OpenCode v2 向けの読み替え ----
#
# v2 は設定の形が変わっている（provider→providers、permission→permissions、
# agent→agents、npm→package、options→settings/headers、modalities→capabilities）。
# 上の payload は旧来（v1 時代）の形で組み、ここで機械的に読み替える。測って書いた
# コメント（窓の大きさ、圧縮の発火点、重い道具の内訳）を二重に持たないため。
#
# v2 は OPENCODE_CONFIG / OPENCODE_CONFIG_DIR / OPENCODE_CONFIG_CONTENT の
# どれも見ない（2.0.2 で実測）。job ごとに config directory を作り、
# XDG_CONFIG_HOME でそこへ向ける。利用者自身の ~/.config/opencode は読まれない。


def _v2_permissions(permission: dict) -> list[dict]:
    """v1 の tool 別 map を、v2 の (action, resource, effect) の並びへ読み替える。

    v2 は「後に一致した規則が勝つ」ので、広い規則から順に並べる。bash は shell へ
    名前が変わった。tool 名そのものを action に置ける（v2 自身が v1 の
    トップレベル `tools` をこの形へ移行する）ので、道具の出し入れもここで書ける。
    """
    renamed = {"bash": "shell"}
    rules: list[dict] = []
    for tool, entries in permission.items():
        action = renamed.get(tool, tool)
        for resource, effect in entries.items():
            rules.append({"action": action, "resource": resource, "effect": effect})
    return rules


# v2 の agent の system は既定の指示を丸ごと置き換える。既定の指示には Code Mode（MCP の
# 道具は execute の中で JavaScript から呼ぶ）の説明が入っていて、置き換えると子は道具を
# 見つけられず、`<function=search>` を本文に書いて止まった（2.3.0、2026-11-15 実機。
# system を外すと同じ子が tools.playwright.browser_navigate で開けた）。
# 子の指示の先頭に、その呼び方だけを足す。
CODE_MODE_PRIMER = """## 道具の呼び方（OpenCode）

ファイルの読み書きとコマンドは read / shell など通常の道具で行う。MCP の道具（playwright・controldeck_addons・context7 など）は
execute の道具の中で JavaScript を書いて呼ぶ。名前が分からなければ、まず execute で
`return (await search({ query: "browser navigate", limit: 10 })).items.map(i => i.path)` のように探し、
見つかった path をそのまま `await tools.<名前空間>.<道具名>({ ... })` で呼ぶ
（例: `await tools.playwright.browser_navigate({ url: "http://127.0.0.1:5173/" })`）。
1 回の execute で複数の道具を順に呼び、必要な値だけを return してよい。

"""


def _v2_agents(agent: dict) -> dict:
    """v1 の agent 定義を v2 へ読み替える。

    v2 の agent は `tools` を持たない。道具の出し入れは agent ごとの permissions
    へ `{action: "<道具名のパターン>", resource: "*", effect: deny}` で書く。これは
    v2 自身が v1 のトップレベル `tools: {pattern: false}` を移行する先と同じ形である。

    実機で確認したところ、deny した道具は主エージェントの一覧そのものから消えた
    （Add-on の道具 7 個だけが見え、media_scene_* / media_job_* / sonic_* は出ない）。
    v1 の `tools: false` と同じ効きで、文脈の節約（実測 26,692 → 5,458 トークン）も
    そのまま残る。
    """
    agents: dict[str, Any] = {}
    for name, spec in agent.items():
        converted = {key: value for key, value in spec.items() if key not in ("tools", "prompt")}
        # v1 の prompt は v2 で system になった。prompt のままだと黙って無視される
        # （2.3.0 の debug agents で確認）。
        if spec.get("prompt"):
            converted["system"] = CODE_MODE_PRIMER + spec["prompt"]
        tools = spec.get("tools") or {}
        rules = [
            {"action": pattern, "resource": "*", "effect": "allow" if allowed else "deny"}
            for pattern, allowed in tools.items()
        ]
        if rules:
            converted["permissions"] = rules
        agents[name] = converted
    return agents


# 道具の結果 1 件に許す長さ。v2 の組み込み上限で、v1 には対応する設定が無い。
#
# v2 の既定は max_lines 2,000 / max_bytes 51,200。51,200 バイトは約 14,600 トークンで、
# 窓 131,072 なら 1 件で 11% を使う。実測（262,142 トークンで打ち止めた会話）では
# 道具の結果が 48.1% を占め、内訳は bash 178k 文字・read 135k・edit 95k だった。
# 既定のままでも数件で窓が埋まる。
#
# そこで窓に対する割合で決める。入力窓の 1/16 を上限とし、v2 の既定を超えない。
# 狭いモデルへ回っても 1 件が窓を食い尽くさず、広いモデルでは既定どおりになる。
#
# 削っても情報は失われない。v2 は全文を data directory の tool-output/ へ落とし、
# その directory を読む権限を既定で開けている（実測）。必要な行は agent が grep で
# 拾える。ControlDeck が MCP bridge で手作りした形（RESULT_INLINE_LIMIT）と同じ。
TOOL_OUTPUT_DEFAULT_BYTES = 51_200
TOOL_OUTPUT_DEFAULT_LINES = 2_000
TOOL_OUTPUT_MIN_BYTES = 8_000
TOOL_OUTPUT_WINDOW_SHARE = 16
# 実測: 8,000 文字 ≒ 2,300 トークン（agent-notes.md の RESULT_INLINE_LIMIT の根拠）。
BYTES_PER_TOKEN = 3.5


def _v2_tool_output(limit: dict | None) -> dict:
    """1 件の道具の結果に許す長さ。窓が分からなければ v2 の既定に任せる。"""
    tokens = int((limit or {}).get("input") or 0)
    if tokens <= 0:
        return {"max_lines": TOOL_OUTPUT_DEFAULT_LINES, "max_bytes": TOOL_OUTPUT_DEFAULT_BYTES}
    share = int(tokens * BYTES_PER_TOKEN) // TOOL_OUTPUT_WINDOW_SHARE
    max_bytes = max(TOOL_OUTPUT_MIN_BYTES, min(TOOL_OUTPUT_DEFAULT_BYTES, share))
    return {"max_lines": TOOL_OUTPUT_DEFAULT_LINES, "max_bytes": max_bytes}


def _v2_payload(payload: dict) -> dict:
    """v1 形の runtime config を v2 形へ読み替える。"""
    provider = payload["provider"]["controldeck"]
    options = dict(provider.get("options") or {})
    headers = dict(options.pop("headers", {}) or {})
    models: dict[str, Any] = {}
    limit: dict | None = None
    for model_id, spec in provider["models"].items():
        converted = {key: value for key, value in spec.items()
                     if key not in ("attachment", "modalities")}
        modalities = spec.get("modalities") or {}
        # v2 は attachment / modalities を capabilities へ統合した。tools を明示
        # しないと道具を渡さない転送先とみなされうるので、併せて宣言する。
        converted["capabilities"] = {
            "tools": True,
            "input": list(modalities.get("input") or ["text"]),
            "output": list(modalities.get("output") or ["text"]),
        }
        models[model_id] = converted
        limit = limit or spec.get("limit")
    compaction = dict(payload.get("compaction") or {})
    v2: dict[str, Any] = {
        "$schema": payload["$schema"],
        "model": payload["model"],
        "providers": {
            "controldeck": {
                "name": provider["name"],
                # AI SDK の npm ではなく v2 同梱の OpenAI 互換 provider を使う。
                # 実行のたびに npm を取りに行かせない。
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": options,
                # headers は settings の中では送られない（2.0.2 で実測）。
                # provider 直下に置く必要がある。
                **({"headers": headers} if headers else {}),
                "models": models,
            }
        },
        # v2 は reserved を buffer へ改名し、prune は警告付きで無視する
        # （完了した道具の出力を捨てる剪定は v2 では設定項目ではない）。
        "compaction": {"auto": True, "buffer": int(compaction.get("reserved") or 0)},
        "permissions": _v2_permissions(payload.get("permission") or {}),
        "agents": _v2_agents(payload.get("agent") or {}),
        "tool_output": _v2_tool_output(limit),
        # 編集のあとに整形を通す。差分が整形だけで膨らむのを防ぎ、
        # lint との往復を 1 往復ぶん減らす。v1 に対応する設定は無い。
        "formatter": True,
    }
    if payload.get("plugin"):
        v2["plugins"] = list(payload["plugin"])
    # skills は paths/urls をまとめた1本の配列になった。
    skills = (payload.get("skills") or {}).get("paths") or []
    if skills:
        v2["skills"] = list(skills)
    if payload.get("instructions"):
        v2["instructions"] = list(payload["instructions"])
    if payload.get("mcp"):
        servers = {}
        for name, spec in payload["mcp"].items():
            converted = {key: value for key, value in spec.items()
                         if key not in ("enabled", "timeout")}
            # enabled は disabled へ反転、timeout は catalog/execution の組になった。
            converted["disabled"] = not bool(spec.get("enabled", True))
            timeout = int(spec.get("timeout") or 0)
            if timeout:
                converted["timeout"] = {"catalog": timeout, "execution": timeout}
            servers[name] = converted
        v2["mcp"] = {"servers": servers}
    return v2


# TUI 側の設定（keybind / theme / layout）。v2 は config directory の cli.json から読む。
#
# ControlDeck は job ごとに config directory を作り替えるので、そのままだと利用者が
# keybind を変えても次の起動で消える。系列をまたいで1枚だけ持ち、生成した directory
# からはそこへ symlink する。ControlDeck はこのファイルの中身を作った後は触らない。
#
# 既定は v1 の操作へ揃えてある。
#
# v1（1.18.30）と v2（2.0.2）の既定キーを突き合わせると、162 件中 16 件だけが変わって
# いた。そのうち「v2 が割り当てを外した／ずらした」ものを v1 の値へ戻す。plan への
# 切替（agent.cycle）が tab でなくなったのが実用上いちばん響く。
#
# tab は v2 で prompt.autocomplete.complete も使うが、この衝突は無害である。
# autocomplete 一群は候補が出ている間だけ効く文脈限定の割り当てで（next=down,
# prev=up, select=return と、通常操作と重なる key を並べていることから分かる）、
# v1 でも tab は agent_cycle と diff_switch_focus が共有していた。left / right /
# home / end / space も v1 で同じ共存をしていた組み合わせをそのまま戻している。
#
# 戻さないものが 2 つある。v2 が新機能へ割り当て直したキーで、戻すと新機能が潰れる:
#   theme.switch        <leader>t     → v2 では terminal.toggle
#   session.child.first <leader>down  → v2 では terminal.select
# この 2 つは v2 の割り当てのままにする（theme は command palette から選べる）。
CLI_CONFIG_SEED = {
    "keybinds": {
        # plan ⇔ build の切替。v1 と同じく tab / shift+tab。
        "agent.cycle": "tab",
        "agent.cycle.reverse": "shift+tab",
        # v2 が割り当てを外した diff viewer の操作。
        "diff.collapse": "left",
        "diff.expand": "right",
        "diff.expand_all": "E",
        "diff.switch_focus": "tab",
        "diff.toggle": "enter,space",
        # 同じく v2 が外した入力欄の先頭／末尾移動。
        "input.buffer.end": "end",
        "input.buffer.home": "home",
    }
}


def cli_config_path() -> Path:
    """利用者が編集する TUI 設定。無ければ既定で1度だけ作る。"""
    path = _integration_dir() / "cli.json"
    if not path.exists():
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(CLI_CONFIG_SEED, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    return path


def _link_cli_config(config_dir: Path) -> None:
    """生成した config directory から、共有の cli.json を見えるようにする。

    TUI 自身が設定画面から書き換えて symlink を実体ファイルへ置き換えた場合は、
    そちらを尊重して触らない（利用者がその session で変えた結果である）。
    """
    link = config_dir / "cli.json"
    try:
        shared = cli_config_path()
        if link.is_symlink():
            if link.readlink() == shared:
                return
            link.unlink()
        elif link.exists():
            return
        link.symlink_to(shared)
    except OSError:  # noqa: BLE001 - 設定の都合で session を止めない
        logger.exception("cli.jsonを共有設定へ繋げられませんでした")


def _write_v2_config(safe_job_id: str, payload: dict) -> Path:
    """v2 用の config directory を作り、そのパス（XDG_CONFIG_HOME 相当）を返す。"""
    root = _integration_dir() / f"runtime-config-v2-{safe_job_id}"
    if not root.resolve().is_relative_to(_integration_dir()):
        raise CodeAgentError("runtime config pathがintegration directory外です")
    config_dir = root / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(config_dir, 0o700)
    path = config_dir / "opencode.json"
    temp = path.with_suffix(".tmp")
    v2 = _v2_payload(payload)
    if payload.get("_chat_server"):
        v2 = _apply_chat_server(v2)
    temp.write_text(json.dumps(v2, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    # Auto Advisor は $XDG_CONFIG_HOME/opencode/auto-advisor.json を読む。
    advisor_path = config_dir / "auto-advisor.json"
    if payload.get("_auto_advisor"):
        advisor_path.write_text(json.dumps(payload["_auto_advisor"], ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(advisor_path, 0o600)
    else:
        advisor_path.unlink(missing_ok=True)
    _link_cli_config(config_dir)
    return root


def _runtime_env(runtime: str, config: Path, project: Path | None = None) -> dict[str, str]:
    """runtime config を読ませるための環境変数。"""
    env = {"XDG_CONFIG_HOME": str(config)}
    # v2 本体は XDG を読むが、Adversarial Review のコマンド配置先はこの変数を
    # 読む。HOME へ配置すると session の設定から見えず、初回は親も存在しない。
    env["OPENCODE_CONFIG_DIR"] = str(config / "opencode")
    # session は XDG_CONFIG_HOME を job ごとの場所へ向けるので、gh は ~/.config/gh を見失う
    # （"not logged into any GitHub hosts"、2026-11-15 実機）。使わせる設定のときだけ、
    # gh の設定の場所だけを元へ向ける。他の個人設定は引き続き読ませない。
    gh_dir = github_cli_config_dir()
    if gh_dir is not None:
        env["GH_CONFIG_DIR"] = str(gh_dir)
    # 管理領域に入れた CLI（ast-grep）を session から使えるようにする。
    tools = review_tool_bin()
    if tools is not None:
        env["PATH"] = f"{tools}{os.pathsep}{os.environ.get('PATH', '')}"
    # v2 resolves its workspace from PWD; systemd's inherited PWD can still be
    # HOME even when WorkingDirectory points at the selected project.
    if project is not None:
        env["PWD"] = str(project.resolve())
    return env


def isolation_mode() -> str:
    """OpenCode を動かす場所。設定が box でも podman が無ければ host。"""
    if get_settings().get("isolation", "box") == "box" and shutil.which("podman"):
        return "box"
    return "host"


def _opencode_home() -> Path:
    """箱の中の HOME。OpenCode 自身の履歴（~/.local/share/opencode など）は元の場所を共有する。"""
    home = data_dir().resolve() / "sandboxes" / ".opencode-home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _sibling_projects(project: Path) -> list[Path]:
    """箱の中で隠す場所: CodeDEV のプロジェクトなら CodeDEV 全体（その上にこのプロジェクトだけをつなぐ）。"""
    try:
        root = codedev_root().resolve()
    except Exception:  # noqa: BLE001 - 分からなければ隠さない（今まで通り）
        return []
    return [root] if project.resolve().parent == root else []


def project_tmp_dir(project: Path) -> Path:
    """プロジェクトの箱の /tmp（箱の外に残る。プロジェクトごとに別）。

    以前は --rootfs の /tmp（data/sandboxes/.rootfs/tmp）を全部の箱が共有していて、別のプロジェクトの下書きが
    見えた（2026-11-18 利用者「proto1〜3 が Project Lab に無い」で判明。178 ファイル・730 MB）。プロジェクトの
    中には置かない（ブラウザの検証などで大きな一時ファイルが溜まり、git や Project Lab を汚す）。
    """
    import hashlib

    resolved = project.resolve()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in resolved.name)[:40] or "project"
    folder = data_dir().resolve() / "sandboxes" / "project-tmp" / f"{safe}-{hashlib.sha1(str(resolved).encode()).hexdigest()[:10]}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _launch(runtime: str, command: list[str], project: Path, config: Path, *,
            interactive: bool, readonly: tuple[Path, ...] = (), extra_env: dict[str, str] | None = None,
            env_file: Path | None = None) -> tuple[list[str], dict[str, str]]:
    """OpenCode を起こす argv と環境変数。箱なら podman の argv（環境変数は箱の中へ渡す）。

    箱で書けるもの: プロジェクト、この session の設定、UI 検証の写真・MCP の長い結果の置き場、
    OpenCode 自身の履歴。読み取り専用: ホストの /usr・/etc・/opt、管理領域の道具、ControlDeck の
    Python（MCP bridge）と指示ファイル、有効なスキル、Add-on の資産、ターミナルの添付、git の設定、
    （GitHub 連携をオンにしたときだけ）gh の設定。本番の data・秘密鍵・他のプロジェクトは見えない。
    """
    env = {**_runtime_env(runtime, config, project), **(extra_env or {})}
    if isolation_mode() != "box":
        return command, env
    from app.maintenance import sandbox
    from app.terminals import attachments

    home = _opencode_home()
    real_home = Path.home()
    opencode_state = [real_home / ".local" / "share" / "opencode", real_home / ".local" / "state" / "opencode",
                      real_home / ".cache" / "opencode"]
    for path in opencode_state:
        path.mkdir(parents=True, exist_ok=True)
    ui_shots = _integration_dir() / "ui-verify"
    ui_shots.mkdir(parents=True, exist_ok=True)
    writable = [project, config, home, *opencode_state, _mcp_result_dir(), ui_shots]
    readonly_paths = [Path(sys.prefix), Path(__file__).resolve().parent, *map(Path, _skill_paths()),
                      *_addon_asset_roots(), attachments.store.root, *readonly]
    # session の cli.json は共有設定への絶対 symlink。config だけのマウントでは
    # リンク先が箱の外になり、Tab の plan 切替など利用者のキー設定が消える。
    # integration 全体を公開せず、TUI 設定の1枚だけを読み取り専用で渡す。
    readonly_paths.append(cli_config_path())
    from app.features import playwright_browsers

    if playwright_browsers.mcp_browsers_path().is_dir():
        readonly_paths.append(playwright_browsers.mcp_browsers_path())   # MCP の録画用 ffmpeg（読み取り専用）
    gitconfig = real_home / ".gitconfig"
    if gitconfig.is_file():
        readonly_paths.append(gitconfig)
    if env.get("GH_CONFIG_DIR"):
        readonly_paths.append(Path(env["GH_CONFIG_DIR"]))
    box_env = {
        **env,
        "HOME": str(home), "XDG_DATA_HOME": str(real_home / ".local" / "share"),
        "XDG_STATE_HOME": str(real_home / ".local" / "state"), "XDG_CACHE_HOME": str(real_home / ".cache"),
        "GIT_CONFIG_GLOBAL": str(gitconfig) if gitconfig.is_file() else "/dev/null",
        "PLAYWRIGHT_BROWSERS_PATH": str(data_dir().resolve() / "cache" / "ms-playwright"),
        "CONTROL_DECK_OPENCODE_BOX": "1",
        # 管理領域の道具（ast-grep など）は箱の PATH に入っている。ホストの PATH は持ち込まない。
        "PATH": sandbox.tool_path(),
        **sandbox._android_env(),  # noqa: SLF001 - Android SDK が入っているときだけ（入っていなければ空）
    }
    # プロジェクトごとの「USB 機器」（既定は off）。渡すのは今の /sys・/dev から解決した実体だけ
    from app.integrations.opencode import usb

    usb_plan = usb.launch_plan(project)
    if usb_plan is not None:
        box_env.update(usb_plan["env"])
        usb.attach_notes(config, usb_plan["notes"])
    argv = sandbox.container_argv(command, workdir=project, writable=writable, readonly=readonly_paths,
                                  env=box_env, interactive=interactive, env_file=env_file,
                                  binds=[(project_tmp_dir(project), Path("/tmp")), *(usb_plan["binds"] if usb_plan else [])],
                                  hide=_sibling_projects(project), devices=usb_plan["devices"] if usb_plan else [])
    return argv, {}


def _discard_runtime_config(runtime: str, config: Path) -> None:
    """job 別の runtime config（v2 は directory）をまとめて消す。"""
    shutil.rmtree(config, ignore_errors=True)


def _run_argv(runtime: str, binary: Path, message: str, model: str, project: Path) -> list[str]:
    """`opencode run` の argv。系列ごとの差はここだけに閉じる。

    v2 は `--dir` を持たず、作業ディレクトリで対象を決める（呼び出し側が
    systemd-run の --working-directory で渡している）。既定では常駐の
    background service へ繋ぎに行くため、`--standalone` で job 専用のサーバーに
    する。常駐を増やさず、job ごとの config が確実に効く。
    """
    argv = [str(binary), "run", message, "--format", "json", "--auto",
            "--model", f"controldeck/{model}"]
    return argv + ["--standalone"]


def _event_error(event: dict) -> str:
    """error イベントから表に出してよい要約を取る。

    v1 は error.name、v2 は error.type / error.message を返す。どちらも
    prompt や credential を含めない短い識別子だけを拾う。
    """
    error = event.get("error")
    if not isinstance(error, dict):
        return "provider error"
    for key in ("name", "type", "message"):
        value = error.get(key)
        if isinstance(value, str) and value:
            return value[:100]
    return "provider error"


def _managed_project_id(project: Path) -> str | None:
    try:
        relative = project.resolve(strict=True).relative_to(codedev_root().resolve())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return relative.name if len(relative.parts) == 1 and not relative.name.startswith(".") else None


def codedev_root() -> Path:
    """OpenCodeプロジェクトのルート。置き場の決定は config へ委ねる。

    Project Lab も同じ場所を見るため、両者で別々に組み立てない。
    """
    from app.config import codedev_dir

    root = codedev_dir()
    root.mkdir(exist_ok=True)
    return root


def list_projects() -> list[dict]:
    """CodeDEV配下のプロジェクト一覧（更新の新しい順）。"""
    projects = []
    for path in codedev_root().iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        projects.append({
            "name": path.name, "path": str(path),
            "git": (path / ".git").is_dir(), "modified_at": mtime,
        })
    return sorted(projects, key=lambda item: -float(item["modified_at"]))


def ensure_project(name: str) -> dict:
    """プロジェクト名からCodeDEV配下のフォルダを取得（無ければ作成 + git init）。"""
    import subprocess

    cleaned = name.strip()
    if (not cleaned or len(cleaned) > 64 or cleaned in (".", "..")
            or cleaned.startswith(".") or any(c in cleaned for c in "/\\\0")):
        raise CodeAgentError("プロジェクト名は64文字以内で、/ や先頭の . は使えません")
    path = codedev_root() / cleaned
    created = not path.exists()
    path.mkdir(exist_ok=True)
    if created and shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, timeout=15)
    return {"name": cleaned, "path": str(path), "created": created,
            "git": (path / ".git").is_dir()}


def delete_project(name: str) -> dict:
    """CodeDEV配下のプロジェクトをフォルダごと削除する（配下チェック付き）。"""
    cleaned = name.strip()
    if (not cleaned or len(cleaned) > 64 or cleaned in (".", "..")
            or cleaned.startswith(".") or any(c in cleaned for c in "/\\\0")):
        raise CodeAgentError("プロジェクト名が不正です")
    root = codedev_root()
    path = (root / cleaned).resolve()
    if not path.is_relative_to(root) or path == root:
        raise CodeAgentError("CodeDEV配下のプロジェクトのみ削除できます")
    if not path.is_dir():
        raise CodeAgentError("プロジェクトが見つかりません")
    shutil.rmtree(path)
    # 箱の /tmp（プロジェクトごと・箱の外）も消す。残すと誰も使わない一時ファイルが溜まる
    shutil.rmtree(project_tmp_dir(path), ignore_errors=True)
    return {"name": cleaned, "path": str(path)}


def import_project(source_path: str) -> dict:
    """CodeDEV外のフォルダをCodeDEVへコピーして取り込む（管理下は素通し）。

    依存物などの重量ディレクトリ（node_modules/.venv等）は再生成可能なため
    コピー対象から除外する。名前衝突時は -2, -3 と連番を付ける。
    """
    import shutil as _shutil

    source = files.resolve(source_path)
    if not source.is_dir():
        raise CodeAgentError("フォルダを指定してください")
    root = codedev_root()
    if source == root:
        raise CodeAgentError("CodeDEVルート自体は開けません。プロジェクトを選択してください")
    if source.is_relative_to(root):
        return {"name": source.name, "path": str(source), "imported": False}
    base = source.name or "project"
    destination = root / base
    counter = 2
    while destination.exists():
        destination = root / f"{base}-{counter}"
        counter += 1
    _shutil.copytree(
        source, destination,
        ignore=_shutil.ignore_patterns("node_modules", ".venv", "venv", "__pycache__",
                                       ".mypy_cache", ".pytest_cache"),
        symlinks=True,
    )
    return {"name": destination.name, "path": str(destination), "imported": True}


RESUME_SESSION_RE = re.compile(r"ses_[A-Za-z0-9_-]{8,64}")


def project_sessions(project: str, runtime: str = "") -> list[dict]:
    """そのプロジェクトの OpenCode の会話（新しい順）。`opencode session list --format json`。

    会話の保存先は TUI と AI アシスタントで共有なので、両方の会話が混ざって返る。
    利用者の ~/.config/opencode は読ませない（空の config directory を渡す）。
    """
    import subprocess
    import tempfile

    _, binary = active_binary(runtime)
    with tempfile.TemporaryDirectory(prefix="cd-oc-list-") as empty:
        env = {**os.environ, "XDG_CONFIG_HOME": empty, "OPENCODE_CONFIG_DIR": empty}
        try:
            result = subprocess.run(
                [str(binary), "session", "list", "--standalone", "--format", "json", "-n", "50"],
                cwd=project, env=env, capture_output=True, text=True, timeout=30,
            )
            rows = json.loads(result.stdout or "[]") if result.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []
    out = [row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
           and str(row.get("directory") or "").rstrip("/") == str(project).rstrip("/")]
    out.sort(key=lambda row: float(row.get("updated") or 0), reverse=True)
    return out


def tui_command(
    *,
    project_path: str,
    prompt: str = "",
    base_url: str = "",
    model: str = "",
    owner_user_id: int | None = None,
    runtime: str = "",
    resume: str = "",
    config_key: str = "",
    validated_project: Path | None = None,
) -> tuple[str, str]:
    """対話TUIセッション用のshellコマンドを組み立てる。(command, project_dir)を返す。

    config_key: 設定フォルダを分ける印（英数字）。同じ利用者が別のモデルの TUI を並べて開くとき
    （AI Commander）に、後から開いた方の設定で先の TUI の設定を書き換えないようにする。

    resume: "" は新しい会話、"last" はそのプロジェクトの最後の会話の続き（--continue）、
    "ses_…" はその会話の続き（--session）。TUI は閉じると終わるので、開き直すときに使う。

    ターミナル基盤（tmux）の上でopencode TUIをそのまま動かす。設定は永続config
    （Control Deck LLM provider）を都度再生成して渡す。
    """
    import shlex

    runtime, binary = active_binary(runtime)
    settings = get_settings()
    endpoint = (base_url or settings["base_url"]).strip().rstrip("/")
    model_id = (model or settings["model"]).strip()
    if not endpoint.startswith(("http://", "https://")) or not model_id:
        raise CodeAgentError("LLM endpointとmodelを設定してください")
    if validated_project is not None:
        # 呼び出し側が自分の決まりで検証済みのフォルダ（Nexarch の作業フォルダ: commander/workdir.py が
        # /data1tb・/data2tb の中・data_dir と拒否の場所の外を realpath で確かめた実体のパス）。
        project = validated_project
        if not project.is_absolute() or project.resolve() != project:
            raise CodeAgentError("作業フォルダは実体の絶対パスで渡してください")
    else:
        raw_project = project_path or settings.get("project_path") or str(Path.home())
        try:
            project = files.resolve(raw_project)
        except (files.FileAccessError, FileNotFoundError) as exc:
            raise CodeAgentError(str(exc)) from exc
    if not project.is_dir():
        raise CodeAgentError("project pathはディレクトリを指定してください")
    config = _runtime_config(
        f"tui-{owner_user_id or 0}" + (f"-{config_key}" if config_key else ""), endpoint, model_id,
        owner_user_id=owner_user_id, project_id=_managed_project_id(project),
        runtime=runtime,
    )
    # v2 の root command は --model を持たない（model は config で決まる）。
    # 常駐 service へ繋がないよう --standalone を付ける。TUI を閉じたら一緒に終わる。
    argv = [str(binary)]
    argv += ["--standalone"]
    if resume == "last":
        argv += ["--continue"]
    elif resume:
        if not RESUME_SESSION_RE.fullmatch(resume):
            raise CodeAgentError("続きから開く会話の ID が正しくありません")
        argv += ["--session", resume]
    if prompt.strip():
        argv += ["--prompt", prompt.strip()]
    argv.append(str(project))
    argv, launch_env = _launch(runtime, argv, project, config, interactive=True)
    env = " ".join(f"{key}={shlex.quote(value)}" for key, value in launch_env.items())
    command = (f"{env} " if env else "") + "exec " + " ".join(shlex.quote(a) for a in argv)
    return command, str(project)


def _prompt(operation: str, instruction: str) -> str:
    labels = {
        "analyze": "コードを読み取り、問題点と改善案を分析してください。ファイルは変更しないでください。",
        "implement": "要求を実装し、必要なテストも更新してください。",
        "fix": "不具合を再現・原因特定して修正し、回帰テストを追加してください。",
        "test": "対象をテストし、失敗があれば原因と再現手順を報告してください。",
        "review": "変更をレビューし、重大度順に具体的な指摘を報告してください。ファイルは変更しないでください。",
    }
    return f"{labels[operation]}\n\nユーザー要求:\n{instruction.strip()}"


def _extract_text(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            found.append(value["text"])
        elif isinstance(value.get("content"), str) and value.get("type") in ("message", "result"):
            found.append(value["content"])
        for child in value.values():
            found.extend(_extract_text(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_extract_text(child))
    return found


def _find_session_id(value: Any) -> str:
    """opencode JSONイベントからセッションIDを再帰探索する（継続対話用）。"""
    if isinstance(value, dict):
        for key in ("sessionID", "session_id", "sessionId"):
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
        for child in value.values():
            found = _find_session_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_session_id(child)
            if found:
                return found
    return ""


async def run_chat(
    job: Job, *, instruction: str, project_name: str = "", project_path: str = "",
    session_id: str = "", on_text=None, on_event=None,
) -> dict:
    """AIチャット用のheadless実行。JSONイベントを逐次読み、本文テキストを

    on_text コールバックへストリームする（Codex/Claude風のチャット内コーディング）。
    session_id 指定で前回のopencodeセッションを継続する。
    """
    if not instruction.strip():
        raise CodeAgentError("指示が空です")
    runtime, binary = active_binary()
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    if systemd_run is None or systemctl is None:
        raise CodeAgentError("systemd user managerを利用できません")
    settings = get_settings()
    if project_name.strip():
        project = Path(ensure_project(project_name)["path"])
    elif (project_path or "").strip():
        # CodeDEV外のフォルダはコピーして取り込んでから開く
        imported = await asyncio.to_thread(import_project, project_path)
        project = Path(imported["path"])
    else:
        raw = settings.get("project_path") or ""
        if not raw:
            raise CodeAgentError("プロジェクトを指定してください")
        project = files.resolve(raw)
    if not project.is_dir():
        raise CodeAgentError("project pathはディレクトリを指定してください")
    endpoint = str(settings["base_url"]).rstrip("/")
    model_id = str(settings["model"]).strip()
    runtime_config = await asyncio.to_thread(_runtime_config,
        f"chat-{job.id}", endpoint, model_id, owner_user_id=job.owner_user_id,
        project_id=await asyncio.to_thread(_managed_project_id, project),
        runtime=runtime,
    )
    # LLM endpoint（llama.cpp instance）はondemand hookを通らないため先に起動保証する
    from app.models_mgmt import local_llm

    await local_llm.ensure_ready_by_base_url(endpoint)
    unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
    command = _run_argv(runtime, binary, instruction[:32_000], model_id, project)
    if session_id:
        command += ["--session", session_id]
    command, launch_env = _launch(runtime, command, project, runtime_config, interactive=False)
    argv = [
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        f"--unit={unit}", f"--working-directory={project}",
        *(f"--setenv={key}={value}" for key, value in launch_env.items()),
        *command,
    ]
    job.set_progress("OpenCodeを起動", 0, 1)
    events = 0
    emitted: set[str] = set()
    found_session = ""
    reported_error = ""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )

        async def drain_stderr() -> bytes:
            assert proc is not None and proc.stderr is not None
            return await proc.stderr.read()

        stderr_task = asyncio.create_task(drain_stderr())
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            events += 1
            if on_event is not None:
                await on_event(str(event.get("type") or ""), events)
            if not found_session:
                found_session = _find_session_id(event)
            if event.get("type") == "error":
                reported_error = _event_error(event)
            for text in _extract_text(event):
                cleaned = text.strip()
                if not cleaned or cleaned in emitted:
                    continue
                emitted.add(cleaned)
                if on_text is not None:
                    await on_text(cleaned)
            if events % 5 == 0:
                job.set_progress("OpenCode実行中", events, 0)
        await stderr_task
        await proc.wait()
    except asyncio.CancelledError:
        stop = await asyncio.create_subprocess_exec(
            systemctl, "--user", "stop", f"{unit}.service",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await stop.wait()
        raise
    finally:
        await asyncio.to_thread(_discard_runtime_config, runtime, runtime_config)
    if reported_error:
        raise CodeAgentError(f"OpenCode provider error: {reported_error}")
    if proc is None or proc.returncode != 0:
        raise CodeAgentError(f"OpenCode実行失敗（終了コード {proc.returncode if proc else 'unknown'}）")
    output = "\n\n".join(emitted)
    job.set_progress("完了", 1, 1)
    return {"output": output[-100_000:], "events": events,
            "session_id": found_session, "project_path": str(project)}


class OpenCodeProvider:
    async def run(
        self, job: Job, *, operation: str, project_path: str, instruction: str,
        base_url: str = "", model: str = "",
    ) -> dict:
        if operation not in OPERATIONS:
            raise CodeAgentError("未対応のoperationです")
        if not instruction.strip() or len(instruction) > 32_000:
            raise CodeAgentError("instructionは1〜32000文字で指定してください")
        try:
            project = files.resolve(project_path)
        except (files.FileAccessError, FileNotFoundError) as exc:
            raise CodeAgentError(str(exc)) from exc
        if not project.is_dir():
            raise CodeAgentError("project pathはディレクトリを指定してください")
        settings = get_settings()
        endpoint = (base_url or settings["base_url"]).strip().rstrip("/")
        model_id = (model or settings["model"]).strip()
        runtime, binary = active_binary()
        systemd_run = shutil.which("systemd-run")
        systemctl = shutil.which("systemctl")
        if systemd_run is None or systemctl is None:
            raise CodeAgentError("systemd user managerを利用できません")
        runtime_config = await asyncio.to_thread(_runtime_config,
            job.id, endpoint, model_id, owner_user_id=job.owner_user_id,
            project_id=await asyncio.to_thread(_managed_project_id, project),
            runtime=runtime,
        )
        prompt_path = (_integration_dir() / f"prompt-{job.id}.txt").resolve()
        if not prompt_path.is_relative_to(_integration_dir()):
            raise CodeAgentError("prompt pathがintegration directory外です")
        prompt_path.write_text(_prompt(operation, instruction), encoding="utf-8")
        os.chmod(prompt_path, 0o600)
        unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
        command = [*_run_argv(runtime, binary, "添付されたControl Deckの指示を実行してください。",
                              model_id, project), "--file", str(prompt_path)]
        command, launch_env = _launch(runtime, command, project, runtime_config, interactive=False,
                                      readonly=(prompt_path,))
        argv = [
            systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
            f"--unit={unit}", f"--working-directory={project}",
            *(f"--setenv={key}={value}" for key, value in launch_env.items()),
            *command,
        ]
        job.set_progress("OpenCodeを起動", 0, 1)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            stop = await asyncio.create_subprocess_exec(
                systemctl, "--user", "stop", f"{unit}.service",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await stop.wait()
            raise
        finally:
            prompt_path.unlink(missing_ok=True)
            _discard_runtime_config(runtime, runtime_config)
        if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
            raise CodeAgentError("OpenCode出力が上限を超えました")
        if proc is None or proc.returncode != 0:
            # stderrはprovider/pluginがpromptやcredentialを含める可能性があるため公開しない。
            raise CodeAgentError(f"OpenCode実行失敗（終了コード {proc.returncode if proc else 'unknown'}）")
        events = []
        text_parts: list[str] = []
        reported_error = ""
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") == "error":
                reported_error = _event_error(event)
            text_parts.extend(_extract_text(event))
            if len(events) % 10 == 0:
                job.set_progress("OpenCode実行中", len(events), 0)
        if reported_error:
            raise CodeAgentError(f"OpenCode provider error: {reported_error}")
        output = "\n".join(dict.fromkeys(part.strip() for part in text_parts if part.strip()))
        job.set_progress("完了", 1, 1)
        return {"output": output[-100_000:], "events": len(events), "operation": operation,
                "project_path": str(project), "model": model_id}


provider = OpenCodeProvider()
