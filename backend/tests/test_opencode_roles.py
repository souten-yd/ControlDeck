"""OpenCode の「役割ごとのモデル」（plan / build / research / quick_check / final_check / evaluate）。

目的: 役割ごとに自由にモデルを選べ（全部同じでも全部違ってもよい）、全ての役割にそのモデルが明示されることを、
本番の session・開発の作業場の両方で守る。同じ段に別のローカルがあればお知らせだけ出す（断らない）。
根拠（2026-11-22 利用者、2 回目）: 「個別にモデルを設定できるようにして。全部同じも全部違うもできるように。変な制限は
設けなくて良い」。ゲートウェイは別々のローカルへの同時の要求を順番に載せ替え、生成中の要求は最後まで返す（Opus 実測）。
根拠: 2026-11-22 利用者「計画を実行するモデルと、調査、開発、検証、評価など。制約: 同時に 1 モデルしかできないので、
同時に実行する可能性のある作業は同じモデルにしたい」。ローカルは GPU で同時に 1 つしか動かせない。
最終検証が開発と同じモデルなら別の目で見られないので注意を出す。秘密（confidential）は全役割をローカルへ落とす。
旧 review_model は最終検証として読み、古い設定は動き続ける。
"""
from __future__ import annotations

import json

import pytest

from app.integrations.opencode import provider, roles
from app.integrations.opencode.router import _role_info
from app.maintenance import sandbox
from tests.test_dev_sandbox import repo  # noqa: F401 - 箱を作る fixture
from tests.test_opencode_workflow import CLOUD, _v2, cloud_store, isolated  # noqa: F401


def test_stage_defaults_follow_build_and_plan():
    res = roles.resolve({}, session_model="qwen-flash-next")
    assert res.models == {r: "qwen-flash-next" for r in roles.ROLES}
    assert roles.SAME_AS_BUILD in res.notes, "外部が無く計画も無ければ最終検証は開発と同じ（注意）"
    res = roles.resolve({"build": "maya-s"}, session_model="qwen-flash-next")
    assert res.models["research"] == res.models["quick_check"] == "maya-s", "段 2 は開発と同じ"
    assert res.models["plan"] == "qwen-flash-next"
    res = roles.resolve({"plan": "cloud:x/y"}, session_model="a")
    assert res.models["final_check"] == res.models["evaluate"] == "cloud:x/y", "段 3 は計画と同じ"
    res = roles.resolve({}, session_model="a", first_external="cloud:x/first")
    assert res.models["final_check"] == "cloud:x/first" and res.models["plan"] == "a", "計画が未設定なら外部の最初"
    assert roles.SAME_AS_BUILD not in res.notes


def test_any_combination_is_allowed_and_only_noted():
    # 同じ段に別のローカルでも断らない・揃えない。お知らせだけ出す
    res = roles.resolve({"build": "maya-s", "research": "qwen-flash-next"}, session_model="auto", strict=True)
    assert res.models["build"] == "maya-s" and res.models["research"] == "qwen-flash-next"
    assert any("載せ替え" in n for n in res.notes)
    # 全部違うモデル
    every = {"plan": "a", "build": "b", "research": "c", "quick_check": "d", "final_check": "e", "evaluate": "f", "advise": "g"}
    res = roles.resolve(every, session_model="auto", strict=True)
    assert res.models == every
    # 全部同じモデルならお知らせも出ない（載せ替えは起きない）
    same = {role: "qwen-flash-next" for role in roles.ROLES}
    res = roles.resolve(same, session_model="auto", strict=True)
    assert set(res.models.values()) == {"qwen-flash-next"} and not any("載せ替え" in n for n in res.notes)


def test_external_models_mix_freely_and_stages_may_swap_locals():
    res = roles.resolve({"build": "maya-s", "research": "cloud:a/b", "quick_check": "opencode/glm",
                         "plan": "qwen-flash-next", "final_check": "cloud:c/d", "evaluate": "controldeck/x"},
                        session_model="auto", strict=True)
    assert res.models["research"] == "cloud:a/b" and res.models["quick_check"] == "opencode/glm"
    assert res.models["plan"] != res.models["build"], "段をまたぐ載せ替えは許す"


def test_final_check_same_as_build_warns_but_saves(isolated):
    saved = provider.save_workflow({"roles": {"build": "maya-s", "final_check": "maya-s"}})
    assert saved["roles"]["final_check"] == "maya-s"
    info = _role_info(saved, saved["roles"])
    assert roles.SAME_AS_BUILD in info["notes"] and info["error"] == ""
    assert "別の目で見られません" in roles.SAME_AS_BUILD


def test_save_accepts_two_locals_in_a_stage(isolated):
    saved = provider.save_workflow({"roles": {"build": "maya-s", "research": "qwen-flash-next"}})
    assert saved["roles"]["build"] == "maya-s" and saved["roles"]["research"] == "qwen-flash-next"
    with pytest.raises(ValueError, match="登録されていないモデル"):
        provider.save_workflow({"roles": {"plan": "not-registered"}})


