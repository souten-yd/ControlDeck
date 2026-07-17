"""反復型Deep Researchエンジン。検索/LLM/provider実装はcallbackで注入する。"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

Complete = Callable[..., Awaitable[str]]
Search = Callable[[str, int], Awaitable[list[dict]]]
SpecializedSearch = Callable[[str, str, int], Awaitable[list[dict]]]
Fetch = Callable[[str, int], Awaitable[str]]
Progress = Callable[[str, str, int, dict[str, Any]], None]


class DeepPlan(BaseModel):
    objective: str = Field(min_length=1, max_length=500)
    sub_questions: list[str] = Field(min_length=2, max_length=8)
    search_queries: list[str] = Field(min_length=2, max_length=8)
    evaluation_criteria: list[str] = Field(default_factory=list, max_length=8)
    source_types: list[str] = Field(default_factory=lambda: ["web", "academic"], max_length=6)


class DeepAssessment(BaseModel):
    sufficient: bool = False
    coverage_score: int = Field(default=0, ge=0, le=100)
    gaps: list[str] = Field(default_factory=list, max_length=6)
    contradictions: list[str] = Field(default_factory=list, max_length=6)
    next_queries: list[str] = Field(default_factory=list, max_length=6)


PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["objective", "sub_questions", "search_queries", "evaluation_criteria", "source_types"],
    "properties": {
        "objective": {"type": "string", "maxLength": 500},
        "sub_questions": {"type": "array", "minItems": 2, "maxItems": 8, "items": {"type": "string", "maxLength": 500}},
        "search_queries": {"type": "array", "minItems": 2, "maxItems": 8, "items": {"type": "string", "maxLength": 500}},
        "evaluation_criteria": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 300}},
        "source_types": {
            "type": "array", "minItems": 1, "maxItems": 6, "uniqueItems": True,
            "items": {"type": "string", "enum": ["web", "academic", "github", "patent", "market", "direct"]},
        },
    },
}

ASSESSMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["sufficient", "coverage_score", "gaps", "contradictions", "next_queries"],
    "properties": {
        "sufficient": {"type": "boolean"},
        "coverage_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "gaps": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 300}},
        "contradictions": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 300}},
        "next_queries": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 500}},
    },
}


def _json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("有効なJSON objectがありません")


def _fallback_plan(query: str) -> DeepPlan:
    return DeepPlan(
        objective=query,
        sub_questions=[
            f"{query}の現状と主要な仕組みは何か",
            f"{query}を裏付ける一次資料・実装・データは何か",
            f"{query}の比較対象、限界、反証は何か",
            f"{query}から実現可能な統合や次の判断は何か",
        ],
        search_queries=[query, f"{query} 仕組み 実装", f"{query} 比較 課題", f"{query} 論文 systematic review"],
        evaluation_criteria=["一次情報", "複数観点", "反証・限界", "実装またはデータの根拠"],
        source_types=["web", "academic", "github"],
    )


async def _make_plan(query: str, complete: Complete) -> DeepPlan:
    prompt = (
        "複雑な依頼を深く調査する計画を作ってください。単なる言い換えではなく、事実、仕組み、比較、"
        "限界・反証、実装や構造、統合可能性を検証できる問いへ分解します。GitHub URLやコード調査を含む場合は、"
        "repository構造、関数・変数、依存関係、テスト、CI、統合点も対象にします。"
        "source_typesは必要に応じweb/academic/github/patent/market/directから選びます。"
        "技術・科学はacademic、発明・競合技術はpatent、企業動向はmarketを積極的に併用します。指定JSONだけを返してください。"
    )
    try:
        raw = await complete(
            [{"role": "system", "content": prompt}, {"role": "user", "content": query}],
            max_tokens=1400,
            response_format={"type": "json_schema", "name": "deep_research_plan", "schema": PLAN_SCHEMA, "strict": True},
        )
        return DeepPlan.model_validate(_json_object(raw))
    except Exception:
        return _fallback_plan(query)


def _canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))
    except ValueError:
        return url.strip()


def _source_key(source: dict) -> str:
    provider = str(source.get("source") or "").casefold()
    if provider.startswith("github"):
        return f"github:{str(source.get('title') or '').strip().casefold()}"
    url = _canonical_url(str(source.get("url") or ""))
    return url or re.sub(r"\W+", "", str(source.get("title") or "").casefold())[:200]


def _normalize_source(item: dict, provider: str, query: str) -> dict:
    actual_provider = str(item.get("source") or provider)
    return {
        "title": str(item.get("title") or item.get("url") or "無題")[:500],
        "url": str(item.get("url") or "")[:2048],
        "source": actual_provider[:128],
        "kind": str(item.get("kind") or ("paper" if provider == "academic" else "page")),
        "snippet": str(item.get("snippet") or item.get("abstract") or item.get("text") or "")[:12_000],
        "query": query[:500],
        "meta": item.get("meta") if isinstance(item.get("meta"), dict) else {},
    }


def _evidence_summary(sources: list[dict], limit_chars: int = 24_000) -> str:
    chunks: list[str] = []
    used = 0
    for index, source in enumerate(sources, 1):
        chunk = (
            f"[{index}] ({source.get('source','')}) {source.get('title','')}\n"
            f"query: {source.get('query','')}\n{str(source.get('snippet') or '')[:700]}"
        )
        if used + len(chunk) > limit_chars:
            break
        chunks.append(chunk)
        used += len(chunk)
    return "\n\n".join(chunks)


async def _assess(
    query: str, plan: DeepPlan, sources: list[dict], round_number: int, complete: Complete,
) -> DeepAssessment:
    assessment_sources = _select_evidence(sources, max_sources=32)
    prompt = (
        f"調査依頼:\n{query}\n\nサブ質問:\n- " + "\n- ".join(plan.sub_questions) +
        f"\n\n第{round_number}ラウンドまでの代表根拠:\n" + _evidence_summary(assessment_sources) +
        "\n\n各サブ質問が複数の独立した根拠で覆われているか、一次情報、反証、最新性、コード構造の観点で評価してください。"
        "不足を埋める重複しない次の検索語を返します。資料数だけで十分と判断しないでください。指定JSONだけを返してください。"
    )
    try:
        raw = await complete(
            [{"role": "system", "content": "あなたは厳格な調査coverage評価器です。"}, {"role": "user", "content": prompt}],
            max_tokens=1200,
            response_format={"type": "json_schema", "name": "deep_research_assessment", "schema": ASSESSMENT_SCHEMA, "strict": True},
        )
        return DeepAssessment.model_validate(_json_object(raw))
    except Exception:
        enough = len(sources) >= 18 and round_number >= 2
        return DeepAssessment(
            sufficient=enough,
            coverage_score=min(90, 25 + len(sources) * 3),
            gaps=[] if enough else ["構造化coverage評価を利用できないため追加探索"],
            next_queries=[] if enough else [f"{query} limitations evidence", f"{query} implementation architecture"],
        )


def _select_evidence(sources: list[dict], max_sources: int = 36) -> list[dict]:
    selected: list[dict] = []
    domain_counts: dict[str, int] = {}
    # 本文・コード・一次的providerを優先し、同一domainの検索結果だけで埋まることを防ぐ。
    ranked = sorted(
        sources,
        key=lambda source: (
            0 if str(source.get("source") or "").startswith("GitHub") else 1,
            -len(str(source.get("snippet") or "")),
        ),
    )
    for source in ranked:
        hostname = urlsplit(str(source.get("url") or "")).hostname or "no-domain"
        is_github_analysis = str(source.get("source") or "").startswith("GitHub")
        cap = 16 if is_github_analysis else 4
        if domain_counts.get(hostname, 0) >= cap:
            continue
        selected.append(source)
        domain_counts[hostname] = domain_counts.get(hostname, 0) + 1
        if len(selected) >= max_sources:
            break
    return selected


def _build_corpus(sources: list[dict], max_chars: int = 90_000) -> str:
    chunks: list[str] = []
    used = 0
    for index, source in enumerate(sources, 1):
        excerpt = str(source.get("snippet") or "")[:6000]
        chunk = (
            f"[{index}] ({source.get('source','')}; {source.get('kind','')}) {source.get('title','')}\n"
            f"URL: {source.get('url','')}\n取得クエリ: {source.get('query','')}\n{excerpt}"
        )
        remaining = max_chars - used
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        used += min(len(chunk), remaining)
    return "\n\n---\n\n".join(chunks)


def citation_metrics(report: str, source_count: int) -> dict:
    citations = [int(value) for value in re.findall(r"\[(\d{1,3})\]", report)]
    valid = [value for value in citations if 1 <= value <= source_count]
    invalid = sorted({value for value in citations if value < 1 or value > source_count})
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", report) if len(part.strip()) >= 80]
    cited_paragraphs = sum(bool(re.search(r"\[\d{1,3}\]", part)) for part in paragraphs)
    coverage = cited_paragraphs / len(paragraphs) if paragraphs else 0.0
    return {
        "citation_count": len(valid), "cited_sources": len(set(valid)), "invalid_citations": invalid,
        "citation_coverage": round(coverage, 3), "report_chars": len(report),
    }


SECTION_COMPLETE = "<!-- CONTROLDECK_SECTION_COMPLETE -->"


def _merge_continuation(previous: str, continuation: str) -> str:
    """継続生成が末尾を繰り返した場合、最大400文字の重複を除いて結合する。"""
    next_text = continuation.replace(SECTION_COMPLETE, "").strip()
    base = previous.rstrip()
    max_overlap = min(400, len(base), len(next_text))
    for size in range(max_overlap, 19, -1):
        if base[-size:] == next_text[:size]:
            return base + next_text[size:]
    return base + "\n\n" + next_text


async def _synthesize(
    query: str, plan: DeepPlan, sources: list[dict], assessment: DeepAssessment,
    coverage_limits: list[str], complete: Complete, *, max_context_chars: int = 90_000,
    max_report_tokens: int = 32_768,
) -> tuple[str, dict]:
    corpus = _build_corpus(sources, max_chars=max_context_chars)
    system = (
        "あなたはDeep Researchアナリストです。与えられた根拠だけを使い、日本語の監査可能な長文レポートを作成します。"
        "事実主張には必ず[番号]を付け、異なる資料を相互確認します。検索結果一覧の紹介で終わらず、因果、構造、比較、"
        "矛盾、限界を分析してください。コード対象では関数・変数・データフロー・依存・API境界・テスト・CIを評価し、"
        "既存構成から実現可能な統合機能、接続点、制約、追加実装を具体化します。静的観測と推論を区別し、"
        "根拠のない実装詳細は断定しません。末尾の出典一覧はUIが生成するため不要です。"
    )
    common_user = (
        f"調査依頼: {query}\n\n目的: {plan.objective}\n\nサブ質問:\n- " + "\n- ".join(plan.sub_questions) +
        "\n\n最終coverage評価:\n" + json.dumps(assessment.model_dump(), ensure_ascii=False) +
        "\n\n取得上の制限:\n- " + ("\n- ".join(coverage_limits) if coverage_limits else "特記事項なし") +
        "\n\n根拠:\n" + corpus
    )
    section_specs = [
        ("エグゼクティブサマリー", "重要な結論、判断根拠、利用者への影響を先にまとめる"),
        ("調査範囲と方法", "検索範囲、資料種別、評価方法、取得限界を監査可能に示す"),
        ("主要分析", "サブ質問を漏れなく横断し、複数資料を比較・反証する"),
        ("構造・機能・統合評価", "コード、関数、変数、データフロー、依存、テスト、統合可能性を詳述する"),
        ("矛盾と不確実性", "資料間の食い違い、source freshness、未確認事項、推論を区別する"),
        ("結論と次の行動", "優先順位付きの結論、実施案、検証方法を具体化する"),
    ]
    sections: list[str] = []
    incomplete: list[str] = []
    requested_tokens = 0
    section_budget = max_report_tokens // len(section_specs)
    for index, (title, instruction) in enumerate(section_specs):
        # 総予算を6章へ均等配分する。一章だけが使い切って後半章を欠落させない。
        # 128K設定では一章あたり約21Kまで継続できる。
        initial_tokens = min(4096, max(1024, section_budget - min(4096, section_budget // 3)))
        prompt = (
            f"{common_user}\n\nこの応答では「## {title}」章だけを書いてください。{instruction}。"
            "他章へ進まず、章を省略・途中終了しないでください。完結した末尾に必ず " + SECTION_COMPLETE + " を付けます。"
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        text = await complete(messages, max_tokens=initial_tokens)
        requested_tokens += initial_tokens
        section_used = initial_tokens
        complete_marker = SECTION_COMPLETE in text
        merged = text.replace(SECTION_COMPLETE, "").strip()
        continuation_count = 0
        while not complete_marker and continuation_count < 8:
            available = section_budget - section_used
            if available < 512:
                break
            continuation_tokens = min(4096, available)
            followup = (
                "直前の章が出力上限で途切れました。新しい見出しや前置きを付けず、最後の文の続きから同じ章だけを完結させ、"
                f"末尾に{SECTION_COMPLETE}を付けてください。"
            )
            continuation = await complete(
                messages + [{"role": "assistant", "content": merged}, {"role": "user", "content": followup}],
                max_tokens=continuation_tokens,
            )
            requested_tokens += continuation_tokens
            section_used += continuation_tokens
            complete_marker = SECTION_COMPLETE in continuation
            merged = _merge_continuation(merged, continuation)
            continuation_count += 1
        if not complete_marker:
            incomplete.append(title)
        sections.append(merged)
    report = "\n\n".join(sections).strip()
    metrics = citation_metrics(report, len(sources))
    section_metrics = {
        "section_count": len(sections), "completed_sections": len(sections) - len(incomplete),
        "possibly_truncated_sections": incomplete, "requested_token_budget": requested_tokens,
    }
    metrics.update(section_metrics)
    min_diversity = min(6, len(sources))
    if metrics["invalid_citations"] or metrics["citation_coverage"] < 0.55 or metrics["cited_sources"] < min_diversity:
        revision = (
            "次の草稿は引用検証に不合格です。根拠にない内容を削除し、各事実段落へ有効な[番号]を付け、"
            f"少なくとも{min_diversity}件の異なる根拠を使って全面改稿してください。存在しない番号は禁止です。\n\n"
            f"草稿:\n{report}\n\n根拠:\n{corpus}"
        )
        try:
            revised = await complete([{"role": "system", "content": system}, {"role": "user", "content": revision}], max_tokens=8192)
            revised_metrics = citation_metrics(revised, len(sources))
            if len(revised) >= int(len(report) * 0.85) and not revised_metrics["invalid_citations"] and (
                revised_metrics["citation_coverage"], revised_metrics["cited_sources"]
            ) >= (metrics["citation_coverage"], metrics["cited_sources"]):
                report, metrics = revised, revised_metrics
                metrics.update(section_metrics)
                metrics["revised"] = True
        except Exception:
            pass
    metrics.setdefault("revised", False)
    return report, metrics


async def run_deep_research(
    query: str, *, complete: Complete, web_search: Search, academic_search: Search,
    page_fetch: Fetch, specialized_search: SpecializedSearch | None = None,
    progress: Progress | None = None,
    max_rounds: int = 4, max_search_calls: int = 24,
    max_evidence_chars: int = 90_000, max_report_tokens: int = 32_768,
) -> dict:
    def emit(phase: str, label: str, round_number: int = 0, **details: Any) -> None:
        if progress:
            progress(phase, label, round_number, details)

    emit("plan", "調査計画を作成中")
    plan = await _make_plan(query, complete)
    emit("plan_ready", f"{len(plan.sub_questions)}個の検証項目を計画", details=plan.model_dump())
    pending = list(plan.search_queries)
    seen_queries: set[str] = set()
    source_by_key: dict[str, dict] = {}
    fetched_urls: set[str] = set()
    inspected_repositories: set[tuple[str, str]] = set()
    coverage_limits: list[str] = []
    search_calls = 0
    rounds = 0
    assessment = DeepAssessment()

    url_re = re.compile(r"https?://[^\s<>()\]\[\"']+")
    for url in url_re.findall(query):
        source = _normalize_source({"title": url, "url": url}, "direct URL", query)
        source_by_key[_source_key(source)] = source

    async def safe_search(callback: Search, search_query: str, limit: int, provider: str) -> list[dict]:
        try:
            return [_normalize_source(item, provider, search_query) for item in await callback(search_query, limit)]
        except Exception as exc:
            coverage_limits.append(f"{provider}検索「{search_query[:80]}」: {type(exc).__name__}")
            return []

    for round_number in range(1, max(2, min(max_rounds, 6)) + 1):
        queries = []
        for candidate in pending:
            normalized = " ".join(candidate.split()).casefold()
            if normalized and normalized not in seen_queries:
                seen_queries.add(normalized)
                queries.append(candidate[:500])
            if len(queries) >= 6:
                break
        if not queries:
            break
        rounds = round_number
        emit("round", f"探索ラウンド {round_number}/{max_rounds}", round_number, queries=queries)
        tasks: list[Awaitable[list[dict]]] = []
        for search_query in queries:
            if search_calls >= max_search_calls:
                break
            tasks.append(safe_search(web_search, search_query, 8, "web"))
            search_calls += 1
        if "academic" in plan.source_types:
            for search_query in queries[:3]:
                if search_calls >= max_search_calls:
                    break
                tasks.append(safe_search(academic_search, search_query, 4, "academic"))
                search_calls += 1
        if specialized_search is not None:
            for source_type in ("patent", "market"):
                if source_type not in plan.source_types:
                    continue
                for search_query in queries[:2]:
                    if search_calls >= max_search_calls:
                        break
                    async def search_specialized(q: str, limit: int, kind: str = source_type) -> list[dict]:
                        return await specialized_search(kind, q, limit)
                    tasks.append(safe_search(search_specialized, search_query, 5, source_type))
                    search_calls += 1
        emit("search", f"{len(tasks)}件の検索を並列実行", round_number, search_calls=search_calls)
        batches = await asyncio.gather(*tasks)
        new_count = 0
        for source in (item for batch in batches for item in batch):
            key = _source_key(source)
            if key and key not in source_by_key and len(source_by_key) < 120:
                source_by_key[key] = source
                new_count += 1

        # GitHub repositoryはmetadata/tree/主要コード/静的symbol索引まで展開する。
        from app.workflows.github_research import extract_repositories, inspect_repository

        repo_inputs = [query] + [f"{source.get('url','')} {source.get('title','')}" for source in source_by_key.values()]
        # 既に調査済みのURLが入力先頭に残っていても、後続roundで発見した候補を
        # 上限より手前で切り捨てない。抽出後に既調査分を除外し、残枠だけを使う。
        repository_slots = max(0, 3 - len(inspected_repositories))
        repos = [
            repo for repo in extract_repositories(repo_inputs, 12)
            if repo not in inspected_repositories
        ][:repository_slots]
        if repos:
            emit("github", f"GitHubリポジトリ {len(repos)}件の構造を解析", round_number)
        repo_results = await asyncio.gather(*(inspect_repository(owner, repo, query) for owner, repo in repos))
        for repo, result in zip(repos, repo_results):
            inspected_repositories.add(repo)
            coverage_limits.extend(str(error) for error in result.get("errors", []))
            for item in result.get("sources", []):
                source = _normalize_source(item, str(item.get("source") or "GitHub"), query)
                source["canonical_id"] = f"github:{repo[0].casefold()}/{repo[1].casefold()}:{source['title'].casefold()}"
                source_by_key[_source_key(source)] = source

        fetch_candidates = [
            source for source in source_by_key.values()
            if source.get("url") and source["url"] not in fetched_urls
            and "github.com" not in str(source["url"]).casefold()
            and not str(source.get("source") or "").startswith("academic")
        ][:8]
        if fetch_candidates:
            emit("fetch", f"{len(fetch_candidates)}ページの本文を取得", round_number)
        texts = await asyncio.gather(*(page_fetch(str(source["url"]), 6000) for source in fetch_candidates))
        for source, text in zip(fetch_candidates, texts):
            fetched_urls.add(str(source["url"]))
            if text and len(text) > len(str(source.get("snippet") or "")):
                source["snippet"] = text[:12_000]

        current_sources = list(source_by_key.values())
        emit("assess", f"{len(current_sources)}件の根拠coverageを評価", round_number, new_sources=new_count)
        assessment = await _assess(query, plan, current_sources, round_number, complete)
        emit(
            "coverage", f"coverage {assessment.coverage_score}% · 未解決 {len(assessment.gaps)}件",
            round_number, assessment=assessment.model_dump(), sources=len(current_sources),
        )
        if round_number >= 2 and len(current_sources) >= 12 and assessment.sufficient:
            break
        pending = assessment.next_queries
        if not pending and round_number < 2:
            pending = [f"{query} limitations counter evidence", f"{query} implementation architecture integration"]
        if search_calls >= max_search_calls:
            coverage_limits.append(f"検索呼び出し上限 {max_search_calls}件へ到達")
            break

    if not source_by_key:
        raise RuntimeError("Deep Researchで根拠を取得できませんでした")
    selected = _select_evidence(list(source_by_key.values()))
    emit("synthesize", f"{len(selected)}件の根拠からレポートを統合", rounds)
    report, metrics = await _synthesize(
        query, plan, selected, assessment, coverage_limits, complete,
        max_context_chars=max_evidence_chars, max_report_tokens=max_report_tokens,
    )
    emit("verify", f"引用coverage {metrics['citation_coverage'] * 100:.0f}%", rounds, metrics=metrics)
    return {
        "mode": "deep", "report": report, "sources": selected,
        "sub_questions": plan.sub_questions,
        "research": {
            "rounds": rounds, "search_calls": search_calls, "sources_discovered": len(source_by_key),
            "sources_selected": len(selected), "repositories_inspected": len(inspected_repositories),
            "coverage": assessment.model_dump(), "citation_metrics": metrics,
            "coverage_limits": coverage_limits[:30], "plan": plan.model_dump(),
        },
    }
