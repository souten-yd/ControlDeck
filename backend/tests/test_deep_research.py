"""反復Deep Research、GitHub構造解析、CTX profileの回帰テスト。"""
import asyncio
import json


def run(coro):
    return asyncio.run(coro)


def test_deep_research_runs_multiple_rounds_and_source_portfolio():
    from app.workflows.deep_research import run_deep_research

    assessments = 0
    specialized_calls: list[str] = []
    progress: list[tuple[str, int]] = []

    async def complete(messages, *, max_tokens, response_format=None):
        nonlocal assessments
        schema_name = (response_format or {}).get("name")
        if schema_name == "deep_research_plan":
            return json.dumps({
                "objective": "対象を構造まで評価", "sub_questions": ["仕組み", "実装", "反証", "統合"],
                "search_queries": ["topic architecture", "topic evidence"],
                "evaluation_criteria": ["一次情報", "反証"],
                "source_types": ["web", "academic", "patent", "market"],
            })
        if schema_name == "deep_research_assessment":
            assessments += 1
            return json.dumps({
                "sufficient": assessments >= 2, "coverage_score": 45 if assessments == 1 else 88,
                "gaps": ["追加実装"] if assessments == 1 else [], "contradictions": [],
                "next_queries": ["topic implementation details"] if assessments == 1 else [],
            })
        return (
            "## 分析\n\n事実を複数資料で確認した。[1][2][3]\n\n"
            "構造と実装を比較した。[4][5][6]\n\n統合可能性と制約を評価した。[7][8]"
            "\n<!-- CONTROLDECK_SECTION_COMPLETE -->"
        )

    async def web(query, limit):
        return [
            {"title": f"Web {query} {i}", "url": f"https://web{i}.test/{query.replace(' ', '-')}", "snippet": "web evidence " * 80}
            for i in range(limit)
        ]

    async def academic(query, limit):
        return [
            {"title": f"Paper {query} {i}", "url": f"https://doi.org/10.1/{i}-{query.replace(' ', '-')}", "snippet": "paper evidence " * 80}
            for i in range(limit)
        ]

    async def specialized(kind, query, limit):
        specialized_calls.append(kind)
        return [
            {"title": f"{kind} {query} {i}", "url": f"https://{kind}{i}.test/item", "snippet": f"{kind} evidence " * 80}
            for i in range(limit)
        ]

    async def fetch(url, limit):
        return (f"full page {url} " * 300)[:limit]

    result = run(run_deep_research(
        "topic", complete=complete, web_search=web, academic_search=academic,
        specialized_search=specialized, page_fetch=fetch,
        progress=lambda phase, label, round_number, details: progress.append((phase, round_number)),
    ))
    assert result["research"]["rounds"] == 2
    assert result["research"]["sources_discovered"] >= 20
    assert result["research"]["coverage"]["coverage_score"] == 88
    assert {"patent", "market"}.issubset(set(specialized_calls))
    assert ("coverage", 1) in progress and ("coverage", 2) in progress
    assert result["research"]["citation_metrics"]["invalid_citations"] == []
    assert result["research"]["citation_metrics"]["completed_sections"] == 6


def test_deep_report_continues_sections_cut_by_output_limit():
    from app.workflows.deep_research import DeepAssessment, DeepPlan, _synthesize

    calls = []

    async def complete(messages, *, max_tokens, response_format=None):
        calls.append((messages[-1]["content"], max_tokens))
        if "直前の章が出力上限" in messages[-1]["content"]:
            return "後半まで完結した。[1]\n<!-- CONTROLDECK_SECTION_COMPLETE -->"
        return "## 章\n\n前半で出力上限に達した。[1]"

    report, metrics = run(_synthesize(
        "網羅調査", DeepPlan(
            objective="完全な報告", sub_questions=["構造", "実装"], search_queries=["query", "query detail"],
            evaluation_criteria=["完全性"], source_types=["web"],
        ), [{"title": "一次資料", "url": "https://example.test", "source": "web", "snippet": "evidence"}],
        DeepAssessment(sufficient=True, coverage_score=100), [], complete,
        max_context_chars=12000, max_report_tokens=32768,
    ))
    assert report.count("後半まで完結した") == 6
    assert metrics["completed_sections"] == 6
    assert metrics["possibly_truncated_sections"] == []
    assert any("直前の章が出力上限" in prompt for prompt, _ in calls)


def test_code_structure_indexes_functions_variables_routes_and_integrations():
    from app.workflows.code_structure import repository_structure_summary

    summary = repository_structure_summary([
        {"meta": {"path": "backend/app/router.py"}, "snippet": """
from fastapi import APIRouter
router = APIRouter()
LIMIT = 10
class Service: pass
@router.get('/items')
async def list_items(db, limit=LIMIT):
    return load_items(db, limit)
"""},
        {"meta": {"path": "frontend/src/api.ts"}, "snippet": """
import { client } from './client';
export interface Item { id: number }
export const loadItems = async () => client.get('/items');
"""},
    ])
    assert "async list_items(db, limit)" in summary
    assert "LIMIT" in summary and "router.get" in summary
    assert "loadItems()" in summary and "./client" in summary and "Item" in summary


