"""AI Commander のコンソール: Terminal の tmux セッション + 印。

新しい PTY の仕組みは作らない。コンソールは「CLI の実行ファイルとアカウントの
環境変数で開いた Terminal のセッション」に、tmux の user option（@cd-commander-*）
で印を付けたもの。コマンドは CLIS の表の実行ファイル名だけを使い、
利用者の入力は混ぜない。環境変数の値は accounts が作ったフォルダのパスだけ。
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex

from app.commander import accounts, status, usage, workdir
from app.integrations.opencode import projects
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
    usage_cache: dict[tuple[str, str], dict | None] = {}
    for row in rows:
        cli_id = str(row["commander_cli"])
        if cli_id not in labels:
            labels[cli_id] = accounts.labels_for(cli_id) if cli_id in CLIS else {}
        label = labels[cli_id].get(str(row.get("commander_account") or ""), "")
        if label:
            row["commander_account_label"] = label
        login = accounts.login_name(cli_id, str(row.get("commander_account") or accounts.DEFAULT_ACCOUNT)) \
            if cli_id in CLIS else ""
        if login:
            row["commander_login_name"] = login
        # CodeDEV 直下の作業場所なら、フォルダ名の代わりに表示名を見せる
        if row.get("cwd"):
            shown = projects.display_name_if_codedev(str(row["cwd"]))
            if shown:
                row["commander_workdir_name"] = shown
        # C2: 画面の文字から状態と最後の行（LLM なし）
        text = manager.capture_text(str(row["id"]), status.CAPTURE_LINES) if row.get("alive", True) else ""
        judged = status.classify(cli_id, text, alive=bool(row.get("alive", True)),
                                 program=str(row.get("program") or ""))
        key = (cli_id, str(row.get("commander_account") or accounts.DEFAULT_ACCOUNT))
        if key not in usage_cache:
            usage_cache[key] = usage.usage_for(*key)
        if usage_cache[key]:
            row["commander_usage"] = usage_cache[key]
        row["commander_state"] = judged["state"]
        row["commander_last_line"] = judged["last_line"]
        row["commander_detail"] = judged["detail"]
        # 鍵（ロック）は boolean で出す（frontend との契約。印は "1" の文字列で持つ）。
        row["commander_locked"] = row.get("commander_locked") == "1"
    return rows


def _command_for(cli: CliDef, workdir: str, user_id: int | None,
                 account: str = accounts.DEFAULT_ACCOUNT, model: str = "",
                 effort: str = "", *, resume: bool = False) -> tuple[str, dict[str, str] | None]:
    """開くコマンドと環境変数。

    ControlDeck の OpenCode は OpenCode 画面と同じ tui_command（OpenCode の設定の API・
    job ごとの設定フォルダ・箱）で開く。ほかは CLIS の実行ファイルをアカウントの環境変数で開く。
    resume=True のとき続きから（鍵をかけたコンソールの開き直し）: Claude は --continue、
    Codex は resume --last、OpenCode は既存の tui_command(resume="last")。
    """
    if cli.connector == accounts.CONNECTOR_CONTROLDECK_OPENCODE:
        from app.integrations.opencode import model_choice, provider

        if model_choice.is_zen(model):
            # TUI の設定はゲートウェイのモデルだけを持つ（Zen の鍵を渡す道が無い）。チームの画面のない実行で使う
            raise ValueError("OpenCode（Zen・Go）のモデルはコンソールでは使えません。AI チームの担い手で選んでください")
        try:
            connection = accounts.opencode_connection(account)
            command, _project = provider.tui_command(
                project_path=workdir, owner_user_id=user_id, model=model or connection["model"],
                # Nexarch の作業フォルダの決まり（/data1tb・/data2tb）で検証済みの実体のパス
                validated_project=Path(workdir),
                # 接続ごとに設定フォルダを分ける（並べて開いた別の接続の TUI の設定を書き換えない）
                config_key="" if account == accounts.DEFAULT_ACCOUNT else account,
                # 続きから: そのプロジェクトの最後の会話（--continue）
                resume="last" if resume else "",
            )
        except provider.CodeAgentError as exc:
            raise ValueError(str(exc)) from exc
        return command, None
    # 見つけた絶対パスで開き、その場所を PATH の先頭に足す（codex は `#!/usr/bin/env node` の
    # スクリプトなので、同じ場所の道具を PATH で探す）。
    path = accounts.executable_path(cli) or cli.executable
    # API キー（従量課金）は使わない: 鍵の環境変数を消してから開く（tmux のサーバーの環境を引き継がない）。
    # Codex は forced_login_method で ChatGPT のログインだけにする（accounts.API_KEY_ENV の説明）。
    command = accounts.UNSET_API_KEYS + " " + shlex.quote(path)
    if cli.id == "codex":
        command += " " + " ".join(shlex.quote(a) for a in accounts.CODEX_SUBSCRIPTION_ARGS)
    if cli.id == "claude":
        # 5 時間・週の使用率を statusline から受け取る（usage.py。鍵は読まない）
        command += " --settings " + shlex.quote(str(usage.claude_settings_file(account)))
    # モデル・考えるレベル（空なら付けない。値は models.clean_* で検証済み＝英数と ._:/#- だけ）
    if model:
        command += {"claude": " --model ", "codex": " -m "}.get(cli.id, " --model ") + shlex.quote(model)
    if effort and cli.id == "claude":
        command += " --effort " + shlex.quote(effort)
    elif effort and cli.id == "codex":
        command += " -c " + shlex.quote(f"model_reasoning_effort={effort}")
    # 続きから（鍵をかけたコンソールの開き直し）。CLI ごとの公式の再開の形だけを使う。
    if resume:
        if cli.id == "claude":
            command += " --continue"
        elif cli.id == "codex":
            command += " resume --last"
    if "/" not in path:
        return command, None
    folder = os.path.dirname(path)
    current = os.environ.get("PATH", "/usr/bin:/bin")
    env_path = current if folder in current.split(os.pathsep) else f"{folder}{os.pathsep}{current}"
    return command, {"PATH": env_path[:4096]}


def create_consoles(
    *, cli: CliDef, account: str, cwd: str, count: int, label: str, created_by: str,
    user_id: int | None = None, model: str = "", effort: str = "", resume: bool = False,
    session_id: str | None = None,
) -> dict:
    """コンソールを count 個開く。

    Terminal の上限（config.terminal.max_sessions）に当たったら、
    開けた分と開けなかった数と理由を返す（例外にはしない）。
    作業フォルダは workdir.resolve_dir で commander.workdir_roots の中か検証する。
    resume=True は続きから（鍵をかけたコンソールの開き直し。locks.py から使う）。
    """
    cfg = get_config().terminal
    if not cfg.enabled:
        raise RuntimeError("ターミナルは無効化されています")
    work = workdir.resolve_dir(cwd)  # 存在しない・ルートの外は例外（router が 400 へ直す）
    env = accounts.env_for(cli, account)  # 既定のアカウントなら None（環境変数を付けない）
    from app.commander import models as model_list

    model, effort = model_list.clean_model(model), model_list.clean_effort(effort)  # 不正なら ValueError
    command, extra_env = _command_for(cli, str(work), user_id, account, model, effort, resume=resume)
    if extra_env:
        env = {**extra_env, **(env or {})}

    opened: list[dict] = []
    reason = ""
    for _ in range(count):
        if len(manager.list_sessions()) >= cfg.max_sessions:
            reason = (
                f"セッション数の上限（{cfg.max_sessions}）に達しました。"
                "config/config.yaml の terminal.max_sessions で変えられます"
            )
            break
        if session_id is not None and count != 1:
            raise ValueError("復旧先 ID は 1 件の作成にだけ使えます")
        session = manager.create_session(cwd=str(work), command=command, env=env,
                                         **({"session_id": session_id} if session_id else {}))
        marks = {"cli": cli.id, "account": account, "created_by": created_by}
        if label:
            marks["label"] = label
        # 開いたときのモデル・考えるレベルも印へ（鍵をかけたときの保存・開き直しに使う）
        if model:
            marks["model"] = model
        if effort:
            marks["effort"] = effort
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
