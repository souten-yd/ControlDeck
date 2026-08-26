from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addons import health, proxy, registry, tokens
from app.audit import service as audit
from app.config import get_config
from app.database import SessionLocal
from app.models import User
from app.security.deps import user_permissions
from app.security.rate_limit import api_rate_limiter


router = APIRouter(prefix="/{addon_id}/devices", tags=["addon-runtime-device"])
DeviceRelayAuth = Annotated[
    RuntimePrincipal, Depends(require_runtime_capability("devices.relay"))
]
PAIRING_TTL_SECONDS = 5 * 60
# Device credentials follow the same maximum TTL policy as other Add-on
# credentials. A successful reconnect rotates the credential, but device kind
# does not receive a special long-lived exception.
DEVICE_TOKEN_TTL_SECONDS = tokens.MAX_TOKEN_TTL_SECONDS
POLICY_RECHECK_SECONDS = 60.0
MAX_PENDING_PAIRINGS = 128
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LOCK = threading.RLock()


class DevicePairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relay_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9._-]{0,127}$"
    )
    device_label: str | None = Field(default=None, max_length=80)


@dataclass(frozen=True)
class PendingPairing:
    addon_id: str
    relay_id: str
    actor_user_id: int
    device_id: str
    device_label: str | None
    code_hash: str
    expires_at: int


_pairings: dict[str, PendingPairing] = {}


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("ascii", errors="strict")).hexdigest()


def _cleanup_pairings(now: int) -> None:
    for key, pairing in list(_pairings.items()):
        if pairing.expires_at <= now:
            _pairings.pop(key, None)


def _new_pairing(
    *,
    addon_id: str,
    relay_id: str,
    actor_user_id: int,
    device_label: str | None,
) -> tuple[PendingPairing, str]:
    now = int(time.time())
    with _LOCK:
        _cleanup_pairings(now)
        if len(_pairings) >= MAX_PENDING_PAIRINGS:
            raise HTTPException(status_code=429, detail="device pairing queue is full")
        for _ in range(8):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            digest = _code_hash(code)
            if digest not in _pairings:
                pairing = PendingPairing(
                    addon_id=addon_id,
                    relay_id=relay_id,
                    actor_user_id=actor_user_id,
                    device_id=uuid.uuid4().hex,
                    device_label=device_label,
                    code_hash=digest,
                    expires_at=now + PAIRING_TTL_SECONDS,
                )
                _pairings[digest] = pairing
                return pairing, code
    raise HTTPException(status_code=503, detail="could not allocate a pairing code")


def _consume_pairing(code: str, *, addon_id: str, relay_id: str) -> PendingPairing:
    if len(code) != 8 or any(
        character not in _CODE_ALPHABET for character in code
    ):
        raise ValueError("invalid pairing code")
    now = int(time.time())
    digest = _code_hash(code)
    with _LOCK:
        _cleanup_pairings(now)
        pairing = _pairings.get(digest)
        if pairing is None or pairing.expires_at <= now:
            raise ValueError("pairing code expired or was already used")
        if pairing.addon_id != addon_id or pairing.relay_id != relay_id:
            # Do not consume a valid code presented to the wrong relay. This
            # prevents a scope-confusion attempt from becoming a one-shot DoS.
            raise ValueError("pairing code scope does not match this relay")
        _pairings.pop(digest, None)
        return pairing


def _user_permissions(actor_user_id: int) -> set[str]:
    db = SessionLocal()
    try:
        user = db.get(User, actor_user_id)
        if user is None or not bool(user.is_active):
            raise PermissionError("paired user is unavailable")
        return user_permissions(user)
    finally:
        db.close()


def _audit_device(
    action: str,
    *,
    actor_user_id: int,
    addon_id: str,
    relay_id: str,
    result: str = "success",
    request: Request | WebSocket | None = None,
    metadata: dict | None = None,
) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, actor_user_id)
        audit.record(
            db,
            action,
            user=user,
            username=f"user:{actor_user_id}" if user is None else "",
            resource_type="addon_device",
            resource_id=f"{addon_id}:{relay_id}"[:64],
            result=result,
            request=request,
            metadata={
                "addon_id": addon_id,
                "relay_id": relay_id,
                **(metadata or {}),
            },
        )
    finally:
        db.close()


