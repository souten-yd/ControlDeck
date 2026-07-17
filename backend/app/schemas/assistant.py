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
    decided_by: Literal["rule", "llm", "fallback"] = "llm"


class ResearchEvaluation(BaseModel):
    sufficient: bool
    reason: str = Field(default="", max_length=200)
    next_steps: list[ResearchStep] = Field(default_factory=list, max_length=3)
