"""AI Commander の API。権限は Terminal と同じ（terminal.use）。

- GET    /commander/clis                    CLI の一覧（導入済みか・アカウントを分けられるか）
- GET    /commander/accounts/{cli}          アカウントの一覧（ログイン済みの印つき）
- POST   /commander/accounts/{cli}          アカウントの作成
- DELETE /commander/accounts/{cli}/{name}    アカウントの削除（ログインの情報も消える）
- GET    /commander/consoles                コンソールの一覧
- POST   /commander/consoles                コンソールを個数ぶん開く
- DELETE /commander/consoles/{session_id}   コンソールを閉じる

アカウントの作成・削除とコンソールの作成・削除は監査ログへ残す
（アカウントのフォルダにはログインの情報が入るため）。
endpoint は def で threadpool へ任せる（tmux の subprocess で event loop を止めない）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.commander import accounts, consoles
from app.database import get_db
from app.models import User
from app.schemas.commander import AccountCreate, ConsoleCreate
from app.security.deps import require_permission
from app.terminals.router import streams

router = APIRouter(prefix="/commander", tags=["commander"])


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


@router.get("/consoles")
def list_consoles(user: User = Depends(require_permission("terminal.use"))):
    del user
    return {"consoles": consoles.list_consoles()}


@router.post("/consoles", status_code=201)
def create_consoles(
    body: ConsoleCreate,
    request: Request,
    user: User = Depends(require_permission("terminal.use")),
    db: Session = Depends(get_db),
):
    cli = _cli_or_400(body.cli)
    if not accounts.is_installed(cli):
        raise HTTPException(status_code=400, detail=f"{cli.name} は導入されていません")
    if body.account != accounts.DEFAULT_ACCOUNT and not cli.env_targets:
        raise HTTPException(status_code=400, detail=f"{cli.name} は既定のアカウントだけ使えます")
    try:
        result = consoles.create_consoles(
            cli=cli, account=body.account, cwd=body.cwd,
            count=body.count, label=body.label, created_by=user.username,
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
            "cwd": body.cwd,
            "label": body.label,
        },
    )
    return result


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
