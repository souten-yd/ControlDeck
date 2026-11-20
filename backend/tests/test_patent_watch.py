"""公開データの取得・特許ウォッチ・期限の通知（統合後の再設計 R3-2）。

目的:
- 外へ送る検索語は利用者が「公開してよい」と確認したものだけ。確認の無い取得・ウォッチは動かさず、検索語を変えたら確認をやり直す。
- 取得は https の公開アドレスだけ。内部アドレス・リダイレクトを拒む（SSRF）。鍵は暗号化して保存し、応答に出さない。
- EPO OPS の書誌を統合ストアの書誌の形（meta["biblio"]）にそろえる。
- ウォッチは新しく入った文献だけをヒットにし、2 回目は同じ文献を出さない。ヒットから X/Y/A で先行文献に登録できる。
- 期限の通知はしきい値ごとに 1 回だけで、本文に案件名を書かない（通知は外部のサーバーを通る）。

根拠: ResearchDataHub v0.2 にあった機能（watch.py・notify.py・connectors.py）で、v0.102 の統合で抜けていた。
2026-11-21 利用者「各リポジトリの計画を ControlDeck 側に反映して進める」。docs/PLAN-knowledge-platform.md §10。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import shutil

import pytest

from app.config import data_dir
from tests.conftest import CSRF_HEADERS


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store
    from app.patents import watch

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    (data_dir() / "knowledge-sources.json").unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    watch._ready.clear()
    yield


class Job:
    id, status = "job-watch", "running"

    def set_progress(self, *args):
        pass


EPO = {"ops:world-patent-data": {"ops:biblio-search": {"ops:search-result": {"exchange-documents": [
    {"exchange-document": {"@country": "JP", "@doc-number": "2026116114", "@kind": "A", "bibliographic-data": {
        "invention-title": [{"@lang": "ja", "$": "半導体装置"}, {"@lang": "en", "$": "SEMICONDUCTOR DEVICE"}],
        "parties": {"applicants": {"applicant": [{"@data-format": "epodoc", "applicant-name": {"name": {"$": "RENESAS"}}}]}},
        "classifications-ipcr": {"classification-ipcr": [{"text": {"$": "H10D  89/60   A I"}}]},
        "publication-reference": {"document-id": [{"date": {"$": "20260709"}}]}},
        "abstract": {"p": {"$": "電源保護回路"}}}}]}}}}


def test_epo_records_use_the_shared_biblio_shape():
    from app.knowledge import sources

    rec = sources.epo_docs(EPO)[0]
    assert rec["ext_id"] == "JP2026116114A" and rec["title"] == "半導体装置" and rec["kind"] == "patent"
    assert rec["biblio"]["applicants"] == ["RENESAS"] and rec["biblio"]["publication_date"] == "2026-07-09"
    assert rec["biblio"]["ipc"][0].startswith("H10D 89/60")


def test_internal_addresses_plain_http_and_redirects_are_refused():
    from app.knowledge import sources

    for url in ("http://example.com/feed", "https://127.0.0.1/feed", "https://10.0.0.5/x"):
        with pytest.raises(sources.SourceError):
            sources._public_host(url)
    with pytest.raises(sources.SourceError):
        sources._NoRedirect().redirect_request(None, None, 302, "", {}, "https://127.0.0.1/")


def test_keys_are_encrypted_and_never_returned():
    from app.knowledge import sources

    sources.set_keys({"epo_key": "my-key-123", "epo_secret": "my-secret-456"})
    raw = (data_dir() / "knowledge-sources.json").read_text()
    assert "my-key-123" not in raw and "my-secret-456" not in raw
    listed = {s["id"]: s for s in sources.listing()}
    assert listed["epo"]["ready"] and not listed["uspto"]["ready"] and "my-key" not in str(listed)
    with pytest.raises(sources.SourceError):
        sources.set_keys({"unknown": "x"})


def _case_with_claims():
    from app.patents import store as pstore

    case = pstore.create_case("電源保護回路")
    pstore.save_claims(case["id"], "【請求項１】電源端子と、電源保護回路と、を備える半導体装置。")
    return case["id"]


def test_watch_needs_public_approval_and_only_new_documents_become_hits(monkeypatch):
    from app.knowledge import ingest, sources
    from app.patents import store as pstore
    from app.patents import watch

    async def no_embed(chunks, collection):
        return "", False

    monkeypatch.setattr(ingest, "_embed", no_embed)
    sent = []
    monkeypatch.setattr(sources, "fetch", lambda source, query, limit: sent.append(query) or sources.epo_docs(EPO))
    case_id = _case_with_claims()
    unapproved = watch.save_watch(case_id, source="epo", query="ic=H10D", interval_hours=24, approved_public=False)
    with pytest.raises(pstore.PatentError):
        asyncio.run(watch.run_watch(Job(), unapproved["id"]))
    assert sent == [] and unapproved["id"] not in watch.due_watches()
    approved = watch.save_watch(case_id, source="epo", query="ic=H10D", interval_hours=24, approved_public=True,
                                watch_id=unapproved["id"])
    assert approved["approved_public"] and approved["id"] in watch.due_watches()
    first = asyncio.run(watch.run_watch(Job(), approved["id"]))
    second = asyncio.run(watch.run_watch(Job(), approved["id"]))
    assert (first["new"], second["new"]) == (1, 0) and sent == ["ic=H10D", "ic=H10D"]
    hits = watch.hits(case_id)
    assert len(hits) == 1 and hits[0]["title"] == "半導体装置"
    watch.decide_hit(case_id, hits[0]["id"], "registered", "X")
    assert pstore.get_case(case_id)["refs"][0]["category"] == "X"
    changed = watch.save_watch(case_id, source="epo", query="ic=H02M", interval_hours=24, approved_public=False,
                               watch_id=approved["id"])
    assert not changed["approved_public"]       # 検索語を変えたら確認をやり直す


def test_deadline_notices_once_per_threshold_without_case_names(monkeypatch):
    from app.maintenance import auto_update
    from app.patents import store as pstore
    from app.patents import watch

    case = pstore.create_case("極秘の新型インバータ")
    filing = (dt.date.today() - dt.timedelta(days=365 * 3 - 20)).isoformat()   # 審査請求の期限まで約 20 日
    pstore.add_event(case["id"], "filing", filing)
    sent = []

    async def notify(title, message):
        sent.append(title + message)

    monkeypatch.setattr(auto_update, "notify", notify)
    first = asyncio.run(watch.tick())
    assert first["notices"] >= 1 and sent and "極秘" not in sent[0] and "インバータ" not in sent[0]
    assert asyncio.run(watch.tick())["notices"] == 0        # 同じ日・同じしきい値では 2 度知らせない


def test_api_refuses_unapproved_queries(admin_client):
    case_id = admin_client.post("/api/v1/patents/cases", json={"title": "x"}, headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/patents/cases/{case_id}/watches", json={"source": "epo", "query": "ic=H02M"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 422 and "確認" in r.json()["detail"]
    r = admin_client.post("/api/v1/patents/sources/openalex/fetch", json={"query": "inverter"}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    listed = admin_client.get("/api/v1/patents/sources").json()["sources"]
    assert {s["id"] for s in listed} == {"epo", "uspto", "openalex", "rss", "gdelt"}
