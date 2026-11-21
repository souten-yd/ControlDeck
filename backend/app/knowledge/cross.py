"""承認済みの知識エッセンスの横断検索・比較（計画 §10 R3-5、PatentBuddy Draft #36 を作り直したもの）。

- どの文献を探すかは検索の入口（documents.search_documents。画面の「探す」と同じ 1 つ）に任せる。
  ここではその文献から、**承認済みで今の版の**エッセンスだけを集めて返す（K1 の list_essences が
  版（stale）と最新のレビュー（epoch つき）を判定する。それをそのまま使い回す）。
- 読むたびに引用を原文と確かめ直す（K1 の validate と同じ考え方）。原文が変わっていたり引用が
  ずれていたりする item は出さない。保存時に検めていても、読むときにもう一度検める。
- consumer は local: エッセンスはローカル専用の AI の推論であり、この横断検索は後のローカルの
  AI 支援（アイデアの検討など）へ知識を渡す入口でもあるため、利用条件で AI に使えない文献
  （ai_use=prohibited）は対象にしない（search_documents の allowed_for が守る）。
- 比較は並べて見せるだけ。AI の判断（意味の一致・新規性など）は足さない。
- 書き込みはしない（新しい表も Job も監査も不要。読み取りだけ）。
"""
from __future__ import annotations

import logging

from app.knowledge import documents, essence, store

logger = logging.getLogger("control_deck.knowledge.cross")

MAX_ITEMS = 50   # 1 回の応答の上限
SCAN_DOCS = 40   # 1 回の走査で見る文献の上限（当たりの文献を無制限に開かせない）
MAX_CURSOR = 500  # ページを先へ進められる限界（それ以降は検索語の絞り込みを促す）


def search(q: str, kind: str = "", limit: int = MAX_ITEMS, cursor: int = 0) -> dict:
    """承認済みの知識エッセンスを、文献ごとに横断して探す。

    - q で文献を探す（search_documents。番号の完全一致も先頭に来る）。
    - kind を指定すればその種類だけ。
    - limit は 1 回の応答の目安の上限。**同じ文献の承認済みはページの途中で切らない**ため、
      応答は limit を超えうる（上限はその文献の承認済みの全件数。欠落・重複なしを優先する）。
    - cursor は「これまでに見た文献」の位置で、続きを取るときに渡す。
    - 1 回の走査は SCAN_DOCS 文献まで。続きがあれば next_cursor を返す。
    """
    if kind and kind not in essence.KINDS:
        raise store.KnowledgeError("種類は知識エッセンスの 8 種のどれか、または指定なしです")
    limit = max(1, min(int(limit), MAX_ITEMS))
    cursor = max(0, min(int(cursor), MAX_CURSOR))
    # 1 hop の関係広げはしない: 当たりの文献だけを見る（走査の上限を守りやすくするため）。
    # +1 件だけ多く取って、未見の文献がまだあるか（next_cursor を出すか）を判断する。
    docs = documents.search_documents(q, consumer="local", limit=cursor + SCAN_DOCS + 1, expand=False)
    items: list[dict] = []
    consumed = 0
    for doc in docs[cursor:cursor + SCAN_DOCS]:
        consumed += 1
        try:
            found = _approved_items(doc["ref"], kind)
        except store.KnowledgeError:
            # 走査の間に消えた文献: 検索全体を落とさず、その文献だけ飛ばす（AGENTS.md ルール 7 と同じ）
            logger.debug("横断検索: 文書 %s が読めませんでした", doc["ref"])
            continue
        items.extend({**found_item, "doc_ref": doc["ref"], "doc_title": doc["title"]} for found_item in found)
        if len(items) >= limit:
            break  # この文献までは出し切って、次の文献から続き（next_cursor）
    next_cursor = cursor + consumed
    return {"items": items, "next_cursor": next_cursor if next_cursor < len(docs) else None,
            "limit": limit, "kinds": essence.KINDS}


def _approved_items(ref: str, kind: str = "") -> list[dict]:
    """1 文献の承認済み・今の版・引用が原文に一致する item を集める（読むたびに確かめ直す）。

    - list_essences（K1）が版（stale）と最新のレビューを判定する。原文が変わった行（stale）の
      item は review_status が未確認に落ちるので、承認済みだけがここへ来る。
    - 引用は原文の [start, end) に今も一字一句一致していることを確かめる（全文は 1 文献で 1 回だけ読む）。
    """
    out: list[dict] = []
    text: str | None = None
    for row in essence.list_essences(ref):
        if row["stale"] or row["status"] != "succeeded":
            continue
        for index, item in enumerate(row["items"]):
            if item["review_status"] != "approved":
                continue
            if kind and item["kind"] != kind:
                continue
            if text is None:
                text = documents.document_text(ref)
            if text[item["start"]:item["end"]] != item["quote"]:
                continue  # 引用がずれていたら出さない（原文が読め直せない限り本物の引用とは言えない）
            out.append({"essence_ref": f"{row['shard']}:{row['id']}", "item_index": index,
                        "kind": item["kind"], "summary": item["summary"], "quote": item["quote"],
                        "reviewed_at": (item["review"] or {}).get("at")})
    return out


def compare(refs: list[str]) -> dict:
    """別の文献 2〜3 件の承認済みのエッセンスを、種類ごとに並べる。AI の判断は足さない。

    検索と同じ再照合（版・レビュー・引用）を通った item だけを並べる。
    """
    refs = list(refs or [])
    if not 2 <= len(refs) <= 3:
        raise store.KnowledgeError("比較は別の文献 2〜3 件でしてください")
    if len(set(refs)) != len(refs):
        raise store.KnowledgeError("同じ文献は 1 度だけ選んでください")
    docs = []
    for ref in refs:
        doc = documents.get_document(ref)
        if not documents.allowed_for(doc, "local"):
            raise store.KnowledgeError("利用条件により AI に使えない文献は比較できません")
        docs.append({"ref": ref, "title": doc["title"], "items": _approved_items(ref)})
    rows = []
    for kind, label in essence.KINDS.items():
        cells = [[item for item in d["items"] if item["kind"] == kind] for d in docs]
        if any(cells):
            rows.append({"kind": kind, "label": label, "cells": cells})
    return {"documents": [{"ref": d["ref"], "title": d["title"]} for d in docs],
            "rows": rows, "kinds": essence.KINDS}
