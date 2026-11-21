"""知識エッセンス（K1a）: 文献から根拠付きの知識を抽出し、人のレビューで確かめる。

目的:
- モデルの出力をそのまま信用しない。抽出（種類 8 つ・1 文献 12 件まで）は、引用が原文の
  [start, end) に一字一句そのまま一致することを検めてから版付きで保存すること。モデルが位置を
  出せない場合は、引用が原文にちょうど 1 回だけ現れるときに限って位置を補うこと（2 回以上・
  0 回は捨てる）。形違い・上限超過は件を捨てて捨てた数を記録すること。
- AI の出力は `basis=model_inference`・`review_status=unreviewed` で固定され、承認しても
  「原文の事実」にはならないこと（統合の決まり 1）。
- 人のレビュー（承認・却下・差し戻し）は消さず追記だけ。最新のレビューがその件の状態であること。
- 原文の版が変わったらエッセンスは stale として扱い、前の承認は効かないこと。同じ版へ
  戻しても前の承認は戻らないこと（版ごとに数える番号 epoch を単調に増やす）。
- 利用条件で AI に使えない文書（購入した規格など）は抽出しないこと。LLM はローカル専用
  （`local_only=True`）で、外部へは通信しないこと。

根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は
ControlDeck にルールに従って反映して」（K1a〜K2 の作り直し。HANDOFF-knowledge-platform.md
の K1 の行）。docs/PLAN-knowledge-platform.md §10 R3-4「引用の偽造・版の変更・推論の事実化を
防ぐテスト」。PatentBuddy Draft #29〜#33 を設計だけ流用し、保存先・認証・Job は ControlDeck
の既存（analysis.py・survey.py・judge.py）を使い回す。
"""
from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from app.config import data_dir

TEXT = "温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。"
UNIQUE_QUOTE = "低下時は緩やかに減速する"
TWICE_TEXT = "温度が急上昇したらファンを速く回す。温度が急上昇したら警報も出す。"
TWICE_QUOTE = "温度が急上昇したら"
DOC = {"ref": "patents:1", "revision": "rev-1", "text": TEXT}


def _item(**patch):
    start = TEXT.index(UNIQUE_QUOTE)
    base = {"kind": "principle", "summary": "状態の変化の方向で応答の速さを変える", "quote": UNIQUE_QUOTE,
            "start": start, "end": start + len(UNIQUE_QUOTE)}
    base.update(patch)
    return base


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    yield


# ---- validate: 純粋な関数（LLM も DB も使わない） ----

def test_validate_keeps_exact_spans_and_drops_mismatched_positions():
    from app.knowledge import essence

    out = essence.validate(DOC, {"source_revision": "rev-1", "items": [
        _item(),  # 位置が原文どおり → そのまま通る
        _item(kind="problem", summary="温度を下げたい", start=DOC["text"].index(UNIQUE_QUOTE) + 1),  # 位置がずれている → 捨てる
        _item(kind="cause", summary="熱がこもるため", quote="存在しない引用", start=0, end=7),  # 引用が偽物 → 捨てる
        # 範囲外の位置は「位置を出せていない」と同じ扱い: 引用が 1 回だけなら補う
        _item(kind="effect", summary="速く冷える", start=999999, end=999999 + len(UNIQUE_QUOTE)),
    ]})
    assert len(out["items"]) == 2 and out["dropped"] == 2
    assert out["items"][0]["start"] == TEXT.index(UNIQUE_QUOTE)
    filled = out["items"][1]
    assert filled["kind"] == "effect" and filled["start"] == TEXT.index(UNIQUE_QUOTE)
    kept = out["items"][0]
    assert kept["kind"] == "principle" and kept["summary"] == "状態の変化の方向で応答の速さを変える"
    assert kept["quote"] == UNIQUE_QUOTE
    assert kept["start"] == TEXT.index(UNIQUE_QUOTE) and kept["end"] == kept["start"] + len(UNIQUE_QUOTE)
    assert out["source_revision"] == "rev-1"


