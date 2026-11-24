"""目的: ControlDeck の「QNAP ニュースハブとつなぐ」（app/news/hub_link.py・hub_sync.py・hub_jobs.py と News 設定の hub 節）の試験。
**ハブの本物のコード（QnapNewsHub の uvicorn を一時の置き場で起こす）と往復**させ、つなぐ → 指紋 → 鍵を渡す → 差分 → 仕事を借りて返す → 画像を送る を通す。
ハブのリポジトリ（既定 /data1tb/ControlDeck/QnapNewsHub。環境変数 NEWSHUB_REPO で変える）の venv が無い環境では、往復の試験だけ skip する（それ以外はハブ無しで動く）。
外へは通信せず（127.0.0.1 だけ）、LLM と画像生成のモデルは模擬（ローカル LLM を呼ばない）。

根拠: 2026-11-25 利用者「ControlDeck に QNAP ニュースハブとつなぐ機能（v0.3 の ControlDeck 側）」（docs/PLAN-integrated-knowledge.md 1.5 章・案 C、QnapNewsHub の docs/DESIGN.md 3 章）。
守ること:
- 封じ方（ECIES: X25519＋HKDF-SHA256＋AES-GCM・追加データに名前）がハブの link.py と同じで、**ハブが開けた鍵が送った鍵と一致する**（ハブの本物のコードで確かめる）。
- 指紋はハブの公開鍵から自分で計算し、ハブの画面の指紋と同じ。利用者が「同じ」と確かめるまで、同期・仕事・鍵の受け渡しをしない。
- トークン・秘密鍵・OpenRouter の鍵は、保存ファイル（暗号化）・画面の応答・ログに平文で出ない。
- 差分は冪等（二重に入れない）・cursor は 1 ページごとに保存・reset や範囲の変更で取り直す・delete と archive で消す（star は残す）。
- 出どころ: 既定は local（今の動きを変えない）・auto はハブが 2 回答えなければ local へ、答えたらハブへ・hub のあいだは自分で集めない。
- 借りた仕事は種類ごとに既存の処理で返し、画像は送ってから結果を返す。ハブが取り消されたら認証エラーを画面に出す。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from app.models_mgmt import cloud
from app.news import hub_jobs, hub_link, hub_sync, imagegen, llm, service, settings
from app.news import db as news_db
from app.news.db import NewsAction, NewsHubEntity, NewsHubEvent, NewsItem
from tests.test_news import clean_news, login  # noqa: F401  (clean_news は autouse の fixture を共有する)

HUB_REPO = Path(os.environ.get("NEWSHUB_REPO", "/data1tb/ControlDeck/QnapNewsHub"))
HUB_PY = HUB_REPO / ".venv" / "bin" / "python"
H = {"X-Requested-With": "ControlDeck"}
ADMIN_PW = "-".join(["hub", "test", "password", "1234"])       # 見本の値は実行時に組み立てる（AGENTS.md 4）
FAKE_KEY = "-".join(["sk", "or", "v1", "q" * 24])
NOW = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)

SEED = """
from datetime import datetime, timedelta, timezone
from qnapnewshub import auth, writer
from qnapnewshub import db as news_db
from qnapnewshub.db import Entity, Event, EventArticle, NewsItem
import os
auth.create_admin("admin", os.environ["HUB_TEST_PW"])
now = datetime.now(timezone.utc)
with news_db.session() as db:
    arts = [NewsItem(kind="news", source=f"S{i}", source_key=f"rss:s{i}", title=f"合成の記事 {i}", url=f"https://hub.test/{i}", summary="合成の抜粋 12345",
                     published_at=now - timedelta(minutes=i), prep_tries=3 if i == 4 else 0) for i in range(5)]
    db.add_all(arts); db.flush()
    ev = Event(rep_id=arts[0].id, title="合成イベント", n_articles=3, n_sources=3, first_at=now, last_at=now)
    db.add(ev); db.flush()
    db.add_all([EventArticle(article_id=a.id, event_id=ev.id) for a in arts[:3]])
    db.add(Entity(key="gpt", name="GPT", type="model", first_seen=now, last_seen=now, article_count=2))
    db.commit()
