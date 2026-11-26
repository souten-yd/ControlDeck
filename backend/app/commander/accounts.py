"""AI Commander の CLI アカウント（設定ディレクトリ）の管理。

アカウント = `data_dir()/ai-commander/accounts/<cli>/<名前>/` のフォルダ。
開くときに CLI ごとの環境変数（CLAUDE_CONFIG_DIR など）をこのフォルダへ向ける。
ログインは利用者がそのアカウントのコンソールの中で行う。ControlDeck は
フォルダの中身（auth.json・.credentials.json など）を読まない・返さない。
「ログイン済みか」はファイルの有無だけで判定する（公式の保存場所だけ）。
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import data_dir

# アカウント名。CLI 側の設定フォルダの名前にもなるので、素朴で安全な形だけ。
ACCOUNT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEFAULT_ACCOUNT = "default"


@dataclass(frozen=True)
class CliDef:
    """1 つの AI の CLI の定義。

    env_targets: アカウントを分ける環境変数と、アカウントフォルダのどこへ向けるか。
      "dir" = フォルダ自身、ほかの値 = フォルダ直下のその名前のサブフォルダ。
    login_files: 「ログイン済み」判定に見るファイル（アカウントフォルダからの相対パス。
      1 つでもあれば「あり」。公式の保存場所が不確かな CLI では空 = 判定しない）。
    default_home_dir: 既定のアカウント（環境変数を付けない）の設定フォルダの HOME からの相対。
      ログイン済みの判定だけに使う（空なら判定しない）。
    """

    id: str
    name: str
    executable: str
    env_targets: tuple[tuple[str, str], ...] = ()
    login_files: tuple[str, ...] = ()
    default_home_dir: str = ""


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
    "opencode": CliDef(
        id="opencode", name="OpenCode", executable="opencode",
        env_targets=(("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data")),
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


def cli_info(cli: CliDef) -> dict:
    """一覧用の CLI の情報（アカウントの有無は含めない。別途 list_accounts）。"""
    return {
        "id": cli.id,
        "name": cli.name,
        "executable": cli.executable,
        "installed": shutil.which(cli.executable) is not None,
        "multi_account": bool(cli.env_targets),
    }


def is_installed(cli: CliDef) -> bool:
    """CLI の実行ファイルが PATH にあるか。無いものは開けない（画面でも選べない）。"""
    return shutil.which(cli.executable) is not None


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


def list_accounts(cli: CliDef) -> list[dict]:
    """アカウントの一覧。既定のアカウントは常に先頭（作る・消すはできない）。"""
    rows = [{"name": DEFAULT_ACCOUNT, "default": True, "logged_in": _default_logged_in(cli)}]
    root = accounts_root() / cli.id
    if root.is_dir():
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and ACCOUNT_NAME_RE.fullmatch(directory.name):
                rows.append({
                    "name": directory.name,
                    "default": False,
                    "logged_in": logged_in(cli, directory),
                })
    return rows


def env_for(cli: CliDef, account: str) -> dict[str, str] | None:
    """コンソールを開くときの環境変数。既定のアカウントなら None（付けない）。

    値は accounts_root 配下のパスだけ。検証済みのパス以外は create_session に渡さない。
    """
    if account == DEFAULT_ACCOUNT:
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
