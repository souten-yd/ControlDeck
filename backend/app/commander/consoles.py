"""AI Commander のコンソール: Terminal の tmux セッション + 印。

新しい PTY の仕組みは作らない。コンソールは「CLI の実行ファイルとアカウントの
環境変数で開いた Terminal のセッション」に、tmux の user option（@cd-commander-*）
で印を付けたもの。コマンドは CLIS の表の実行ファイル名だけを使い、
利用者の入力は混ぜない。環境変数の値は accounts が作ったフォルダのパスだけ。
"""
from __future__ import annotations

from app.commander import accounts, status
from app.commander.accounts import CLIS, CliDef
from app.config import get_config
from app.files import service as files
from app.terminals.manager import manager

# 1 回の「開く」で作れる個数の上限（c1-spec.md）。
MAX_BATCH = 8


def list_consoles() -> list[dict]:
    """印のある Terminal セッション（= AI Commander のコンソール）だけを返す。

    アカウントの画面の名前（既定のアカウントにも付けられる）を commander_account_label に添える。
    """
    rows = [s for s in manager.list_sessions() if s.get("commander_cli")]
    labels: dict[str, dict[str, str]] = {}
    for row in rows:
        cli_id = str(row["commander_cli"])
        if cli_id not in labels:
            labels[cli_id] = accounts.labels_for(cli_id) if cli_id in CLIS else {}
        label = labels[cli_id].get(str(row.get("commander_account") or ""), "")
        if label:
            row["commander_account_label"] = label
        # C2: 画面の文字から状態と最後の行（LLM なし）
        text = manager.capture_text(str(row["id"]), status.CAPTURE_LINES) if row.get("alive", True) else ""
        judged = status.classify(cli_id, text, alive=bool(row.get("alive", True)),
                                 program=str(row.get("program") or ""))
        row["commander_state"] = judged["state"]
        row["commander_last_line"] = judged["last_line"]
        row["commander_detail"] = judged["detail"]
    return rows


def _command_for(cli: CliDef, workdir: str, user_id: int | None,
                 account: str = accounts.DEFAULT_ACCOUNT) -> tuple[str, dict[str, str] | None]:
    """開くコマンドと環境変数。

    ControlDeck の OpenCode は OpenCode 画面と同じ tui_command（OpenCode の設定の API・
    job ごとの設定フォルダ・箱）で開く。ほかは CLIS の実行ファイルをアカウントの環境変数で開く。
    """
    if cli.connector == accounts.CONNECTOR_CONTROLDECK_OPENCODE:
        from app.integrations.opencode import provider
        try:
            connection = accounts.opencode_connection(account)
            command, _project = provider.tui_command(
                project_path=workdir, owner_user_id=user_id, model=connection["model"],
                # 接続ごとに設定フォルダを分ける（並べて開いた別の接続の TUI の設定を書き換えない）
                config_key="" if account == accounts.DEFAULT_ACCOUNT else account,
            )
        except provider.CodeAgentError as exc:
            raise ValueError(str(exc)) from exc
        return command, None
    return cli.executable, None


def create_consoles(
    *, cli: CliDef, account: str, cwd: str, count: int, label: str, created_by: str,
    user_id: int | None = None,
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
    command, _ = _command_for(cli, str(workdir), user_id, account)

    opened: list[dict] = []
    reason = ""
    for _ in range(count):
        if len(manager.list_sessions()) >= cfg.max_sessions:
            reason = (
                f"セッション数の上限（{cfg.max_sessions}）に達しました。"
                "config/config.yaml の terminal.max_sessions で変えられます"
            )
            break
        session = manager.create_session(cwd=str(workdir), command=command, env=env)
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
                **({"commander_account_label": name} if (name := accounts.account_label(cli.id, account)) else {}),
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
