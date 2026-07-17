"""AIアシスタントのハイブリッド判定と複合調査計画。"""
from __future__ import annotations

import json
import logging
import re

from app.schemas.assistant import AssistantPlan, ResearchEvaluation, ResearchStep
from app.workflows.chat_router import _llm

logger = logging.getLogger(__name__)


PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mode", "reason", "steps", "max_iterations"],
    "properties": {
        "mode": {"type": "string", "enum": ["chat", "web", "academic", "research"]},
        "reason": {"type": "string", "maxLength": 200},
        "steps": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["tool", "query"],
                "properties": {
                    "tool": {"type": "string", "enum": ["web", "academic"]},
                    "query": {"type": "string", "maxLength": 500},
                },
            },
        },
        "max_iterations": {"type": "integer", "minimum": 1, "maximum": 5},
    },
}

EVALUATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["sufficient", "reason", "next_steps"],
    "properties": {
        "sufficient": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
        "next_steps": {
            "type": "array", "maxItems": 3,
            "items": PLAN_SCHEMA["properties"]["steps"]["items"],
        },
    },
}


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def rule_plan(query: str) -> AssistantPlan | None:
    """確度の高い入力だけを即時判定し、曖昧ならNoneを返す。"""
    text = query.strip().lower()
    if not text:
        return AssistantPlan(mode="chat", reason="通常の対話", decided_by="rule")
    academic = _contains(text, ("論文", "arxiv", "openalex", "crossref", "学術", "先行研究", "査読"))
    current = _contains(text, ("最新", "現在", "今日", "ニュース", "web検索", "ウェブ検索", "価格", "天気"))
    combined = _contains(text, ("組み合わせ", "横断", "両方", "複数ソース", "比較調査"))
    if _contains(text, ("deep research", "deepサーチ", "ディープリサーチ", "徹底的に調査", "調査レポート")):
        return AssistantPlan(mode="deep", reason="複数ソースを使う詳細調査", decided_by="rule")
    if academic and (current or combined):
        return AssistantPlan(
            mode="research", reason="Webと学術情報を組み合わせる依頼",
            steps=[ResearchStep(tool="web", query=query), ResearchStep(tool="academic", query=query)],
            decided_by="rule",
        )
    if academic:
        return AssistantPlan(mode="academic", reason="学術情報の明示的な検索", decided_by="rule")
    if current:
        return AssistantPlan(mode="web", reason="現在のWeb情報が必要", decided_by="rule")
    if re.fullmatch(r"[\s\W]*(こんにちは|こんばんは|おはよう|やあ|ありがとう|hello|hi)[\s\W]*", text):
        return AssistantPlan(mode="chat", reason="挨拶・通常の対話", decided_by="rule")
    return None


def _json_object(text: str) -> dict:
    """Markdown fence等を含む応答から最初の有効なJSON objectだけを抽出する。"""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("LLM応答に有効なJSON objectがありません")


async def decide(query: str, base_url: str, model: str, api_key: str = "") -> AssistantPlan:
    immediate = rule_plan(query)
    if immediate is not None:
        return immediate
    system = (
        "あなたはControl Deckの処理ルーターです。利用者の依頼を分類してください。"
        "単なる説明・相談はchat、現在の公開情報が必要ならweb、論文中心ならacademic、"
        "Webと学術の併用や複数観点の調査ならresearchです。researchだけ具体的な検索手順をstepsへ入れ、"
        "通常は最大3回、特に広い調査だけ4〜5回にします。指定JSON以外は出力しません。"
    )
    try:
        raw = await _llm(
            [{"role": "system", "content": system}, {"role": "user", "content": query}],
            base_url, model, api_key, temperature=0, max_tokens=768, disable_thinking=True,
            response_format={"type": "json_schema", "name": "assistant_plan", "schema": PLAN_SCHEMA, "strict": True},
        )
        plan = AssistantPlan.model_validate(_json_object(raw))
        plan.decided_by = "llm"
        if plan.mode == "research" and not plan.steps:
            plan.steps = [ResearchStep(tool="web", query=query), ResearchStep(tool="academic", query=query)]
        if plan.mode != "research":
            plan.steps = []
        return plan
    except Exception as exc:
        logger.warning("AIアシスタントのLLM判定をruleへfallback: %s", type(exc).__name__)
        return AssistantPlan(mode="chat", reason="LLM判定を利用できないため通常対話へフォールバック", decided_by="fallback")


async def evaluate(
    query: str, evidence_summary: str, base_url: str, model: str, api_key: str = "",
) -> ResearchEvaluation:
    prompt = (
        "元の依頼に答える根拠が十分か評価してください。不足時だけ、重複しない追加検索を最大3件返してください。"
        "指定JSON以外は出力しません。\n\n依頼:\n" + query + "\n\n現在の根拠:\n" + evidence_summary[:12000]
    )
    try:
        raw = await _llm(
            [{"role": "system", "content": "あなたは調査品質の評価器です。"}, {"role": "user", "content": prompt}],
            base_url, model, api_key, temperature=0, max_tokens=768, disable_thinking=True,
            response_format={"type": "json_schema", "name": "research_evaluation", "schema": EVALUATION_SCHEMA, "strict": True},
        )
        return ResearchEvaluation.model_validate(_json_object(raw))
    except Exception as exc:
        logger.warning("複合調査の不足評価を省略: %s", type(exc).__name__)
        return ResearchEvaluation(sufficient=True, reason="再評価を利用できないため収集済み根拠で要約")
