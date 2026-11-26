"""AI Commander のコンソール: Terminal の tmux セッション + 印。

新しい PTY の仕組みは作らない。コンソールは「CLI の実行ファイルとアカウントの
環境変数で開いた Terminal のセッション」に、tmux の user option（@cd-commander-*）
で印を付けたもの。コマンドは CLIS の表の実行ファイル名だけを使い、
利用者の入力は混ぜない。環境変数の値は accounts が作ったフォルダのパスだけ。
"""
from __future__ import annotations

from app.commander import accounts
from app.commander.accounts import CLIS, CliDef
from app.config import get_config
from app.files import service as files
from app.terminals.manager import manager

# 1 回の「開く」で作れる個数の上限（c1-spec.md）。
MAX_BATCH = 8


def list_consoles() -> list[dict]:
    """印のある Terminal セッション（= AI Commander のコンソール）だけを返す。"""
    return [s for s in manager.list_sessions() if s.get("commander_cli")]


def create_consoles(
    *, cli: CliDef, account: str, cwd: str, count: int, label: str, created_by: str,
) -> dict:
    """コンソールを count 個開く。

    Terminal の上限（config.terminal.max_sessions）に当たったら、
    開けた分と開けなかった数と理由を返す（例外にはしない）。
    作業フォルダは files.resolve で許可ルートの中か検証する。
    """
    cfg = get_config().terminal
    if not cfg.enabled:
        raise RuntimeError("ターミナルは無効化されています")
    workdir = files.resolve(cwd)  # 存在しない・許可の外は例外（router が 400 へ直す）
    if not workdir.is_dir():
        raise NotADirectoryError(f"ディレクトリではありません: {cwd}")
    env = accounts.env_for(cli, account)  # 既定のアカウントなら None（環境変数を付けない）

    opened: list[dict] = []
    reason = ""
    for _ in range(count):
        if len(manager.list_sessions()) >= cfg.max_sessions:
            reason = (
                f"セッション数の上限（{cfg.max_sessions}）に達しました。"
                "config/config.yaml の terminal.max_sessions で変えられます"
            )
            break
        session = manager.create_session(cwd=str(workdir), command=cli.executable, env=env)
        marks = {"cli": cli.id, "account": account, "created_by": created_by}
        if label:
            marks["label"] = label
        try:
            manager.tag_session(session["id"], marks)
        except (RuntimeError, OSError, ValueError):
            # 印の無いセッションはコンソールとして一覧へ出ないので、閉じて止める。
            manager.kill_session(session["id"])
            reason = "セッション属性を保存できなかったため、残りは開きませんでした"
            break
        opened.append(session)

    return {
        "opened": [
            {
                **session,
                "commander_cli": cli.id,
                "commander_account": account,
                "commander_label": label,
            }
            for session in opened
        ],
        "not_opened": count - len(opened),
        "reason": reason,
    }


def close_console(session_id: str) -> None:
    """コンソールを閉じる。Terminal のセッションを閉じるのと同じ。"""
    manager.kill_session(session_id)


def console_cli(cli_id: str) -> CliDef:
    """cli_id を CLIS の表へ引き当てる。無ければ KeyError（router が 400 へ直す）。"""
    return CLIS[cli_id]
