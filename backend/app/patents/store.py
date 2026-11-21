"""特許案件の保存（ナレッジの patents 分野のファイル data_dir/knowledge/patents.sqlite3 に同居）。

案件・請求項・調査メモは未公開のアイデアそのもの。外部（クラウド）LLM へは出さない（app.patents.service）。
公報などの文献は app.knowledge.store の documents に置き、案件からは doc_ref（"分野:番号"）で参照する。
"""
from __future__ import annotations

import json
import sqlite3
import time

from app.knowledge import store as kstore
from app.knowledge import textproc
from app.patents import domain

SHARD = "patents"
SCHEMA = """
CREATE TABLE IF NOT EXISTS patent_cases(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, stage TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS patent_events(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, kind TEXT NOT NULL, date TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_patent_events_case ON patent_events(case_id);
CREATE TABLE IF NOT EXISTS patent_claims(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, version INTEGER NOT NULL, text TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', created REAL NOT NULL, UNIQUE(case_id, version));
CREATE TABLE IF NOT EXISTS patent_refs(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, doc_ref TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL, relevance TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_patent_refs_case ON patent_refs(case_id);
CREATE TABLE IF NOT EXISTS patent_assists(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, task TEXT NOT NULL, status TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
  basis TEXT NOT NULL DEFAULT 'model_inference', review_status TEXT NOT NULL DEFAULT 'unreviewed',
  job_id TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_patent_assists_case ON patent_assists(case_id);
"""

DATA_FIELDS = ("field", "problem", "solution", "effect", "embodiment", "claims_draft", "notes", "hearing")
MAX_TEXT = 200_000


class PatentError(ValueError):
    pass


class NotFound(PatentError):
    pass


_ready: set[str] = set()


def connect() -> sqlite3.Connection:
    conn = kstore.connect(SHARD)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='patent_assists'").fetchone() is None:
        conn.executescript(SCHEMA)
        conn.commit()
    return conn


def _now() -> float:
    return time.time()


def _clean_data(data: dict | None) -> dict:
    out = {}
    for key, value in (data or {}).items():
        if key in DATA_FIELDS:
            out[key] = str(value or "")[:MAX_TEXT]
        elif key == "foreign_resident":
            out[key] = bool(value)
    return out


