from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, Request

from app.addons import registry, tokens


@dataclass(frozen=True)
class RuntimePrincipal:
    addon_id: str
    subject: str
    expires_at: int
    granted_capabilities: frozenset[str]
    active: bool


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail="Add-on service tokenが必要です",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value or " " in value:
        raise HTTPException(
            status_code=401,
            detail="Authorization Bearer tokenが不正です",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return value


def authorize_runtime(
    request: Request,
    *,
    authorization: str | None,
    header_addon_id: str | None,
    capability: str | None = None,
    allow_inactive: bool = False,
) -> RuntimePrincipal:
    path_addon_id = request.path_params.get("addon_id")
    if not header_addon_id:
        raise HTTPException(status_code=400, detail="X-Control-Deck-Addon-IDが必要です")
    if path_addon_id is not None and path_addon_id != header_addon_id:
        raise HTTPException(status_code=403, detail="Add-on ID scopeが一致しません")

    token = _bearer_token(authorization)
    try:
        payload = tokens.verify(token, addon_id=header_addon_id, kind="service")
    except tokens.AddonTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail="Add-on service tokenが無効です",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=401, detail="Add-on service token subjectが不正です")
    try:
        current = registry.status(header_addon_id)
    except registry.AddonRegistryError as exc:
        raise HTTPException(status_code=401, detail="Add-onが登録されていません") from exc
    active = bool(current["enabled"] and current["state"] != "disable_pending")
    if not active and not allow_inactive:
        raise HTTPException(status_code=409, detail="Add-onは有効ではありません")

    granted = frozenset(current["granted_capabilities"])
    if capability is not None and capability not in granted:
        raise HTTPException(status_code=403, detail=f"Host capability {capability} がgrantされていません")
    return RuntimePrincipal(
        addon_id=header_addon_id,
        subject=subject,
        expires_at=payload["exp"],
        granted_capabilities=granted,
        active=active,
    )


def require_runtime_capability(capability: str | None = None, *, allow_inactive: bool = False) -> Callable:
    def dependency(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        addon_id_header: Annotated[str | None, Header(alias="X-Control-Deck-Addon-ID")] = None,
    ) -> RuntimePrincipal:
        return authorize_runtime(
            request,
            authorization=authorization,
            header_addon_id=addon_id_header,
            capability=capability,
            allow_inactive=allow_inactive,
        )

    return dependency
