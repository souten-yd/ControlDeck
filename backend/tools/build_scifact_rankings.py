"""Regenerate rank inputs from an explicitly supplied BEIR SciFact directory.

cd backend && python -m tools.build_scifact_rankings /path/to/scifact /tmp/ranks.json
Source files must match the pinned mirror blobs. No download or production index.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

SHA = {"corpus":"2d94adae7011508e4375377add4ab94f2a9c787e",
       "queries":"535fd802ec3f9a7f5b99c7cf35c57294b6c255b4",
       "qrels":"c62f58c75f10c0cee23483b7269bff2902ae4080"}
MIRROR = "https://github.com/Jonas-Gustafsson/retrieval-evaluation-thesis/tree/main/data/raw/scifact"


def verified(path, name):
    if path.stat().st_size > 16*1024*1024:
        raise ValueError("source size limit")
    b=path.read_bytes()
    if hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()!=SHA[name]:
        raise ValueError("source blob mismatch: "+name)
    return b.decode("utf-8")


def tokens(s):
    return re.findall("[a-z0-9]+",s.lower())


def build(docs, field):
    postings=defaultdict(list)
    lengths=[]
    for i,d in enumerate(docs):
        ts=tokens(field(d))
        lengths.append(len(ts))
        for t,n in Counter(ts).items():
            postings[t].append((i,n))
    return postings,lengths,sum(lengths)/len(lengths)


def rank(docs,index,query):
    postings,lengths,avg=index
    scores=defaultdict(float)
    for t in dict.fromkeys(tokens(query)):
        rows=postings.get(t,[])
        if not rows:
            continue
        idf=math.log(1+(len(docs)-len(rows)+0.5)/(len(rows)+0.5))
        for i,tf in rows:
            scores[i]+=idf*tf*2.2/(tf+1.2*(.25+.75*lengths[i]/avg))
    return [docs[i]["_id"] for i in sorted(scores,key=lambda i:(-scores[i],docs[i]["_id"]))[:20]]


def generate(root):
    docs=[json.loads(l) for l in verified(root/"corpus.jsonl","corpus").splitlines()]
    queries={r["_id"]:r["text"] for r in map(json.loads,verified(root/"queries.jsonl","queries").splitlines())}
    gold=defaultdict(dict)
    for line in verified(root/"qrels"/"test.tsv","qrels").splitlines()[1:]:
        q,d,s=line.split("\t")
        if int(s)>0:
            gold[q][d]=int(s)
    if len(docs)!=5183 or len(gold)!=300 or len(queries)!=1109:
        raise ValueError("unexpected dataset size")
    indexes=[build(docs,f) for f in (lambda d:d["title"],lambda d:d["text"],lambda d:d["title"]+" "+d["text"])]
    cases=[]
    for q in sorted(gold,key=int):
        t,a,c=[rank(docs,i,queries[q]) for i in indexes]
        cases.append({"q":q,"g":gold[q],"t":t,"a":a,"c":c})
    return {"dataset":"BEIR SciFact","mirror":MIRROR,"sha":SHA,"corpus_count":len(docs),"test_queries":len(gold),
            "selection":"numeric query ID sort; first 30 development, remaining 270 held back from weight selection",
            "ranker":"BM25 k1=1.2,b=.75; unique lower ASCII alphanumeric query tokens; title / abstract / combined; top20 each; id string tie-break",
            "cases":cases}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    p.add_argument("output",type=Path)
    args=p.parse_args()
    # Exclusive create: do not overwrite the fixed fixture.
    with args.output.open("x",encoding="utf-8") as out:
        json.dump(generate(args.root),out,ensure_ascii=False,separators=(",",":"))
        out.write("\n")
