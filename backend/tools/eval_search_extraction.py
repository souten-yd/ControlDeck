"""固定合成ケースで検索融合/URL/HTML表を評価。外部通信や本番DBへの書き込みなし。
cd backend && python -m tools.eval_search_extraction
比較対象の Draft ブランチをローカルに取得し、このスクリプトを評価用コピーで実行する。
未導入の部品は skip として明示する。未実装項目を CI の失敗にしない。
"""
import json
import statistics
import time

from app.workflows.research_tools import _merge, _norm_url


def rank_cases():
    try:
        from app.workflows.research_fusion import reciprocal_rank_fusion, rank_metrics
    except ModuleNotFoundError as exc:
        if exc.name != "app.workflows.research_fusion":
            raise
        return {"skip": "W5b-1 not available"}
    cases = [
        ("consensus", [["a","gold","b"],["c","gold","d"],["e","gold","f"]], {"gold":3}),
        ("rare_official", [["official","popular","x"],["popular","y","z"],["popular","w","v"]], {"official":3}),
        ("multi_evidence", [["a","gold1","gold2"],["b","gold2","gold1"],["c","gold2","d"]], {"gold1":3,"gold2":2}),
        ("independent_aspects", [["official","a","b"],["community","a","c"],["a","d","e"]], {"official":3,"community":2}),
        ("duplicate_noise", [["a","a","gold"],["b","gold"],["c","gold"]], {"gold":3}),
        ("no_overlap", [["official","a"],["community","b"],["paper","c"]], {"official":3,"community":2,"paper":2}),
    ]
    rows = []
    for name, groups, gold in cases:
        lists = [[{"url": "https://example.test/"+v} for v in g] for g in groups]
        ranked = {"interleave": _merge(lists, 30),
                  "rrf": reciprocal_rank_fusion([(str(i),g) for i,g in enumerate(lists)], _norm_url)}
        row = {"case": name}
        for mode, vals in ranked.items():
            order = [v["url"].rsplit("/",1)[-1] for v in vals]
            row[mode] = {"top3": order[:3], **rank_metrics(order, gold, cutoff=3)}
        rows.append(row)
    return {"cases": rows, "macro_at_3": {mode: {k: statistics.mean(r[mode][k] for r in rows)
            for k in ("recall","mrr","ndcg")} for mode in ("interleave","rrf")}}


def url_cases():
    pairs = [
        ("tracking", "https://example.test/a?id=1&utm_source=x","https://example.test/a?id=1",True),
        ("distinct_query","https://example.test/a?id=1","https://example.test/a?id=2",False),
        ("repeated_order","https://example.test/a?x=1&x=2","https://example.test/a?x=2&x=1",False),
        ("www","https://example.test/a","https://www.example.test/a",False),
        ("scheme","http://example.test/a","https://example.test/a",False),
        ("slash","https://example.test/a","https://example.test/a/",False),
    ]
    return [{"case":name,"equal":_norm_url(a)==_norm_url(b),"expected_equal":want,
             "pass": (_norm_url(a)==_norm_url(b))==want} for name,a,b,want in pairs]


def table_cases():
    try:
        from app.web_collector.html_tables import extract_tables, MAX_HTML
    except ModuleNotFoundError as exc:
        if exc.name != "app.web_collector.html_tables":
            raise
        return {"skip": "W4a not available"}
    cases = [
        ("simple", '<table><tr><th>GPU</th><th>Decode</th></tr><tr><td>R9700</td><td>60</td></tr></table>',True),
        ("merged", '<table><tr><th rowspan="2">GPU</th><th colspan="2">Speed</th></tr><tr><th>Decode</th><th>Prefill</th></tr><tr><td>R9700</td><td>60</td><td>420</td></tr></table>',True),
        ("layout", '<table><tr><td>layout</td></tr></table>',False),
        ("invalid_span", '<table><tr><th rowspan="0">GPU</th></tr></table>',False),
        ("oversized", '<table>'+("x"*MAX_HTML)+'</table>',False),
        ("nested", '<table><tr><th>GPU</th><td><table><tr><th>inner</th></tr></table></td></tr></table>',False),
        ("ragged", '<table><tr><th>GPU</th><th>Decode</th></tr><tr><td>60</td></tr></table>',False),
        ("hidden", '<table hidden><tr><th>GPU</th></tr><tr><td>wrong</td></tr></table>',False),
        ("aria_hidden", '<table aria-hidden="true"><tr><th>GPU</th></tr><tr><td>wrong</td></tr></table>',False),
        ("script_removal", '<table><tr><th>GPU</th></tr><tr><td>R9700<script>evil()</script></td></tr></table>',True),
    ]
    rows = []
    for name, html, expected in cases:
        tables, warnings = extract_tables(html)
        ok = bool(tables)==expected
        if name=="merged" and ok:
            ok = [(c["row"],c["col"],c["rowspan"],c["colspan"]) for c in tables[0]["cells"][:4]]==[(0,0,2,1),(0,1,1,2),(1,1,1,1),(1,2,1,1)]
        if name=="script_removal" and ok:
            ok = tables[0]["cells"][-1]["text"]=="R9700"
        rows.append({"case":name,"pass":ok,"warnings":warnings})
    samples = []
    for _ in range(100):
        start=time.perf_counter()
        extract_tables(cases[1][1])
        samples.append((time.perf_counter()-start)*1000)
    return {"cases": rows,"passed":sum(r["pass"] for r in rows),"total":len(rows),
            "small_table_ms":{"p50":statistics.median(samples),"p95":sorted(samples)[94]}}


if __name__ == "__main__":
    print(json.dumps({"mode":"synthetic","ranking":rank_cases(),"url":url_cases(),"tables":table_cases()},ensure_ascii=False,indent=2))