def _resolve_relay(
    addon_id: str, relay_id: str, permissions: set[str]
) -> tuple[dict, str]:
    try:
        current = registry.status(addon_id)
    except registry.AddonRegistryError as exc:
        raise PermissionError("add-on is not registered") from exc
    if not current.get("enabled") or current.get("state") == "disable_pending":
        raise PermissionError("add-on is not enabled")
    if "devices.relay" not in set(current.get("granted_capabilities") or []):
        raise PermissionError("devices.relay is not granted")
    effective = registry.effective_for_permissions(permissions)
    relays = (effective.get("contributions") or {}).get("device_relays") or []
    relay = next(
        (
            item
            for item in relays
            if item.get("addon_id") == addon_id and item.get("id") == relay_id
        ),
        None,
    )
    if relay is None:
        raise PermissionError("device relay is not available to this user")
    endpoint = relay.get("endpoint")
    protocol = relay.get("protocol")
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise PermissionError("device relay endpoint is invalid")
    if not isinstance(protocol, str) or not protocol:
        raise PermissionError("device relay protocol is invalid")
    base_url = health.approved_health_url(
        current["runtime"]["base_url"], "/"
    ).rstrip("/")
    return relay, base_url


def _device_subject(relay_id: str, device_id: str) -> str:
    return f"device:{relay_id}:{device_id}"


def _parse_device_subject(subject: object, relay_id: str) -> str:
    if not isinstance(subject, str):
        raise ValueError("device token subject is invalid")
    prefix = f"device:{relay_id}:"
    if not subject.startswith(prefix):
        raise ValueError("device token relay scope does not match")
    device_id = subject.removeprefix(prefix)
    if len(device_id) != 32 or any(
        character not in "0123456789abcdef" for character in device_id
    ):
        raise ValueError("device token device id is invalid")
    return device_id


def _bearer(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not token
        or " " in token
    ):
        raise ValueError("invalid device authorization")
    return token


def _authorize_device(
    websocket: WebSocket, addon_id: str, relay_id: str
) -> tuple[int, str, bool]:
    code = websocket.headers.get("x-control-deck-pairing-code")
    authorization = _bearer(websocket.headers.get("authorization"))
    if bool(code) == bool(authorization):
        raise ValueError("supply exactly one device credential")
    if code:
        pairing = _consume_pairing(code, addon_id=addon_id, relay_id=relay_id)
        return pairing.actor_user_id, pairing.device_id, True
    assert authorization is not None
    payload = tokens.verify(
        authorization,
        addon_id=addon_id,
        kind="device",
        max_ttl_seconds=DEVICE_TOKEN_TTL_SECONDS,
    )
    actor_user_id = payload.get("actor_user_id")
    if (
        not isinstance(actor_user_id, int)
        or isinstance(actor_user_id, bool)
        or actor_user_id <= 0
    ):
        raise ValueError("device token actor is invalid")
    return actor_user_id, _parse_device_subject(payload.get("sub"), relay_id), False


def _fresh_device_token(
    addon_id: str, relay_id: str, device_id: str, actor_user_id: int
) -> tuple[str, int]:
    now = int(time.time())
    token = tokens.issue(
        addon_id,
        subject=_device_subject(relay_id, device_id),
        kind="device",
        actor_user_id=actor_user_id,
        ttl_seconds=DEVICE_TOKEN_TTL_SECONDS,
        now=now,
    )
    return token, now + DEVICE_TOKEN_TTL_SECONDS


