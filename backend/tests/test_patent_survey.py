"""特許の調査（P1）の最初の部分: 調査テーマ → 検索語の案 → 外へ送る語の確認。

目的:
- 案件と調査テーマ（空なら請求項 1）から、ローカル専用のモデルで検索語の案（構成要素ごとの ja/en・IPC・
  各取得先の検索式）を JSON で作り、judge と同じ専用 sqlite に保存すること。
- モデルの答えをそのまま信用しないこと: 読めない JSON・形違いは失敗として記録する。語は 1〜80 文字で
  前後の空白を取り、重複を除き、1 構成要素につき各 20 個まで。IPC は ^[A-H]\\d{2}[A-Z] ?\\d{1,4}/\\d{1,6}$ に
  合うものだけ（合わないものは捨てて、捨てた数を記録）。
- 未公開の内容の検出: 語・検索式が案件の請求項・ヒアリングの記録の文章と 8 文字以上重なっていたら
  （一部分が写されている場合も）risk を付け、外へ送る語の確認（approve）で選べないこと。
  確認（approved_public）の無い保存は断ること。
- 権限の無い利用者は API を使えないこと。案件を消したら案も消えること（案件番号は使い回される）。

根拠: docs/PLAN-web-collector.md P1「テーマ/文言 → 検索語の案（ローカル LLM）→ 外へ送る語の確認」、
同 §6「外へ出す検索語・URL は、未公開の発明の内容を含まないことを利用者が確かめたものだけ」。
2026-11-21 の作業指示（trial/instruction.md）。手本は judge.py（start → Job でローカル LLM → 出力を検めて
保存 → 人の確認）と watch.py（approved_public の決まり）で、同じ観点の既存テストは docs/test-catalog.md で
確認した（test_patent_judge.py は判定の引用検査、test_patent_watch.py は取得語の確認。本件は案の作成と
語ごとの risk 検出で、重複しない新しい観点）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.conftest import CSRF_HEADERS

API = "/api/v1/patents"
CLAIM = "【請求項１】\n薄膜状のゲート絶縁膜と、前記ゲート絶縁膜上のチャネル層と、を備える半導体装置。"
CLAIM_BODY = "薄膜状のゲート絶縁膜と、前記ゲート絶縁膜上のチャネル層と、を備える半導体装置。"   # 見出しを除いた請求項 1
HEARING = "発明者が「パワーアンプの帰還回路」を小型にしたいと話していた。"

# モデルの答えの見本: 案の語（請求項・ヒアリングに含まれるもの、長すぎるもの、重複、形の合わない IPC、
# 20 個を超える分）と検索式（請求項の言葉を含むもの）を混ぜる。
ANSWER = {"concepts": [
    {"name": "絶縁膜", "ja": ["薄膜状のゲート絶縁膜", "  ゲート絶縁膜 ", "絶縁膜", "ゲート絶縁膜"],
     "en": ["gate insulating film", "GaN" * 30],
     "ipc": ["H01L 21/02", "H01L21/02", "規格 X1", "G06F 16/33"]},
    {"name": "アンプ", "ja": ["パワーアンプの帰還回路"] + [f"語{i}" for i in range(25)],
     "en": [123, "y" * 80], "ipc": []},
], "queries": {"epo": 'ti="薄膜状のゲート絶縁膜"', "uspto": "gate insulating film",
               "openalex": "H01L 21/02"}}


def _case(title="調査の試験", hearing=HEARING, claims=CLAIM):
    from app.patents import store as pstore

    case = pstore.create_case(title, data={"hearing": hearing} if hearing else {})
    if claims:
        pstore.save_claims(case["id"], claims)
    return case["id"]


class Job:
    id, status = "job-survey", "running"

    def set_progress(self, *args):
        pass


def _fake_model(monkeypatch, answer, sent):
    """LLM を偽物にする（test_patent_judge.py と同じ方法: 呼び出し側の関数を差し替えるだけ）。"""
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


def test_valid_json_is_cleaned_saved_and_unpublished_terms_flagged(monkeypatch):
    from app.patents import survey

    case_id = _case()
    sent = {}
    _fake_model(monkeypatch, ANSWER, sent)
    survey_id = survey.start(case_id, "")                      # テーマが空なら請求項 1 を使う
    asyncio.run(survey.run(Job(), case_id, survey_id))
    out = survey.get(case_id, survey_id)
    assert sent["local_only"] is True and "薄膜状のゲート絶縁膜" in sent["prompt"]
    assert out["status"] == "succeeded" and out["theme"] == CLAIM_BODY
    first = out["concepts"][0]
    assert first["ja"] == ["薄膜状のゲート絶縁膜", "ゲート絶縁膜", "絶縁膜"]       # 空白を取り重複を除く
    assert first["en"] == ["gate insulating film"]                              # 81 文字以上は捨てる
    assert first["ipc"] == ["H01L 21/02", "H01L21/02", "G06F 16/33"]            # IPC は形の合うものだけ
    second = out["concepts"][1]
    assert len(second["ja"]) == 20 and second["ja"][0] == "パワーアンプの帰還回路"  # 1 構成要素につき 20 個まで
    assert second["en"] == ["y" * 80]                                           # 80 文字までは残す
    assert out["dropped"] == {"terms": 9, "ipc": 1}                            # 捨てた数を記録
    assert out["risks"]["薄膜状のゲート絶縁膜"] == "unpublished"                   # 請求項に 8 文字以上で含まれる
    assert out["risks"]["パワーアンプの帰還回路"] == "unpublished"                  # ヒアリングに含まれる
    assert out["risks"]['ti="薄膜状のゲート絶縁膜"'] == "unpublished"              # 検索式も同じ検査
    assert "gate insulating film" not in out["risks"]
    assert "ゲート絶縁膜" not in out["risks"]                                    # 8 文字未満は対象外


def test_unparsable_or_wrong_shape_answers_fail_the_survey(monkeypatch):
    from app.patents import survey

    case_id = _case("壊れた答え")
    for answer, why in (("すみません。JSON では出せません。", "JSON"),
                        (json.dumps({"rows": []}), "concepts"),
                        (json.dumps({"concepts": [], "queries": "検索式"}), "queries")):
        _fake_model(monkeypatch, answer, {})
        survey_id = survey.start(case_id, "テーマ")
        with pytest.raises(ValueError):
            asyncio.run(survey.run(Job(), case_id, survey_id))
        out = survey.get(case_id, survey_id)
        assert out["status"] == "failed" and out["concepts"] == [] and out["risks"] == {}
        assert why in out["error"]


def test_theme_validation_and_claim1_fallback():
    from app.patents import store as pstore
    from app.patents import survey

    case_id = _case("テーマの確認")
    assert survey.get(case_id, survey.start(case_id, "明示テーマ"))["theme"] == "明示テーマ"
    with pytest.raises(pstore.PatentError, match="2000"):
        survey.start(case_id, "x" * 2001)
    empty = pstore.create_case("請求項の無い案件")
    with pytest.raises(pstore.PatentError, match="請求項"):
        survey.start(empty["id"], "")


def test_partially_copied_terms_are_also_flagged(monkeypatch):
    """語が請求項の一部を 8 文字以上写していても（全体としては含まれなくても）risk が付くこと。
    根拠: 2026-11-21 のレビュー（major）: 語は全体の包含・検索式は 8 文字の窓で判定が食い違っていて、
    一部だけ写した語が確認を通り抜けられた。語にも同じ窓検査を適用して閉じる。"""
    from app.patents import survey

    case_id = _case("一部だけ写した語")
    answer = {"concepts": [{"name": "絶縁膜", "ja": ["薄膜状のゲート絶縁膜であって"], "en": [], "ipc": []}],
              "queries": {"epo": "", "uspto": "", "openalex": ""}}
    _fake_model(monkeypatch, answer, {})
    survey_id = survey.start(case_id, "テーマ")
    asyncio.run(survey.run(Job(), case_id, survey_id))
    out = survey.get(case_id, survey_id)
    assert out["risks"]["薄膜状のゲート絶縁膜であって"] == "unpublished"   # 請求項の「〜絶縁膜と、」の一部を写す


def test_approve_needs_public_flag_refuses_risky_and_unknown_terms(monkeypatch):
    from app.patents import store as pstore
    from app.patents import survey

    case_id = _case("確認の試験")
    _fake_model(monkeypatch, ANSWER, {})
    survey_id = survey.start(case_id, "")
    asyncio.run(survey.run(Job(), case_id, survey_id))

    pending = survey.start(case_id, "まだ終わっていない")
    with pytest.raises(pstore.PatentError, match="まだ"):          # 出来上がる前の案は確認できない
        survey.approve(case_id, pending, [], approved_public=True)

    safe = ["gate insulating film", "H01L 21/02"]
    with pytest.raises(pstore.PatentError, match="確認"):            # approved_public が無い
        survey.approve(case_id, survey_id, safe, approved_public=False)
    with pytest.raises(pstore.PatentError, match="未公開"):           # risk の付いた語
        survey.approve(case_id, survey_id, ["薄膜状のゲート絶縁膜", *safe], approved_public=True)
    with pytest.raises(pstore.PatentError, match="案"):               # 案に無い語
        survey.approve(case_id, survey_id, ["手で作った語"], approved_public=True)
    out = survey.approve(case_id, survey_id, safe + ["H01L 21/02"], approved_public=True)
    assert out["approved"] == ["gate insulating film", "H01L 21/02"]  # 選んだ語だけ（重複を除く）
    assert out["approved_public"] and out["approved_at"]
    again = survey.approve(case_id, survey_id, ["絶縁膜"], approved_public=True)
    assert again["approved"] == ["絶縁膜"]                            # 選び直したら置き換わる


def test_survey_api_start_list_and_detail(admin_client, monkeypatch):
    from app.patents import service

    case_id = admin_client.post(f"{API}/cases", json={"title": "API の試験", "data": {"hearing": HEARING}},
                                headers=CSRF_HEADERS).json()["id"]
    admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": CLAIM}, headers=CSRF_HEADERS)

    async def unavailable():
        raise service.LocalAIUnavailable("ローカル専用のモデルが使えません")

    monkeypatch.setattr(service, "resolve_local_target", unavailable)
    r = admin_client.post(f"{API}/cases/{case_id}/surveys", json={"theme": ""}, headers=CSRF_HEADERS)
    assert r.status_code == 503                                   # ローカルが無ければ始まらない
    assert admin_client.get(f"{API}/cases/{case_id}/surveys").json()["surveys"] == []

    _fake_model(monkeypatch, ANSWER, {})
    r = admin_client.post(f"{API}/cases/{case_id}/surveys", json={"theme": "ワイドギャップ半導体"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 201 and set(r.json()) >= {"survey_id", "job_id"}
    survey_id = r.json()["survey_id"]
    listed = admin_client.get(f"{API}/cases/{case_id}/surveys").json()["surveys"]
    assert len(listed) == 1 and listed[0]["id"] == survey_id
    detail = admin_client.get(f"{API}/cases/{case_id}/surveys/{survey_id}")
    assert detail.status_code == 200 and detail.json()["theme"] == "ワイドギャップ半導体"
    assert admin_client.get(f"{API}/cases/{case_id}/surveys/{survey_id + 999}").status_code == 404
    assert admin_client.get(f"{API}/cases/{case_id + 999}/surveys").status_code == 404


def test_survey_api_approve_flow(admin_client, monkeypatch):
    from app.patents import survey

    case_id = admin_client.post(f"{API}/cases", json={"title": "確認 API"}, headers=CSRF_HEADERS).json()["id"]
    admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": CLAIM}, headers=CSRF_HEADERS)
    _fake_model(monkeypatch, ANSWER, {})
    survey_id = survey.start(case_id, "")
    asyncio.run(survey.run(Job(), case_id, survey_id))

    url = f"{API}/cases/{case_id}/surveys/{survey_id}/approve"
    r = admin_client.post(url, json={"terms": ["gate insulating film"], "approved_public": False},
                          headers=CSRF_HEADERS)
    assert r.status_code == 422 and "確認" in r.json()["detail"]
    r = admin_client.post(url, json={"terms": ["薄膜状のゲート絶縁膜"], "approved_public": True}, headers=CSRF_HEADERS)
    assert r.status_code == 422 and "未公開" in r.json()["detail"]
    r = admin_client.post(url, json={"terms": ["gate insulating film"], "approved_public": True},
                          headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["approved"] == ["gate insulating film"]


def test_viewer_cannot_read_or_approve_surveys(client):
    from app.database import SessionLocal
    from app.models import Role, User
    from app.patents import store as pstore
    from app.security.passwords import hash_password
    from sqlalchemy import select

    case_id = pstore.create_case("権限の試験")["id"]
    with SessionLocal() as db:
        role = db.execute(select(Role).where(Role.name == "viewer")).scalar_one()
        if db.execute(select(User).where(User.username == "ro-survey")).scalar_one_or_none() is None:
            db.add(User(username="ro-survey", password_hash=hash_password("viewer-pass-123"), role_id=role.id))
            db.commit()
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": "ro-survey", "password": "viewer-pass-123"},
                    headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert client.get(f"{API}/cases/{case_id}/surveys").status_code == 403        # 閲覧も不可
    assert client.post(f"{API}/cases/{case_id}/surveys", json={"theme": "x"},
                       headers=CSRF_HEADERS).status_code == 403
    assert client.post(f"{API}/cases/{case_id}/surveys/1/approve",
                       json={"terms": [], "approved_public": True}, headers=CSRF_HEADERS).status_code == 403
    client.cookies.clear()


def test_deleting_a_case_removes_surveys_and_reboot_interrupts_running(monkeypatch):
    from app.patents import store as pstore
    from app.patents import survey

    case_id = _case("消す案件")
    _fake_model(monkeypatch, ANSWER, {})
    survey_id = survey.start(case_id, "")
    asyncio.run(survey.run(Job(), case_id, survey_id))
    survey._update(survey_id, status="running")               # 再起動で止まった状態を再現
    assert survey.interrupt_unfinished() >= 1
    assert survey.get(case_id, survey_id)["status"] == "interrupted"
    pstore.delete_case(case_id)                              # 案件番号は使い回されるので残してはいけない
    conn = survey.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM patent_surveys WHERE case_id=?",
                            (case_id,)).fetchone()[0] == 0
    finally:
        conn.close()
