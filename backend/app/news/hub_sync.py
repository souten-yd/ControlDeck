"""ニュースハブの差分の同期（`GET /changes?cursor=`）と、データの出どころの切り替え。

2026-11-25 利用者「ControlDeck に QNAP ニュースハブとつなぐ機能」。相手の仕様は QnapNewsHub の docs/DESIGN.md 3.3 章・changes.py。

- **差分**: 保存した cursor の続きから 500 件ずつ（has_more の間、1 回に最大 MAX_PAGES ページ）読み、記事・物・イベントをニュースの DB（news.sqlite3）へ入れる。
  1 ページを入れて**コミットしてから** cursor を保存する（途中で止まっても、同じ分をもう一度入れるだけ。入れ方は冪等＝同じハブの id・同じ url は 1 行）。
  ハブが `reset: true`（cursor が最新より大きい＝ハブのデータが作り直された）を返したら、cursor を 0 に戻して最初から取り直す。
  受け取る範囲（種類・チャンネル・分類）を設定で変えたら、cursor を 0 に戻して取り直す（絞り込みで落ちた物も cursor は進むため）。
- **記事の入れ方**: 同じハブの id の行を更新。無ければ同じ url の行（自分で集めた同じ記事）にハブの id を付けて更新。どちらも無ければ新しい行。
  op=delete・archive（重複として畳まれた記事）は行を消す（star した記事は残す）。ハブの記事の画像が**ハブの中の道**（AI 生成の画像）のときは、
  トークンつきで取って data/news/images/hub/<sha256>.<拡張子> に写す（名前が中身の sha256 なので、取れた中身が名前と合うかを確かめる）。
- **データの出どころ**（設定 hub.source）: local＝今までどおり自分で集める・hub＝ハブから受け取る（自分では集めない）・auto＝ハブが答えるあいだはハブ、
  続けて 2 回答えなければ自分で集め、答えたらハブへ戻る。つないでいない・指紋を確かめていないときは、どれを選んでいても local。
- 取り込んだ記事の RAG への蓄積・画像生成は、設定の機能の入・切（hub.features）に従う。下ごしらえ・一言要約はハブがやったものを使い、ここでは呼ばない（ローカル LLM を動かさない）。
- 同期の関数は同期（asyncio.to_thread で呼ぶ）。見張りは service.news_loop が 1 分ごとに tick() を呼ぶ。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.news import channels, hub_link, imagegen, settings, sources
from app.news import db as news_db
from app.news.db import NewsAction, NewsHubEntity, NewsHubEvent, NewsItem

logger = logging.getLogger("control_deck.news.hub_sync")

LIMIT = 500
MAX_PAGES = 20
IMAGE_MAX_BYTES = 640 * 1024
IMAGE_PER_RUN = 40
FAILURES_TO_LOCAL = 2          # 続けてこの回数答えないと、auto は自分で集める側へ
_IMAGE_PATH = re.compile(r"^/api/v1/artifacts/images/([0-9a-f]{64})\.(webp|png|jpg)$")
_sync_lock = threading.Lock()
_last_try = {"sync": 0.0, "jobs": 0.0}


def effective_source(conf: dict | None = None, state: dict | None = None) -> str:
    """いま記事を集める側: "local"（自分で集める）か "hub"（ハブから受け取る）。"""
    conf = conf or settings.load()
    state = state or hub_link.load()
    source = conf["hub"]["source"]
    if source == "local" or not hub_link.ready(state):
        return "local"
    if source == "hub":
        return "hub"
    return "hub" if state["sync"].get("reachable") is True else "local"


def _scope(hub: dict) -> str:
    return json.dumps([hub["kinds"], hub["channels"], hub["categories"]], sort_keys=True)


def _when(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _s(value: object, limit: int) -> str:
    return str(value or "")[:limit]


# ---- 取り込み ----

def _existing(db, model_col, ids: list[int]) -> dict:
    out: dict = {}
    for i in range(0, len(ids), 400):
        for row in db.scalars(select(NewsItem).where(model_col.in_(ids[i:i + 400]))):
            out[getattr(row, model_col.key)] = row
    return out


def _item_fields(data: dict) -> dict | None:
    """ハブの記事（changes の data）を、こちらの列の値にする。形が合わなければ None（その記事は入れない）。"""
    url = sources.safe_url(data.get("url"))
    published = _when(data.get("published_at"))
    title = " ".join(str(data.get("title") or "").split())[:400]
    if not url or not published or not title:
        return None
    kind = data.get("kind") if data.get("kind") in sources.KINDS and data.get("kind") != "event" else "news"
    topics = [t for t in data.get("topics") or [] if isinstance(t, str)]
    topics = list(dict.fromkeys(topics + [t for t in data.get("entities") or [] if isinstance(t, str)]))[:14]
    return {"kind": kind, "source": _s(data.get("source"), 120) or "ハブ", "source_key": _s(data.get("source_key"), 200) or "hub", "title": title, "url": url[:1000],
            "summary": _s(data.get("summary"), 2000), "summary_ja": _s(data.get("summary_ja"), 2000), "gist": _s(data.get("gist"), 600), "img_prompt": _s(data.get("img_prompt"), 600),
            "topics": json.dumps(topics, ensure_ascii=False), "published_at": published, "image": data.get("image_url") if isinstance(data.get("image_url"), str) else ""}


def _apply_image(row: NewsItem, image: str) -> None:
    """ハブの画像を記事に付ける。外の URL はそのまま。ハブの中の道は、写しがあれば写しの道、無ければ取り込み待ちにする。"""
    if not image:
        return
    found = _IMAGE_PATH.match(image)
    if found:
        name = f"{found.group(1)}.{found.group(2)}"
        if imagegen.hub_image_path(name):
            row.image_url, row.hub_image = f"{imagegen.HUB_URL_PREFIX}{name}", ""
        else:
            row.hub_image = image
        return
    safe = sources.safe_url(image)
    if safe:
        row.image_url, row.hub_image = safe, ""


def apply_changes(rows: list[dict], *, on_prune: str = "keep") -> dict:
    """changes の 1 ページぶんを DB へ。{"items", "entities", "events", "removed", "changed": [RAG へ渡す記事の dict], "removed_urls": [...]}。冪等。"""
    from app.news import store

    counts = {"items": 0, "entities": 0, "events": 0, "removed": 0}
    changed_ids: list[int] = []
    removed_urls: list[str] = []
    with news_db.session() as db:
        item_up = {c["id"]: c for c in rows if c.get("kind") == "items" and c.get("op") == "upsert" and isinstance(c.get("data"), dict)}
        item_gone = [c["id"] for c in rows if c.get("kind") == "items" and c.get("op") in ("delete", "archive")]
        by_hub = _existing(db, NewsItem.hub_id, list(item_up))
        urls = {c["id"]: (sources.safe_url(c["data"].get("url")) or "") for c in item_up.values()}
        by_url = _existing(db, NewsItem.url, [u for hid, u in urls.items() if u and hid not in by_hub])
        for hub_id, change in item_up.items():
            fields = _item_fields(change["data"])
            if fields is None:
                continue
            image = fields.pop("image")
            row = by_hub.get(hub_id) or by_url.get(fields["url"])
            fresh = row is None
            if fresh:
                row = NewsItem(fetched_at=datetime.now(timezone.utc), **{k: v for k, v in fields.items() if k not in ("summary_ja", "gist", "img_prompt")})
                db.add(row)
            moved = fresh or (row.title, row.summary) != (fields["title"], fields["summary"])
            row.hub_id = hub_id
            for key in ("kind", "source", "source_key", "title", "summary", "topics"):
                setattr(row, key, fields[key])
            for key in ("summary_ja", "gist", "img_prompt"):       # ハブが空のときは、こちらにあるものを消さない
                setattr(row, key, fields[key] or getattr(row, key) or "")
            row.published_at = row.published_at if (not fresh and row.published_at) else fields["published_at"]
            row.fetched_at = datetime.now(timezone.utc)
            _apply_image(row, image)
            db.flush()
            counts["items"] += 1
            if moved:
                changed_ids.append(row.id)
        if item_gone:
            starred = select(NewsAction.item_id).where(NewsAction.action == "star")
            doomed = list(db.scalars(select(NewsItem).where(NewsItem.hub_id.in_(item_gone), NewsItem.id.not_in(starred))))
            ids = [r.id for r in doomed]
            if on_prune == "delete":
                removed_urls += [r.url for r in doomed if r.kind != "event"]
            for row in doomed:
                db.delete(row)
            counts["removed"] += len(ids)
            db.flush()
            imagegen.delete_images(ids)
        for change in rows:
            data = change.get("data")
            if change.get("kind") == "entities":
                if change.get("op") == "delete":
                    db.execute(delete(NewsHubEntity).where(NewsHubEntity.hub_id == change["id"]))
                    counts["removed"] += 1
                elif isinstance(data, dict):
                    db.merge(NewsHubEntity(hub_id=change["id"], key=_s(data.get("key"), 200), name=_s(data.get("name"), 200), type=_s(data.get("type"), 24) or "other",
                                           first_seen=_s(data.get("first_seen"), 24), last_seen=_s(data.get("last_seen"), 24), article_count=int(data.get("article_count") or 0)))
                    counts["entities"] += 1
            elif change.get("kind") == "events":
                if change.get("op") == "delete":
                    db.execute(delete(NewsHubEvent).where(NewsHubEvent.hub_id == change["id"]))
                    counts["removed"] += 1
                elif isinstance(data, dict):
                    db.merge(NewsHubEvent(hub_id=change["id"], rep_id=data.get("rep_id") if isinstance(data.get("rep_id"), int) else None, title=_s(data.get("title"), 400),
                                          n_articles=int(data.get("n_articles") or 0), n_sources=int(data.get("n_sources") or 0), has_primary=bool(data.get("has_primary")),
                                          first_at=_s(data.get("first_at"), 24), last_at=_s(data.get("last_at"), 24), summary=_s(data.get("summary"), 4000),
                                          summary_model=_s(data.get("summary_model"), 200), entities=json.dumps(data.get("entities") if isinstance(data.get("entities"), list) else [], ensure_ascii=False),
                                          article_ids=json.dumps([i for i in data.get("article_ids") or [] if isinstance(i, int)][:200])))
                    counts["events"] += 1
        db.commit()
        changed = [store.item_dict(r) for r in db.scalars(select(NewsItem).where(NewsItem.id.in_(changed_ids or [0]))) if r.kind != "event"]
    return {**counts, "changed": changed, "removed_urls": removed_urls}


def fetch_images(limit: int = IMAGE_PER_RUN) -> dict:
    """取り込み待ちのハブの画像（AI 生成）を、トークンつきで取って写す。名前の sha256 と中身が合うものだけ。{"done", "failed"}。"""
    done = failed = 0
    with news_db.session() as db:
        rows = list(db.scalars(select(NewsItem).where(NewsItem.hub_id.is_not(None), NewsItem.hub_image != "").limit(limit)))
        for row in rows:
            found = _IMAGE_PATH.match(row.hub_image)
            if not found:
                row.hub_image = ""
                continue
            sha, ext = found.groups()
            name = f"{sha}.{ext}"
            try:
                if not imagegen.hub_image_path(name):
                    response = hub_link.call("GET", row.hub_image[len(hub_link.API):], raw=True)
                    data = response.content
                    if not data or len(data) > IMAGE_MAX_BYTES or hashlib.sha256(data).hexdigest() != sha:
                        raise hub_link.HubError("画像の中身が名前（sha256）と合いません")
                    root = imagegen.hub_images_dir()
                    root.mkdir(parents=True, exist_ok=True)
                    tmp = root / f".{name}.tmp"
                    tmp.write_bytes(data)
                    tmp.replace(root / name)
                row.image_url, row.hub_image = f"{imagegen.HUB_URL_PREFIX}{name}", ""
                done += 1
            except hub_link.HubError as exc:
                failed += 1
                if exc.status == 404:
                    row.hub_image = ""
                logger.info("ハブの画像を取れませんでした: %s", exc.message)
        db.commit()
    return {"done": done, "failed": failed}


# ---- 同期 ----

def _record_failure(error: str) -> None:
    def apply(state: dict) -> None:
        sync = state["sync"]
        sync.update(last_at=hub_link.now_iso(), last_error=error[:300], failures=int(sync.get("failures") or 0) + 1)
        if sync["failures"] >= FAILURES_TO_LOCAL:
            sync["reachable"] = False
    hub_link.update(apply)


def sync_once(*, reset: bool = False) -> dict:
    """差分を取れるだけ取って入れる（同期）。{"ok", "pages", "counts", "changed", "removed_urls", "error", "skipped"}。ハブが答えないとき ok=False。"""
    state = hub_link.load()
    if not hub_link.ready(state):
        return {"ok": False, "skipped": "not_ready", "pages": 0, "counts": {}, "changed": [], "removed_urls": [], "error": ""}
    conf = settings.load()
    hub = conf["hub"]
    scope = _scope(hub)
    cursor = 0 if (reset or state.get("scope") != scope) else int(state["cursor"] or 0)
    total = {"items": 0, "entities": 0, "events": 0, "removed": 0}
    changed: list[dict] = []
    removed_urls: list[str] = []
    pages, restarted, latest = 0, False, int(state["latest"] or 0)
    try:
        while pages < MAX_PAGES:
            params = {"cursor": cursor, "limit": LIMIT, "kinds": ",".join(hub["kinds"])}
            if hub["channels"]:
                params["channels"] = ",".join(hub["channels"])
            if hub["categories"]:
                params["categories"] = ",".join(hub["categories"])
            data = hub_link.call("GET", "/changes", params=params)
            if not isinstance(data, dict) or not isinstance(data.get("changes"), list) or not isinstance(data.get("cursor"), int):
                raise hub_link.HubError("ハブの差分の応答の形が正しくありません")
            if data.get("reset") and not restarted:
                restarted, cursor = True, 0
                continue
            pages += 1
            result = apply_changes(data["changes"], on_prune=conf["rag"]["on_prune"])
            for key in total:
                total[key] += result[key]
            changed += result["changed"]
            removed_urls += result["removed_urls"]
            cursor, latest = int(data["cursor"]), int(data.get("latest") or cursor)
            hub_link.update(lambda s, c=cursor, l=latest: s.update(cursor=c, latest=l, scope=scope))
            if not data.get("has_more"):
                break
        images = fetch_images()
    except hub_link.HubError as exc:
        _record_failure(exc.message)
        return {"ok": False, "skipped": "", "pages": pages, "counts": total, "changed": changed, "removed_urls": removed_urls, "error": exc.message}

    def apply(s: dict) -> None:
        sync = s["sync"]
        sync.update(last_at=hub_link.now_iso(), last_ok_at=hub_link.now_iso(), last_error="", failures=0, reachable=True, counts={**total, "pages": pages, "images": images["done"]})
        for key in ("items", "entities", "events", "removed"):
            sync["total"][key] = int(sync["total"].get(key) or 0) + total[key]
    hub_link.update(apply)
    return {"ok": True, "skipped": "", "pages": pages, "counts": total, "images": images, "changed": changed, "removed_urls": removed_urls, "error": ""}


async def sync(*, reset: bool = False) -> dict:
    """同期して、取り込んだ記事を設定どおり RAG・画像生成へ回す。同時に 1 本だけ。"""
    from app.news import service

    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "skipped": "running", "counts": {}}
    try:
        result = await asyncio.to_thread(sync_once, reset=reset)
        conf = settings.load()
        features = conf["hub"]["features"]
        result["rag"] = {"added": 0, "error": ""}
        if result["ok"] and features["rag"] and (result["changed"] or result["removed_urls"]):
            result["rag"] = await service.ingest_rag(result["changed"], conf, result["removed_urls"])
        result["imagegen"] = {"done": 0, "failed": 0, "skipped": "disabled"}
        if result["ok"] and features["imagegen"]:
            try:
                result["imagegen"] = await asyncio.to_thread(imagegen.generate_pending, conf)
            except Exception as exc:  # noqa: BLE001 - 画像が作れなくても同期は成功
                logger.warning("ハブの記事のイメージ画像の生成に失敗しました: %s", exc)
                result["imagegen"] = {"done": 0, "failed": 0, "skipped": "", "error": service._error_text(exc)}  # noqa: SLF001
        result.pop("changed", None)
        result.pop("removed_urls", None)
        return result
    finally:
        _sync_lock.release()


async def tick() -> None:
    """見張り（service.news_loop が 1 分ごとに呼ぶ）。間隔が来ていれば同期し、仕事を借りる設定なら借りる。失敗は握る。"""
    conf = settings.load()
    hub = conf["hub"]
    state = hub_link.load()
    if not hub_link.ready(state):
        return
    now = time.monotonic()
    if hub["source"] != "local" and now - _last_try["sync"] >= hub["sync_minutes"] * 60:
        _last_try["sync"] = now
        try:
            result = await sync()
            if result.get("ok"):
                logger.info("ニュースハブと同期しました: %s", result["counts"])
        except Exception:  # noqa: BLE001 - 1 回の失敗で見張りを止めない
            logger.exception("ニュースハブとの同期に失敗しました")
    if hub["jobs"]["enabled"] and now - _last_try["jobs"] >= hub["jobs"]["interval_minutes"] * 60 and not settings.in_quiet(conf, datetime.now()):
        _last_try["jobs"] = now
        try:
            from app.news import hub_jobs

            await hub_jobs.run_once()
        except Exception:  # noqa: BLE001
            logger.exception("ニュースハブの仕事を借りられませんでした")


def reset_timers() -> None:
    _last_try.update(sync=0.0, jobs=0.0)
