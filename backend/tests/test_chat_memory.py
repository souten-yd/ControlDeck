"""AI アシスタントの会話をまたぐ記憶（app/memory）。情報の流出を防ぐ守りを厚く確かめる。

目的:
- 置き場: 本文が DB ファイル（WAL 含む）に平文で無い・秘密の会話と特許の流れから作った記憶は必ず protected・守りを外せない。
- 入れ分け（policy）: protected は秘密の会話の中にだけ入る・秘密でない会話には本文も件数も出ない・外部の LLM には既定で入らず、
  設定で入れても normal だけ・OpenCode は既定で入らず、設定で入れても normal だけ。
- 検索への漏れ: 記憶は答えを作る段にだけ入り、Web 検索の語を作る段（_server_search に渡す文）・道具の引数に渡らない。
  モデルが記憶の言葉を道具の引数へ写したときは、道具を実行する前に止める。
- 作る側: 取り出しは必ずローカルの LLM（会話がクラウドでも）。ローカルが無ければ作らない。切／明示／確認／自動・会話ごとの切り替え。
- 消す・見せる: 削除・会話と一緒に消す・一覧／使った記憶の API は秘密でない会話に protected を返さない。監査に本文が無い。
根拠: 2026-11-23 利用者「保護された会話や特許のアイデア関連の記憶はインターネット検索を保護されていない会話には露呈できない」。
実際の LLM・外部へは通信しない（すべてモック）。"""
import asyncio
import json
import sqlite3

import pytest

from app.memory import policy, service, store
from tests.conftest import CSRF_HEADERS

UID = 4242
SECRET_TEXT = "利用者は特許出願前の磁気浮上モータの新構造を検討している"
NORMAL_TEXT = "利用者は回答を箇条書きで短くまとめる文体を好む"


@pytest.fixture(autouse=True)
def clean_memory(client):
    store.delete_all(UID)
    yield
    store.delete_all(UID)


def _both():
    normal = store.add(UID, NORMAL_TEXT, kind="preference")
    protected = store.add(UID, SECRET_TEXT, kind="work", source_kind="confidential", source_id="x")
    return normal, protected


# ---- 置き場 ----

def test_text_is_encrypted_in_the_database_file():
    store.add(UID, NORMAL_TEXT)
    store.add(UID, SECRET_TEXT, source_kind="patent")
    conn = store.connect()
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    raw_rows = [r["text"] for r in conn.execute("SELECT text FROM memories")]
    conn.close()
    assert raw_rows and all("利用者" not in t and "磁気" not in t for t in raw_rows)
    blob = b"".join(p.read_bytes() for p in store.db_path().parent.glob("memory.sqlite3*"))
    assert "磁気浮上".encode() not in blob and "箇条書き".encode() not in blob
    assert [m.text for m in store.list_memories(UID)] == [SECRET_TEXT, NORMAL_TEXT]   # 読むときは復号される


def test_confidential_and_patent_origin_are_forced_protected_and_cannot_be_unprotected():
    secret = store.add(UID, "秘密の会話で聞いた話", sensitivity="normal", source_kind="confidential", source_id="c1")
    patent = store.add(UID, "特許の案件で出た話", sensitivity="normal", source_kind="patent", source_id="c2")
    chat = store.add(UID, "ふつうの会話の話", source_kind="chat", source_id="c3")
    assert secret.protected and patent.protected and not chat.protected
    for m in (secret, patent):
        with pytest.raises(store.MemoryError_):
            store.update(UID, m.id, protected=False)
        assert store.get(UID, m.id).protected
    assert store.update(UID, chat.id, protected=True).protected          # 利用者が「守る」にしたもの
    assert not store.update(UID, chat.id, protected=False).protected      # これは外せる（元が普通の会話）
    assert store.add(UID, "ふつうの会話の話") is None                      # 重複は足さない


def test_unknown_sensitivity_falls_back_to_protected():
    assert store.add(UID, "分類不明", sensitivity="???").protected