@router.post("/pairings", status_code=201)
def create_pairing(
    body: DevicePairingRequest,
    principal: DeviceRelayAuth,
    request: Request,
):
    if principal.actor_user_id is None:
        raise HTTPException(
            status_code=403,
            detail="device pairing must originate from an authenticated user session",
        )
    try:
        permissions = _user_permissions(principal.actor_user_id)
        relay, _base_url = _resolve_relay(
            principal.addon_id, body.relay_id, permissions
        )
    except PermissionError as exc:
        _audit_device(
            "addon.device.pairing.create",
            actor_user_id=principal.actor_user_id,
            addon_id=principal.addon_id,
            relay_id=body.relay_id,
            result="failure",
            request=request,
            metadata={"reason": str(exc)[:120]},
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    pairing, code = _new_pairing(
        addon_id=principal.addon_id,
        relay_id=body.relay_id,
        actor_user_id=principal.actor_user_id,
        device_label=body.device_label,
    )
    _audit_device(
        "addon.device.pairing.create",
        actor_user_id=principal.actor_user_id,
        addon_id=principal.addon_id,
        relay_id=body.relay_id,
        request=request,
        metadata={
            "device_id": pairing.device_id,
            "device_label": body.device_label,
            "expires_at": pairing.expires_at,
        },
    )
    return {
        "pairing_code": code,
        "expires_at": pairing.expires_at,
        "relay_id": body.relay_id,
        "protocol": relay["protocol"],
        "websocket_path": (
            f"/api/v1/addon-runtime/{principal.addon_id}"
            f"/devices/relay/{body.relay_id}"
        ),
    }


@router.websocket("/relay/{relay_id}")
async def device_relay(websocket: WebSocket, addon_id: str, relay_id: str):
    peer = websocket.client.host if websocket.client else "unknown"
    allowed, _retry_after = api_rate_limiter.check(
        "websocket",
        peer,
        get_config().security.websocket_rate_limit_per_minute,
    )
    if not allowed:
        await websocket.close(code=4429, reason="rate limited")
        return
    try:
        actor_user_id, device_id, newly_paired = _authorize_device(
            websocket, addon_id, relay_id
        )
        permissions = _user_permissions(actor_user_id)
        relay, base_url = _resolve_relay(addon_id, relay_id, permissions)
    except (ValueError, tokens.AddonTokenError, PermissionError):
        await websocket.close(code=4403)
        return

    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    upstream_url = urlunsplit(
        (scheme, parsed.netloc, relay["endpoint"], "", "")
    )
    try:
        async with proxy._connect_websocket(
            upstream_url,
            proxy._service_headers(addon_id, actor_user_id),
            [],
        ) as upstream:
            await websocket.accept()
            refreshed, expires_at = _fresh_device_token(
                addon_id, relay_id, device_id, actor_user_id
            )
            await websocket.send_json(
                {
                    "type": "control-deck.device.session",
                    "protocol_version": "1",
                    "relay_protocol": relay["protocol"],
                    "device_id": device_id,
                    "device_token": refreshed,
                    "expires_at": expires_at,
                    "newly_paired": newly_paired,
                }
            )
            _audit_device(
                "addon.device.session.connect",
                actor_user_id=actor_user_id,
                addon_id=addon_id,
                relay_id=relay_id,
                request=websocket,
                metadata={
                    "device_id": device_id,
                    "newly_paired": newly_paired,
                },
            )

            async def device_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_device() -> None:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            async def authorization_watch() -> None:
                # The active socket is already authenticated. Recheck infrequently
                # so an explicit user/add-on revoke eventually terminates it. The
                # credential TTL governs future reconnect authorization rather than
                # forcing a healthy local WebSocket to disconnect mid-session.
                while True:
                    await asyncio.sleep(POLICY_RECHECK_SECONDS)
                    try:
                        current_permissions = _user_permissions(actor_user_id)
                        _resolve_relay(addon_id, relay_id, current_permissions)
                    except PermissionError:
                        await websocket.close(
                            code=4403, reason="device authorization revoked"
                        )
                        return

            first = asyncio.create_task(device_to_upstream())
            second = asyncio.create_task(upstream_to_device())
            policy = asyncio.create_task(authorization_watch())
            done, pending = await asyncio.wait(
                (first, second, policy), return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
            registry.record_activity(
                addon_id,
                "addon-device.websocket",
                "success",
                {"relay_id": relay_id},
            )
    except (OSError, TimeoutError, websockets.WebSocketException):
        registry.record_activity(
            addon_id,
            "addon-device.websocket",
            "upstream_error",
            {"relay_id": relay_id},
        )
        try:
            await websocket.close(
                code=4502, reason="add-on device relay unavailable"
            )
        except RuntimeError:
            pass
    except (WebSocketDisconnect, RuntimeError):
        registry.record_activity(
            addon_id,
            "addon-device.websocket",
            "closed",
            {"relay_id": relay_id},
        )
    finally:
        try:
            _audit_device(
                "addon.device.session.disconnect",
                actor_user_id=actor_user_id,
                addon_id=addon_id,
                relay_id=relay_id,
                request=websocket,
                metadata={"device_id": device_id},
            )
        except UnboundLocalError:
            pass
