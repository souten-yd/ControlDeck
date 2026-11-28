"""目的: ニュースの取得先の目録（catalog.json）・ソースごとの取得の間隔・フィードを作る手伝い・取得先の管理（API・取得の履歴）の試験。外へ通信しない。

根拠: 2026-11-24 利用者「これらを元に、情報収集の蓄積、ナレッジ化を効率的に実施してほしい」「有料なのは使わない」
「登録時に HTTP 応答、XML 形式、最終更新日時を検証し、配信停止済みのフィードを除外する方式を推奨」「設定の中にボタンを置き、別の管理ページに飛ばして、疎通確認だったりとか、
これまでの取得実績、最終取得日時などを…新しいサイトの追加、削除などができるように」。
守ること: 目録は https・URL の重複なし・分野あり・確かめた日あり・有料でない・Reddit はキー印／間隔の来たソースだけを 1 回の上限つきで選ぶ／
作った URL の組み立てと不正な入力の拒否／履歴は 1 ソース 200 回まで／一括の操作は設定を壊さない。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import delete

from app.news import db as news_db
from app.news import feedgen, fetchlog, presets, service, sourcemgr, sources
from app.news.db import NewsFetchLog
from tests.conftest import CSRF_HEADERS


@pytest.fixture(autouse=True)
def isolated(client, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "_path", lambda: tmp_path / "news" / "sources.json")
    with news_db.session() as db:
        db.execute(delete(NewsFetchLog))
        db.commit()
    service._status.clear()
    yield


# ---- 目録の形 ----

def test_catalog_shape_unique_https_grouped_verified_free():
    assert len(presets.CATALOG["items"]) >= 300
    groups = {g["id"] for g in presets.GROUPS}
    ids = [p["id"] for p in presets.PRESETS]
    assert len(ids) == len(set(ids)), "URL（種類と値）の重複"
    for p in presets.PRESETS:
        assert p["group"] in groups and all(a in groups for a in p["also"]) and p["verified"]
        if p["kind"] == "rss":
            assert p["value"].startswith("https://")
        assert p["interval"] >= 0
    paid = ("bloomberg", "wsj.com", "nikkei.com/news", "site%3Anikkei", "business.nikkei", "nytimes", "economist.com", "technologyreview", "seekingalpha", "barrons", "shikiho")
    assert not any(h in p["value"] for p in presets.PRESETS for h in paid)
    assert not any((urlsplit(p["value"]).hostname or "") in ("ft.com", "www.ft.com") for p in presets.PRESETS if p["kind"] == "rss")
    for e in presets.CATALOG["items"]:
        if e["kind"] == "reddit":
            assert "キー" in e["note"]       # 取得の確認はしていない印
        else:
            assert re.fullmatch(r"20\d\d-\d\d-\d\d", e["latest"]) and e["count"] >= 1 and e["verified"] == presets.CATALOG["verified"]
            age = (datetime.fromisoformat(presets.CATALOG["verified"]) - datetime.fromisoformat(e["latest"])).days
            assert age <= (365 if e.get("stale") else 90)
    assert all(x["reason"] and x["group"] in groups for x in presets.EXCLUDED)


def test_catalog_has_requested_groups_intervals_and_recommended():
    labels = {g["label"] for g in presets.GROUPS}
    for want in ("AI・公式", "研究論文・arXiv", "GitHub・LLM推論", "Reddit・ゲーム", "ゲーム開発・3D", "日本政治・行政・政策", "暗号資産・海外", "ST・RWA・デジタル証券 海外"):
        assert want in labels
    by = {p["id"]: p for p in presets.PRESETS}
    assert by["github:ollama/ollama"]["interval"] == 15 and by["github:ollama/ollama"]["recommended"]
    assert by["rss:https://rss.arxiv.org/rss/cs.AI"]["interval"] == 1440
    assert by["rss:https://openai.com/news/rss.xml"]["interval"] == 30
    rec = [p for p in presets.PRESETS if p["recommended"]]
    assert len(rec) >= 30 and {p["kind"] for p in rec} >= {"rss", "github"}
    assert sourcemgr.format_of("rss", "https://rss.arxiv.org/rss/cs.AI") == "arxiv"
    assert presets.groups_of("github", "ollama/ollama")[0]


# ---- 間隔の判定 ----

def _src(key, interval=0):
    return sources.Source(key, key, "news", lambda: None, interval)


def test_due_keys_only_sources_whose_interval_has_come_and_cap():
    listing = [_src("a", 15), _src("b", 60), _src("c"), _src("d", 1440)]
    last = {"a": 1000.0, "b": 1000.0, "c": 1000.0, "d": 1000.0}
    now = 1000.0 + 20 * 60
    assert service.due_keys(listing, last, now, default_minutes=30) == ["a"]                        # 15 分だけ来た（c は既定 30 分でまだ）
    assert set(service.due_keys(listing, last, 1000.0 + 61 * 60, 30)) == {"a", "b", "c"}            # 1440 分の d はまだ
    assert set(service.due_keys(listing, {}, now, 30)) == {"a", "b", "c", "d"}                      # まだ取っていないものは来ている
    many = [_src(f"s{i}") for i in range(100)]
    assert len(service.due_keys(many, {}, now, 30)) == sources.MAX_PER_RUN                            # 1 回の上限
    old = service.due_keys([_src("x", 15), _src("y", 15)], {"x": 0.0, "y": -5000.0}, 6000.0, 30)
    assert old == ["y", "x"]                                                                          # 長く待たされたものを先に


def test_intervals_and_disabled_in_config_and_build_sources():
    cfg = sources.normalize({"github": ["a/b", "c/d"], "intervals": {"github:a/b": 15}, "disabled": ["github:c/d"]})
    built = {s.key: s for s in sources.build_sources(cfg)}
    assert built["github:a/b"].interval == 15 and "github:c/d" not in built
    for bad in ({"intervals": {"github:a/b": 1}}, {"intervals": {"k": "x"}}, {"intervals": []}, {"intervals": {"k": 99999}}, {"disabled": "x"}):
        with pytest.raises(sources.ConfigError):
            sources.normalize(bad)


# ---- フィードを作る手伝い ----

def test_feedgen_builds_urls_and_rejects_bad_input():
    k = feedgen.google_news("ROCm LLM", "en")
    assert k["value"] == "https://news.google.com/rss/search?q=ROCm%20LLM&hl=en-US&gl=US&ceid=US:en"
    assert "ceid=JP:ja" in feedgen.google_news("ローカルLLM", "ja")["value"]
    assert feedgen.github("https://github.com/ggml-org/llama.cpp/releases", "releases") == {"kind": "github", "value": "ggml-org/llama.cpp", "name": "llama.cpp", "lang": "en"}
    assert feedgen.github("a/b", "commits", "dev")["value"] == "https://github.com/a/b/commits/dev.atom"
    assert feedgen.github("a/b", "tags")["value"] == "https://github.com/a/b/tags.atom"
    assert feedgen.steam("https://store.steampowered.com/app/1091500/Cyberpunk_2077/")["value"] == "https://store.steampowered.com/feeds/news/app/1091500/"
    assert feedgen.steam("427520")["value"].endswith("/app/427520/")
    assert feedgen.arxiv(["cs.AI", "cs.CL", "cs.AI"])["value"] == "https://rss.arxiv.org/rss/cs.AI+cs.CL"
    for bad in (lambda: feedgen.google_news(""), lambda: feedgen.google_news("x" * 101), lambda: feedgen.google_news("a", "fr"), lambda: feedgen.github("not a repo"),
                lambda: feedgen.github("https://evil.test/a/b"), lambda: feedgen.github("a/b", "commits", "../x"), lambda: feedgen.github("a/b", "wiki"),
                lambda: feedgen.steam("abc"), lambda: feedgen.steam("https://evil.test/app/1"), lambda: feedgen.arxiv([]), lambda: feedgen.arxiv(["cs.XX"]),
                lambda: feedgen.arxiv([f"cs.{i}" for i in range(9)]), lambda: feedgen.generate({"type": "x"})):
        with pytest.raises(ValueError):
            bad()


# ---- 取得の履歴 ----

def test_fetchlog_keeps_200_per_source_and_summarizes():
    # 「今」を日の中ほど（UTC 12 時）に固定する。記録は今から 300〜51 分前に置くので、UTC の 0 時 51 分より前に流すと
    # 全部が前の日に入り、今日の棒が空になって落ちていた（2026-11-28・docs/ISSUES.md）。
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [{"source_key": "rss:https://a.test/f", "at": now - timedelta(minutes=300 - i), "ok": i % 10 != 0, "status": 200 if i % 10 else 503, "count": 5, "new": 1, "ms": 120,
             "error": "" if i % 10 else "HTTP 503"} for i in range(250)]
    rows.append({"source_key": "hn", "at": now, "ok": True, "status": 200, "count": 3, "new": 0, "ms": 50, "error": ""})
    with news_db.session() as db:
        fetchlog.record(db, rows)
        assert len(fetchlog.history(db, "rss:https://a.test/f", 500)) == fetchlog.KEEP
        assert len(fetchlog.history(db, "rss:https://a.test/f")) == fetchlog.HISTORY_DEFAULT
        s = fetchlog.summary(db)
        assert s["hn"]["last_ok"] is True and s["rss:https://a.test/f"]["last_ok_at"]
        bars = fetchlog.week_bars(db, now=now)["rss:https://a.test/f"]
        assert len(bars) == 7 and bars[-1] is not None and bars[-1] < 100
        fetchlog.forget(db, ["hn"])
        assert fetchlog.history(db, "hn") == []
    assert fetchlog.status_of("HTTP 403") == 403 and fetchlog.status_of("timeout") is None


def test_refresh_with_keys_records_history_and_skips_other_sources(monkeypatch):
    import asyncio

    async def ok():
        return [sources.make_item(kind="news", source="a", source_key="rss:https://a.test/f", title="記事のタイトル", url="https://a.test/1", published=datetime.now(timezone.utc))]

    async def boom():
        raise RuntimeError("HTTP 404 not found")
    listing = [sources.Source("rss:https://a.test/f", "A", "news", ok), sources.Source("rss:https://b.test/f", "B", "news", boom), sources.Source("rss:https://c.test/f", "C", "news", ok)]
    monkeypatch.setattr(sources, "build_sources", lambda cfg, ch=None: listing)
    monkeypatch.setattr(service, "_after_save", lambda saved, cfg: _noop())

    async def _noop():
        return {"gist": {}, "rag": {}}
    result = asyncio.run(service.refresh(force=True, keys=["rss:https://a.test/f", "rss:https://b.test/f"]))
    assert result["started"] and result["failed"] == 1
    with news_db.session() as db:
        a = fetchlog.history(db, "rss:https://a.test/f")[0]
        b = fetchlog.history(db, "rss:https://b.test/f")[0]
        assert fetchlog.history(db, "rss:https://c.test/f") == []
    assert a["ok"] and a["count"] == 1 and a["new"] == 1 and b["ok"] is False and b["status"] == 404
    assert "rss:https://c.test/f" not in service._last_fetch and "rss:https://a.test/f" in service._last_fetch


# ---- 管理の API ----

def test_manage_api_list_detail_bulk_add_and_permissions(admin_client, client):
    c = admin_client
    r = c.post("/api/v1/news/sources/manage/add", json={"items": [{"kind": "rss", "value": "https://a.test/feed.xml", "name": "A サイト", "interval": 320},
                                                               {"kind": "github", "value": "x/y", "interval": 15}, {"kind": "rss", "value": "https://a.test/feed.xml"}]}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["added"] == 2
    listing = c.get("/api/v1/news/sources/manage").json()
    by = {i["key"]: i for i in listing["items"]}
    assert by["rss:https://a.test/feed.xml"]["interval"] == 320 and by["github:x/y"]["interval"] == 15 and by["github:x/y"]["format"] == "github"
    assert by["rss:https://a.test/feed.xml"]["enabled"] and by["rss:https://a.test/feed.xml"]["domain"] == "a.test"
    with news_db.session() as db:
        fetchlog.record(db, [{"source_key": "github:x/y", "at": datetime.now(timezone.utc), "ok": False, "status": 403, "count": 0, "new": 0, "ms": 90, "error": "HTTP 403"}])
    d = c.get("/api/v1/news/sources/manage/detail", params={"key": "github:x/y"}).json()
    assert d["history"][0]["status"] == 403 and d["item"]["last_ok"] is False
    assert c.get("/api/v1/news/sources/manage/detail", params={"key": "rss:nope"}).status_code == 404
    k = ["rss:https://a.test/feed.xml"]
    assert c.post("/api/v1/news/sources/manage/bulk", json={"action": "disable", "keys": k}, headers=CSRF_HEADERS).status_code == 200
    assert sources.load_config()["disabled"] == k and not [s for s in sources.build_sources(sources.load_config()) if s.key in k]
    assert c.post("/api/v1/news/sources/manage/bulk", json={"action": "interval", "keys": k, "interval": 1280}, headers=CSRF_HEADERS).status_code == 200
    assert sources.load_config()["intervals"][k[0]] == 1280
    assert c.post("/api/v1/news/sources/manage/bulk", json={"action": "interval", "keys": k, "interval": 1}, headers=CSRF_HEADERS).status_code == 422
    assert c.post("/api/v1/news/sources/manage/bulk", json={"action": "delete", "keys": ["rss:https://unknown.test/"]}, headers=CSRF_HEADERS).status_code == 422
    assert c.post("/api/v1/news/sources/manage/bulk", json={"action": "delete", "keys": k}, headers=CSRF_HEADERS).status_code == 200
    cfg = sources.load_config()
    assert not [f for f in cfg["rss"] if "a.test" in f["url"]] and k[0] not in cfg["intervals"] and k[0] not in cfg["disabled"]
    g = c.post("/api/v1/news/sources/generate", json={"type": "steam", "target": "427520"}, headers=CSRF_HEADERS)
    assert g.status_code == 200 and g.json()["value"].endswith("/app/427520/")
    assert c.post("/api/v1/news/sources/generate", json={"type": "github", "target": "bad"}, headers=CSRF_HEADERS).status_code == 422
    assert c.post("/api/v1/news/sources/generate", json={"type": "zzz"}, headers=CSRF_HEADERS).status_code == 422


def test_manage_write_needs_settings_manage(client):
    from tests.test_news import login

    login(client, "news-viewer-2")
    assert client.get("/api/v1/news/sources/manage").status_code == 200
    for path, body in (("add", {"items": [{"kind": "reddit", "value": "Foo"}]}), ("bulk", {"action": "disable", "keys": ["hn"]})):
        assert client.post(f"/api/v1/news/sources/manage/{path}", json=body, headers=CSRF_HEADERS).status_code == 403
    assert client.post("/api/v1/news/sources/generate", json={"type": "steam", "target": "1"}, headers=CSRF_HEADERS).status_code == 403
