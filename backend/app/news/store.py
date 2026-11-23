"""ニュースの保存と検索（記事の更新・保存期間・出来事の記録・一覧・アーカイブ・話題の動き）。

- 同じ url は 1 行（題・抜粋・画像・話題だけ更新。published_at は最初の値を保つ＝アーカイブの並びが動かない）。
- 保存期間: アーカイブなので消さない。ただし MAX_ITEMS を超えたら古い順に消す（star された記事は残す）。
- 全文検索は SQLite なら FTS5（trigram: 3 文字以上）、それ以外・短い語・FTS5 が無いときは LIKE。
- 日の区切りは、サーバーの地域の時刻（日本なら日本時間の 0 時）。DB には UTC で持つ。
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, delete, exists, func, not_, or_, select, text
from sqlalchemy.orm import Session

from app.news import channels as channels_mod
from app.news import db as news_db
from app.news.db import NewsAction, NewsItem
from app.news import imagegen, prefs, profile
from app.news.sources import HIDDEN_SOURCE_PREFIXES, plain, safe_url
from app.news.topics import extract_topics

logger = logging.getLogger("control_deck.news.store")

MAX_ITEMS = 50_000
ACTIONS = ("open", "like", "dislike", "hide", "star")
RECOMMEND_WINDOW_DAYS = 21
RECOMMEND_CANDIDATES = 600
PAGE_DEFAULT, PAGE_MAX = 24, 60
_UTC = timezone.utc


def utc_naive(value: datetime) -> datetime:
    return (value.astimezone(_UTC) if value.tzinfo else value).replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=_UTC) if value.tzinfo is None else value.astimezone(_UTC)


def iso(value: datetime) -> str:
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_tz():
    return datetime.now().astimezone().tzinfo


def local_day(value: datetime) -> str:
    return as_utc(value).astimezone(local_tz()).strftime("%Y-%m-%d")


def day_range(day: str) -> tuple[datetime, datetime]:
    """YYYY-MM-DD（地域の日）を [開始, 終了) の UTC にする。形が違えば ValueError。"""
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=local_tz())
    return utc_naive(start), utc_naive(start + timedelta(days=1))


# ---- 保存 ----

def topics_of(item: NewsItem) -> list[str]:
    try:
        value = json.loads(item.topics or "[]")
    except ValueError:
        return []
    return [t for t in value if isinstance(t, str)] if isinstance(value, list) else []


def item_dict(item: NewsItem) -> dict:
    """RAG・LLM へ渡す記事の形（出来事は呼ぶ側で除く）。"""
    return {"id": item.id, "kind": item.kind, "source": item.source, "source_key": item.source_key, "title": item.title, "url": item.url,
            "summary": item.summary or "", "summary_ja": item.summary_ja or "", "published_at": iso(item.published_at), "topics": topics_of(item),
            "gist": item.gist or ""}


def upsert_items(db: Session, items: list[dict]) -> dict:
    """記事を保存する。同じ url は更新だけ。{"new": 件数, "updated": 件数, "changed": 新しい・題か抜粋が変わった記事の dict}。"""
    now = datetime.now(_UTC)
    by_url = {item["url"]: item for item in items}   # 同じ回の重複は後勝ち
    existing: dict[str, NewsItem] = {}
    urls = list(by_url)
    for i in range(0, len(urls), 400):
        for row in db.execute(select(NewsItem).where(NewsItem.url.in_(urls[i:i + 400]))).scalars():
            existing[row.url] = row
    new = updated = 0
    touched: list[NewsItem] = []
    for url, data in by_url.items():
        topics = json.dumps(extract_topics(data["title"], data["summary"], data.get("extra_topics", ())), ensure_ascii=False)
        row = existing.get(url)
        if row is None:
            row = NewsItem(kind=data["kind"], source=data["source"], source_key=data["source_key"], title=data["title"], url=url,
                           summary=data["summary"], image_url=data.get("image_url"), published_at=data["published_at"],
                           fetched_at=now, topics=topics, user_id=data.get("user_id"))
            db.add(row)
            touched.append(row)
            new += 1
        else:
            if (row.title, row.summary) != (data["title"], data["summary"]):
                touched.append(row)
            row.kind, row.source, row.source_key = data["kind"], data["source"], data["source_key"]
            if row.gist:      # LLM が付けた話題の語は、取り直しで消さない
                topics = json.dumps(list(dict.fromkeys(topics_of(row) + json.loads(topics))), ensure_ascii=False)
            if row.summary != data["summary"]:
                row.summary_ja, row.prep_tries = "", 0      # 抜粋が変わったら、古い訳は捨てて下ごしらえし直す
            row.title, row.summary, row.topics, row.fetched_at = data["title"], data["summary"], topics, now
            row.image_url = data.get("image_url") or row.image_url
            updated += 1
    db.commit()
    return {"new": new, "updated": updated, "changed": [item_dict(r) for r in touched if r.kind != "event"],
            "new_ids": [r.id for r in touched if r.id is not None and r.url not in existing]}


def prune(db: Session, limit: int | None = None, removed_urls: list[str] | None = None) -> int:
    """残す件数（設定のアーカイブ。既定 MAX_ITEMS）を超えた分を古い順に消す（star は残す）。消した件数。
    removed_urls を渡すと、消した記事の URL をそこへ足す（RAG から一緒に消す設定のとき）。"""
    cap = MAX_ITEMS if limit is None else limit
    total = db.scalar(select(func.count(NewsItem.id))) or 0
    if total <= cap:
        return 0
    starred = select(NewsAction.item_id).where(NewsAction.action == "star")
    ids = list(db.scalars(select(NewsItem.id).where(NewsItem.id.not_in(starred))
                          .order_by(NewsItem.published_at.asc(), NewsItem.id.asc()).limit(total - cap)))
    if removed_urls is not None:
        for i in range(0, len(ids), 500):
            removed_urls += list(db.scalars(select(NewsItem.url).where(NewsItem.id.in_(ids[i:i + 500]), NewsItem.kind != "event")))
    for i in range(0, len(ids), 500):
        db.execute(delete(NewsItem).where(NewsItem.id.in_(ids[i:i + 500])))
    db.commit()
    imagegen.delete_images(ids)     # 生成した画像も一緒に消す
    return len(ids)


def _internal_path(url: str) -> str:
    return url if url.startswith("/") and not url.startswith("//") else "/"


def record_event(kind: str, *, title: str, body: str = "", url: str = "/", user_ids: list[int] | None = None,
                 permission: str | None = None) -> int:
    """通知の入口（alerts/events.notify_event）から呼ぶ。種類の有効・お休みに関係なく、宛先の人ごとに 1 行を残す。
    title・body は呼び出し側が伏せた後の文（秘密の会話は「秘密の会話」だけ）。記録した行数。"""
    from app.alerts.events import KIND_BY_ID
    from app.database import SessionLocal
    from app.models import User
    from app.security.deps import user_permissions

    with SessionLocal() as main:      # 利用者は本体の DB（ニュースの DB には利用者の表が無い）
        if user_ids is None:
            users = main.scalars(select(User).where(User.is_active.is_(True))).all()
            ids = [u.id for u in users if permission and permission in user_permissions(u)]
        else:
            ids = [i for i in dict.fromkeys(user_ids) if main.get(User, i) is not None]
    if not ids:
        return 0
    with news_db.session() as db:
        path = _internal_path(url or "/")
        label = KIND_BY_ID[kind].label
        stamp = time.time_ns()
        now = datetime.now(_UTC)
        for uid in ids:
            db.add(NewsItem(kind="event", source="ControlDeck", source_key="event", title=plain(title, 300) or label,
                            url=f"{path}{'&' if '?' in path else '?'}news-event={uid}-{stamp}", summary=plain(body),
                            published_at=now, fetched_at=now, topics=json.dumps([label], ensure_ascii=False), user_id=uid))
        db.commit()
        return len(ids)


# ---- 一覧 ----

def visible(user_id: int):
    """その人に見える記事（全員宛て＋自分宛ての出来事）。自分のリポジトリ（HIDDEN_SOURCE_PREFIXES）の記事は、貯まっていても出さない。"""
    return and_(or_(NewsItem.user_id.is_(None), NewsItem.user_id == user_id),
                *[not_(NewsItem.source_key.like(f"{_like_escape(p)}%", escape="\\")) for p in HIDDEN_SOURCE_PREFIXES])


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_clause(db: Session, q: str):
    """全文検索の条件。語はすべて含む（AND）。3 文字以上の語だけなら FTS5、それ以外は LIKE。"""
    terms = [t for t in q.split() if t][:8]
    if not terms:
        return None
    if db.get_bind().dialect.name == "sqlite" and all(len(t) >= 3 for t in terms) and db.execute(
            text("SELECT 1 FROM sqlite_master WHERE name = 'news_items_fts'")).first():
        match = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
        return NewsItem.id.in_(select(text("rowid")).select_from(text("news_items_fts")).where(text("news_items_fts MATCH :m")).params(m=match))
    return and_(*[or_(NewsItem.title.like(f"%{_like_escape(t)}%", escape="\\"), NewsItem.summary.like(f"%{_like_escape(t)}%", escape="\\"),
                      NewsItem.source.like(f"%{_like_escape(t)}%", escape="\\")) for t in terms])


def action_exists(user_id: int, action: str):
    return exists().where(NewsAction.item_id == NewsItem.id, NewsAction.user_id == user_id, NewsAction.action == action)


def mute_conds(user_id: int) -> list:
    """設定のミュートの語（題・抜粋）とソース（出どころの名前）に合う記事を除く条件。"""
    taste = prefs.taste(user_id)
    conds = []
    for term in taste["mute_terms"]:
        like = f"%{_like_escape(term)}%"
        conds.append(not_(or_(NewsItem.title.like(like, escape="\\"), NewsItem.summary.like(like, escape="\\"))))
    if taste["mute_sources"]:
        conds.append(NewsItem.source.not_in(taste["mute_sources"]))
    return conds


def filters(db: Session, user_id: int, *, kinds: list[str] | None = None, source: str = "", topic: str = "", q: str = "",
            day: str = "", mute: bool = False) -> list:
    conds = [visible(user_id)] + (mute_conds(user_id) if mute else [])
    if kinds:
        conds.append(NewsItem.kind.in_(kinds))
    if source:
        conds.append(or_(NewsItem.source_key == source, NewsItem.source == source))
    if topic:
        conds.append(NewsItem.topics.like(f'%"{_like_escape(topic)}"%', escape="\\"))
    clause = search_clause(db, q.strip()) if q.strip() else None
    if clause is not None:
        conds.append(clause)
    if day:
        start, end = day_range(day)
        conds += [NewsItem.published_at >= start, NewsItem.published_at < end]
    return conds


def states(db: Session, user_id: int, ids: list[int]) -> dict[int, dict]:
    out = {i: {a: False for a in ACTIONS} for i in ids}
    if ids:
        for item_id, action in db.execute(select(NewsAction.item_id, NewsAction.action).where(
                NewsAction.user_id == user_id, NewsAction.item_id.in_(ids))):
            if action in ACTIONS:
                out[item_id][action] = True
    return out


def present(item: NewsItem, state: dict, score: float | None = None) -> dict:
    url = safe_url(item.url) or (_internal_path(item.url) if item.kind == "event" else None)
    row = {"id": item.id, "kind": item.kind, "source": item.source, "source_key": item.source_key, "title": item.title,
           "url": url, "summary": item.summary_ja or item.summary,
           "image_url": safe_url(item.image_url) or (item.image_url if imagegen.is_generated_url(item.image_url) else None),
           "image_ai": imagegen.is_generated_url(item.image_url), "published_at": iso(item.published_at),
           "topics": topics_of(item), "state": state}
    if item.summary_ja:      # 訳した概要を summary に出し、元の抜粋は開いたとき見られるよう別に持つ
        row["translated"], row["summary_original"] = True, item.summary
    if score is not None:
        row["score"] = round(score, 4)
    return row


def list_items(db: Session, user_id: int, *, view: str = "latest", kinds: list[str] | None = None, source: str = "", topic: str = "",
               q: str = "", day: str = "", before: str = "", limit: int = PAGE_DEFAULT, now: datetime | None = None,
               channel: dict | None = None) -> dict:
    """{"items": [...], "next": 次のページの印 | None}。view は recommended・latest・starred。
    おすすめ・新着は、隠した記事を除く（検索・日付で探すときは隠したものも出す）。"""
    limit = max(1, min(int(limit or PAGE_DEFAULT), PAGE_MAX))
    now = now or datetime.now(_UTC)
    searching = bool(q.strip() or day)
    conds = filters(db, user_id, kinds=kinds, source=source, topic=topic, q=q, day=day, mute=not searching)
    if view == "starred":
        conds.append(action_exists(user_id, "star"))
    elif not searching:
        conds.append(not_(action_exists(user_id, "hide")))
    if channel is not None:
        return _channel_page(db, user_id, channel, conds, view=view, before=before, limit=limit, now=now)
    if view == "recommended" and not searching:
        since = utc_naive(now - timedelta(days=RECOMMEND_WINDOW_DAYS))
        rows = db.scalars(select(NewsItem).where(*conds, NewsItem.published_at >= since)
                          .order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(RECOMMEND_CANDIDATES)).all()
        st = states(db, user_id, [r.id for r in rows])
        ranked = profile.rank(db, user_id, rows, st, now)
        offset = int(before[2:]) if before.startswith("o:") and before[2:].isdigit() else 0
        page = ranked[offset:offset + limit]
        return {"items": [present(r, st[r.id], score) for r, score in page],
                "next": f"o:{offset + limit}" if offset + limit < len(ranked) else None}
    if before:
        stamp, _, last = before.partition("|")
        try:
            cut = utc_naive(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
            conds.append(or_(NewsItem.published_at < cut, and_(NewsItem.published_at == cut, NewsItem.id < int(last or 0))))
        except ValueError:
            pass
    rows = db.scalars(select(NewsItem).where(*conds).order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    st = states(db, user_id, [r.id for r in rows])
    return {"items": [present(r, st[r.id]) for r in rows],
            "next": f"{iso(rows[-1].published_at)}|{rows[-1].id}" if more and rows else None}


CHANNEL_CANDIDATES = 1500


def _channel_page(db: Session, user_id: int, channel: dict, conds: list, *, view: str, before: str, limit: int, now: datetime) -> dict:
    """チャンネルの記事。語の判定は Python で行うので、新しい順の候補（最大 1500 件）から選ぶ。ページは o:<位置>。"""
    since = utc_naive(now - timedelta(days=RECOMMEND_WINDOW_DAYS * 2))
    rows = [r for r in db.scalars(select(NewsItem).where(*conds, NewsItem.published_at >= since)
                                  .order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(CHANNEL_CANDIDATES))
            if channels_mod.matches(channel, kind=r.kind, source_key=r.source_key, title=r.title, summary=r.summary or "")]
    st = states(db, user_id, [r.id for r in rows])
    scored = profile.rank(db, user_id, rows, st, now) if view == "recommended" else [(r, None) for r in rows]
    offset = int(before[2:]) if before.startswith("o:") and before[2:].isdigit() else 0
    page = scored[offset:offset + limit]
    return {"items": [dict(present(r, st[r.id], score), channel=channel["id"]) for r, score in page],
            "next": f"o:{offset + limit}" if offset + limit < len(scored) else None}


def set_action(db: Session, user_id: int, item_id: int, action: str, on: bool = True) -> dict | None:
    """操作を記録・取り消す。like と dislike は同時に付かない。open は取り消さない。記事が無い・見えないなら None。"""
    item = db.get(NewsItem, item_id)
    if item is None or (item.user_id is not None and item.user_id != user_id):
        return None
    rows = {a.action: a for a in db.scalars(select(NewsAction).where(NewsAction.user_id == user_id, NewsAction.item_id == item_id))}
    if on or action == "open":
        opposite = {"like": "dislike", "dislike": "like"}.get(action)
        if opposite in rows:
            db.delete(rows.pop(opposite))
        if action in rows:
            rows[action].at = datetime.now(_UTC)
        else:
            db.add(NewsAction(user_id=user_id, item_id=item_id, action=action, at=datetime.now(_UTC)))
    elif action in rows:
        db.delete(rows[action])
    db.commit()
    return states(db, user_id, [item_id])[item_id]


# ---- アーカイブ・話題の動き ----

def archive_days(db: Session, user_id: int, *, kinds: list[str] | None = None) -> dict:
    """日ごとの件数（新しい日が先）。"""
    conds = [visible(user_id)] + ([NewsItem.kind.in_(kinds)] if kinds else [])
    counts: Counter[str] = Counter(local_day(p) for (p,) in db.execute(select(NewsItem.published_at).where(*conds)))
    starred = db.scalar(select(func.count(NewsAction.id)).where(NewsAction.user_id == user_id, NewsAction.action == "star")) or 0
    return {"days": [{"date": d, "count": counts[d]} for d in sorted(counts, reverse=True)], "total": sum(counts.values()), "starred": starred}


def trends(db: Session, user_id: int, days: int = 14, now: datetime | None = None, top: int = 8) -> dict:
    """話題の語ごとの日別件数（上位 top 語）と、ソース別の件数。出来事は数えない。"""
    days = max(1, min(int(days or 14), 90))
    today = (now or datetime.now(_UTC)).astimezone(local_tz()).date()
    labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    start, _ = day_range(labels[0])
    per_topic: dict[str, Counter] = defaultdict(Counter)
    sources: Counter[str] = Counter()
    rows = db.execute(select(NewsItem.published_at, NewsItem.topics, NewsItem.source).where(
        visible(user_id), NewsItem.kind != "event", NewsItem.published_at >= start))
    for published, raw, source in rows:
        day = local_day(published)
        sources[source] += 1
        try:
            names = json.loads(raw or "[]")
        except ValueError:
            names = []
        for name in names:
            if isinstance(name, str):
                per_topic[name][day] += 1
    best = sorted(per_topic, key=lambda t: (-sum(per_topic[t].values()), t))[:top]
    return {"days": labels,
            "topics": [{"topic": t, "counts": [per_topic[t][d] for d in labels], "total": sum(per_topic[t].values())} for t in best],
            "sources": [{"source": s, "count": c} for s, c in sources.most_common(10)]}
