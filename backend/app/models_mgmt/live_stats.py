"""Prefill and generation speed for engines that expose no llama.cpp-style /metrics.

llama.cpp reports its own throughput; Strata, Maya-S and Radiance do not report the same way.
ControlDeck relays every request to them (the gateway and the internal provider), so it can time
them itself: prefill = prompt tokens / time to the first streamed token, generation = generated
tokens / time after it. Keyed by the engine's port. Only in memory; display only.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

_lock = threading.Lock()


@dataclass
class _Request:
    port: int
    started: float = field(default_factory=time.monotonic)
    first_at: float | None = None
    thinking: bool = False
    events: int = 0          # streamed deltas: ~tokens while a request is running
    timings: dict | None = None  # the engine's own, when its stream carries them
    local: bool = True           # a local engine (counts as "an LLM is working" for electricity)


@dataclass
class _Last:
    prefill_tps: float = 0.0
    decode_tps: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    at: float = 0.0


_active: dict[int, list[_Request]] = {}
_last: dict[int, _Last] = {}
_last_end = 0.0  # monotonic time the last request ended (any port)


def busy_during(since: float | None) -> bool:
    """Was any local LLM request running between `since` (monotonic) and now? The electricity
    accumulator asks this every sample to count the PC's power while an LLM was working."""
    with _lock:
        if any(r.local for rows in _active.values() for r in rows):
            return True
        return since is not None and _last_end >= since


def busy_now() -> bool:
    with _lock:
        return any(r.local for rows in _active.values() for r in rows)


def begin(port: int, *, local: bool = True) -> _Request:
    """`local=False` for a request that leaves the PC (an external model through the gateway):
    it is timed like the others but never counted as a local LLM working."""
    request = _Request(port=int(port), local=bool(local))
    with _lock:
        _active.setdefault(request.port, []).append(request)
    return request


def delta(request: _Request, *, thinking: bool) -> None:
    """One streamed piece of text arrived."""
    if request.first_at is None:
        request.first_at = time.monotonic()
    request.thinking = thinking
    request.events += 1


def end(request: _Request, usage: dict | None = None, timings: dict | None = None) -> None:
    """Close a request. An engine's own `timings` (llama-server's shape, which Radiance returns)
    win over ControlDeck's estimate; a non-streamed reply without them gives an overall rate."""
    global _last_end
    now = time.monotonic()
    with _lock:
        rows = _active.get(request.port, [])
        if request in rows:
            rows.remove(request)
        if request.local:
            _last_end = now
        prompt = completion = 0
        if isinstance(usage, dict):
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
        completion = completion or request.events
        last = _last.setdefault(request.port, _Last())
        if isinstance(timings, dict) and (timings.get("predicted_per_second") or timings.get("prompt_per_second")):
            last.prefill_tps = float(timings.get("prompt_per_second") or last.prefill_tps)
            last.decode_tps = float(timings.get("predicted_per_second") or last.decode_tps)
        elif request.first_at is None and completion > 1:
            last.decode_tps = completion / max(1e-3, now - request.started)
        elif request.first_at is not None:
            ttft = max(1e-3, request.first_at - request.started)
            gen_time = max(1e-3, now - request.first_at)
            if prompt:
                last.prefill_tps = prompt / ttft
            if completion > 1:
                last.decode_tps = (completion - 1) / gen_time
        last.prompt_tokens, last.completion_tokens, last.at = prompt, completion, now


def snapshot(port: int) -> dict:
    """What the capacity view shows for one engine."""
    now = time.monotonic()
    with _lock:
        rows = list(_active.get(int(port), []))
        last = _last.get(int(port), _Last())
    prefilling = [r for r in rows if r.first_at is None]
    generating = [r for r in rows if r.first_at is not None]
    live = 0.0
    for r in generating:
        elapsed = now - (r.first_at or now)
        if elapsed > 0.5 and r.events > 1:
            live += (r.events - 1) / elapsed
    phase = ("prefill" if prefilling and not generating
             else "think" if any(r.thinking for r in generating)
             else "generate" if generating else "idle")
    return {
        "busy": len(rows), "prefilling": len(prefilling), "phase": phase,
        "tokens_per_second": round(live if generating else last.decode_tps, 1),
        "tokens_per_second_single": round(last.decode_tps, 1),
        "prefill_tokens_per_second": round(last.prefill_tps, 1),
        "last_prompt_tokens": last.prompt_tokens, "last_completion_tokens": last.completion_tokens,
    }


def feed_sse(request: _Request, chunk: bytes, buffer: bytearray) -> dict | None:
    """Read relayed SSE bytes (a chunk may hold several events, or part of one).

    Returns the usage object when the stream carried one. Never changes what is relayed.
    """
    import json

    buffer.extend(chunk)
    usage = None
    while b"\n" in buffer:
        line, _, rest = bytes(buffer).partition(b"\n")
        buffer[:] = rest
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            item = json.loads(data)
        except ValueError:
            continue
        if isinstance(item.get("usage"), dict):
            usage = item["usage"]
        if isinstance(item.get("timings"), dict):
            request.timings = item["timings"]
        try:
            piece = item["choices"][0]["delta"]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(piece, dict):
            if piece.get("reasoning_content"):
                delta(request, thinking=True)
            elif piece.get("content"):
                delta(request, thinking=False)
    return usage


def context_from_metrics(data: object) -> int | None:
    """Tokens in the context from a Strata / Maya-S `/metrics` reply: the running request's prompt and
    what it generated so far, else the last finished request's. None when the reply says neither."""
    if not isinstance(data, dict):
        return None

    def num(value: object) -> int:
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    live = data.get("live") if isinstance(data.get("live"), dict) else {}
    prompt = num(live.get("prompt_total")) or num(live.get("prompt_tokens"))
    if prompt:
        return prompt + num(live.get("generated"))
    requests = data.get("requests") if isinstance(data.get("requests"), list) else []
    last = requests[0] if requests and isinstance(requests[0], dict) else None
    if last:
        prompt = num(last.get("prompt_total")) or num(last.get("prompt_tokens"))
        if prompt:
            return prompt + num(last.get("output_tokens"))
    return None


def last_context(port: int) -> int | None:
    """The last relayed request's prompt + output on this port (engines without their own metrics)."""
    with _lock:
        last = _last.get(int(port))
    if last is None or not last.prompt_tokens:
        return None
    return last.prompt_tokens + last.completion_tokens
