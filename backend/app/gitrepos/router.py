from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.gitrepos import service as gitsvc
from app.models import GitRepository, User
from app.security.deps import require_permission

router = APIRouter(prefix="/gitrepos", tags=["gitrepos"])


class RepoCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    name: str = Field(default="", max_length=64)


class SaveBody(BaseModel):
    message: str = Field(default="", max_length=200)


class RevertBody(BaseModel):
    sha: str = Field(min_length=7, max_length=40)


class DeleteBody(BaseModel):
    delete_files: bool = False


def _get(db: Session, repo_id: int) -> GitRepository:
    repo = db.get(GitRepository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="リポジトリが見つかりません")
    return repo


def _out(repo: GitRepository) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "url": repo.url,
        "path": repo.path,
        "created_at": repo.created_at,
        "status": gitsvc.status(repo.path),
    }


@router.get("/auth-status")
def auth_status(user: User = Depends(require_permission("apps.view"))):
    return gitsvc.gh_auth_status()


@router.post("/login-terminal", status_code=201)
def login_terminal(
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
):
    """gh auth login を実行するターミナルセッションを作成する（デバイスフロー）。"""
    from app.terminals.manager import manager

    try:
        session = manager.create_session(
            command="gh auth status || gh auth login --web --git-protocol https; gh auth setup-git 2>/dev/null; exec bash",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    audit.record(db, "gitrepo.login", user=user, resource_type="gitrepo", request=request)
    return session


@router.get("")
def list_repos(
    user: User = Depends(require_permission("apps.view")), db: Session = Depends(get_db)
):
    rows = db.execute(select(GitRepository).order_by(GitRepository.name)).scalars().all()
    return [_out(r) for r in rows]


@router.post("", status_code=201)
async def create_repo(
    body: RepoCreate,
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
):
    name = body.name.strip() or gitsvc.name_from_url(body.url)
    try:
        gitsvc.validate(body.url, name)
    except gitsvc.GitError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if db.execute(select(GitRepository).where(GitRepository.name == name)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"同名のリポジトリが登録済みです: {name}")
    try:
        dest = await asyncio.to_thread(gitsvc.clone, body.url, name)
    except gitsvc.GitError as e:
        raise HTTPException(status_code=422, detail=str(e))
    repo = GitRepository(name=name, url=body.url, path=str(dest))
    db.add(repo)
    db.commit()
    audit.record(db, "gitrepo.clone", user=user, resource_type="gitrepo", resource_id=str(repo.id), request=request, metadata={"url": body.url})
    return _out(repo)


@router.get("/{repo_id}/log")
def repo_log(
    repo_id: int,
    user: User = Depends(require_permission("apps.view")),
    db: Session = Depends(get_db),
):
    repo = _get(db, repo_id)
    try:
        return gitsvc.log(repo.path)
    except gitsvc.GitError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/{repo_id}/update")
async def update_repo(
    repo_id: int,
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
):
    repo = _get(db, repo_id)
    try:
        detail = await asyncio.to_thread(gitsvc.update, repo.path)
    except gitsvc.GitError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "gitrepo.update", user=user, resource_type="gitrepo", resource_id=str(repo_id), request=request)
    return {"detail": detail, **_out(repo)}


@router.post("/{repo_id}/save")
async def save_repo(
    repo_id: int,
    body: SaveBody,
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
):
    repo = _get(db, repo_id)
    from datetime import datetime

    message = body.message.strip() or f"保存 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    try:
        detail = await asyncio.to_thread(gitsvc.save, repo.path, message)
    except gitsvc.GitError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "gitrepo.save", user=user, resource_type="gitrepo", resource_id=str(repo_id), request=request)
    return {"detail": detail, **_out(repo)}


@router.post("/{repo_id}/revert")
async def revert_repo(
    repo_id: int,
    body: RevertBody,
    request: Request,
    user: User = Depends(require_permission("apps.edit")),
    db: Session = Depends(get_db),
):
    repo = _get(db, repo_id)
    try:
        detail = await asyncio.to_thread(gitsvc.revert, repo.path, body.sha)
    except gitsvc.GitError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "gitrepo.revert", user=user, resource_type="gitrepo", resource_id=str(repo_id), request=request, metadata={"sha": body.sha})
    return {"detail": detail, **_out(repo)}


@router.delete("/{repo_id}")
def delete_repo(
    repo_id: int,
    body: DeleteBody,
    request: Request,
    user: User = Depends(require_permission("apps.delete")),
    db: Session = Depends(get_db),
):
    repo = _get(db, repo_id)
    if body.delete_files:
        try:
            gitsvc.remove_files(repo.path)
        except gitsvc.GitError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except FileNotFoundError:
            pass
    name = repo.name
    db.delete(repo)
    db.commit()
    audit.record(db, "gitrepo.delete", user=user, resource_type="gitrepo", resource_id=str(repo_id), request=request, metadata={"name": name, "files": body.delete_files})
    return {"ok": True}