def test_validate_fills_position_only_when_quote_is_unique():
    from app.knowledge import essence

    twice = {"ref": "patents:2", "revision": "rev-2", "text": TWICE_TEXT}
    # 位置が無い: 引用がちょうど 1 回だけあれば位置を補う
    out = essence.validate(DOC, {"source_revision": "rev-1", "items": [
        {"kind": "mechanism", "summary": "ファンの回転数を制御する", "quote": UNIQUE_QUOTE}]})
    assert out["dropped"] == 0 and out["items"][0]["start"] == TEXT.index(UNIQUE_QUOTE)
    assert out["items"][0]["end"] == out["items"][0]["start"] + len(UNIQUE_QUOTE)
    # 2 回出る引用は位置を補えないので捨てる
    out = essence.validate(twice, {"source_revision": "rev-2", "items": [
        {"kind": "problem", "summary": "温度が上がる", "quote": TWICE_QUOTE}]})
    assert out["items"] == [] and out["dropped"] == 1
    # 0 回（1 度も原文に無い）も捨てる
    out = essence.validate(twice, {"source_revision": "rev-2", "items": [
        {"kind": "effect", "summary": "効率が上がる", "quote": "原文に無い句"}]})
    assert out["items"] == [] and out["dropped"] == 1
    # 2 回出る引用でも、位置まで正しく出していれば通る（曖昧さを位置が解消している）
    out = essence.validate(twice, {"source_revision": "rev-2", "items": [
        {"kind": "problem", "summary": "温度が上がる", "quote": TWICE_QUOTE,
         "start": TWICE_TEXT.index(TWICE_QUOTE), "end": TWICE_TEXT.index(TWICE_QUOTE) + len(TWICE_QUOTE)}]})
    assert out["dropped"] == 0 and len(out["items"]) == 1


def test_validate_rejects_revision_mismatch_and_drops_bad_shapes():
    from app.knowledge import essence, store

    with pytest.raises(store.KnowledgeError):  # 版が一致しない提案は受け付けない
        essence.validate(DOC, {"source_revision": "rev-別", "items": [_item()]})
    with pytest.raises(store.KnowledgeError):
        essence.validate(DOC, {"source_revision": "rev-1"})          # items が無い
    with pytest.raises(store.KnowledgeError):
        essence.validate(DOC, {"source_revision": "rev-1", "items": "×"})  # items が配列でない
    # 形違い・種類は捨てて、捨てた数を記録
    out = essence.validate(DOC, {"source_revision": "rev-1", "items": [
        _item(kind="未定義の種類"), _item(summary=""), _item(summary="字" * 401),
        _item(quote="短い句"), _item(quote="句" * 1201), "形が違う件",
    ]})
    assert out["items"] == [] and out["dropped"] == 6
    # 1 文献 12 件まで。超えた分は捨てて記録する
    many = [_item(kind=k, summary=s) for k, s in zip(
        ("problem", "cause", "principle", "mechanism", "implementation", "effect",
         "constraint", "application", "problem", "cause", "principle", "mechanism",
         "implementation", "effect"),
        (f"件{i}" for i in range(14)))]
    out = essence.validate(DOC, {"source_revision": "rev-1", "items": many})
    assert len(out["items"]) == 12 and out["dropped"] == 2


def test_validate_fixes_basis_and_review_status():
    from app.knowledge import essence

    out = essence.validate(DOC, {"source_revision": "rev-1", "items": [
        _item(basis="source_fact", review_status="approved")]})  # 偽って主張しても
    assert out["items"][0]["basis"] == "model_inference"          # 推論・未確認に固定する
    assert out["items"][0]["review_status"] == "unreviewed"


# ---- start + Job での実行（LLM は偽物・外部へは通信しない） ----

class Job:
    id, status = "job-essence", "running"

    def set_progress(self, *args):
        pass


MODEL_ANSWER = {"items": [
    {"kind": "principle", "summary": "状態の変化の方向で応答の速さを変える", "quote": UNIQUE_QUOTE},
    {"kind": "problem", "summary": "温度が上がりすぎる", "quote": "温度が急上昇したら"},
    {"kind": "effect", "summary": "効率が上がる", "quote": "原文に無い句"},  # 引用が偽物 → 捨てる
]}


def _fake_model(monkeypatch, answer, sent):
    from app.addon_runtime import ai
    from app.patents import service

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def fake_complete(target, request, http_request, require_provenance=False):
        sent["local_only"] = request.local_only
        sent["prompt"] = request.messages[-1]["content"]
        return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), None

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", fake_complete)


def _doc(text=TEXT, title="公報", ext_id="essence-1", **kw):
    from app.knowledge import store

    if store.find_collection("essence-lit") is None:
        store.create_collection("essence-lit", shard="patents", kind="patent")
    out = store.add_document("essence-lit", [store.NewChunk(text)], title=title, body=text, ext_id=ext_id, **kw)
    return f"patents:{out['doc_id']}"


