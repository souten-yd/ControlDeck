"""外部の検索サービスへ送る前の自動の検査（手動の確認 approved_public の置き換え）。

目的:
- 検索語・検索式が案件の未公開の文章（請求項・claims_draft・hearing）と **連続で 25 文字以上** 一致するときだけ止め（422・理由・
  重なった部分は 20 字まで・言い換えの案）、キーワード・短い句は止めないこと。境目（24 字は通る・25 字は止まる）。
- 入口を全部覆うこと: 取得の手動実行（案件が無ければ全案件と照らす）・ウォッチの保存と実行のたび（止めたら last_status に理由）。
- approved_public が無くても動くこと。監査には長さと理由だけを残し、検索語の本文を残さないこと。

根拠: 2026-11-22 利用者「ユーザー側に余計な操作はさせずに自動で適用」「特許の検索自体はインターネット」
「制限で検索が不十分になることは避けたい」。検索語は請求項から取る言葉なので、8 文字の重なりで止めると
「冷却ファンの回転数」のような普通のキーワードまで止まる。backend/app/patents/egress.py。
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from app.config import data_dir
from tests.conftest import CSRF_HEADERS

API = "/api/v1/patents"
# 25 文字ちょうどの請求項の一文（句読点なし）と、その前後に別の文章
SENT25 = "冷却ファンの回転数を温度に応じて制御する制御部を備えること"[:25]
assert len(SENT25) == 25
CLAIM = f"【請求項１】{SENT25}を特徴とする電源装置。"


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store
    from app.patents import watch

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    watch._ready.clear()
    yield


class Job:
    id, status = "job-egress", "running"

    def set_progress(self, *args):
        pass


def _case(title="未公開の案件", claims=CLAIM, hearing=""):
    from app.patents import store as pstore

    case = pstore.create_case(title, data={"hearing": hearing} if hearing else {})
    if claims:
        pstore.save_claims(case["id"], claims)
    return case["id"]


def test_keywords_pass_and_the_boundary_is_25_chars():
    from app.patents import egress

    case_id = _case()
    for ok in ("冷却ファンの回転数", "冷却ファン 回転数 制御部", "ic=H02M", SENT25[:24], "x" + SENT25[:24] + "y"):
        egress.check(ok, case_id)                                  # キーワード・24 字の一致は止めない
    with pytest.raises(egress.QueryBlocked) as e:
        egress.check(SENT25, case_id)                              # 25 字は止める
    assert e.value.overlap == SENT25[:20] and len(e.value.overlap) <= 20
    assert "未公開" in e.value.reason and SENT25[:20] in e.value.reason
    with pytest.raises(egress.QueryBlocked):
        egress.check("ti=(" + SENT25[:12] + "\n" + SENT25[12:] + ")", case_id)   # 改行・括弧の揺れは許す
    with pytest.raises(egress.QueryBlocked):
        egress.check(SENT25.replace("ファン", "ﾌｧﾝ"), case_id)       # 半角カナの揺れ（NFKC）も同じ


def test_blocked_query_gets_a_keyword_suggestion_that_passes():
    from app.patents import egress

    case_id = _case()
    with pytest.raises(egress.QueryBlocked) as e:
        egress.check(f"ti={SENT25} AND ic=H02M", case_id)
    s = e.value.suggestion
    assert s and "冷却ファン" in s and "ic=H02M" in s and "AND" in s and "回転数" in s
    egress.check(s, case_id)                                        # 言い換えの案はそのまま送れる


def test_hearing_and_claims_draft_are_checked_too():
    from app.patents import egress
    from app.patents import store as pstore

    text = "発明者はパワーアンプの帰還回路の位相補償を二段にしたいと話した"
    case_id = _case(claims="", hearing=text)
    with pytest.raises(egress.QueryBlocked):
        egress.check(text[:30], case_id)
    egress.check("パワーアンプの帰還回路", case_id)
    assert pstore.get_case(case_id)


def test_no_case_checks_all_cases():
    from app.patents import egress

    _case("A", claims="【請求項１】別の発明の請求項であって短い。")
    _case("B")
    with pytest.raises(egress.QueryBlocked):                        # 案件が決まらない検索は全案件と照らす
        egress.check(SENT25)
    egress.check("冷却ファンの回転数")


def test_fetch_api_blocks_long_copy_audits_without_text_and_sends_keywords(admin_client, monkeypatch):
    from app.database import SessionLocal
    from app.knowledge import sources
    from app.models import AuditLog
    from sqlalchemy import select

    sent = []
    monkeypatch.setattr(sources, "fetch", lambda source, query, limit: sent.append(query) or [])
    _case()
    r = admin_client.post(f"{API}/sources/openalex/fetch", json={"query": f"{SENT25} 回路"}, headers=CSRF_HEADERS)
    assert r.status_code == 422 and r.json()["detail"]["blocked"] is True
    assert r.json()["detail"]["suggestion"] and r.json()["detail"]["overlap"] == SENT25[:20]
    assert sent == []                                               # 送っていない
    r = admin_client.post(f"{API}/sources/openalex/fetch", json={"query": "冷却ファンの回転数", "approved_public": False},
                          headers=CSRF_HEADERS)
    assert r.status_code == 201                                     # 確認の指定が無くても（False でも）送る
    with SessionLocal() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action.like("patents.source_fetch%"))).scalars().all()
    blob = " ".join(x.metadata_json for x in rows)
    assert {x.action for x in rows} >= {"patents.source_fetch", "patents.source_fetch_blocked"}
    assert "冷却ファン" not in blob and "query_chars" in blob and "unpublished_copy" in blob   # 本文は残さない


def test_watch_save_and_every_run_are_checked(monkeypatch):
    from app.knowledge import ingest, sources
    from app.patents import egress, watch
    from app.patents import store as pstore

    async def no_embed(chunks, collection):
        return "", False

    monkeypatch.setattr(ingest, "_embed", no_embed)
    sent = []
    monkeypatch.setattr(sources, "fetch", lambda source, query, limit: sent.append(query) or [])
    case_id = _case()
    with pytest.raises(egress.QueryBlocked):
        watch.save_watch(case_id, source="openalex", query=SENT25, interval_hours=24)
    w = watch.save_watch(case_id, source="openalex", query="冷却ファン 回転数", interval_hours=24, approved_public=False)
    assert w["id"] in watch.due_watches()
    asyncio.run(watch.run_watch(Job(), w["id"]))
    assert sent == ["冷却ファン 回転数"]
    w2 = watch.save_watch(case_id, source="openalex", query=SENT25[:24], interval_hours=24)   # 24 字は保存できる
    long_id = watch.save_watch(case_id, source="openalex", query="x", interval_hours=24, check=False)["id"]
    conn = watch.connect()
    conn.execute("UPDATE patent_watches SET query=? WHERE id=?", (SENT25, long_id))
    conn.commit()
    conn.close()
    before = list(sent)
    with pytest.raises(egress.QueryBlocked):
        asyncio.run(watch.run_watch(Job(), long_id))                # 実行のたびに検査する
    assert sent == before                                           # 送っていない
    status = next(x for x in watch.list_watches(case_id) if x["id"] == long_id)["last_status"]
    assert status.startswith("止めました") and SENT25[:20] in status
    assert w2["id"] in watch.due_watches()


def test_watch_api_blocks_with_suggestion_but_can_still_be_stopped(admin_client):
    case_id = admin_client.post(f"{API}/cases", json={"title": "x"}, headers=CSRF_HEADERS).json()["id"]
    admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": CLAIM}, headers=CSRF_HEADERS)
    url = f"{API}/cases/{case_id}/watches"
    r = admin_client.post(url, json={"source": "epo", "query": SENT25}, headers=CSRF_HEADERS)
    assert r.status_code == 422 and r.json()["detail"]["suggestion"]
    ok = admin_client.post(url, json={"source": "epo", "query": "冷却ファン 回転数"}, headers=CSRF_HEADERS)
    assert ok.status_code == 201 and ok.json()["approved_public"] is True
    # 保存済みの検索語が後から重なっても、止める操作（enabled=false）は通る
    admin_client.post(f"{API}/cases/{case_id}/claims", json={"text": f"【請求項１】冷却ファン回転数の{SENT25}"}, headers=CSRF_HEADERS)
    r = admin_client.put(f"{url}/{ok.json()['id']}", json={"source": "epo", "query": "冷却ファン 回転数", "enabled": False},
                         headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["enabled"] is False
