"""ニュースの専門ページの API（/api/v1/news）。ログインした利用者。event はその人宛てだけ。設定の変更は settings.manage。"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.news.db import get_news_db
from app.models import User
from app.news import channels as channels_mod
from app.news import digest, home, imagegen, llm, prefs, presets, profile, rag, service, sources, store
from app.news import settings as news_settings
from app.news.db import NewsItem
from app.security.deps import get_current_user, require_permission

router = APIRouter(prefix="/news", tags=["news"])


class ActionBody(BaseModel):
    action: str = Field(pattern="^(open|like|dislike|hide|star)$")
    on: bool = True


class MuteBody(BaseModel):
    topic: str = Field(min_length=1, max_length=60)
    muted: bool = True


def _kinds(kind: str) -> list[str] | None:
    wanted = [k for k in (kind or "").split(",") if k]
    bad = [k for k in wanted if k not in sources.KINDS]
    if bad:
        raise HTTPException(status_code=422, detail=f"知らない種類です: {bad[0]}")
    return wanted or None


def _day(value: str) -> str:
    if value:
        try:
            store.day_range(value)
        except ValueError:
            raise HTTPException(status_code=422, detail="date は YYYY-MM-DD の形です") from None
    return value


def _channel(channel_id: str) -> dict | None:
    if not channel_id:
        return None
    found = next((c for c in channels_mod.load()["channels"] if c["id"] == channel_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="そのチャンネルはありません")
    return found


@router.get("/items")
def items(view: str = Query("recommended", pattern="^(recommended|latest|starred)$"), kind: str = "", source: str = "", topic: str = "", channel: str = "",
          q: str = Query("", max_length=200), date: str = "", before: str = "", limit: int = Query(store.PAGE_DEFAULT, ge=1, le=store.PAGE_MAX),
          user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    result = store.list_items(db, user.id, view=view, kinds=_kinds(kind), source=source[:200], topic=topic[:60], q=q, day=_day(date),
                              before=before, limit=limit, channel=_channel(channel))
    # ホームのカードが色の印を付けられるよう、チャンネルの指定が無い一覧にも所属のチャンネルを付ける
    channels = channels_mod.numbered(channels_mod.load()["channels"])
    for item in result["items"]:
        if "channel" not in item:
            item["channel"] = channels_mod.first_channel(channels, SimpleNamespace(kind=item["kind"], source_key=item.get("source_key", ""), title=item["title"], summary=item.get("summary") or ""))
    return result


@router.get("/archive")
def archive(kind: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    return store.archive_days(db, user.id, kinds=_kinds(kind))


@router.get("/trends")
def trends(days: int = Query(14, ge=1, le=90), user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    return store.trends(db, user.id, days)


@router.get("/profile")
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    return profile.summary(db, user.id)


@router.put("/profile/mute")
def mute_topic(body: MuteBody, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """「あなたの興味」から語を外す（muted=false で戻す）。"""
    profile.set_muted(user.id, body.topic, body.muted)
    return profile.summary(db, user.id)


@router.post("/items/{item_id}/action")
def item_action(item_id: int, body: ActionBody, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    state = store.set_action(db, user.id, item_id, body.action, body.on)
    if state is None:
        raise HTTPException(status_code=404, detail="記事が見つかりません")
    return {"state": state}


@router.post("/refresh")
async def refresh(user: User = Depends(get_current_user)):
    return await service.refresh()


@router.get("/sources")
def get_sources(user: User = Depends(get_current_user)):
    from app.workflows import research_keys

    return {"config": sources.load_config(), "kinds": sources.KIND_LABELS, "reddit_key": research_keys.reddit() is not None,
            "limit": sources.MAX_PER_LIST, **service.status()}


@router.get("/presets")
def get_presets(user: User = Depends(get_current_user)):
    """設定 → ニュース → ソースの「プリセット」。分野ごとの無料のフィード・Release・フォーラム（確かめた日つき）。"""
    return presets.listing()


@router.put("/sources")
def put_sources(body: dict, user: User = Depends(require_permission("settings.manage"))):
    try:
        return {"config": sources.save_config(body)}
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/home")
def get_home(user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """放送局の画面が 1 回で読み込む内容（ON AIR・テロップ・NOW/TODAY/THIS WEEK・チャンネルごと・興味・数字）。"""
    return home.build(db, user.id)


@router.get("/channels")
def get_channels(user: User = Depends(get_current_user)):
    from app.workflows import research_keys

    return {**channels_mod.load(), "kinds": sources.KIND_LABELS, "reddit_key": research_keys.reddit() is not None,
            "limits": {"channels": channels_mod.MAX_CHANNELS, "terms": channels_mod.MAX_TERMS, "palette": list(channels_mod.PALETTE)}}


_background: set[asyncio.Task] = set()


def changed_search_channels(old: dict[str, dict], new: list[dict]) -> list[str]:
    """検索をやり直すチャンネルの id: 検索が有効で、新しい・含める語・言語・検索の有無が変わったもの。"""
    out = []
    for c in new:
        if not c["search"]:
            continue
        before = old.get(c["id"])
        if before is None or any(c[k] != before[k] for k in ("include", "lang", "search")):
            out.append(c["id"])
    return out


@router.put("/channels")
async def put_channels(body: dict, user: User = Depends(require_permission("settings.manage"))):
    """チャンネルを保存する。語・言語・検索の有無が変わった（新しい）チャンネルは、その検索を裏でやり直す
    （古い語で取った記事は、所属の判定が新しい語で外すので、新しい語の記事で埋まる）。"""
    old = {c["id"]: c for c in channels_mod.load()["channels"]}
    try:
        saved = await asyncio.to_thread(channels_mod.save, body)
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    changed = changed_search_channels(old, saved["channels"])
    if changed and os.environ.get("CONTROL_DECK_NEWS_AUTO") != "0":      # 試験では外へ通信しない
        task = asyncio.create_task(service.refresh(force=True, channel_ids=changed))
        _background.add(task)
        task.add_done_callback(_background.discard)
    return saved


@router.get("/prefs")
def get_prefs(user: User = Depends(get_current_user)):
    return prefs.load(user.id)


@router.put("/prefs")
def put_prefs(body: dict, user: User = Depends(get_current_user)):
    """表示のデザインと好み。利用者ごと（その人自身の設定なので settings.manage は要らない）。"""
    try:
        saved = prefs.save(user.id, body)
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return saved


@router.get("/export")
def export(format: str = Query("json", pattern="^(json|csv)$"), channel: str = "", user: User = Depends(get_current_user),
           db: Session = Depends(get_news_db)):
    """アーカイブの書き出し（出来事は除く。チャンネルを指定すればその分だけ）。"""
    rows = home.export_rows(db, user.id, _channel(channel))
    if format == "csv":
        return Response(home.to_csv(rows), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="news-archive.csv"'})
    return Response(home.to_json(rows), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="news-archive.json"'})


# ---- 更新・LLM・RAG の設定 / まとめ / 意味で探す ----

@router.get("/settings")
def get_settings(user: User = Depends(get_current_user)):
    """更新のタイミング・LLM・RAG の設定。ホームのフィードの読み直しの間隔は誰の画面でも使うので、ログインした人なら読める。"""
    settings = news_settings.load()
    return {"settings": settings, "weekdays": list(news_settings.WEEKDAYS), "interval_minutes": channels_mod.load()["interval_minutes"],
            "imagegen_available": imagegen.available(settings["imagegen"]["tier"]), "imagegen_tiers": imagegen.tiers_info()}


@router.get("/images/{item_id}.webp")
def get_image(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """生成したイメージ画像（ログインした人だけ）。パスは記事の id（整数）だけで組み、その人に見える記事の画像だけ返す。"""
    item = db.get(NewsItem, item_id)
    if item is None or (item.user_id is not None and item.user_id != user.id) or not imagegen.is_generated_url(item.image_url):
        raise HTTPException(status_code=404, detail="画像がありません")
    path = imagegen.image_path(item_id)
    if path is None:
        raise HTTPException(status_code=404, detail="画像がありません")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.put("/settings")
async def put_settings(body: dict, user: User = Depends(require_permission("settings.manage"))):
    try:
        return {"settings": await asyncio.to_thread(news_settings.save, body)}
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


_free_cache: dict = {"at": 0.0, "ids": frozenset()}


async def _free_model_ids() -> frozenset[str]:
    """OpenRouter が価格 0 と伝えているモデルの id（:free で終わらないものも拾う）。1 時間覚える。取れなければ空（:free の印だけで判定）。"""
    import time

    from app.models_mgmt import cloud

    if time.monotonic() - _free_cache["at"] < 3600:
        return _free_cache["ids"]
    _free_cache["at"] = time.monotonic()
    try:
        _free_cache["ids"] = frozenset(m["id"] for m in await cloud.list_remote_models("openrouter") if m.get("free"))
    except Exception:  # noqa: BLE001 - キーが無い・つながらない。:free の印だけで足りる
        _free_cache["ids"] = frozenset()
    return _free_cache["ids"]


async def _local_models(user: User) -> list[dict]:
    """ローカルのモデルの選択肢（既存の /workflows/llm-endpoints の、ControlDeck 自身のゲートウェイ）。"""
    from app.workflows.router import llm_endpoints

    rows: list[dict] = [{"id": "auto", "name": "auto（Models で選んだ既定）"}]
    for endpoint in await llm_endpoints(user):
        if not endpoint.get("managed"):
            continue
        labels = endpoint.get("model_labels") or {}
        for model in endpoint.get("models", []):
            if model != "auto" and not str(model).startswith("cloud:") and all(r["id"] != model for r in rows):
                rows.append({"id": model, "name": labels.get(model, model)})
    return rows


@router.get("/llm-options")
async def llm_options(user: User = Depends(get_current_user)):
    """まとめ・一言要約に選べる LLM。クラウドは無料のモデルが先で「無料」の印、有料は paid。許可が無ければ egress=false。"""
    try:
        local = await _local_models(user)
    except Exception:  # noqa: BLE001 - 稼働中のモデルを調べられなくても、auto は選べる
        local = [{"id": "auto", "name": "auto（Models で選んだ既定）"}]
    return llm.options(local, await _free_model_ids())


@router.get("/digests")
def get_digests(user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """/news の上のカード（日刊・週刊の最新）と、アーカイブ用の一覧。"""
    return {"latest": digest.latest(db), "digests": digest.list_digests(db)}


@router.get("/digests/{digest_id}")
def get_digest(digest_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    found = digest.get_digest(db, digest_id)
    if found is None:
        raise HTTPException(status_code=404, detail="まとめが見つかりません")
    return found


class DigestBody(BaseModel):
    period: str = Field(pattern="^(daily|weekly)$")


@router.post("/digests/run")
async def run_digest(body: DigestBody, user: User = Depends(require_permission("settings.manage"))):
    """「今すぐまとめを作る」。今日（週刊は直近の予定の日）の分を作り直す。"""
    from datetime import datetime

    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    if body.period == "weekly":      # 直近の予定の日（今日が曜日なら今日）
        weekly = {**news_settings.load()["digest_weekly"], "enabled": True, "time": "00:00"}
        day = next(d for p, d in news_settings.due_digests({**news_settings.DEFAULTS, "digest_daily": {"enabled": False, "time": "00:00"}, "digest_weekly": weekly}, now) if p == "weekly")
    return await digest.generate(body.period, day)


@router.get("/semantic")
async def semantic(q: str = Query(min_length=1, max_length=200), limit: int = Query(24, ge=1, le=60),
                   user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """意味で探す（RAG の分野 news）。全文検索と並べて使う。ニュースの DB から消えた古い記事も、RAG に残っていれば出る（archived=true）。"""
    hits = await rag.search(q.strip(), limit)
    return {"items": await asyncio.to_thread(_semantic_items, db, user.id, hits), "semantic": bool(hits and hits[0].get("semantic"))}


def _semantic_items(db: Session, user_id: int, hits: list[dict]) -> list[dict]:
    urls = [h["url"] for h in hits]
    rows = {r.url: r for r in db.scalars(select(NewsItem).where(NewsItem.url.in_(urls or [""]), store.visible(user_id)))}
    st = store.states(db, user_id, [r.id for r in rows.values()])
    out = []
    for hit in hits:
        row = rows.get(hit["url"])
        if row is not None:
            out.append(dict(store.present(row, st[row.id], hit["score"]), snippet=hit["text"][:240]))
            continue
        meta = hit["meta"]
        url = sources.safe_url(hit["url"])
        if url is None or meta.get("kind") == "event":
            continue
        out.append({"id": 0, "kind": meta.get("kind", "news"), "source": hit["source"], "source_key": meta.get("source_key", ""), "title": hit["title"],
                    "url": url, "summary": "", "image_url": None, "published_at": meta.get("published_at", ""), "topics": meta.get("topics", []),
                    "state": {a: False for a in store.ACTIONS}, "score": round(hit["score"], 4), "archived": True, "snippet": hit["text"][:240]})
    return out