def test_github_adapter_reads_tree_key_files_and_static_index(monkeypatch):
    from app.workflows import github_research as gh

    tree = [
        {"path": "README.md", "type": "blob", "size": 100},
        {"path": "pyproject.toml", "type": "blob", "size": 100},
        {"path": "src/main.py", "type": "blob", "size": 200},
        {"path": "tests/test_main.py", "type": "blob", "size": 200},
        {"path": ".github/workflows/ci.yml", "type": "blob", "size": 100},
    ]

    class Response:
        def __init__(self, status_code=200, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, **kwargs):
            if url.endswith("/repos/acme/project"):
                return Response(payload={"default_branch": "main", "html_url": "https://github.com/acme/project", "description": "demo", "language": "Python"})
            if "/git/trees/" in url:
                return Response(payload={"tree": tree, "truncated": False})
            if url.endswith("src/main.py"):
                return Response(text="LIMIT = 5\ndef run(value):\n    return value\n")
            return Response(text="fixture")

    monkeypatch.setattr(gh.httpx, "AsyncClient", Client)
    result = run(gh.inspect_repository("acme", "project", "architecture", max_files=12))
    titles = [source["title"] for source in result["sources"]]
    assert result["files_selected"] == 5
    assert any("src/main.py" in title for title in titles)
    assert any("tests/test_main.py" in title for title in titles)
    assert any(".github/workflows/ci.yml" in title for title in titles)
    static = next(source for source in result["sources"] if source["source"] == "GitHub static analysis")
    assert "run(value)" in static["snippet"] and "LIMIT" in static["snippet"]


def test_deep_context_profile_applies_ollama_request_num_ctx(monkeypatch):
    from app.models_mgmt import ollama, runtime_policy

    monkeypatch.setattr(ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(ollama, "get_model_config", lambda model: {
        "num_ctx": 32768, "deep_research_num_ctx": 262144,
    })

    async def running(): return [{"name": "m"}]
    monkeypatch.setattr(ollama, "running_models", running)
    result = run(runtime_policy.prepare_deep_research_context("http://127.0.0.1:11434/v1", "m"))
    assert result["applied"] is True
    assert result["request_context_tokens"] == 262144
    assert result["previous_tokens"] == 32768 and result["changed"] is True


def test_deep_context_profile_unset_uses_model_normal_context(monkeypatch):
    from app.models_mgmt import ollama, runtime_policy

    monkeypatch.setattr(ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(ollama, "get_model_config", lambda model: {"num_ctx": 65536})
    result = run(runtime_policy.prepare_deep_research_context("http://127.0.0.1:11434/v1", "m"))
    assert result["applied"] is True and result["changed"] is False
    assert result["request_context_tokens"] == 65536
    assert "通常CTX" in result["reason"]


def test_deep_context_profile_restores_ollama_normal_options(monkeypatch):
    from app.models_mgmt import ollama, runtime_policy

    calls = []
    monkeypatch.setattr(ollama, "effective_options", lambda model: {"num_ctx": 32768})

    async def unload(model): calls.append(("unload", model))
    async def load(model, options=None): calls.append(("load", model, options))
    monkeypatch.setattr(ollama, "unload", unload)
    monkeypatch.setattr(ollama, "load", load)
    restored = run(runtime_policy.restore_deep_research_context({
        "changed": True, "runtime": "ollama", "model": "m", "was_loaded": True,
    }))
    assert restored["restored"] is True
    assert calls == [("unload", "m"), ("load", "m", {"num_ctx": 32768})]


def test_deep_context_profile_resizes_managed_llamacpp(monkeypatch):
    from app.models_mgmt import llama, ollama, runtime_policy

    changes: list[int] = []
    monkeypatch.setattr(ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(llama, "list_instances", lambda: [{
        "alias": "m", "port": 8080, "ctx_size": 32768,
        "deep_research_ctx_size": 262144, "loaded": True,
    }])
    monkeypatch.setattr(llama, "save_instance", lambda alias, patch: changes.append(patch["ctx_size"]))
    monkeypatch.setattr(llama, "start_instance", lambda alias: (True, ""))

    async def healthy(alias): return {"ok": True}
    monkeypatch.setattr(llama, "health", healthy)
    result = run(runtime_policy.prepare_deep_research_context("http://127.0.0.1:8080/v1", "m"))
    assert result["applied"] is True and result["runtime"] == "llama.cpp"
    assert changes == [262144]
    restored = run(runtime_policy.restore_deep_research_context(result))
    assert restored["restored"] is True
    assert changes == [262144, 32768]
