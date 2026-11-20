"""AIアシスタントの判定・複合調査スキーマ。"""
from typing import Literal

from pydantic import BaseModel, Field


class ResearchStep(BaseModel):
    tool: Literal["web", "academic"]
    query: str = Field(min_length=1, max_length=500)


class AssistantPlan(BaseModel):
    mode: Literal["chat", "web", "academic", "deep", "research"]
    reason: str = Field(min_length=1, max_length=200)
    steps: list[ResearchStep] = Field(default_factory=list, max_length=6)
    max_iterations: int = Field(default=3, ge=1, le=5)
    # tools: 道具を使えるモデルで、機能の判定を省いて対話として送った（調べるかはモデルが道具で決める）
    decided_by: Literal["rule", "llm", "fallback", "tools"] = "llm"
    # 調べる深さ。web=速い、academic/research=標準、deep=深い。チャットなどは None
    depth: Literal["quick", "standard", "deep"] | None = None


class ResearchEvaluation(BaseModel):
    sufficient: bool
    reason: str = Field(default="", max_length=200)
    next_steps: list[ResearchStep] = Field(default_factory=list, max_length=3)