def test_midway_secret_pulls_existing_memories_of_that_conversation_to_protected():
    mine = store.add(UID, "途中で秘密になる会話の話", source_id="cx")
    other = store.add(UID, "別の会話の話", source_id="cy")
    store.protect_by_source(UID, "cx")
    assert store.get(UID, mine.id).protected and store.get(UID, mine.id).forced_protected
    assert not store.get(UID, other.id).protected


def test_patent_flag_is_one_way():
    store.set_flags("cv-flag", UID, patent=True)
    assert store.set_flags("cv-flag", UID, patent=False)["patent"] is True
    assert store.set_flags("cv-flag", UID, no_use=True)["patent"] is True


# ---- 入れ分けの規則（policy） ----

PREFS = dict(store.PREF_DEFAULTS)
NO_FLAGS = {"no_use": False, "no_save": False}


def test_policy_matrix():
    allowed = policy.allowed_sensitivities
    S = policy.Situation
    assert allowed(S(secret=True, external=False), PREFS, NO_FLAGS) == {"normal", "protected"}
    assert allowed(S(secret=False, external=False), PREFS, NO_FLAGS) == {"normal"}          # 秘密でない会話に protected は無い
    assert allowed(S(secret=False, external=True), PREFS, NO_FLAGS) == frozenset()          # 外部の LLM は既定で無し
    on = {**PREFS, "use_external": True, "use_opencode": True}
    assert allowed(S(secret=False, external=True), on, NO_FLAGS) == {"normal"}              # 設定で入れても normal だけ
    assert allowed(S(secret=True, external=True), on, NO_FLAGS) == frozenset()              # 万一でも秘密 + 外部は無し
    assert allowed(S(secret=False, external=False, code=True), PREFS, NO_FLAGS) == frozenset()   # OpenCode は既定で無し
    assert allowed(S(secret=False, external=False, code=True), on, NO_FLAGS) == {"normal"}
    assert allowed(S(secret=True, external=False, code=True), on, NO_FLAGS) == frozenset()
    assert allowed(S(secret=True, external=False), {**PREFS, "enabled": False}, NO_FLAGS) == frozenset()
    assert allowed(S(secret=True, external=False), PREFS, {"no_use": True}) == frozenset()   # この会話で使わない


def test_rank_never_returns_a_kind_that_is_not_allowed():
    normal, protected = _both()
    pool = store.list_memories(UID)
    assert [m.id for m in policy.rank(pool, "磁気浮上モータ", allowed=policy.NORMAL)] == [normal.id]
    got = {m.id for m in policy.rank(pool, "磁気浮上モータ", allowed=policy.BOTH)}
    assert got == {normal.id, protected.id}
    pending = store.add(UID, "承認待ちの候補です", pending=True)
    assert pending.id not in {m.id for m in policy.rank(store.list_memories(UID), "承認待ち", allowed=policy.BOTH)}


def test_rank_keeps_top_five_and_prefers_pinned_and_matches():
    for i in range(8):
        store.add(UID, f"利用者は項目{i}番について専用のスクリプトを使う", kind="fact")
    pinned = store.add(UID, "利用者の呼び名はソウです", pinned=True)
    top = policy.rank(store.list_memories(UID), "項目3番のスクリプト", allowed=policy.NORMAL)
    assert len(top) == policy.TOP_N and top[0].id == pinned.id
    assert any("項目3番" in m.text for m in top)


def test_leak_guard_flags_copied_words_but_not_the_users_own():
    mem = store.add(UID, SECRET_TEXT)
    assert policy.leaks_into_arguments('{"query": "磁気浮上モータ 新構造 特許出願前"}', [mem], "今日の天気は？")
    # 利用者が会話の中で書いた言葉なら、検索語に出てもよい（写したのではない）
    assert not policy.leaks_into_arguments('{"query": "磁気浮上モータの新構造"}', [mem], "磁気浮上モータの新構造について調べて")
    assert not policy.leaks_into_arguments('{"query": "横浜 天気"}', [mem], "天気は？")


# ---- 会話の実行（答えの段にだけ入る） ----

def _chat_setup(conv, msg, secret=False):
    from app.database import SessionLocal
    from app.models import ChatMessage, Conversation, utcnow

    with SessionLocal() as db:
        db.add(Conversation(id=conv, owner_user_id=None, confidential_at=utcnow() if secret else None))
        db.flush()
        db.add(ChatMessage(id=msg, conversation_id=conv, role="assistant", status="generating"))
        db.commit()


