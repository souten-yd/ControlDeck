"""AI アシスタントと OpenCode のモデルの選び方をそろえる（2026-11-27）。

目的: (1) 会話で選んだモデルが、OpenCode のモードに切り替えても変わらない（Qwen で始めた会話は OpenCode でも Qwen）。
(2) OpenCode で使えないモデルを選んでいるときは、Models の既定の OpenCode のモデルへ置き換えて 1 行出す。
(3) モデルの一覧は種類ごとのまとまりで、使えない経路（外部送信が許可されていない・Zen のキー無し・秘密の会話）は並べない。
(4) Models で選ぶ OpenCode の既定のモデルが保存・読み出しでき、作業場の順番の先頭になる。
根拠: 2026-11-27 利用者「AI アシスタントで Qwen で会話を始めたが、OpenCode に切り替えた後は GLM が選択されていた」。
原因は、OpenCode の回が会話のモデル（params の model）を見ず、作業場の順番の最初（opencode-go/glm-5.3）で
動いていたこと。OpenCode・会話サーバーは偽物（test_opencode_workspace_persist.py の _fake を使う）。
"""
from types import SimpleNamespace

from app.integrations.opencode import model_choice
from tests.conftest import CSRF_HEADERS
from tests.test_opencode_workspace_persist import _fake, _new_conversation, _ok, _wait_done


def _box(monkeypatch, *, zen=True, llm=True):
    from app.maintenance import sandbox as _sandbox

    box = SimpleNamespace(allow_zen=zen, capabilities={"llm"} if llm else set())
    monkeypatch.setattr(_sandbox, "load", lambda name: box)
    from app.integrations.opencode import zen as zen_mod

    monkeypatch.setattr(zen_mod, "get", lambda: "key" if zen else "")


def _send_code(client, conv_id, **extra):
    body = {"content": "直して", "mode": "code", "model": "qwen-test", **extra}
    r = client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 201, r.text
    return r.json()["assistant_message_id"]


# ---- 原因の再現: 会話のモデルが OpenCode へ渡る ----

def test_conversation_model_follows_into_opencode(admin_client, monkeypatch, tmp_path):
    """Qwen で選んだ会話を OpenCode へ切り替えても、OpenCode のセッションのモデルは Qwen（GLM に戻らない）。"""
    server, calls = _fake(monkeypatch, tmp_path, _ok, chain=["opencode-go/glm-5.3", "controldeck/auto"])
    _box(monkeypatch)
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send_code(admin_client, conv_id, model_explicit=True))
    assert final["status"] == "completed"
    assert calls["switched"] == ["controldeck/qwen-test"]
    assert final["meta"]["model_ref"] == "controldeck/qwen-test"
    assert "model_note" not in final["meta"]


def test_unselected_model_keeps_the_existing_order(admin_client, monkeypatch, tmp_path):
    """会話でモデルを選んでおらず既定も無ければ、従来どおり作業場の順番（切り替えない）。"""
    server, calls = _fake(monkeypatch, tmp_path, _ok, chain=["opencode-go/glm-5.3"])
    _box(monkeypatch)
    monkeypatch.setattr(model_choice, "default_model", lambda: "")
    conv_id = _new_conversation(admin_client)
    _wait_done(admin_client, conv_id, _send_code(admin_client, conv_id))
    assert "switched" not in calls


def test_unselected_model_uses_the_models_default(admin_client, monkeypatch, tmp_path):
    server, calls = _fake(monkeypatch, tmp_path, _ok, chain=["opencode-go/glm-5.3"])
    _box(monkeypatch)
    monkeypatch.setattr(model_choice, "default_model", lambda: "opencode-go/glm-5.3")
    conv_id = _new_conversation(admin_client)
    _wait_done(admin_client, conv_id, _send_code(admin_client, conv_id))
    assert calls["switched"] == ["opencode-go/glm-5.3"]


def test_unusable_model_is_replaced_by_the_default_with_one_line(admin_client, monkeypatch, tmp_path):
    """OpenCode への外部送信が許可されていない外部モデルは、既定へ置き換えて 1 行出す。"""
    server, calls = _fake(monkeypatch, tmp_path, _ok, chain=["opencode-go/glm-5.3"])
    _box(monkeypatch)
    monkeypatch.setattr(model_choice, "default_model", lambda: "opencode-go/glm-5.3")
    conv_id = _new_conversation(admin_client)
    aid = _send_code(admin_client, conv_id, model="cloud:openrouter/x", model_explicit=True)
    final = _wait_done(admin_client, conv_id, aid)
    assert calls["switched"] == ["opencode-go/glm-5.3"]
    assert "OpenCode ではxが使えないのでGLM-5.3にします" in final["content"]
    assert final["meta"]["model_note"].startswith("OpenCode ではxが使えない")


