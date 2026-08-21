from __future__ import annotations

from copy import deepcopy

import pytest
from starlette.requests import Request

from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def runtime_addon(admin_client, monkeypatch, tmp_path):
    from app.addons import health, registry, tokens
    from app.addons.schema import AddonHealthReport

    data = tmp_path / "runtime-auth-data"
    monkeypatch.setattr(registry, "data_dir", lambda: data)
    monkeypatch.setattr(tokens, "data_dir", lambda: data)
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    monkeypatch.setattr(health, "recheck", healthy)
    manifest = deepcopy(addon_manifest())
    assert admin_client.post("/api/v1/addons", json=manifest, headers=CSRF_HEADERS).status_code == 201
    assert admin_client.post(
        "/api/v1/addons/fake-addon/enable",
        json={"granted_capabilities": ["jobs.write", "resources.acquire"]},
        headers=CSRF_HEADERS,
    ).status_code == 200
    return admin_client, registry, tokens


def _headers(token: str, addon_id: str = "fake-addon") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Control-Deck-Addon-ID": addon_id,
    }


def _request(addon_id: str = "fake-addon") -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/addon-runtime/{addon_id}/resources/requests",
        "headers": [],
        "path_params": {"addon_id": addon_id},
    })


def test_introspection_accepts_scoped_service_token_without_session_or_csrf(runtime_addon):
    client, _registry, tokens = runtime_addon
    client.cookies.clear()
    token = tokens.issue("fake-addon", subject="job:12345", kind="service")
    response = client.post("/api/v1/addon-runtime/token/introspect", headers=_headers(token))
    assert response.status_code == 200
    assert response.json() == {
        "active": True,
        "addon_id": "fake-addon",
        "subject": "job:12345",
        "expires_at": tokens.verify(token, addon_id="fake-addon", kind="service")["exp"],
        "granted_capabilities": ["jobs.write", "resources.acquire"],
    }


@pytest.mark.parametrize("case", ["tampered", "wrong_audience", "wrong_kind"])
def test_introspection_returns_inactive_without_leaking_failure(case, runtime_addon):
    client, _registry, tokens = runtime_addon
    if case == "tampered":
        token = tokens.issue("fake-addon", subject="7", kind="service") + "x"
    elif case == "wrong_audience":
        token = tokens.issue("other-addon", subject="7", kind="service")
    else:
        token = tokens.issue("fake-addon", subject="7", kind="bridge")
    response = client.post("/api/v1/addon-runtime/token/introspect", headers=_headers(token))
    assert response.status_code == 200
    assert response.json() == {"active": False}


def test_introspection_fails_closed_when_addon_disabled_or_disable_pending(runtime_addon):
    client, registry, tokens = runtime_addon
    token = tokens.issue("fake-addon", subject="7", kind="service")
    registry.begin_disable("fake-addon")
    assert client.post("/api/v1/addon-runtime/token/introspect", headers=_headers(token)).json() == {"active": False}
    registry.complete_disable("fake-addon")
    assert client.post("/api/v1/addon-runtime/token/introspect", headers=_headers(token)).json() == {"active": False}


def test_runtime_dependency_binds_path_header_and_granted_capability(runtime_addon):
    _client, _registry, tokens = runtime_addon
    from app.addon_runtime.auth import authorize_runtime
    from fastapi import HTTPException

    token = tokens.issue("fake-addon", subject="job:123", kind="service")
    principal = authorize_runtime(
        _request(), authorization=f"Bearer {token}", header_addon_id="fake-addon", capability="resources.acquire",
    )
    assert principal.subject == "job:123"
    with pytest.raises(HTTPException) as mismatch:
        authorize_runtime(
            _request(), authorization=f"Bearer {token}", header_addon_id="other-addon", capability="resources.acquire",
        )
    assert mismatch.value.status_code == 403
    with pytest.raises(HTTPException) as missing_grant:
        authorize_runtime(
            _request(), authorization=f"Bearer {token}", header_addon_id="fake-addon", capability="files.read",
        )
    assert missing_grant.value.status_code == 403
