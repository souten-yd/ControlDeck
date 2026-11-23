"""好み（おすすめ）: 興味の重みと、おすすめの点。

興味の重み（話題の語・ソースごと）:
- 開いた・like・star した記事の話題の語とソースは正、dislike・hide は負。新しい操作ほど重く、30 日で半減。
- チャットの会話の題（直近 30 日・**秘密の会話を除く**・その利用者のもの）から取った語を少し足す。
- 利用者が「外した」語（data_dir/news/muted.json）は 0 にする。
おすすめの点 = 新しさ（3 日で半減。日単位で減衰）× (1 + 興味)。興味 = (話題の重みの合計 + ソースの重み) / 4 を [-0.8, 4] に収める。
dislike した記事は点を 5 分の 1 にする（hide した記事は一覧に出さない）。
"""
from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import data_dir
from app.models import Conversation
from app.news.db import NewsAction, NewsItem
from app.news import prefs
from app.news.topics import extract_topics

HALF_LIFE_DAYS = 30.0
RECENCY_HALF_LIFE_DAYS = 3.0
ACTION_LOOKBACK_DAYS = 180
CHAT_LOOKBACK_DAYS = 30
CHAT_WEIGHT = 0.5
# 操作ごとの (話題への重み, ソースへの重み)
ACTION_WEIGHTS = {"open": (1.0, 0.5), "like": (3.0, 2.0), "star": (3.0, 2.0), "dislike": (-3.0, -4.0), "hide": (-2.0, -1.0)}
INTEREST_SCALE = 4.0
DISLIKED_FACTOR = 0.2
_UTC = timezone.utc
_muted_lock = threading.Lock()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=_UTC) if value.tzinfo is None else value


def _decay(then: datetime, now: datetime, half_life: float) -> float:
    age = max((now - _aware(then)).total_seconds() / 86400, 0.0)
    return 0.5 ** (age / half_life)


# ---- 外した語 ----

def _muted_path():
    return data_dir() / "news" / "muted.json"


def _muted_all() -> dict:
    try:
        raw = json.loads(_muted_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def muted_topics(user_id: int) -> list[str]:
    value = _muted_all().get(str(user_id))
    return [t for t in value if isinstance(t, str)] if isinstance(value, list) else []


def set_muted(user_id: int, topic: str, muted: bool) -> list[str]:
    topic = topic.strip()[:60]
    with _muted_lock:
        everything = _muted_all()
        current = [t for t in muted_topics(user_id) if t != topic]
        if muted and topic:
            current.append(topic)
        everything[str(user_id)] = current[-200:]
        path = _muted_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(everything, handle, ensure_ascii=False)
        os.replace(tmp, path)
    return everything[str(user_id)]


# ---- 重み ----

def interests(db: Session, user_id: int, now: datetime | None = None) -> dict:
    """{"topics": {語: 重み}, "sources": {source_key: 重み}, "source_names": {source_key: 表示名}, "chat_terms": 数}。"""
    now = now or datetime.now(_UTC)
    taste = prefs.taste(user_id)
    half_life = float(taste["half_life_days"])
    since = (now - timedelta(days=ACTION_LOOKBACK_DAYS)).replace(tzinfo=None)
    topics: dict[str, float] = defaultdict(float)
    sources: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    rows = db.execute(select(NewsAction.action, NewsAction.at, NewsItem.topics, NewsItem.source_key, NewsItem.source, NewsItem.kind)
                      .join(NewsItem, NewsItem.id == NewsAction.item_id)
                      .where(NewsAction.user_id == user_id, NewsAction.at >= since))
    for action, at, raw, source_key, source, kind in rows:
        weights = ACTION_WEIGHTS.get(action)
        if not weights or kind == "event":
            continue
        scale = _decay(at, now, half_life)
        try:
            for name in json.loads(raw or "[]"):
                if isinstance(name, str):
                    topics[name] += weights[0] * scale
        except ValueError:
            pass
        sources[source_key] += weights[1] * scale
        names[source_key] = source
    chats = 0
    # 秘密の会話は題も語も使わない（confidential_at が付いた会話は読まない）
    chat_since = (now - timedelta(days=CHAT_LOOKBACK_DAYS)).replace(tzinfo=None)
    chat_rows = []
    if taste["use_chat"]:
        from app.database import SessionLocal

        with SessionLocal() as main:      # チャットの題は本体の DB（ニュースの DB とは別のファイル）
            chat_rows = main.execute(select(Conversation.title, Conversation.updated_at).where(
                Conversation.owner_user_id == user_id, Conversation.confidential_at.is_(None), Conversation.updated_at >= chat_since)).all()
    for title, updated in chat_rows:
        if not title or title == "新しい会話":
            continue
        for word in extract_topics(title):
            topics[word] += CHAT_WEIGHT * _decay(updated, now, half_life)
            chats += 1
    for word in muted_topics(user_id):
        topics.pop(word, None)
    return {"topics": dict(topics), "sources": dict(sources), "source_names": names, "chat_terms": chats}


def summary(db: Session, user_id: int, now: datetime | None = None, top: int = 20) -> dict:
    """画面の「あなたの興味」。正の上位の語・ソースと、避けているソース、外した語。"""
    found = interests(db, user_id, now)
    ranked = sorted(((t, w) for t, w in found["topics"].items() if w > 0.05), key=lambda kv: (-kv[1], kv[0]))[:top]
    src = sorted(found["sources"].items(), key=lambda kv: (-kv[1], kv[0]))
    return {"topics": [{"topic": t, "weight": round(w, 2)} for t, w in ranked],
            "sources": [{"source_key": k, "source": found["source_names"].get(k, k), "weight": round(w, 2)} for k, w in src if w > 0.05][:8],
            "avoided_sources": [{"source_key": k, "source": found["source_names"].get(k, k), "weight": round(w, 2)} for k, w in reversed(src) if w < -0.05][:5],
            "muted": muted_topics(user_id), "chat_terms": found["chat_terms"]}


def score(item: NewsItem, found: dict, state: dict, now: datetime, strength: float = 0.5) -> float:
    """おすすめの点。found は interests() の結果、state はその人のこの記事への操作。
    strength（0〜1。設定の「おすすめの強さ」）は興味の効き方: 0.5 で従来どおり、0 で新しさだけ、1 で興味を 2 倍に効かせる。"""
    try:
        names = [t for t in json.loads(item.topics or "[]") if isinstance(t, str)]
    except ValueError:
        names = []
    raw = sum(found["topics"].get(t, 0.0) for t in names) + found["sources"].get(item.source_key, 0.0)
    interest = max(-0.8, min(raw / INTEREST_SCALE, 4.0)) * (strength * 2)
    value = _decay(item.published_at, now, RECENCY_HALF_LIFE_DAYS) * (1 + interest)
    return value * DISLIKED_FACTOR if state.get("dislike") else value


def rank(db: Session, user_id: int, rows: list[NewsItem], states: dict[int, dict], now: datetime) -> list[tuple[NewsItem, float]]:
    found = interests(db, user_id, now)
    strength = prefs.taste(user_id)["strength"]
    scored = [(row, score(row, found, states.get(row.id, {}), now, strength)) for row in rows]
    return sorted(scored, key=lambda pair: (-pair[1], -pair[0].id))