def test_model_choice_is_recorded_on_the_user_message(admin_client, monkeypatch, tmp_path):
    """会話ごとに保存（再読み込み・別の端末でも同じ）: 選んだモデルが発言の meta に残る。"""
    server, calls = _fake(monkeypatch, tmp_path, _ok)
    _box(monkeypatch)
    conv_id = _new_conversation(admin_client)
    aid = _send_code(admin_client, conv_id, model_explicit=True, model_choice="qwen-test", code_model="opencode-go/glm-5.3")
    _wait_done(admin_client, conv_id, aid)
    rows = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
    user = next(m for m in rows if m["role"] == "user")
    assert user["meta"]["model_choice"] == "qwen-test" and user["meta"]["code_model"] == "opencode-go/glm-5.3"


def test_agent_switch_keeps_the_chosen_model_for_roles_that_follow_the_session(tmp_path):
    """プランに切り替えても、セッションのモデルに従う役割は会話で選んだモデルのまま（別に指定した役割は変えない）。"""
    import json

    from app.integrations.opencode import chat_server

    config = tmp_path / "opencode"
    config.mkdir()
    (config / "opencode.json").write_text(json.dumps({
        "model": "controldeck/auto",
        "agent": {"plan": {"model": "controldeck/auto"}, "build": {"model": "opencode/x"}}}), encoding="utf-8")
    server = SimpleNamespace(config=str(tmp_path))
    assert chat_server._agent_model_ref(server, "plan", "controldeck/qwen") == {"providerID": "controldeck", "id": "qwen"}
    assert chat_server._agent_model_ref(server, "build", "controldeck/qwen") == {"providerID": "opencode", "id": "x"}
    assert chat_server._agent_model_ref(server, "plan") == {"providerID": "controldeck", "id": "auto"}


# ---- 解決の決まり ----

def test_resolve_rules():
    r = model_choice.resolve_code_model
    ok = dict(secret=False, zen_ok=True, llm_ok=True, default="")
    assert r("auto", explicit=True, **ok).ref == "controldeck/auto"
    assert r("opencode-go/glm-5.3", explicit=True, **ok).ref == "opencode-go/glm-5.3"
    # Zen のキーが無い・ローカルを借りない箱では使えない
    assert r("opencode/hy4-flash-free", explicit=True, **{**ok, "zen_ok": False}).note
    assert r("qwen", explicit=True, **{**ok, "llm_ok": False}).note
    # 使えなくて既定も無ければ、切り替えず 1 行だけ
    none = r("qwen", explicit=True, **{**ok, "llm_ok": False})
    assert none.ref == "" and "既定のモデルにします" in none.note
    # 秘密の会話は何も使わない
    assert r("qwen", explicit=True, **{**ok, "secret": True}).ref == ""


# ---- 一覧 ----

def _groups(**kw):
    base = dict(local_models=["auto", "qwen"], labels={"qwen": "Qwen3"}, auto_name="Qwen3", gateway_base="http://x/v1",
                secret=False, zen_ok=True)
    return model_choice.groups(**{**base, **kw})


def test_groups_are_split_by_kind_and_hide_unusable_routes(monkeypatch):
    from app.models_mgmt import cloud

    monkeypatch.setattr(cloud, "enabled_models", lambda: [
        {"id": "cloud:openrouter/a:free", "provider": "openrouter", "name": "OpenRouter / a:free"},
        {"id": "cloud:anthropic/c", "provider": "anthropic", "name": "Anthropic（Claude） / c"},
        {"id": "cloud:openai/g", "provider": "openai", "name": "OpenAI / g"}])
    monkeypatch.setattr(cloud, "usable", lambda m, caller: not (m.endswith("/g") or (m.endswith("/c") and caller == "opencode")))
    result = _groups()
    by = {g["id"]: g["models"] for g in result["groups"]}
    assert list(by) == ["local", "openrouter", "opencode", "claude"], "Codex の経路は無いので出さない・使えない OpenAI も出ない"
    assert [m["id"] for m in by["local"]] == ["auto", "qwen"] and by["local"][1]["name"] == "Qwen3"
    assert by["local"][0]["hint"].endswith("（今は Qwen3）")
    assert by["openrouter"][0]["hint"].startswith("外へ出ます・無料枠")
    claude = by["claude"][0]
    assert claude["chat"] is True and claude["code"] is False and "チャット専用" in claude["hint"]
    assert all(m["chat"] is False and m["code"] for m in by["opencode"]), "Zen は OpenCode 専用"
    assert all(m["hint"] for g in by.values() for m in g), "各モデルに一言"


