from tests.conftest import CSRF_HEADERS


def test_url_shortcut_create(admin_client):
    r = admin_client.post(
        "/api/v1/apps",
        json={"name": "Example", "application_type": "url_shortcut", "url": "https://example.com"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["application_type"] == "url_shortcut"
    assert body["url"] == "https://example.com"
    assert body["runtime"]["status"] == "URL"
    assert body["systemd_unit_name"] == ""
    admin_client.delete(f"/api/v1/apps/{body['id']}", headers=CSRF_HEADERS)


def test_url_shortcut_rejects_bad_url(admin_client):
    for bad in ["ftp://x", "javascript:alert(1)", "notaurl", ""]:
        r = admin_client.post(
            "/api/v1/apps",
            json={"name": "bad", "application_type": "url_shortcut", "url": bad},
            headers=CSRF_HEADERS,
        )
        assert r.status_code == 422, bad
