"""Patent 機能の検索・クレームチャート・AI 支援。

- 検索はナレッジ全体（一般・特許・規格）を横断する。答えを作るのはローカル LLM だけなので consumer=local。
- AI 支援は **ローカル専用（Host 管理の llama.cpp・GGUF）だけ** で動かす。検証できなければ止め、クラウドへ回さない
  （app.models_mgmt.ai_gateway.require_local_target。PatentBuddy の local-only 契約と同じ判定）。
- AI の答えは「モデルの推論・未確認」として保存し、根拠に使った文献の参照（doc_ref）を添える。
"""
from __future__ import annotations

import asyncio
import logging

from app.knowledge import store as kstore
from app.patents import domain
from app.patents import store as pstore

logger = logging.getLogger("control_deck.patents")



class LocalAIUnavailable(RuntimeError):
    pass


async def _query_vector(question: str):
    from app.knowledge import context

    vector, model = await context.query_vector(question)
    return ([vector] if vector is not None else None), model


async def search(question: str, *, limit: int = 20, mode: str = "hybrid", exclude_terms: tuple[str, ...] = (), expansion: dict | None = None) -> list[dict]:
    """文書を単位に探す（断片を文書ごとに集め、番号の完全一致・書誌の関係も出す）。
    設定でオンのとき（news.hub.features.expand_patent。既定オフ: 測定は docs/implementation-status.md）、ニュースハブの関係・概要で広げた語の OR の問いでも引いて融合する。
    広げた語は expansion["terms"] に入れて返す（画面に理由つきで出し、利用者が exclude_terms で外せる）。"""
    from app.knowledge import documents

    question = (question or "").strip()
    if not question:
        return []
    vector, model = (None, "")
    if mode in ("hybrid", "vector"):
        vecs, model = await _query_vector(question)
        vector = vecs[0] if vecs else None
    ranked = await asyncio.to_thread(documents.search_documents, question, consumer="local", query_vector=vector,
                                     model=model, mode=mode if vector is not None else "fulltext", limit=limit)
    ranked = await _widen(documents, question, ranked, limit, exclude_terms, expansion)
    out = []
    for doc in ranked:
        first = doc["hits"][0] if doc["hits"] else {"text": "", "locator": {}}
        out.append({"doc_ref": doc["ref"], "chunk_ref": doc["ref"], "collection": doc["collection"],
                    "title": doc["title"], "source": "", "url": "", "score": doc["score"],
                    "snippet": documents.snippet(first["text"], question), "text": first["text"],
                    "locator": first["locator"], "sensitivity": doc["sensitivity"], "relation": doc["relation"],
                    "via": doc.get("via", "")})
    return out


async def _widen(documents, question: str, ranked: list[dict], limit: int, exclude_terms: tuple[str, ...], expansion: dict | None) -> list[dict]:
    from app.knowledge import hub_expand

    if not await asyncio.to_thread(hub_expand.enabled, "patent"):
        return ranked
    ex = await hub_expand.aexpand(question, purpose="patent")
    skip = {t.casefold() for t in exclude_terms}
    terms = [t for t in ex["terms"] if t["term"].casefold() not in skip]
    if expansion is not None:
        expansion["terms"] = terms
    if not terms:
        return ranked
    extra = await asyncio.to_thread(documents.search_documents, hub_expand.or_query(terms), consumer="local", mode="fulltext", limit=limit, expand=False)
    return hub_expand.fuse_hits(ranked, [{**d, "via": d.get("via") or "hub_expand"} for d in extra], key="ref")[:limit]


def _hit(h: dict) -> dict:
    text = h["text"] or ""
    return {"doc_ref": h["doc_ref"], "chunk_ref": h["chunk_ref"], "collection": h["collection"],
            "title": h["title"] or h["source"], "source": h["source"], "url": h["url"], "score": h["score"],
            "snippet": text[:400], "text": text, "locator": h["locator"], "sensitivity": h["sensitivity"]}


