"""Nexarch の担い手のモデルと考えるレベルの一覧（T3b）。

担い手の指定は「コンソール（種類・CLI・アカウント）」と「モデル（モデル・考えるレベル）」を別々に選ぶ。
ここはモデル側の自動の一覧と検証。

- claude: CLI の別名（fable・opus・sonnet・haiku）。`claude --help` のとおり完全な名前も入力できる（自由入力）。
  考えるレベルは `--effort`（low・medium・high・xhigh・max）。
- codex: アカウントの CODEX_HOME（無ければ ~/.codex）の `models_cache.json` の slug と
  `supported_reasoning_levels[].effort` だけを読む。**auth.json など鍵のファイルは読まない**。読めなければ空の一覧＋自由入力。
- opencode: ControlDeck のゲートウェイのモデル（OpenCode 画面と同じ local_llm.llm_instances と、外部送信を許した外部モデル）と、
  接続（アカウント）の既定のモデル。考えるレベルは `--model provider/model#variant` の variant（一覧は持てないので無し・自由入力）。
- 結果は 5 分覚える。外へは何も取りに行かない。
根拠: 利用者（2026-11-27）「設定はコンソールとモデルを個別に設定できること。モデルは自動でリストアップし、考えるレベルも選べるように」。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300
CLAUDE_MODELS = ("fable", "opus", "sonnet", "haiku")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_ORDER = CLAUDE_EFFORTS + ("ultra",)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,79}$")
EFFORT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,19}$")
CODEX_CACHE_FILE = "models_cache.json"
CODEX_CACHE_MAX = 2 * 1024 * 1024

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = threading.Lock()


def clean_model(value: object) -> str:
    """モデルの指定。空は「付けない」。英数と ._:/#- だけ・先頭は英数・80 文字まで（引数の取り違えを防ぐ）。"""
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not MODEL_RE.fullmatch(value.strip()):
        raise ValueError("モデルは英数と . _ : / # - だけの 80 文字以内（先頭は英数）で指定してください")
    return value.strip()


def clean_effort(value: object) -> str:
    """考えるレベル。空は「付けない」。"""
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not EFFORT_RE.fullmatch(value.strip()):
        raise ValueError("考えるレベルは英小文字と数字だけの 20 文字以内で指定してください")
    return value.strip()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _codex_home(account: str) -> Path:
    from app.commander import accounts

    default = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if account and account != accounts.DEFAULT_ACCOUNT and accounts.ACCOUNT_NAME_RE.fullmatch(account):
        folder = accounts.account_dir(accounts.CLIS["codex"], account)
        if (folder / CODEX_CACHE_FILE).is_file():
            return folder
    return default


def _codex_models(account: str) -> list[dict]:
    """models_cache.json の slug・表示名・考えるレベルだけを読む（他の欄・鍵のファイルは読まない）。"""
    path = _codex_home(account) / CODEX_CACHE_FILE
    try:
        if path.stat().st_size > CODEX_CACHE_MAX:
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = raw.get("models") if isinstance(raw, dict) else None
    out: list[dict] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict) or item.get("visibility") == "hide":
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not MODEL_RE.fullmatch(slug):
            continue
        levels = item.get("supported_reasoning_levels")
        efforts = [lv["effort"] for lv in levels if isinstance(lv, dict) and isinstance(lv.get("effort"), str)
                   and EFFORT_RE.fullmatch(lv["effort"])] if isinstance(levels, list) else []
        default = item.get("default_reasoning_level")
        out.append({"id": slug, "label": str(item.get("display_name") or slug)[:60], "efforts": efforts,
                    "default_effort": default if isinstance(default, str) and default in efforts else ""})
    return out


def _opencode_models(account: str) -> tuple[list[dict], str]:
    from app.commander import accounts
    from app.models_mgmt import local_llm

    default_model = ""
    try:
        default_model = str(accounts.opencode_connection(account or accounts.DEFAULT_ACCOUNT)["model"])
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass
    ids: list[tuple[str, str]] = []
    if default_model:
        ids.append((default_model, f"{default_model}（この接続の既定）"))
    try:
        ids += [(str(i["alias"]), str(i["alias"])) for i in local_llm.llm_instances() if i.get("alias")]
    except Exception:  # noqa: BLE001 - ローカルのモデルの一覧を取れなくても動かす
        logger.warning("ゲートウェイのモデルの一覧を読めませんでした")
    try:
        from app.integrations.opencode import provider

        ids += [(str(i["id"]), str(i.get("name") or i["id"]))
                for i in provider._cloud_models_for_opencode() if i.get("id")]  # noqa: SLF001
    except Exception:  # noqa: BLE001
        logger.warning("外部モデルの一覧を読めませんでした")
    try:  # OpenCode（Zen・Go）のモデル。キーがあるときだけ（チームの画面のない実行で使える。コンソールは断る）
        from app.integrations.opencode import model_choice, zen

        if zen.configured():
            ids += [(model_id, f"{name}（OpenCode）") for model_id, (name, _note) in model_choice.ZEN_MODELS.items()]
    except Exception:  # noqa: BLE001
        logger.warning("OpenCode Zen のモデルの一覧を読めませんでした")
    seen: set[str] = set()
    out: list[dict] = []
    for model_id, label in ids:
        if model_id in seen or not MODEL_RE.fullmatch(model_id):
            continue
        seen.add(model_id)
        out.append({"id": model_id, "label": label[:80], "efforts": [], "default_effort": ""})
    return out, default_model