def _run(cp, conv, msg, params, text="明日の横浜の天気は？"):
    from app.jobs import service as jobs

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, msg, conv, [{"role": "user", "content": text}],
            {"base_url": "http://127.0.0.1:9/v1", "model": "m", "thinking": "off", "max_output_tokens": 128,
             "owner_user_id": UID, **params}))
        for _ in range(300):
            await asyncio.sleep(0.02)
            if job.status not in ("queued", "running"):
                break
        return job

    return asyncio.run(scenario())


def _provider(monkeypatch, cp, requests, script=None):
    from app.models_mgmt.runtime_provider import RuntimeChunk
    from app.workflows import rag

    class Provider:
        tools_verified = True

        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            if script and len(requests) == 1:
                yield RuntimeChunk("tool_call", tool_calls=script)
                return
            yield RuntimeChunk("content", content="答え")

        async def cancel(self, request_id):
            return False

    async def fake_ctx(base_url, model):
        return 32768

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    monkeypatch.setattr(cp, "_context_max", fake_ctx)
    monkeypatch.setattr(rag, "collection_exists", lambda name: False)


def _sent_text(requests) -> str:
    return json.dumps([m.get("content") for r in requests for m in r.messages], ensure_ascii=False)


@pytest.mark.parametrize("secret", [True, False])
def test_protected_memory_enters_only_secret_conversation_and_is_not_even_counted_elsewhere(client, monkeypatch, secret):
    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.models import ChatMessage

    _both()
    conv, msg = f"mem-cv-{secret}", f"mem-am-{secret}"
    _chat_setup(conv, msg, secret=secret)
    requests = []
    _provider(monkeypatch, cp, requests)
    job = _run(cp, conv, msg, {"mode": "chat"}, text="磁気浮上モータの新構造の続きを考えたい")   # 守られた記憶に関係する発言
    assert job.status == "succeeded", job.error
    sent = _sent_text(requests)
    assert NORMAL_TEXT in sent
    assert (SECRET_TEXT in sent) is secret                    # protected は秘密の会話にだけ
    with SessionLocal() as db:
        meta = json.loads(db.get(ChatMessage, msg).meta_json or "{}")
    assert meta["memory"]["count"] == (2 if secret else 1)    # 秘密でない会話では件数にも出ない
    assert len(meta["memory"]["ids"]) == meta["memory"]["count"]
    assert NORMAL_TEXT not in json.dumps(meta, ensure_ascii=False)   # 本文は会話の DB へ複写しない


def test_memory_is_not_given_when_disabled_or_conversation_opted_out(client, monkeypatch):
    import app.workflows.chat_persist as cp

    store.add(UID, NORMAL_TEXT)
    requests = []
    _provider(monkeypatch, cp, requests)
    _chat_setup("mem-cv-off1", "mem-am-off1")
    store.set_flags("mem-cv-off1", UID, no_use=True)
    assert _run(cp, "mem-cv-off1", "mem-am-off1", {"mode": "chat"}).status == "succeeded"
    assert NORMAL_TEXT not in _sent_text(requests)
    requests.clear()
    store.save_prefs(UID, {"enabled": False})
    _chat_setup("mem-cv-off2", "mem-am-off2")
    assert _run(cp, "mem-cv-off2", "mem-am-off2", {"mode": "chat"}).status == "succeeded"
    assert NORMAL_TEXT not in _sent_text(requests)
    store.save_prefs(UID, {"enabled": True})


@pytest.mark.parametrize("allow_external", [False, True])
def test_external_llm_conversation_gets_memory_only_when_enabled_and_only_normal(client, monkeypatch, allow_external):
    import app.workflows.chat_persist as cp

    _both()
    store.save_prefs(UID, {"use_external": allow_external})
    requests = []
    _provider(monkeypatch, cp, requests)
    conv, msg = f"mem-cv-ext{allow_external}", f"mem-am-ext{allow_external}"
    _chat_setup(conv, msg)
    job = _run(cp, conv, msg, {"mode": "chat", "base_url": "https://api.example.invalid/v1", "model": "cloud:x/y"})
    assert job.status == "succeeded", job.error
    sent = _sent_text(requests)
    assert (NORMAL_TEXT in sent) is allow_external
    assert SECRET_TEXT not in sent                            # どちらでも protected は外へ出ない
    store.save_prefs(UID, {"use_external": False})


