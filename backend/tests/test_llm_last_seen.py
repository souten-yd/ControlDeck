"""最後に見えていたローカル LLM（Home の LLM カードのアンロード表示）。"""
import json

import pytest

from app.models_mgmt import llm_last_seen as seen


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    path = tmp_path / "llm-last-seen.json"
    monkeypatch.setattr(seen, "_path", lambda: path)
    monkeypatch.setattr(seen, "_last", None)
    monkeypatch.setattr(seen, "_saved", None)
    monkeypatch.setattr(seen, "_attempted", None)
    monkeypatch.setattr(seen, "_loaded", False)
    return path


def _endpoint(alias="qwen-flash-next", **kw):
    return {"id": f"strata:{alias}", "label": alias, "port": 8096, "running_alias": alias, "runtime": "strata",
            "available": True, "slots": 1, "busy": 0, "ctx_total": 1048576, "ctx_used": 0, "ctx_active": 322,
            "tokens_per_second": 52.5, "tokens_per_second_single": 52.5, "prefill_tokens_per_second": 376.0,
            "timed_by_controldeck": True, **kw}


def test_remembers_the_running_model_and_returns_it_after_unload(fresh):
    assert seen.update([_endpoint()], now=1000.0) is None          # 動いている間は返さない
    assert json.loads(fresh.read_text())["running_alias"] == "qwen-flash-next"
    last = seen.update([], now=2000.0)
    assert last["running_alias"] == "qwen-flash-next" and last["tokens_per_second"] == 52.5
    assert last["ctx_active"] == 322 and last["at"] == 1000.0


def test_idle_zero_speed_does_not_erase_the_last_speed(fresh):
    seen.update([_endpoint()], now=1000.0)
    seen.update([_endpoint(tokens_per_second=0, prefill_tokens_per_second=0, ctx_active=None)], now=1003.0)
    last = seen.update([], now=1010.0)
    assert last["tokens_per_second"] == 52.5 and last["prefill_tokens_per_second"] == 376.0
    assert last["ctx_active"] == 322 and last["at"] == 1003.0


def test_writes_only_on_model_change_and_unload(fresh, monkeypatch):
    writes = []
    real = seen._save
    monkeypatch.setattr(seen, "_save", lambda item: (writes.append(item["running_alias"]), real(item)))
    for t in range(5):
        seen.update([_endpoint(tokens_per_second=50 + t)], now=1000.0 + t)
    assert writes == ["qwen-flash-next"]                          # 3 秒ごとの取得では書かない
    seen.update([], now=1100.0)
    seen.update([], now=1103.0)
    assert writes == ["qwen-flash-next", "qwen-flash-next"]       # アンロードで 1 回だけ
    seen.update([_endpoint("radiance-27b")], now=1200.0)
    assert writes[-1] == "radiance-27b"


def test_survives_a_restart_and_ignores_a_loading_model(fresh, monkeypatch):
    seen.update([_endpoint()], now=1000.0)
    seen.update([], now=1001.0)
    monkeypatch.setattr(seen, "_last", None)
    monkeypatch.setattr(seen, "_saved", None)
    monkeypatch.setattr(seen, "_attempted", None)
    monkeypatch.setattr(seen, "_loaded", False)
    loading = _endpoint("next-model", available=False, slots=0)
    last = seen.update([loading], now=1500.0)                     # 読み込み中は「動いている」に数えない
    assert last["running_alias"] == "qwen-flash-next"


def test_broken_file_is_treated_as_nothing(fresh):
    fresh.write_text("{broken", encoding="utf-8")
    assert seen.update([], now=1.0) is None


def test_a_failing_disk_is_not_retried_every_poll(fresh, monkeypatch):
    calls = []

    def broken_replace(src, dst):
        calls.append(dst)
        raise OSError("disk full")

    monkeypatch.setattr(seen.os, "replace", broken_replace)
    for t in range(5):
        seen.update([_endpoint()], now=1000.0 + t)
    assert len(calls) == 1                                         # 書き直し続けない
    assert not list(fresh.parent.glob(".llm-last-seen.json.*.tmp"))  # 一時ファイルを残さない
    assert seen.update([], now=1100.0)["running_alias"] == "qwen-flash-next"  # 表示は続く


def test_two_loaded_models_keep_the_same_pick_while_both_idle(fresh, monkeypatch):
    writes = []
    real = seen._save
    monkeypatch.setattr(seen, "_save", lambda item: (writes.append(item["running_alias"]), real(item)))
    a, b = _endpoint("model-a"), _endpoint("model-b")
    seen.update([a, b], now=1.0)
    seen.update([b, a], now=2.0)                                  # 並び順が変わっても同じモデル
    seen.update([a, b], now=3.0)
    assert writes == ["model-a"]
    seen.update([a, _endpoint("model-b", busy=1)], now=4.0)       # 処理中が先
    assert writes == ["model-a", "model-b"]
