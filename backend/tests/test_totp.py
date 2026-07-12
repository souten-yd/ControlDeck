import pyotp

from tests.conftest import CSRF_HEADERS


def _fresh_admin_client(client):
    """admin でログイン済みのクライアントを返す。"""
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200
    return client


def test_totp_full_lifecycle(client):
    c = _fresh_admin_client(client)

    # setup
    r = c.post("/api/v1/auth/totp/setup", headers=CSRF_HEADERS)
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert r.json()["qr_data_uri"].startswith("data:image/svg+xml;base64,")

    # 間違ったコードは拒否
    assert c.post("/api/v1/auth/totp/verify", json={"code": "000000"}, headers=CSRF_HEADERS).status_code == 400

    # 正しいコードで有効化 → リカバリーコード取得
    code = pyotp.TOTP(secret).now()
    r = c.post("/api/v1/auth/totp/verify", json={"code": code}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    recovery = r.json()["recovery_codes"]
    assert len(recovery) == 10

    # me に反映
    me = c.get("/api/v1/auth/me").json()
    assert me["totp_enabled"] is True
    assert me["recovery_codes_remaining"] == 10

    # ログアウト → 2FA なしログインは 401 (two_factor_required)
    c.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}, headers=CSRF_HEADERS)
    assert r.status_code == 401
    assert r.json()["detail"] == "two_factor_required"

    # TOTP コード付きでログイン成功
    r = c.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123", "totp_code": pyotp.TOTP(secret).now()},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200

    # リカバリーコードでもログインできる（使い捨て）
    c.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    rc = recovery[0]
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123", "totp_code": rc}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert c.get("/api/v1/auth/me").json()["recovery_codes_remaining"] == 9
    # 同じリカバリーコードは 2 度使えない
    c.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123", "totp_code": rc}, headers=CSRF_HEADERS)
    assert r.status_code == 401

    # 無効化（TOTP コードで確認）
    c.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123", "totp_code": pyotp.TOTP(secret).now()}, headers=CSRF_HEADERS)
    r = c.post("/api/v1/auth/totp/disable", json={"code": pyotp.TOTP(secret).now()}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert c.get("/api/v1/auth/me").json()["totp_enabled"] is False

    # 以降は通常ログイン
    c.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    assert c.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}, headers=CSRF_HEADERS).status_code == 200


def test_totp_secret_is_encrypted(client):
    from app.auth import totp
    from app.database import SessionLocal
    from app.models import User
    from sqlalchemy import select

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
        secret = totp.generate_secret()
        totp.store_secret(user, secret)
        # 保存値は平文ではない
        assert secret not in (user.totp_secret_encrypted or "")
        # 復号すると一致
        assert totp.get_secret(user) == secret
    finally:
        db.close()
