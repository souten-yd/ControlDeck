"""特許の分析（書誌の読み取り・集計・類似マップ・案件に近い公報）。

目的:
- 公報の 1 ページ目（INID コード）から出願人・IPC・日付・請求項の数を読み、読めないものは空のまま（推測で埋めない）。
  2 段組みで (71) の名前が別の列に回っても出願人を拾い、「…株式会社内」（発明者の住所）は出願人にしない。
- 集計は書誌の読めた公報だけを数え、読めた件数（coverage）と偏りの注意を返す。
- 類似マップは埋め込みのある公報だけを 2 次元に置く。書誌は手で直せる（source=manual）。

根拠: 2026-11-21 利用者「特許の傾向・書誌・請求項・類似特許などをグラフ化して表示する機能はどうなっているか」。
ResearchDataHub v0.3 の集計と PatentBuddy の 10 種のグラフの試作は本体に入っていなかった（計画 §10）。
"""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from app.config import data_dir
from tests.conftest import CSRF_HEADERS

FRONT = """JP 2026-116114 A 2026.7.9
特開2026-116114
(43)公開日 令和8年7月9日(2026.7.9)
(51)Int.Cl.   H10D 89/60 (2025.01)    H02M 7/48
審査請求 未請求 請求項の数14 OL (全15頁)
(21)出願番号 特願2025-100480
(22)出願日 令和7年6月16日(2025.6.16)          (71)出願人 302062931
(32)優先日 令和6年12月27日(2024.12.27)           ルネサスエレクトロニクス株式会社
(72)発明者 島田 太郎
東京都江東区豊洲三丁目2番24号 ルネサスエレクトロニクス株式会社内
(54)【発明の名称】半導体装置
"""


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    yield


def test_front_page_fields_are_read_and_unknowns_stay_empty():
    from app.knowledge import biblio

    b = biblio.patent_fields(FRONT)
    assert b["applicants"] == ["ルネサスエレクトロニクス株式会社"]
    assert b["ipc"] == ["H10D 89/60", "H02M 7/48"]
    assert (b["filing_date"], b["publication_date"], b["priority_date"]) == ("2025-06-16", "2026-07-09", "2024-12-27")
    assert b["claims_count"] == 14 and b["country"] == "JP" and b["kind"] == "A" and b["title"] == "半導体装置"
    empty = biblio.patent_fields("本文だけの資料。")
    assert empty["applicants"] == [] and empty["ipc"] == [] and empty["claims_count"] is None


def _seed():
    from app.knowledge import store

    store.create_collection("lit", shard="patents", kind="patent")
    rows = [("A社 株式会社", ["H02M 7/48"], "2024-01-10", 10, [1, 0]), ("A社 株式会社", ["H02M 1/08", "H02P 21/00"], "2025-03-01", 6, [0.9, 0.1]),
            ("B大学", ["H02P 27/06"], "2025-05-05", 22, [0, 1]), (None, [], "", None, [0.1, 0.9])]
    refs = []
    for i, (app, ipc, date, claims, vec) in enumerate(rows):
        biblio = {"applicants": [app] if app else [], "ipc": ipc, "publication_date": date, "claims_count": claims,
                  "country": "JP" if app else "", "kind": "A" if app else ""}
        r = store.add_document("lit", [store.NewChunk(f"公報{i} インバータ", vector=np.array(vec, np.float32))],
                               title=f"公報{i}", meta={"biblio": biblio}, model="m")
        refs.append(f"patents:{r['doc_id']}")
    return refs


def test_overview_counts_only_what_was_read_and_says_so():
    from app.patents import analytics

    _seed()
    o = analytics.overview()
    assert o["documents"] == 4 and o["coverage"] == {"year": 3, "applicant": 3, "ipc": 3, "claims": 3}
    assert o["by_year"] == [{"key": "2024", "count": 1}, {"key": "2025", "count": 2}]
    assert o["applicants"][0] == {"key": "A社 株式会社", "count": 2}
    assert {"key": "H02M", "count": 2} in o["ipc_subclasses"] and {"key": "H02P", "count": 2} in o["ipc_subclasses"]
    assert {"applicant": "A社 株式会社", "ipc": "H02P", "count": 1} in o["applicant_ipc"]
    assert next(b for b in o["claims_histogram"] if b["key"] == "21–30")["count"] == 1
    assert "偏る" in o["note"]


def test_similarity_map_places_documents_with_vectors():
    from app.patents import analytics

    _seed()
    m = analytics.similarity_map()
    assert len(m["points"]) == 4 and m["model"] == "m"
    near = {p["title"]: (p["x"], p["y"]) for p in m["points"]}
    d01 = np.hypot(*np.subtract(near["公報0"], near["公報1"]))
    d02 = np.hypot(*np.subtract(near["公報0"], near["公報2"]))
    assert d01 < d02


def test_api_analytics_manual_biblio_and_similar(admin_client, monkeypatch):
    from app.patents import service

    async def no_vector(question):
        return None, ""

    monkeypatch.setattr(service, "_query_vector", no_vector)
    refs = _seed()
    r = admin_client.get("/api/v1/patents/analytics")
    assert r.status_code == 200 and r.json()["documents"] == 4
    assert admin_client.get("/api/v1/patents/analytics/map").json()["points"]
    fixed = admin_client.patch(f"/api/v1/patents/documents/{refs[3]}/biblio",
                               json={"fields": {"applicants": ["C社"], "publication_date": "2023-02-03"}}, headers=CSRF_HEADERS)
    assert fixed.json()["source"] == "manual"
    assert admin_client.patch(f"/api/v1/patents/documents/{refs[3]}/biblio", json={"fields": {"evil": 1}},
                              headers=CSRF_HEADERS).status_code == 422
    assert admin_client.get("/api/v1/patents/analytics").json()["coverage"]["applicant"] == 4
    case_id = admin_client.post("/api/v1/patents/cases", json={"title": "インバータ"}, headers=CSRF_HEADERS).json()["id"]
    admin_client.post(f"/api/v1/patents/cases/{case_id}/claims", json={"text": "【請求項１】インバータ。"}, headers=CSRF_HEADERS)
    sim = admin_client.get(f"/api/v1/patents/cases/{case_id}/similar").json()
    assert sim["results"] and sim["results"][0]["ref"].startswith("patents:")
