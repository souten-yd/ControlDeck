"""放送局の画面が 1 回で読み込む内容（GET /news/home）と、書き出し（JSON・CSV）。

ON AIR（おすすめの 1 位）・テロップ（新しい見出し）・NOW / TODAY / THIS WEEK・チャンネルごとの上位・興味・話題の動き・数字。
おすすめの順位は 1 回だけ計算し、各区画はそれを切り分ける（同じ記事を NOW と TODAY に重ねない）。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, not_, select
from sqlalchemy.orm import Session

from app.news import channels as ch
from app.news.db import NewsAction, NewsItem
from app.news import profile, store

_UTC = timezone.utc
CANDIDATES = 800
PER_BUCKET = 3
TICKER_ITEMS = 14
BUCKETS = (("now", timedelta(hours=6)), ("today", timedelta(hours=24)), ("week", timedelta(days=7)))


def _tag(rows_scored: list[tuple[NewsItem, float]], states: dict, channels: list[dict]) -> list[dict]:
    return [dict(store.present(r, states[r.id], s), channel=ch.first_channel(channels, r)) for r, s in rows_scored]


def build(db: Session, user_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(_UTC)
    cfg = ch.load()
    channels = ch.numbered(cfg["channels"])
    conds = store.filters(db, user_id, mute=True)
    conds.append(not_(store.action_exists(user_id, "hide")))
    conds.append(NewsItem.kind != "event")
    since = store.utc_naive(now - timedelta(days=store.RECOMMEND_WINDOW_DAYS))
    rows = db.scalars(select(NewsItem).where(*conds, NewsItem.published_at >= since)
                      .order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(CANDIDATES)).all()
    st = store.states(db, user_id, [r.id for r in rows])
    ranked = profile.rank(db, user_id, rows, st, now)

    # ON AIR は、どれかのチャンネルに合う記事の中から選ぶ（合う記事が 1 件も無いときだけ、全体の 1 位）
    top = next(((r, s) for r, s in ranked if profile.fits_channel(r, channels)), None) or (ranked[0] if ranked else None)
    onair = _tag([top] if top else [], st, channels)
    taken = {top[0].id} if top else set()
    buckets: dict[str, list[dict]] = {}
    for name, span in BUCKETS:
        edge = store.utc_naive(now - span)
        pick = [(r, s) for r, s in ranked if r.id not in taken and r.published_at >= edge][:PER_BUCKET]
        taken |= {r.id for r, _ in pick}
        buckets[name] = _tag(pick, st, channels)

    sections = []
    for channel in channels:
        mine = [(r, s) for r, s in ranked if ch.matches(channel, kind=r.kind, source_key=r.source_key, title=r.title, summary=r.summary or "")]
        week_edge = store.utc_naive(now - timedelta(days=7))
        sections.append({"id": channel["id"], "name": channel["name"], "color": channel["color"], "number": channel["number"],
                         "total": len(mine), "week": sum(1 for r, _ in mine if r.published_at >= week_edge),
                         "items": [dict(store.present(r, st[r.id], s), channel=channel["id"]) for r, s in mine[:channel["limit"]]]})

    ticker = [{"id": r.id, "title": r.title, "source": r.source, "url": store.present(r, st[r.id])["url"], "published_at": store.iso(r.published_at)}
              for r in rows[:TICKER_ITEMS]]

    today_start, _ = store.day_range(now.astimezone(store.local_tz()).strftime("%Y-%m-%d"))
    today_count = db.scalar(select(func.count(NewsItem.id)).where(store.visible(user_id), NewsItem.kind != "event", NewsItem.published_at >= today_start)) or 0
    opened = db.execute(select(NewsAction.at, NewsItem).join(NewsItem, NewsItem.id == NewsAction.item_id).where(
        NewsAction.user_id == user_id, NewsAction.action == "open", NewsAction.at >= store.utc_naive(now - timedelta(days=30)), NewsItem.kind != "event")).all()
    read_today = len({item.id for at, item in opened if at >= today_start})
    per_channel: dict[str, int] = {}
    for _, item in opened:
        cid = ch.first_channel(channels, item)
        if cid:
            per_channel[cid] = per_channel.get(cid, 0) + 1
    top = max(per_channel, key=lambda k: per_channel[k]) if per_channel else None
    top_channel = next((c for c in channels if c["id"] == top), None)
    interest = profile.summary(db, user_id, now)
    trend = store.trends(db, user_id, 14, now)
    return {"channels": [{"id": c["id"], "name": c["name"], "color": c["color"], "number": c["number"]} for c in channels],
            "onair": onair[0] if onair else None, "ticker": ticker, "now": buckets["now"], "today": buckets["today"], "week": buckets["week"],
            "sections": sections, "interest": interest, "trends": trend,
            "numbers": {"today": today_count, "read_today": read_today, "read_30d": len({i.id for _, i in opened}),
                        "top_channel": {"id": top_channel["id"], "name": top_channel["name"], "color": top_channel["color"], "reads": per_channel[top]} if top_channel else None,
                        "top_topic": (trend["topics"][0]["topic"] if trend["topics"] else None),
                        "top_topic_count": (trend["topics"][0]["total"] if trend["topics"] else 0)},
            "generated_at": store.iso(now)}


# ---- 書き出し ----

EXPORT_FIELDS = ("id", "published_at", "kind", "source", "title", "url", "summary", "topics", "starred")


def _csv_cell(value: object) -> str:
    """表計算ソフトで開いたとき式として実行されないよう、= + - @ で始まる文は ' を前に付ける。"""
    text = str(value if value is not None else "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_rows(db: Session, user_id: int, channel: dict | None = None, limit: int = 100_000) -> list[dict]:
    conds = [store.visible(user_id), NewsItem.kind != "event"]
    rows = db.scalars(select(NewsItem).where(*conds).order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(limit)).all()
    if channel is not None:
        rows = [r for r in rows if ch.matches(channel, kind=r.kind, source_key=r.source_key, title=r.title, summary=r.summary or "")]
    starred = set(db.scalars(select(NewsAction.item_id).where(NewsAction.user_id == user_id, NewsAction.action == "star")))
    return [{"id": r.id, "published_at": store.iso(r.published_at), "kind": r.kind, "source": r.source, "title": r.title,
             "url": store.present(r, {})["url"] or "", "summary": r.summary or "", "topics": store.topics_of(r), "starred": r.id in starred} for r in rows]


def to_json(rows: list[dict]) -> str:
    return json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False, indent=1)


def to_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_FIELDS)
    for row in rows:
        writer.writerow([_csv_cell("、".join(row[f]) if f == "topics" else row[f]) for f in EXPORT_FIELDS])
    return "﻿" + out.getvalue()      # Excel が UTF-8 と分かるように BOM を付ける
