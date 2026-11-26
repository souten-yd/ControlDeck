"""Compare RRF, optional #644 fixed floor, and #645 soft reranking on synthetic cases.

Run in backend: python -m tools.eval_research_diversity
The #644 alternative is optional; for a comparison use a disposable evaluation
worktree with its research_fusion.py and this branch's research_diversity.py.
No network, database, LLM, or production search settings are accessed.
"""
from statistics import mean
import json
from app.workflows.research_fusion import reciprocal_rank_fusion, rank_metrics
from app.workflows.research_diversity import diversify
try:
    from app.workflows.research_fusion import coverage_fusion
except ImportError:
    coverage_fusion = None


def run():
    cases = [
        ('single_primary', [('speed',['popular','official','noise']),('speed',['popular','other'])], {'speed':{'popular':.90,'official':.95}}, {'official'}, {'official':3}),
        ('two_aspects', [('speed',['popular','speed','other']),('power',['popular','power','noise'])], {'speed':{'popular':.7,'speed':.95},'power':{'popular':.2,'power':.95}}, set(), {'speed':3,'power':3}),
        ('unrelated_official', [('speed',['best','official','noise']),('speed',['best','noise','official'])], {'speed':{'best':.95,'official':.2}}, {'official'}, {'best':3}),
        ('weak_aspect', [('speed',['best','second','third']),('power',['noise','other'])], {'speed':{'best':.95,'second':.9,'third':.85},'power':{'noise':.1,'other':.2}}, set(), {'best':3,'second':2,'third':1}),
        ('ranker_mistake', [('speed',['gold','popular','other']),('power',['power','popular','noise'])], {'speed':{'gold':.3,'popular':.95},'power':{'power':.8,'popular':.2}}, set(), {'gold':3,'power':3}),
    ]
    all_rows = []
    totals = {}
    for name, groups, aspects, primary, gold in cases:
        grouped = [(str(i),aspect,[{"url":u} for u in urls]) for i,(aspect,urls) in enumerate(groups)]
        fused = reciprocal_rank_fusion([(n,rows) for n,_,rows in grouped],lambda u:u,limit=200)
        choices = {"rrf": fused,
                   "soft_initial": diversify(fused,lambda u:u,aspects=aspects,verified_primary=frozenset(primary),limit=2,quality_weight=0),
                   "soft_revised": diversify(fused,lambda u:u,aspects=aspects,verified_primary=frozenset(primary),limit=2)}
        if coverage_fusion is not None:
            choices["floor"] = coverage_fusion(grouped,lambda u:u,verified_primary=frozenset(primary),limit=2)
        row = {"case":name}
        for mode, rows in choices.items():
            ids = [r["url"] for r in rows]
            metrics = rank_metrics(ids,gold,cutoff=2)
            row[mode] = {"top2":ids[:2], **metrics}
            totals.setdefault(mode,[]).append(metrics)
        all_rows.append(row)
    return {"mode":"synthetic","floor_available":coverage_fusion is not None,"cases":all_rows,
            "macro_at_2":{mode:{k:mean(r[k] for r in rows) for k in ("recall","mrr","ndcg")}
                         for mode,rows in totals.items()}}


if __name__ == "__main__":
    print(json.dumps(run(),ensure_ascii=False,indent=2))
