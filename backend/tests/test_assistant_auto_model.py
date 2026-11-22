"""目的: AI アシスタントのヘッダの「auto」に出す実モデル（llm-endpoints の auto_resolves_to）が、
ゲートウェイの転送と同じ解き方（選択中のランタイムの優先モデル・載っているもの優先）になること。解けないときは None。
根拠: 2026-11-23 利用者の通し（評価 U11）。「auto」では実際に何のモデルが動くか分からなかった。"""
from app.models_mgmt import local_llm
from app.models_mgmt import runtime_policy as rp
from app.workflows import router as wf


def _rows(*aliases, loaded=()):
    return [{"runtime": "strata", "alias": a, "port": 8090 + i, "role": "llm", "activated": True,
             "loaded": a in loaded} for i, a in enumerate(aliases)]


def test_auto_target_follows_selected_runtime_preference_and_loaded(monkeypatch):
    policy = rp.RuntimePolicy(selected_runtime="strata", preferred_models={"strata": "qwen-flash-next"})
    monkeypatch.setattr(rp, "get_policy", lambda: policy)
    rows = _rows("other", "qwen-flash-next")
    monkeypatch.setattr(local_llm, "list_instances", lambda **kw: rows)
    got = wf._auto_target({"qwen-flash-next": "Qwen Flash"})
    assert got == {"alias": "qwen-flash-next", "name": "Qwen Flash", "runtime": "strata", "loaded": False}
    rows[0]["loaded"] = True          # 載っているものが優先
    got = wf._auto_target({})
    assert (got["alias"], got["name"], got["loaded"]) == ("other", "other", True)


def test_auto_target_is_none_when_nothing_registered(monkeypatch):
    monkeypatch.setattr(local_llm, "list_instances", lambda **kw: [])
    assert wf._auto_target({}) is None
