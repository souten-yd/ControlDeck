"""AI Commander の API。権限は Terminal と同じ（terminal.use）。

- GET    /commander/clis                    CLI の一覧（導入済みか・アカウントを分けられるか）
- GET    /commander/accounts/{cli}          アカウントの一覧（ログイン済みの印つき）
- POST   /commander/accounts/{cli}          アカウントの作成
- DELETE /commander/accounts/{cli}/{name}    アカウントの削除（ログインの情報も消える）
- PUT    /commander/accounts/{cli}/{name}/label  画面の名前（既定のアカウントにも付けられる）
- GET    /commander/consoles                コンソールの一覧
- POST   /commander/consoles                コンソールを個数ぶん開く
- DELETE /commander/consoles/{session_id}   コンソールを閉じる
- /commander/teams ...                      AI チーム（タスク板・送り込み。commander/teams.py）

アカウントの作成・削除とコンソールの作成・削除は監査ログへ残す
（アカウントのフォルダにはログインの情報が入るため）。
endpoint は def で threadpool へ任せる（tmux の subprocess で event loop を止めない）。
コンソールを開くだけは async で、OpenCode のときに LLM を裏で温めてから threadpool で開く。
"""
from __future__ import annotations

import asyncio
import logging

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.commander import accounts, consoles, teams, workdir
from app.database import get_db
from app.models import User
from app.schemas.commander import (
    AccountCreate, AccountLabel, CommanderSettingsBody, ConsoleAutoResume, ConsoleCreate, ResumeMessage,
    UsageStatusline, WorkdirPath,
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
    from app.commander import settings as commander_settings

    try:
        result = commander_settings.set_resume_message(body.resume_message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(db, "commander.settings", user=user, resource_type="commander", resource_id="settings",
                 request=request)
    return result


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


@router.delete("/consoles/{session_id}")
def delete_console(
    session_id: str,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    # Terminal のセッションを閉じるのと同じ（WS の接続も先に閉じる）。
    console = next(
        (s for s in consoles.list_consoles() if s.get("id") == session_id), None,
    )
    if console is None:
        raise HTTPException(status_code=404, detail="コンソールが見つかりません")
    streams.close_session(session_id)
    consoles.close_console(session_id)
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


class _TeamUpdate(BaseModel):
    """チームの名前・前提（brief）・送る前のリセット。送った欄だけ変える（検証は teams.py）。"""
    name: str | None = Field(default=None, max_length=200)
    brief: str | None = Field(default=None, max_length=20000)
    reset_before_dispatch: bool | None = None


class _TeamMembers(BaseModel):
    members: list[dict[str, Any]] = Field(max_length=50)


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
    team = _team_call(teams.create_team, body.name)
    _team_audit(db, user, request, "create", team["id"], name=team["name"])
    return team


@router.put("/teams/{team_id}")
def update_team(
    team_id: str, body: _TeamUpdate, request: Request,
    user: User = Depends(require_permission("terminal.use")), db: Session = Depends(get_db),
):
    team = _team_call(teams.update_team, team_id, body.model_dump())
    _team_audit(db, user, request, "update", team_id, name=team["name"], brief_length=len(team.get("brief") or ""),
                reset_before_dispatch=team.get("reset_before_dispatch", True))
    return team


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
