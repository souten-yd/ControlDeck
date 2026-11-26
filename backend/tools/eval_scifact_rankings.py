"""Run the actual RRF and metrics on independent SciFact relevance judgments.

cd backend && python -m tools.eval_scifact_rankings
The frozen ranks come from offline BM25 over all 5183 abstracts, not ControlDeck
production search. Development 30 chooses a title weight; remaining 270 are
held back from that selection. No network, database, model or production switch.
"""
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean

from app.workflows.research_fusion import reciprocal_rank_fusion, rank_metrics

FIXTURE = Path(__file__).parent / "fixtures" / "scifact_rankings_300.json"
WEIGHTS = (0, 0.1, 0.25, 0.5, 0.75, 1)


def weighted(row, weight):
    if weight == 0:
        return row["a"]
    if weight == 1:
        return [x["url"] for x in reciprocal_rank_fusion(
            [(n,[{"url":v} for v in row[k]]) for n,k in (("title","t"),("abstract","a"))],
            lambda x:x, limit=20)]
    scores, best = defaultdict(float), {}
    for w, ranks in ((weight,row["t"]),(1,row["a"])):
        for i, d in enumerate(ranks, 1):
            scores[d] += w / (60 + i)
            best[d] = min(best.get(d,i),i)
    return sorted(scores, key=lambda d:(-scores[d],best[d],d))[:20]


def aggregate(rows, rank):
    values = [rank_metrics(rank(r),r["g"],cutoff=10) for r in rows]
    return {k:mean(v[k] for v in values) for k in ("recall","mrr","ndcg")}


def baseline(rows):
    return {name:aggregate(rows,lambda r,k=k:r[k]) for name,k in
            (("title","t"),("abstract","a"),("combined","c"))}


def run(cases):
    if (len(cases)!=300 or len({r["q"] for r in cases})!=300
            or [r["q"] for r in cases]!=sorted((r["q"] for r in cases),key=int)):
        raise ValueError("invalid fixed split")
    for r in cases:
        for k in ("t","a","c"):
            if len(r[k])>20 or len(r[k])!=len(set(r[k])):
                raise ValueError("invalid ranks")
    dev, held = cases[:30], cases[30:]
    scores = [(w,aggregate(dev,lambda r,w=w:weighted(r,w))) for w in WEIGHTS]
    chosen = max(scores,key=lambda item:(item[1]["ndcg"],-item[0]))[0]
    # The validation data cannot affect weight selection.
    return {"development":{**baseline(dev),"weights":scores},"chosen_title_weight":chosen,
            "held_back":{**baseline(held),"rrf":aggregate(held,lambda r:weighted(r,1)),
                         "chosen":aggregate(held,lambda r:weighted(r,chosen))},
            "full":{**baseline(cases),"rrf":aggregate(cases,lambda r:weighted(r,1))}}


if __name__=="__main__":
    data=json.loads(FIXTURE.read_text())
    print(json.dumps(run(data["cases"]),indent=2))
