"""Prefill / generation timing for engines without llama.cpp-style metrics."""
import time

from app.models_mgmt import live_stats, model_names


def test_relayed_stream_gives_prefill_and_generation_speed():
    timing = live_stats.begin(9999)
    assert live_stats.snapshot(9999)["phase"] == "prefill"
    pending = bytearray()
    time.sleep(0.05)
    events = b"".join(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n' for _ in range(20))
    usage = live_stats.feed_sse(timing, events[:37], pending)          # split mid-event
    usage = live_stats.feed_sse(timing, events[37:] + b'data: {"choices":[],"usage":{"prompt_tokens":500,"completion_tokens":20}}\n\n', pending) or usage
    assert live_stats.snapshot(9999)["phase"] == "generate"
    time.sleep(0.05)
    live_stats.end(timing, usage)
    snap = live_stats.snapshot(9999)
    assert snap["busy"] == 0 and snap["phase"] == "idle"
    assert snap["prefill_tokens_per_second"] > 0 and snap["tokens_per_second_single"] > 0
    assert snap["last_prompt_tokens"] == 500


def test_model_names_are_uniform():
    assert model_names.from_file("/m/Qwen3.8-27B-UD-Q4_K_M.gguf") == ("Qwen3.8-27B", "UD-Q4_K_M")
    assert model_names.from_file("/m/qwen3.8-27b-iq5r-v4.rad") == ("Qwen3.8-27B", "IQ5R-V4")
    assert model_names.from_file("/m/Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf") == ("Qwen3.8-Flash-Next", "UD-IQ4_XS")


def test_busy_during_covers_requests_that_ended_since_the_last_sample():
    since = time.monotonic()
    timing = live_stats.begin(9998)
    assert live_stats.busy_now() and live_stats.busy_during(since)
    live_stats.end(timing, {"prompt_tokens": 1200, "completion_tokens": 30})
    assert not live_stats.busy_now()
    assert live_stats.busy_during(since)                 # ended after the last sample: still counted
    assert not live_stats.busy_during(time.monotonic() + 1)
    assert live_stats.last_context(9998) == 1230


def test_context_from_engine_metrics():
    running = {"live": {"prompt_total": 180000, "prompt_tokens": 2000, "generated": 512}, "requests": []}
    assert live_stats.context_from_metrics(running) == 180512
    idle = {"live": {"prompt_tokens": None, "generated": None},
            "requests": [{"prompt_total": 9000, "output_tokens": 300}, {"prompt_total": 1, "output_tokens": 1}]}
    assert live_stats.context_from_metrics(idle) == 9300
    assert live_stats.context_from_metrics({"live": {}, "requests": []}) is None
    assert live_stats.context_from_metrics("broken") is None
