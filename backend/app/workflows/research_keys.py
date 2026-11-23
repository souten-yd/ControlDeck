"""調査の道具（web_search・read_page）が使うキーと、調べるフォーラムの一覧（2026-11-23 利用者「Reddit の API を登録するところを作って、
入っていたらそれを使って検索できるように。XDA など技術的に有望なフォーラムも検索したい」）。

- キー: Reddit（OAuth のアプリ）と Stack Exchange（任意）。`knowledge/sources.py` と同じく Fernet で暗号化して data_dir/research/keys.json に置く。
  値は画面・ログ・API の応答・監査に出さない（設定済みか・末尾 4 文字だけ）。
- フォーラム: Discourse 系（公開の search.json が匿名で使える）の既定の一覧＋利用者が足したドメイン。ドメインの形だけ許す
  （小文字・英数字・ハイフン・ドット。IP・localhost・内部の名前は不可。通信は egress が内部アドレスも断る）。
"""
from __future__ import annotations

import json
import re
import threading

from app.config import data_dir

FIELDS = ("reddit_client_id", "reddit_client_secret", "reddit_username", "stackexchange_key")

DEFAULT_FORUMS = (
    "forum.level1techs.com", "discuss.huggingface.co", "discuss.pytorch.org", "discourse.ubuntu.com",
    "discussion.fedoraproject.org", "community.frame.work", "discourse.nixos.org", "users.rust-lang.org",
    "community.home-assistant.io", "forums.developer.nvidia.com",
)
MAX_EXTRA = 30
# 読むページ数と出典の上限（2026-11-23 利用者「ニュースを調べて 3 件くらいしか文献を調査していない。エビデンスを増やせるか」）
# llm_parallel: 調査で LLM を呼ぶ処理の同時数。0 は自動（モデルの並列のスロット数から。会話に 1 つ残す）、1〜8 は固定の上書き
LIMITS = {"read_per_aspect": (2, 10, 5), "max_sources": (5, 30, 20), "llm_parallel": (0, 8, 0)}   # 名前: (最小, 最大, 既定)


class ResearchKeyError(ValueError):
    pass


_lock = threading.Lock()
_DOMAIN = re.compile(r"^(?=.{4,100}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def _path():
    d = data_dir() / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d / "keys.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(raw: dict) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def secret(name: str) -> str:
    from app.security.crypto import decrypt_text

    token = _load().get("keys", {}).get(name)
    if not token:
        return ""
    try:
        return decrypt_text(token)
    except Exception:  # noqa: BLE001 - 壊れた鍵は未設定として扱う
        return ""


def set_keys(values: dict[str, str]) -> None:
    """キーを保存する（空文字は削除）。知らない名前は拒む。"""
    from app.security.crypto import encrypt_text

    with _lock:
        raw = _load()
        keys = raw.setdefault("keys", {})
        for name, value in values.items():
            if name not in FIELDS:
                raise ResearchKeyError("知らない項目です")
            if str(value).strip():
                keys[name] = encrypt_text(str(value).strip())
            else:
                keys.pop(name, None)
        _save(raw)


def hint(name: str) -> str:
    """末尾 4 文字（短い値は出さない）。"""
    value = secret(name)
    return f"••••{value[-4:]}" if len(value) >= 12 else ("••••" if value else "")


def reddit() -> dict | None:
    """Reddit の認証情報。3 つそろったときだけ返す（UA にユーザー名が要る）。"""
    cred = {"client_id": secret("reddit_client_id"), "client_secret": secret("reddit_client_secret"),
            "username": secret("reddit_username")}
    return cred if all(cred.values()) else None


def valid_domain(text: str) -> str:
    """ドメインの形だけ許す。正規化した名前を返し、IP・localhost・形が違うものは ResearchKeyError。"""
    domain = re.sub(r"^[a-z]+://", "", str(text or "").strip().lower(), flags=re.I).split("/", 1)[0].split(":", 1)[0]
    domain = domain.removeprefix("www.")
    if re.fullmatch(r"[0-9.]+", domain) or domain.endswith((".local", ".localhost", ".internal", ".lan")) or not _DOMAIN.match(domain):
        raise ResearchKeyError(f"フォーラムのドメインとして使えません: {str(text)[:60]}")
    return domain


def extra_forums() -> list[str]:
    return [d for d in _load().get("forums", []) if isinstance(d, str)]


def set_extra_forums(domains: list[str]) -> None:
    """利用者が足した Discourse のドメイン（空のリストで全部消す）。"""
    cleaned = list(dict.fromkeys(valid_domain(d) for d in domains))
    if len(cleaned) > MAX_EXTRA:
        raise ResearchKeyError(f"足せるのは {MAX_EXTRA} 件までです")
    with _lock:
        raw = _load()
        raw["forums"] = cleaned
        _save(raw)


def forums() -> list[str]:
    """調べる Discourse のドメイン（既定＋足したもの）。"""
    return list(dict.fromkeys([*DEFAULT_FORUMS, *extra_forums()]))


def limits() -> dict[str, int]:
    """観点ごとに読むページ数（ふつうの深さ。速い・深いはここから決まる）と出典の上限。範囲外・壊れた値は範囲へ寄せる。"""
    raw = _load().get("limits", {})
    out = {}
    for name, (low, high, default) in LIMITS.items():
        try:
            out[name] = max(low, min(high, int(raw.get(name, default))))
        except (TypeError, ValueError):
            out[name] = default
    return out


def set_limits(values: dict[str, str]) -> None:
    """読むページ数・出典の上限を保存する（範囲外・数字でない値は拒む）。"""
    cleaned = {}
    for name, value in values.items():
        if name not in LIMITS:
            raise ResearchKeyError("知らない項目です")
        low, high, _ = LIMITS[name]
        try:
            number = int(str(value).strip())
        except ValueError as exc:
            raise ResearchKeyError("数字で入れてください") from exc
        if not low <= number <= high:
            raise ResearchKeyError(f"{low}〜{high} の間で入れてください")
        cleaned[name] = number
    with _lock:
        raw = _load()
        raw["limits"] = {**raw.get("limits", {}), **cleaned}
        _save(raw)


def reset_limits() -> None:
    with _lock:
        raw = _load()
        raw.pop("limits", None)
        _save(raw)
