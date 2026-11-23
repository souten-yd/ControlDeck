"""記憶を作る（手元の LLM だけ）・答えの段へ渡す・消す。守りの規則は policy.py が持ち、ここはそれを呼ぶだけ。

作る側の守り:
- 取り出しは **必ずローカルの LLM**（offline.local_target）。会話がクラウドの LLM でも同じ。ローカルが無ければ作らない
  （外のモデルへ会話を送って記憶を抜くことはしない）。取り出しの呼び出しも confidential_scope の中で行う
  （ゲートウェイの機密の印・外部通信の遮断が二重に効く）。
- 秘密の会話・特許の流れから作った記憶は必ず protected（store.add が元の種類で強制する）。
- 秘密に見える文字列（鍵・パスワードらしきもの）は記憶にしない。
- 監査（memory.create / memory.delete）には、種類・守られたか・文字数だけを残す（本文は残さない）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from app.memory import policy, store

logger = logging.getLogger("control_deck.memory")

_EXPLICIT = re.compile(
    r"(覚えて(?:おいて|ほしい|欲しい|ください|下さい|くれ|おく|おいてね)|覚えといて|覚えてね|記憶して(?:おいて|ください|ほしい)?|"
    r"忘れないで|メモしておいて|remember (?:that|this|my)|please remember)", re.I)
_NEGATED = re.compile(r"(覚えなくて|覚えないで|覚えなくていい|記憶しないで|忘れて)")
_SECRETISH = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|[A-Fa-f0-9]{32,}|password|パスワード|passwd|api[_ -]?key|秘密鍵|BEGIN [A-Z ]*PRIVATE)", re.I)

EXTRACT_SYSTEM = (
    "あなたは会話から「次の会話でも役立つ、利用者についての長く変わらない事柄」だけを抜き出します。"
    "好み（文体・言語・呼び方など）・事実（所属・使う道具・環境など）・仕事の文脈（進めている案件・方針）に限ります。"
    "一度きりの質問の内容・調べものの結果・一時的な状況・パスワードや鍵や個人番号は抜きません。"
    "各項目は 1 文・80 字以内の日本語で、「利用者は…」の形にします。"
    '出力は JSON だけ: {"memories":[{"text":"…","kind":"preference|fact|work"}]}。無ければ {"memories":[]}。')


def wants_remember(text: str) -> bool:
    """「覚えて」と明示したか。否定（覚えなくていい）は除く。"""
    return bool(_EXPLICIT.search(text or "")) and not _NEGATED.search(text or "")


def looks_secret(text: str) -> bool:
    return bool(_SECRETISH.search(text or ""))


def parse_candidates(answer: str, limit: int = 3) -> list[dict]:
    """取り出しの返事から候補を読む。JSON でなければ空（推測で作らない）。"""
    cleaned = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.S)
    found = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not found:
        return []
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return []
    items = data.get("memories") if isinstance(data, dict) else None
    out: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())[:store.MAX_TEXT]
        kind = str(item.get("kind") or "fact")
        if text and not looks_secret(text):
            out.append({"text": text, "kind": kind if kind in store.KINDS else "fact"})
        if len(out) >= limit:
            break
    return out


async def extract(user_text: str, assistant_text: str, base_url: str, model: str, *, explicit: bool) -> list[dict]:
    """会話から覚えることを抜く。必ずローカルの LLM で。ローカルが無ければ空（作らない）。"""
    from app.security import offline

    try:
        target = offline.local_target(base_url, model)
    except offline.LocalModelUnavailable:
        logger.info("記憶の取り出しを見送りました（ローカルのモデルがありません）")
        return []
    except Exception:  # noqa: BLE001
        return []
    from app.workflows.chat_router import _llm

    ask = ("利用者が「覚えて」と頼みました。頼まれた内容だけを抜いてください。" if explicit
           else "明らかに長く役立つ事柄だけを最大 3 件抜いてください。迷うなら抜かないでください。")
    body = f"利用者の発言:\n{user_text[:1500]}\n\nAI の答え:\n{assistant_text[:1500]}"
    try:
        with offline.confidential_scope():
            answer = await _llm([{"role": "system", "content": EXTRACT_SYSTEM + ask}, {"role": "user", "content": body}],
                                target[0], target[1], "", temperature=0.1, max_tokens=400, disable_thinking=True,
                                timeout_seconds=90)
    except Exception:  # noqa: BLE001 - 作れなくても会話には影響させない
        logger.info("記憶の取り出しに失敗しました", exc_info=True)
        return []
    return parse_candidates(answer, 3)


# ---- 答えの段へ渡す ----

async def prepare(conv_id: str, user_id: int | None, *, query: str, secret: bool, external: bool,
                  code: bool = False) -> list[store.Memory]:
    """この回に入れる記憶（policy が許した種類だけから、関係の深い上位 5 件ほど）。使った分の使用履歴も更新する。"""
    if user_id is None:
        return []

    def run() -> list[store.Memory]:
        prefs = store.get_prefs(user_id)
        flags = store.get_flags(conv_id)
        allowed = policy.allowed_sensitivities(policy.Situation(secret=secret, external=external, code=code), prefs, flags)
        if not allowed:
            return []
        chosen = policy.rank(store.list_memories(user_id, include_pending=False), query, allowed=allowed)
        store.touch(user_id, [m.id for m in chosen])
        return chosen

    try:
        return await asyncio.to_thread(run)
    except Exception:  # noqa: BLE001 - 記憶が壊れていても会話は続ける（記憶なしで）
        logger.warning("記憶を読めませんでした", exc_info=True)
        return []


def used_meta(memories: list[store.Memory]) -> dict:
    """答えのメタ。本文は入れない（ID と件数だけ。本文は会話の権限を見て /memory/used が返す）。"""
    return {"count": len(memories), "ids": [m.id for m in memories]}


# ---- 作る ----

def _audit(user_id: int, action: str, memory_id: int | str, metadata: dict) -> None:
    from app.audit import service as audit
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        audit.record(db, action, user=db.get(User, user_id), resource_type="memory", resource_id=str(memory_id), metadata=metadata)
    except Exception:  # noqa: BLE001 - 監査の失敗で記憶の操作を巻き戻さない
        logger.warning("記憶の監査を残せませんでした", exc_info=True)
    finally:
        db.close()


def audit_create(user_id: int, mem: store.Memory) -> None:
    _audit(user_id, "memory.create", mem.id, {"kind": mem.kind, "sensitivity": mem.sensitivity,
                                              "source_kind": mem.source_kind, "chars": len(mem.text), "pending": mem.pending})


def audit_delete(user_id: int, memory_id: int | str, *, count: int = 1, scope: str = "one") -> None:
    _audit(user_id, "memory.delete", memory_id, {"scope": scope, "count": count})


def origin_of(secret: bool, patent: bool) -> tuple[str, str]:
    """作る記憶の (source_kind, sensitivity)。秘密・特許は必ず protected。"""
    if secret:
        return "confidential", "protected"
    if patent:
        return "patent", "protected"
    return "chat", "normal"


async def remember_after_turn(conv_id: str, user_id: int | None, *, user_text: str, assistant_text: str, mode: str,
                              base_url: str, model: str, secret: bool) -> dict:
    """回の終わりに、設定に従って記憶を作る。{"created": n, "pending": m}。作らないときは両方 0。"""
    none = {"created": 0, "pending": 0}
    if user_id is None or mode == "code" or not user_text.strip():
        return none

    def prefs_and_flags() -> tuple[dict, dict]:
        return store.get_prefs(user_id), store.get_flags(conv_id)

    prefs, flags = await asyncio.to_thread(prefs_and_flags)
    if not prefs["enabled"] or prefs["creation"] == "off" or flags["no_save"]:
        return none
    explicit = wants_remember(user_text)
    if prefs["creation"] == "explicit" and not explicit:
        return none
    candidates = await extract(user_text, assistant_text, base_url, model, explicit=explicit)
    if not candidates:
        return none
    source_kind, sensitivity = origin_of(secret, flags["patent"])
    # 明示の依頼は確認なしで入れる。自動のうち「画面で承認」は候補にとどめる
    pending = prefs["creation"] == "confirm" and not explicit

    def save() -> dict:
        made, held = 0, 0
        for item in candidates:
            try:
                mem = store.add(user_id, item["text"], kind=item["kind"], sensitivity=sensitivity,
                                source_kind=source_kind, source_id=conv_id, pending=pending)
            except store.MemoryError_:
                break
            if mem is None:
                continue
            audit_create(user_id, mem)
            held += 1 if pending else 0
            made += 0 if pending else 1
        return {"created": made, "pending": held}

    try:
        return await asyncio.to_thread(save)
    except Exception:  # noqa: BLE001
        logger.warning("記憶を保存できませんでした", exc_info=True)
        return none