def test_save_allows_cloud_in_a_stage_and_swapping_between_stages(isolated, cloud_store):
    cloud_store.save_egress({"opencode": True})
    saved = provider.save_workflow({"roles": {
        "plan": "maya-s", "build": "qwen-flash-next", "research": CLOUD, "quick_check": "qwen-flash-next",
        "final_check": "maya-s", "evaluate": CLOUD}})
    assert saved["roles"]["research"] == CLOUD and saved["roles"]["plan"] == "maya-s"


def test_review_model_migrates_to_final_check(isolated):
    """旧設定（review_model だけ）は、そのまま最終検証として動く。roles を保存したら空にする。"""
    provider.save_workflow({"review_model": "maya-s"})
    payload, _ = _v2(isolated)
    assert payload["agents"]["reviewer"]["model"] == "controldeck/maya-s"
    res = provider.role_resolution(provider.get_settings(), "qwen-flash-next")
    assert res.models["final_check"] == "maya-s" and "final_check" in res.explicit
    from app.integrations.opencode.router import _roles_for_screen
    assert _roles_for_screen(provider.get_settings())["final_check"] == "maya-s"
    saved = provider.save_workflow({"roles": {"final_check": "maya-s"}})
    assert saved["review_model"] == "" and saved["roles"] == {"final_check": "maya-s"}
    provider.save_workflow({"roles": {}})
    assert provider.role_resolution(provider.get_settings(), "qwen-flash-next").models["final_check"] == "qwen-flash-next"


def test_session_config_puts_a_model_on_every_role_agent(isolated):
    provider.save_workflow({"roles": {"plan": "maya-s", "build": "qwen-flash-next", "research": "qwen-flash-next",
                                      "final_check": "maya-s", "evaluate": "maya-s"}})
    payload, _ = _v2(isolated, model="qwen-flash-next")
    agents = payload["agents"]
    assert agents["plan"]["model"] == "controldeck/maya-s"
    assert agents["build"]["model"] == "controldeck/qwen-flash-next", "セッションと同じでも明示する"
    assert any(rule["effect"] == "deny" for rule in agents["plan"]["permissions"]), "道具の設定は残る"
    assert agents["evaluator"]["model"] == "controldeck/maya-s"
    assert agents["reviewer"]["model"] == "controldeck/maya-s"
    for name in ("researcher", "quick-checker", "evaluator"):
        assert agents[name]["mode"] == "subagent" and agents[name]["system"], name
    # 空だと子は呼んだ側（計画など）のモデルを引き継ぐので、全ての役割に明示する
    assert agents["quick-checker"]["model"] == agents["researcher"]["model"] == "controldeck/qwen-flash-next"
    assert "調査" in agents["researcher"]["description"] and "テスト" in agents["quick-checker"]["description"]
    assert "目的" in agents["evaluator"]["description"]
    assert set(payload["providers"]["controldeck"]["models"]) >= {"qwen-flash-next", "maya-s"}


def test_adversarial_reviewers_follow_final_check(isolated):
    provider.save_workflow({"adversarial_review": True, "roles": {"final_check": "maya-s", "evaluate": "maya-s"}})
    payload, _ = _v2(isolated, model="qwen-flash-next")
    assert payload["agents"]["adversarial-reviewer"]["model"] == "controldeck/maya-s"


def test_confidential_drops_every_role_to_local(isolated, cloud_store):
    from app.security.offline import confidential_scope

    cloud_store.save_egress({"opencode": True})
    provider.save_workflow({"roles": {"plan": CLOUD, "research": CLOUD, "final_check": CLOUD, "evaluate": CLOUD}})
    normal, _ = _v2(isolated, model="qwen-flash-next")
    assert normal["agents"]["reviewer"]["model"] == f"controldeck/{CLOUD}"
    with confidential_scope():
        secret, _ = _v2(isolated, model="qwen-flash-next")
    assert CLOUD not in json.dumps(secret), "外部が選ばれていても全役割をローカルへ（外部のモデルは並べもしない）"
    only = provider._config_payload("job", "http://127.0.0.1:8765/api/v1/llm/v1", "qwen-flash-next", local_only=True)
    assert CLOUD not in json.dumps(only)
    assert all("cloud:" not in str(spec.get("model", "")) for spec in only["agent"].values())


# ---- 開発の作業場（箱） ----

