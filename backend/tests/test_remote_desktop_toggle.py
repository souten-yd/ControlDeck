"""リモートデスクトップの有効・無効の切り替え（設定、ControlDeck 側の強制、特権補助の enable/disable）を守る。

目的:
- 設定 remote_desktop.enabled は settings.manage だけが変えられ、監査に残り、既定は有効（今までどおり）。
- オフのとき、ControlDeck のリモートの API と WebSocket は断られ、/api/v1/meta の enabled_features から
  "remote-desktop" が消える（ナビ・ホームのアイコン・/remote の画面はこれで出し分ける）。オンで戻る。
- オフ/オンで xrdp 本体と sesman に stop+disable / enable+start を補助経由で送る。補助や一覧が古いときは
  設定は保存し、「補助の更新が要ります」を結果に返す（失敗を隠さない）。
- 補助（helper）の system-service が enable/disable を受け付け、一覧に無い id・不正な action を断る。
  subprocess はモックで、本物の xrdp は止めない。

根拠: 利用者の依頼（2026-11-21）「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。オフになったら
通信・サーバーの起動・ホームのアイコンも消す」。特権の操作に関わる（L2）ので、強制の場所ごとに固定する。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.applications import system_services
from app.remote_desktop import toggle
from tests.conftest import CSRF_HEADERS

URL = "/api/v1/settings/remote-desktop"


def _catalog(tmp_path, actions=("start", "stop", "enable", "disable")):
    path = tmp_path / "system-services.json"
    path.write_text(json.dumps({"version": 1, "services": {
        "remote-desktop": {"label": "xrdp", "unit": "xrdp.service", "actions": list(actions)},
        "remote-desktop-session": {"label": "sesman", "unit": "xrdp-sesman.service", "actions": list(actions)},
    }}), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture()
def calls(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(system_services, "CATALOG_PATH", _catalog(tmp_path))
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(system_services, "control", lambda sid, action: (recorded.append((sid, action)) or (True, "")))
    yield recorded
    toggle._write({})  # 他のテストへ無効を持ち越さない


def test_default_is_enabled_and_meta_lists_it(admin_client):
    toggle._write({})
    assert admin_client.get(URL).json()["enabled"] is True
    assert "remote-desktop" in admin_client.get("/api/v1/meta").json()["enabled_features"]


def test_disable_stops_server_blocks_api_and_hides_feature_then_enable_restores(admin_client, calls):
    r = admin_client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["apply"]["state"] == "ok"
    assert calls == [("remote-desktop", "stop"), ("remote-desktop", "disable"),
                     ("remote-desktop-session", "stop"), ("remote-desktop-session", "disable")]
    assert "remote-desktop" not in admin_client.get("/api/v1/meta").json()["enabled_features"]
    for path in ("/status", "/connections"):
        blocked = admin_client.get(f"/api/v1/remote{path}")
        assert blocked.status_code == 403 and "無効" in blocked.json()["detail"]
    assert admin_client.post("/api/v1/remote/connections", headers=CSRF_HEADERS, json={
        "name": "x", "protocol": "rdp", "host": "h"}).status_code == 403
    with pytest.raises(Exception):
        with admin_client.websocket_connect("/api/v1/remote/connections/1/tunnel"):
            pass

    calls.clear()
    r = admin_client.put(URL, json={"enabled": True}, headers=CSRF_HEADERS)
    assert r.json()["enabled"] is True
    assert calls == [("remote-desktop-session", "enable"), ("remote-desktop-session", "start"),
                     ("remote-desktop", "enable"), ("remote-desktop", "start")]
    assert "remote-desktop" in admin_client.get("/api/v1/meta").json()["enabled_features"]
    assert admin_client.get("/api/v1/remote/connections").status_code == 200


def test_old_helper_or_catalog_saves_setting_and_reports_update_needed(admin_client, tmp_path, monkeypatch):
    # 一覧に remote-desktop が無い（今の本番: 空）
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"version": 1, "services": {}}), encoding="utf-8")
    empty.chmod(0o600)
    monkeypatch.setattr(system_services, "CATALOG_PATH", empty)
    try:
        r = admin_client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False  # 設定は保存される
        assert body["apply"]["state"] == "needs_helper_update"
        assert "sudo ./deck.sh service" in body["apply"]["message"]
        assert admin_client.get(URL).json()["apply"]["state"] == "needs_helper_update"
        assert admin_client.get("/api/v1/remote/connections").status_code == 403  # 強制は補助に依らない

        # 一覧は新しいが補助が古く enable を知らない場合
        monkeypatch.setattr(system_services, "CATALOG_PATH", _catalog(tmp_path))
        monkeypatch.setattr(system_services, "control", lambda *_a: (False, "不正なsystem service操作です"))
        assert admin_client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS).json()["apply"]["state"] == "needs_helper_update"

        # 補助の本物の失敗は隠さず error
        monkeypatch.setattr(system_services, "control", lambda *_a: (False, "systemctl stopが失敗しました（終了コード 5）"))
        apply = admin_client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS).json()["apply"]
        assert apply["state"] == "error" and "終了コード 5" in apply["message"]
    finally:
        toggle._write({})


def test_permission_and_audit(admin_client, calls, client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    assert admin_client.put(URL, json={"enabled": "x"}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(URL, json={"enabled": False, "extra": 1}, headers=CSRF_HEADERS).status_code == 422
    admin_client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS)
    admin_client.put(URL, json={"enabled": True}, headers=CSRF_HEADERS)
    db = SessionLocal()
    try:
        actions = [row.action for row in db.execute(select(AuditLog).where(AuditLog.resource_id == "remote_desktop")).scalars()]
    finally:
        db.close()
    assert "remote_desktop.disable" in actions and "remote_desktop.enable" in actions
    client.cookies.clear()
    assert client.get(URL).status_code == 401
    assert client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS).status_code in (401, 403)


def test_builtin_catalog_defines_both_xrdp_units():
    rendered = system_services.render_config_catalog()["services"]
    assert rendered["remote-desktop"]["unit"] == "xrdp.service"
    assert rendered["remote-desktop-session"]["unit"] == "xrdp-sesman.service"
    for item in ("remote-desktop", "remote-desktop-session"):
        assert {"start", "stop", "enable", "disable"} <= set(rendered[item]["actions"])


def _helper(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "helper" / "control-deck-hw-helper.py"
    spec = importlib.util.spec_from_file_location("control_deck_helper_toggle", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixed", encoding="utf-8")
    systemctl.chmod(0o700)
    monkeypatch.setattr(helper, "CATALOG_PATH", _catalog(tmp_path))
    monkeypatch.setattr(helper, "SYSTEMCTL", systemctl)
    real = helper.os.fstat
    monkeypatch.setattr(helper.os, "fstat", lambda fd: SimpleNamespace(
        st_mode=real(fd).st_mode, st_uid=0, st_size=real(fd).st_size))
    argvs: list[list[str]] = []
    monkeypatch.setattr(helper.subprocess, "run", lambda argv, **_k: (
        argvs.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr="")))
    return helper, systemctl, argvs


def test_helper_accepts_enable_disable_and_rejects_unknown(tmp_path, monkeypatch):
    helper, systemctl, argvs = _helper(tmp_path, monkeypatch)
    helper.control_system_service("disable", "remote-desktop")
    helper.control_system_service("enable", "remote-desktop-session")
    assert argvs == [
        [str(systemctl), "--no-ask-password", "disable", "--", "xrdp.service"],
        [str(systemctl), "--no-ask-password", "enable", "--", "xrdp-sesman.service"],
    ]
    for action, service_id in (("mask", "remote-desktop"), ("enable", "ssh"), ("enable", "../x"), ("restart", "remote-desktop")):
        with pytest.raises(SystemExit):
            helper.control_system_service(action, service_id)
    assert len(argvs) == 2  # 断ったものは systemctl まで行かない


def test_isolated_instance_never_touches_the_machine_service(admin_client, monkeypatch):
    # E2E の検証用インスタンスが、将来 allowlist が入った本番の xrdp を止めないための保険。
    monkeypatch.setattr(toggle, "isolated", lambda: True)
    monkeypatch.setattr(system_services, "control", lambda *_a: (_ for _ in ()).throw(AssertionError("xrdp touched")))
    try:
        r = admin_client.put(URL, json={"enabled": False}, headers=CSRF_HEADERS)
        assert r.status_code == 200 and r.json()["apply"]["state"] == "skipped"
        assert admin_client.get("/api/v1/remote/connections").status_code == 403
    finally:
        toggle._write({})
