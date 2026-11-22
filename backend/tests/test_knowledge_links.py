"""技術仮説のリンク（K3）: app/knowledge/links.py と API。

目的:
- 承認済みの知識エッセンスどうし（別の文献）を、利用者が明示して結べること。
  結ぶのは人だけ（AI を呼ばない）。basis は inferred で固定され、承認しても
  「原文の事実」にならないこと（統合の決まり 1）。
- 理由のメモが必須であること。未承認の知識・同じ文献・同じ組合せは断ること。
- 作っただけでは「未確認」で、別の操作（承認・却下）で確かめること。
  レビューは消さず追記だけ（K1 のレビューと同じ考え）。
- どちらかの知識が stale（今の版で承認済みでない）になったらリンクも stale になること。
  stale なリンクにはレビューを付けられないこと。同じ文面へ戻っても承認は戻らないこと
  （K1 の epoch の決まりを使い回す）。
- 一覧は結ぶ側・結ばれる側のどちらの文献でも見えること。
- 権限: 一覧は読み取り（workflows.run）、作成・レビューは workflows.edit。
  同期の DB 読み取りは event loop の外（threadpool）で動くこと。

根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は
ControlDeck にルールに従って反映して」（K3 の作り直し。docs/HANDOFF-knowledge-platform.md
の K3 の行）。PatentBuddy Draft #35 を設計だけ流用し（コードは写していない）、
保存先はナレッジの shard DB（K1 の表の足し方）、失効の判定は K1 の list_essences に任せる。
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from app.config import data_dir

TEXT_A = "冷却装置の制御では、温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。"
TEXT_B = "振動装置の制御では、回転体の振動を検知したら質量を再配分し、共振を段階的に抑える。"
TEXT_C = "潤滑装置の制御では、摺動部の油膜が切れたら給油を増やし、摩擦を低減する。"

# 引用はそれぞれの文献の本文から（共通の引用は TEXT_A にしか現れないため、B・C は全件捨てられる）
ANSWER_A = {"items": [
    {"kind": "principle", "summary": "温度の上がり方でファンの速さを変える", "quote": "温度が急上昇したらファンを速く回し"},
    {"kind": "effect", "summary": "下がるときは穏やかに遅くなる", "quote": "低下時は緩やかに減速する"},
]}
ANSWER_B = {"items": [
    {"kind": "problem", "summary": "回転体の振動が問題になる", "quote": "回転体の振動を検知したら"},
    {"kind": "mechanism", "summary": "質量の再配分で共振を抑える", "quote": "共振を段階的に抑える"},
]}
ANSWER_C = {"items": [
    {"kind": "constraint", "summary": "油膜が切れる条件で給油が要る", "quote": "摺動部の油膜が切れたら"},
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
    id, status = "job-link", "running"

    def set_progress(self, *args):
        pass


def _fake_model(monkeypatch, answer):
    import json

    from app.addon_runtime import ai
    from app.patents import service

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def fake_complete(target, request, http_request, require_provenance=False):
        return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), None

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", fake_complete)


def _doc(text: str, ext_id: str, title: str) -> str:
    from app.knowledge import store

    if store.find_collection("link-lit") is None:
        store.create_collection("link-lit", shard="patents", kind="patent")
    out = store.add_document("link-lit", [store.NewChunk(text)], title=title, body=text, ext_id=ext_id)
    return f"patents:{out['doc_id']}"


def _extract_and_approve(monkeypatch, text: str, ext_id: str, title: str,
                         answer: dict = ANSWER_A, approve=(0, 1)) -> tuple[str, int]:
    """1 文献を抽出して指定の件を承認済みにする。LLM は偽物（外部へは通信しない）。"""
    from app.knowledge import essence

    _fake_model(monkeypatch, answer)
    ref = _doc(text, ext_id, title)
    essence_id = essence.start(ref)
    asyncio.run(essence.run(Job(), ref, essence_id))
    for index in approve:
        essence.review(f"patents:{essence_id}", index, "approved", reviewer="admin")
    return ref, essence_id


def _two_approved(monkeypatch) -> tuple[tuple[str, int], tuple[str, int]]:
    a = _extract_and_approve(monkeypatch, TEXT_A, "link-a", "冷却", answer=ANSWER_A)
    b = _extract_and_approve(monkeypatch, TEXT_B, "link-b", "振動", answer=ANSWER_B)
    return a, b


# ---- 作成: 承認済みどうし・別の文献・理由のメモ必須 ----

def test_create_links_approved_items_of_different_documents(monkeypatch):
    from app.knowledge import links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    out = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "同じ制御思想を振動に適用できる", user="admin")
    assert out["basis"] == "inferred" and out["review_status"] == "unreviewed"
    assert out["source"]["doc_ref"] == ref_a and out["source"]["summary"] == "温度の上がり方でファンの速さを変える"
    assert out["target"]["doc_ref"] == ref_b and out["target"]["summary"] == "質量の再配分で共振を抑える"
    assert out["source"]["doc_title"] == "冷却" and out["target"]["doc_title"] == "振動"

    # 未承認の側からは結べない（抽出はあるが承認していない文献）
    (ref_c, c_id) = _extract_and_approve(monkeypatch, TEXT_C, "link-c", "潤滑", answer=ANSWER_C, approve=())
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{c_id}", 0, f"patents:{b_id}", 1, "未承認の知識")
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 0, f"patents:{c_id}", 0, "相手も未承認")
    # 無い件・範囲外の件
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 99, f"patents:{b_id}", 1, "範囲外")
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 99, "範囲外")


def test_create_refuses_same_document_empty_note_and_duplicates(monkeypatch):
    from app.knowledge import links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    # 同じ文献の中では結べない（別の文献を選ぶもの）
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 0, f"patents:{a_id}", 1, "同じ文献")
    # 理由のメモは必須（空・空白だけは断る）
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "")
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "   ")
    links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "同じ制御思想")
    # 同じ組合せの再作成は断る（一覧から確認してもらう）
    with pytest.raises(store.KnowledgeError):
        links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "同じ組合せ")
    # 逆方向（B から A へ）は別のリンクとして作れる
    out = links.create(f"patents:{b_id}", 1, f"patents:{a_id}", 0, "逆方向")
    assert out["source"]["doc_ref"] == ref_b and out["target"]["doc_ref"] == ref_a


# ---- レビュー（追記だけ）と失効（stale）の伝わり ----

def test_link_is_unreviewed_until_reviewed_and_reviews_append(monkeypatch):
    from app.knowledge import links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    link_ref = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "理由")["link_ref"]
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["review_status"] == "unreviewed" and found[link_ref]["review"] is None

    # 承認 → 一覧に承認済みとして出る
    links.review(link_ref, "approved", reviewer="admin")
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["review_status"] == "approved"
    assert found[link_ref]["review"]["reviewer"] == "admin"
    # 却下で上書き（追記だけ。最新のレビューがそのリンクの状態）
    links.review(link_ref, "rejected", reviewer="admin")
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["review_status"] == "rejected"
    # 判断は承認か却下のどちらか
    with pytest.raises(store.KnowledgeError):
        links.review(link_ref, "returned")
    # 無いリンク・分野の無い id
    with pytest.raises(store.KnowledgeError):
        links.review("patents:99999", "approved")
    with pytest.raises(store.KnowledgeError):
        links.review("99999", "approved")


def test_link_goes_stale_when_source_essence_stales(monkeypatch):
    from app.knowledge import links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    link_ref = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "理由")["link_ref"]
    links.review(link_ref, "approved", reviewer="admin")

    # 結ぶ側（source）の原文が変わった（差し替えで文書の id も新しくなる。リンクも付け直される）:
    # リンクは stale になり、承認は効かなくなる
    new_a = _doc(TEXT_A + "追記の一文。", "link-a", "冷却")
    found = {l["link_ref"]: l for l in links.list_for(new_a)["links"]}
    assert found[link_ref]["stale"] is True
    assert found[link_ref]["review_status"] == "unreviewed"
    assert found[link_ref]["review"]["decision"] == "approved"  # 前の判断は残る（効いていない）
    assert found[link_ref]["source"] is None  # 失効した側の知識は一覧から落ちる
    # stale なリンクにはレビューを付けられない
    with pytest.raises(store.KnowledgeError):
        links.review(link_ref, "approved")

    # 同じ文面へ戻しても承認は戻らない（K1 の epoch と同じ決まり）
    back_a = _doc(TEXT_A, "link-a", "冷却")
    found = {l["link_ref"]: l for l in links.list_for(back_a)["links"]}
    assert found[link_ref]["stale"] is True


def test_link_goes_stale_when_target_essence_stales(monkeypatch):
    from app.knowledge import links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    link_ref = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "理由")["link_ref"]
    links.review(link_ref, "approved", reviewer="admin")
    # 結ばれる側（target）の原文が変わっても失効する（source の文献の一覧で見える）
    _doc(TEXT_B + "追記の一文。", "link-b", "振動")
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["stale"] is True
    assert found[link_ref]["target"] is None
    with pytest.raises(store.KnowledgeError):
        links.review(link_ref, "approved")


def test_list_for_shows_both_sides_with_summaries(monkeypatch):
    from app.knowledge import links

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    link_ref = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "同じ制御思想", user="taro")["link_ref"]
    # 結ぶ側の一覧
    for ref in (ref_a, ref_b):
        found = {l["link_ref"]: l for l in links.list_for(ref)["links"]}
        assert link_ref in found
        link = found[link_ref]
        assert link["basis"] == "inferred" and link["note"] == "同じ制御思想"
        assert link["source"]["doc_title"] == "冷却" and link["target"]["doc_title"] == "振動"
        assert link["source"]["summary"] == "温度の上がり方でファンの速さを変える"
        assert link["stale"] is False
    # 関係の無い文献には出ない
    (ref_c, _) = _extract_and_approve(monkeypatch, TEXT_C, "link-c", "潤滑", answer=ANSWER_C, approve=(0,))
    assert all(l["link_ref"] != link_ref for l in links.list_for(ref_c)["links"])


def test_link_endpoints_keep_sync_work_off_the_event_loop():
    import inspect

    from app.knowledge import links, router

    for fn in (router.get_knowledge_map, router.list_links, router.create_link, router.review_link):
        assert not inspect.iscoroutinefunction(fn)
    assert not inspect.iscoroutinefunction(links.list_for)  # DB を同期的に読むので thread でだけ呼ぶ


def test_link_across_shards_survives_target_replacement(monkeypatch):
    """target が別の分野にいるリンク: target の差し替えで文書の id が変わっても、
    一覧は落ちずに失効（stale）として見えること。付け替えはコミットの後に
    ほかの分野の DB へも届くこと（app/knowledge/store.py の _carry_links_other_shards）。"""
    from app.knowledge import essence, links, store

    (ref_a, a_id), _ = _two_approved(monkeypatch)
    if store.find_collection("link-gen") is None:
        store.create_collection("link-gen", shard="general", kind="general")
    out = store.add_document("link-gen", [store.NewChunk(TEXT_B)], title="振動（一般）",
                             body=TEXT_B, ext_id="link-gen-b")
    ref_g = f"general:{out['doc_id']}"
    _fake_model(monkeypatch, ANSWER_B)
    g_id = essence.start(ref_g)
    asyncio.run(essence.run(Job(), ref_g, g_id))
    essence.review(f"general:{g_id}", 1, "approved", reviewer="admin")

    link_ref = links.create(f"patents:{a_id}", 0, f"general:{g_id}", 1, "分野をまたぐ仮説")["link_ref"]
    link_id = int(link_ref.partition(":")[2])
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["target"]["doc_ref"] == ref_g and found[link_ref]["stale"] is False

    # target（general 側）の文書を差し替える: id が新しくなる。ほかの分野に置いてある
    # リンク行もコミットの後に付け替わる（残っていれば一覧は失効として出るだけ）
    out2 = store.add_document("link-gen", [store.NewChunk(TEXT_B + "追記の一文。")], title="振動（一般）",
                              body=TEXT_B + "追記の一文。", ext_id="link-gen-b")
    new_g = f"general:{out2['doc_id']}"
    conn = links.connect("patents")
    try:
        row = conn.execute("SELECT target_doc_ref FROM knowledge_links WHERE id=?", (link_id,)).fetchone()
    finally:
        conn.close()
    assert row["target_doc_ref"] == new_g
    # 一覧は落ちず、リンクは失効（知識の承認が今の版で効かなくなった）として見える
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["stale"] is True and found[link_ref]["target"] is None


def test_list_for_keeps_links_apart_by_shard(monkeypatch):
    """リンクの id は分野ごとに振られる。同じ番号でも別の分野のリンクは別々に一覧に出ること
    （番号だけで重複を除くと、別の分野のリンクが黙って消えて承認・却下できなくなる）。
    2026-11-22 レビューの major 指摘で直した。"""
    from app.knowledge import essence, links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    p_link = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "同じ分野")["link_ref"]

    if store.find_collection("link-gen") is None:
        store.create_collection("link-gen", shard="general", kind="general")
    out = store.add_document("link-gen", [store.NewChunk(TEXT_C)], title="潤滑", body=TEXT_C, ext_id="link-g1")
    ref_g = f"general:{out['doc_id']}"
    _fake_model(monkeypatch, ANSWER_C)
    g_id = essence.start(ref_g)
    asyncio.run(essence.run(Job(), ref_g, g_id))
    essence.review(f"general:{g_id}", 0, "approved", reviewer="admin")
    g_link = links.create(f"general:{g_id}", 0, f"patents:{b_id}", 1, "別の分野から")["link_ref"]

    # どちらも id 1（分野ごとの番号なので同じ番号になる）
    assert p_link == "patents:1" and g_link == "general:1"
    # 結ばれる側（patents:2）の一覧に、分野違いの同じ番号も両方出る
    found = {l["link_ref"]: l for l in links.list_for(ref_b)["links"]}
    assert p_link in found and g_link in found


def test_delete_document_and_collection_remove_links(monkeypatch):
    """文書・コレクションの削除が、リンクの表があっても落ちることなく通ること。
    削除した文書を source・target に持つリンクは、（ほかの分野に置いてあるものも）消えること。
    （2026-11-22 レビューの critical: _delete_link_rows のバインド数ミスで、リンクの表が
    ある分野では文書削除が 500 になっていた。リンクの表は一覧を開くだけで全分野に作られる。）"""
    from app.knowledge import essence, links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    if store.find_collection("link-gen") is None:
        store.create_collection("link-gen", shard="general", kind="general")
    out = store.add_document("link-gen", [store.NewChunk(TEXT_C)], title="潤滑", body=TEXT_C, ext_id="link-gen-c")
    ref_g = f"general:{out['doc_id']}"
    _fake_model(monkeypatch, ANSWER_C)
    g_id = essence.start(ref_g)
    asyncio.run(essence.run(Job(), ref_g, g_id))
    essence.review(f"general:{g_id}", 0, "approved", reviewer="admin")

    link_same = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "同じ分野")["link_ref"]
    link_cross = links.create(f"patents:{a_id}", 0, f"general:{g_id}", 0, "分野をまたぐ")["link_ref"]
    assert len(links.list_for(ref_a)["links"]) == 2  # リンクの表が見えている状態

    # target（general 側）の文書を消す: ほかの分野（patents）に置いてあるリンクも消える
    store.delete_document("link-gen", int(ref_g.split(":")[1]))
    found = {l["link_ref"] for l in links.list_for(ref_a)["links"]}
    assert link_same in found and link_cross not in found

    # source（patents 側）の文書を消す: その文献を指すリンクが消える（コレクションは残る）
    store.delete_document("link-lit", int(ref_a.split(":")[1]))
    assert links.list_for(ref_b)["links"] == []

    # コレクションの削除も、リンクの表があっても落ちずに通る
    store.delete_collection("link-lit")
    assert links.list_for(ref_b)["links"] == []


def test_link_review_does_not_revive_after_reapproval(monkeypatch):
    """リンクの承認は、知識の承認をやり直しても復活しないこと。
    レビューには両側の版の番号（epoch）を記録し、番号が変わったら前の判断は効かない
    （K1 のレビューと同じ考え。2026-11-22 レビューの minor 指摘で直した）。"""
    from app.knowledge import essence, links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    link_ref = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "理由")["link_ref"]
    links.review(link_ref, "approved", reviewer="admin")

    # source の原文を差し替えて、同じ文面へ戻す（epoch が 2 つ増える）
    _doc(TEXT_A + "追記の一文。", "link-a", "冷却")
    back_a = _doc(TEXT_A, "link-a", "冷却")
    found = {l["link_ref"]: l for l in links.list_for(back_a)["links"]}
    assert found[link_ref]["stale"] is True  # 前の版の承認は戻らない

    # 知識を（同じ文面に戻った持越された行で）もう一度承認する: リンクは有効に戻るが、
    # リンクの前の承認は（epoch が変わったので）戻らない
    essence.review(f"patents:{a_id}", 0, "approved", reviewer="admin")
    found = {l["link_ref"]: l for l in links.list_for(back_a)["links"]}
    link = found[link_ref]
    assert link["stale"] is False
    assert link["review_status"] == "unreviewed"
    assert link["review"]["decision"] == "approved" and link["review"]["stale"] is True
    # 改めてレビューし直せる（新しい epoch で記録される）
    links.review(link_ref, "approved", reviewer="admin")
    found = {l["link_ref"]: l for l in links.list_for(back_a)["links"]}
    assert found[link_ref]["review_status"] == "approved"


def test_connect_adds_missing_review_columns_to_existing_tables(monkeypatch):
    """レビュー表に後から足した列（source_epoch・target_epoch）が、既に作られた DB にも
    ALTER で足ること（表を作り直すと既存のレビューが消える）。
    2026-11-22 再レビューの major: 列の無い DB では一覧・レビューが列不明で 500 になっていた。"""
    from app.knowledge import links, store

    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    link_ref = links.create(f"patents:{a_id}", 0, f"patents:{b_id}", 1, "理由")["link_ref"]

    # 旧い形（列がまだ無い）のレビュー表を作り直して、旧 DB の状態を再現する
    conn = store.connect("patents")
    try:
        conn.execute("DROP TABLE knowledge_link_reviews")
        conn.execute("CREATE TABLE knowledge_link_reviews("
                     "id INTEGER PRIMARY KEY, link_id INTEGER NOT NULL, decision TEXT NOT NULL, "
                     "note TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL DEFAULT '', created REAL NOT NULL)")
        conn.commit()
    finally:
        conn.close()

    # connect が ALTER で列を足すので、レビューも一覧も落ちない
    links.review(link_ref, "approved", reviewer="admin")
    found = {l["link_ref"]: l for l in links.list_for(ref_a)["links"]}
    assert found[link_ref]["review_status"] == "approved"


# ---- API（権限の区分・監査の対象。LLM は偽物・外部へは通信しない） ----

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


def test_api_create_list_and_review(monkeypatch, admin_client):
    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    # 作成（201。basis=inferred・未確認）
    r = admin_client.post(f"{API}/essences/patents:{a_id}/items/0/links",
                          json={"target_essence_ref": f"patents:{b_id}", "target_item": 1,
                                "note": "同じ制御思想を振動に適用できる"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["basis"] == "inferred" and body["review_status"] == "unreviewed"
    link_ref = body["link_ref"]
    # 一覧（結ぶ側・結ばれる側のどちらにも）
    r = admin_client.get(f"{API}/documents/{ref_a}/links")
    assert r.status_code == 200 and {l["link_ref"] for l in r.json()["links"]} == {link_ref}
    r = admin_client.get(f"{API}/documents/{ref_b}/links")
    assert r.status_code == 200 and {l["link_ref"] for l in r.json()["links"]} == {link_ref}
    assert r.json()["links"][0]["target"]["summary"] == "質量の再配分で共振を抑える"
    # レビュー（承認）は別の操作
    r = admin_client.post(f"{API}/links/{link_ref}/review", json={"decision": "approved"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["decision"] == "approved"
    assert admin_client.get(f"{API}/documents/{ref_a}/links").json()["links"][0]["review_status"] == "approved"
    # 形の悪い指定は断る
    r = admin_client.post(f"{API}/essences/patents:{a_id}/items/0/links",
                          json={"target_essence_ref": f"patents:{b_id}", "target_item": 1, "note": ""},
                          headers=CSRF_HEADERS)
    assert r.status_code == 422
    r = admin_client.post(f"{API}/essences/patents:{a_id}/items/0/links",
                          json={"target_essence_ref": "patents:99999", "target_item": 0, "note": "無い知識"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 404
    r = admin_client.post(f"{API}/links/{link_ref}/review", json={"decision": "maybe"}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    r = admin_client.post(f"{API}/links/patents:99999/review", json={"decision": "approved"}, headers=CSRF_HEADERS)
    assert r.status_code == 404


def test_api_link_permissions_separate_view_and_edit(client, admin_client, monkeypatch):
    (ref_a, a_id), (ref_b, b_id) = _two_approved(monkeypatch)
    r = admin_client.post(f"{API}/essences/patents:{a_id}/items/0/links",
                          json={"target_essence_ref": f"patents:{b_id}", "target_item": 1, "note": "権限の確認"},
                          headers=CSRF_HEADERS)
    link_ref = r.json()["link_ref"]
    _limited_user("link-operator", "operator", "link-operator-9")
    _limited_user("link-viewer", "viewer", "link-viewer-9")
    # 閲覧だけ（workflows.run）の利用者: 一覧は見られるが、作成もレビューもできない
    _login(client, "link-operator", "link-operator-9")
    assert client.get(f"{API}/documents/{ref_a}/links").status_code == 200
    assert client.post(f"{API}/essences/patents:{a_id}/items/0/links",
                       json={"target_essence_ref": f"patents:{b_id}", "target_item": 1, "note": "×"},
                       headers=CSRF_HEADERS).status_code == 403
    assert client.post(f"{API}/links/{link_ref}/review", json={"decision": "approved"},
                       headers=CSRF_HEADERS).status_code == 403
    # 閲覧の権限も無い利用者: すべて断られる
    _login(client, "link-viewer", "link-viewer-9")
    assert client.get(f"{API}/documents/{ref_a}/links").status_code == 403
    assert client.post(f"{API}/links/{link_ref}/review", json={"decision": "approved"},
                       headers=CSRF_HEADERS).status_code == 403