def _build(cli: str, account: str) -> dict:
    if cli == "claude":
        models = [{"id": m, "label": m, "efforts": list(CLAUDE_EFFORTS), "default_effort": ""} for m in CLAUDE_MODELS]
        return {"models": models, "efforts": list(CLAUDE_EFFORTS), "default_model": "", "source": "cli",
                "note": "別名のほか、完全なモデルの名前も入力できます"}
    if cli == "codex":
        models = _codex_models(account)
        efforts = sorted({e for m in models for e in m["efforts"]},
                         key=lambda e: EFFORT_ORDER.index(e) if e in EFFORT_ORDER else 99)
        return {"models": models, "efforts": efforts, "default_model": "", "source": "codex-cache" if models else "none",
                "note": "" if models else "一覧を読めませんでした。モデルの名前を入力してください"}
    models, default_model = _opencode_models(account)
    return {"models": models, "efforts": [], "default_model": default_model, "source": "gateway" if models else "none",
            "note": "考えるレベルは OpenCode のモデルの variant（モデルが持つときだけ入力）"}


def list_models(cli: str, account: str = "default") -> dict:
    """そのコンソール（CLI・アカウント）で選べるモデルと考えるレベル。5 分覚える。cli が不明なら ValueError。"""
    if cli not in ("claude", "codex", "opencode"):
        raise ValueError("CLI は claude・codex・opencode のどれかにしてください")
    account = account or "default"
    key = (cli, account)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = {"cli": cli, "account": account, "free_input": True, **_build(cli, account)}
    with _lock:
        _cache[key] = (now, result)
    return result


# ---------------------------------------------------------------- 役割用のモデルの統合一覧（B）

ROLE_GROUPS = ("local", "opencode", "openrouter", "claude", "codex")


def role_choices() -> dict:
    """役割の設定に並べる、使えるモデルの統合一覧（2026-11-28 利用者「使えるモデルを事前に抽出して選択肢から
    選べるように。役割を決める所でモデルを選べるとよい」）。

    - group: local（ゲートウェイのローカル）・opencode（Zen・Go）・openrouter（外部）・claude（CLI の別名）・codex（models_cache）。
    - 選んだ choice の endpoint（kind・cli・account・model・effort）は、そのまま役割の endpoint へ渡せる形
      （選択で CLI・接続が決まる。アカウントの細かい選び方はエキスパートの設定で上書きできる）。
    - 同じモデルが複数の接続にあるときは先頭（既定側）の接続だけを出す。
    - 外へは何も取りに行かない。一覧は list_models（5 分覚える・鍵のファイルは読まない）の結果を種類ごとに分けるだけ。
    """
    from app.commander import accounts
    from app.integrations.opencode import model_choice

    choices: list[dict] = []
    seen: set[str] = set()

    def add(cli: str, account: str, model: str, label: str, group: str, efforts: list[str]) -> None:
        if f"{cli}:{model}" in seen:
            return
        seen.add(f"{cli}:{model}")
        choices.append({"id": f"{cli}:{account}:{model}", "label": str(label)[:80], "group": group,
                        "endpoint": {"kind": "headless", "cli": cli, "account": account, "model": model, "effort": ""},
                        "efforts": list(efforts)})

    for m in list_models("claude", accounts.DEFAULT_ACCOUNT)["models"]:
        add("claude", accounts.DEFAULT_ACCOUNT, str(m["id"]), str(m["label"]), "claude", m["efforts"])
    for row in accounts.list_accounts(accounts.CLIS["codex"]):
        for m in list_models("codex", str(row["name"]))["models"]:
            add("codex", str(row["name"]), str(m["id"]), str(m["label"]), "codex", m["efforts"])
    for row in accounts.opencode_connections():
        for m in list_models("opencode", str(row["name"]))["models"]:
            model = str(m["id"])
            group = "opencode" if model_choice.is_zen(model) else ("openrouter" if model.startswith("cloud:") else "local")
            add("opencode", str(row["name"]), model, str(m["label"]), group, m["efforts"])
    note = ("Claude は別名のほか完全なモデル名も入力できます / Codex の一覧は models_cache.json から（読めなければ名前を入力） / "
            "OpenCode の考えるレベルはモデルの variant")
    return {"choices": choices, "note": note}
