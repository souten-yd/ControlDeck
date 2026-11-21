"""承認済みの知識エッセンスの横断検索・比較（K2）: app/knowledge/cross.py と API。

目的:
- 横断検索が、**承認済みで今の版の**エッセンスだけを返すこと。未承認・却下・差し戻し・原文が
  変わった（stale）の item は出ないこと。
- 検索の入口を 1 つに保つこと（計画 §10.2 の 5）: どの文献を探すかは documents.search_documents が
  決める。consumer は local なので、利用条件で AI に使えない文献（ai_use=prohibited）は出ないこと
  （エッセンスはローカル専用の AI の推論であり、横断検索は後のローカルの AI 支援へ知識を渡す入口）。
- 読むたびに引用を原文と確かめ直すこと（K1 の validate と同じ考え方。保存時に検めていても）。
  引用が原文に一致しなくなった item は出ないこと。
- 件数の上限（limit）とページ送り（cursor）が、1 回の走査の上限（SCAN_DOCS）と共に守られること。
- 比較が、別の文献 2〜3 件の承認済みを種類ごとに並べるだけであること（AI の判断を足さない）。
- 権限: 読み取り（workflows.run）で見られ、その権限の無い利用者は断られること。

根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は
ControlDeck にルールに従って反映して」（K2 の作り直し。docs/HANDOFF-knowledge-platform.md の
K2 の行）。PatentBuddy Draft #36 を設計だけ流用し、保存先・認証・走査の仕組みは ControlDeck の
既存（search_documents・essence.list_essences）を使い回す（コードは写していない）。
"""
from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from app.config import data_dir

TEXT_A = "冷却装置の制御では、温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。"
TEXT_B = "振動装置の制御では、回転体の振動を検知したら質量を再配分し、共振を段階的に抑える。"
TEXT_C = "潤滑装置の制御では、摺動部の油膜が切れたら給油を増やし、摩擦を低減する。"
TEXT_D = "加湿装置の制御では、湿度が下がったら霧化量を増やし、乾燥を防ぐ。"

ANSWER_A = {"items": [
    {"kind": "principle", "summary": "温度の上がり方でファンの速さを変える",
     "quote": "温度が急上昇したらファンを速く回し"},
    {"kind": "effect", "summary": "下がるときは穏やかに遅くなる", "quote": "低下時は緩やかに減速する"},
]}
ANSWER_B = {"items": [
    {"kind": "problem", "summary": "回転体の振動が問題になる", "quote": "回転体の振動を検知したら"},
    {"kind": "mechanism", "summary": "質量の再配分で共振を抑える", "quote": "共振を段階的に抑える"},
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
    id, status = "job-cross", "running"

    def set_progress(self, *args):
        pass


def _fake_model(monkeypatch, answer, sent):
    from app.addon_runtime import ai
    from app.patents import service

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def fake_complete(target, request, http_request, require_provenance=False):
        sent["local_only"] = request.local_only
        return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), None

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", fake_complete)


def _doc(text: str, ext_id: str, title: str = "文献") -> str:
    from app.knowledge import store

    if store.find_collection("cross-lit") is None:
        store.create_collection("cross-lit", shard="patents", kind="patent")
    out = store.add_document("cross-lit", [store.NewChunk(text)], title=title, body=text, ext_id=ext_id)
    return f"patents:{out['doc_id']}"


def _extract(monkeypatch, ref: str, answer: dict) -> int:
    """抽出して行の id を返す（モデルは偽物。外部へは通信しない）。"""
    from app.knowledge import essence

    _fake_model(monkeypatch, answer, {})
    essence_id = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, essence_id))
    return essence_id


def _approve(shard: str, essence_id: int, index: int, decision: str = "approved") -> None:
    from app.knowledge import essence

    essence.review(f"{shard}:{essence_id}", index, decision, reviewer="admin")


def _extract_and_approve(monkeypatch, text: str, ext_id: str, answer: dict,
                         approve=(0,), title: str = "文献") -> tuple[str, int]:
    """1 文献を抽出して、先頭の件（既定）を承認済みにする。"""
    ref = _doc(text, ext_id, title)
    essence_id = _extract(monkeypatch, ref, answer)
    for index in approve:
        _approve("patents", essence_id, index)
    return ref, essence_id


def _summaries(result: dict) -> set[str]:
    return {item["summary"] for item in result["items"]}


# ---- 横断検索: 承認済み・今の版・引用が一致するものだけ ----

