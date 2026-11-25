"""QNAP ニュースハブとつなぐ（ペアリング・鍵の封じた受け渡し・状態の保存・ハブへの呼び出し）。

2026-11-25 利用者「ControlDeck に QNAP ニュースハブとつなぐ機能」（docs/PLAN-integrated-knowledge.md 1.5 章・案 C。相手の仕様は QnapNewsHub の docs/DESIGN.md 3 章）。

- **つなぐ**: 利用者がハブの画面に出した 6 桁の番号と、ハブの URL を入れる → ControlDeck が X25519 の鍵対を作り、公開鍵を `POST /link/pair` で送る →
  ハブが scope=link のトークンとハブの公開鍵を返す。**両方の指紋**（公開鍵の SHA-256 の先頭 80 ビットを 4 文字ごと）を画面に出し、利用者がハブの画面の指紋と見比べて
  「同じ」と確かめるまで（verified）、同期・仕事・鍵の受け渡しはしない（途中のなりすましを防ぐのは、この目での確認だけ）。ハブの指紋は返された公開鍵から**自分で計算**する（ハブの申告は信じない）。
- **保存**: data_dir/news/hub.json（0600）。トークンと秘密鍵は既存の Fernet（security/crypto）で暗号化し、画面・API の応答・ログ・監査に出さない。
- **鍵を渡す**: 利用者が押したときだけ、OpenRouter の鍵をハブの公開鍵で封じる（ECIES: 使い捨ての X25519 → ECDH → HKDF-SHA256〔salt = 使い捨て公開鍵 ‖ ハブの公開鍵、info = qnapnewshub-link-v1〕
  → AES-256-GCM〔nonce 12 バイト、追加データ = qnapnewshub-link-v1|<名前>〕）。ハブの `link.seal` と同じ方式（試験でハブの本物のコードと往復する）。
- 呼び出しは同期の httpx（LAN の URL へ。Web 収集の出口は私設 IP を断るのでここでは使わない）。**リダイレクトは辿らない**（トークンを別の宛先へ送らない）。async からは asyncio.to_thread で呼ぶ。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import data_dir
from app.http_tls import shared_ssl_context
from app.security.crypto import decrypt_text, encrypt_text

logger = logging.getLogger("control_deck.news.hub")

INFO = b"qnapnewshub-link-v1"
API = "/api/v1"
DEFAULT_PORT = 8787      # ハブの既定のポート（URL に無いとき補う）
CONNECT_TIMEOUT, READ_TIMEOUT = 5.0, 30.0
NAME_DEFAULT = "ControlDeck"
SECRET_NAME = "openrouter"
_lock = threading.RLock()


class HubError(Exception):
    """利用者に見せてよい理由つきの失敗。メッセージにトークン・鍵・番号を入れない。status はハブの HTTP の状態（つながらないときは 0）。"""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- 暗号（ハブの link.py と同じ方式） ----

def fingerprint(public: bytes) -> str:
    """公開鍵の指紋（短い表示）: SHA-256 の先頭 80 ビットを 4 文字ごと。例 1a2b-3c4d-5e6f-7a8b-9c0d。ハブの画面と同じ式。"""
    digest = hashlib.sha256(public).hexdigest()[:20]
    return "-".join(digest[i:i + 4] for i in range(0, 20, 4))


def _raw_public(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def seal(hub_public: bytes, name: str, plaintext: str) -> dict:
    """鍵をハブの公開鍵で封じる。送る形は {"v":1, "epk", "n", "c"}（base64）。"""
    eph = X25519PrivateKey.generate()
    epk = _raw_public(eph)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + hub_public, info=INFO).derive(eph.exchange(X25519PublicKey.from_public_bytes(hub_public)))
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), INFO + b"|" + name.encode("utf-8"))
    return {"v": 1, "epk": base64.b64encode(epk).decode(), "n": base64.b64encode(nonce).decode(), "c": base64.b64encode(sealed).decode()}


# ---- 状態の保存 ----

def _path():
    return data_dir() / "news" / "hub.json"


def _empty() -> dict:
    return {"url": "", "name": "", "token_enc": "", "private_enc": "", "public_key": "", "hub_public_key": "", "hub_fingerprint": "", "my_fingerprint": "",
            "paired_at": "", "verified": False, "verified_at": "", "cursor": 0, "latest": 0, "scope": "",
            "sync": {"last_at": "", "last_ok_at": "", "last_error": "", "failures": 0, "reachable": None, "counts": {}, "total": {"items": 0, "entities": 0, "events": 0, "removed": 0}},
            "jobs": {"last_at": "", "last_error": "", "done": 0, "failed": 0, "rejected": 0, "recent": []},
            "secret": {"sent_at": "", "error": ""}}


def load() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    state = _empty()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in ("sync", "jobs", "secret") and isinstance(value, dict):
                state[key].update(value)
            elif key in state:
                state[key] = value
    return state


def _write(state: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def update(fn) -> dict:  # noqa: ANN001
    """状態を読んで fn(state) で書き換え、保存して返す（同時に書き換えない）。"""
    with _lock:
        state = load()
        fn(state)
        _write(state)
        return state


def linked(state: dict | None = None) -> bool:
    state = state or load()
    return bool(state["url"] and state["token_enc"])


def ready(state: dict | None = None) -> bool:
    """同期・仕事・鍵の受け渡しをしてよいか（つないで、指紋を利用者が確かめた）。"""
    state = state or load()
    return linked(state) and bool(state["verified"])


def _token(state: dict) -> str:
    try:
        return decrypt_text(state["token_enc"])
    except Exception:  # noqa: BLE001
        raise HubError("保存したトークンを読めません。いったん切って、つなぎ直してください") from None


def public_view(state: dict | None = None) -> dict:
    """画面向け。トークン・秘密鍵・番号は含まない。"""
    state = state or load()
    return {"linked": linked(state), "verified": bool(state["verified"]), "url": state["url"], "name": state["name"], "my_fingerprint": state["my_fingerprint"],
            "hub_fingerprint": state["hub_fingerprint"], "paired_at": state["paired_at"], "verified_at": state["verified_at"], "cursor": int(state["cursor"] or 0),
            "latest": int(state["latest"] or 0), "sync": state["sync"], "jobs": state["jobs"], "secret": state["secret"]}


# ---- ハブへの呼び出し ----

def normalize_url(value: object) -> str:
    """ハブの URL（http か https・利用者情報・クエリなし）。末尾の / は除く。**ポートが無ければ 8787 を補う**（http・https とも。明示があればそのまま）。"""
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise HubError("ハブの URL の形が正しくありません（ポートは 1〜65535 の数字です）") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise HubError("ハブの URL は http:// か https:// で始まる形で入れてください（利用者名・パスワード・クエリは入れません）")
    if port is None:
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        text = parts._replace(netloc=f"{host}:{DEFAULT_PORT}").geturl()
    return text.rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(verify=shared_ssl_context(), follow_redirects=False, timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT), trust_env=False)


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, str) and detail:
        return detail[:200]
    return f"ハブが {response.status_code} を返しました"


def call(method: str, path: str, *, url: str | None = None, token: str | None = None, json_body: dict | None = None, content: bytes | None = None,
         params: dict | None = None, headers: dict | None = None, raw: bool = False):
    """ハブの API を呼ぶ。url・token を渡さなければ保存した値（つないで確かめた後だけ）。失敗は HubError。"""
    state = None
    if url is None or token is None:
        state = load()
        if not ready(state):
            raise HubError("ハブとつながっていないか、指紋の確認が済んでいません", 409)
        url, token = state["url"], _token(state)
    head = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})} if token else {"Accept": "application/json", **(headers or {})}
    try:
        with _client() as client:
            response = client.request(method, f"{url}{API}{path}", headers=head, params=params, json=json_body, content=content)
    except httpx.TimeoutException:
        raise HubError(f"ハブが時間内に答えませんでした（試したアドレス: {url}）") from None
    except httpx.HTTPError as exc:
        raise HubError(f"ハブにつながりません（{type(exc).__name__}。試したアドレス: {url}。ポートは通常 {DEFAULT_PORT}。設定の「探す」で LAN から探せます）") from None
    if 300 <= response.status_code < 400:
        raise HubError("ハブがリダイレクトを返しました（URL を確かめてください。辿りません）", response.status_code)
    if response.status_code in (401, 403) and token:
        raise HubError("ハブが認証を断りました（ハブの画面で取り消された可能性があります）。つなぎ直してください", response.status_code)
    if response.status_code >= 400:
        raise HubError(_detail(response), response.status_code)
    if raw:
        return response
    try:
        return response.json()
    except ValueError:
        raise HubError("ハブの応答が JSON ではありません", response.status_code) from None


# ---- つなぐ・確かめる・切る ----

def pair(url: str, code: str, name: str = NAME_DEFAULT) -> dict:
    """番号でつなぐ。トークンと自分の鍵を保存する（まだ verified ではない）。画面に出す両方の指紋を返す。"""
    base = normalize_url(url)
    code = str(code or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        raise HubError("つなぐ番号は 6 桁の数字です")
    name = " ".join(str(name or NAME_DEFAULT).split())[:60] or NAME_DEFAULT
    priv = X25519PrivateKey.generate()
    mine = _raw_public(priv)
    reply = call("POST", "/link/pair", url=base, token="", json_body={"code": code, "name": name, "public_key": base64.b64encode(mine).decode()})
    try:
        token = str(reply["token"])
        hub_pub = base64.b64decode(str(reply["public_key"]), validate=True)
        if not token or len(hub_pub) != 32:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise HubError("ハブの応答の形が正しくありません") from None
    theirs = fingerprint(hub_pub)
    claimed = str(reply.get("fingerprint") or "")
    secret_raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())

    def apply(state: dict) -> None:
        state.update(_empty())
        state.update(url=base, name=name, token_enc=encrypt_text(token), private_enc=encrypt_text(base64.b64encode(secret_raw).decode()),
                     public_key=base64.b64encode(mine).decode(), hub_public_key=base64.b64encode(hub_pub).decode(), hub_fingerprint=theirs,
                     my_fingerprint=fingerprint(mine), paired_at=now_iso())
    update(apply)
    logger.info("ニュースハブとつなぎました（指紋の確認待ち）")
    return {**public_view(), "claimed_fingerprint_matches": (not claimed) or claimed == theirs}


def confirm(match: bool) -> dict:
    """利用者が、画面の指紋とハブの画面の指紋を見比べた結果。同じなら使い始める。違うなら保存したものを捨てる（ハブの画面で相手を取り消す）。"""
    if not linked():
        raise HubError("まだつながっていません", 409)
    if not match:
        disconnect()
        return public_view()

    def apply(state: dict) -> None:
        state["verified"], state["verified_at"] = True, now_iso()
    update(apply)
    return public_view()


def disconnect() -> dict:
    """つなぎを切る（保存したトークン・鍵・cursor を捨てる）。ハブ側の取り消しは、ハブの画面の「つないだ ControlDeck」から。取り込んだ記事は消さない。"""
    update(lambda state: (state.clear(), state.update(_empty())))
    return public_view()


# ---- OpenRouter の鍵を渡す ----

def send_openrouter_key() -> dict:
    """利用者が押したときだけ。ControlDeck の OpenRouter の鍵をハブの公開鍵で封じて送る。値は画面・ログ・応答に出さない。"""
    from app.models_mgmt import cloud

    state = load()
    if not ready(state):
        raise HubError("ハブとつながっていないか、指紋の確認が済んでいません", 409)
    value = cloud.api_key("openrouter")
    if not value:
        raise HubError("ControlDeck に OpenRouter の鍵が入っていません（設定 → API キー）", 409)
    try:
        box = seal(base64.b64decode(state["hub_public_key"]), SECRET_NAME, value)
        call("POST", "/link/secret", json_body={"name": SECRET_NAME, "sealed": box})
    except HubError as exc:
        update(lambda s: s["secret"].update(error=exc.message))
        raise
    finally:
        value = ""
    update(lambda s: s["secret"].update(sent_at=now_iso(), error=""))
    logger.info("OpenRouter の鍵をハブへ封じて渡しました（値は記録しません）")
    return {"ok": True, "sent_at": load()["secret"]["sent_at"]}
