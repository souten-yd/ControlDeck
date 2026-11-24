"""取得の履歴（news_fetch_log）の書き込みと集計。画面「取得先の管理」（/news/sources）の実績・成功率・履歴の元。

2026-11-24 利用者「これまでの取得実績、最終取得日時などを…サイトごとに確認できたりとか」。service._status は最後の状態だけなので、
1 回ごとの結果を残す。ソースごとに直近 KEEP 回まで（古いものは消す）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.news.db import NewsFetchLog, NewsItem

KEEP = 200
HISTORY_DEFAULT = 50
_HTTP = re.compile(r"HTTP[ :]*(\d{3})|\b(40[0-9]|410|429|5\d\d)\b")


def status_of(error: str) -> int | None:
    """失敗の文から HTTP の状態を読み取る（無ければ None）。"""
    m = _HTTP.search(error or "")
    return int(m.group(1) or m.group(2)) if m else None


def record(db: Session, rows: list[dict]) -> None:
    """rows: [{source_key, at(datetime), ok, status, count, new, ms, error, skipped}]。書いた後、ソースごとに KEEP 回を超えた古い行を消す。"""
    if not rows:
        return
    for r in rows:
        db.add(NewsFetchLog(source_key=r["source_key"][:600], at=r["at"], ok=bool(r["ok"]), status=r.get("status"), count=int(r.get("count", 0)),
                            new=int(r.get("new", 0)), ms=int(r.get("ms", 0)), error=str(r.get("error", ""))[:500], skipped=str(r.get("skipped", ""))[:500]))
    db.flush()
    for key in {r["source_key"][:600] for r in rows}:
        keep = select(NewsFetchLog.id).where(NewsFetchLog.source_key == key).order_by(NewsFetchLog.id.desc()).limit(KEEP)
        db.execute(delete(NewsFetchLog).where(NewsFetchLog.source_key == key, NewsFetchLog.id.not_in(keep)))
    db.commit()


def history(db: Session, key: str, limit: int = HISTORY_DEFAULT) -> list[dict]:
    rows = db.scalars(select(NewsFetchLog).where(NewsFetchLog.source_key == key).order_by(NewsFetchLog.id.desc()).limit(max(1, min(limit, KEEP))))
    return [{"at": _iso(r.at), "ok": r.ok, "status": r.status, "count": r.count, "new": r.new, "ms": r.ms, "error": r.error, "skipped": r.skipped} for r in rows]


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def week_bars(db: Session, now: datetime | None = None) -> dict[str, list[int | None]]:
    """ソースごとの直近 7 日（古い日→今日）の成功率（0〜100 %。その日に取っていなければ None）。"""
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(select(NewsFetchLog.source_key, NewsFetchLog.at, NewsFetchLog.ok).where(NewsFetchLog.at >= start)).all()
    days: dict[str, list[list[int]]] = {}
    for key, at, ok in rows:
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        idx = (at.astimezone(timezone.utc).date() - start.date()).days
        if 0 <= idx <= 6:
            cell = days.setdefault(key, [[0, 0] for _ in range(7)])[idx]
            cell[0] += 1 if ok else 0
            cell[1] += 1
    return {k: [round(c[0] * 100 / c[1]) if c[1] else None for c in cells] for k, cells in days.items()}


def summary(db: Session) -> dict[str, dict]:
    """ソースごとの {last_at, last_ok, last_ok_at, last_error, last_status, last_count, last_ms, runs_7d}。最後の 1 行と、最後の成功の時刻。"""
    last_id = select(func.max(NewsFetchLog.id).label("i")).group_by(NewsFetchLog.source_key).subquery()
    out: dict[str, dict] = {}
    for r in db.scalars(select(NewsFetchLog).where(NewsFetchLog.id.in_(select(last_id.c.i)))):
        out[r.source_key] = {"last_at": _iso(r.at), "last_ok": r.ok, "last_error": r.error, "last_status": r.status, "last_count": r.count,
                             "last_ms": r.ms, "last_skipped": r.skipped, "last_ok_at": ""}
    for key, at in db.execute(select(NewsFetchLog.source_key, func.max(NewsFetchLog.at)).where(NewsFetchLog.ok.is_(True), NewsFetchLog.skipped == "").group_by(NewsFetchLog.source_key)):
        if key in out:
            out[key]["last_ok_at"] = _iso(at)
    return out


def article_totals(db: Session) -> dict[str, int]:
    """ソースごとの、これまでに入った記事の累計（news_items を source_key で数える）。"""
    return {k: n for k, n in db.execute(select(NewsItem.source_key, func.count(NewsItem.id)).group_by(NewsItem.source_key))}


def recent_articles(db: Session, key: str, limit: int = 10) -> list[dict]:
    rows = db.scalars(select(NewsItem).where(NewsItem.source_key == key).order_by(NewsItem.published_at.desc()).limit(limit))
    return [{"id": r.id, "title": r.title, "url": r.url, "published_at": _iso(r.published_at)} for r in rows]


def forget(db: Session, keys: list[str]) -> None:
    """ソースを設定から外したとき、履歴も消す（記事は残す）。"""
    if keys:
        db.execute(delete(NewsFetchLog).where(NewsFetchLog.source_key.in_(keys)))
        db.commit()