async def _passages(question: str, limit: int) -> list[dict]:
    """クレームチャート用: 構成要件ごとの断片（文書をまたいで上位）。"""
    vecs, model = await _query_vector(question)
    vector = vecs[0] if vecs else None
    hits = await asyncio.to_thread(kstore.search_all, question, consumer="local", query_vector=vector, model=model,
                                   mode="hybrid" if vector is not None else "fulltext", limit=limit)
    return [_hit(h) for h in hits]


async def claim_chart(case_id: int, claim_no: int = 1, per_element: int = 3) -> dict:
    """請求項 N の構成要件ごとに、ナレッジから当たりそうな箇所を探して並べる（AI を使わない）。"""
    case = await asyncio.to_thread(pstore.get_case, case_id)
    claim = next((c for c in case["claim_elements"] if c["no"] == int(claim_no)), None)
    if claim is None:
        raise pstore.PatentError(f"請求項{claim_no}がありません。先に請求項を保存してください")
    rows = []
    for element in claim["elements"]:
        if element["label"] in ("前提", "結び"):
            continue
        query = " ".join(domain.keywords(element["text"])[:6]) or element["text"][:40]
        hits = await _passages(query, per_element)
        rows.append({"element": element, "query": query,
                     "evidence": [{k: h[k] for k in ("doc_ref", "chunk_ref", "title", "collection", "snippet", "locator")}
                                  for h in hits]})
    return {"claim_no": int(claim_no), "claim": claim["text"], "rows": rows}


def build_messages(case: dict, task: str, evidence_text: str, extra: str = "") -> list[dict]:
    data = case["data"]
    claims = case["claims"][0]["text"] if case["claims"] else data.get("claims_draft", "")
    refs = "\n".join(f"- [{r['category']}] {r['ref'] or r['doc_ref']} {r['relevance']}" for r in case["refs"])
    context = domain.PROJECT_TEMPLATE.format(
        title=case["title"], stage=case["stage_label"], field=data.get("field", ""), problem=data.get("problem", ""),
        solution=data.get("solution", ""), effect=data.get("effect", ""), embodiment=data.get("embodiment", ""),
        claims=claims or "(未作成)", refs=refs or "(なし)")
    user = domain.PROMPTS[task]["prompt"] + "\n\n" + context
    hearing = str(data.get("hearing", "")).strip()
    if hearing and task in ("hearing", "interview", "idea", "claim_draft"):
        user += "\n\n# ヒアリングの記録\n" + hearing[-60_000:]
    if evidence_text:
        user += "\n\n# 参照文献（ナレッジ。引用は [D番号] で示す）\n" + evidence_text
    if extra:
        user += "\n\n# 追加指示\n" + extra[:20_000]
    system = domain.SYSTEM_PROMPT + "参照文献に書かれていないことを、文献に書かれた事実として述べないでください。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _law_and_classes(task: str, query_text: str) -> str:
    """審査基準・法令（種類 law）から最大 4 件を [基準n] で、分類辞書（種類 classification）から候補を足す（RDH v0.2 と同じ）。"""
    from app.knowledge import documents

    query = " ".join(domain.keywords(domain.PROMPTS[task]["label"] + " " + query_text)[:10]) or query_text[:80]
    out = ""
    laws = await asyncio.to_thread(kstore.collections_of_kind, "law")
    if laws and task in domain.LAW_TASKS:
        hits = await asyncio.to_thread(kstore.search, laws, query, mode="fulltext", limit=4)
        if hits:
            out += "\n\n# 審査基準・法令（ナレッジ。引用は [基準n]）\n" + "\n\n".join(
                f"[基準{i}] {h['title']}\n{(h['parent'] or h['text'])[:900]}" for i, h in enumerate(hits, 1))
    classes = await asyncio.to_thread(kstore.collections_of_kind, "classification")
    if classes and task in ("search_plan", "novelty", "idea", "hearing"):
        hits = await asyncio.to_thread(kstore.search, classes, query, mode="fulltext", limit=15)
        if hits:
            out += "\n\n# 分類候補（分類辞書）\n" + "\n".join(f"- {h['text'][:160]}" for h in hits)
    return out


