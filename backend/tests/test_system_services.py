from __future__ import annotations

import json
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.applications import system_services
from tests.conftest import CSRF_HEADERS


def _catalog(tmp_path):
    path = tmp_path / "system-services.json"
    path.write_text(json.dumps({
        "version": 1,
        "services": {
            "remote-desktop": {
                "label": "Remote Desktop",
                "unit": "xrdp.service",
                "actions": ["start", "stop", "restart"],
            },
        },
    }), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_installed_catalog_rejects_symlink_and_invalid_definition(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(system_services, "CATALOG_PATH", catalog)
    installed = system_services.installed_catalog()
    assert installed["remote-desktop"].unit == "xrdp.service"
    assert system_services.catalog_for_api() == [{
        "id": "remote-desktop", "label": "Remote Desktop", "unit": "xrdp.service",
        "actions": ["start", "stop", "restart"],
    }]

    link = tmp_path / "catalog-link.json"
    link.symlink_to(catalog)
    monkeypatch.setattr(system_services, "CATALOG_PATH", link)
    assert system_services.catalog_for_api() == []

    catalog.write_text(json.dumps({
        "version": 1,
        "services": {"bad": {"label": "Bad", "unit": "bad.service", "actions": ["start", "kill"]}},
    }), encoding="utf-8")
    monkeypatch.setattr(system_services, "CATALOG_PATH", catalog)
    assert system_services.catalog_for_api() == []


def test_system_service_api_uses_catalog_id_and_helper_boundary(admin_client, tmp_path, monkeypatch):
    from app.applications import systemd as sd

    monkeypatch.setattr(system_services, "CATALOG_PATH", _catalog(tmp_path))
    monkeypatch.setattr(sd, "query_status", lambda unit, *, user_scope=True: {
        "status": "STOPPED", "pid": None, "uptime_seconds": None, "started_at": None,
        "restart_count": 0, "enabled": True, "unit": unit, "user_scope": user_scope,
    })
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(system_services, "control", lambda service_id, action: (calls.append((service_id, action)) or (True, "")))

    catalog = admin_client.get("/api/v1/apps/system-services")
    assert catalog.status_code == 200
    assert catalog.json()[0]["id"] == "remote-desktop"

    rejected = admin_client.post("/api/v1/apps", json={
        "name": "Rejected root unit", "application_type": "systemd_service",
        "systemd_scope": "system", "system_service_id": "remote-desktop",
        "systemd_unit_name": "ssh.service",
    }, headers=CSRF_HEADERS)
    assert rejected.status_code == 422

    created = admin_client.post("/api/v1/apps", json={
        "name": "Allowed root unit", "application_type": "systemd_service",
        "systemd_scope": "system", "system_service_id": "remote-desktop",
    }, headers=CSRF_HEADERS)
    assert created.status_code == 201, created.text
    body = created.json()
    app_id = body["id"]
    assert body["systemd_scope"] == "system"
    assert body["system_service_id"] == "remote-desktop"
    assert body["systemd_unit_name"] == "xrdp.service"
    assert body["systemd_actions"] == ["start", "stop", "restart"]
    assert body["auto_start"] is True

    started = admin_client.post(f"/api/v1/apps/{app_id}/start", headers=CSRF_HEADERS)
    assert started.status_code == 200, started.text
    assert calls == [("remote-desktop", "start")]
    assert admin_client.post(f"/api/v1/apps/{app_id}/kill", headers=CSRF_HEADERS).status_code == 409

    changed = admin_client.patch(f"/api/v1/apps/{app_id}", json={
        "systemd_scope": "user", "system_service_id": None, "systemd_unit_name": "example.service",
    }, headers=CSRF_HEADERS)
    assert changed.status_code == 200, changed.text
    assert changed.json()["systemd_scope"] == "user"
    assert changed.json()["systemd_unit_name"] == "example.service"
    assert admin_client.delete(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS).status_code == 200


def test_system_service_disallowed_action_is_rejected_before_helper(admin_client, tmp_path, monkeypatch):
    from app.applications import systemd as sd

    catalog = _catalog(tmp_path)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["services"]["remote-desktop"]["actions"] = ["restart"]
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(system_services, "CATALOG_PATH", catalog)
    monkeypatch.setattr(sd, "query_status", lambda _unit, *, user_scope=True: {
        "status": "STOPPED", "pid": None, "restart_count": 0, "enabled": False,
    })
    monkeypatch.setattr(system_services, "control", lambda *_args: (_ for _ in ()).throw(AssertionError("helper called")))
    created = admin_client.post("/api/v1/apps", json={
        "name": "Restart only root unit", "application_type": "systemd_service",
        "systemd_scope": "system", "system_service_id": "remote-desktop",
    }, headers=CSRF_HEADERS)
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    assert admin_client.post(f"/api/v1/apps/{app_id}/start", headers=CSRF_HEADERS).status_code == 409
    assert admin_client.delete(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS).status_code == 200


def test_privileged_helper_executes_only_fixed_systemctl_and_catalog_unit(tmp_path, monkeypatch):
    helper_path = Path(__file__).resolve().parents[2] / "helper" / "control-deck-hw-helper.py"
    spec = importlib.util.spec_from_file_location("control_deck_privileged_helper", helper_path)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    catalog = _catalog(tmp_path)
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixed", encoding="utf-8")
    systemctl.chmod(0o700)
    monkeypatch.setattr(helper, "CATALOG_PATH", catalog)
    monkeypatch.setattr(helper, "SYSTEMCTL", systemctl)
    real_fstat = helper.os.fstat

    def root_owned_fstat(descriptor):
        info = real_fstat(descriptor)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_size=info.st_size)

    monkeypatch.setattr(helper.os, "fstat", root_owned_fstat)
    calls: list[list[str]] = []
    monkeypatch.setattr(helper.subprocess, "run", lambda argv, **_kwargs: (
        calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    ))
    helper.control_system_service("restart", "remote-desktop")
    assert calls == [[str(systemctl), "--no-ask-password", "restart", "--", "xrdp.service"]]

    with pytest.raises(SystemExit):
        helper.control_system_service("kill", "remote-desktop")
    assert len(calls) == 1
