"""アプリ内Webビュー（/appview reverse proxy）のテスト。"""

from tests.conftest import CSRF_HEADERS


def test_appview_proxy_unknown_app_returns_404(admin_client):
    r = admin_client.get("/appview/99999/")
    assert r.status_code == 404


def test_appview_referer_fallback_redirects_absolute_paths(admin_client):
    r = admin_client.get(
        "/static/css/app.css",
        headers={"referer": "https://example.ts.net/appview/5/"},
        follow_redirects=False,
    )
    assert r.status_code == 307
    assert r.headers["location"] == "/appview/5/static/css/app.css"


def test_appview_referer_fallback_ignores_api_and_assets(admin_client):
    r = admin_client.get(
        "/api/v1/health",
        headers={"referer": "https://example.ts.net/appview/5/"},
        follow_redirects=False,
    )
    assert r.status_code == 200  # Control Deck 自身のAPIはリダイレクトしない


def test_updating_an_app_with_a_health_check_does_not_500(admin_client, tmp_path):
    """model_dump は入れ子のモデルも dict にする。set_health_check は
    HealthCheckConfig を受ける契約なので、dump 側から渡すと必ず落ちる。
    実際に「PC起動時に自動起動」を入れて更新すると 500 になっていた。"""
    script = tmp_path / "app.sh"
    script.write_text("#!/bin/sh\nsleep 1\n", encoding="utf-8")
    created = admin_client.post("/api/v1/apps", headers=CSRF_HEADERS, json={
        "name": "health-check-update",
        "application_type": "shell_script",
        "script_path": str(script),
        "health_check": {"type": "none"},
    })
    assert created.status_code in (200, 201), created.text
    app_id = created.json()["id"]

    # 画面はフォーム全体を送るので、auto_start だけ変えても health_check が同送される
    updated = admin_client.patch(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS, json={
        "auto_start": True,
        "health_check": {"type": "none"},
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["auto_start"] is True
    assert updated.json()["health_check"]["type"] == "none"


def test_updating_an_app_without_a_health_check_leaves_it_alone(admin_client, tmp_path):
    script = tmp_path / "keep.sh"
    script.write_text("#!/bin/sh\nsleep 1\n", encoding="utf-8")
    created = admin_client.post("/api/v1/apps", headers=CSRF_HEADERS, json={
        "name": "health-check-keep",
        "application_type": "shell_script",
        "script_path": str(script),
        "health_check": {"type": "none"},
    })
    app_id = created.json()["id"]

    updated = admin_client.patch(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS, json={"description": "touched"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["health_check"]["type"] == "none"
