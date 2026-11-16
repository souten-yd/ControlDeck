"""Automatic compaction of AI assistant conversations.

The chat used to send every past turn on every request, so a long conversation ran
into the model's context and failed (Maya-S and Radiance refuse an oversized prompt;
llama.cpp cuts it). Now, once the prompt would pass the threshold for the model's
window (app.models_mgmt.compaction), the older turns are folded into one summary
written by the same model. Recent turns stay word for word.

The summary is stored on the assistant message of the turn that made it
(meta["compaction"] = {summary, covers_until}); the next turn starts from it, so a
conversation is summarized once per threshold crossing, not on every request.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("control_deck.chat_compaction")

IMAGE_TOKENS = 1_500          # one picture's prompt cost, roughly (the encoders give 500-1,500)
KEEP_SHARE = 0.35             # of the threshold, kept word for word at the end
SUMMARY_MAX_TOKENS = 4_096
SUMMARY_MARK = "_compaction_summary"
ID_KEY = "_message_id"

SUMMARY_SYSTEM = (
    "あなたは会話の記録係です。以下はこれまでの会話です。この会話を後で続けるために、"
    "利用者の目的・前提・決まったこと・数値や固有名詞・作業の途中経過・未解決の質問を落とさず、"
    "日本語の箇条書きで簡潔に要約してください。推測で補わず、会話に無いことは書かないでください。"
)


def estimate_tokens(message: dict[str, Any]) -> int:
    """A deliberately high estimate: UTF-8 bytes / 3 (Japanese ~1 token a character,
    English ~4 bytes a token), plus a fixed cost per picture and per message."""
    content = message.get("content")
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                total += IMAGE_TOKENS
            else:
                total += len(str(part.get("text") or "").encode("utf-8")) // 3
    else:
        total = len(str(content or "").encode("utf-8")) // 3
    return total + 8


def estimate(history: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(m) for m in history)


def strip_private(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove ControlDeck's bookkeeping keys before a request leaves for an engine."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in history]


def summary_message(summary: str) -> dict[str, Any]:
    return {"role": "system", SUMMARY_MARK: True,
            "content": "これまでの会話の要約です（古いやり取りは自動で圧縮しました）:\n\n" + summary}


def resume_point(past: list) -> tuple[str, int]:
    """(summary, index of the first message after it) for messages ordered by time."""
    for index in range(len(past) - 1, -1, -1):
        try:
            meta = json.loads(past[index].meta_json or "{}")
        except (TypeError, ValueError):
            continue
        state = meta.get("compaction") if isinstance(meta, dict) else None
        if not isinstance(state, dict) or not state.get("summary"):
            continue
        covered = str(state.get("covers_until") or "")
        ids = [m.id for m in past]
        if covered in ids:
            return str(state["summary"]), ids.index(covered) + 1
    return "", 0


def _split(history: list[dict[str, Any]], keep_tokens: int):
    """(leading system messages, turns to fold, turns kept). The last message is always kept."""
    head = 0
    while head < len(history) and history[head].get("role") == "system" and not history[head].get(SUMMARY_MARK):
        head += 1
    system, rest = history[:head], history[head:]
    kept: list[dict[str, Any]] = []
    used = 0
    for message in reversed(rest):
        cost = estimate_tokens(message)
        if kept and used + cost > keep_tokens:
            break
        kept.insert(0, message)
        used += cost
    fold = rest[:len(rest) - len(kept)]
    # Start the kept part on a user turn, so the model never sees an answer without its question.
    while kept and kept[0].get("role") == "assistant" and len(kept) > 1:
        fold.append(kept.pop(0))
    return system, fold, kept


def _line(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        text = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        text += " [画像]" * sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
    else:
        text = str(content or "")
    role = "要約" if message.get(SUMMARY_MARK) else {"user": "利用者", "assistant": "アシスタント"}.get(
        str(message.get("role")), str(message.get("role")))
    return f"[{role}] {text}"


def _chunks(messages: list[dict[str, Any]], budget_tokens: int) -> list[str]:
    """The transcript in pieces that each fit one summary request. Nothing is cut out:
    a single message longer than a piece is split across pieces."""
    limit = max(1_000, budget_tokens * 3)       # back to bytes, the estimate's unit
    pieces: list[str] = []
    current = ""
    for message in messages:
        line = _line(message)
        while len(line.encode("utf-8")) > limit:
            raw = line.encode("utf-8")
            head = raw[:limit].decode("utf-8", "ignore")
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
            line = raw[len(head.encode("utf-8")):].decode("utf-8", "ignore")
        if current and len((current + "\n\n" + line).encode("utf-8")) > limit:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n\n{line}" if current else line
    if current:
        pieces.append(current)
    return pieces


async def compact(history: list[dict[str, Any]], *, base_url: str, model: str,
                  output_reserve: int, provider, settings=None) -> tuple[list[dict[str, Any]], dict | None]:
    """Fold old turns when the prompt passes the threshold.

    Returns (history to send, state to store or None). Never raises: a failed summary
    leaves the history as it was (the engine then reports an overlong prompt itself).
    """
    from app.models_mgmt import compaction
    from app.models_mgmt.runtime_provider import RuntimeChatRequest

    window = compaction.window_for(base_url, model)
    if window <= 0:
        return history, None
    reserve = min(max(0, output_reserve), window // 4)
    limit = compaction.threshold(window, output_reserve=reserve, settings=settings)
    before = estimate(history)
    if limit is None or before <= limit:
        return history, None
    system, fold, kept = _split(history, int(limit * KEEP_SHARE))
    covered = [m for m in fold if m.get(ID_KEY)]
    if not covered:
        return history, None
    summary_tokens = min(SUMMARY_MAX_TOKENS, max(512, window // 8))
    # Usually one request. A conversation carried over from a wider model can be bigger
    # than this window: then it is summarized piece by piece, each piece on top of the
    # summary so far, so nothing in the middle is dropped.
    budget = window - 2 * summary_tokens - 2_048
    summary = ""
    try:
        for piece in _chunks(fold, budget):
            body = f"これまでの要約:\n{summary}\n\n続きの会話:\n{piece}" if summary else piece
            request = RuntimeChatRequest(
                client="assistant",
                base_url=base_url, model=model, temperature=None, max_tokens=summary_tokens,
                disable_thinking=True, thinking=False,
                messages=[{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": body}],
            )
            summary = (await provider.complete(request)).strip() or summary
    except Exception:  # noqa: BLE001 - keep the conversation going without a summary
        logger.exception("会話の自動圧縮に失敗しました")
        return history, None
    if not summary:
        return history, None
    compacted = [*system, summary_message(summary), *kept]
    state = {"summary": summary, "covers_until": str(covered[-1][ID_KEY]),
             "tokens_before": before, "tokens_after": estimate(compacted),
             "threshold": limit, "window": window, "folded_messages": len(fold)}
    return compacted, state
