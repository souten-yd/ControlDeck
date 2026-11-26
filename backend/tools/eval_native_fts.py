"""Offline native Knowledge FTS candidate evaluation; no app startup or real DB.

cd backend && python -m tools.eval_native_fts /path/to/valid-v1.1.json [--sample]
"""
import argparse
import hashlib
import json
import math
import re
import sqlite3
import statistics
import time
from pathlib import Path

from app.knowledge import textproc
from app.knowledge.store import _fts_ranks

SOURCE = "bcde414e0b7131607bb1fa2c4542dd888f68771a"


def load(path, sample):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("source too large")
    raw = path.read_bytes()
    if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != SOURCE:
        raise ValueError("source blob mismatch")
    rows = []
    for ai, article in enumerate(json.loads(raw)["data"]):
        for pi, p in enumerate(article["paragraphs"]):
            body = p["context"]
            valid = [q for q in p["qas"] if any(
                body[a["answer_start"]:a["answer_start"]+len(a["text"])] == a["text"]
                for a in q["answers"])]
            if sample:
                # JS selection used UTF-16 lengths/offsets; this sample is BMP-only.
                if not 100 <= len(body) <= 240 or len(p["qas"]) < 2 or valid[:2] != p["qas"][:2]:
                    continue
                valid = valid[:2]
            rows.append((ai, pi, body, [q["question"] for q in valid]))
            if sample:
                break
        if sample and len(rows) == 20:
            break
    if sample and [(a,p) for a,p,_,_ in rows] != [(0,0),(1,0),(2,0),(3,1),(4,0),(5,0),(6,0),(7,0),(9,0),(10,0),(12,0),(13,0),(14,0),(15,0),(16,2),(17,1),(18,0),(19,0),(20,2),(21,6)]:
        raise ValueError("sample selection mismatch")
    return rows


def query(conn, expr, limit):
    if not expr:
        return []
    return [r[0] for r in conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?", (expr, limit))]


def guarded(conn, question, limit):
    """Evaluation-only candidate: explicit syntax must not fall back to bag-of-words."""
    if re.search(r'(?:^|\s)-\S|(?:^|\s)or(?:\s|$)|"', textproc.normalize(question)):
        return query(conn, textproc.parse_query(question), limit)
    return _fts_ranks(conn, question, [1], limit)


def run(rows):
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("CREATE TABLE chunks(id INTEGER PRIMARY KEY, collection_id INTEGER);"
                           "CREATE VIRTUAL TABLE chunks_fts USING fts5(tok, tokenize='unicode61 remove_diacritics 0');"
                           "CREATE VIRTUAL TABLE news_fts USING fts5(body, tokenize='trigram');")
        for i, (_, _, body, _) in enumerate(rows, 1):
            conn.execute("INSERT INTO chunks VALUES (?,1)", (i,))
            conn.execute("INSERT INTO chunks_fts(rowid,tok) VALUES (?,?)", (i,textproc.index_text(body)))
            conn.execute("INSERT INTO news_fts(rowid,body) VALUES (?,?)", (i,body))

        def literal(q):
            terms = q.split()[:8]
            if not terms:
                return []
            if all(len(t)>=3 for t in terms):
                expr = " ".join('"' + t.replace('"','""') + '"' for t in terms)
                return [r[0] for r in conn.execute(
                    "SELECT rowid FROM news_fts WHERE news_fts MATCH ? ORDER BY bm25(news_fts) LIMIT 20",(expr,))]
            return [i for i, (_,_,body,_) in enumerate(rows,1) if all(t in body for t in terms)][:20]

        out = {}
        for name, fn in [
            ("strict",lambda q:query(conn,textproc.parse_query(q),20)),
            ("loose",lambda q:query(conn,textproc.or_query(q),20)),
            ("native_20",lambda q:_fts_ranks(conn,q,[1],20)),
            ("native_160",lambda q:_fts_ranks(conn,q,[1],160)),
            ("guarded_20",lambda q:guarded(conn,q,20)),
            ("news_literal_diagnostic",literal),
        ]:
            ranks, times, empty = [], [], 0
            for i, (_, _, _, qs) in enumerate(rows,1):
                for q in qs:
                    t = time.perf_counter_ns()
                    found = fn(q)
                    times.append((time.perf_counter_ns()-t)/1e6)
                    empty += not found
                    ranks.append(found.index(i)+1 if i in found else 0)
            n = len(ranks)
            out[name] = dict(n=n,hit1=sum(p==1 for p in ranks),
                hit10=sum(0<p<=10 for p in ranks),
                mrr10=sum(1/p for p in ranks if 0<p<=10)/n,
                ndcg10=sum(1/math.log2(p+1) for p in ranks if 0<p<=10)/n,
                empty=empty,p50_ms=statistics.median(times),
                p95_ms=sorted(times)[math.ceil(n*.95)-1])
        negatives = []
        for q, forbidden in [("コンゴ共和国 -ブラザヴィル","ブラザヴィル"),
                            ("国際連合 -平和","平和"),("オランダ -ベルギー","ベルギー")]:
            actual = _fts_ranks(conn,q,[1],20)
            proposed = guarded(conn,q,20)
            negatives.append({"query":q,"native_forbidden":sum(forbidden in rows[i-1][2] for i in actual),
                              "guarded_forbidden":sum(forbidden in rows[i-1][2] for i in proposed)})
        explicit = []
        # Sample-specific expected document identities, derived from the source passages.
        if [(a,p) for a,p,_,_ in rows] == [(0,0),(1,0),(2,0),(3,1),(4,0),(5,0),(6,0),(7,0),(9,0),(10,0),(12,0),(13,0),(14,0),(15,0),(16,2),(17,1),(18,0),(19,0),(20,2),(21,6)]:
            for q, expected in [
                ("首都 -ベルギー",{6,7}), ("首都 -コンゴ共和国",{7,10}),
                ("梅雨 OR 落語家",{1,14}), ('"国際連合平和維持軍"',{5}),
                ('"国際連合平和維持軍" -PKF',set()),
                ('"日本共産党" OR "コンゴ共和国"',{6,8}),
            ]:
                native = set(_fts_ranks(conn,q,[1],20))
                proposed = set(guarded(conn,q,20))
                explicit.append({"query":q,"expected":sorted(expected),
                    "native":sorted(native),"guarded":sorted(proposed),
                    "native_exact":native==expected,"guarded_exact":proposed==expected})
        return {"sqlite":sqlite3.sqlite_version,"documents":len(rows),"metrics":out,"negatives":negatives,"explicit":explicit}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source",type=Path)
    parser.add_argument("--sample",action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(load(args.source,args.sample)),ensure_ascii=False,indent=2))