def test_search_query_stage_never_receives_memory_but_the_answer_stage_does(client, monkeypatch):
    """Web 検索の語を作る段（_server_search。research_intent を呼ぶ）へ渡る文に、記憶は入らない。答えの段には入る。"""
    import app.workflows.chat_persist as cp

    store.add(UID, NORMAL_TEXT, kind="preference")
    seen = {}

    async def fake_search(job, buf, conv_id, mode, query, params, checkpoint, recent=""):
        seen.update(query=query, recent=recent, params=json.dumps(params, default=str))
        return [{"role": "system", "content": "検索資料"}, {"role": "user", "content": query}]

    monkeypatch.setattr(cp, "_server_search", fake_search)

    async def no_register(*a, **k):
        return None

    monkeypatch.setattr(cp, "register_chat_document", no_register)
    requests = []
    _provider(monkeypatch, cp, requests)
    _chat_setup("mem-cv-web", "mem-am-web")
    job = _run(cp, "mem-cv-web", "mem-am-web", {"mode": "web"}, text="横浜の天気を調べて")
    assert job.status == "succeeded", job.error
    assert NORMAL_TEXT not in json.dumps(seen, ensure_ascii=False)           # 検索の語の段には渡らない
    assert NORMAL_TEXT in _sent_text(requests)                                # 答えの段には入る


def test_tool_arguments_that_copy_memory_are_blocked_before_any_search(client, monkeypatch):
    """モデルが記憶の言葉を検索語へ写しても、通信の前に止める（記憶が検索エンジンへ漏れない）。"""
    import app.workflows.chat_persist as cp
    from app.workflows import research_tools

    store.add(UID, SECRET_TEXT)   # normal として入る（秘密でない会話・元の会話は chat）
    searched = []

    async def forbidden(*a, **k):
        searched.append(a)
        raise AssertionError("検索した")

    monkeypatch.setattr(research_tools, "run_search_tool", forbidden)
    monkeypatch.setattr(research_tools, "searx_for", lambda *a, **k: None)
    store.update(UID, store.list_memories(UID)[0].id, kind="preference")   # 好みは常に答えの段へ入る
    requests = []
    call = [{"id": "c1", "name": "web_search", "arguments": json.dumps({"query": "磁気浮上モータ 新構造 特許出願前"}, ensure_ascii=False)}]
    _provider(monkeypatch, cp, requests, script=call)
    _chat_setup("mem-cv-leak", "mem-am-leak")
    job = _run(cp, "mem-cv-leak", "mem-am-leak", {"mode": "chat", "mode_choice": "auto"}, text="今日の話題を調べて")
    assert job.status == "succeeded", job.error
    assert SECRET_TEXT in _sent_text(requests[:1])            # 答えの段には入っている
    assert searched == []                                     # 検索は実行されていない
    tool_msgs = [m for m in requests[1].messages if m.get("role") == "tool"]
    assert tool_msgs and "覚えている内容が含まれている" in tool_msgs[0]["content"]


def test_opencode_turn_gets_memory_only_by_setting_and_only_normal(client):
    async def prep(**kw):
        return await service.prepare("c", UID, query="文体", secret=False, external=False, code=True, **kw)

    _both()
    assert asyncio.run(prep()) == []
    store.save_prefs(UID, {"use_opencode": True})
    got = asyncio.run(prep())
    assert [m.text for m in got] == [NORMAL_TEXT]
    store.save_prefs(UID, {"use_opencode": False})


# ---- 作る（手元の LLM だけ） ----