def test_search_returns_only_approved_items(monkeypatch):
    from app.knowledge import cross

    # A: 先頭の件だけ承認（2 件目は未承認）。B: 先頭を承認、2 件目は却下。C: 承認 1 件。
    _extract_and_approve(monkeypatch, TEXT_A, "cross-a", ANSWER_A, approve=(0,), title="冷却")
    ref_b, b_id = _extract_and_approve(monkeypatch, TEXT_B, "cross-b", ANSWER_B, approve=(0,), title="振動")
    _approve("patents", b_id, 1, "rejected")  # B の 2 件目（mechanism）は却下
    _extract_and_approve(monkeypatch, TEXT_C, "cross-c", ANSWER_C, approve=(0,), title="潤滑")

    out = cross.search("装置")
    assert _summaries(out) == {"温度の上がり方でファンの速さを変える", "回転体の振動が問題になる",
                               "油膜が切れる条件で給油が要る"}
    for item in out["items"]:
        assert item["reviewed_at"] > 0  # 承認の日時が付く
    # 種類ごとの表記も返す（画面の絞り込みに使う）
    assert out["kinds"]["principle"] == "原理"

    # 未承認・却下の件は出ない（承認済みだけ）
    out_all = cross.search("ファン")  # A だけに当たる
    assert _summaries(out_all) == {"温度の上がり方でファンの速さを変える"}


