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

MAX_EVIDENCE_CHARS = 900


class LocalAIUnavailable(RuntimeError):
    pass


async def _query_vector(question: str):
    """埋め込みが使えれば質問のベクトル。使えなければ None（全文検索だけにする）。"""
    from app.workflows import rag

    cfg = rag._merged({})
    try:
        return (await rag.embed([question], cfg["embed_base_url"], cfg["embed_model"], "")), cfg["embed_model"]
    except Exception:  # 埋め込みが止まっていても特許の検索は全文で続ける
        logger.info("埋め込みが使えないため全文検索だけで探します")
        return None, ""


async def search(question: str, *, limit: int = 20, mode: str = "hybrid") -> list[dict]:
    question = (question or "").strip()
    if not question:
        return []
    vector, model = (None, "")
    if mode in ("hybrid", "vector"):
        vecs, model = await _query_vector(question)
        vector = vecs[0] if vecs else None
    use_mode = mode if vector is not None else "fulltext"
    hits = await asyncio.to_thread(kstore.search_all, question, consumer="local", query_vector=vector, model=model,
                                   mode=use_mode, limit=limit)
    return [_hit(h) for h in hits]


def _hit(h: dict) -> dict:
    text = h["text"] or ""
    return {"doc_ref": h["doc_ref"], "chunk_ref": h["chunk_ref"], "collection": h["collection"],
            "title": h["title"] or h["source"], "source": h["source"], "url": h["url"], "score": h["score"],
            "snippet": text[:400], "text": text, "locator": h["locator"], "sensitivity": h["sensitivity"]}


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
        hits = await search(query, limit=per_element)
        rows.append({"element": element, "query": query,
                     "evidence": [{k: h[k] for k in ("doc_ref", "chunk_ref", "title", "collection", "snippet", "locator")}
                                  for h in hits]})
    return {"claim_no": int(claim_no), "claim": claim["text"], "rows": rows}


def build_messages(case: dict, task: str, evidence: list[dict], extra: str = "") -> list[dict]:
    data = case["data"]
    claims = case["claims"][0]["text"] if case["claims"] else data.get("claims_draft", "")
    refs = "\n".join(f"- [{r['category']}] {r['ref'] or r['doc_ref']} {r['relevance']}" for r in case["refs"])
    context = domain.PROJECT_TEMPLATE.format(
        title=case["title"], stage=case["stage_label"], field=data.get("field", ""), problem=data.get("problem", ""),
        solution=data.get("solution", ""), effect=data.get("effect", ""), embodiment=data.get("embodiment", ""),
        claims=claims or "(未作成)", refs=refs or "(なし)")
    ev_text = "\n\n".join(f"[{i + 1}] {e['title']}（{e['collection']} {e['doc_ref']}）\n{e['text'][:MAX_EVIDENCE_CHARS]}"
                          for i, e in enumerate(evidence))
    user = domain.PROMPTS[task]["prompt"] + "\n\n" + context
    if ev_text:
        user += "\n\n# 参照文献（ナレッジの検索結果。引用は [番号] で示す）\n" + ev_text
    if extra:
        user += "\n\n# 追加指示\n" + extra[:20_000]
    system = domain.SYSTEM_PROMPT + "参照文献に書かれていないことを、文献に書かれた事実として述べないでください。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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
        evidence: list[dict] = []
        if task not in ("interview", "filing_check"):
            job.set_progress("ナレッジから文献を探しています", 1, 3)
            claims = case["claims"][0]["text"] if case["claims"] else case["data"].get("claims_draft", "")
            query_text = " ".join(filter(None, [case["title"], case["data"].get("problem", ""),
                                                case["data"].get("solution", ""), claims[:400]]))
            evidence = await search(" ".join(domain.keywords(query_text)[:10]) or case["title"], limit=8)
        messages = build_messages(case, task, evidence, extra)
        job.set_progress("ローカルのモデルが回答を作成中", 2, 3)
        request = RuntimeChatRequest(base_url=target.base_url, model=target.model, messages=messages,
                                     temperature=0.3, max_tokens=4096, thinking=False, disable_thinking=True,
                                     timeout_seconds=900, local_only=True)
        answer, _ = await _complete_with_host_admission(target, request, _JobRequest(job))
        refs = [{k: e[k] for k in ("doc_ref", "chunk_ref", "title", "collection")} for e in evidence]
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