@pytest.fixture()
def extractor(monkeypatch):
    """取り出しの LLM をモックし、呼ばれた宛先と、機密の枠の中で呼ばれたかを記録する。"""
    from app.security import offline
    from app.workflows import chat_router

    calls = {"targets": [], "scopes": [], "answer": json.dumps(
        {"memories": [{"text": "利用者は回答を箇条書きで好む", "kind": "preference"}]}, ensure_ascii=False)}

    async def fake_llm(messages, base_url, model, api_key, **kw):
        calls["targets"].append((base_url, model))
        calls["scopes"].append(offline.in_confidential_scope())
        return calls["answer"]

    monkeypatch.setattr(chat_router, "_llm", fake_llm)
    monkeypatch.setattr(offline, "local_target", lambda b, m: ("http://127.0.0.1:8081/v1", "local-default"))
    return calls


def _remember(text="これからは箇条書きで答えて。覚えておいて", *, secret=False, conv="rem-cv", model="cloud:x/y",
              base="https://api.example.invalid/v1"):
    return asyncio.run(service.remember_after_turn(conv, UID, user_text=text, assistant_text="はい", mode="chat",
                                                   base_url=base, model=model, secret=secret))


def test_extraction_always_goes_to_the_local_model_even_for_a_cloud_conversation(extractor):
    made = _remember()
    assert made == {"created": 1, "pending": 0}
    assert extractor["targets"] == [("http://127.0.0.1:8081/v1", "local-default")]   # cloud: ではなくローカル
    assert extractor["scopes"] == [True]                                              # 機密の枠の中（ゲートウェイでも cloud: を断る）


def test_no_local_model_means_no_memory_and_no_outbound_call(extractor, monkeypatch):
    from app.security import offline

    def none(b, m):
        raise offline.LocalModelUnavailable("ローカルなし")

    monkeypatch.setattr(offline, "local_target", none)
    assert _remember() == {"created": 0, "pending": 0}
    assert extractor["targets"] == [] and store.list_memories(UID) == []              # 外へは流さない


def test_creation_modes_off_explicit_confirm_auto(extractor):
    plain = "今日は少し疲れた。箇条書きで答えてくれると助かる"
    store.save_prefs(UID, {"creation": "off"})
    assert _remember() == {"created": 0, "pending": 0}
    store.save_prefs(UID, {"creation": "explicit"})
    assert _remember(plain) == {"created": 0, "pending": 0}                           # 「覚えて」が無ければ作らない
    assert extractor["targets"] == []
    assert _remember("覚えなくていいよ。箇条書きで答えて") == {"created": 0, "pending": 0}   # 否定は作らない
    assert _remember() == {"created": 1, "pending": 0}
    store.delete_all(UID)
    store.save_prefs(UID, {"creation": "confirm"})
    assert _remember(plain) == {"created": 0, "pending": 1}                           # 候補にとどめる
    cand = store.list_memories(UID)[0]
    assert cand.pending
    assert policy.rank(store.list_memories(UID), "箇条書き", allowed=policy.BOTH) == []   # 承認前は使わない
    assert _remember() == {"created": 0, "pending": 0}                                # 同じ内容は足さない
    store.delete_all(UID)
    store.save_prefs(UID, {"creation": "auto"})
    assert _remember(plain) == {"created": 1, "pending": 0}
    store.save_prefs(UID, {"creation": "explicit"})


def test_conversation_opt_out_of_saving(extractor):
    store.set_flags("rem-cv-ns", UID, no_save=True)
    assert _remember(conv="rem-cv-ns") == {"created": 0, "pending": 0}
    assert extractor["targets"] == []


def test_secret_and_patent_conversations_create_protected_memory(extractor):
    assert _remember(secret=True, conv="rem-secret")["created"] == 1
    made = store.list_memories(UID)[0]
    assert made.protected and made.source_kind == "confidential" and made.source_id == "rem-secret"
    store.delete_all(UID)
    store.set_flags("rem-patent", UID, patent=True)
    assert _remember(conv="rem-patent")["created"] == 1
    made = store.list_memories(UID)[0]
    assert made.protected and made.source_kind == "patent"
    store.delete_all(UID)
    assert _remember(conv="rem-plain")["created"] == 1
    assert not store.list_memories(UID)[0].protected


