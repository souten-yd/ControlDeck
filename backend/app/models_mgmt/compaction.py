"""When a conversation is compacted, from the model's context length.

One rule for the AI assistant chat and OpenCode sessions (CompactionSettings in the
runtime policy). The threshold is the prompt size at which older turns are folded into
a summary, so the next request still has room for the answer.

auto: a short window fills in a few turns and needs its answer room early, so it folds
sooner; a long one folds later but never past `max_tokens` (a 1M-token Maya-S window
would otherwise re-read ~900K tokens per turn: minutes of prefill and a weaker recall).
"""
from __future__ import annotations

# (window up to, share of it) - beyond the last band: 85%.
AUTO_BANDS: tuple[tuple[int, int], ...] = ((32_768, 70), (131_072, 75), (262_144, 80))
AUTO_TOP_PERCENT = 85
MIN_THRESHOLD = 2_048


def _settings():
    from app.models_mgmt.runtime_policy import get_policy

    return get_policy().compaction


def auto_percent(context: int) -> int:
    for limit, percent in AUTO_BANDS:
        if context <= limit:
            return percent
    return AUTO_TOP_PERCENT


def threshold(context: int, *, output_reserve: int = 0, settings=None) -> int | None:
    """Prompt tokens at which to compact; None when compaction is off or the window unknown.

    `output_reserve`: what the answer needs; the threshold never eats into it.
    """
    settings = settings or _settings()
    if not settings.enabled or context <= 0:
        return None
    if settings.mode == "percent":
        value = context * settings.percent // 100
    else:
        value = context * auto_percent(context) // 100
        if settings.max_tokens:
            value = min(value, settings.max_tokens)
    if output_reserve:
        value = min(value, context - output_reserve)
    return max(MIN_THRESHOLD, value) if value > 0 else None


def describe(context: int, *, settings=None) -> dict:
    """For the Models page: what a given window means under the current setting."""
    settings = settings or _settings()
    value = threshold(context, settings=settings)
    return {"context": context, "threshold": value,
            "percent": round(100 * value / context) if value and context else None}


def window_of(instance: dict) -> int:
    """そのモデル設定で 1 request が使える窓の大きさ。

    鍵の名前はランタイムで違う（llama.cpp は ctx_size）。
    共有KVを切っている llama.cpp は ctx_size を slot 数へ固定配分するので、
    1 request の取り分はその割り算のあとになる。総量をそのまま宣言すると、
    宣言した窓に届く前に転送先が溢れて、後追いの圧縮に戻る。
    """
    context = 0
    for key in ("ctx_size", "max_ctx"):
        context = int(instance.get(key) or 0)
        if context > 0:
            break
    if context <= 0:
        return 0
    if not instance.get("kv_unified", True):
        context //= max(1, int(instance.get("n_parallel") or 1))
    return context


def window_for(base_url: str, model: str) -> int:
    """The per-request window of the managed model behind base_url (0 if unknown)."""
    from urllib.parse import urlsplit

    from app.models_mgmt import local_llm

    try:
        port = urlsplit(base_url).port
        rows = local_llm.llm_instances()
    except Exception:  # noqa: BLE001 - unknown window = no compaction, never a failed chat
        return 0
    match = next((i for i in rows if str(i.get("alias") or "") == model), None) \
        or next((i for i in rows if port and int(i.get("port") or 0) == port), None)
    return window_of(match) if match else 0
