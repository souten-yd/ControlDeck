"""AI Commander の API。権限は Terminal と同じ（terminal.use）。

- GET    /commander/clis                    CLI の一覧（導入済みか・アカウントを分けられるか）
- GET    /commander/accounts/{cli}          アカウントの一覧（ログイン済みの印つき）
- POST   /commander/accounts/{cli}          アカウントの作成
- DELETE /commander/accounts/{cli}/{name}    アカウントの削除（ログインの情報も消える）
- PUT    /commander/accounts/{cli}/{name}/label  画面の名前（既定のアカウントにも付けられる）
- GET    /commander/consoles                コンソールの一覧（commander_locked は boolean）
- POST   /commander/consoles                コンソールを個数ぶん開く
- PUT    /commander/consoles/{session_id}/lock  コンソールの鍵（ロック）の付け外し
- DELETE /commander/consoles/{session_id}   コンソールを閉じる（ロック中は 409）
- DELETE /commander/consoles                コンソールを一括で閉じる（ロックは飛ばして数を返す）
- /commander/teams ...                      AI チーム（タスク板・送り込み。commander/teams.py）

アカウントの作成・削除とコンソールの作成・削除は監査ログへ残す
（アカウントのフォルダにはログインの情報が入るため）。
endpoint は def で threadpool へ任せる（tmux の subprocess で event loop を止めない）。
コンソールを開くだけは async で、OpenCode のときに LLM を裏で温めてから threadpool で開く。
"""
from __future__ import annotations

import asyncio
import logging
import re

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.commander import accounts, consoles, teams, workdir
from app.database import get_db
from app.models import User
from app.schemas.commander import (
    AccountCreate, AccountLabel, CommanderSettingsBody, ConsoleAutoResume, ConsoleCreate, ConsoleLock,
    ResumeMessage, UsageStatusline, WorkdirPath,
)
from app.security.deps import require_permission
from app.terminals.router import streams

router = APIRouter(prefix="/commander", tags=["commander"])
logger = logging.getLogger(__name__)
# OpenCode の LLM を温める task（GC されないよう持っておく。OpenCode 画面と同じ方式）
_warmups: set[asyncio.Task] = set()


def _cli_or_400(cli_id: str) -> accounts.CliDef:
    try:
        return consoles.console_cli(cli_id)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"不明な CLI です: {cli_id}") from None


@router.get("/clis")
def list_clis(user: User = Depends(require_permission("terminal.use"))):
    del user
    return {"clis": [accounts.cli_info(cli) for cli in accounts.CLIS.values()]}


@router.get("/accounts/{cli_id}")
def list_accounts(cli_id: str, user: User = Depends(require_permission("terminal.use"))):
    del user
    cli = _cli_or_400(cli_id)
    return {"cli": accounts.cli_info(cli), "accounts": accounts.list_accounts(cli)}


