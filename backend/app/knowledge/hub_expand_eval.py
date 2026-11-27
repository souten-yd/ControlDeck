"""ハブの関係・概要で検索の語を広げる効果と時間を測る（「広げない／広げる」の Recall@10・nDCG@10・時間）。

目的: AGENTS.md「機能を取り込む条件」に従い、特許・Web 調査・RAG の 3 つの使う場所それぞれで、広げる前後を同じ問いで比べ、効いた所だけ既定オンにする。
根拠: 2026-11-27 利用者の依頼（ハブの関係・概要を検索語の拡張に使えないか）。評価は読むだけ: ControlDeck の知識の DB は sqlite のバックアップ（読み取り専用の接続）で
複製して使い（本番を書き換えない・開かない）、ハブは複製の DB で別のポートに起こした読み取りのトークンの取り出し（本番のハブへは書かない）。外へ通信しない・LLM は呼ばない。

使い方（隔離して流す。CONTROL_DECK_CONFIG は外す）:
  env -u CONTROL_DECK_CONFIG .venv/bin/python -m app.knowledge.hub_expand_eval build --hub-db <ハブの複製 news.sqlite3> --knowledge <knowledge の置き場> --out questions.json
  env -u CONTROL_DECK_CONFIG .venv/bin/python -m app.knowledge.hub_expand_eval run --questions questions.json --knowledge <knowledge の置き場> --hub-url http://127.0.0.1:18787 --hub-token-file <token> --out result.json

問い（3 つの使う場所 × 10 問）:
- rag・web: ControlDeck の「news」コレクション（ハブから同期した 4,774 記事）。7 問は「言い方の違い」（別名・別の表記で問い、正解は正式名・別名のどちらかを含む記事）、
  3 問は対照（別名の無い物の名前で問う。正解は名前を含む記事。広げて悪くならないかを見る）。
- patent: 「papers」コレクション（ControlDeck の公開文献＝ESD の論文）。題の語で問い、正解はその論文。**ハブはこの分野を知らない**ので、広がらない（広がらないことの確認と時間）。
正解は語の一致（本文に名前・別名を含む）で決めた機械的なもの（人の判定ではない）。「言い方の違い」の問いは、別名を持つ物を選んでいるので、広げる側に有利な上限に近い値である。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from pathlib import Path

from app.knowledge import hub_expand, store

K = 10
FUSE_K = 60
EXPAND_WEIGHT = 0.5      # 広げた問いの結果の重み（元の問いは 1.0）
OR_TERMS = 6
WEB_EXTRA = 2


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", _fold(text))


# ---- 問いを作る ----

def _news_chunks(knowledge: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(f"file:{knowledge / 'news.sqlite3'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT 'news:' || d.id, d.title || ' ' || group_concat(c.text, ' ') FROM documents d JOIN chunks c ON c.doc_id=d.id GROUP BY d.id").fetchall()
    finally:
        conn.close()
    return [(ref, _fold(text)) for ref, text in rows]


def build(hub_db: Path, knowledge: Path, out: Path, seed: int = 1, per_mismatch: int = 7, per_control: int = 3) -> dict:
    rnd = random.Random(seed)
    docs = _news_chunks(knowledge)
    hub = sqlite3.connect(f"file:{hub_db}?mode=ro", uri=True)
    try:
        ents = {r[0]: {"id": r[0], "name": r[1], "type": r[2], "count": r[3], "aliases": []}
                for r in hub.execute("SELECT id, name, type, article_count FROM entities WHERE article_count BETWEEN 3 AND 400")}
        for eid, alias in hub.execute("SELECT entity_id, alias FROM entity_aliases"):
            if eid in ents:
                ents[eid]["aliases"].append(alias)
    finally:
        hub.close()

    def having(term: str) -> set[str]:
        needle = _fold(term)
        return {ref for ref, text in docs if needle in text}

    mismatch, control = [], []
    for ent in ents.values():
        name_docs = having(ent["name"])
        if not ent["aliases"]:
            if 3 <= len(name_docs) <= 10 and len(ent["name"]) >= 4:
                control.append({"type": "control", "q": ent["name"], "gold": sorted(name_docs), "entity": ent["name"]})
            continue
        for alias in ent["aliases"]:
            if _key(alias) == _key(ent["name"]) or len(alias) < 3 or re.search(r"\d+\.\d+", alias):
                continue      # 大文字小文字・区切りだけの違い（全文検索がそもそも吸収する）・版つきの別名は「言い方の違い」ではない
            alias_docs = having(alias)
            gold = name_docs | alias_docs
            for query, own in ((alias, alias_docs), (ent["name"], name_docs)):
                missing = gold - own
                if own and len(missing) >= 2 and len(gold) <= 40:
                    mismatch.append({"type": "mismatch", "q": query, "gold": sorted(gold), "entity": ent["name"], "alias": alias, "missed_by_plain": len(missing)})
    best: dict[str, dict] = {}
    for row in sorted(mismatch, key=lambda r: (-r["missed_by_plain"], r["q"])):
        best.setdefault(row["entity"], row)
    mism = list(best.values())
    rnd.shuffle(mism)
    control.sort(key=lambda r: r["q"])
    rnd.shuffle(control)
    questions = {"rag": [], "web": [], "patent": []}
    m, c = per_mismatch, per_control
    questions["rag"] = [{**q, "id": f"rag-{i + 1}"} for i, q in enumerate(mism[:m] + control[:c])]
    questions["web"] = [{**q, "id": f"web-{i + 1}"} for i, q in enumerate(mism[m:2 * m] + control[c:2 * c])]
    questions["candidates"] = {"mismatch": len(mism), "control": len(control)}
    # 特許: ControlDeck の公開文献（papers）。題の語の問い（英語の論文）。ハブはこの分野を知らない
    conn = sqlite3.connect(f"file:{knowledge / 'general.sqlite3'}?mode=ro", uri=True)
    try:
        papers = conn.execute("SELECT 'general:' || d.id, d.title FROM documents d JOIN collections c ON c.id=d.collection_id WHERE c.name='papers' ORDER BY d.id").fetchall()
    finally:
        conn.close()
    stop = {"the", "and", "for", "with", "using", "based", "analysis", "design", "study", "research", "novel", "devices", "approach", "method", "methods"}
    picked = []
    for ref, title in papers:
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", title) if w.lower() not in stop]
        if len(words) >= 3:
            picked.append({"type": "title", "q": " ".join(words[:3]), "gold": [ref], "entity": title[:60]})
    rnd.shuffle(picked)
    questions["patent"] = [{**q, "id": f"patent-{i + 1}"} for i, q in enumerate(picked[:10])]
    candidates = questions.pop("candidates")
    data = {"version": 1, "candidates": candidates, "seed": seed, "note": "正解は語の一致で決めた機械的なもの。mismatch は別名を持つ物を選んでいるので広げる側に有利", "questions": questions}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**{p: len(v) for p, v in questions.items()}, **candidates}


# ---- 検索と融合 ----

def _docs(hits: list[dict]) -> list[str]:
    seen, out = set(), []
    for hit in hits:
        if hit["doc_ref"] not in seen:
            seen.add(hit["doc_ref"])
            out.append(hit["doc_ref"])
    return out


def _search(collection: str, query: str) -> list[str]:
    return _docs(store.search([collection], query, mode="fulltext", limit=80))


def rrf(lists: list[list[str]], weights: list[float]) -> list[str]:
    score: dict[str, float] = {}
    for items, weight in zip(lists, weights):
        for rank, ref in enumerate(items):
            score[ref] = score.get(ref, 0.0) + weight / (FUSE_K + rank)
    return sorted(score, key=lambda ref: -score[ref])


def round_robin(lists: list[list[str]]) -> list[str]:
    out, seen = [], set()
    for rank in range(max((len(x) for x in lists), default=0)):
        for items in lists:
            if rank < len(items) and items[rank] not in seen:
                seen.add(items[rank])
                out.append(items[rank])
    return out


def recall_at(ranked: list[str], gold: set[str], k: int = K) -> float:
    return len(set(ranked[:k]) & gold) / min(k, len(gold)) if gold else 0.0


def ndcg_at(ranked: list[str], gold: set[str], k: int = K) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, ref in enumerate(ranked[:k]) if ref in gold)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(gold))))
    return dcg / ideal if ideal else 0.0


def run_question(purpose: str, collection: str, q: dict, link: dict, min_weight: float = 0.0) -> dict:
    t0 = time.perf_counter()
    base = _search(collection, q["q"])
    base_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    ex = hub_expand.expand(q["q"], purpose=purpose, link=link, confidential=False, budget_ms=800, min_weight=0.0)
    hub_ms = (time.perf_counter() - t1) * 1000
    terms = [t for t in ex["terms"] if t["weight"] >= min_weight]
    t2 = time.perf_counter()
    if not terms:
        fused = base
    elif purpose == "web":
        extra = [_search(collection, f"{q['q']} {t['term']}") for t in terms[:WEB_EXTRA]]
        fused = round_robin([base, *extra])
    else:      # rag・patent: 元の問い＋広げた語の OR の問いを RRF で融合
        fused = rrf([base, _search(collection, hub_expand.or_query(terms, max_terms=OR_TERMS))], [1.0, EXPAND_WEIGHT])
    extra_ms = (time.perf_counter() - t2) * 1000
    gold = set(q["gold"])
    return {"id": q["id"], "type": q["type"], "q": q["q"], "terms": [(t["term"], t["weight"]) for t in terms[:OR_TERMS]], "reason": ex["reason"], "gold": len(gold),
            "base": {"recall": recall_at(base, gold), "ndcg": ndcg_at(base, gold), "ms": base_ms},
            "expanded": {"recall": recall_at(fused, gold), "ndcg": ndcg_at(fused, gold), "ms": base_ms + hub_ms + extra_ms, "hub_ms": hub_ms, "extra_ms": extra_ms}}


def _mean(rows: list[dict], side: str, key: str) -> float:
    return statistics.fmean(r[side][key] for r in rows) if rows else 0.0


def run(questions: Path, knowledge: Path, hub_url: str, token: str) -> dict:
    data = json.loads(questions.read_text(encoding="utf-8"))["questions"]
    scratch = knowledge
    store.shard_path = lambda shard: scratch / f"{shard}.sqlite3"      # 複製を読む（本番の置き場を開かない）
    link = {"url": hub_url, "token": token}
    where = {"rag": "news", "web": "news", "patent": "papers"}
    result: dict = {"purposes": {}}
    for purpose, rows in data.items():
        out = []
        for variant, min_weight in (("all", 0.0), ("mid", 0.45), ("strong", 0.7)):
            hub_expand.clear_cache()
            got = [run_question(purpose, where[purpose], q, link, min_weight) for q in rows]
            out.append({"variant": variant, "rows": got})
        result["purposes"][purpose] = out
    return result


def summarize(result: dict) -> str:
    lines = []
    for purpose, variants in result["purposes"].items():
        for item in variants:
            rows = item["rows"]
            for label, subset in (("全体", rows), ("言い方の違い・題", [r for r in rows if r["type"] != "control"]), ("対照", [r for r in rows if r["type"] == "control"])):
                if not subset:
                    continue
                lines.append(f"| {purpose} | {item['variant']} | {label} | {len(subset)} | {_mean(subset, 'base', 'recall'):.3f} → {_mean(subset, 'expanded', 'recall'):.3f} "
                             f"| {_mean(subset, 'base', 'ndcg'):.3f} → {_mean(subset, 'expanded', 'ndcg'):.3f} | {_mean(subset, 'base', 'ms'):.1f} → {_mean(subset, 'expanded', 'ms'):.1f} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hub_expand_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--hub-db", required=True)
    b.add_argument("--knowledge", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--per-mismatch", type=int, default=7)
    b.add_argument("--per-control", type=int, default=3)
    r = sub.add_parser("run")
    r.add_argument("--questions", required=True)
    r.add_argument("--knowledge", required=True)
    r.add_argument("--hub-url", required=True)
    r.add_argument("--hub-token-file", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print(build(Path(a.hub_db), Path(a.knowledge), Path(a.out), per_mismatch=a.per_mismatch, per_control=a.per_control))
        return 0
    token = Path(a.hub_token_file).read_text().strip()
    result = run(Path(a.questions), Path(a.knowledge), a.hub_url, token)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("| 場所 | 版 | 区分 | 問い | Recall@10 前→後 | nDCG@10 前→後 | 時間 ms 前→後 |\n|---|---|---|---|---|---|---|")
    print(summarize(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
