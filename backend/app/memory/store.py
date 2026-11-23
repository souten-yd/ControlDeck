"""記憶の置き場: data_dir/memory/memory.sqlite3（本体の DB とは別ファイル。WAL）。

- 本文は ``app.security.crypto.encrypt_text`` で暗号化して持つ（DB のファイルを読んでも平文は無い）。
  全文の索引は作らない。件数は利用者ごとに数百程度なので、使うときに復号して語の一致で選ぶ。
- sensitivity: normal / protected。protected は秘密の会話の中でしか会話に入れない（policy.py）。
- 同期関数だけを持つ。async から呼ぶときは asyncio.to_thread を使う（AGENTS.md 絶対ルール 9）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import data_dir
from app.security.crypto import decrypt_text, encrypt_text

KINDS = ("preference", "fact", "work")
KIND_LABELS = {"preference": "好み", "fact": "事実", "work": "仕事の文脈"}
SENSITIVITY = ("normal", "protected")
# 作った元。confidential・patent で作った記憶は守りを外せない（利用者が「守る」にしたものだけ外せる）
SOURCE_KINDS = ("chat", "confidential", "patent", "manual")
FORCED_PROTECTED_SOURCES = ("confidential", "patent")
MAX_TEXT = 400
MAX_PER_USER = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact', sensitivity TEXT NOT NULL DEFAULT 'normal',
  source_kind TEXT NOT NULL DEFAULT 'chat', source_id TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, last_used_at REAL, use_count INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_memories_user ON memories(user_id, deleted);
CREATE INDEX IF NOT EXISTS ix_memories_source ON memories(source_id);
CREATE TABLE IF NOT EXISTS prefs(user_id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conv_flags(
  conv_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
  no_use INTEGER NOT NULL DEFAULT 0, no_save INTEGER NOT NULL DEFAULT 0, patent INTEGER NOT NULL DEFAULT 0);
"""

_lock = threading.Lock()


class MemoryError_(ValueError):
    """記憶の操作として成り立たない（本人の判断が要る・守りを外せない等）。"""


def db_path() -> Path:
    folder = data_dir() / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "memory.sqlite3"


def connect() -> sqlite3.Connection:
    path = db_path()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone() is None:
        with _lock:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
        try:
            path.chmod(0o600)
        except OSError:
            pass
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@dataclass
class Memory:
    id: int
    user_id: int
    text: str
    kind: str
    sensitivity: str
    source_kind: str
    source_id: str
    created_at: float
    last_used_at: float | None
    use_count: int
    pinned: bool
    pending: bool

    @property
    def protected(self) -> bool:
        return self.sensitivity == "protected"

    @property
    def forced_protected(self) -> bool:
        return self.source_kind in FORCED_PROTECTED_SOURCES

    def as_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "kind": self.kind, "kind_label": KIND_LABELS.get(self.kind, self.kind),
                "sensitivity": self.sensitivity, "protected": self.protected, "forced_protected": self.forced_protected,
                "source_kind": self.source_kind, "source_id": self.source_id, "created_at": self.created_at,
                "last_used_at": self.last_used_at, "use_count": self.use_count, "pinned": self.pinned,
                "pending": self.pending}


def _row(row: sqlite3.Row) -> Memory | None:
    try:
        text = decrypt_text(row["text"])
    except Exception:  # noqa: BLE001 - 鍵が変わった等で読めない行は無いものとして扱う（平文は持たない）
        return None
    return Memory(id=row["id"], user_id=row["user_id"], text=text, kind=row["kind"], sensitivity=row["sensitivity"],
                  source_kind=row["source_kind"], source_id=row["source_id"], created_at=row["created_at"],
                  last_used_at=row["last_used_at"], use_count=row["use_count"], pinned=bool(row["pinned"]),
                  pending=bool(row["pending"]))


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())[:MAX_TEXT]


def norm(text: str) -> str:
    """重複の判定用（空白・句読点を落とす）。"""
    return "".join(ch for ch in _clean(text).lower() if ch.isalnum())