def test_start_runs_local_model_and_saves_validated_essence(monkeypatch):
    from app.knowledge import analysis, essence

    sent = {}
    _fake_model(monkeypatch, MODEL_ANSWER, sent)
    ref = _doc()
    essence_id = essence.start(ref)
    out = asyncio.run(essence.run(Job(), ref, essence_id))
    assert sent["local_only"] is True and "公報" in sent["prompt"] and TEXT in sent["prompt"]
    assert "JSON" in sent["prompt"] and "原文に無い句" not in json.dumps(out)
    assert out["items"] == 2 and out["dropped"] == 1 and out["unchanged"] is False
    assert out["essence_ref"] == f"patents:{essence_id}"  # Job の結果から分野つきで指せる
    rows = essence.list_essences(ref)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == essence_id and row["status"] == "succeeded" and row["stale"] is False
    assert row["source_revision"] == analysis.revision(ref) and row["basis"] == "model_inference"
    first = row["items"][0]
    assert first["kind"] == "principle" and first["quote"] == UNIQUE_QUOTE
    assert first["start"] == TEXT.index(UNIQUE_QUOTE) and first["end"] == first["start"] + len(UNIQUE_QUOTE)
    assert all(i["basis"] == "model_inference" and i["review_status"] == "unreviewed" for i in row["items"])


def test_same_content_rerun_does_not_add_a_row(monkeypatch):
    from app.knowledge import essence

    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc()
    first = asyncio.run(essence.run(Job(), ref, essence.start(ref)))
    second = asyncio.run(essence.run(Job(), ref, essence.start(ref)))
    assert second["unchanged"] is True and second["essence_id"] == first["essence_id"]
    rows = essence.list_essences(ref)
    assert len(rows) == 1 and rows[0]["id"] == first["essence_id"]


def test_unparsable_model_answer_is_recorded_as_failure(monkeypatch):
    from app.knowledge import essence

    _fake_model(monkeypatch, "抽出できませんでした", {})
    ref = _doc(text="別の文献の本文", title="壊れた答え", ext_id="essence-2")
    essence_id = essence.start(ref)
    with pytest.raises(ValueError):
        asyncio.run(essence.run(Job(), ref, essence_id))
    rows = essence.list_essences(ref)
    assert rows[0]["status"] == "failed" and "JSON" in rows[0]["error"] and rows[0]["items"] == []


def test_documents_ai_may_not_read_are_not_extracted(monkeypatch):
    from app.knowledge import analysis, essence, store

    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc(text="購入した規格の本文", title="規格", ext_id="essence-3", ai_use="prohibited")
    with pytest.raises(store.KnowledgeError):  # 始める前に断る
        essence.start(ref)
    # 抽出の待ちの間に利用条件が変わっても、実行側で同じ門を通る（analysis.py と同じ判定）
    ref2 = _doc(text="規格の本文（別の版）", title="規格2", ext_id="essence-4")
    essence_id = essence.start(ref2)
    from app.knowledge import documents as kdocuments

    shard, doc_id = kdocuments.parse_ref(ref2)
    conn = store.connect(shard)
    try:
        conn.execute("UPDATE documents SET ai_use='prohibited' WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(analysis.AnalysisUnavailable):
        asyncio.run(essence.run(Job(), ref2, essence_id))


# ---- 人のレビュー（追記だけ）と版の失効 ----

def test_review_appends_only_and_latest_wins(monkeypatch):
    from app.knowledge import essence, store

    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc()
    essence_id = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, essence_id))
    assert essence.list_essences(ref)[0]["items"][0]["review"] is None

    essence.review(f"patents:{essence_id}", 0, "approved", note="根拠を原文で確認した", reviewer="admin")
    rows = essence.list_essences(ref)
    item0 = rows[0]["items"][0]
    assert item0["review_status"] == "approved"
    assert item0["review"]["reviewer"] == "admin" and item0["review"]["stale"] is False
    assert rows[0]["items"][1]["review_status"] == "unreviewed"  # 他の件はそのまま

    # 差し戻しを追記すると、最新のレビューがその件の状態になる（前の承認は履歴に残るだけ）
    essence.review(f"patents:{essence_id}", 0, "returned", note="表現を直してほしい", reviewer="admin")
    item0 = essence.list_essences(ref)[0]["items"][0]
    assert item0["review_status"] == "returned"

    conn = essence.connect("patents")
    try:
        count = conn.execute("SELECT COUNT(*) FROM knowledge_essence_reviews").fetchone()[0]
    finally:
        conn.close()
    assert count == 2  # 消さずに追記だけ

    with pytest.raises(store.KnowledgeError):  # 判断・件の番号・id が違うものは断る
        essence.review(f"patents:{essence_id}", 0, "approved?")
    with pytest.raises(store.KnowledgeError):
        essence.review(f"patents:{essence_id}", 99, "approved")
    with pytest.raises(store.KnowledgeError):
        essence.review(f"patents:{essence_id + 999}", 0, "approved")


