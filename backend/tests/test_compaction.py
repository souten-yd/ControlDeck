"""Automatic compaction: the threshold rule, the AI assistant's folding, and the Models flags."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.models_mgmt import compaction
from app.models_mgmt.runtime_policy import CompactionSettings
from app.workflows import chat_compaction


def test_threshold_follows_the_window():
    auto = CompactionSettings()
    assert compaction.threshold(32768, settings=auto) == 22937          # 70%
    assert compaction.threshold(131072, settings=auto) == 98304         # 75%
    assert compaction.threshold(262144, settings=auto) == 209715        # 80%
    assert compaction.threshold(1048576, settings=auto) == 262144       # 85%, capped
    assert compaction.threshold(1048576, settings=CompactionSettings(max_tokens=0)) == 891289
    assert compaction.threshold(65536, settings=CompactionSettings(mode="percent", percent=60)) == 39321
    # The answer's room is never eaten into, and off means no threshold at all.
    assert compaction.threshold(32768, output_reserve=16384, settings=auto) == 16384
    assert compaction.threshold(32768, settings=CompactionSettings(enabled=False)) is None
    assert compaction.threshold(0, settings=auto) is None


def test_frontend_uses_the_same_bands():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "frontend/src/pages/Models.tsx").read_text(encoding="utf-8")
    bands = ", ".join(f"[{limit}, {percent}]" for limit, percent in compaction.AUTO_BANDS)
    assert f"COMPACTION_BANDS: Array<[number, number]> = [{bands}]" in source
    assert f"?? {compaction.AUTO_TOP_PERCENT}" in source


def test_window_divides_fixed_slots():
    assert compaction.window_of({"ctx_size": 131072, "kv_unified": False, "n_parallel": 4}) == 32768
    assert compaction.window_of({"max_ctx": 1048576}) == 1048576


class _Provider:
    def __init__(self, text="- 目的: テスト"):
        self.text, self.requests = text, []

    async def complete(self, request):
        self.requests.append(request)
        return self.text


def _history(turns: int, size: int):
    rows = [{"role": "system", "content": "sys"}]
    for i in range(turns):
        rows.append({"role": "user", "content": "あ" * size, chat_compaction.ID_KEY: f"u{i}"})
        rows.append({"role": "assistant", "content": "い" * size, chat_compaction.ID_KEY: f"a{i}"})
    rows.append({"role": "user", "content": "最後の質問"})
    return rows


def test_long_conversation_is_folded_into_a_summary(monkeypatch):
    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: 32768)
    provider = _Provider()
    history = _history(20, 1500)          # ~60K estimated tokens, over 70% of 32K
    out, state = asyncio.run(chat_compaction.compact(
        history, base_url="http://127.0.0.1:8097/v1", model="Q", output_reserve=4096,
        provider=provider, settings=CompactionSettings()))
    assert state is not None and state["tokens_after"] < state["threshold"] < state["tokens_before"]
    assert out[0] == history[0] and out[1][chat_compaction.SUMMARY_MARK]
    assert out[-1]["content"] == "最後の質問" and out[2]["role"] == "user"
    assert state["covers_until"].startswith("a")       # folded up to an answer
    # The summary request is one call with thinking off, and nothing private leaves.
    request = provider.requests[0]
    assert request.disable_thinking and "[利用者]" in request.messages[1]["content"]
    assert all(not k.startswith("_") for m in chat_compaction.strip_private(out) for k in m)


def test_short_conversation_or_unknown_window_is_left_alone(monkeypatch):
    provider = _Provider()
    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: 131072)
    history = _history(2, 100)
    assert asyncio.run(chat_compaction.compact(
        history, base_url="x", model="m", output_reserve=0, provider=provider,
        settings=CompactionSettings())) == (history, None)
    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: 0)
    assert asyncio.run(chat_compaction.compact(
        _history(20, 1500), base_url="x", model="m", output_reserve=0, provider=provider,
        settings=CompactionSettings()))[1] is None
    assert not provider.requests


def test_a_failed_summary_keeps_the_conversation(monkeypatch):
    class Broken:
        async def complete(self, request):
            raise RuntimeError("engine down")
    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: 32768)
    history = _history(20, 1500)
    assert asyncio.run(chat_compaction.compact(
        history, base_url="x", model="m", output_reserve=0, provider=Broken(),
        settings=CompactionSettings())) == (history, None)


def test_next_turn_resumes_from_the_stored_summary():
    rows = [SimpleNamespace(id=f"m{i}", meta_json="{}") for i in range(6)]
    rows[4].meta_json = json.dumps({"compaction": {"summary": "要約", "covers_until": "m2"}})
    assert chat_compaction.resume_point(rows) == ("要約", 3)
    assert chat_compaction.resume_point(rows[:4]) == ("", 0)


def test_only_llama_and_ollama_can_coexist(monkeypatch):
    from app.models_mgmt import runtime_policy

    env = asyncio.run(runtime_policy.environment())
    flags = {item["runtime"]: item.get("coexist") for item in env["runtimes"]}
    assert flags.get("ollama") is True
    for runtime in ("strata", "maya-s", "radiance"):
        assert flags.get(runtime, False) is False
    assert "compaction_windows" in env and env["policy"]["compaction"]["mode"] == "auto"


def test_history_bigger_than_the_window_is_summarized_in_pieces(monkeypatch):
    """A conversation carried over from a wider model: no part of it is cut out."""
    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: 8192)
    provider = _Provider()
    history = _history(30, 1500)
    out, state = asyncio.run(chat_compaction.compact(
        history, base_url="x", model="m", output_reserve=1024, provider=provider,
        settings=CompactionSettings()))
    assert state is not None and len(provider.requests) > 1
    sent = "".join(r.messages[1]["content"] for r in provider.requests)
    assert "中略" not in sent and "これまでの要約" in provider.requests[1].messages[1]["content"]
    assert all(len(r.messages[1]["content"].encode()) <= (8192 - 2 * 1024 - 2048) * 3 + 600 for r in provider.requests)