writer.forget_all()
"""
READ_SECRET = """
import hashlib, os, sys
from qnapnewshub import secretbox
got = secretbox.get_secret("openrouter_key") or ""
sys.exit(0 if hashlib.sha256(got.encode()).hexdigest() == os.environ["EXPECT_SHA"] else 3)
"""


def webp(width: int = 96, height: int = 96) -> bytes:
    body = b"\x2f" + struct.pack("<I", (width - 1) | ((height - 1) << 14)) + b"\x00" * 9
    chunk = b"VP8L" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


class Hub:
    """一時の置き場で起こしたハブ（本物の uvicorn）。管理者の cookie つきの client を持つ。"""

    def __init__(self, tmp: Path) -> None:
        self.data = tmp / "hubdata"
        self.data.mkdir()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "NEWSHUB_DATA": str(self.data), "NEWSHUB_AUTO": "0",
                    "NEWSHUB_HOST": "127.0.0.1", "NEWSHUB_PORT": str(self.port), "HUB_TEST_PW": ADMIN_PW}
        subprocess.run([str(HUB_PY), "-c", SEED], env=self.env, cwd=HUB_REPO / "backend", check=True, timeout=120)
        self.proc = subprocess.Popen([str(HUB_PY), "-m", "qnapnewshub.cli", "run"], env=self.env, cwd=HUB_REPO / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.admin = httpx.Client(base_url=f"{self.url}/api/v1", timeout=20)
        for _ in range(100):
            try:
                if self.admin.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise RuntimeError("ハブが起動しません")
        assert self.admin.post("/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 200

    def pairing(self) -> dict:
        return self.admin.post("/link/pairing").json()

    def stop(self) -> None:
        self.admin.close()
        self.proc.terminate()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture()
def hub(tmp_path):
    if not HUB_PY.is_file():
        pytest.skip("ハブ（QnapNewsHub）の venv が無い環境では往復の試験を飛ばす")
    h = Hub(tmp_path)
    yield h
    h.stop()


@pytest.fixture(autouse=True)
def places(tmp_path, monkeypatch):
    """状態・設定・画像・クラウドの鍵を試験ごとの場所へ（本番の data にも、他の試験にも触れない）。"""
    monkeypatch.setattr(hub_link, "_path", lambda: tmp_path / "news" / "hub.json")
    monkeypatch.setattr(settings, "_path", lambda: tmp_path / "news" / "settings.json")
    monkeypatch.setattr(cloud, "_path", lambda: tmp_path / "cloud-providers.json")
    monkeypatch.setattr(imagegen, "images_dir", lambda: tmp_path / "images")
    monkeypatch.setattr(imagegen, "models_dir", lambda: tmp_path / "models" / "news-imagegen")
    with news_db.session() as db:
        for model in (NewsHubEntity, NewsHubEvent):
            db.execute(delete(model))
        db.commit()
    hub_sync.reset_timers()
    yield


def put_settings(**hub_part) -> dict:
    conf = settings.load()
    conf["hub"] = {**conf["hub"], **hub_part}
    return settings.save(conf)


def rows() -> list[NewsItem]:
    with news_db.session() as db:
        return list(db.scalars(select(NewsItem).order_by(NewsItem.id)))


# ---- ハブ無しで確かめること ----

def test_defaults_keep_todays_behavior_and_settings_are_validated():
    conf = settings.normalize({})
    assert conf["hub"]["source"] == "local" and conf["hub"]["jobs"]["enabled"] is False and conf["hub"]["sync_minutes"] == 5
    assert hub_sync.effective_source(conf) == "local"          # つないでいなければ、どれを選んでも local
    ok = settings.normalize({"hub": {"source": "auto", "channels": ["ai-news"], "categories": ["news", "model"], "kinds": ["items"], "features": {"imagegen": True}}})["hub"]
    assert ok["source"] == "auto" and ok["categories"] == ["news", "model"] and ok["features"] == {"imagegen": True, "reader": True, "rag": True}
    for bad in ({"source": "cloud"}, {"sync_minutes": 0}, {"categories": ["x"]}, {"channels": ["a b"]}, {"kinds": "items"}, {"jobs": {"kinds": ["embed"]}}, {"jobs": {"max_per_run": 99}}):
        with pytest.raises(settings.ConfigError):
            settings.normalize({"hub": bad})


def test_fingerprint_matches_hub_formula_and_url_is_checked():
    pub = bytes(range(32))
    assert hub_link.fingerprint(pub) == "-".join(hashlib.sha256(pub).hexdigest()[:20][i:i + 4] for i in range(0, 20, 4))
    assert hub_link.normalize_url("http://nas.local:8787/") == "http://nas.local:8787"
    for bad in ("ftp://x", "http://u:p@x", "http://x/?a=1", "nas.local", ""):
        with pytest.raises(hub_link.HubError):
            hub_link.normalize_url(bad)


def _state_ready(url: str = "http://127.0.0.1:9") -> None:
    def apply(s: dict) -> None:
        s.update(url=url, token_enc=hub_link.encrypt_text("tok"), verified=True)
    hub_link.update(apply)


def test_effective_source_auto_goes_local_after_two_failures_and_back(monkeypatch):
    _state_ready()
    put_settings(source="auto")
    assert hub_sync.effective_source() == "local"                          # まだ一度も答えていない
    hub_link.update(lambda s: s["sync"].update(reachable=True, failures=0))
    assert hub_sync.effective_source() == "hub"
    monkeypatch.setattr(hub_link, "call", lambda *a, **k: (_ for _ in ()).throw(hub_link.HubError("つながりません")))
    assert hub_sync.sync_once()["ok"] is False and hub_sync.effective_source() == "hub"       # 1 回の失敗ではまだハブ
    assert hub_sync.sync_once()["ok"] is False and hub_sync.effective_source() == "local"      # 2 回で自分で集める側へ
    put_settings(source="hub")
    assert hub_sync.effective_source() == "hub"                                               # hub を選んでいれば、答えなくても自分では集めない
    monkeypatch.setattr(hub_link, "call", lambda *a, **k: {"cursor": 0, "latest": 0, "has_more": False, "reset": False, "changes": []})
    put_settings(source="auto")
    assert hub_sync.sync_once()["ok"] is True and hub_sync.effective_source() == "hub"          # 答えたらハブへ戻る


def item_change(hub_id: int, **over) -> dict:
    data = {"id": hub_id, "kind": "news", "source": "S", "source_key": "rss:s", "title": f"記事 {hub_id}", "url": f"https://x.test/{hub_id}", "summary": "抜粋",
            "summary_ja": "", "published_at": "2026-11-25T08:00:00Z", "topics": ["AI"], "gist": "", "image_url": None, "image_ai": False, "img_prompt": "", "entities": ["GPT"], **over}
    return {"seq": hub_id, "kind": "items", "id": hub_id, "op": "upsert", "data": data}


def test_apply_changes_is_idempotent_merges_same_url_and_removes_except_starred():
    with news_db.session() as db:      # 自分で集めた同じ記事（同じ url）にハブの id が付く
        db.add(NewsItem(kind="news", source="X", source_key="rss:x", title="元", url="https://x.test/1", summary="", published_at=NOW, fetched_at=NOW, topics="[]"))
        db.commit()
    page = [item_change(1), item_change(2), item_change(3, summary_ja="日本語の概要", gist="一言", img_prompt="a chip")]
    first = hub_sync.apply_changes(page)
    again = hub_sync.apply_changes(page)
    got = {r.hub_id: r for r in rows()}
    assert first["items"] == again["items"] == 3 and len(got) == 3 and got[1].url == "https://x.test/1" and got[1].title == "記事 1"
    assert got[3].summary_ja == "日本語の概要" and got[3].gist == "一言" and got[3].img_prompt == "a chip" and json.loads(got[3].topics) == ["AI", "GPT"]
    assert again["changed"] == []                                            # 変わっていない記事は RAG へ渡し直さない
    with news_db.session() as db:
        db.add(NewsAction(user_id=1, item_id=got[2].id, action="star"))
        db.commit()
    out = hub_sync.apply_changes([{"seq": 9, "kind": "items", "id": 1, "op": "delete", "data": None}, {"seq": 10, "kind": "items", "id": 2, "op": "archive", "data": {"id": 2, "dup_of": 1}},
                                  {"seq": 11, "kind": "items", "id": 3, "op": "archive", "data": {"id": 3, "dup_of": 1}}])
    assert sorted(r.hub_id for r in rows()) == [2] and out["removed"] == 2          # star した記事は残る
    bad = hub_sync.apply_changes([item_change(5, url="javascript:alert(1)"), item_change(6, published_at="x")])
    assert bad["items"] == 0 and sorted(r.hub_id for r in rows()) == [2]


def test_sync_saves_cursor_per_page_resets_and_refetches_on_scope_change(monkeypatch):
    _state_ready()
    put_settings(source="hub")
    calls: list[dict] = []
    pages = {0: {"cursor": 2, "latest": 4, "has_more": True, "reset": False, "changes": [item_change(1), item_change(2)]},
             2: {"cursor": 4, "latest": 4, "has_more": False, "reset": False, "changes": [item_change(3), item_change(4)]}}

    def fake(method, path, **kw):
        calls.append(kw["params"])
        if kw["params"]["cursor"] > 100:
            return {"cursor": 4, "latest": 4, "has_more": False, "reset": True, "changes": []}
        return pages[kw["params"]["cursor"]]
    monkeypatch.setattr(hub_link, "call", fake)
    assert hub_sync.sync_once()["counts"]["items"] == 4 and hub_link.load()["cursor"] == 4 and [c["cursor"] for c in calls] == [0, 2]
    calls.clear()
    pages[4] = {"cursor": 4, "latest": 4, "has_more": False, "reset": False, "changes": []}
    assert hub_sync.sync_once()["counts"]["items"] == 0 and [c["cursor"] for c in calls] == [4]      # 続きから
    calls.clear()
    put_settings(source="hub", categories=["model"])                      # 範囲を変えたら、最初から取り直す（絞り込みで落ちた物も cursor は進むため）
    hub_sync.sync_once()
    assert calls[0]["cursor"] == 0 and calls[0]["categories"] == "model" and len(rows()) == 4       # 二重にならない
    calls.clear()
    hub_link.update(lambda s: s.update(cursor=500))                       # ハブのデータが作り直された（cursor が最新より大きい）→ 0 から
    hub_sync.sync_once()
    assert [c["cursor"] for c in calls] == [500, 0, 2]


def test_failed_job_page_does_not_move_cursor(monkeypatch):
    _state_ready()
    seen = iter([{"cursor": 2, "latest": 4, "has_more": True, "reset": False, "changes": [item_change(1)]}, hub_link.HubError("途中で止まった")])

    def fake(*a, **k):
        value = next(seen)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(hub_link, "call", fake)
    result = hub_sync.sync_once()
    assert result["ok"] is False and hub_link.load()["cursor"] == 2 and len(rows()) == 1       # 入れたページの cursor だけ進む


def test_hub_image_is_checked_against_its_sha256_name(monkeypatch):
    data = webp()
    sha = hashlib.sha256(data).hexdigest()
    _state_ready()
    hub_sync.apply_changes([item_change(1, image_url=f"/api/v1/artifacts/images/{sha}.webp", image_ai=True), item_change(2, image_url="https://img.test/a.png")])
    got = {r.hub_id: r for r in rows()}
    assert got[1].hub_image and got[1].image_url is None and got[2].image_url == "https://img.test/a.png"
    bodies = iter([b"x" * 10, data])
    monkeypatch.setattr(hub_link, "call", lambda *a, **k: type("R", (), {"content": next(bodies)})())
    assert hub_sync.fetch_images() == {"done": 0, "failed": 1}         # 名前（sha256）と中身が合わない画像は取り込まない
    assert hub_sync.fetch_images() == {"done": 1, "failed": 0}
    row = {r.hub_id: r for r in rows()}[1]
    assert row.image_url == f"{imagegen.HUB_URL_PREFIX}{sha}.webp" and row.hub_image == "" and imagegen.hub_image_path(f"{sha}.webp")
    assert imagegen.hub_image_path("../x.webp") is None and imagegen.is_generated_url(row.image_url)


def test_hub_items_follow_feature_switches_and_manual_refresh(admin_client, monkeypatch):
    hub_sync.apply_changes([item_change(1)])
    put_settings(source="local", features={"imagegen": False, "reader": False, "rag": True})
    item_id = rows()[0].id
    assert admin_client.get(f"/api/v1/news/items/{item_id}/reader").status_code == 409        # ハブの記事のリーダーは切
    assert [i["id"] for i in imagegen.pending(10)] == []                                       # ハブの記事の画像生成は切（自分の記事だけ）
    assert [i["id"] for i in imagegen.pending(10, include_hub=True)] == [item_id]
    _state_ready()
    put_settings(source="hub")
    called = []

    async def fake_sync(**kw):
        called.append(kw)
        return {"ok": True, "counts": {"items": 0}}
    monkeypatch.setattr(hub_sync, "sync", fake_sync)

    async def boom(**kw):
        raise AssertionError("出どころがハブのあいだは自分で集めない")
    monkeypatch.setattr(service, "refresh", boom)
    r = admin_client.post("/api/v1/news/refresh", headers=H)
    assert r.status_code == 200 and r.json()["hub"] is True and called


def test_hub_endpoints_need_settings_manage(client):
    login(client, "hub-viewer", "viewer")
    assert client.get("/api/v1/news/hub").status_code == 403
    assert client.post("/api/v1/news/hub/secret", headers=H).status_code == 403
    assert client.delete("/api/v1/news/hub", headers=H).status_code == 403


# ---- ハブの本物のコードとの往復 ----

def test_round_trip_with_real_hub_pair_secret_sync_jobs_image(hub, admin_client, tmp_path, monkeypatch, caplog):
    caplog.set_level("DEBUG")
    # 1. 番号が違えば、何も保存しない
    r = admin_client.post("/api/v1/news/hub/pair", json={"url": hub.url, "code": "000000"}, headers=H)
    assert r.status_code == 422 and not hub_link.linked()
    # 2. つなぐ → 両方の指紋。ハブの画面の指紋・ハブが覚えた ControlDeck の指紋と同じ
    started = hub.pairing()
    r = admin_client.post("/api/v1/news/hub/pair", json={"url": hub.url + "/", "code": started["code"], "name": "試験の ControlDeck"}, headers=H)
    assert r.status_code == 200, r.text
    view = r.json()
    ours = hub.admin.get("/link").json()
    assert view["hub_fingerprint"] == started["fingerprint"] == ours["fingerprint"] and view["my_fingerprint"] == ours["peers"][0]["fingerprint"] and not view["verified"]
    token = hub_link._token(hub_link.load())  # noqa: SLF001
    text = hub_link._path().read_text(encoding="utf-8")  # noqa: SLF001
    assert token not in text and token not in json.dumps(view) and "private_enc" in text and "token_enc" in text      # 保存は暗号化・応答に出ない
    # 3. 指紋を確かめるまでは、同期・鍵・仕事をしない
    assert admin_client.post("/api/v1/news/hub/secret", headers=H).status_code == 422
    assert hub_sync.sync_once()["skipped"] == "not_ready"
    # 4. 確かめた → 鍵を渡す（OpenRouter の鍵が無ければ断る → 入れて渡す）
    assert admin_client.post("/api/v1/news/hub/confirm", json={"match": True}, headers=H).json()["verified"] is True
    assert admin_client.post("/api/v1/news/hub/secret", headers=H).status_code == 422
    cloud.save_provider("openrouter", {"api_key": FAKE_KEY})
    sent = admin_client.post("/api/v1/news/hub/secret", headers=H)
    assert sent.status_code == 200 and sent.json()["secret"]["sent_at"] and FAKE_KEY not in sent.text
    done = subprocess.run([str(HUB_PY), "-c", READ_SECRET], env={**hub.env, "EXPECT_SHA": hashlib.sha256(FAKE_KEY.encode()).hexdigest()}, cwd=HUB_REPO / "backend", timeout=60)
    assert done.returncode == 0                                              # ハブの本物のコードで開けた鍵が、送った鍵と一致
    with pytest.raises(hub_link.HubError):                                   # 受け取れる名前は openrouter だけ（追加データに名前を含めるので、別の名前では開かれない）
        hub_link.call("POST", "/link/secret", json_body={"name": "other", "sealed": hub_link.seal(base64.b64decode(hub_link.load()["hub_public_key"]), "other", "x")})
    # 5. 差分: 記事 5・物 1・イベント 1。2 回目は何も増えない
    put_settings(source="auto")
    synced = admin_client.post("/api/v1/news/hub/sync", json={}, headers=H).json()
    assert synced["result"]["ok"] and synced["result"]["counts"] == {"items": 5, "entities": 1, "events": 1, "removed": 0}
    assert synced["status"]["cursor"] == synced["status"]["latest"] > 0 and synced["status"]["effective_source"] == "hub"
    again = admin_client.post("/api/v1/news/hub/sync", json={}, headers=H).json()["result"]
    assert again["counts"]["items"] == 0 and len(rows()) == 5
    with news_db.session() as db:
        assert db.scalar(select(NewsHubEvent.n_sources)) == 3 and db.scalar(select(NewsHubEntity.name)) == "GPT"
    assert admin_client.post("/api/v1/news/hub/sync", json={"reset": True}, headers=H).json()["result"]["counts"]["items"] == 5 and len(rows()) == 5      # 最初から取り直しても二重にならない
    # 6. 仕事を借りて返す: 要約（LLM は模擬）・取り出し（模擬）・画像（作るのは模擬、送るのは本物）
    asked: list[str] = []

    async def fake_complete(model, messages, **kw):
        asked.append(model)
        prompt = messages[-1]["content"]
        if "JSON だけを返してください" in prompt:
            return '```json\n{"entities": [{"name": "GPT", "type": "model"}, {"name": "x", "type": "bogus"}], "facts": [{"about": "GPT", "kind": "release", "text": "新しい版が出た"}]}\n```'
        return "同じ出来事を三社が報じた。新しい合成の発表があり、詳細は各社の記事にまとめられている。"
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(imagegen, "available", lambda tier="small": True)
    made: list[dict] = []

    def runner(job):
        made.append(job)
        for item in job["items"]:
            (Path(job["out_dir"]) / f"{item['id']}.webp").write_bytes(webp())
        return [{"id": i["id"], "ok": True} for i in job["items"]]
    put_settings(source="auto", jobs={"enabled": True, "kinds": ["summarize_hq", "extract_retry", "image"], "max_per_run": 10, "interval_minutes": 15})
    import asyncio

    result = asyncio.run(hub_jobs.run_once(runner=runner))
    assert result["claimed"] == 3 and result["done"] == 3 and result["failed"] == 0, result
    assert made and made[0]["items"][0]["id"] > 0 and "wordless" in made[0]["items"][0]["prompt"] and set(asked) == {"auto"}      # 設定の「まとめ・一言要約のモデル」を使う
    jobs_state = hub_link.load()["jobs"]
    assert jobs_state["done"] == 3 and {r["kind"] for r in jobs_state["recent"]} == {"summarize_hq", "extract_retry", "image"}
    # 7. 次の同期で、要約・取り出しの結果・画像（ハブの中の道）が入る。画像は sha256 を確かめて写し、配る API から見える
    after = admin_client.post("/api/v1/news/hub/sync", json={}, headers=H).json()
    assert after["result"]["ok"] and after["result"]["images"]["done"] == 1
    with news_db.session() as db:
        assert "合成の" in (db.scalar(select(NewsHubEvent.summary)) or "")
    rep = [r for r in rows() if r.title == "合成の記事 0"][0]
    sha = hashlib.sha256(webp()).hexdigest()
    assert rep.image_url == f"{imagegen.HUB_URL_PREFIX}{sha}.webp"
    got = admin_client.get(rep.image_url)
    assert got.status_code == 200 and got.content == webp() and got.headers["content-type"] == "image/webp"
    assert admin_client.get(f"{imagegen.HUB_URL_PREFIX}../x.webp").status_code in (404, 422)
    listed = admin_client.get("/api/v1/news/items?view=latest&limit=10").json()["items"]
    assert any(i["image_ai"] and i["image_url"] == rep.image_url for i in listed)         # 画面の「AI 生成のイメージ」の印が付く
    # 8. ハブが答えない（宛先を閉じたポートへ）→ 2 回で自分で集める側へ。直すとハブへ戻る
    good = hub_link.load()["url"]
    hub_link.update(lambda s: s.update(url="http://127.0.0.1:9"))
    assert not hub_sync.sync_once()["ok"] and hub_sync.effective_source() == "hub" and not hub_sync.sync_once()["ok"] and hub_sync.effective_source() == "local"
    hub_link.update(lambda s: s.update(url=good))
    assert hub_sync.sync_once()["ok"] and hub_sync.effective_source() == "hub"
    # 9. ハブの画面で取り消されたら、認証エラーが画面に出る
    assert hub.admin.delete(f"/link/peers/{hub.admin.get('/link').json()['peers'][0]['id']}").status_code == 200
    failed = hub_sync.sync_once()
    assert not failed["ok"] and "認証" in failed["error"] and "認証" in admin_client.get("/api/v1/news/hub").json()["sync"]["last_error"]
    # 10. 切る: トークン・鍵・cursor を捨てる。取り込んだ記事は残る
    assert admin_client.delete("/api/v1/news/hub", headers=H).json()["linked"] is False
    text = hub_link._path().read_text(encoding="utf-8")  # noqa: SLF001
    assert '"token_enc": ""' in text and '"cursor": 0' in text and len(rows()) == 5
    # 全体を通して、トークン・OpenRouter の鍵はログにも応答にも出ていない
    assert token not in caplog.text and FAKE_KEY not in caplog.text


def test_mismatched_fingerprint_discards_the_pairing(hub, admin_client):
    r = admin_client.post("/api/v1/news/hub/pair", json={"url": hub.url, "code": hub.pairing()["code"]}, headers=H)
    assert r.status_code == 200 and hub_link.linked()
    no = admin_client.post("/api/v1/news/hub/confirm", json={"match": False}, headers=H).json()
    assert no["linked"] is False and not hub_link.ready() and '"token_enc": ""' in hub_link._path().read_text(encoding="utf-8")  # noqa: SLF001