def test_stale_when_source_changes_and_approvals_do_not_return(monkeypatch):
    from app.knowledge import analysis, essence, store

    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc()
    essence_id = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, essence_id))
    essence.review(f"patents:{essence_id}", 0, "approved", reviewer="admin")

    # 原文が変わる（同じ ext_id で本文を差し替える。先に別の文書を足して id が変わるようにする）
    _doc(text="余白の文献", title="余白", ext_id="essence-other")
    ref2 = _doc(text=TEXT + "追記の一文。", ext_id="essence-1")
    assert ref2 != ref  # 差し替えで文書の id が変わる
    rows = essence.list_essences(ref2)
    assert len(rows) == 1 and rows[0]["id"] == essence_id  # 新しい id へ持ち越されて残る
    assert rows[0]["stale"] is True
    item0 = rows[0]["items"][0]
    assert item0["review_status"] == "unreviewed"  # 前の承認は効かない
    assert item0["review"]["decision"] == "approved" and item0["review"]["stale"] is True  # 履歴としては見える
    with pytest.raises(store.KnowledgeError):  # 古い版の抽出へのレビューは断る
        essence.review(f"patents:{essence_id}", 0, "rejected", reviewer="admin")

    # 同じ文面へ戻しても、前の承認は戻らない（置き換えのたびに版の番号 epoch が増えるため）
    ref3 = _doc(text=TEXT, ext_id="essence-1")
    rows = essence.list_essences(ref3)
    assert rows[0]["source_revision"] == analysis.revision(ref3)
    assert rows[0]["stale"] is False  # 文面は同じなので、抽出自体は今の版と一致する
    item0 = rows[0]["items"][0]
    assert item0["review_status"] == "unreviewed"  # それでも承認は戻らない
    assert item0["review"]["stale"] is True

    # 改めてレビューを追記すれば、また効く
    essence.review(f"patents:{essence_id}", 0, "approved", reviewer="admin")
    item0 = essence.list_essences(ref3)[0]["items"][0]
    assert item0["review_status"] == "approved" and item0["review"]["stale"] is False
    # 文書を消したらエッセンス・レビューも消える（annotations と同じ）
    from app.knowledge import documents as kdocuments

    shard, doc_id = kdocuments.parse_ref(ref3)
    store.delete_collection("essence-lit")
    conn = essence.connect(shard)
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_essences").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_essence_reviews").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM knowledge_essence_epochs").fetchone()[0] == 0
    finally:
        conn.close()


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
            role_id = db.execute(select(Role.id).where(Role.name == role_name)).scalar_one()
            db.add(User(username=name, password_hash=hash_password(password), role_id=role_id))
            db.commit()


