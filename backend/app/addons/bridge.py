from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.addons import registry, tokens
from app.addons.contract import BRIDGE_SCHEMA_VERSION
from app.auth.policy import totp_required_for
from app.models import User
from sqlalchemy.orm import Session
from app.security.deps import user_permissions
from app.security.permissions import ALL_PERMISSIONS
from app.security.rate_limit import SlidingWindowRateLimiter

BRIDGE_REQUEST_LIMIT = 16 * 1024
BRIDGE_CALLS_PER_MINUTE = 120
_limiter = SlidingWindowRateLimiter(max_keys=10_000)

BridgeMethod = Literal[
    "host.context.get",
    "host.theme.get",
    "host.route.open",
    "host.route.sync",
    "host.title.set",
    "host.file.pick",
    "host.file.export",
    "host.project.pick",
    "host.job.open",
    "host.job.subscribe",
    "host.notification.show",
    "host.permission.has",
    "host.busy.set",
]

METHOD_CAPABILITY: dict[str, str | None] = {
    "host.context.get": "context.read",
    "host.theme.get": "theme.read",
    "host.route.open": "route.open",
    "host.route.sync": "route.open",
    "host.title.set": "route.open",
    "host.file.pick": "files.pick",
    "host.file.export": "files.export",
    "host.project.pick": "projects.pick",
    "host.job.open": "jobs.read",
    "host.job.subscribe": "jobs.read",
    "host.notification.show": "notifications.show",
    "host.permission.has": None,
    "host.busy.set": None,
}

METHOD_PERMISSION: dict[str, str | None] = {
    "host.file.pick": "files.view",
    "host.file.export": "files.edit",
    "host.project.pick": "project_lab.view",
    "host.job.open": "workflows.run",
    "host.job.subscribe": "workflows.run",
}


class BridgeHandshake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_version: Literal["1.0"]
    view_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")


class BridgeCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_version: Literal["1.0"]
    session_nonce: str = Field(min_length=32, max_length=2048)
    view_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{0,127}$")
    method: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict, max_length=32)


