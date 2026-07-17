"""AI会話内の文献レジストリ。

LLMランタイムのtool calling方言には依存せず、短いIDの検出と必要な文献だけの
コンテキスト化をサーバーで行う。API/将来のエージェントツールからも同じ関数を使う。
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ChatReference

# 英単語（RAG等）との衝突を避けるため、英字だけを含むIDは [] または @ を必須にする。
# 数字始まりのID（R1/R10）は裸でも受け付ける。
REFERENCE_RE = re.compile(
    r"\[(R[0-9A-Z]{1,6})\]|@(R[0-9A-Z]{1,6})(?![A-Z0-9])|(?<![A-Z0-9])(R[0-9][0-9A-Z]{0,5})(?![A-Z0-9])",
    re.IGNORECASE,
)
MAX_RESOLVED_REFERENCES = 12
MAX_REFERENCE_CONTEXT_CHARS = 18_000


def _base36(value: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if value < 1:
        raise ValueError("文献連番は1以上である必要があります")
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def _canonical_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    except ValueError:
        return value


def _canonical_key(source: dict) -> str:
    explicit = str(source.get("canonical_id") or "").strip().casefold()
    if explicit:
        return hashlib.sha256(explicit.encode("utf-8")).hexdigest()
    url = _canonical_url(str(source.get("url") or ""))
    identity = url or "\n".join((
        str(source.get("title") or "").strip().casefold(),
        str(source.get("source") or source.get("provider") or "").strip().casefold(),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _kind(source: dict) -> str:
    explicit = str(source.get("kind") or "").strip().lower()
    if explicit in {"page", "paper", "document", "dataset", "patent", "report"}:
        return explicit
    provider = str(source.get("source") or source.get("provider") or "").casefold()
    url = str(source.get("url") or "").casefold()
    if any(token in provider or token in url for token in ("arxiv", "crossref", "openalex", "doi.org", "pubmed")):
        return "paper"
    return "page" if url else "document"


def reference_out(ref: ChatReference, *, include_excerpt: bool = False) -> dict:
    result = {
        "reference_id": ref.short_id,
        "kind": ref.kind,
        "title": ref.title,
        "url": ref.url,
        "source": ref.provider,
    }
    if include_excerpt:
        result["excerpt"] = ref.excerpt
        try:
            result["metadata"] = json.loads(ref.metadata_json or "{}")
        except json.JSONDecodeError:
            result["metadata"] = {}
    elif ref.excerpt:
        result["excerpt_preview"] = ref.excerpt[:240]
    return result


def register_sources(db: Session, conversation_id: str, sources: list[dict]) -> list[dict]:
    """出典を重複排除して登録し、表示用データへreference_idを付けて返す。"""
    if not sources:
        return []
    # 同じ会話を複数クライアントから同時に調査した場合、unique制約を正本として
    # 最新のsequenceを読み直す。SQLite/PostgreSQLの双方でprovider非依存に扱う。
    for attempt in range(3):
        try:
            return _register_sources_once(db, conversation_id, sources)
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
    return []


def _register_sources_once(db: Session, conversation_id: str, sources: list[dict]) -> list[dict]:
    next_sequence = int(db.scalar(
        select(func.max(ChatReference.sequence)).where(ChatReference.conversation_id == conversation_id)
    ) or 0) + 1
    registered: list[dict] = []
    for source in sources:
        key = _canonical_key(source)
        ref = db.execute(select(ChatReference).where(
            ChatReference.conversation_id == conversation_id,
            ChatReference.canonical_key == key,
        )).scalar_one_or_none()
        excerpt = str(source.get("excerpt") or source.get("snippet") or source.get("abstract") or "")[:6000]
        if ref is None:
            ref = ChatReference(
                conversation_id=conversation_id,
                sequence=next_sequence,
                short_id=f"R{_base36(next_sequence)}",
                canonical_key=key,
                kind=_kind(source),
                title=str(source.get("title") or "")[:500],
                url=str(source.get("url") or "")[:2048],
                provider=str(source.get("source") or source.get("provider") or "")[:128],
                excerpt=excerpt,
            )
            db.add(ref)
            db.flush()
            next_sequence += 1
        else:
            if excerpt and len(excerpt) > len(ref.excerpt or ""):
                ref.excerpt = excerpt
            if not ref.title and source.get("title"):
                ref.title = str(source["title"])[:500]
        registered.append(reference_out(ref))
    db.commit()
    return registered


def extract_reference_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in REFERENCE_RE.finditer(text.upper()):
        ref_id = next(group for group in match.groups() if group)
        if ref_id not in seen:
            seen.add(ref_id)
            result.append(ref_id)
    return result[:MAX_RESOLVED_REFERENCES]


def resolve_references(db: Session, conversation_id: str, reference_ids: list[str]) -> list[ChatReference]:
    ids = list(dict.fromkeys(value.upper() for value in reference_ids))[:MAX_RESOLVED_REFERENCES]
    if not ids:
        return []
    refs = db.execute(select(ChatReference).where(
        ChatReference.conversation_id == conversation_id,
        ChatReference.short_id.in_(ids),
    )).scalars().all()
    by_id = {ref.short_id: ref for ref in refs}
    return [by_id[value] for value in ids if value in by_id]


def build_reference_context(refs: list[ChatReference]) -> str:
    """明示された文献だけを有限長のLLMコンテキストへ変換する。"""
    chunks: list[str] = []
    used = 0
    for ref in refs:
        chunk = f"[{ref.short_id}] {ref.title}\n種別: {ref.kind}\n提供元: {ref.provider}\nURL: {ref.url}\n抜粋:\n{ref.excerpt}"
        remaining = MAX_REFERENCE_CONTEXT_CHARS - used
        if remaining <= 0:
            break
        chunk = chunk[:remaining]
        chunks.append(chunk)
        used += len(chunk)
    return "\n\n---\n\n".join(chunks)