async def resolve_local_target():
    from app.models_mgmt.ai_gateway import AITargetUnavailable, require_local_target, resolve_ai_target

    try:
        target = await resolve_ai_target("text.generate")
        await asyncio.to_thread(require_local_target, target)
    except (AITargetUnavailable, OSError) as exc:
        raise LocalAIUnavailable(
            "ローカル専用のモデル（ControlDeck が管理する llama.cpp の GGUF）が使えません。"
            "案件の内容は外部へ送らないため、Models でローカルのモデルを選んでから実行してください") from exc
    return target


class _JobRequest:
    """バックグラウンドの job で GPU の順番待ちをするための代わり。利用者が job を止めたら切断とみなす。"""

    def __init__(self, job) -> None:
        self._job = job

    async def is_disconnected(self) -> bool:
        return getattr(self._job, "status", "") == "canceled"


async def run_assist(job, case_id: int, task: str, assist_id: int, extra: str = "") -> dict:
    from app.addon_runtime.ai import _complete_with_host_admission
    from app.models_mgmt.runtime_provider import RuntimeChatRequest

    pstore.finish_assist(assist_id, status="running", job_id=job.id)
    try:
        job.set_progress("ローカル専用のモデルを確認中", 0, 3)
        target = await resolve_local_target()
        case = await asyncio.to_thread(pstore.get_case, case_id)
        from app.knowledge import context as kcontext
        from app.models_mgmt import context_budget

        refs: list[dict] = []
        evidence_text = ""
        if task not in ("interview", "filing_check", "hearing"):
            job.set_progress("ナレッジから文献を探しています", 1, 3)
            claims = case["claims"][0]["text"] if case["claims"] else case["data"].get("claims_draft", "")
            query_text = " ".join(filter(None, [case["title"], case["data"].get("problem", ""),
                                                case["data"].get("solution", ""), claims[:400]]))
            budget = context_budget.for_model(target.base_url, target.model).exhaustive_evidence_chars
            built, _ = await kcontext.search_and_build(" ".join(domain.keywords(query_text)[:10]) or case["title"],
                                                       consumer="local", budget_chars=budget, max_docs=6)
            evidence_text = built.text
            refs = [{"doc_ref": s["ref"], "chunk_ref": s["ref"], "title": s["title"], "collection": s["collection"],
                     "label": s["label"], "mode": built.mode} for s in built.sources]
        evidence_text += await _law_and_classes(task, query_text if task not in ("interview", "filing_check", "hearing") else case["title"])
        messages = build_messages(case, task, evidence_text, extra)
        job.set_progress("ローカルのモデルが回答を作成中", 2, 3)
        request = RuntimeChatRequest(base_url=target.base_url, model=target.model, messages=messages,
                                     temperature=0.3, max_tokens=4096, thinking=False, disable_thinking=True,
                                     timeout_seconds=900, local_only=True)
        answer, _ = await _complete_with_host_admission(target, request, _JobRequest(job))
        pstore.finish_assist(assist_id, status="succeeded", answer=answer or "", evidence=refs)
        job.set_progress("完了", 3, 3)
        return {"assist_id": assist_id, "chars": len(answer or "")}
    except asyncio.CancelledError:
        pstore.finish_assist(assist_id, status="canceled")
        raise
    except LocalAIUnavailable as exc:
        pstore.finish_assist(assist_id, status="failed", error=str(exc))
        raise
    except Exception as exc:  # 失敗を記録してから job を失敗にする（握り潰さない）
        logger.exception("特許の AI 支援に失敗しました")
        pstore.finish_assist(assist_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
        raise