def list_memories(user_id: int, *, include_pending: bool = True) -> list[Memory]:
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM memories WHERE user_id=? AND deleted=0 ORDER BY pinned DESC, id DESC",
                            (user_id,)).fetchall()
    finally:
        conn.close()
    out = [m for m in (_row(r) for r in rows) if m is not None]
    return out if include_pending else [m for m in out if not m.pending]


def get(user_id: int, memory_id: int) -> Memory | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM memories WHERE id=? AND user_id=? AND deleted=0", (memory_id, user_id)).fetchone()
    finally:
        conn.close()
    return _row(row) if row else None


def add(user_id: int, text: str, *, kind: str = "fact", sensitivity: str = "normal", source_kind: str = "chat",
        source_id: str = "", pending: bool = False, pinned: bool = False) -> Memory | None:
    """記憶を 1 件足す。同じ内容が既にあれば足さない（None）。守りの強制: 元が秘密・特許なら必ず protected。"""
    text = _clean(text)
    if not text:
        raise MemoryError_("記憶の本文が空です")
    kind = kind if kind in KINDS else "fact"
    if source_kind not in SOURCE_KINDS:
        source_kind = "chat"
    if sensitivity not in SENSITIVITY:
        sensitivity = "protected"   # 分からないときは守る側
    if source_kind in FORCED_PROTECTED_SOURCES:
        sensitivity = "protected"
    key = norm(text)
    existing = list_memories(user_id)
    if any(norm(m.text) == key for m in existing):
        return None
    if len(existing) >= MAX_PER_USER:
        raise MemoryError_(f"記憶は {MAX_PER_USER} 件までです。古いものを消してください")
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO memories(user_id, text, kind, sensitivity, source_kind, source_id, created_at, pending, pinned)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (user_id, encrypt_text(text), kind, sensitivity, source_kind, str(source_id)[:64], time.time(),
             1 if pending else 0, 1 if pinned else 0))
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()
    return get(user_id, int(new_id))


def update(user_id: int, memory_id: int, *, text: str | None = None, kind: str | None = None,
           protected: bool | None = None, pinned: bool | None = None, pending: bool | None = None) -> Memory | None:
    current = get(user_id, memory_id)
    if current is None:
        return None
    fields: dict[str, object] = {}
    if text is not None:
        cleaned = _clean(text)
        if not cleaned:
            raise MemoryError_("記憶の本文が空です")
        fields["text"] = encrypt_text(cleaned)
    if kind is not None and kind in KINDS:
        fields["kind"] = kind
    if protected is not None:
        if not protected and current.forced_protected:
            raise MemoryError_("秘密の会話・特許の流れから作った記憶は、守りを外せません")
        fields["sensitivity"] = "protected" if protected else "normal"
    if pinned is not None:
        fields["pinned"] = 1 if pinned else 0
    if pending is not None:
        fields["pending"] = 1 if pending else 0
    if fields:
        conn = connect()
        try:
            conn.execute(f"UPDATE memories SET {', '.join(f'{k}=?' for k in fields)} WHERE id=? AND user_id=?",
                         (*fields.values(), memory_id, user_id))
            conn.commit()
        finally:
            conn.close()
    return get(user_id, memory_id)


def _purge(conn: sqlite3.Connection, where: str, args: tuple) -> int:
    """消す = 行ごと削除（本文の暗号文も残さない）。"""
    cur = conn.execute(f"DELETE FROM memories WHERE {where}", args)
    conn.commit()
    return cur.rowcount


def delete(user_id: int, memory_id: int) -> bool:
    conn = connect()
    try:
        return _purge(conn, "id=? AND user_id=?", (memory_id, user_id)) > 0
    finally:
        conn.close()


def delete_by_source(user_id: int, source_id: str) -> int:
    """会話を消すときに「この会話から作った記憶も消す」。"""
    conn = connect()
    try:
        return _purge(conn, "user_id=? AND source_id=?", (user_id, str(source_id)))
    finally:
        conn.close()


