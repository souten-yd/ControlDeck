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


def test_app_icon_upload_sanitizes_and_does_not_leak_path(admin_client):
    created = admin_client.post(
        "/api/v1/apps",
        json={"name": "Icon App", "application_type": "url_shortcut", "url": "https://example.com"},
        headers=CSRF_HEADERS,
    ).json()
    app_id = created["id"]
    malicious = b'''<svg xmlns="http://www.w3.org/2000/svg" onclick="alert(1)">
      <script>alert(1)</script><rect width="10" height="10" fill="blue"/>
      <image href="https://evil.example/a.png"/>
    </svg>'''
    try:
        r = admin_client.post(
            f"/api/v1/apps/{app_id}/icon",
            files={"file": ("icon.svg", malicious, "image/svg+xml")},
            headers=CSRF_HEADERS,
        )
        assert r.status_code == 200, r.text
        icon_url = r.json()["icon_path"]
        assert icon_url.startswith(f"/api/v1/apps/{app_id}/icon?")
        assert "/home/" not in icon_url

        r = admin_client.get(icon_url)
        assert r.status_code == 200
        assert b"script" not in r.content.lower()
        assert b"onclick" not in r.content.lower()
        assert b"evil.example" not in r.content

        r = admin_client.post(
            f"/api/v1/apps/{app_id}/icon",
            files={"file": ("fake.png", b"not png", "image/png")},
            headers=CSRF_HEADERS,
        )
        assert r.status_code == 422

        assert admin_client.delete(f"/api/v1/apps/{app_id}/icon", headers=CSRF_HEADERS).status_code == 204
        assert admin_client.get(f"/api/v1/apps/{app_id}/icon").status_code == 404
    finally:
        admin_client.delete(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS)
