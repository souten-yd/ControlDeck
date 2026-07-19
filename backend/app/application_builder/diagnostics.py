from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Diagnostic(BaseModel):
    code: str
    severity: Literal["error", "warning", "suggestion"]
    message: str
    path: str = ""
    source: str
    suggested_fix: str = Field(default="", serialization_alias="suggestedFix")
    auto_fix: bool = Field(default=False, serialization_alias="autoFix")
    details: dict[str, Any] = Field(default_factory=dict)


def diagnostic(
    code: str, severity: Literal["error", "warning", "suggestion"], message: str,
    *, path: str = "", source: str = "application-validator", suggested_fix: str = "",
    details: dict[str, Any] | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code, severity=severity, message=message, path=path, source=source,
        suggested_fix=suggested_fix, details=details or {},
    )