class BridgeAccessError(ValueError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _view(addon_id: str, view_id: str, user: User) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        current = registry.status(addon_id)
    except registry.AddonRegistryError as exc:
        raise BridgeAccessError(404, "addon_not_found", "拡張機能が登録されていません") from exc
    if not current["enabled"]:
        raise BridgeAccessError(409, "addon_disabled", "拡張機能は無効です")
    effective = registry.effective_for_permissions(user_permissions(user))
    contribution = next((
        item for item in effective.get("contributions", {}).get("embedded_views", [])
        if item["addon_id"] == addon_id and item["id"] == view_id
    ), None)
    if contribution is None:
        raise BridgeAccessError(409, "view_unavailable", "埋め込み画面を現在利用できません")
    return current, contribution


def handshake(addon_id: str, value: BridgeHandshake, user: User) -> dict[str, Any]:
    current, _contribution = _view(addon_id, value.view_id, user)
    grants = set(current["granted_capabilities"])
    permissions = user_permissions(user)
    allowed = [
        method for method, capability in METHOD_CAPABILITY.items()
        if (capability is None or capability in grants)
        and (METHOD_PERMISSION.get(method) is None or METHOD_PERMISSION[method] in permissions)
    ]
    nonce = tokens.issue(
        addon_id,
        subject=f"{user.id}:{value.view_id}",
        kind="bridge",
    )
    return {
        "addon_id": addon_id,
        "view_id": value.view_id,
        "bridge_version": BRIDGE_SCHEMA_VERSION,
        "session_nonce": nonce,
        "expires_in": tokens.TOKEN_TTL_SECONDS,
        "allowed_methods": allowed,
    }


def _require_string(params: dict[str, Any], name: str, *, maximum: int, allow_empty: bool = False) -> str:
    value = params.get(name)
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        raise BridgeAccessError(422, "invalid_params", f"{name}が不正です")
    return value


def _only(params: dict[str, Any], names: set[str]) -> None:
    if not set(params).issubset(names):
        raise BridgeAccessError(422, "invalid_params", "未対応のparameterがあります")


def _relative_path(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or "\\" in value or parsed.scheme or parsed.netloc or parsed.fragment:
        raise BridgeAccessError(422, "invalid_params", f"{name}は同一host内pathにしてください")
    return value


def validate_params(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method not in METHOD_CAPABILITY:
        raise BridgeAccessError(422, "method_not_supported", "未対応のBridge methodです")
    if method in {"host.context.get", "host.theme.get"}:
        _only(params, set())
    elif method == "host.route.open":
        _only(params, {"route", "replace"})
        _relative_path(_require_string(params, "route", maximum=512), "route")
        if "replace" in params and not isinstance(params["replace"], bool):
            raise BridgeAccessError(422, "invalid_params", "replaceはbooleanにしてください")
    elif method == "host.route.sync":
        _only(params, {"path", "replace"})
        _relative_path(_require_string(params, "path", maximum=512, allow_empty=True), "path")
        if "replace" in params and not isinstance(params["replace"], bool):
            raise BridgeAccessError(422, "invalid_params", "replaceはbooleanにしてください")
    elif method == "host.title.set":
        _only(params, {"title"})
        _require_string(params, "title", maximum=80)
    elif method == "host.file.pick":
        _only(params, {"mode", "title"})
        if params.get("mode", "file") not in {"file", "dir"}:
            raise BridgeAccessError(422, "invalid_params", "modeはfileまたはdirにしてください")
        if "title" in params:
            _require_string(params, "title", maximum=80)
    elif method == "host.file.export":
        _only(params, {"suggested_name", "mime_type"})
        name = _require_string(params, "suggested_name", maximum=128)
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise BridgeAccessError(422, "invalid_params", "suggested_nameはfile名だけにしてください")
        if "mime_type" in params:
            _require_string(params, "mime_type", maximum=128)
    elif method == "host.project.pick":
        _only(params, {"title"})
        if "title" in params:
            _require_string(params, "title", maximum=80)
    elif method in {"host.job.open", "host.job.subscribe"}:
        _only(params, {"job_id"})
        _require_string(params, "job_id", maximum=128)
    elif method == "host.notification.show":
        _only(params, {"title", "message", "level", "dedupe_key"})
        _require_string(params, "title", maximum=80)
        _require_string(params, "message", maximum=300)
        if params.get("level", "info") not in {"info", "success", "error"}:
            raise BridgeAccessError(422, "invalid_params", "levelが不正です")
        if "dedupe_key" in params:
            _require_string(params, "dedupe_key", maximum=128)
    elif method == "host.permission.has":
        _only(params, {"permission"})
        if _require_string(params, "permission", maximum=64) not in ALL_PERMISSIONS:
            raise BridgeAccessError(422, "invalid_params", "未知のpermissionです")
    elif method == "host.busy.set":
        _only(params, {"busy"})
        if not isinstance(params.get("busy"), bool):
            raise BridgeAccessError(422, "invalid_params", "busyはbooleanにしてください")
    return params


def authorize(addon_id: str, value: BridgeCall, user: User) -> dict[str, Any]:
    if len(json.dumps(value.model_dump(mode="json"), ensure_ascii=False).encode()) > BRIDGE_REQUEST_LIMIT:
        raise BridgeAccessError(413, "request_too_large", "Bridge requestは16KiB以下にしてください")
    current, _contribution = _view(addon_id, value.view_id, user)
    try:
        tokens.verify(
            value.session_nonce,
            addon_id=addon_id,
            kind="bridge",
            subject=f"{user.id}:{value.view_id}",
        )
    except tokens.AddonTokenError as exc:
        raise BridgeAccessError(403, "invalid_session", "Bridge sessionが無効です") from exc
    rate_key = hashlib.sha256(value.session_nonce.encode()).hexdigest()
    allowed, retry_after = _limiter.check("bridge", rate_key, BRIDGE_CALLS_PER_MINUTE)
    if not allowed:
        raise BridgeAccessError(429, "rate_limited", f"Bridge呼び出しが多すぎます。{retry_after}秒後に再試行してください")
    validate_params(value.method, value.params)
    capability = METHOD_CAPABILITY[value.method]
    if capability is not None and capability not in set(current["granted_capabilities"]):
        raise BridgeAccessError(403, "capability_not_granted", f"{capability}は許可されていません")
    required_permission = METHOD_PERMISSION.get(value.method)
    if required_permission is not None and required_permission not in user_permissions(user):
        raise BridgeAccessError(403, "permission_denied", f"{required_permission}権限がありません")
    result: dict[str, Any] = {"ok": True, "method": value.method}
    if value.method == "host.permission.has":
        result["has_permission"] = value.params["permission"] in user_permissions(user)
    return result


def authenticate_websocket_session(addon_id: str, token: str, db: Session) -> tuple[User, set[str]]:
    try:
        payload = tokens.verify(token, addon_id=addon_id, kind="bridge")
        subject = payload.get("sub", "")
        raw_user_id, view_id = subject.split(":", 1)
        user_id = int(raw_user_id)
    except (tokens.AddonTokenError, ValueError, TypeError) as exc:
        raise BridgeAccessError(403, "invalid_session", "Bridge sessionが無効です") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active or totp_required_for(user) and not user.totp_enabled:
        raise BridgeAccessError(403, "invalid_session", "Bridge sessionが無効です")
    _view(addon_id, view_id, user)
    return user, user_permissions(user)


def reset_for_tests() -> None:
    _limiter.clear()
