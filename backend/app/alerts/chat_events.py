"""AI アシスタントの会話の回が終わった・止まったときの通知（種類の判断と文面）。

通知の文は短く、会話の本文の全文・パス・鍵を載せない。題は会話の題、本文は「何が起きたか」と 1 行の要約まで。
秘密の会話（confidential_at）は、題も要約も載せず「秘密の会話の…」だけにする（Web Push は Apple・Google の
push service を通るため）。押すと、その会話を開く（/assistant?conversation=<id>）。
"""
from __future__ import annotations

import json
import logging
import re

from app.alerts.events import notify_event

logger = logging.getLogger("control_deck.alerts.chat")

LONG_CHAT_SECONDS = 60
RESEARCH_MODES = ("web", "academic", "deep", "research")
QUESTION_HEADING = "決めてもらいたいこと"
_PATHLIKE = re.compile(r"\S*/\S*")
_KEYLIKE = re.compile(r"\b(?:sk|ghp|gho|hf|xox[a-z]?|AKIA)[-_A-Za-z0-9]{6,}|\b[A-Za-z0-9_\-]{24,}\b")


def one_line(text: str, limit: int = 80) -> str:
    """本文の最初の 1 行。パス・URL・鍵らしい語は伏せる。"""
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#>*-• ").strip()
        if line and not line.startswith("```"):
            break
    else:
        return ""
    line = _KEYLIKE.sub("…", _PATHLIKE.sub("…", line))
    return line if len(line) <= limit else line[:limit] + "…"


def _load(conv_id: str, assistant_id: str | None):
    from app.database import SessionLocal
    from app.models import ChatMessage, Conversation

    with SessionLocal() as db:
        conv = db.get(Conversation, conv_id)
        row = db.get(ChatMessage, assistant_id) if assistant_id else None
        if conv is None:
            return None
        meta = {}
        if row is not None:
            try:
                meta = json.loads(row.meta_json or "{}")
            except ValueError:
                meta = {}
        return {"title": conv.title, "secret": conv.confidential_at is not None, "owner": conv.owner_user_id,
                "status": row.status if row else "", "content": (row.content if row else "") or "", "meta": meta}


def _send(kind: str, conv_id: str, info: dict, label: str, *, summary: str = "", tag_suffix: str = "",
          cooldown: float = 0) -> None:
    if info["owner"] is None:
        return
    if info["secret"]:
        title, body = "秘密の会話", f"{label}（内容は表示しません）"
    else:
        title, body = (info["title"] or "AI アシスタント")[:60], f"{label}" + (f" — {one_line(summary)}" if summary else "")
    notify_event(kind, title=title, body=body, url=f"/assistant?conversation={conv_id}",
                 tag=f"chat-{conv_id}{tag_suffix}", user_ids=[info["owner"]], cooldown=cooldown)


def announce_turn(conv_id: str, assistant_id: str, mode: str, *, failed: bool, seconds: float) -> None:
    """会話の回が終わったとき（chat_persist の chat.completion ジョブの終わりから呼ぶ）。
    止めた・キャンセルされた回（status が completed でない）は、本人の操作なので知らせない。"""
    try:
        info = _load(conv_id, assistant_id)
        if info is None:
            return
        mode = str(info["meta"].get("mode") or mode or "chat")
        if failed or info["status"] == "failed":
            if mode == "code":
                _send("code_failed", conv_id, info, "コード開発が失敗しました")
            elif mode in RESEARCH_MODES:
                _send("research_done", conv_id, info, "調査が失敗しました")
            elif seconds >= LONG_CHAT_SECONDS:
                _send("chat_done", conv_id, info, "答えを作れませんでした")
            return
        if info["status"] != "completed":
            return
        text = info["content"]
        if mode == "code":
            if info["meta"].get("agent") == "plan" and QUESTION_HEADING in text:
                _send("code_question", conv_id, info, "プランに決めてもらいたいことがあります")
            else:
                _send("code_done", conv_id, info, "コード開発が完了しました", summary=text)
        elif mode in RESEARCH_MODES:
            _send("research_done", conv_id, info, "調査が完了しました", summary=text)
        elif seconds >= LONG_CHAT_SECONDS:
            _send("chat_done", conv_id, info, "答えができました", summary=text)
    except Exception:  # noqa: BLE001 - 通知の失敗で会話の保存を止めない
        logger.exception("会話の通知に失敗しました")


def announce_waiting(conv_id: str, kind: str) -> None:
    """OpenCode が質問・許可の答えを待って止まったとき（回の途中）。"""
    try:
        info = _load(conv_id, None)
        if info is None:
            return
        label = "質問の答えを待っています" if kind == "code_question" else "許可の確認を待っています"
        _send(kind, conv_id, info, label, tag_suffix="-wait", cooldown=30)
    except Exception:  # noqa: BLE001
        logger.exception("会話の通知に失敗しました")


def announce_interrupted(job_ids: list[str]) -> None:
    """起動時の立て直しで中断にしたジョブのうち、OpenCode の回だったものを知らせる（#561）。"""
    from app.database import SessionLocal
    from app.models import ChatMessage

    try:
        with SessionLocal() as db:
            rows = db.query(ChatMessage.id, ChatMessage.conversation_id, ChatMessage.meta_json).filter(
                ChatMessage.job_id.in_(job_ids)).all()
        for message_id, conv_id, meta_json in rows:
            try:
                meta = json.loads(meta_json or "{}")
            except ValueError:
                continue
            if meta.get("mode") != "code":
                continue
            info = _load(conv_id, message_id)
            if info is not None:
                _send("code_interrupted", conv_id, info, "再起動で中断されました。会話から再開できます")
    except Exception:  # noqa: BLE001
        logger.exception("中断の通知に失敗しました")