def test_secret_looking_text_and_bad_output_are_not_remembered(extractor):
    fake_key = "-".join(["sk", "abcdef1234567890xyz"])                         # 秘密らしい値は実行時に組み立てる（dev-check）
    extractor["answer"] = json.dumps({"memories": [{"text": f"利用者の API キーは {fake_key}", "kind": "fact"}]})
    assert _remember() == {"created": 0, "pending": 0}
    extractor["answer"] = "覚えました！"                                              # JSON でない返事は作らない
    assert _remember() == {"created": 0, "pending": 0}


def test_patent_collection_detection(monkeypatch):
    import app.workflows.chat_persist as cp
    from app.knowledge import store as kstore

    class Ref:
        def __init__(self, shard):
            self.shard = shard

    monkeypatch.setattr(kstore, "find_collection", lambda name: Ref("patents") if name == "pat" else Ref("general"))
    assert cp._used_patent_collection({"sources": [{"collection": "pat"}]})
    assert not cp._used_patent_collection({"sources": [{"collection": "doc"}]})
    assert not cp._used_patent_collection({})


# ---- API・削除・監査 ----

def test_api_settings_items_and_protection_rules(admin_client):
    c = admin_client
    got = c.get("/api/v1/memory/settings").json()["prefs"]
    assert got == {"enabled": True, "creation": "explicit", "use_external": False, "use_opencode": False}   # 既定
    assert c.put("/api/v1/memory/settings", json={"creation": "bogus"}, headers=CSRF_HEADERS).status_code == 422
    put = c.put("/api/v1/memory/settings", json={"creation": "confirm", "use_external": True}, headers=CSRF_HEADERS)
    assert put.json()["prefs"]["creation"] == "confirm" and put.json()["prefs"]["use_external"] is True
    c.put("/api/v1/memory/settings", json={"creation": "explicit", "use_external": False}, headers=CSRF_HEADERS)

    made = c.post("/api/v1/memory/items", json={"text": "利用者は Python を主に使う", "kind": "fact"}, headers=CSRF_HEADERS)
    assert made.status_code == 201
    mid = made.json()["id"]
    assert c.post("/api/v1/memory/items", json={"text": "利用者は Python を主に使う"}, headers=CSRF_HEADERS).status_code == 409
    edited = c.patch(f"/api/v1/memory/items/{mid}", json={"text": "利用者は Python と Rust を使う", "protected": True, "pinned": True},
                     headers=CSRF_HEADERS).json()
    assert edited["text"].endswith("Rust を使う") and edited["protected"] and edited["pinned"]
    uid = store.list_memories(_admin_id(c))[0].user_id
    forced = store.add(uid, "秘密の会話から", source_kind="confidential", source_id="zz")
    assert c.patch(f"/api/v1/memory/items/{forced.id}", json={"protected": False}, headers=CSRF_HEADERS).status_code == 409
    items = c.get("/api/v1/memory/items").json()["items"]
    assert {i["id"] for i in items} == {mid, forced.id}
    assert next(i for i in items if i["id"] == forced.id)["forced_protected"] is True
    assert c.delete(f"/api/v1/memory/items/{mid}", headers=CSRF_HEADERS).status_code == 204
    assert c.delete(f"/api/v1/memory/items/{mid}", headers=CSRF_HEADERS).status_code == 404
    assert c.delete("/api/v1/memory/items", headers=CSRF_HEADERS).json()["deleted"] == 1
    assert c.get("/api/v1/memory/items").json()["items"] == []


def _admin_id(client) -> int:
    first = client.post("/api/v1/memory/items", json={"text": "id 取得用の記憶です"}, headers=CSRF_HEADERS).json()
    client.delete(f"/api/v1/memory/items/{first['id']}", headers=CSRF_HEADERS)
    from app.database import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        return db.query(User).filter(User.username == "admin").one().id


