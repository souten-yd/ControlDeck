"""アプリ内Webビュー（/appview reverse proxy）のテスト。"""


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
