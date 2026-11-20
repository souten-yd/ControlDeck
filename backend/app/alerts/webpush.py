"""Web Push（ホーム画面に置いた ControlDeck・PC のブラウザへの通知）。

2026-11-21 利用者の依頼。通知先（Discord・Slack・メール）は登録が無いと届かず、本番には 1 件も無かった。
ブラウザの Web Push なら、端末で「通知を受け取る」を押すだけで届く。

- 暗号化は RFC 8291（aes128gcm）、送り主の証明は RFC 8292（VAPID, ES256）。cryptography と httpx だけで
  組む（pywebpush は足さない。外からの取得が要らず、手順も 60 行ほど）。RFC 8291 の付録の例で確かめている。
- VAPID の鍵は 1 組。秘密鍵は他の秘密と同じ Fernet で暗号化して data_dir に置く。
- 送り先は既知の push service だけ（SSRF を防ぐ。endpoint は端末が渡す URL なので、そのままでは
  ControlDeck から内側の任意の URL へ POST させられる）。
- 404・410 は端末側で購読が消えた印（iOS は黙って失効させる）。その行を消す。
- iOS は 16.4 以降で、ホーム画面に追加した ControlDeck の中からだけ購読できる。通知への返信は
  できないので、通知 → タップ → その画面（会話・実行結果）を開く、までを 1 本の導線にする。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import data_dir

logger = logging.getLogger("control_deck.alerts.push")

RECORD_SIZE = 4096
MAX_PAYLOAD_BYTES = 3000
TTL_SECONDS = 12 * 3600
# 送り先として認める push service（後ろの一致）。主要ブラウザの全部。
PUSH_SERVICE_SUFFIXES = (
    ".googleapis.com",            # Chrome・Edge（Android 含む）: fcm.googleapis.com
    ".push.services.mozilla.com",  # Firefox
    ".push.apple.com",            # Safari（iOS・macOS）: web.push.apple.com
    ".notify.windows.com",        # 旧 Edge
)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https" and not parsed.username and not parsed.password
        and parsed.port in (None, 443) and len(endpoint) <= 2048
        and any(host.endswith(suffix) for suffix in PUSH_SERVICE_SUFFIXES)
    )


# ---- VAPID の鍵 ----
_key_lock = threading.Lock()


def _key_path():
    return data_dir() / "push" / "vapid.json"


def _private_key() -> ec.EllipticCurvePrivateKey:
    from app.security.crypto import decrypt_text, encrypt_text

    path = _key_path()
    with _key_lock:
        if path.exists():
            pem = decrypt_text(json.loads(path.read_text(encoding="utf-8"))["private_encrypted"])
            key = serialization.load_pem_private_key(pem.encode(), password=None)
            assert isinstance(key, ec.EllipticCurvePrivateKey)
            return key
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"private_encrypted": encrypt_text(pem), "public": b64url(_public_bytes(key))}, handle)
        os.replace(tmp, path)
        return key


def _public_bytes(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    public = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return public.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key() -> str:
    """ブラウザの pushManager.subscribe に渡す applicationServerKey（base64url）。"""
    return b64url(_public_bytes(_private_key()))


def vapid_authorization(endpoint: str, subject: str, *, now: float | None = None,
                        key: ec.EllipticCurvePrivateKey | None = None) -> str:
    """RFC 8292 の Authorization 頭（vapid t=<JWT>, k=<公開鍵>）。"""
    key = key or _private_key()
    parsed = urlparse(endpoint)
    claims = {"aud": f"{parsed.scheme}://{parsed.netloc}", "exp": int((now or time.time()) + TTL_SECONDS), "sub": subject}
    signing_input = (
        b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        + "." + b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    token = f"{signing_input}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={b64url(_public_bytes(key))}"


# ---- RFC 8291 の暗号化 ----
def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(plaintext: bytes, p256dh: str, auth: str, *,
            sender_key: ec.EllipticCurvePrivateKey | None = None, salt: bytes | None = None) -> bytes:
    """aes128gcm の本文（salt・rs・送り手の公開鍵・暗号文）。記録は 1 つだけ（4096 バイトに収める）。

    sender_key と salt は試験で RFC の例を再現するためだけに渡す。ふだんは毎回作り直す。"""
    ua_public = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    if len(ua_public) != 65 or ua_public[0] != 4 or len(auth_secret) != 16:
        raise ValueError("購読の鍵の形が正しくありません")
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = _public_bytes(sender_key)
    shared = sender_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    # HKDF（RFC 5869）。長さが 1 ブロック以内なので Expand は HMAC 1 回で足りる。
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(plaintext) + 1 + 16 > RECORD_SIZE:
        raise ValueError("通知の中身が大きすぎます")
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ciphertext


# ---- 送る ----
@dataclass
class Target:
    subscription_id: int
    endpoint: str
    p256dh: str
    auth: str
    subject: str


def send_one(target: Target, payload: dict, *, client: httpx.Client) -> int:
    """1 台へ送り、push service の status を返す（送れなかったら 0）。"""
    if not allowed_endpoint(target.endpoint):
        return 0
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > MAX_PAYLOAD_BYTES:
        payload = {**payload, "body": str(payload.get("body", ""))[:500]}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    try:
        response = client.post(target.endpoint, content=encrypt(body, target.p256dh, target.auth), headers={
            "Authorization": vapid_authorization(target.endpoint, target.subject),
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
            "TTL": str(TTL_SECONDS), "Urgency": "normal",
        })
        return response.status_code
    except (httpx.HTTPError, ValueError) as error:
        # endpoint は資格情報なので出さない
        logger.warning("Web Push を送れません (%s)", type(error).__name__)
        return 0


def _targets(db, user_ids: list[int] | None, permission: str | None) -> list[Target]:
    from app.models import PushSubscription, User
    from app.security.crypto import decrypt_text
    from app.security.deps import user_permissions

    query = db.query(PushSubscription).join(User, User.id == PushSubscription.user_id).filter(User.is_active.is_(True))
    if user_ids is not None:
        query = query.filter(PushSubscription.user_id.in_(user_ids))
    targets = []
    for row in query.all():
        if permission and permission not in user_permissions(db.get(User, row.user_id)):
            continue
        try:
            secret = json.loads(decrypt_text(row.secret_encrypted))
        except Exception:  # noqa: BLE001 - 壊れた行は飛ばす
            continue
        targets.append(Target(row.id, secret["endpoint"], secret["p256dh"], secret["auth"], row.subject))
    return targets


def send_to(*, title: str, body: str, url: str = "/", tag: str | None = None,
            user_ids: list[int] | None = None, permission: str | None = None,
            client: httpx.Client | None = None) -> dict[str, int]:
    """購読している端末へ送る。user_ids を渡せばその人だけ、permission を渡せばその権限を持つ人だけ。

    戻り値は {"sent": 届いた台数, "removed": 失効で消した台数, "failed": 届かなかった台数}。"""
    from app.database import SessionLocal
    from app.models import PushSubscription

    if user_ids is not None and not user_ids:
        return {"sent": 0, "removed": 0, "failed": 0}
    payload = {"title": title[:120], "body": body[:1000], "url": url if url.startswith("/") and not url.startswith("//") else "/"}
    if tag:
        payload["tag"] = tag[:64]
    result = {"sent": 0, "removed": 0, "failed": 0}
    with SessionLocal() as db:
        targets = _targets(db, user_ids, permission)
        if not targets:
            return result
        own_client = client is None
        client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False)
        try:
            for target in targets:
                status = send_one(target, payload, client=client)
                row = db.get(PushSubscription, target.subscription_id)
                if status in (404, 410):
                    if row is not None:
                        db.delete(row)
                    result["removed"] += 1
                elif 200 <= status < 300:
                    if row is not None:
                        row.last_success_at = datetime.now(timezone.utc)
                    result["sent"] += 1
                else:
                    result["failed"] += 1
            db.commit()
        finally:
            if own_client:
                client.close()
    return result


def send_in_background(**kwargs) -> None:
    """待たずに送る（実行の終わりを見つけた所など、同期の処理の途中から呼ぶ）。"""
    def run() -> None:
        try:
            send_to(**kwargs)
        except Exception:  # noqa: BLE001 - 通知の失敗で本来の処理を止めない
            logger.exception("Web Push を送れませんでした")

    threading.Thread(target=run, name="web-push", daemon=True).start()


def endpoint_digest(endpoint: str) -> str:
    from app.security.crypto import keyed_digest

    return keyed_digest("push-endpoint", endpoint, length=64)
