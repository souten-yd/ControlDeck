"""Patent 機能（特許調査・検討）と、PatentBuddy Add-on の撤去。

目的:
- 案件・出来事・請求項の版・先行文献・クレームチャート・AI 支援の記録が API から使えること。
- 期限の目安と構成要件分解が ResearchDataHub から移植したとおりに動くこと。
- AI 支援はローカル専用のモデルでだけ動き、検証できなければ 503 で止まり、外部へ回さないこと（アイデアの流出防止）。
- AI の答えは「モデルの推論・未確認」として保存されること。
- PatentBuddy は機能追加のカタログに出ず、導入済みなら起動時に外れ、データ（feature-data）は残ること。

根拠: 2026-11-21 利用者「PatentBuddy のアドオンは不要。アドオンと機能追加画面の導入項目を一式削除し、
特許調査検討用ツールである Patent 機能をつけて」。docs/PLAN-knowledge-platform.md（K-PR3）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from tests.conftest import CSRF_HEADERS

API = "/api/v1/patents"


def test_deadlines_and_claim_elements_follow_japanese_practice():
    from app.patents import domain

    today = dt.date(2026, 11, 21)
    out = {d["key"]: d for d in domain.deadlines([{"kind": "filing", "date": "2026-01-31"}], today=today)}
    assert out["exam_request"]["due"] == "2029-01-31"       # 出願日から 3 年
    assert out["publication"]["due"] == "2027-07-31"        # 18 か月
    oa = domain.deadlines([{"kind": "oa_dispatch", "date": "2026-11-01"}], foreign_resident=True, today=today)
    assert oa[0]["due"] == "2027-01-30"                       # 在外者は 90 日
    elements = domain.claim_elements("電力を変換する装置であって、スイッチング素子と、制御部と、を備える電力変換装置。")
    assert [e["label"] for e in elements][:3] == ["前提", "A", "B"]


def test_case_lifecycle_over_api(admin_client):
    r = admin_client.post(f"{API}/cases", json={"title": "低損失インバータ"}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    case_id = r.json()["id"]
    r = admin_client.patch(f"{API}/cases/{case_id}", json={"stage": "prior_art", "data": {"problem": "損失", "evil": "x"}},
                           headers=CSRF_HEADERS)
    assert r.json()["stage"] == "prior_art" and "evil" not in r.json()["data"]
    r = admin_client.post(f"{API}/cases/{case_id}/events", json={"kind": "filing", "date": "2026-01-31"}, headers=CSRF_HEADERS)
    assert any(d["key"] == "exam_request" for d in r.json()["deadlines"])
    claims = "【請求項１】\nスイッチング素子と、制御部と、を備えるインバータ。\n【請求項２】\n前記制御部がＰＷＭ制御する請求項１のインバータ。"
    admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": claims}, headers=CSRF_HEADERS)
    r = admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": claims}, headers=CSRF_HEADERS)
    assert len(r.json()["claims"]) == 1                 # 同じ内容では版を増やさない
    assert [c["no"] for c in r.json()["claim_elements"]] == [1, 2]
    r = admin_client.post(f"{API}/cases/{case_id}/refs", json={"ref": "特開2026-000001", "category": "X"}, headers=CSRF_HEADERS)
    assert r.json()["refs"][0]["category"] == "X"
    assert admin_client.post(f"{API}/cases/{case_id}/refs", json={"ref": "x", "category": "Z"},
                             headers=CSRF_HEADERS).status_code == 422
    assert admin_client.post(f"{API}/cases/{case_id}/refs", json={"doc_ref": "../etc:1", "category": "A"},
                             headers=CSRF_HEADERS).status_code == 422
    listed = admin_client.get(f"{API}/cases").json()
    assert listed[0]["id"] == case_id and listed[0]["stage_label"] == "先行技術調査"
    assert admin_client.delete(f"{API}/cases/{case_id}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.get(f"{API}/cases/{case_id}").status_code == 404


def test_search_and_claim_chart_use_the_shared_knowledge(admin_client, monkeypatch):
    from app.knowledge import store
    from app.patents import service

    async def no_vector(question):
        return None, ""

    monkeypatch.setattr(service, "_query_vector", no_vector)
    if store.find_collection("pat-lit-test") is None:
        store.create_collection("pat-lit-test", shard="patents", kind="patent")
    store.add_document("pat-lit-test", [store.NewChunk("スイッチング素子を窒化ガリウムで構成したインバータ")], title="公報A")
    hits = admin_client.post(f"{API}/search", json={"query": "スイッチング素子"}, headers=CSRF_HEADERS).json()["results"]
    assert hits and hits[0]["doc_ref"].startswith("patents:")
    case_id = admin_client.post(f"{API}/cases", json={"title": "GaN インバータ"}, headers=CSRF_HEADERS).json()["id"]
    admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": "【請求項１】\nスイッチング素子と、制御部と、を備えるインバータ。"},
                      headers=CSRF_HEADERS)
    chart = admin_client.post(f"{API}/cases/{case_id}/claim-chart", json={"claim_no": 1}, headers=CSRF_HEADERS).json()
    assert chart["rows"][0]["element"]["label"] == "A" and chart["rows"][0]["evidence"]
    assert admin_client.post(f"{API}/cases/{case_id}/claim-chart", json={"claim_no": 9},
                             headers=CSRF_HEADERS).status_code == 422


def test_assist_refuses_without_a_verified_local_model(admin_client, monkeypatch):
    from app.patents import service

    async def unavailable():
        raise service.LocalAIUnavailable("ローカル専用のモデルが使えません")

    monkeypatch.setattr(service, "resolve_local_target", unavailable)
    case_id = admin_client.post(f"{API}/cases", json={"title": "未公開の発明"}, headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"{API}/cases/{case_id}/assists", json={"task": "novelty"}, headers=CSRF_HEADERS)
    assert r.status_code == 503 and "ローカル" in r.json()["detail"]
    assert admin_client.get(f"{API}/cases/{case_id}").json()["assists"] == []
    assert admin_client.get(f"{API}/meta").json()["local_ai_available"] is False


def test_assist_runs_local_only_and_is_saved_as_unreviewed_inference(monkeypatch):
    from app.addon_runtime import ai
    from app.patents import service
    from app.patents import store as pstore

    sent = {}

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def fake_complete(target, request, http_request, require_provenance=False):
        sent["local_only"] = request.local_only
        sent["messages"] = request.messages
        assert await http_request.is_disconnected() is False
        return "## 新規性\n- 文献 [1] は構成 B を開示していない（推論）", None

    from app.knowledge import context as kcontext

    async def built(question, *, consumer, budget_chars, bound=(), names=None, max_docs=8):
        sent["consumer"] = consumer
        return kcontext.Built("[D1] 公報A\n本文", "full",
                              [{"label": "D1", "ref": "patents:1", "title": "公報A", "collection": "c"}]), []

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", fake_complete)
    monkeypatch.setattr(kcontext, "search_and_build", built)

    class Job:
        id, status = "job-1", "running"

        def set_progress(self, *args):
            pass

    case = pstore.create_case("AI 支援の試験", {"problem": "損失が大きい"})
    assist_id = pstore.start_assist(case["id"], "novelty")
    asyncio.run(service.run_assist(Job(), case["id"], "novelty", assist_id))
    saved = pstore.get_case(case["id"])["assists"][0]
    assert sent["local_only"] is True
    assert "損失が大きい" in sent["messages"][1]["content"] and "[D1] 公報A" in sent["messages"][1]["content"]
    assert sent["consumer"] == "local"
    assert saved["status"] == "succeeded" and saved["basis"] == "model_inference" and saved["review_status"] == "unreviewed"
    assert saved["evidence"][0]["doc_ref"] == "patents:1"


def test_patentbuddy_is_gone_from_the_catalog_and_retired_when_installed(tmp_path, monkeypatch):
    from app.features import registry, release_bundle

    assert "patent-buddy" not in json.loads(registry.Path(registry.__file__).with_name("trusted-catalog.json").read_text())
    assert "patent-buddy" not in registry.KNOWN_FEATURES
    calls = []
    monkeypatch.setattr(release_bundle, "uninstall", lambda fid, spec: calls.append((fid, spec["addon_id"])))
    root = registry._features_root() / "patent-buddy"
    root.mkdir(parents=True, exist_ok=True)
    keep = registry.data_dir() / "feature-data" / "patent-buddy"
    keep.mkdir(parents=True, exist_ok=True)
    (keep / "analysis-jobs.json").write_text("[]")
    assert registry.retire_removed_features() == ["patent-buddy"]
    assert calls == [("patent-buddy", "patent-buddy")]
    assert (root / registry.RETIRED_MARK).exists() and (keep / "analysis-jobs.json").exists()
    assert registry.retire_removed_features() == []      # 2 回目は何もしない


@pytest.mark.parametrize("status", ["queued", "running"])
def test_assists_cut_by_restart_are_marked_interrupted(status):
    from app.patents import store as pstore

    case = pstore.create_case("再起動の試験")
    assist_id = pstore.start_assist(case["id"], "idea")
    pstore.finish_assist(assist_id, status=status)
    assert pstore.interrupt_unfinished() >= 1
    assert pstore.get_case(case["id"])["assists"][0]["status"] == "interrupted"
