"""AI クレームチャートの判定（R3-3）。

目的:
- 構成要件ごとの ○/△/×/? を、ローカル専用のモデルでだけ作ること（ローカルが無ければ 503 で止まり、記録も作らない）。
- ○/△ の引用が原文に無ければ「?」に落とすこと（モデルの作った引用を根拠に残さない）。OCR の改行・全角半角の揺れは許すこと。
- モデルが判定しなかった構成要件も「?」で必ず行に出すこと。
- 人が行の判定を直せ、直したら未確認へ戻ること。確認済み・却下の状態を持つこと。
- 請求項の版・文献の原文が変わったら stale を出すこと。案件を消したら判定も消えること。
- AI に渡せない文献（規格の利用条件など）は判定を始めないこと。

根拠: docs/PLAN-knowledge-platform.md §10 R3-3「引用が原文に無ければ ? に落とす」。PatentBuddy Draft #10。
"""
from __future__ import annotations

import asyncio
import json

from tests.conftest import CSRF_HEADERS

API = "/api/v1/patents"
CLAIM = "【請求項１】\nスイッチング素子と、前記スイッチング素子を駆動する制御部と、冷却器と、を備えるインバータ。"
DOC = ("【０００１】本発明はインバータに関する。\n【００１０】スイッチング素子は窒化ガリウムで構成され、"
       "制御部がゲート信号を出力して\nスイッチング素子を駆動する。")


def _setup(title="判定の試験"):
    from app.knowledge import store
    from app.patents import store as pstore

    if store.find_collection("judge-lit") is None:
        store.create_collection("judge-lit", shard="patents", kind="patent")
    doc_id = store.add_document("judge-lit", [store.NewChunk(DOC)], title="公報J", body=DOC, ext_id=f"judge:{title}", force=True)
    ref = f"patents:{doc_id['doc_id']}"
    case = pstore.create_case(title)
    pstore.save_claims(case["id"], CLAIM)
    return case["id"], ref


class Job:
    id, status = "job-j", "running"

    def set_progress(self, *args):
        pass


def _fake_model(monkeypatch, answer: dict | str, sent: dict):
    from app.addon_runtime import ai
    from app.patents import service

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def fake_complete(target, request, http_request, require_provenance=False):
        sent["local_only"] = request.local_only
        sent["prompt"] = request.messages[1]["content"]
        return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), None

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", fake_complete)


def test_fabricated_quotes_fall_to_question_mark_and_missing_elements_are_listed(monkeypatch):
    from app.patents import judge

    case_id, ref = _setup()
    sent = {}
    _fake_model(monkeypatch, "```json\n" + json.dumps({"rows": [
        # 改行と全角半角が違っても本物の引用なら ○ のまま
        {"label": "A", "verdict": "○", "quote": "スイッチング素子は窒化ガリウムで構成され", "where": "【0010】", "reason": "開示"},
        # 原文に無い引用は ? へ
        {"label": "B", "verdict": "○", "quote": "制御部はPWM信号でスイッチング素子を高速に駆動する", "reason": "開示"},
        # × は引用なしでよい
        {"label": "C", "verdict": "×", "reason": "冷却器の記載は無い"},
    ]}, ensure_ascii=False) + "\n```", sent)
    chart_id = judge.start(case_id, 1, ref)
    out = asyncio.run(judge.run(Job(), case_id, chart_id))
    chart = judge.get(case_id, chart_id)
    rows = {r["label"]: r for r in chart["rows"]}
    assert sent["local_only"] is True and "公報J" in sent["prompt"] and "窒化ガリウム" in sent["prompt"]
    assert chart["status"] == "succeeded" and chart["context_mode"] == "full" and chart["basis"] == "model_inference"
    assert rows["A"]["verdict"] == "○" and rows["A"]["quote_verified"] is True
    assert rows["B"]["model_verdict"] == "○" and rows["B"]["verdict"] == "?" and "原文に見つからない" in rows["B"]["flag"]
    assert rows["C"]["verdict"] == "×"
    assert out["demoted"] == 1
    assert chart["review_status"] == "unreviewed"