def test_used_endpoint_never_returns_protected_to_a_normal_conversation(admin_client):
    c = admin_client
    uid = _admin_id(c)
    normal = store.add(uid, NORMAL_TEXT)
    protected = store.add(uid, SECRET_TEXT, source_kind="confidential", source_id="q")
    plain = c.post("/api/v1/chat/conversations", json={}, headers=CSRF_HEADERS).json()["id"]
    secret = c.post("/api/v1/chat/conversations", json={"confidential": True}, headers=CSRF_HEADERS).json()["id"]
    ids = f"{normal.id},{protected.id}"
    got = c.get(f"/api/v1/memory/used?conv_id={plain}&ids={ids}").json()["items"]
    assert [i["id"] for i in got] == [normal.id] and SECRET_TEXT not in json.dumps(got, ensure_ascii=False)
    got = c.get(f"/api/v1/memory/used?conv_id={secret}&ids={ids}").json()["items"]
    assert {i["id"] for i in got} == {normal.id, protected.id}
    assert c.get(f"/api/v1/memory/used?conv_id=nope&ids={ids}").status_code == 404
    store.delete_all(uid)


def test_conversation_flags_api_and_delete_with_memories(admin_client):
    c = admin_client
    uid = _admin_id(c)
    conv = c.post("/api/v1/chat/conversations", json={}, headers=CSRF_HEADERS).json()["id"]
    assert c.get(f"/api/v1/memory/conversations/{conv}/flags").json() == {"no_use": False, "no_save": False}
    got = c.put(f"/api/v1/memory/conversations/{conv}/flags", json={"no_use": True}, headers=CSRF_HEADERS).json()
    assert got == {"no_use": True, "no_save": False}
    assert c.put("/api/v1/memory/conversations/none/flags", json={"no_use": True}, headers=CSRF_HEADERS).status_code == 404
    mine = store.add(uid, "この会話で覚えた話です", source_id=conv)
    keep = store.add(uid, "別の会話で覚えた話です", source_id="other")
    assert c.delete(f"/api/v1/chat/conversations/{conv}", headers=CSRF_HEADERS).status_code == 204   # 既定は記憶を残す
    assert store.get(uid, mine.id) is not None
    conv2 = c.post("/api/v1/chat/conversations", json={}, headers=CSRF_HEADERS).json()["id"]
    mine2 = store.add(uid, "二つ目の会話で覚えた話です", source_id=conv2)
    assert c.delete(f"/api/v1/chat/conversations/{conv2}?delete_memories=true", headers=CSRF_HEADERS).status_code == 204
    assert store.get(uid, mine2.id) is None and store.get(uid, keep.id) is not None
    assert store.get_flags(conv) == {"no_use": False, "no_save": False, "patent": False}       # 会話と一緒に印も消える
    store.delete_all(uid)


def test_midway_secret_api_protects_memories_made_before(admin_client):
    c = admin_client
    uid = _admin_id(c)
    conv = c.post("/api/v1/chat/conversations", json={}, headers=CSRF_HEADERS).json()["id"]
    mem = store.add(uid, "途中で秘密にした会話の話です", source_id=conv)
    assert not mem.protected
    assert c.post(f"/api/v1/chat/conversations/{conv}/confidential", headers=CSRF_HEADERS).status_code == 200
    assert store.get(uid, mem.id).protected
    store.delete_all(uid)


def test_audit_records_create_and_delete_without_the_text(admin_client):
    from app.database import SessionLocal
    from app.models import AuditLog

    c = admin_client
    body = "監査に残してはいけない本文ひみつ"
    made = c.post("/api/v1/memory/items", json={"text": body}, headers=CSRF_HEADERS).json()
    c.delete(f"/api/v1/memory/items/{made['id']}", headers=CSRF_HEADERS)
    with SessionLocal() as db:
        rows = db.query(AuditLog).filter(AuditLog.action.in_(["memory.create", "memory.delete"])).all()
    actions = {r.action for r in rows}
    assert {"memory.create", "memory.delete"} <= actions
    assert all(body not in json.dumps([r.resource_id, r.metadata_json], ensure_ascii=False) for r in rows)
    create = next(r for r in rows if r.action == "memory.create" and r.resource_id == str(made["id"]))
    assert json.loads(create.metadata_json)["chars"] == len(body)


def test_new_database_file_is_separate_from_the_main_database():
    path = store.db_path()
    assert path.name == "memory.sqlite3" and path.parent.name == "memory"
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"memories", "prefs", "conv_flags"} <= tables