def test_secret_conversation_lists_only_local_models(monkeypatch):
    from app.models_mgmt import cloud

    monkeypatch.setattr(cloud, "enabled_models", lambda: [{"id": "cloud:openrouter/a", "provider": "openrouter", "name": "a"}])
    monkeypatch.setattr(cloud, "usable", lambda m, caller: True)
    result = _groups(secret=True, zen_ok=False)
    assert [g["id"] for g in result["groups"]] == ["local"]
    assert all(m["code"] is False for m in result["groups"][0]["models"]), "秘密の会話は OpenCode を使わない"


def test_zen_group_needs_a_key(monkeypatch):
    from app.models_mgmt import cloud

    monkeypatch.setattr(cloud, "enabled_models", lambda: [])
    assert "opencode" not in [g["id"] for g in _groups(zen_ok=False)["groups"]]


def test_model_choices_endpoint(admin_client, monkeypatch):
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "configured", lambda: True)
    data = admin_client.get("/api/v1/workflows/model-choices").json()
    assert {"groups", "default_model", "base_url", "secret"} <= set(data)
    assert "opencode" in [g["id"] for g in data["groups"]]
    secret = admin_client.get("/api/v1/workflows/model-choices?secret=true").json()
    assert secret["secret"] is True and [g["id"] for g in secret["groups"]] in ([], ["local"])
    assert secret["default_model"] == ""


def test_secret_flag_follows_the_conversation(admin_client, monkeypatch):
    from app.integrations.opencode import zen
    from app.database import SessionLocal
    from app.models import Conversation
    from app.models import utcnow

    monkeypatch.setattr(zen, "configured", lambda: True)
    conv_id = _new_conversation(admin_client)
    assert admin_client.get(f"/api/v1/workflows/model-choices?conversation={conv_id}").json()["secret"] is False
    with SessionLocal() as db:
        db.get(Conversation, conv_id).confidential_at = utcnow()
        db.commit()
    data = admin_client.get(f"/api/v1/workflows/model-choices?conversation={conv_id}").json()
    assert data["secret"] is True and "opencode" not in [g["id"] for g in data["groups"]]


# ---- Models の既定の OpenCode のモデル ----

import pytest  # noqa: E402


@pytest.fixture
def api(admin_client):
    """opencode のルーターは機能が有効なときだけ main に付くので、同じ DB・同じログインで単体のアプリへ付けて使う。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.integrations.opencode.router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app, cookies=admin_client.cookies)
    yield client
    client.close()


def test_default_model_roundtrip_and_validation(api, monkeypatch):
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "configured", lambda: True)
    assert api.get("/api/v1/opencode/default-model").json() == {"default_model": ""}
    r = api.put("/api/v1/opencode/default-model", headers=CSRF_HEADERS, json={"default_model": "opencode-go/glm-5.3"})
    assert r.status_code == 200, r.text
    assert api.get("/api/v1/opencode/default-model").json() == {"default_model": "opencode-go/glm-5.3"}
    bad = api.put("/api/v1/opencode/default-model", headers=CSRF_HEADERS, json={"default_model": "foo/bar"})
    assert bad.status_code == 422
    monkeypatch.setattr(zen, "configured", lambda: False)
    assert api.put("/api/v1/opencode/default-model", headers=CSRF_HEADERS,
                            json={"default_model": "opencode/hy4-flash-free"}).status_code == 422, "キーが無い Zen は選べない"
    cleared = api.put("/api/v1/opencode/default-model", headers=CSRF_HEADERS, json={"default_model": ""})
    assert cleared.status_code == 200 and model_choice.default_model() == ""


def test_local_default_keeps_the_host_model_in_step(api):
    from app.integrations.opencode import provider

    r = api.put("/api/v1/opencode/default-model", headers=CSRF_HEADERS, json={"default_model": "controldeck/auto"})
    assert r.status_code == 200, r.text
    assert provider.get_settings()["model"] == "auto"


def test_default_model_leads_the_workspace_order(monkeypatch):
    from app.maintenance import sandbox
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "key")
    box = SimpleNamespace(meta={}, capabilities={"llm"}, allow_zen=True)
    monkeypatch.setattr(model_choice, "default_model", lambda: "opencode/hy4-flash-free")
    chain = sandbox.model_chain(box)
    assert chain[0] == "opencode/hy4-flash-free" and chain.count("opencode/hy4-flash-free") == 1
    monkeypatch.setattr(model_choice, "default_model", lambda: "")
    assert sandbox.model_chain(box) == sandbox.DEFAULT_MODEL_CHAIN