def test_api_extraction_and_review_flow(monkeypatch, admin_client):
    from app.knowledge import essence

    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc()
    essence_id = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, essence_id))

    r = admin_client.get(f"{API}/documents/{ref}/essences")
    assert r.status_code == 200
    body = r.json()
    assert body["kinds"]["principle"] == "原理" and len(body["essences"]) == 1
    row = body["essences"][0]
    assert row["stale"] is False and row["items"][0]["review_status"] == "unreviewed"

    r = admin_client.post(f"{API}/essences/patents:{essence_id}/items/0/review",
                          json={"decision": "approved", "note": "原文で確認した"}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["decision"] == "approved"
    row = admin_client.get(f"{API}/documents/{ref}/essences").json()["essences"][0]
    assert row["items"][0]["review_status"] == "approved" and row["items"][0]["review"]["reviewer"] == "admin"
    assert row["shard"] == "patents"
    # まだ終わっていない抽出・範囲外の件（無いものは 404）・悪い判断（422）は断る
    r = admin_client.post(f"{API}/essences/patents:{essence_id}/items/99/review",
                          json={"decision": "approved"}, headers=CSRF_HEADERS)
    assert r.status_code == 404
    r = admin_client.post(f"{API}/essences/{essence_id}/items/0/review",
                          json={"decision": "maybe"}, headers=CSRF_HEADERS)
    assert r.status_code == 422


def test_api_permissions_separate_view_and_edit(client, admin_client, monkeypatch):
    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc()
    _limited_user("essence-operator", "operator", "essence-operator-9")
    _limited_user("essence-viewer", "viewer", "essence-viewer-9")
    # 開始（workflows.edit）は admin だけが通る。201 に Job の id が返る
    r = admin_client.post(f"{API}/documents/{ref}/essences", headers=CSRF_HEADERS)
    assert r.status_code == 201
    assert set(r.json()) >= {"job_id", "essence_id"}
    # 閲覧だけ（workflows.run）の利用者: 見られるが、始められもレビューもできない
    _login(client, "essence-operator", "essence-operator-9")
    assert client.get(f"{API}/documents/{ref}/essences").status_code == 200
    assert client.post(f"{API}/documents/{ref}/essences", headers=CSRF_HEADERS).status_code == 403
    assert client.post(f"{API}/essences/patents:1/items/0/review", json={"decision": "approved"},
                       headers=CSRF_HEADERS).status_code == 403
    # 閲覧の権限も無い利用者: すべて断られる
    _login(client, "essence-viewer", "essence-viewer-9")
    assert client.get(f"{API}/documents/{ref}/essences").status_code == 403
    assert client.post(f"{API}/documents/{ref}/essences", headers=CSRF_HEADERS).status_code == 403


def test_api_refuses_prohibited_documents_missing_documents_and_no_local_model(admin_client, monkeypatch):
    from app.patents import service

    ref = _doc(text="購入した規格の本文", title="購入した規格", ext_id="essence-9", ai_use="prohibited")
    r = admin_client.post(f"{API}/documents/{ref}/essences", headers=CSRF_HEADERS)
    assert r.status_code == 422 and "AI に入力できません" in r.json()["detail"]

    async def unavailable():
        raise service.LocalAIUnavailable("ローカル専用のモデルが使えません")

    monkeypatch.setattr(service, "resolve_local_target", unavailable)
    ref2 = _doc(title="公報2", ext_id="essence-10")
    r = admin_client.post(f"{API}/documents/{ref2}/essences", headers=CSRF_HEADERS)
    assert r.status_code == 503

    r = admin_client.get(f"{API}/documents/patents:99999/essences")
    assert r.status_code == 404


def test_review_id_is_shard_qualified_and_never_attaches_across_shards(monkeypatch):
    """抽出の id は分野ごとに振られる。分野つき（patents:1 など）で指定しないと、
    別の分野の同じ番号の抽出へ承認が誤着する。2026-11-21 reviewer の major 指摘で修正。"""
    from app.knowledge import essence, store

    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref_p = _doc()  # patents の分野
    if store.find_collection("essence-gen") is None:
        store.create_collection("essence-gen", shard="general", kind="general")
    out = store.add_document("essence-gen", [store.NewChunk(TEXT)], title="一般の文献", body=TEXT, ext_id="g-1")
    ref_g = f"general:{out['doc_id']}"
    id_p, id_g = essence.start(ref_p), essence.start(ref_g)
    asyncio.run(essence.run(Job(), ref_p, id_p))
    asyncio.run(essence.run(Job(), ref_g, id_g))
    assert id_p == id_g  # 分野ごとの番号なので、同じ番号が両方の分野にできる

    essence.review(f"patents:{id_p}", 0, "approved", reviewer="admin")
    row_p = essence.list_essences(ref_p)[0]
    row_g = essence.list_essences(ref_g)[0]
    assert row_p["items"][0]["review_status"] == "approved"   # 指定した分野だけが承認され
    assert row_g["items"][0]["review_status"] == "unreviewed"  # 別の分野には付かない
    # 分野の無い・分からない id は断る（誤着を防ぐため、当て推量はしない）
    with pytest.raises(store.KnowledgeError):
        essence.review("1", 0, "approved")
    with pytest.raises(store.KnowledgeError):
        essence.review("moon:1", 0, "approved")


def test_run_keeps_sync_writes_off_the_event_loop(monkeypatch):
    """run（async）の中の DB 書き込みは asyncio.to_thread を通すこと（AGENTS.md 絶対ルール 9。
    同期的に待つと要求だけではなくプロセス全体の event loop が止まる）。2026-11-21 reviewer の指摘で修正。"""
    import threading

    from app.knowledge import essence

    seen: list[bool] = []
    real_update = essence._update

    def spy(shard, essence_id, **fields):
        seen.append(threading.current_thread() is threading.main_thread())
        return real_update(shard, essence_id, **fields)

    monkeypatch.setattr(essence, "_update", spy)
    _fake_model(monkeypatch, MODEL_ANSWER, {})
    ref = _doc()
    id_ok = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, id_ok))            # 成功の経路
    ref_bad = _doc(text="壊れた答えの本文", title="壊れた答え", ext_id="essence-bad")
    id_bad = essence.start(ref_bad)
    _fake_model(monkeypatch, "読めません", {})
    with pytest.raises(ValueError):
        asyncio.run(essence.run(Job(), ref_bad, id_bad))    # 失敗の経路
    assert seen and not any(seen)                           # どちらの書き込みも event loop の外で



