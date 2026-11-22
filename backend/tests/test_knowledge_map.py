"""知識マップ（K3）: app/knowledge/map.py と API。

目的:
- 1 つの文献を中心に、書誌の関係（引用する・される・別の版）で 1〜2 hop の文献と、
  それぞれの **承認済み・今の版の** 知識エッセンスを、ノードとエッジで返すこと。
  未承認・却下・差し戻し・原文が変わった（stale）の知識は地図に載らないこと。
- 読み取りだけであること（LLM を呼ばない・書き込みしない・表を足さない）。
- 上限（ノード 61・エッジ 60）を超えたら打ち切りの印（truncated）を出すこと。
  hop は 1 か 2 だけで、無い文書は「ありません」で断ること。
- 権限: 読み取り（workflows.run）で見られ、その権限の無い利用者は断られること。
  同期の DB 読み取りは event loop の外（threadpool）で動くこと。

根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は
ControlDeck にルールに従って反映して」（K3 の作り直し。docs/HANDOFF-knowledge-platform.md
の K3 の行）。PatentBuddy Draft #34 を設計だけ流用し（コードは写していない）、
関係の正本は ControlDeck の書誌の関係（documents.related_documents）、知識の判定は
K1 の list_essences を使い回す。上限 36/52 は 61/60 に広げた（文献と知識を 1 つの
地図に載せる分。指示書どおり）。
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from app.config import data_dir

# 末尾の参照で書誌の関係を作る（biblio.references → identifiers own=0）。
# 題名の番号が自分の番号（own=1）。
TEXT_A = "冷却装置の制御では、温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。先行技術: 特許第1000002号、特許第1000003号。"
TEXT_B = "振動装置の制御では、回転体の振動を検知したら質量を再配分し、共振を段階的に抑える。次に参照する: 特許第1000004号。"
TEXT_C = "潤滑装置の制御では、摺動部の油膜が切れたら給油を増やし、摩擦を低減する。"
TEXT_D = "加湿装置の制御では、湿度が下がったら霧化量を増やし、乾燥を防ぐ。"

# 引用はそれぞれの文献の本文から（共通の引用は TEXT_A にしか現れないため、B・C・D は全件捨てられる）
ANSWER_A = {"items": [
    {"kind": "principle", "summary": "温度の上がり方でファンの速さを変える", "quote": "温度が急上昇したらファンを速く回し"},
]}
ANSWER_B = {"items": [
    {"kind": "problem", "summary": "回転体の振動が問題になる", "quote": "回転体の振動を検知したら"},
]}
ANSWER_C = {"items": [
    {"kind": "constraint", "summary": "油膜が切れる条件で給油が要る", "quote": "摺動部の油膜が切れたら"},
]}
ANSWER_D = {"items": [
    {"kind": "application", "summary": "湿度の低下に霧化量で応じる", "quote": "湿度が下がったら霧化量を増やし"},
]}


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    yield


class Job:
    id, status = "job-map", "running"

    def set_progress(self, *args):
        pass


def _fake_model(monkeypatch, answer):
    from app.addon_runtime import ai
    from app.patents import service

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def fake_complete(target, request, http_request, require_provenance=False):
        return (answer if isinstance(answer, str) else __import__("json").dumps(answer, ensure_ascii=False)), None

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", fake_complete)


def _doc(text: str, ext_id: str, title: str) -> str:
    from app.knowledge import store

    if store.find_collection("map-lit") is None:
        store.create_collection("map-lit", shard="patents", kind="patent")
    out = store.add_document("map-lit", [store.NewChunk(text)], title=title, body=text, ext_id=ext_id)
    return f"patents:{out['doc_id']}"


def _extract_and_approve(monkeypatch, text: str, ext_id: str, title: str,
                         answer: dict = ANSWER_A, approve=(0,)) -> tuple[str, int]:
    """1 文献を抽出して先頭の件（既定）を承認済みにする。LLM は偽物（外部へは通信しない）。"""
    from app.knowledge import essence

    _fake_model(monkeypatch, answer)
    ref = _doc(text, ext_id, title)
    essence_id = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, essence_id))
    for index in approve:
        essence.review(f"patents:{essence_id}", index, "approved", reviewer="admin")
    return ref, essence_id


def _docs(monkeypatch, approved: set[str]) -> dict[str, str]:
    """A・B・C・D を作る。A は B・D を引用し、B は C を引用（A→C は 2 hop だけ）。
    approved に指定した文献の先頭件だけ承認済みにする。"""
    made = {}
    for key, text, ext_id, title, answer in (
        ("A", TEXT_A, "map-a", "特許第1000001号 冷却", ANSWER_A),
        ("B", TEXT_B, "map-b", "特許第1000003号 振動", ANSWER_B),
        ("C", TEXT_C, "map-c", "特許第1000004号 潤滑", ANSWER_C),
        ("D", TEXT_D, "map-d", "特許第1000002号 加湿", ANSWER_D),
    ):
        made[key] = _extract_and_approve(
            monkeypatch, text, ext_id, title, answer=answer,
            approve=(0,) if key in approved else ())[0]
    return made


def _summaries(out: dict) -> set[str]:
    return {n["summary"] for n in out["nodes"] if n["kind"] == "essence"}


def _refs(out: dict, depth: int) -> list[str]:
    return [n["ref"] for n in out["nodes"] if n["kind"] == "document" and n.get("depth") == depth]


# ---- 1〜2 hop: 書誌の関係と承認済みの知識をノードとエッジで返す ----

def test_map_one_hop_with_essences(monkeypatch):
    from app.knowledge import map as kmap

    made = _docs(monkeypatch, {"A", "B", "D"})
    out = kmap.knowledge_map(made["A"], hops=1)
    nodes = {n["id"]: n for n in out["nodes"]}
    # root と 1 hop の文献（cites で B と D。C は A から直接は参照していないので出ない）
    assert _refs(out, 0) == [made["A"]]
    assert set(_refs(out, 1)) == {made["B"], made["D"]}
    assert made["C"] not in {n.get("ref") for n in out["nodes"]}
    root = nodes[f"d:{made['A']}"]
    assert root["relation"] == "root" and root["title"] == "特許第1000001号 冷却"
    # 承認済みの知識は「出典」のエッジで文献に繋がる。未承認の文献（C）の知識は載らない
    assert _summaries(out) == {"温度の上がり方でファンの速さを変える",
                               "回転体の振動が問題になる",
                               "湿度の低下に霧化量で応じる"}
    essence_node = next(n for n in out["nodes"] if n["kind"] == "essence" and n["doc_ref"] == made["A"])
    assert essence_node["essence_kind"] == "principle" and essence_node["reviewed_at"] > 0
    edges = {(e["source"], e["target"], e["relation"]) for e in out["edges"]}
    assert (f"d:{made['A']}", f"k:patents:1:0", "evidence") in edges
    assert (f"d:{made['A']}", f"d:{made['D']}", "cites") in edges
    assert (f"d:{made['A']}", f"d:{made['B']}", "cites") in edges
    assert out["truncated"] is False
    assert out["limits"] == {"nodes": 61, "edges": 60}
    assert out["kinds"]["principle"] == "原理" and out["relations"]["cites"] == "参照"


def test_map_two_hop_reaches_documents_one_hop_cannot(monkeypatch):
    from app.knowledge import map as kmap

    made = _docs(monkeypatch, {"A", "B", "C"})
    one = kmap.knowledge_map(made["A"], hops=1)
    assert made["C"] not in {n.get("ref") for n in one["nodes"]}
    two = kmap.knowledge_map(made["A"], hops=2)
    # 2 hop で B の参照先 C に届く（depth 2）。B → C のエッジも付く
    assert made["C"] in _refs(two, 2)
    edges = {(e["source"], e["target"], e["relation"]) for e in two["edges"]}
    assert (f"d:{made['B']}", f"d:{made['C']}", "cites") in edges
    # C の承認済みの知識も載る
    c_nodes = [n for n in two["nodes"] if n["kind"] == "essence" and n.get("doc_ref") == made["C"]]
    assert c_nodes and c_nodes[0]["summary"] == "油膜が切れる条件で給油が要る"


def test_map_shows_only_approved_and_current_essences(monkeypatch):
    from app.knowledge import map as kmap

    made = _docs(monkeypatch, {"A", "B", "D"})
    out = kmap.knowledge_map(made["A"], hops=2)
    # 承認済みだけ（C は承認していないので知識は載らない。文献だけ 2 hop に出る）
    assert _summaries(out) == {"温度の上がり方でファンの速さを変える",
                               "回転体の振動が問題になる",
                               "湿度の低下に霧化量で応じる"}
    assert made["C"] in _refs(out, 2)
    assert not [n for n in out["nodes"] if n["kind"] == "essence" and n.get("doc_ref") == made["C"]]

    # root の原文が変わった（同じ ext_id で差し替え。文書の id も新しくなる）:
    # 前の承認は効かないので、差し替え後の id で見ても root の知識は載らない
    new_a = _doc(TEXT_A + "追記の一文。", "map-a", "特許第1000001号 冷却")
    out = kmap.knowledge_map(new_a, hops=1)
    assert "温度の上がり方でファンの速さを変える" not in _summaries(out)
    # 同じ文面へ戻しても承認は戻らない（K1 の epoch と同じ決まり）
    back_a = _doc(TEXT_A, "map-a", "特許第1000001号 冷却")
    out = kmap.knowledge_map(back_a, hops=1)
    assert "温度の上がり方でファンの速さを変える" not in _summaries(out)
    # ほかの文献の承認済みはそのまま載る
    assert "回転体の振動が問題になる" in _summaries(kmap.knowledge_map(made["B"], hops=1))


def test_map_cuts_at_limits_with_a_mark(monkeypatch):
    from app.knowledge import map as kmap

    made = _docs(monkeypatch, {"A"})
    # 上限を意図的に下げる（本物の上限は 61/60。切れ味を確かめるための小さい値）
    real_nodes, real_edges = kmap.MAX_NODES, kmap.MAX_EDGES
    monkeypatch.setattr(kmap, "MAX_NODES", 3)
    monkeypatch.setattr(kmap, "MAX_EDGES", 3)
    out = kmap.knowledge_map(made["A"], hops=2)
    assert out["truncated"] is True
    assert len(out["nodes"]) <= 3 and len(out["edges"]) <= 3
    # ノードの上限に達したら、それ以上の文献・知識は載せない（root と 1 hop までで止まる）
    assert all(n.get("depth", 0) <= 1 for n in out["nodes"])
    # 上限を元に戻せば（本物の 61/60）打ち切りの印は付かない
    monkeypatch.setattr(kmap, "MAX_NODES", real_nodes)
    monkeypatch.setattr(kmap, "MAX_EDGES", real_edges)
    out = kmap.knowledge_map(made["A"], hops=2)
    assert out["truncated"] is False

    # 1 文献の知識が上限（8 件）を超えたら、その分も打ち切りの印。中心はその文献自身
    lots = {"items": [{"kind": "principle", "summary": f"知識{i}",
                       "quote": "温度が急上昇したらファンを速く回し"} for i in range(9)]}
    lots_ref, _ = _extract_and_approve(monkeypatch, TEXT_A, "map-lots", "特許第1000005号 多数",
                                       answer=lots, approve=tuple(range(9)))
    out = kmap.knowledge_map(lots_ref, hops=1)
    assert out["truncated"] is True  # この文献の知識で 8 件の上限に達した
    assert len([n for n in out["nodes"] if n["kind"] == "essence"]) == 8


def test_map_validates_hops_and_missing_documents():
    from app.knowledge import map as kmap, store

    with pytest.raises(store.KnowledgeError):
        kmap.knowledge_map("patents:1", hops=3)
    with pytest.raises(store.KnowledgeError):
        kmap.knowledge_map("patents:1", hops=0)
    with pytest.raises(store.KnowledgeError):  # 無い文書
        kmap.knowledge_map("patents:99999", hops=1)


def test_map_marks_truncation_when_hop_docs_cut(monkeypatch):
    """1 回の広げで見る文献の上限（HOP_DOCS）で切ったときも打ち切りの印を出すこと
    （見えていない関係があることを隠さない。2026-11-22 レビューの minor 指摘で直した）。"""
    from app.knowledge import map as kmap

    made = _docs(monkeypatch, {"A"})
    # A は 2 件（B・D）を引用している。HOP_DOCS を 1 に下げると 1 件で切れる
    monkeypatch.setattr(kmap, "HOP_DOCS", 1)
    out = kmap.knowledge_map(made["A"], hops=1)
    assert out["truncated"] is True
    assert len(_refs(out, 1)) == 1
    # 上限を超えなければ印は付かない
    monkeypatch.setattr(kmap, "HOP_DOCS", 12)
    out = kmap.knowledge_map(made["A"], hops=1)
    assert out["truncated"] is False and len(_refs(out, 1)) == 2


def test_map_keeps_sync_work_off_the_event_loop():
    import inspect

    from app.knowledge import map as kmap, router

    assert not inspect.iscoroutinefunction(router.get_knowledge_map)
    assert not inspect.iscoroutinefunction(kmap.knowledge_map)


# ---- API（権限の区分。LLM は偽物・外部へは通信しない） ----

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from tests.conftest import CSRF_HEADERS  # noqa: E402

API = "/api/v1/knowledge"


def _login(client, username, password):
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text


def _limited_user(name, role_name, password):
    with SessionLocal() as db:
        if db.execute(select(User).where(User.username == name)).scalar_one_or_none() is None:
            role_id = db.execute(select(Role.id).where(Role.name == role_name)).scalar()
            db.add(User(username=name, password_hash=hash_password(password), role_id=role_id))
            db.commit()


def test_api_map_and_links_list(monkeypatch, admin_client):
    made = _docs(monkeypatch, {"A", "B", "D"})
    r = admin_client.get(f"{API}/documents/{made['A']}/map")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {n["ref"] for n in body["nodes"] if n["kind"] == "document"} == {made["A"], made["B"], made["D"]}
    assert body["hops"] == 1 and body["truncated"] is False
    # hops=2 で 2 段先まで出る
    r = admin_client.get(f"{API}/documents/{made['A']}/map?hops=2")
    assert r.status_code == 200 and made["C"] in {n.get("ref") for n in r.json()["nodes"]}
    # 形の悪い指定・無い文書は断る
    assert admin_client.get(f"{API}/documents/{made['A']}/map?hops=3").status_code == 422
    assert admin_client.get(f"{API}/documents/patents:99999/map").status_code == 404
    # リンクの一覧（読み取りだけ。まだ無ければ空）
    r = admin_client.get(f"{API}/documents/{made['A']}/links")
    assert r.status_code == 200 and r.json()["links"] == []


def test_api_map_permissions(client, admin_client, monkeypatch):
    made = _docs(monkeypatch, {"A"})
    _limited_user("map-operator", "operator", "map-operator-9")
    _limited_user("map-viewer", "viewer", "map-viewer-9")
    # 読み取り（workflows.run）の利用者は見られる（新しい権限を足さない。既存の閲覧と同じ）
    _login(client, "map-operator", "map-operator-9")
    assert client.get(f"{API}/documents/{made['A']}/map").status_code == 200
    assert client.get(f"{API}/documents/{made['A']}/links").status_code == 200
    # 閲覧の権限も無い利用者は断られる
    _login(client, "map-viewer", "map-viewer-9")
    assert client.get(f"{API}/documents/{made['A']}/map").status_code == 403
    assert client.get(f"{API}/documents/{made['A']}/links").status_code == 403
