"""W5b-1: オフライン評価用の検索順位融合。既定の検索経路は変更しない。"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable


def reciprocal_rank_fusion(groups: list[tuple[str, list[dict]]], key: Callable[[str], str],
                           *, limit: int = 30, k: int = 60) -> list[dict]:
    """独立した検索語/提供元ごとに一票。同じグループ内の重複は加点しない。
    既存の結果dictを変更せず返す。グループ名は呼び出し側が固定する。
    """
    if not 1 <= k <= 1000 or not 1 <= limit <= 200:
        raise ValueError("invalid fusion bounds")
    scores = defaultdict(float)
    records = {}
    best = {}
    seen_groups = set()
    for name, rows in groups:
        if not name or name in seen_groups:
            continue
        seen_groups.add(name)
        seen = set()
        rank = 0
        for row in rows[:200]:
            url = row.get("url")
            if not isinstance(url, str) or not url:
                continue
            identity = key(url)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            rank += 1
            scores[identity] += 1 / (k + rank)
            if identity not in records or rank < best[identity]:
                records[identity] = row
                best[identity] = rank
    order = sorted(scores, key=lambda identity: (-scores[identity], best[identity], identity))
    return [dict(records[identity]) for identity in order[:limit]]


def rank_metrics(ranked: list[str], relevant: dict[str, int], *, cutoff: int = 10) -> dict:
    """固定の独立正解で recall/MRR/nDCG を評価。重複は除き、正解なしを採点しない。"""
    if cutoff < 1 or not relevant or any(type(v) is not int or not 1 <= v <= 3 for v in relevant.values()):
        raise ValueError("invalid gold relevance")
    top = list(dict.fromkeys(ranked))[:cutoff]
    gains = [relevant.get(x, 0) for x in top]
    dcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sum((2**g - 1) / math.log2(i + 2)
                for i, g in enumerate(sorted(relevant.values(), reverse=True)[:cutoff]))
    return {"recall": sum(g > 0 for g in gains) / len(relevant),
            "mrr": next((1 / (i + 1) for i, g in enumerate(gains) if g), 0.0),
            "ndcg": dcg / ideal}
