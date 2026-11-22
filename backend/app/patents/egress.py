"""外部の検索サービスへ検索語を送る直前の自動の検査（手動の確認・approved_public の代わり。2026-11-22）。

- 利用者「ユーザー側に余計な操作はさせずに自動で適用されるように。特許の検索自体はインターネットでやる」
  「制限で検索が不十分になることは避けたい」。検索語はもともと請求項から取る言葉なので、キーワード・短い句は止めない。
- 止めるのは、検索語・検索式の中に、案件の未公開の文章（請求項・claims_draft・hearing）と **連続で BLOCK_MIN_CHARS 文字以上**
  一致する部分があるとき（文章をそのまま写した塊）。幅・改行・句読点の揺れは judge._flat と同じ正規化で許す。
- 案件が決まらない検索（取得の手動実行）は、全案件の未公開の文章と照らす。ウォッチは案件で照らし、実行のたびに検査する。
- 止めたときは理由・重なった部分（20 字まで）・言い換えの案（その塊をキーワードに分けた形）を返す。
- 外へ送る検索語・止めた検索語は、長さと理由だけ監査に残す（本文は残さない）。
"""
from __future__ import annotations

import logging
import re

from app.patents import judge
from app.patents import store as pstore
from app.patents import survey

logger = logging.getLogger("control_deck.patents.egress")

BLOCK_MIN_CHARS = 25
SHOW_MAX_CHARS = 20
_PIECE_SPLIT = re.compile(r"[、。,，;；:：\s/・]+|[はがをにでとものへや](?=[^\s])")
_CHUNK = 12


class QueryBlocked(pstore.PatentError):
    """未公開の文章をそのまま写した塊が検索語に入っている。"""

    def __init__(self, reason: str, *, overlap: str, suggestion: str | None, case_id: int | None):
        super().__init__(reason)
        self.reason, self.overlap, self.suggestion, self.case_id = reason, overlap, suggestion, case_id

    def detail(self) -> dict:
        return {"message": self.reason, "overlap": self.overlap, "suggestion": self.suggestion, "blocked": True}


def _flat_map(text: str) -> tuple[str, list[int]]:
    """judge._flat と同じ正規化（NFKC・小文字・空白と句読点を落とす）を 1 文字ずつ行い、各文字の元の位置も返す。"""
    chars, idx = [], []
    for i, ch in enumerate(text):
        for c in judge._flat(ch):
            chars.append(c)
            idx.append(i)
    return "".join(chars), idx


def _spans(text: str, flat_source: str) -> list[tuple[int, int, str]]:
    """text のうち、未公開の文章と連続で BLOCK_MIN_CHARS 文字以上一致する範囲（text の位置）と、その一致した文字列。"""
    flat, idx = _flat_map(text)
    out: list[tuple[int, int, str]] = []
    i = 0
    n = BLOCK_MIN_CHARS
    while i + n <= len(flat):
        if flat[i:i + n] in flat_source:
            j = i + n
            while j < len(flat) and flat[i:j + 1] in flat_source:
                j += 1
            out.append((idx[i], idx[j - 1] + 1, flat[i:j]))
            i = j
        else:
            i += 1
    return out


def _keywords(chunk: str) -> list[str]:
    pieces: list[str] = []
    for p in _PIECE_SPLIT.split(chunk):
        p = p.strip()
        if len(p) < 2:
            continue
        pieces += [p[k:k + _CHUNK] for k in range(0, len(p), _CHUNK)] if len(p) > BLOCK_MIN_CHARS - 1 else [p]
    seen: list[str] = []
    for p in pieces:
        if p not in seen:
            seen.append(p)
    return seen


def _suggest(norm: str, spans: list[tuple[int, int, str]], flat_source: str) -> str | None:
    text = norm
    for start, end, _ in sorted(spans, reverse=True):
        words = _keywords(norm[start:end])
        text = text[:start] + " ".join(words) + text[end:]
    text = re.sub(r"\s+", " ", text).strip()
    if not text or _spans(text, flat_source):
        return None
    return text[:500]


def inspect(query: str, case: dict) -> QueryBlocked | None:
    """1 つの案件の未公開の文章と照らす。止めるなら QueryBlocked（送出はしない）、通るなら None。"""
    _, flat_source = survey._unpublished_source(case)
    norm = query or ""
    spans = _spans(norm, flat_source)
    if not spans:
        return None
    overlap = spans[0][2][:SHOW_MAX_CHARS]
    title = str(case.get("title") or "")[:40]
    reason = (f"検索語に、案件「{title}」の未公開の文章をそのまま写した部分（{BLOCK_MIN_CHARS} 文字以上の連続）があるため、"
              f"外部へは送りませんでした。重なった部分: 「{overlap}」。キーワードに分けて入れ直してください")
    return QueryBlocked(reason, overlap=overlap, suggestion=_suggest(norm, spans, flat_source), case_id=int(case["id"]))


def check(query: str, case_id: int | None = None) -> None:
    """外部へ送る前の検査。case_id があればその案件、無ければ全案件の未公開の文章と照らす。止めるなら QueryBlocked。"""
    ids = [int(case_id)] if case_id is not None else [int(c["id"]) for c in pstore.list_cases()]
    for cid in ids:
        blocked = inspect(query, pstore.get_case(cid))
        if blocked is not None:
            raise blocked


def sent_metadata(query: str, source: str, case_id: int | None) -> dict:
    return {"source": source, "query_chars": len(query or ""), "case_id": case_id, "checked": "auto"}


def blocked_metadata(query: str, source: str, case_id: int | None, blocked: QueryBlocked) -> dict:
    return {"source": source, "query_chars": len(query or ""), "case_id": case_id, "checked": "auto",
            "reason": f"unpublished_copy>={BLOCK_MIN_CHARS}", "overlap_chars": len(blocked.overlap)}


def audit_background(action: str, *, result: str, resource_id: str, metadata: dict) -> None:
    """要求の無い実行（定期のウォッチ）の監査。失敗しても検索の処理は止めない。"""
    try:
        from app.audit import service as audit
        from app.database import SessionLocal

        with SessionLocal() as db:
            audit.record(db, action, username="system", resource_type="patent_case", resource_id=resource_id,
                         result=result, metadata=metadata)
    except Exception:  # noqa: BLE001
        logger.exception("監査の記録に失敗しました")