def test_search_goes_through_search_documents_and_ai_use_gate(monkeypatch):
    from app.knowledge import cross, store

    _extract_and_approve(monkeypatch, TEXT_A, "cross-a", ANSWER_A, approve=(0,), title="冷却")
    _extract_and_approve(monkeypatch, TEXT_C, "cross-c", ANSWER_C, approve=(0,), title="潤滑")

    # D は承認済みの知識を持つが、検索語に当たらない（search_documents が選ぶ文献だけが出る）
    d_ref, d_id = _extract_and_approve(monkeypatch, TEXT_D, "cross-d", ANSWER_D, approve=(0,), title="加湿")
    out = cross.search("ファン")
    assert _summaries(out) == {"温度の上がり方でファンの速さを変える"}

    # 抽出の待ちの間に利用条件が変わった（ai_use=prohibited）文献は、承認済みでも出ない
    _extract_and_approve(monkeypatch, TEXT_B, "cross-b", ANSWER_B, approve=(0,), title="振動")
    shard, doc_id = "patents", int(d_ref.split(":")[1])
    conn = store.connect(shard)
    try:
        conn.execute("UPDATE documents SET ai_use='prohibited' WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    out = cross.search("装置")
    assert "湿度の低下に霧化量で応じる" not in _summaries(out)
    assert "回転体の振動が問題になる" in _summaries(out)

    # 種類は知識エッセンスの 8 種のどれか
    from app.knowledge import store as kstore

    with pytest.raises(kstore.KnowledgeError):
        cross.search("装置", kind="未定義の種類")


def test_search_rechecks_revision_and_quote_every_time(monkeypatch):
    from app.knowledge import cross, essence

    ref = _extract_and_approve(monkeypatch, TEXT_A, "cross-a", ANSWER_A, approve=(0,), title="冷却")[0]
    out = cross.search("ファン")
    assert _summaries(out) == {"温度の上がり方でファンの速さを変える"}

    # 原文が変わる（同じ ext_id で差し替え。エッセンスは新しい id へ持ち越されて stale になる）
    _doc(TEXT_A + "追記の一文。", "cross-a", "冷却")
    out = cross.search("ファン")
    assert _summaries(out) == set()  # 前の版の承認は効かないので、何も出ない

    # 同じ文面へ戻しても承認は戻らない（epoch が増えるため。K1 の決まりと同じ）
    _doc(TEXT_A, "cross-a", "冷却")
    out = cross.search("ファン")
    assert _summaries(out) == set()

    # 版は今のままだが、保存してある引用が原文と一致しなくなった item は出さない
    essence_id = _extract_and_approve(monkeypatch, TEXT_B, "cross-b", ANSWER_B, approve=(0,), title="振動")[1]
    conn = essence.connect("patents")
    try:
        row = conn.execute("SELECT items FROM knowledge_essences WHERE id=?", (essence_id,)).fetchone()
        items = json.loads(row["items"])
        items[0]["quote"] = "原文に無い句"  # 引用を破壊する（位置はそのまま）
        conn.execute("UPDATE knowledge_essences SET items=? WHERE id=?",
                     (json.dumps(items, ensure_ascii=False), essence_id))
        conn.commit()
    finally:
        conn.close()
    out = cross.search("振動")
    assert _summaries(out) == set()  # 引用が合わないものは出ない（読むたびに確かめ直す）


def test_search_kind_filter_limit_and_cursor(monkeypatch):
    from app.knowledge import cross

    # A は 2 件承認（文献の途中で切らないことの確認に使う）。B・C は 1 件ずつ。
    _extract_and_approve(monkeypatch, TEXT_A, "cross-a", ANSWER_A, approve=(0, 1), title="冷却")
    _extract_and_approve(monkeypatch, TEXT_B, "cross-b", ANSWER_B, approve=(0,), title="振動")
    _extract_and_approve(monkeypatch, TEXT_C, "cross-c", ANSWER_C, approve=(0,), title="潤滑")

    # 種類の絞り込み
    out = cross.search("装置", kind="principle")
    assert _summaries(out) == {"温度の上がり方でファンの速さを変える"}
    out = cross.search("装置", kind="mechanism")
    assert _summaries(out) == set()  # mechanism の承認済みは無い（未承認のため）

    # limit=1 でも文献の途中で切らない。ページを進めれば 4 件すべてがいつか見える（欠落・重複なし）。
    pages: list[list[dict]] = []
    cursor: int | None = 0
    while cursor is not None:
        page = cross.search("装置", limit=1, cursor=cursor)
        if page["items"]:
            pages.append(page["items"])
        cursor = page["next_cursor"]
    assert len(pages) == 3  # 文献 3 つで 3 ページ（順位は search_documents が決める）
    flat = [i for p in pages for i in p]
    assert len(flat) == 4  # 重複なく 4 件すべて
    assert _summaries({"items": flat}) == {
        "温度の上がり方でファンの速さを変える", "下がるときは穏やかに遅くなる",
        "回転体の振動が問題になる", "油膜が切れる条件で給油が要る"}
    # A（承認 2 件）は 1 ページに両方入る（limit=1 を超えて出し切る。次の文献から続き）
    a_page = next(p for p in pages if any(i["doc_title"] == "冷却" for i in p))
    assert len(a_page) == 2

    # 全部見終わったら next_cursor は無い
    last = cross.search("装置", limit=50)
    assert last["next_cursor"] is None

    # 1 回の走査は SCAN_DOCS 文献まで（当たりの文献を無制限に開かせない）
    monkeypatch.setattr(cross, "SCAN_DOCS", 2)
    page = cross.search("装置", limit=50)
    assert len(page["items"]) <= 3  # 2 文献分まで（A が 2 件 + 他 1 件。どの 2 文献かは順位次第）
    assert page["next_cursor"] is not None  # まだ未見の文献がある


# ---- 比較: 別の文献 2〜3 件の承認済みを種類ごとに並べるだけ ----

def _three_documents(monkeypatch):
    a = _extract_and_approve(monkeypatch, TEXT_A, "cross-a", ANSWER_A, approve=(0,), title="冷却")
    b = _extract_and_approve(monkeypatch, TEXT_B, "cross-b", ANSWER_B, approve=(0,), title="振動")
    c = _extract_and_approve(monkeypatch, TEXT_C, "cross-c", ANSWER_C, approve=(0,), title="潤滑")
    return a[0], b[0], c[0]


def test_compare_rows_by_kind(monkeypatch):
    from app.knowledge import cross

    ref_a, ref_b, ref_c = _three_documents(monkeypatch)
    out = cross.compare([ref_a, ref_b])
    assert [d["ref"] for d in out["documents"]] == [ref_a, ref_b]
    kinds = [row["kind"] for row in out["rows"]]
    assert kinds == ["problem", "principle"]  # KINDS の定義順。未承認（effect）・却下（mechanism）は無い行
    problem, principle = out["rows"]
    assert problem["label"] == "課題"
    assert [item["summary"] for cell in problem["cells"] for item in cell] == ["回転体の振動が問題になる"]
    assert problem["cells"][0] == []  # 冷却の文献には課題の承認済みが無い
    assert [item["summary"] for cell in principle["cells"] for item in cell] == ["温度の上がり方でファンの速さを変える"]

    # 3 件でも同じ形。列は入力の順。
    out3 = cross.compare([ref_c, ref_b, ref_a])
    assert [d["ref"] for d in out3["documents"]] == [ref_c, ref_b, ref_a]
    rows = {row["kind"]: row for row in out3["rows"]}
    assert [item["summary"] for item in rows["constraint"]["cells"][0]] == ["油膜が切れる条件で給油が要る"]
    assert rows["constraint"]["cells"][1] == [] and rows["constraint"]["cells"][2] == []
    # 比較に AI の判断は足さない（承認済みの要約・引用をそのまま並べるだけ）
    assert set(out3) == {"documents", "rows", "kinds"}


def test_compare_rejects_wrong_shapes_and_prohibited(monkeypatch):
    from app.knowledge import cross, store

    ref_a, ref_b, ref_c = _three_documents(monkeypatch)
    with pytest.raises(store.KnowledgeError):  # 1 件・4 件は断る
        cross.compare([ref_a])
    with pytest.raises(store.KnowledgeError):
        cross.compare([ref_a, ref_b, ref_c, ref_a])
    with pytest.raises(store.KnowledgeError):  # 同じ文献は 1 度だけ
        cross.compare([ref_a, ref_a])
    with pytest.raises(store.KnowledgeError):  # 無い文書
        cross.compare([ref_a, "patents:99999"])

    # 利用条件で AI に使えなくなった文献は比較しない（検索と同じ門）
    d_ref, _ = _extract_and_approve(monkeypatch, TEXT_D, "cross-d", ANSWER_D, approve=(0,), title="加湿")
    shard, doc_id = "patents", int(d_ref.split(":")[1])
    conn = store.connect(shard)
    try:
        conn.execute("UPDATE documents SET ai_use='prohibited' WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(store.KnowledgeError):
        cross.compare([ref_a, d_ref])


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


def test_api_search_and_compare(monkeypatch, admin_client):
    ref_a, ref_b, _ = _three_documents(monkeypatch)

    r = admin_client.post(f"{API}/essences/search", json={"q": "ファン"}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 1 and body["items"][0]["doc_ref"] == ref_a
    assert body["items"][0]["doc_title"] == "冷却"
    assert body["items"][0]["kind"] == "principle" and body["items"][0]["reviewed_at"] > 0
    assert body["next_cursor"] is None and body["kinds"]["principle"] == "原理"

    # 種類の絞り込み・上限・ページ送り
    r = admin_client.post(f"{API}/essences/search", json={"q": "装置", "kind": "problem"}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and {i["summary"] for i in r.json()["items"]} == {"回転体の振動が問題になる"}
    r = admin_client.post(f"{API}/essences/search", json={"q": "装置", "limit": 2}, headers=CSRF_HEADERS)
    body = r.json()
    assert len(body["items"]) == 2 and body["next_cursor"] is not None
    r = admin_client.post(f"{API}/essences/search",
                          json={"q": "装置", "limit": 2, "cursor": body["next_cursor"]}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and len(r.json()["items"]) >= 1

    # 比較（読み取りだけ。AI の判断は無い）
    r = admin_client.post(f"{API}/essences/compare", json={"refs": [ref_a, ref_b]}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["ref"] for d in body["documents"]] == [ref_a, ref_b]
    assert [row["kind"] for row in body["rows"]] == ["problem", "principle"]

    # 形の悪い指定は断る（Pydantic と backend の両方の門）
    r = admin_client.post(f"{API}/essences/search", json={"q": "装置", "kind": "未定義"}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    r = admin_client.post(f"{API}/essences/search", json={"q": ""}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    r = admin_client.post(f"{API}/essences/compare", json={"refs": [ref_a]}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    r = admin_client.post(f"{API}/essences/compare", json={"refs": [ref_a, ref_b, ref_a]}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    r = admin_client.post(f"{API}/essences/compare", json={"refs": [ref_a, "patents:99999"]}, headers=CSRF_HEADERS)
    assert r.status_code == 404


def test_api_permissions_are_read_only_view(client, admin_client, monkeypatch):
    from app.knowledge import cross

    ref_a, ref_b, ref_c = _three_documents(monkeypatch)
    _limited_user("cross-operator", "operator", "cross-operator-9")
    _limited_user("cross-viewer", "viewer", "cross-viewer-9")

    # 読み取り（workflows.run）の利用者は見られる（新しい権限を足さない。既存の閲覧と同じ）
    _login(client, "cross-operator", "cross-operator-9")
    assert client.post(f"{API}/essences/search", json={"q": "装置"}, headers=CSRF_HEADERS).status_code == 200
    assert client.post(f"{API}/essences/compare", json={"refs": [ref_a, ref_b]},
                      headers=CSRF_HEADERS).status_code == 200
    # 閲覧の権限も無い利用者は断られる
    _login(client, "cross-viewer", "cross-viewer-9")
    assert client.post(f"{API}/essences/search", json={"q": "装置"}, headers=CSRF_HEADERS).status_code == 403
    assert client.post(f"{API}/essences/compare", json={"refs": [ref_a, ref_b]},
                       headers=CSRF_HEADERS).status_code == 403


def test_search_keeps_sync_work_off_the_event_loop():
    """検索・比較は同期の DB・ファイル I/O を event loop の外（threadpool）で実行すること
    （AGENTS.md 絶対ルール 9。endpoint は def なので FastAPI が threadpool へ任せる）。"""
    import inspect

    from app.knowledge import cross, router

    for fn in (router.search_essences, router.compare_essences):
        assert not inspect.iscoroutinefunction(fn)  # def なら FastAPI が threadpool で実行する
    assert not inspect.iscoroutinefunction(cross.search)  # 中で DB を同期的に読むので thread でだけ呼ぶ
