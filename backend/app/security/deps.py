"""FastAPI 依存性: 認証・権限。REST と WebSocket の両方で使用する。"""
from __future__ import annotations

import json

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security.sessions import SESSION_COOKIE, resolve_session


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE, "")
    resolved = resolve_session(db, token)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="認証が必要です")
    _, user = resolved
    return user


def user_permissions(user: User) -> set[str]:
    try:
        return set(json.loads(user.role.permissions_json))
    except Exception:
        return set()


def require_permission(permission: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if permission not in user_permissions(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"権限がありません: {permission}",
            )
        return user

    return checker


def require_permissions(*permissions: str):
    """指定した全権限を要求する。複合操作の片側だけを許可しない。"""
    required = tuple(dict.fromkeys(permissions))

    def checker(user: User = Depends(get_current_user)) -> User:
        missing = [permission for permission in required if permission not in user_permissions(user)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"権限がありません: {', '.join(missing)}",
            )
        return user

    return checker


async def authenticate_websocket(
    websocket: WebSocket, db: Session, permission: str
) -> User | None:
    """WebSocket の認証 + Origin + 権限確認。失敗時は close して None を返す。"""
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    # 同一オリジンのみ許可（Origin が付かない非ブラウザクライアントは Cookie 必須のため許容）
    if origin:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        if parsed.netloc != host:
            await websocket.close(code=4403)
            return None
    token = websocket.cookies.get(SESSION_COOKIE, "")
    resolved = resolve_session(db, token)
    if resolved is None:
        await websocket.close(code=4401)
        return None
    _, user = resolved
    if permission not in user_permissions(user):
        await websocket.close(code=4403)
        return None
    return user