def test_box_agents_get_models_from_roles_and_the_chain(repo, monkeypatch):  # noqa: F811
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "zen-key")
    box = sandbox.create("role1", str(repo))
    config = json.loads(sandbox.opencode_config(box, roles={
        "plan": "opencode-go/glm-5.3", "build": "local", "research": "local",
        "final_check": "opencode/hy4-flash-free", "evaluate": "opencode/hy4-flash-free"}).read_text())
    agents = config["agents"]
    assert agents["plan"]["model"] == "opencode-go/glm-5.3"
    assert agents["reviewer"]["model"] == "opencode/hy4-flash-free"
    assert agents["evaluator"]["model"] == "opencode/hy4-flash-free"
    assert agents["build"]["model"] == agents["researcher"]["model"], "作業のモデルと同じでも明示する"
    assert agents["researcher"]["model"], "空だと呼んだ側のモデルを引き継ぐ"
    assert agents["quick-checker"]["mode"] == "subagent"


def test_box_default_final_check_is_plan_then_the_reviewer_chain(repo, monkeypatch):
    """reviewer_model_for は final_check の既定に統合された: 計画が未設定なら、作業と別のモデル。"""
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "zen-key")
    box = sandbox.create("role2", str(repo))
    config = json.loads(sandbox.opencode_config(box).read_text())
    assert config["agents"]["reviewer"]["model"] == "opencode-go/glm-5.3"
    assert config["agents"]["evaluator"]["model"] == "opencode-go/glm-5.3"
    planned = json.loads(sandbox.opencode_config(box, roles={"plan": "opencode/hy4-flash-free"}).read_text())
    assert planned["agents"]["reviewer"]["model"] == "opencode/hy4-flash-free"
    external = json.loads(sandbox.opencode_config(box, model="opencode-go/glm-5.3", work_model="opencode-go/glm-5.3").read_text())
    assert external["agents"]["reviewer"]["model"] == "opencode/hy4-flash-free", "作業が外部なら別の外部を先に"


def test_box_without_llm_cannot_choose_a_local_model(repo, monkeypatch):
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "zen-key")
    box = sandbox.create("role3", str(repo), target="app", adopt="pr", capabilities=["tools"])
    assert "llm" not in box.capabilities
    res = sandbox.box_roles(box, "opencode-go/glm-5.3", {"final_check": "local", "evaluate": "controldeck/auto",
                                                          "research": "opencode/hy4-flash-free"})
    assert not roles.is_local(res.models["final_check"]) and not roles.is_local(res.models["evaluate"]), "ローカルは選べない"
    assert res.models["research"] == "opencode/hy4-flash-free"
    text = sandbox.opencode_config(box, model="opencode-go/glm-5.3", work_model="opencode-go/glm-5.3",
                                   roles={"final_check": "local"}).read_text()
    assert '"controldeck/auto"' not in json.dumps(json.loads(text)["agents"])


def test_box_keeps_two_locals_in_a_stage(repo):
    box = sandbox.create("role4", str(repo))
    res = sandbox.box_roles(box, "auto", {"final_check": "controldeck/auto", "evaluate": "controldeck/other"})
    assert res.models["final_check"] == "controldeck/auto" and res.models["evaluate"] == "controldeck/other"


def test_api_saves_any_combination_and_returns_the_notes(isolated):
    from app.integrations.opencode import router

    saved = router.put_workflow(router.WorkflowBody(roles={"build": "maya-s", "research": "qwen-flash-next"}), user=None)
    assert saved["role_info"]["effective"]["research"]["model"] == "qwen-flash-next"
    assert any("載せ替え" in n for n in saved["role_info"]["notes"])
    checked = router.check_roles(router.RolesCheckBody(roles={"build": "maya-s", "research": "qwen-flash-next"}), user=None)
    assert not checked["error"]
    ok = router.put_workflow(router.WorkflowBody(roles={"build": "maya-s", "final_check": "maya-s"}), user=None)
    assert ok["role_info"]["effective"]["research"]["model"] == "maya-s"
    assert roles.SAME_AS_BUILD in ok["role_info"]["notes"]


def test_host_never_defaults_a_role_to_an_external_model(isolated, cloud_store):
    """ホストの AI アシスタントでは、外部を許可していても、役割を明示しない限り外部へコードを送らない
    （2026-11-22 Opus の監査。既定の最終検証が黙って外部になっていた）。"""
    cloud_store.save_egress({"opencode": True})
    payload, _ = _v2(isolated, model="qwen-flash-next")
    assert "cloud:" not in json.dumps(payload["agents"]), "既定の役割に外部のモデルが入った"


def test_box_lists_every_local_role_model(repo):  # noqa: F811
    """役割で選んだローカルのモデルが、箱の OpenCode のモデルの一覧に載ること。
    根拠: 2026-11-22 全部違うモデルの実機で「Model unavailable: controldeck/qwen-flash-next」で止まった。"""
    box = sandbox.create("role5", str(repo))
    config = json.loads(sandbox.opencode_config(box, roles={
        "build": "controldeck/qwen-flash-next", "research": "controldeck/maya-deepseek-xs",
        "quick_check": "controldeck/qwen-flash-next-uc"}).read_text())
    listed = set(config["providers"]["controldeck"]["models"])
    assert {"qwen-flash-next", "maya-deepseek-xs", "qwen-flash-next-uc"} <= listed
