"""AI Commander の CLI アカウント（設定ディレクトリ）の管理。

アカウント = `data_dir()/ai-commander/accounts/<cli>/<名前>/` のフォルダ。
開くときに CLI ごとの環境変数（CLAUDE_CONFIG_DIR など）をこのフォルダへ向ける。
ログインは利用者がそのアカウントのコンソールの中で行う。ControlDeck は
フォルダの中身（auth.json・.credentials.json など）を読まない・返さない。
「ログイン済みか」はファイルの有無だけで判定する（公式の保存場所だけ）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import data_dir

# アカウント名。CLI 側の設定フォルダの名前にもなるので、素朴で安全な形だけ。
ACCOUNT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEFAULT_ACCOUNT = "default"
# 画面に出す名前（既定のアカウントにも付けられる）。フォルダの名前とは別。
LABEL_MAX = 32
# ControlDeck の OpenCode 画面と同じ形で開く（OpenCode の設定の API: ゲートウェイ・OpenRouter・Zen）
CONNECTOR_CONTROLDECK_OPENCODE = "controldeck-opencode"


@dataclass(frozen=True)
class CliDef:
    """1 つの AI の CLI の定義。

    env_targets: アカウントを分ける環境変数と、アカウントフォルダのどこへ向けるか。
      "dir" = フォルダ自身、ほかの値 = フォルダ直下のその名前のサブフォルダ。
    login_files: 「ログイン済み」判定に見るファイル（アカウントフォルダからの相対パス。
      1 つでもあれば「あり」。公式の保存場所が不確かな CLI では空 = 判定しない）。
    default_home_dir: 既定のアカウント（環境変数を付けない）の設定フォルダの HOME からの相対。
      ログイン済みの判定だけに使う（空なら判定しない）。
    connector: 空なら executable をそのまま開く。CONNECTOR_CONTROLDECK_OPENCODE なら
      ControlDeck の OpenCode（v2）を OpenCode 画面の API 設定で開く（provider.tui_command）。
    """

    id: str
    name: str
    executable: str
    env_targets: tuple[tuple[str, str], ...] = ()
    login_files: tuple[str, ...] = ()
    default_home_dir: str = ""
    connector: str = ""


# CLI の表。実行ファイル名はここにあるものだけを使う（利用者の入力を混ぜない）。
CLIS: dict[str, CliDef] = {
    "claude": CliDef(
        id="claude", name="Claude Code", executable="claude",
        env_targets=(("CLAUDE_CONFIG_DIR", "dir"),),
        login_files=(".credentials.json", ".claude.json"),
        default_home_dir=".claude",
    ),
    "codex": CliDef(
        id="codex", name="Codex CLI", executable="codex",
        env_targets=(("CODEX_HOME", "dir"),),
        login_files=("auth.json",),
        default_home_dir=".codex",
    ),
    # OpenCode は ControlDeck の OpenCode（v2）を、OpenCode 画面に登録した API（ゲートウェイ・
    # OpenRouter・Zen）で開く。接続先は OpenCode の設定が決めるので、アカウントは既定の 1 つだけ。
    "opencode": CliDef(
        id="opencode", name="OpenCode", executable="opencode2",
        connector=CONNECTOR_CONTROLDECK_OPENCODE,
    ),
    # Gemini CLI はアカウントを分ける確かな方法が無い（HOME を変えると他の道具も変わる）。
    # C1 では既定のアカウントだけ。アカウントの作成・選択は断る。
    "gemini": CliDef(
        id="gemini", name="Gemini CLI", executable="gemini",
    ),
}


def accounts_root() -> Path:
    return data_dir() / "ai-commander" / "accounts"


def cli_ids() -> list[str]:
    return list(CLIS)


# ControlDeck の service の PATH には、利用者が npm・bun などで入れた CLI の場所が入っていない
# （2026-11-27 実機: claude・codex は ~/.local/npm-global/bin にあるのに「未導入」と出た）。
# PATH の次にこれらの場所を探す。実行ファイル名は CLIS の表のものだけ。
USER_BIN_DIRS = (".local/npm-global/bin", ".npm-global/bin", ".local/bin", ".bun/bin",
                 ".volta/bin", ".cargo/bin", "bin")


def executable_path(cli: CliDef) -> str | None:
    """CLI の実行ファイルの絶対パス。PATH → 利用者の bin の順に探す。無ければ None。"""
    found = shutil.which(cli.executable)
    if found:
        return found
    home = Path.home()
    for rel in USER_BIN_DIRS:
        candidate = home / rel / cli.executable
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def cli_info(cli: CliDef) -> dict:
    """一覧用の CLI の情報（アカウントの有無は含めない。別途 list_accounts）。"""
    return {
        "id": cli.id,
        "name": cli.name,
        "executable": cli.executable,
        "installed": is_installed(cli),
        "multi_account": bool(cli.env_targets),
        "connector": cli.connector,
    }


def is_installed(cli: CliDef) -> bool:
    """開けるか。無いものは開けない（画面でも選べない）。

    ControlDeck の OpenCode は、オプション機能が有効で実行ファイルがあるとき（OpenCode 画面と同じ判定）。
    """
    if cli.connector == CONNECTOR_CONTROLDECK_OPENCODE:
        from app.integrations.opencode import provider
        try:
            provider.active_binary()
        except provider.CodeAgentError:
            return False
        return True
    return executable_path(cli) is not None


# ---- OpenCode の接続（OpenCode 画面に登録した API） --------------------------------------

def opencode_connections() -> list[dict]:
    """OpenCode のアカウント = OpenCode 画面に登録した接続（ゲートウェイの設定のモデルと、送信を許した外部モデル）。

    name は印・環境に使うので ACCOUNT_NAME_RE の形（既定は "default"、外部は "api-" + 識別子の hash）。
    model は tui_command へ渡すモデル（ゲートウェイ経由）。title は画面の既定の見せ方。
    """
    from app.integrations.opencode import provider

    settings = provider.get_settings()
    model = str(settings.get("model") or "auto")
    rows = [{"name": DEFAULT_ACCOUNT, "model": model,
             "title": f"OpenCode の設定のモデル（{model}・ゲートウェイ）"}]
    try:
        cloud = provider._cloud_models_for_opencode()
    except Exception:  # noqa: BLE001 - 外部モデルの都合で一覧を止めない
        cloud = []
    for item in cloud:
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        digest = hashlib.sha1(model_id.encode("utf-8")).hexdigest()[:10]
        provider_name = str(item.get("provider") or "")
        name = str(item.get("name") or model_id)
        rows.append({"name": f"api-{digest}", "model": model_id,
                     "title": f"{name}（{provider_name}）" if provider_name else name})
    return rows


def opencode_connection(account: str) -> dict:
    for row in opencode_connections():
        if row["name"] == account:
            return row
    raise FileNotFoundError("OpenCode の接続が見つかりません（OpenCode 画面の設定を確かめてください）")


def account_exists(cli: CliDef, account: str) -> bool:
    if account == DEFAULT_ACCOUNT:
        return True
    if not ACCOUNT_NAME_RE.fullmatch(account):
        return False
    if cli.connector == CONNECTOR_CONTROLDECK_OPENCODE:
        return any(row["name"] == account for row in opencode_connections())
    return account_dir(cli, account).is_dir()


# ---- 画面に出す名前 ----------------------------------------------------------------------

def _labels_path() -> Path:
    return accounts_root().parent / "labels.json"


def _read_labels() -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(_labels_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {cli: {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str)}
            for cli, names in raw.items() if cli in CLIS and isinstance(names, dict)}


def _write_labels(labels: dict[str, dict[str, str]]) -> None:
    path = _labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def account_label(cli_id: str, account: str) -> str:
    return _read_labels().get(cli_id, {}).get(account, "")


def labels_for(cli_id: str) -> dict[str, str]:
    return dict(_read_labels().get(cli_id, {}))


def clean_label(value: str) -> str:
    label = value.strip()
    if len(label) > LABEL_MAX or any(ord(c) < 0x20 or ord(c) == 0x7f for c in label):
        raise ValueError(f"名前は {LABEL_MAX} 文字以内で、制御文字（tab・改行など）は使えません")
    return label


def set_account_label(cli: CliDef, account: str, label: str) -> str:
    """アカウントに画面の名前を付ける（空なら外す）。既定のアカウントにも付けられる。"""
    if account != DEFAULT_ACCOUNT:
        validate_name(account)
        if not account_exists(cli, account):
            raise FileNotFoundError("アカウントが見つかりません")
    label = clean_label(label)
    labels = _read_labels()
    names = labels.setdefault(cli.id, {})
    if label:
        names[account] = label
    else:
        names.pop(account, None)
    _write_labels(labels)
    return label


def account_dir(cli: CliDef, name: str) -> Path:
    return accounts_root() / cli.id / name


def validate_name(name: str) -> str:
    if not ACCOUNT_NAME_RE.fullmatch(name):
        raise ValueError(
            "アカウント名は英小数字・_・- で 32 文字以内（先頭は英小数字）にしてください"
        )
    return name


def _prepare_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def create_account(cli: CliDef, name: str) -> Path:
    """アカウントのフォルダを作る。中身は空（ログインは利用者が CLI の中で行う）。"""
    if name == DEFAULT_ACCOUNT:
        raise ValueError("「default」は既定のアカウントを表す名前なので使えません")
    validate_name(name)
    if not cli.env_targets:
        raise ValueError(f"{cli.name} は既定のアカウントだけ使えます")
    directory = account_dir(cli, name)
    if directory.exists():
        raise FileExistsError(f"アカウント「{name}」は既にあります")
    _prepare_dir(directory)
    # 環境変数が指す先（opencode の config・data など）も作っておく。
    for _var, target in cli.env_targets:
        if target != "dir":
            _prepare_dir(directory / target)
    return directory


def delete_account(cli: CliDef, name: str) -> None:
    """アカウントをフォルダごと削除する（ログインの情報も消える。監査は router 側）。"""
    directory = account_dir(cli, validate_name(name))
    # リンクを辿って accounts の外を消さない（rmtree は中のリンクは辿らないが、入口は確かめる）。
    if directory.is_symlink():
        raise ValueError("アカウントのフォルダがリンクになっているため削除しません")
    if not directory.is_dir():
        raise FileNotFoundError("アカウントが見つかりません")
    shutil.rmtree(directory)
    labels = _read_labels()
    if labels.get(cli.id, {}).pop(name, None) is not None:
        _write_labels(labels)


def logged_in(cli: CliDef, directory: Path) -> bool | None:
    """「ログイン済み」をファイルの有無だけで判定する。判定できない CLI は None。

    ファイルの中身（鍵・トークン）は読まない・返さない。
    """
    if not cli.login_files:
        return None
    return any((directory / name).exists() for name in cli.login_files)


def _default_logged_in(cli: CliDef) -> bool | None:
    """既定のアカウント（環境変数を付けない＝利用者の普段のフォルダ）のログイン済み。"""
    if not cli.default_home_dir or not cli.login_files:
        return None
    return logged_in(cli, Path.home() / cli.default_home_dir)


# ---- ログインしているアカウントの名前（鍵は読まない） ---------------------------------------------

_LOGIN_CACHE_S = 300
_login_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _claude_config_file(account: str) -> Path:
    if account == DEFAULT_ACCOUNT:
        base = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"
    return account_dir(CLIS["claude"], validate_name(account)) / ".claude.json"


def _claude_login(account: str) -> str:
    """Claude Code の .claude.json の oauthAccount.emailAddress（ログインのメール。鍵ではない）。"""
    try:
        raw = json.loads(_claude_config_file(account).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    oauth = raw.get("oauthAccount") if isinstance(raw, dict) else None
    email = oauth.get("emailAddress") if isinstance(oauth, dict) else None
    return str(email)[:120] if isinstance(email, str) else ""


def _codex_login(account: str) -> str:
    """`codex login status` の 1 行目を短く（メールは鍵のファイルにしか無いので出さない）。"""
    import subprocess

    exe = executable_path(CLIS["codex"])
    if not exe:
        return ""
    env = {**os.environ}
    if account != DEFAULT_ACCOUNT:
        env["CODEX_HOME"] = str(account_dir(CLIS["codex"], validate_name(account)))
    try:
        done = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=15,
                              env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    line = ((done.stdout or done.stderr).strip().splitlines() or [""])[0]
    if "ChatGPT" in line:
        return "ChatGPT でログイン"
    if "API key" in line or "API Key" in line:
        return "API キーでログイン"
    return ""


def login_name(cli_id: str, account: str) -> str:
    """そのアカウントでログインしている名前（Claude はメール、Codex はログインの方法）。分からなければ空。"""
    if cli_id not in ("claude", "codex"):
        return ""
    import time

    key = (cli_id, account)
    cached = _login_cache.get(key)
    if cached and time.monotonic() - cached[0] < _LOGIN_CACHE_S:
        return cached[1]
    try:
        value = _claude_login(account) if cli_id == "claude" else _codex_login(account)
    except (ValueError, KeyError):
        value = ""
    _login_cache[key] = (time.monotonic(), value)
    return value


def list_accounts(cli: CliDef) -> list[dict]:
    """アカウントの一覧。既定のアカウントは常に先頭（作る・消すはできない）。"""
    labels = labels_for(cli.id)
    if cli.connector == CONNECTOR_CONTROLDECK_OPENCODE:
        return [{"name": row["name"], "default": row["name"] == DEFAULT_ACCOUNT, "logged_in": None,
                 "label": labels.get(row["name"], ""), "title": row["title"]}
                for row in opencode_connections()]
    rows = [{"name": DEFAULT_ACCOUNT, "default": True, "logged_in": _default_logged_in(cli),
             "label": labels.get(DEFAULT_ACCOUNT, "")}]
    root = accounts_root() / cli.id
    if root.is_dir():
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and ACCOUNT_NAME_RE.fullmatch(directory.name):
                rows.append({
                    "name": directory.name,
                    "default": False,
                    "logged_in": logged_in(cli, directory),
                    "label": labels.get(directory.name, ""),
                })
    # 5 時間・週の使用率（取れる CLI だけ。commander/usage.py）
    from app.commander import usage

    for row in rows:
        row["usage"] = usage.usage_for(cli.id, row["name"])
        row["login_name"] = login_name(cli.id, row["name"])
        if cli.id == "claude":
            row["usage_statusline"] = usage.statusline_state(row["name"])
    return rows


def env_for(cli: CliDef, account: str) -> dict[str, str] | None:
    """コンソールを開くときの環境変数。既定のアカウントなら None（付けない）。

    値は accounts_root 配下のパスだけ。検証済みのパス以外は create_session に渡さない。
    """
    if account == DEFAULT_ACCOUNT:
        return None
    if cli.connector == CONNECTOR_CONTROLDECK_OPENCODE:
        opencode_connection(validate_name(account))   # 接続はモデルで分ける（環境変数は付けない）
        return None
    if not cli.env_targets:
        raise ValueError(f"{cli.name} は既定のアカウントだけ使えます")
    directory = account_dir(cli, validate_name(account))
    if not directory.is_dir():
        raise FileNotFoundError("アカウントが見つかりません")
    env: dict[str, str] = {}
    for var, target in cli.env_targets:
        target_dir = directory if target == "dir" else _prepare_dir(directory / target)
        env[var] = str(target_dir)
    return env