def test_quote_check_tolerates_ocr_line_breaks_and_width():
    from app.patents import judge

    source = judge._flat("制御部がゲート信号を出力して\nスイッチング素子を駆動する。ＧａＮ")
    assert judge.quote_found("制御部がゲート信号を出力してスイッチング素子を駆動する", source)
    assert judge.quote_found("「ゲート信号を出力して」…「素子を駆動する」", source)
    assert not judge.quote_found("駆動", source)                       # 短すぎる引用は根拠にしない
    assert not judge.quote_found("制御部がPWM信号を出力して", source)


def test_unparsable_answer_fails_the_chart_without_inventing_rows(monkeypatch):
    from app.patents import judge

    case_id, ref = _setup("壊れた答え")
    _fake_model(monkeypatch, "判定できませんでした", {})
    chart_id = judge.start(case_id, 1, ref)
    try:
        asyncio.run(judge.run(Job(), case_id, chart_id))
    except ValueError:
        pass
    chart = judge.get(case_id, chart_id)
    assert chart["status"] == "failed" and chart["rows"] == [] and "JSON" in chart["error"]


def test_human_review_and_stale_when_claims_or_document_change(monkeypatch):
    from app.knowledge import store
    from app.patents import judge
    from app.patents import store as pstore

    case_id, ref = _setup("人の確認")
    _fake_model(monkeypatch, {"rows": [{"label": "A", "verdict": "×"}, {"label": "B", "verdict": "×"},
                                       {"label": "C", "verdict": "×"}]}, {})
    chart_id = judge.start(case_id, 1, ref)
    asyncio.run(judge.run(Job(), case_id, chart_id))
    judge.review(case_id, chart_id, "checked")
    chart = judge.set_row(case_id, chart_id, "A", "○", "【0010】に記載")
    row = chart["rows"][0]
    assert row["human_verdict"] == "○" and row["model_verdict"] == "×" and chart["review_status"] == "unreviewed"
    assert judge.list_charts(case_id)[0]["stale"] == []
    pstore.save_claims(case_id, CLAIM.replace("冷却器", "放熱器"))
    assert "請求項が新しい版になりました" in judge.list_charts(case_id)[0]["stale"]
    store.add_document("judge-lit", [store.NewChunk(DOC + "追記")], title="公報J", body=DOC + "追記", ext_id="judge:人の確認", force=True)
    stale = judge.list_charts(case_id)[0]["stale"]
    assert any("文献" in s for s in stale)
    pstore.delete_case(case_id)
    conn = judge.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM patent_charts WHERE case_id=?", (case_id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_api_refuses_without_local_model_and_for_documents_ai_may_not_read(admin_client, monkeypatch):
    from app.knowledge import store
    from app.patents import service

    case_id, ref = _setup("API の試験")

    async def unavailable():
        raise service.LocalAIUnavailable("ローカル専用のモデルが使えません")

    monkeypatch.setattr(service, "resolve_local_target", unavailable)
    r = admin_client.post(f"{API}/cases/{case_id}/charts", json={"claim_no": 1, "doc_ref": ref}, headers=CSRF_HEADERS)
    assert r.status_code == 503
    assert admin_client.get(f"{API}/cases/{case_id}/charts").json()["charts"] == []

    async def local():
        return object()

    monkeypatch.setattr(service, "resolve_local_target", local)
    if store.find_collection("judge-std") is None:
        store.create_collection("judge-std", shard="standards", kind="standard")
    std_id = store.add_document("judge-std", [store.NewChunk("規格の本文")], title="購入した規格", ai_use="prohibited")
    r = admin_client.post(f"{API}/cases/{case_id}/charts", json={"claim_no": 1, "doc_ref": f"standards:{std_id['doc_id']}"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 422 and "AI に渡せません" in r.json()["detail"]
    r = admin_client.post(f"{API}/cases/{case_id}/charts", json={"claim_no": 7, "doc_ref": ref}, headers=CSRF_HEADERS)
    assert r.status_code == 422
