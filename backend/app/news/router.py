"""ニュースの専門ページの API（/api/v1/news）。ログインした利用者。event はその人宛てだけ。設定の変更は settings.manage。"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.news.db import get_news_db
from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.news import channels as channels_mod
from app.news import hub_jobs, hub_link, hub_sync
from app.news import feedgen, fetchlog, probe, sourcemgr, cpu_llm, digest, home, imagegen, llm, prefs, prep, presets, profile, rag, reader, service, sources, store
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


def _reader_item(item_id: int, user: User, db: Session) -> NewsItem:
    item = db.get(NewsItem, item_id)
    if item is None or (item.user_id is not None and item.user_id != user.id) or item.kind == "event":
        raise HTTPException(status_code=404, detail="記事が見つかりません")
    if item.hub_id is not None and not news_settings.load()["hub"]["features"]["reader"]:
        raise HTTPException(status_code=409, detail="ハブから受け取った記事のリーダーは、設定で切になっています（ニュースの設定 → ニュースハブ）")
    return item


async def _reader_view(item: NewsItem) -> dict:
    row = (await asyncio.to_thread(reader._load, item.id))[1]  # noqa: SLF001
    if reader.needs_job(row):
        reader.ensure_job(item.id)
    return reader.view(item, row)


@router.get("/items/{item_id}/reader")
async def get_reader(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """リーダー（本文・要約・訳）。未取得なら取り出しを裏で始め、途中の状態をそのまま返す（画面が読み直す）。"""
    return await _reader_view(_reader_item(item_id, user, db))


@router.post("/items/{item_id}/reader/more")
async def reader_more(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """続きを訳す。"""
    item = _reader_item(item_id, user, db)
    if not await asyncio.to_thread(reader.more, item_id):
        raise HTTPException(status_code=409, detail="本文がまだ取り出せていません")
    return await _reader_view(item)


@router.post("/items/{item_id}/reader/retry")
async def reader_retry(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """取り出せなかった記事の取り直し、止まった要約・訳の続き。"""
    item = _reader_item(item_id, user, db)
    await asyncio.to_thread(reader.retry, item_id)
    return await _reader_view(item)


@router.post("/refresh")
async def refresh(user: User = Depends(get_current_user)):
    if hub_sync.effective_source() == "hub":      # 出どころがハブのあいだは、自分では集めず、ハブの続きを取る
        return {"started": True, "reason": "", "hub": True, **{k: v for k, v in (await hub_sync.sync()).items() if k in ("ok", "counts", "error", "skipped")}}
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


class DiscoverBody(BaseModel):
    url: str = Field(min_length=8, max_length=800)


class CheckBody(BaseModel):
    kind: str = Field(pattern="^(rss|page|github|codeberg|discourse|reddit|hackernews|huggingface)$")
    value: str = Field(default="", max_length=800)
    name: str = Field(default="", max_length=60)


@router.post("/sources/discover")
async def discover_site(body: DiscoverBody, user: User = Depends(require_permission("settings.manage"))):
    """「サイトを追加」: URL が RSS ならそのまま、HTML なら RSS の候補（link rel・よくある場所）と、記事の一覧の巡回の可否を返す。
    外への取得は既存の安全な取得（内部アドレスの遮断・大きさと時間の上限）。保存はしない。"""
    return await probe.discover(body.url.strip())


@router.post("/sources/check")
async def check_source(body: CheckBody, user: User = Depends(require_permission("settings.manage"))):
    """通信確認: そのソースを今この場で 1 回取り、件数・最新の見出し 3 件・かかった時間・HTTP の状態・失敗の理由を返す。保存はしない。"""
    return await probe.check(body.kind, body.value.strip(), body.name.strip())


class GenerateBody(BaseModel):
    type: str = Field(pattern="^(keyword|github|steam|arxiv)$")
    keyword: str = Field(default="", max_length=200)
    lang: str = Field(default="ja", max_length=4)
    target: str = Field(default="", max_length=300)
    what: str = Field(default="releases", max_length=12)
    branch: str = Field(default="main", max_length=100)
    cats: list[str] = Field(default_factory=list, max_length=20)


@router.post("/sources/generate")
def generate_feed(body: GenerateBody, user: User = Depends(require_permission("settings.manage"))):
    """「フィードを作る」: キーワード・GitHub・Steam・arXiv の入力から取得先の URL を組み立てる（通信はしない。確認は /sources/check）。"""
    try:
        return feedgen.generate(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _manage_rows(db: Session) -> list[dict]:
    cfg = sources.load_config()
    stats, bars, totals = fetchlog.summary(db), fetchlog.week_bars(db), fetchlog.article_totals(db)
    mem = {s["source_key"]: s for s in service.status()["sources"]}
    rows = []
    for e in sourcemgr.entries(cfg):
        st = stats.get(e["key"]) or {}
        live = mem.get(e["key"]) or {}
        rows.append({**e, "last_at": st.get("last_at", "") or live.get("at", ""), "last_ok_at": st.get("last_ok_at", "") or live.get("ok_at", ""),
                     "last_ok": st.get("last_ok"), "last_error": st.get("last_error", ""), "last_count": st.get("last_count", 0), "last_status": st.get("last_status"),
                     "last_skipped": st.get("last_skipped", ""), "week": bars.get(e["key"], [None] * 7), "total": totals.get(e["key"], 0)})
    return rows


@router.get("/sources/manage")
def manage_list(user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """「取得先の管理」の一覧: 設定にある全ソース（止めているものも）と、取得の実績（最終取得・最終成功・直近の結果・7 日の成功率・記事の累計）。"""
    from app.workflows import research_keys

    cfg = sources.load_config()
    return {"items": _manage_rows(db), "groups": presets.listing()["groups"], "default_interval": channels_mod.load()["interval_minutes"],
            "reddit_key": research_keys.reddit() is not None, "limit": sources.MAX_PER_LIST, "interval_range": [sources.INTERVAL_MIN, sources.INTERVAL_MAX],
            "formats": sourcemgr.FORMAT_LABELS, "running": service.status()["running"], "enabled": cfg["enabled"]}


@router.get("/sources/manage/detail")
def manage_detail(key: str = Query(min_length=1, max_length=600), limit: int = Query(default=fetchlog.HISTORY_DEFAULT, ge=1, le=fetchlog.KEEP),
                  user: User = Depends(get_current_user), db: Session = Depends(get_news_db)):
    """1 件の詳細: 取得の履歴（直近 limit 回）と、そのソースから入った最近の記事。"""
    row = next((r for r in _manage_rows(db) if r["key"] == key), None)
    if row is None:
        raise HTTPException(status_code=404, detail="そのソースはありません")
    return {"item": row, "history": fetchlog.history(db, key, limit), "articles": fetchlog.recent_articles(db, key)}


class BulkBody(BaseModel):
    action: str = Field(pattern="^(enable|disable|delete|interval|check)$")
    keys: list[str] = Field(min_length=1, max_length=300)
    interval: int | None = Field(default=None, ge=0, le=sources.INTERVAL_MAX)


@router.post("/sources/manage/bulk")
async def manage_bulk(body: BulkBody, user: User = Depends(require_permission("settings.manage")), db: Session = Depends(get_news_db)):
    """選んだソースへの一括の操作。enable・disable・delete・interval（0 は既定に戻す）は設定へ保存、check は通信確認（保存しない・同時 4・40 件まで）。"""
    cfg = sources.load_config()
    if body.action == "check":
        by_key = {e["key"]: e for e in sourcemgr.entries(cfg)}
        wanted = [by_key[k] for k in body.keys if k in by_key][:40]
        gate = asyncio.Semaphore(4)

        async def one(e: dict) -> dict:
            async with gate:
                return {"key": e["key"], **await probe.check(e["kind"], e["value"], e["name"])}
        return {"results": await asyncio.gather(*[one(e) for e in wanted])}
    try:
        sourcemgr.apply(cfg, body.action, body.keys, body.interval)
        saved = sources.save_config(cfg)
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.action == "delete":
        fetchlog.forget(db, body.keys)
    return {"count": len(body.keys), "items": _manage_rows(db), "enabled": saved["enabled"]}


class AddItem(BaseModel):
    kind: str = Field(pattern="^(rss|page|github|codeberg|discourse|reddit|hackernews|huggingface)$")
    value: str = Field(default="", max_length=800)
    name: str = Field(default="", max_length=60)
    interval: int | None = Field(default=None, ge=0, le=sources.INTERVAL_MAX)


class AddBody(BaseModel):
    items: list[AddItem] = Field(min_length=1, max_length=400)


@router.post("/sources/manage/add")
def manage_add(body: AddBody, user: User = Depends(require_permission("settings.manage")), db: Session = Depends(get_news_db)):
    """取得先を足す（目録からのまとめ・フィードを作る手伝い・サイトを追加）。すでにあるものは足さず、間隔だけ付ける。"""
    cfg = sources.load_config()
    try:
        added = sourcemgr.add(cfg, [i.model_dump() for i in body.items])
        sources.save_config(cfg)
    except sources.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"added": added, "items": _manage_rows(db)}


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
            "imagegen_available": imagegen.available(settings["imagegen"]["tier"]), "prep_calls_today": prep.calls_today(), "imagegen_tiers": imagegen.tiers_info(), "cpu_llm": cpu_llm.info()}


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


@router.get("/images/hub/{name}")
def get_hub_image(name: str, user: User = Depends(get_current_user)):
    """ハブが生成した画像の写し（ログインした人だけ）。名前は <sha256>.<webp|png|jpg> の形だけ。"""
    found = imagegen.hub_image_path(name)
    if found is None:
        raise HTTPException(status_code=404, detail="画像がありません")
    return FileResponse(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


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
    return store.mark_reader_failed(db, out)


# ---- QNAP ニュースハブとつなぐ（app/news/hub_link.py・hub_sync.py・hub_jobs.py。設定の管理権限が要る） ----

HUB_MANAGE = Depends(require_permission("settings.manage"))


class HubPairBody(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str = Field(default=hub_link.NAME_DEFAULT, min_length=1, max_length=60)


class HubConfirmBody(BaseModel):
    match: bool


class HubSyncBody(BaseModel):
    reset: bool = False


def _hub_error(exc: hub_link.HubError) -> HTTPException:
    """つながらない（status 0）は 502、ハブが断った・形が違うは 422（メッセージは利用者に見せてよい文だけ）。"""
    return HTTPException(status_code=502 if exc.status == 0 else 422, detail=exc.message)


def _hub_status() -> dict:
    from app.models_mgmt import cloud

    conf = news_settings.load()
    state = hub_link.load()
    return {**hub_link.public_view(state), "source": conf["hub"]["source"], "effective_source": hub_sync.effective_source(conf, state),
            "openrouter_key": bool(cloud.api_key("openrouter")),
            "imagegen_ready": imagegen.available(conf["imagegen"]["tier"])}


@router.get("/hub")
def hub_status(user: User = HUB_MANAGE):
    """つないだ状態・最後の同期・cursor・借りた仕事の成否（トークン・鍵は含まない）。"""
    return _hub_status()


@router.post("/hub/pair")
def hub_pair(body: HubPairBody, request: Request, user: User = HUB_MANAGE, db=Depends(get_db)):
    """ハブの URL と 6 桁の番号でつなぐ。返す指紋（ハブの公開鍵から自分で計算したもの）を、ハブの画面の指紋と見比べて /hub/confirm を押す。"""
    try:
        hub_link.pair(body.url, body.code, body.name)
    except hub_link.HubError as exc:
        audit.record(db, "news.hub_pair", user=user, request=request, resource_type="news_hub", result="failure", metadata={"error": exc.message})
        raise _hub_error(exc) from exc
    audit.record(db, "news.hub_pair", user=user, request=request, resource_type="news_hub", metadata={"url": hub_link.load()["url"]})
    return _hub_status()


@router.post("/hub/confirm")
def hub_confirm(body: HubConfirmBody, request: Request, user: User = HUB_MANAGE, db=Depends(get_db)):
    """指紋を見比べた結果。同じなら使い始め、違うなら保存したトークンと鍵を捨てる（ハブの画面で、相手を取り消してください）。"""
    try:
        hub_link.confirm(body.match)
    except hub_link.HubError as exc:
        raise _hub_error(exc) from exc
    audit.record(db, "news.hub_confirm", user=user, request=request, resource_type="news_hub", metadata={"match": body.match})
    return _hub_status()


@router.delete("/hub")
def hub_disconnect(request: Request, user: User = HUB_MANAGE, db=Depends(get_db)):
    """つなぎを切る（保存したトークン・鍵・cursor を捨てる。取り込んだ記事は残す）。ハブ側の取り消しは、ハブの画面の「つないだ ControlDeck」から。"""
    hub_link.disconnect()
    audit.record(db, "news.hub_disconnect", user=user, request=request, resource_type="news_hub")
    return _hub_status()


@router.post("/hub/secret")
def hub_send_secret(request: Request, user: User = HUB_MANAGE, db=Depends(get_db)):
    """利用者が押したときだけ、OpenRouter の鍵をハブの公開鍵で封じて渡す（値は画面・ログ・応答・監査に出さない）。"""
    try:
        hub_link.send_openrouter_key()
    except hub_link.HubError as exc:
        audit.record(db, "news.hub_secret", user=user, request=request, resource_type="news_hub", result="failure", metadata={"name": hub_link.SECRET_NAME})
        raise _hub_error(exc) from exc
    audit.record(db, "news.hub_secret", user=user, request=request, resource_type="news_hub", metadata={"name": hub_link.SECRET_NAME})
    return _hub_status()


@router.post("/hub/sync")
async def hub_sync_now(body: HubSyncBody, user: User = HUB_MANAGE):
    """いま差分を取る（reset=true は cursor を 0 に戻して最初から）。出どころが local でも、つないで確かめてあれば取れる。"""
    result = await hub_sync.sync(reset=body.reset)
    return {"result": {k: v for k, v in result.items() if k in ("ok", "counts", "error", "skipped", "pages", "images", "rag", "imagegen")}, "status": await asyncio.to_thread(_hub_status)}


@router.post("/hub/jobs/run")
async def hub_jobs_now(user: User = HUB_MANAGE):
    """いま仕事を借りて処理する（設定の「借りる種類」・件数のとおり）。"""
    result = await hub_jobs.run_once()
    return {"result": result, "status": await asyncio.to_thread(_hub_status)}