def _case(conn: sqlite3.Connection, case_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM patent_cases WHERE id=?", (int(case_id),)).fetchone()
    if row is None:
        raise NotFound("案件がありません")
    return row


def _touch(conn: sqlite3.Connection, case_id: int) -> None:
    conn.execute("UPDATE patent_cases SET updated=? WHERE id=?", (_now(), int(case_id)))


def _summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    data = json.loads(row["data"] or "{}")
    events = [dict(r) for r in conn.execute("SELECT kind, date FROM patent_events WHERE case_id=?", (row["id"],))]
    upcoming = [d for d in domain.deadlines(events, bool(data.get("foreign_resident"))) if d["days_left"] >= -30]
    stage = next((s for s in domain.STAGES if s["id"] == row["stage"]), None)
    return {"id": int(row["id"]), "title": row["title"], "stage": row["stage"],
            "stage_label": stage["label"] if stage else row["stage"], "created": row["created"],
            "updated": row["updated"], "next_deadline": upcoming[0] if upcoming else None}


def list_cases() -> list[dict]:
    conn = connect()
    try:
        return [_summary(conn, r) for r in conn.execute("SELECT * FROM patent_cases ORDER BY updated DESC")]
    finally:
        conn.close()


def get_case(case_id: int) -> dict:
    conn = connect()
    try:
        row = _case(conn, case_id)
        data = json.loads(row["data"] or "{}")
        events = [dict(r) for r in conn.execute(
            "SELECT id, kind, date, note FROM patent_events WHERE case_id=? ORDER BY date, id", (row["id"],))]
        claims = [dict(r) for r in conn.execute(
            "SELECT id, version, text, note, created FROM patent_claims WHERE case_id=? ORDER BY version DESC", (row["id"],))]
        refs = [dict(r) for r in conn.execute(
            "SELECT id, doc_ref, ref, category, relevance, note, created FROM patent_refs WHERE case_id=? "
            "ORDER BY category, id", (row["id"],))]
        assists = [_assist_row(r) for r in conn.execute(
            "SELECT * FROM patent_assists WHERE case_id=? ORDER BY id DESC LIMIT 50", (row["id"],))]
        out = _summary(conn, row)
    finally:
        conn.close()
    latest = claims[0]["text"] if claims else data.get("claims_draft", "")
    out.update({
        "data": data, "events": events, "claims": claims, "refs": refs, "assists": assists,
        "deadlines": domain.deadlines(events, bool(data.get("foreign_resident"))),
        "stage_info": next((s for s in domain.STAGES if s["id"] == out["stage"]), None),
        "claim_elements": [{"no": no, "text": text, "elements": domain.claim_elements(text)}
                           for no, text in textproc.split_claims(latest)][:50],
    })
    return out


def create_case(title: str, data: dict | None = None) -> dict:
    title = (title or "").strip()
    if not title:
        raise PatentError("発明の名称を入れてください")
    now = _now()
    conn = connect()
    try:
        case_id = conn.execute(
            "INSERT INTO patent_cases(title, stage, data, created, updated) VALUES(?,?,?,?,?)",
            (title[:300], domain.STAGE_IDS[0], json.dumps(_clean_data(data), ensure_ascii=False), now, now)).lastrowid
        conn.commit()
    finally:
        conn.close()
    return get_case(int(case_id))


def update_case(case_id: int, *, title: str | None = None, stage: str | None = None, data: dict | None = None) -> dict:
    conn = connect()
    try:
        row = _case(conn, case_id)
        new_stage = stage or row["stage"]
        if new_stage not in domain.STAGE_IDS:
            raise PatentError("不正な段階です")
        merged = json.loads(row["data"] or "{}")
        merged.update(_clean_data(data))
        new_title = (title if title is not None else row["title"]).strip()[:300]
        if not new_title:
            raise PatentError("発明の名称を入れてください")
        conn.execute("UPDATE patent_cases SET title=?, stage=?, data=?, updated=? WHERE id=?",
                     (new_title, new_stage, json.dumps(merged, ensure_ascii=False), _now(), row["id"]))
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def append_hearing(case_id: int, text: str, *, replace: bool = False) -> dict:
    """ヒアリングの記録に足す（音声の文字起こし・手入力）。区切りに日時を入れる。"""
    import datetime as dt

    text = (text or "").strip()
    conn = connect()
    try:
        row = _case(conn, case_id)
        data = json.loads(row["data"] or "{}")
        if replace:
            data["hearing"] = text[:MAX_TEXT]
        elif text:
            stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            joined = (str(data.get("hearing", "")).rstrip() + f"\n\n〔{stamp}〕\n{text}").strip()
            data["hearing"] = joined[-MAX_TEXT:]
        conn.execute("UPDATE patent_cases SET data=?, updated=? WHERE id=?",
                     (json.dumps(data, ensure_ascii=False), _now(), row["id"]))
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def delete_case(case_id: int) -> None:
    conn = connect()
    try:
        _case(conn, case_id)
        for table in ("patent_events", "patent_claims", "patent_refs", "patent_assists"):
            conn.execute(f"DELETE FROM {table} WHERE case_id=?", (int(case_id),))
        # ウォッチ・候補・期限の通知の記録も消す。案件の番号は使い回されるので、残すと次の案件に出る（2026-11-21 E2E で発見）
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("patent_watches", "patent_watch_hits"):
            if table in names:
                conn.execute(f"DELETE FROM {table} WHERE case_id=?", (int(case_id),))
        if "patent_charts" in names:
            conn.execute("DELETE FROM patent_charts WHERE case_id=?", (int(case_id),))
        if "patent_surveys" in names:
            conn.execute("DELETE FROM patent_surveys WHERE case_id=?", (int(case_id),))
        if "patent_notices" in names:
            conn.execute("DELETE FROM patent_notices WHERE key LIKE ?", (f"{int(case_id)}:%",))
        conn.execute("DELETE FROM patent_cases WHERE id=?", (int(case_id),))
        conn.commit()
    finally:
        conn.close()


def add_event(case_id: int, kind: str, date: str, note: str = "") -> dict:
    if kind not in domain.EVENT_KINDS:
        raise PatentError("不正な出来事の種類です")
    if domain._date(date) is None:
        raise PatentError("日付は YYYY-MM-DD で入れてください")
    conn = connect()
    try:
        _case(conn, case_id)
        conn.execute("INSERT INTO patent_events(case_id, kind, date, note, created) VALUES(?,?,?,?,?)",
                     (int(case_id), kind, str(date)[:10], (note or "")[:2000], _now()))
        _touch(conn, case_id)
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def delete_event(case_id: int, event_id: int) -> dict:
    conn = connect()
    try:
        _case(conn, case_id)
        conn.execute("DELETE FROM patent_events WHERE id=? AND case_id=?", (int(event_id), int(case_id)))
        _touch(conn, case_id)
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def save_claims(case_id: int, text: str, note: str = "") -> dict:
    """請求項を新しい版として保存する（前の版は残す）。内容が最新版と同じなら版を増やさない。"""
    text = (text or "").strip()
    if not text:
        raise PatentError("請求項が空です")
    conn = connect()
    try:
        _case(conn, case_id)
        last = conn.execute("SELECT version, text FROM patent_claims WHERE case_id=? ORDER BY version DESC LIMIT 1",
                            (int(case_id),)).fetchone()
        if last is None or last["text"] != text[:MAX_TEXT]:
            version = (last["version"] if last else 0) + 1
            conn.execute("INSERT INTO patent_claims(case_id, version, text, note, created) VALUES(?,?,?,?,?)",
                         (int(case_id), version, text[:MAX_TEXT], (note or "")[:1000], _now()))
            _touch(conn, case_id)
            conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def add_ref(case_id: int, *, ref: str = "", doc_ref: str = "", category: str = "A", relevance: str = "",
            note: str = "") -> dict:
    if category not in domain.REF_CATEGORIES:
        raise PatentError("分類は X / Y / A / P / E のどれかです")
    if not (ref or doc_ref):
        raise PatentError("文献番号か、検索で選んだ文献を指定してください")
    if doc_ref and not _valid_doc_ref(doc_ref):
        raise PatentError("文献の参照が不正です")
    conn = connect()
    try:
        _case(conn, case_id)
        conn.execute("INSERT INTO patent_refs(case_id, doc_ref, ref, category, relevance, note, created) "
                     "VALUES(?,?,?,?,?,?,?)", (int(case_id), doc_ref, (ref or "")[:300], category,
                                               (relevance or "")[:2000], (note or "")[:2000], _now()))
        _touch(conn, case_id)
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def _valid_doc_ref(doc_ref: str) -> bool:
    shard, _, number = doc_ref.partition(":")
    return shard in kstore.SHARDS and number.isdigit()


def delete_ref(case_id: int, ref_id: int) -> dict:
    conn = connect()
    try:
        _case(conn, case_id)
        conn.execute("DELETE FROM patent_refs WHERE id=? AND case_id=?", (int(ref_id), int(case_id)))
        _touch(conn, case_id)
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


# ---- AI 支援の記録 ----

def _assist_row(row: sqlite3.Row) -> dict:
    task = domain.PROMPTS.get(row["task"], {})
    return {"id": int(row["id"]), "task": row["task"], "label": task.get("label", row["task"]),
            "status": row["status"], "answer": row["answer"], "evidence": json.loads(row["evidence"] or "[]"),
            "error": row["error"], "basis": row["basis"], "review_status": row["review_status"],
            "job_id": row["job_id"], "created": row["created"], "updated": row["updated"]}


def start_assist(case_id: int, task: str) -> int:
    if task not in domain.PROMPTS:
        raise PatentError("不明な支援の種類です")
    now = _now()
    conn = connect()
    try:
        _case(conn, case_id)
        assist_id = conn.execute("INSERT INTO patent_assists(case_id, task, status, created, updated) VALUES(?,?,?,?,?)",
                                 (int(case_id), task, "queued", now, now)).lastrowid
        conn.commit()
    finally:
        conn.close()
    return int(assist_id)


def finish_assist(assist_id: int, *, status: str, answer: str = "", evidence: list | None = None,
                  error: str = "", job_id: str | None = None) -> None:
    conn = connect()
    try:
        sets = ["status=?", "answer=?", "evidence=?", "error=?", "updated=?"]
        values: list = [status, answer[:MAX_TEXT], json.dumps(evidence or [], ensure_ascii=False), error[:2000], _now()]
        if job_id is not None:
            sets.append("job_id=?")
            values.append(job_id)
        conn.execute(f"UPDATE patent_assists SET {', '.join(sets)} WHERE id=?", (*values, int(assist_id)))
        conn.commit()
    finally:
        conn.close()


def review_assist(case_id: int, assist_id: int, review_status: str) -> dict:
    if review_status not in ("unreviewed", "checked", "rejected"):
        raise PatentError("不正な確認状態です")
    conn = connect()
    try:
        conn.execute("UPDATE patent_assists SET review_status=?, updated=? WHERE id=? AND case_id=?",
                     (review_status, _now(), int(assist_id), int(case_id)))
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def delete_assist(case_id: int, assist_id: int) -> dict:
    conn = connect()
    try:
        conn.execute("DELETE FROM patent_assists WHERE id=? AND case_id=?", (int(assist_id), int(case_id)))
        conn.commit()
    finally:
        conn.close()
    return get_case(case_id)


def interrupt_unfinished() -> int:
    """再起動で止まった支援を「中断」にする（起動時に 1 回。件数だけの軽い更新）。"""
    if not kstore.shard_path(SHARD).exists():
        return 0
    conn = connect()
    try:
        count = conn.execute("UPDATE patent_assists SET status='interrupted', updated=? "
                             "WHERE status IN ('queued','running')", (_now(),)).rowcount
        conn.commit()
    finally:
        conn.close()
    return count