def delete_all(user_id: int) -> int:
    conn = connect()
    try:
        return _purge(conn, "user_id=?", (user_id,))
    finally:
        conn.close()


def protect_by_source(user_id: int, source_id: str) -> int:
    """会話が途中で秘密になったとき、その会話から作った記憶を守る側へ寄せる。"""
    conn = connect()
    try:
        cur = conn.execute("UPDATE memories SET sensitivity='protected', source_kind=CASE WHEN source_kind='chat' "
                           "THEN 'confidential' ELSE source_kind END WHERE user_id=? AND source_id=?",
                           (user_id, str(source_id)))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def touch(user_id: int, ids: list[int]) -> None:
    if not ids:
        return
    conn = connect()
    try:
        conn.executemany("UPDATE memories SET last_used_at=?, use_count=use_count+1 WHERE id=? AND user_id=?",
                         [(time.time(), i, user_id) for i in ids])
        conn.commit()
    finally:
        conn.close()


# ---- 設定（利用者ごと） ----

PREF_DEFAULTS = {
    "enabled": True,            # 記憶を使う
    "creation": "explicit",     # off / explicit（「覚えて」と言ったときだけ）/ confirm（候補を画面で承認）/ auto
    "use_external": False,      # 外部の AI の会話にも使う（normal だけ）
    "use_opencode": False,      # OpenCode（作業場）にも使う（normal だけ）
}
CREATION_MODES = ("off", "explicit", "confirm", "auto")


def get_prefs(user_id: int) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT data FROM prefs WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    saved: dict = {}
    if row:
        try:
            saved = json.loads(row["data"])
        except ValueError:
            saved = {}
    prefs = dict(PREF_DEFAULTS)
    for key, default in PREF_DEFAULTS.items():
        if key in saved and type(saved[key]) is type(default):
            prefs[key] = saved[key]
    if prefs["creation"] not in CREATION_MODES:
        prefs["creation"] = PREF_DEFAULTS["creation"]
    return prefs


def save_prefs(user_id: int, patch: dict) -> dict:
    merged = {**get_prefs(user_id), **{k: v for k, v in patch.items() if k in PREF_DEFAULTS}}
    if merged["creation"] not in CREATION_MODES:
        raise MemoryError_("作り方が不正です")
    conn = connect()
    try:
        conn.execute("INSERT INTO prefs(user_id, data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                     (user_id, json.dumps(merged)))
        conn.commit()
    finally:
        conn.close()
    return merged


# ---- 会話ごとの切り替え・特許の印 ----

def get_flags(conv_id: str) -> dict:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM conv_flags WHERE conv_id=?", (conv_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"no_use": False, "no_save": False, "patent": False}
    return {"no_use": bool(row["no_use"]), "no_save": bool(row["no_save"]), "patent": bool(row["patent"])}


def set_flags(conv_id: str, user_id: int, *, no_use: bool | None = None, no_save: bool | None = None,
              patent: bool | None = None) -> dict:
    """patent は一方通行（立てたら下ろせない。秘密の会話と同じ）。"""
    current = get_flags(conv_id)
    merged = {"no_use": current["no_use"] if no_use is None else no_use,
              "no_save": current["no_save"] if no_save is None else no_save,
              "patent": current["patent"] or bool(patent)}
    conn = connect()
    try:
        conn.execute("INSERT INTO conv_flags(conv_id, user_id, no_use, no_save, patent) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(conv_id) DO UPDATE SET no_use=excluded.no_use, no_save=excluded.no_save, patent=excluded.patent",
                     (conv_id, user_id, int(merged["no_use"]), int(merged["no_save"]), int(merged["patent"])))
        conn.commit()
    finally:
        conn.close()
    return merged


def delete_flags(conv_id: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM conv_flags WHERE conv_id=?", (conv_id,))
        conn.commit()
    finally:
        conn.close()
