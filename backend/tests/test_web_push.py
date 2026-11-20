"""Web Push（端末への通知）。

目的:
- 暗号化（RFC 8291 aes128gcm）が規格の例と 1 バイト違わず一致し、受け手の鍵で復号できる。
- VAPID（RFC 8292）の JWT が公開鍵で検証でき、aud は push service の origin、sub は https。
- 端末が渡す endpoint は既知の push service だけ受ける（内側の URL へ POST させない）。
- 404・410 の端末は消す。届いたら最後に届いた時刻を残す。endpoint は一覧に出さない。
- Project Run が終わったら起動した人へ送る。取り消しでは送らない。同じ終わりは 1 回だけ。
- 通知チャンネル push は、監視を見られる人（system.view）の端末へ送る。
- VAPID の JWT は 1 時間以上使い回す（Apple の文書の求め）。
根拠: 2026-11-21 利用者の依頼（通知の片方向を塞ぐ）。本番は通知先が 0 件で、自動更新にも気づけなかった。
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.alerts import webpush
from app.database import SessionLocal
from app.models import PushSubscription, User
from tests.conftest import CSRF_HEADERS

FCM = "https://fcm.googleapis.com/fcm/send/abc123"


def _receiver():
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return key, webpush.b64url(public), webpush.b64url(auth)


def _decrypt(body: bytes, key: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """受け手（ブラウザ）の側の復号。RFC 8291 の手順をそのまま書く。"""
    import hmac as _hmac
    from hashlib import sha256

    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert rs == 4096
    ua_public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    h = lambda k, d: _hmac.new(k, d, sha256).digest()  # noqa: E731
    ikm = h(h(webpush.b64url_decode(auth), shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = h(salt, ikm)
    cek = h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = h(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert record.endswith(b"\x02")
    return record[:-1]


def test_encryption_matches_rfc8291_example():
    sender = ec.derive_private_key(
        int.from_bytes(webpush.b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg", sender_key=sender, salt=webpush.b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert webpush.b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A"
        "_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_encryption_round_trips_with_a_fresh_receiver():
    key, p256dh, auth = _receiver()
    message = json.dumps({"title": "日本語", "body": "x" * 500}).encode()
    assert _decrypt(webpush.encrypt(message, p256dh, auth), key, auth) == message


def test_vapid_token_verifies_with_the_public_key():
    key = ec.generate_private_key(ec.SECP256R1())
    header = webpush.vapid_authorization("https://web.push.apple.com/QAbc", "https://deck.example.ts.net", now=1000, key=key)
    token = header.split("t=", 1)[1].split(",", 1)[0]
    k = header.split("k=", 1)[1]
    head, claims, signature = token.split(".")
    raw = webpush.b64url_decode(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64url_decode(k))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(webpush.b64url_decode(head)) == {"typ": "JWT", "alg": "ES256"}
    body = json.loads(webpush.b64url_decode(claims))
    assert body == {"aud": "https://web.push.apple.com", "exp": 1000 + webpush.TTL_SECONDS, "sub": "https://deck.example.ts.net"}
    assert body["exp"] - 1000 <= 24 * 3600   # RFC 8292: 24 時間以内


def test_vapid_token_is_reused_for_an_hour_or_more():
    """Apple は JWT を 1 時間に 1 回より頻繁に作り直さないよう求める。期限の 1 時間前までは使い回す。"""
    key = ec.generate_private_key(ec.SECP256R1())
    first = webpush.vapid_authorization("https://web.push.apple.com/a", "https://deck.example", now=10_000, key=key)
    assert webpush.vapid_authorization("https://web.push.apple.com/b", "https://deck.example", now=10_000 + 3600, key=key) == first
    other = webpush.vapid_authorization("https://fcm.googleapis.com/x", "https://deck.example", now=10_000 + 3600, key=key)
    assert other != first                                  # push service（aud）ごとに別
    near_expiry = 10_000 + webpush.TTL_SECONDS - webpush.JWT_REUSE_MARGIN_SECONDS
    assert webpush.vapid_authorization("https://web.push.apple.com/a", "https://deck.example", now=near_expiry, key=key) != first


def test_only_known_push_services_are_accepted():
    for good in (FCM, "https://web.push.apple.com/QOk", "https://updates.push.services.mozilla.com/wpush/v2/x"):
        assert webpush.allowed_endpoint(good), good
    for bad in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://evil.example/x",
                "https://fcm.googleapis.com.evil.example/x", "https://user:pw@fcm.googleapis.com/x",
                "https://fcm.googleapis.com:8443/x", "https://169.254.169.254/latest"):
        assert not webpush.allowed_endpoint(bad), bad


def _clear_subscriptions():
    with SessionLocal() as db:
        db.query(PushSubscription).delete()
        db.commit()


def test_subscription_api_rejects_internal_endpoints_and_hides_secrets(admin_client):
    _clear_subscriptions()
    _, p256dh, auth = _receiver()
    keys = {"p256dh": p256dh, "auth": auth}
    bad = admin_client.post("/api/v1/push/subscriptions", json={"endpoint": "https://127.0.0.1:8765/api/v1/x", "keys": keys},
                            headers=CSRF_HEADERS)
    assert bad.status_code == 422
    assert admin_client.get("/api/v1/push/public-key").json()["public_key"]
    created = admin_client.post("/api/v1/push/subscriptions", json={"endpoint": FCM, "keys": keys, "label": "Android・Chrome"},
                                headers={**CSRF_HEADERS, "origin": "https://deck.example.ts.net"})
    assert created.status_code == 201, created.text
    again = admin_client.post("/api/v1/push/subscriptions", json={"endpoint": FCM, "keys": keys, "label": "Android・Chrome"},
                              headers=CSRF_HEADERS)
    assert again.json()["id"] == created.json()["id"]          # 同じ端末は 1 行
    listed = admin_client.get("/api/v1/push/subscriptions").text
    assert "fcm.googleapis.com" not in listed and p256dh not in listed and "Android" in listed
    with SessionLocal() as db:
        row = db.query(PushSubscription).one()
        assert FCM not in row.secret_encrypted and row.subject.startswith("https://")
    removed = admin_client.request("DELETE", "/api/v1/push/subscriptions", json={"endpoint": FCM}, headers=CSRF_HEADERS)
    assert removed.json() == {"removed": True}
    _clear_subscriptions()


def test_send_removes_gone_devices_and_records_success(admin_client):
    _clear_subscriptions()
    receivers = {}
    for name in ("alive", "gone"):
        key, p256dh, auth = _receiver()
        receivers[f"{FCM}-{name}"] = (key, auth)
        admin_client.post("/api/v1/push/subscriptions", json={"endpoint": f"{FCM}-{name}", "keys": {"p256dh": p256dh, "auth": auth}},
                          headers=CSRF_HEADERS)
    delivered: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-encoding"] == "aes128gcm"
        assert request.headers["authorization"].startswith("vapid t=")
        assert int(request.headers["ttl"]) > 0
        if str(request.url).endswith("gone"):
            return httpx.Response(410)
        key, auth = receivers[str(request.url)]
        delivered.append(json.loads(_decrypt(request.content, key, auth)))
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = webpush.send_to(title="実行が終わりました", body="exit 0", url="/project-lab?run=3", client=client)
    assert result == {"sent": 1, "removed": 1, "failed": 0}
    assert delivered == [{"title": "実行が終わりました", "body": "exit 0", "url": "/project-lab?run=3"}]
    with SessionLocal() as db:
        rows = db.query(PushSubscription).all()
        assert len(rows) == 1 and rows[0].last_success_at is not None
    _clear_subscriptions()


def test_send_refuses_offsite_urls_in_payload():
    captured: list[dict] = []
    original = webpush.send_one
    try:
        webpush.send_one = lambda target, payload, client: captured.append(payload) or 201  # type: ignore[assignment]
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").one()
            _, p256dh, auth = _receiver()
            from app.security.crypto import encrypt_text
            db.add(PushSubscription(user_id=user.id, endpoint_digest="x" * 64, subject="https://x",
                                    secret_encrypted=encrypt_text(json.dumps({"endpoint": FCM, "p256dh": p256dh, "auth": auth}))))
            db.commit()
        webpush.send_to(title="t", body="b", url="//evil.example/")
        assert captured[0]["url"] == "/"
    finally:
        webpush.send_one = original  # type: ignore[assignment]
        _clear_subscriptions()


def test_finished_run_notifies_its_starter_once_and_not_on_cancel(monkeypatch):
    from app.models import ProjectRun
    from app.project_lab import runs

    sent: list[dict] = []
    monkeypatch.setattr(webpush, "send_in_background", lambda **kwargs: sent.append(kwargs))
    states = iter([{"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "Result": "exit-code",
                    "ExecMainStatus": "2"}])
    monkeypatch.setattr(runs, "_show", lambda unit: next(states))
    monkeypatch.setattr(runs, "_finalize_artifacts", lambda db, row: None)
    with SessionLocal() as db:
        starter = db.query(User).filter(User.username == "admin").one().id
        row = ProjectRun(project_id="my app", project_name="My App", profile_id="test", profile_type="test",
                         status="RUNNING", unit_name="unit-x", created_by=starter)
        db.add(row)
        db.commit()
        runs.refresh_run(db, row)
        assert row.status == "FAILED"
        runs._announce_finished(row)                       # 画面と見張りが同時に見つけても 1 回
        canceled = ProjectRun(project_id="my app", project_name="My App", profile_id="test", profile_type="test",
                              status="CANCELED", unit_name="unit-y", created_by=starter)
        db.add(canceled)
        db.commit()
        runs._announce_finished(canceled)
        row.status = canceled.status = "SUCCEEDED"
        db.commit()
    assert len(sent) == 1
    assert sent[0]["user_ids"] == [starter]
    assert sent[0]["url"] == f"/project-lab?project=my%20app&run={row.id}"
    assert "失敗" in sent[0]["title"] and "終了コード 2" in sent[0]["body"]


def test_push_channel_sends_to_monitoring_viewers(monkeypatch):
    from app.alerts.notify import send_notification

    calls: list[dict] = []
    monkeypatch.setattr(webpush, "send_to", lambda **kwargs: calls.append(kwargs) or {"sent": 1, "removed": 0, "failed": 0})
    assert asyncio.run(send_notification("push", "", "CPU が高い", "95%")) is True
    assert calls[0]["permission"] == "system.view" and calls[0]["title"] == "CPU が高い"


def test_push_channel_needs_no_url(admin_client):
    created = admin_client.post("/api/v1/alert-channels", json={"name": "端末", "channel_type": "push"}, headers=CSRF_HEADERS)
    assert created.status_code == 201, created.text
    assert created.json()["url_preview"] == "通知を受け取る端末すべて"
    admin_client.delete(f"/api/v1/alert-channels/{created.json()['id']}", headers=CSRF_HEADERS)
