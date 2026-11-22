"""特許ウォッチと期限の通知（計画 §10、R3-2）。ResearchDataHub v0.2 の watch.py・notify.py を統合したもの。

- ウォッチ: 案件ごとに「取得先・検索語・間隔（6 時間以上）」を登録し、期限が来たら公開データを取得して**新しく入った文献だけ**を
  案件の請求項（無ければ課題・解決手段）に近い順に並べ、ヒットとして残す。利用者がヒットを X / Y / A で先行文献に登録するか、対象外にする。
- 外へ送る検索語は、保存時と実行のたびに egress.check で自動に検査する（案件の未公開の文章を 25 文字以上連続で写した
  塊があれば送らず、last_status に理由を残す）。手動の確認（approved_public）は要らない（列は互換のため残す）。
- 期限の通知: 1 日 1 回、各案件の期限の目安が 60 / 30 / 7 日前に入ったら、しきい値ごとに 1 回だけ。**本文に案件名や内容を書かない**
  （Web Push は外部のサーバーを通るため）。件数と「Patent で確認」だけ。
- 起動は既存の保守の周期（maintenance_loop）から tick() を呼ぶ。常駐を増やさない。重い処理は Jobs。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from app.knowledge import sources
from app.patents import domain
from app.patents import store as pstore

logger = logging.getLogger("control_deck.patents.watch")

MIN_INTERVAL_HOURS = 6
THRESHOLDS = (60, 30, 7)
HIT_STATUSES = ("new", "relevant", "excluded", "registered")

SCHEMA = """
CREATE TABLE IF NOT EXISTS patent_watches(
  id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, source TEXT NOT NULL, query TEXT NOT NULL,
  interval_hours INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, approved_public INTEGER NOT NULL DEFAULT 0,
  approved_at REAL, last_run REAL, last_status TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS patent_watch_hits(
  id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL, case_id INTEGER NOT NULL, doc_ref TEXT NOT NULL, title TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new', created REAL NOT NULL, UNIQUE(watch_id, doc_ref));
CREATE INDEX IF NOT EXISTS ix_watch_hits_case ON patent_watch_hits(case_id, status);
CREATE TABLE IF NOT EXISTS patent_notices(key TEXT PRIMARY KEY, sent REAL NOT NULL);
CREATE TABLE IF NOT EXISTS patent_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_ready: set[str] = set()


def connect():
    conn = pstore.connect()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='patent_meta'").fetchone() is None:
        conn.executescript(SCHEMA)
        conn.commit()
    return conn


def _row(r) -> dict:
    return {"id": int(r["id"]), "case_id": int(r["case_id"]), "source": r["source"], "query": r["query"],
            "interval_hours": int(r["interval_hours"]), "enabled": bool(r["enabled"]),
            "approved_public": bool(r["approved_public"]), "last_run": r["last_run"], "last_status": r["last_status"],
            "source_label": sources.SOURCES.get(r["source"], {}).get("label", r["source"])}


def list_watches(case_id: int) -> list[dict]:
    conn = connect()
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM patent_watches WHERE case_id=? ORDER BY id", (int(case_id),))]
    finally:
        conn.close()


def save_watch(case_id: int, *, source: str, query: str, interval_hours: int, approved_public: bool = True,
               watch_id: int | None = None, enabled: bool = True, check: bool = True) -> dict:
    if source not in sources.SOURCES:
        raise pstore.PatentError("知らない取得先です")
    query = (query or "").strip()
    if not query or len(query) > 500:
        raise pstore.PatentError("検索語（1〜500 文字）を入れてください")
    if interval_hours < MIN_INTERVAL_HOURS or interval_hours > 24 * 30:
        raise pstore.PatentError(f"間隔は {MIN_INTERVAL_HOURS} 時間〜30 日です")
    from app.patents import egress

    pstore.get_case(case_id)
    if check:
        egress.check(query, case_id)        # 未公開の文章を写した塊は保存の時点で断る（実行のたびにも検査する）
    approved_public = True                  # 手動の確認は廃止（自動の検査に置き換え）
    now = time.time()
    conn = connect()
    try:
        if watch_id is None:
            watch_id = conn.execute(
                "INSERT INTO patent_watches(case_id, source, query, interval_hours, enabled, approved_public, approved_at, created) "
                "VALUES(?,?,?,?,?,?,?,?)", (int(case_id), source, query, int(interval_hours), int(enabled),
                                            int(approved_public), now if approved_public else None, now)).lastrowid
        else:
            old = conn.execute("SELECT * FROM patent_watches WHERE id=? AND case_id=?", (int(watch_id), int(case_id))).fetchone()
            if old is None:
                raise pstore.NotFound("ウォッチがありません")
            # 検索語・取得先が変わったら、公開してよいかの確認をやり直す
            same = old["query"] == query and old["source"] == source
            approved = True
            conn.execute("UPDATE patent_watches SET source=?, query=?, interval_hours=?, enabled=?, approved_public=?, approved_at=? "
                         "WHERE id=?", (source, query, int(interval_hours), int(enabled), int(approved),
                                        now if approved and (not same or not old["approved_public"]) else old["approved_at"], int(watch_id)))
        conn.commit()
        return _row(conn.execute("SELECT * FROM patent_watches WHERE id=?", (int(watch_id),)).fetchone())
    finally:
        conn.close()


def delete_watch(case_id: int, watch_id: int) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM patent_watches WHERE id=? AND case_id=?", (int(watch_id), int(case_id)))
        conn.commit()
    finally:
        conn.close()


def hits(case_id: int, status: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql, args = "SELECT * FROM patent_watch_hits WHERE case_id=?", [int(case_id)]
        if status:
            sql += " AND status=?"
            args.append(status)
        rows = conn.execute(sql + " ORDER BY created DESC, score DESC LIMIT 200", args).fetchall()
    finally:
        conn.close()
    return [{"id": int(r["id"]), "watch_id": int(r["watch_id"]), "doc_ref": r["doc_ref"], "title": r["title"],
             "score": r["score"], "status": r["status"], "created": r["created"]} for r in rows]


def decide_hit(case_id: int, hit_id: int, status: str, category: str | None = None) -> dict:
    """ヒットを対象外にする / 関連として残す / 先行文献（X/Y/A…）に登録する。"""
    if status not in HIT_STATUSES:
        raise pstore.PatentError("不正な状態です")
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM patent_watch_hits WHERE id=? AND case_id=?", (int(hit_id), int(case_id))).fetchone()
        if row is None:
            raise pstore.NotFound("ヒットがありません")
        conn.execute("UPDATE patent_watch_hits SET status=? WHERE id=?", (status, int(hit_id)))
        conn.commit()
    finally:
        conn.close()
    if status == "registered":
        pstore.add_ref(case_id, doc_ref=row["doc_ref"], ref=row["title"], category=category or "A",
                       relevance="ウォッチで見つかった")
    return {"id": int(hit_id), "status": status}


def _case_query(case: dict) -> str:
    claims = case["claims"][0]["text"] if case["claims"] else case["data"].get("claims_draft", "")
    text = claims or " ".join(str(case["data"].get(k, "")) for k in ("problem", "solution")) or case["title"]
    return " ".join(domain.keywords(text)[:12]) or case["title"]


async def run_watch(job, watch_id: int) -> dict:
    """1 つのウォッチを動かす。確認の無いもの・無効なものは動かさない。"""
    from app.knowledge import documents

    conn = connect()
    try:
        row = conn.execute("SELECT * FROM patent_watches WHERE id=?", (int(watch_id),)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise pstore.NotFound("ウォッチがありません")
    if not row["enabled"]:
        raise pstore.PatentError("無効なウォッチは動かしません")
    status = "ok"
    from app.patents import egress

    try:
        try:
            # 案件の請求項は後から変わることがあるので、実行のたびに検査する
            await asyncio.to_thread(egress.check, row["query"], int(row["case_id"]))
        except egress.QueryBlocked as blocked:
            status = f"止めました: {blocked.reason}"[:300]
            egress.audit_background("patents.watch_blocked", result="denied", resource_id=str(row["case_id"]),
                                    metadata=egress.blocked_metadata(row["query"], row["source"], int(row["case_id"]), blocked))
            raise
        egress.audit_background("patents.watch_run", result="success", resource_id=str(row["case_id"]),
                                metadata=egress.sent_metadata(row["query"], row["source"], int(row["case_id"])))
        job.set_progress("公開データを取得しています", 0, 3)
        records = await asyncio.to_thread(sources.fetch, row["source"], row["query"], 50)
        job.set_progress("取り込んでいます", 1, 3)
        result = await sources.ingest(records)
        new_refs = {d["ref"]: d["title"] for d in result["documents"] if d["new"]}
        job.set_progress("案件に近い順に並べています", 2, 3)
        case = await asyncio.to_thread(pstore.get_case, int(row["case_id"]))
        ranked = await asyncio.to_thread(documents.search_documents, _case_query(case), consumer="local",
                                         mode="fulltext", limit=200, expand=False)
        score = {d["ref"]: d["score"] for d in ranked}
        now = time.time()
        conn = connect()
        try:
            conn.executemany("INSERT OR IGNORE INTO patent_watch_hits(watch_id, case_id, doc_ref, title, score, status, created) "
                             "VALUES(?,?,?,?,?,?,?)", [(int(watch_id), int(row["case_id"]), ref, title, float(score.get(ref, 0.0)),
                                                         "new", now) for ref, title in new_refs.items()])
            conn.commit()
        finally:
            conn.close()
        status = f"新しく {len(new_refs)} 件（取得 {len(records)} 件）"
        return {"fetched": len(records), "new": len(new_refs)}
    except egress.QueryBlocked:
        raise
    except Exception as exc:
        status = f"失敗: {exc}"[:200]
        raise
    finally:
        conn = connect()
        try:
            conn.execute("UPDATE patent_watches SET last_run=?, last_status=? WHERE id=?", (time.time(), status, int(watch_id)))
            conn.commit()
        finally:
            conn.close()


def due_watches(now: float | None = None) -> list[int]:
    now = now or time.time()
    conn = connect()
    try:
        rows = conn.execute("SELECT id, interval_hours, last_run FROM patent_watches WHERE enabled=1").fetchall()
    finally:
        conn.close()
    return [int(r["id"]) for r in rows if (r["last_run"] or 0) + int(r["interval_hours"]) * 3600 <= now]


def due_notices(today: dt.date | None = None) -> int:
    """期限が 60/30/7 日以内に入った（まだ知らせていない）件数。記録して同じしきい値では 2 度知らせない。"""
    today = today or dt.date.today()
    count = 0
    conn = connect()
    try:
        for case in pstore.list_cases():
            full = pstore.get_case(case["id"])
            for d in full["deadlines"]:
                left = d["days_left"]
                if left < 0:
                    continue
                for threshold in THRESHOLDS:
                    if left <= threshold:
                        key = f"{case['id']}:{d['key']}:{d['due']}:{threshold}"
                        if conn.execute("SELECT 1 FROM patent_notices WHERE key=?", (key,)).fetchone() is None:
                            conn.execute("INSERT INTO patent_notices(key, sent) VALUES(?,?)", (key, time.time()))
                            count += 1
                        break
        conn.commit()
    finally:
        conn.close()
    return count


async def tick() -> dict:
    """保守の周期から呼ぶ。期限の来たウォッチを Jobs に、期限の通知は 1 日 1 回。"""
    from app.knowledge import store as kstore

    if not kstore.shard_path("patents").exists():
        return {"watches": 0, "notices": 0}
    from app.jobs import service as jobs

    started = 0
    for watch_id in await asyncio.to_thread(due_watches):
        jobs.create("patents.watch", "特許ウォッチ", lambda j, w=watch_id: run_watch(j, w),
                    idempotency_key=f"patent-watch-{watch_id}-{int(time.time() // 3600)}")
        started += 1
    notices = 0
    today = dt.date.today().isoformat()
    conn = connect()
    try:
        last = conn.execute("SELECT value FROM patent_meta WHERE key='notices_day'").fetchone()
    finally:
        conn.close()
    if not last or last[0] != today:
        notices = await asyncio.to_thread(due_notices)
        conn = connect()
        try:
            conn.execute("INSERT INTO patent_meta(key, value) VALUES('notices_day', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (today,))
            conn.commit()
        finally:
            conn.close()
        if notices:
            from app.maintenance.auto_update import notify

            # 本文に案件名・内容を書かない（通知は外部のサーバーを通る）
            await notify("特許の期限", f"期限が近い案件があります（{notices} 件）。ControlDeck の Patent で確認してください。")
    return {"watches": started, "notices": notices}