@router.post("/accounts/{cli_id}", status_code=201)
def create_account(
    cli_id: str,
    body: AccountCreate,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    cli = _cli_or_400(cli_id)
    try:
        accounts.create_account(cli, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "commander.account.create", user=user, resource_type="commander_account",
        resource_id=f"{cli.id}/{body.name}", request=request,
    )
    return {"name": body.name, "logged_in": False}


@router.delete("/accounts/{cli_id}/{name}", status_code=204)
def delete_account(
    cli_id: str,
    name: str,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """アカウントをフォルダごと削除する。中のログインの情報も消える（確認は画面側）。"""
    cli = _cli_or_400(cli_id)
    if name == accounts.DEFAULT_ACCOUNT:
        raise HTTPException(status_code=400, detail="既定のアカウントは削除できません")
    try:
        accounts.delete_account(cli, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(
        db, "commander.account.delete", user=user, resource_type="commander_account",
        resource_id=f"{cli.id}/{name}", request=request,
    )


@router.put("/accounts/{cli_id}/{name}/label")
def set_account_label(
    cli_id: str,
    name: str,
    body: AccountLabel,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """アカウントに画面の名前を付ける（空で外す）。既定のアカウントにも付けられる。"""
    cli = _cli_or_400(cli_id)
    try:
        label = accounts.set_account_label(cli, name, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(
        db, "commander.account.label", user=user, resource_type="commander_account",
        resource_id=f"{cli.id}/{name}", request=request,
    )
    return {"name": name, "label": label}


def _warm_opencode_llm() -> None:
    """OpenCode は ControlDeck の起動の hook を通らないので、設定の LLM を裏で起こしておく（待たない）。"""
    from app.integrations.opencode import provider
    from app.models_mgmt import local_llm

    endpoint = str(provider.get_settings().get("base_url") or "").strip().rstrip("/")
    if not endpoint:
        return
    task = asyncio.create_task(local_llm.ensure_ready_by_base_url(endpoint))
    _warmups.add(task)
    task.add_done_callback(_warmups.discard)


def _workdir_http(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/workdir/roots")
def workdir_roots(user: User = Depends(require_permission("terminal.use"))):
    del user
    return workdir.list_roots()


@router.get("/workdir/list")
def workdir_list(path: str, user: User = Depends(require_permission("terminal.use"))):
    del user
    try:
        return workdir.list_dirs(path)
    except (PermissionError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise _workdir_http(exc) from exc


@router.post("/workdir/directory")
def workdir_mkdir(
    body: WorkdirPath,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    try:
        created = workdir.make_directory(body.path)
    except (PermissionError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise _workdir_http(exc) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="同じ名前があります") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"フォルダを作れません: {exc.strerror}") from exc
    audit.record(db, "commander.workdir.mkdir", user=user, resource_type="commander_workdir",
                 resource_id=created, request=request)
    return {"ok": True, "path": created}


@router.get("/consoles")
def list_consoles(user: User = Depends(require_permission("terminal.use"))):
    del user
    return {"consoles": consoles.list_consoles()}


@router.post("/consoles", status_code=201)
async def create_consoles(
    body: ConsoleCreate,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    cli = _cli_or_400(body.cli)
    if not accounts.is_installed(cli):
        raise HTTPException(status_code=400, detail=f"{cli.name} は導入されていません")
    if body.account != accounts.DEFAULT_ACCOUNT and not cli.env_targets and not cli.connector:
        raise HTTPException(status_code=400, detail=f"{cli.name} は既定のアカウントだけ使えます")
    if cli.connector == accounts.CONNECTOR_CONTROLDECK_OPENCODE:
        try:
            _warm_opencode_llm()
        except Exception:  # noqa: BLE001 - 温めに失敗してもコンソールは開く
            logger.exception("OpenCode の LLM を温められませんでした")
    cwd = body.cwd
    workspace: dict | None = None
    if body.new_workspace is not None:
        # 作る前に、CodeDEV が Nexarch の作業フォルダのルートの中か確かめる（外なら作らない）
        from app.integrations.opencode import projects as auto_projects

        try:
            await asyncio.to_thread(workdir.resolve_dir, str(auto_projects.codedev_root()))
            workspace = await asyncio.to_thread(
                auto_projects.create_auto_project, "nexarch", body.new_workspace.name)
            cwd = workspace["path"]
        except (PermissionError, FileNotFoundError, NotADirectoryError, auto_projects.ProjectError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="作業場所を作れません") from exc
    try:
        result = await asyncio.to_thread(
            consoles.create_consoles,
            cli=cli, account=body.account, cwd=cwd,
            count=body.count, label=body.label, created_by=user.username, user_id=user.id,
            model=body.model, effort=body.effort,
        )
    except PermissionError as exc:  # files.resolve の許可の外
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:  # アカウント名・環境変数の不正
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:  # 作業フォルダ・アカウントが見つからない
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "commander.console.create", user=user, resource_type="commander_console",
        resource_id=f"{cli.id}/{body.account}",
        request=request,
        metadata={
            "cli": cli.id,
            "account": body.account,
            "count": body.count,
            "opened": len(result["opened"]),
            "cwd": cwd,
            "label": body.label,
            **({"new_workspace": workspace["folder"]} if workspace else {}),
        },
    )
    if workspace:
        result["workspace"] = workspace
    return result


@router.put("/consoles/{session_id}/autoresume")
def set_console_autoresume(
    session_id: str,
    body: ConsoleAutoResume,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """上限からの自動再開をコンソールごとに入り切りする（既定は入）。"""
    from app.terminals.manager import manager

    if not any(s.get("id") == session_id for s in consoles.list_consoles()):
        raise HTTPException(status_code=404, detail="コンソールが見つかりません")
    if body.enabled:
        manager.clear_marks(session_id, ("autoresume",))
    else:
        manager.tag_session(session_id, {"autoresume": "off"})
    audit.record(db, "commander.console.autoresume_setting", user=user, resource_type="commander_console",
                 resource_id=session_id, request=request, metadata={"enabled": body.enabled})
    return {"ok": True, "enabled": body.enabled}


@router.put("/accounts/claude/{name}/usage-statusline")
def set_usage_statusline(
    name: str,
    body: UsageStatusline,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """Claude のアカウントの設定に、5 時間・週の使用率を書く statusline を足す・外す（Nexarch の外の分も取る）。"""
    from app.commander import usage

    if name != accounts.DEFAULT_ACCOUNT and not accounts.account_exists(accounts.CLIS["claude"], name):
        raise HTTPException(status_code=404, detail="アカウントが見つかりません")
    try:
        state = usage.set_account_statusline(name, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "commander.account.usage_statusline", user=user, resource_type="commander_account",
                 resource_id=f"claude/{name}", request=request, metadata={"enabled": body.enabled})
    return {"name": name, "usage_statusline": state}


@router.get("/settings")
def get_settings(user: User = Depends(require_permission("terminal.use"))):
    """全体の設定（自動再開の文言）。"""
    del user
    from app.commander import settings as commander_settings

    return commander_settings.get()


@router.put("/settings")
def put_settings(
    body: CommanderSettingsBody,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """全体の設定。送った欄だけ変える（resume_message を省略してもリセットしない）。"""
    from app.commander import settings as commander_settings

    changed: dict = {}
    if body.resume_message is not None:
        try:
            commander_settings.set_resume_message(body.resume_message)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        changed["resume_message"] = bool(body.resume_message)
    if body.auto_restore is not None:
        commander_settings.set_auto_restore(body.auto_restore)
        changed["auto_restore"] = body.auto_restore
    audit.record(db, "commander.settings", user=user, resource_type="commander", resource_id="settings",
                 request=request, metadata=changed or None)
    return commander_settings.get()


@router.put("/consoles/{session_id}/resume-message")
def set_console_resume_message(
    session_id: str,
    body: ResumeMessage,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """そのコンソールだけの再開の文言。空なら全体の設定を使う。"""
    from app.commander import settings as commander_settings
    from app.terminals.manager import manager

    if not any(s.get("id") == session_id for s in consoles.list_consoles()):
        raise HTTPException(status_code=404, detail="コンソールが見つかりません")
    try:
        message = commander_settings.clean_message(body.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if message:
        manager.tag_session(session_id, {"resume_message": message})
    else:
        manager.clear_marks(session_id, ("resume_message",))
    audit.record(db, "commander.console.resume_message", user=user, resource_type="commander_console",
                 resource_id=session_id, request=request, metadata={"own": bool(message)})
    return {"ok": True, "message": message}


@router.put("/consoles/{session_id}/lock")
def set_console_lock(
    session_id: str,
    body: ConsoleLock,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """コンソールの鍵（ロック）の付け外し。body={locked:boolean} → {ok:true,locked:boolean}。

    鍵のかかったコンソールは誰にも勝手に閉じられない（manager.kill_session の中央ガード）。
    外すのはこの操作（terminal.use の利用者。画面）だけで、AI・MCP へは公開しない。
    情報（cli・account・cwd・label・model・effort・owner）は data へ保存する
    （commander/locks.py。tmux が消えた後も分かるように）。
    """
    from app.commander import locks

    console = next((s for s in consoles.list_consoles() if s.get("id") == session_id), None)
    if body.locked:
        if console is None:
            raise HTTPException(status_code=404, detail="コンソールが見つかりません")
        try:
            locks.lock_console(console, user=user)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="ロックの情報を保存できませんでした") from exc
        except (RuntimeError, KeyError) as exc:
            # セッションが今まさに消えた等。一覧へ戻してもらう
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit.record(
            db, "commander.console.lock", user=user, resource_type="commander_console",
            resource_id=session_id, request=request,
            metadata={
                "cli": console.get("commander_cli", ""),
                "account": console.get("commander_account", ""),
                "label": console.get("commander_label", ""),
            },
        )
        return {"ok": True, "locked": True}
    # 解除。tmux にセッションがもう無い（外から消えた）コンソールでも、保存の記録は消せる。
    try:
        if console is None and locks.find_entry(session_id) is None:
            raise HTTPException(status_code=404, detail="コンソールが見つかりません")
        locks.unlock_console(session_id)
    except (OSError, RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="ロックを解除できませんでした。保護を継続します") from exc
    audit.record(
        db, "commander.console.unlock", user=user, resource_type="commander_console",
        resource_id=session_id, request=request,
        metadata={"cli": (console or {}).get("commander_cli", "")},
    )
    return {"ok": True, "locked": False}


@router.delete("/consoles")
def close_all_consoles(
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    """コンソールを一括で閉じる。ロックのかかったコンソールは飛ばして数を返す。

    応答は {closed:number, skipped_locked:number}。ロックは画面で外してもらう前提で、
    ここでは 1 つも閉じない（409 にしない。一括の途中で 1 つ引っかかると全部止まるので）。
    """
    from app.terminals.manager import SessionLockedError

    closed = 0
    skipped_locked = 0
    for row in consoles.list_consoles():
        sid = str(row["id"])
        if row.get("commander_locked"):
            skipped_locked += 1
            continue
        try:
            consoles.close_console(sid)
            streams.close_session(sid)
            closed += 1
        except SessionLockedError:
            # 一覧を見てから鍵をかけられた競合。中央ガードが守ったので飛ばしとして数える
            skipped_locked += 1
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail="状態を確認できないため閉じませんでした") from exc
    audit.record(
        db, "commander.console.close_all", user=user, resource_type="commander_console",
        resource_id="all", request=request,
        metadata={"closed": closed, "skipped_locked": skipped_locked},
    )
    return {"closed": closed, "skipped_locked": skipped_locked}


@router.delete("/consoles/{session_id}")
def delete_console(
    session_id: str,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    from app.terminals.manager import SESSION_LOCKED_MESSAGE, SessionLockedError

    # 終了に成功してから WS を閉じる。中央 guard が断ったときは接続も保つ。
    console = next(
        (s for s in consoles.list_consoles() if s.get("id") == session_id), None,
    )
    if console is None:
        raise HTTPException(status_code=404, detail="コンソールが見つかりません")
    if console.get("commander_locked"):
        # streams.close_session より前に断る（接続を閉じる前に拒否）
        raise HTTPException(status_code=409, detail=SESSION_LOCKED_MESSAGE)
    try:
        consoles.close_console(session_id)
        streams.close_session(session_id)
    except SessionLockedError as exc:
        # 一覧を見てから鍵をかけられた競合も中央ガードが守る
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="セッションを閉じられませんでした") from exc
    audit.record(
        db, "commander.console.kill", user=user, resource_type="commander_console",
        resource_id=session_id, request=request,
        metadata={
            "cli": console.get("commander_cli", ""),
            "account": console.get("commander_account", ""),
        },
    )
    return {"ok": True}


# ---------------------------------------------------------------- AI チーム（T1）。保存と検証は commander/teams.py

class _TeamName(BaseModel):
    name: str = Field(max_length=200)
    # AI アシスタントから作るときは created_by="assistant" と、その会話の作業場所（workdir。検証は teams.py）
    workdir: str = Field(default="", max_length=4096)
    created_by: str = Field(default="", max_length=20)


class _TeamUpdate(BaseModel):
    """チームの名前・前提（brief）・送る前のリセット。送った欄だけ変える（検証は teams.py）。"""
    name: str | None = Field(default=None, max_length=200)
    brief: str | None = Field(default=None, max_length=20000)
    reset_before_dispatch: bool | None = None
    on_limit: str | None = Field(default=None, max_length=20)
    fallback: list[str] | None = Field(default=None, max_length=60)
    max_parallel: int | None = None
    local_parallel: int | None = None
    auto_run: bool | None = None
    codex_without_sandbox: bool | None = None
    git_init: bool | None = None
    web_research: bool | None = None
    autonomy: str | None = Field(default=None, max_length=10)


class _TeamMembers(BaseModel):
    members: list[dict[str, Any]] = Field(max_length=50)


class _TeamRoles(BaseModel):
    roles: list[dict[str, Any]] = Field(max_length=20)


class _TeamLeader(BaseModel):
    leader: dict[str, Any] | None = None


class _AutoTeam(BaseModel):
    """目標からチームを作る（チームリーダーが計画する）。作業場所が空なら新しい自動の作業場所。"""
    goal: str = Field(max_length=20000)
    name: str = Field(default="", max_length=200)
    workdir: str = Field(default="", max_length=4096)


class _LeaderRun(BaseModel):
    goal: str = Field(max_length=20000)


class _Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class _HandoffChoice(BaseModel):
    endpoint: dict[str, Any]
    add_to_secondary: bool = False


class _TeamDefaults(BaseModel):
    default_leader: dict[str, Any] | None = None
    default_roles: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    # チームの保存先（親フォルダ）。渡したときだけ変える・空文字は未設定へ戻す
    team_root: str | None = Field(default=None, max_length=4096)


class _TeamNotify(BaseModel):
    notify: bool


class _TaskBody(BaseModel):
    """作成・更新で使う欄。更新は送った欄だけ変える（検証は teams.py）。"""
    title: str | None = Field(default=None, max_length=1000)
    goal: str | None = Field(default=None, max_length=20000)
    acceptance: str | None = Field(default=None, max_length=20000)
    files: list[str] | None = Field(default=None, max_length=200)
    checks: list[str] | None = Field(default=None, max_length=100)
    assignee: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=20)
    role: str | None = Field(default=None, max_length=16)


class _ReportBody(BaseModel):
    summary: str = Field(max_length=10000)
    changed_files: list[str] = Field(default_factory=list, max_length=200)
    tests: str = Field(default="", max_length=10000)
    verify: str = Field(default="", max_length=10000)
    remaining: str = Field(default="", max_length=10000)


def _team_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except teams.TeamError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _team_audit(db, user, request, action: str, team_id: str, **meta) -> None:
    audit.record(db, f"commander.team.{action}", user=user, resource_type="commander_team",
                 resource_id=team_id, request=request, metadata=meta or None)


@router.get("/teams")
def list_teams(user: User = Depends(require_permission("terminal.use"))):
    del user
    return {"teams": teams.list_teams()}


@router.post("/teams", status_code=201)
def create_team(
    body: _TeamName, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    team = _team_call(teams.create_team, body.name, workdir_path=body.workdir, created_by=body.created_by)
    _team_audit(db, user, request, "create", team["id"], name=team["name"], created_by=body.created_by)
    return team


@router.put("/teams/{team_id}")
def update_team(
    team_id: str, body: _TeamUpdate, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    team = _team_call(teams.update_team, team_id, body.model_dump())
    _team_audit(db, user, request, "update", team_id, name=team["name"], brief_length=len(team.get("brief") or ""),
                reset_before_dispatch=team.get("reset_before_dispatch", True), on_limit=team.get("on_limit"),
                max_parallel=team.get("max_parallel"), local_parallel=team.get("local_parallel"),
                auto_run=team.get("auto_run"))
    return team


@router.get("/models")
def list_models(cli: str, account: str = "default", user: User = Depends(require_permission("terminal.use"))):
    """担い手のモデルと考えるレベルの自動の一覧（claude の別名・codex の models_cache・ゲートウェイ）。"""
    del user
    from app.commander import models as model_list

    try:
        return model_list.list_models(cli, account)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/team-defaults")
def put_team_defaults(
    body: _TeamDefaults, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """チームの既定（既定のチームリーダー・既定の役割）。新しいチームはこれを写して始まる。"""
    from app.commander import settings as commander_settings
    from app.commander import team_roles

    def work():
        leader_ep = team_roles.clean_endpoint_or_none(body.default_leader, "既定のチームリーダー")
        roles = None if body.default_roles is None else team_roles.clean_roles(body.default_roles)
        extra: dict = {}
        if "default_leader" in body.model_fields_set:
            extra["leader"] = {**leader_ep, "kind": "headless"} if leader_ep else None
        if "team_root" in body.model_fields_set:
            try:
                extra["team_root"] = commander_settings.clean_team_root(body.team_root)
            except ValueError as exc:
                raise teams.TeamError(str(exc)) from exc
        return commander_settings.set_team_defaults(roles=roles, **extra)

    result = _team_call(work)
    audit.record(db, "commander.settings", user=user, resource_type="commander", resource_id="team-defaults", request=request)
    return result


@router.post("/teams/auto", status_code=201)
def create_auto_team(
    body: _AutoTeam, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """目標からチームを作る。チームを作ってチームリーダーを走らせる（結果は一覧の router_run で見る）。"""
    from app.commander import team_leader

    team = _team_call(team_leader.create_and_start, body.goal, body.name, body.workdir)
    _team_audit(db, user, request, "auto", team["id"], name=team["name"], goal_length=len(body.goal))
    return team


@router.post("/teams/{team_id}/leader/run")
def run_team_leader(
    team_id: str, body: _LeaderRun, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """既存のチームへチームリーダーで計画を足す。"""
    from app.commander import team_leader

    team = _team_call(team_leader.start, team_id, body.goal)
    _team_audit(db, user, request, "leader.run", team_id, goal_length=len(body.goal))
    return team


@router.post("/teams/{team_id}/leader/stop")
def stop_team_leader(
    team_id: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """チームリーダー（と走っている仕事）を止める。"""
    from app.commander import team_leader

    from app.commander import team_runner

    stopped = _team_call(team_leader.stop, team_id)
    team_runner.stop_team(team_id)
    _team_audit(db, user, request, "leader.stop", team_id, running=stopped)
    return {"stopped": stopped}


@router.post("/teams/{team_id}/leader/resume")
def resume_team_leader(
    team_id: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """止まったチームリーダーを、今の状態から再開する。"""
    from app.commander import team_leader

    team = _team_call(team_leader.resume, team_id)
    _team_audit(db, user, request, "leader.resume", team_id)
    return team


@router.post("/teams/{team_id}/leader/land")
def land_team(
    team_id: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """「取り込む」: チームのブランチを元のブランチへ（元から git の作業場所だけ）。"""
    from app.commander import team_leader

    team = _team_call(team_leader.land, team_id)
    _team_audit(db, user, request, "leader.land", team_id)
    return team


@router.post("/teams/{team_id}/questions/{question_id}/answer")
def answer_team_question(
    team_id: str, question_id: str, body: _Answer, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """チームリーダーの質問に答える。"""
    from app.commander import leader_mcp

    if not re.fullmatch(r"q\d{1,4}", question_id):
        raise HTTPException(status_code=400, detail="質問の id が正しくありません")
    try:
        leader_mcp._read(team_id)  # noqa: SLF001
        leader_mcp.answer_question(team_id, question_id, body.answer)
    except leader_mcp.ToolError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    _team_audit(db, user, request, "answer", team_id, question=question_id)
    return {"ok": True}


@router.put("/teams/{team_id}/roles")
def set_team_roles(
    team_id: str, body: _TeamRoles, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    team = _team_call(teams.set_roles, team_id, body.roles)
    _team_audit(db, user, request, "roles", team_id, roles=[r["name"] for r in team["roles"]])
    return team


@router.put("/teams/{team_id}/leader")
def set_team_leader(
    team_id: str, body: _TeamLeader, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """チームリーダーを任命する（null なら既定のチームリーダーへ戻す）。"""
    team = _team_call(teams.set_leader, team_id, body.leader)
    _team_audit(db, user, request, "leader", team_id, leader=team.get("leader"))
    return team


@router.post("/teams/{team_id}/tasks/{task_id}/handoff")
def choose_team_handoff(
    team_id: str, task_id: int, body: _HandoffChoice, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """引き継ぎ先が無くて止まったタスクへ、選んだ担い手で引き継いで再開する。"""
    from app.commander import team_roles

    task = _team_call(team_roles.choose_handoff, team_id, task_id, body.endpoint, body.add_to_secondary, user.username)
    _team_audit(db, user, request, "task.handoff", team_id, task=task_id, add_to_secondary=body.add_to_secondary)
    return task


@router.put("/teams/{team_id}/notify")
def set_team_notify(
    team_id: str, body: _TeamNotify, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    team = _team_call(teams.set_notify, team_id, body.notify)
    _team_audit(db, user, request, "notify", team_id, notify=body.notify)
    return team


@router.delete("/teams/{team_id}")
def delete_team(
    team_id: str, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    _team_call(teams.delete_team, team_id)
    _team_audit(db, user, request, "delete", team_id)
    return {"ok": True}


@router.put("/teams/{team_id}/members")
def set_team_members(
    team_id: str, body: _TeamMembers, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    team = _team_call(teams.set_members, team_id, body.members)
    _team_audit(db, user, request, "members", team_id, count=len(team["members"]))
    return team


@router.post("/teams/{team_id}/tasks", status_code=201)
def create_team_task(
    team_id: str, body: _TaskBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    task = _team_call(teams.create_task, team_id, body.model_dump(exclude_none=True), user.username)
    _team_audit(db, user, request, "task.create", team_id, task=task["id"])
    return task


@router.put("/teams/{team_id}/tasks/{task_id}")
def update_team_task(
    team_id: str, task_id: int, body: _TaskBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    task = _team_call(teams.update_task, team_id, task_id, body.model_dump(exclude_none=True), user.username)
    _team_audit(db, user, request, "task.update", team_id, task=task_id, status=task["status"])
    return task


@router.delete("/teams/{team_id}/tasks/{task_id}")
def delete_team_task(
    team_id: str, task_id: int, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    _team_call(teams.delete_task, team_id, task_id)
    _team_audit(db, user, request, "task.delete", team_id, task=task_id)
    return {"ok": True}


@router.post("/teams/{team_id}/tasks/{task_id}/dispatch")
def dispatch_team_task(
    team_id: str, task_id: int, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """担当のコンソールへタスクの文面を送る（終了・作業中なら 409）。"""
    task = _team_call(teams.dispatch_task, team_id, task_id, user.username)
    _team_audit(db, user, request, "task.dispatch", team_id, task=task_id, assignee=task["assignee"])
    return task


@router.post("/teams/{team_id}/tasks/{task_id}/run")
def run_team_task(
    team_id: str, task_id: int, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_permission("terminal.use")),
):
    """担当（ヘッドレス）でタスクを実行する。担当が画面のコンソールなら送り込みと同じ。"""
    task = _team_call(teams.dispatch_task, team_id, task_id, user.username)
    _team_audit(db, user, request, "task.run", team_id, task=task_id, assignee=task["assignee"])
    return task


@router.post("/teams/{team_id}/tasks/{task_id}/stop")
def stop_team_task(
    team_id: str, task_id: int, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_permission("terminal.use")),
):
    """走っているヘッドレスの実行を止める。"""
    from app.commander import team_runner

    if not team_runner.stop_run(team_id, task_id):
        raise HTTPException(status_code=409, detail="実行中ではありません")
    _team_audit(db, user, request, "task.stop", team_id, task=task_id)
    return {"ok": True}


@router.post("/teams/{team_id}/tasks/{task_id}/collect")
def collect_team_task(
    team_id: str, task_id: int, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    """担当の画面から「完了報告:」の 4 行を読んで報告にする（無ければ 404）。"""
    task = _team_call(teams.collect_report, team_id, task_id, user.username)
    _team_audit(db, user, request, "task.collect", team_id, task=task_id)
    return task


@router.put("/teams/{team_id}/tasks/{task_id}/report")
def put_team_task_report(
    team_id: str, task_id: int, body: _ReportBody, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    task = _team_call(teams.put_report, team_id, task_id, body.model_dump(), user.username)
    _team_audit(db, user, request, "task.report", team_id, task=task_id)
    return task
