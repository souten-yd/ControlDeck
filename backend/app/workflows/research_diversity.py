"""Experimental bounded reranking after RRF; no network or production switch."""
from __future__ import annotations

import math
from typing import Callable, Mapping


def diversify(ranked: list[dict], key: Callable[[str], str], *,
              aspects: Mapping[str, Mapping[str, float]],
              verified_primary: frozenset[str] = frozenset(),
              limit: int = 10, min_relevance: float = 0.6,
              weight: float = 0.45) -> list[dict]:
    """Use caller-validated aspect relevance and primary identities, not page claims.

    The scores must come from an independently evaluated ranker, not gold labels.
    With no useful scores the RRF order remains unchanged. At most one verified
    relevant primary is protected; remaining coverage is a soft bonus, not a quota.
    """
    if not 1 <= limit <= 200 or not 0 <= weight <= 1 or not 0 < min_relevance <= 1:
        raise ValueError("invalid bounds")
    if len(aspects) > 16 or len(verified_primary) > 200:
        raise ValueError("too many aspects or primary identities")
    pool = {}
    for row in ranked[:200]:
        url = row.get("url")
        if isinstance(url, str) and url:
            identity = key(url)
            if identity and identity not in pool:
                pool[identity] = dict(row)
    ids = list(pool)
    scores = {}
    for aspect, values in aspects.items():
        if not aspect or len(values) > 200:
            raise ValueError("invalid aspect scores")
        for identity, score in values.items():
            if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid relevance score")
        scores[aspect] = {identity: float(values.get(identity, 0)) for identity in ids}
    if not ids or not scores or weight == 0:
        return [pool[i] for i in ids[:limit]]
    rank = {identity: i + 1 for i, identity in enumerate(ids)}
    relevance = {identity: max(v[identity] for v in scores.values()) for identity in ids}
    chosen = []
    coverage = dict.fromkeys(scores, 0.0)

    def add(identity):
        chosen.append(identity)
        for a, values in scores.items():
            if values[identity] >= min_relevance:
                coverage[a] = max(coverage[a], values[identity])

    primary = [i for i in ids[:min(10, len(ids))] if i in verified_primary and relevance[i] >= min_relevance]
    if primary:
        best = max(relevance.values())
        primary = [i for i in primary if relevance[i] >= best - 0.1]
        if primary:
            add(min(primary, key=lambda i: rank[i]))
    while len(chosen) < min(limit, len(ids)):
        def score(i):
            base = 1 / rank[i]
            novelty = max((v[i] * (1 - coverage[a]) for a, v in scores.items()
                           if v[i] >= min_relevance), default=0.0)
            return (1 - weight) * base + weight * novelty
        add(max((i for i in ids if i not in chosen), key=lambda i: (score(i), -rank[i])))
    return [pool[i] for i in chosen]
