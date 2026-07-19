"""Workflow payload redaction shared by persistence, APIs, and previews."""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|passphrase|token|secret|authorization|cookie|api[_-]?key)", re.I
)
_SECRET_TEMPLATE = re.compile(r"\{\{\s*secrets\.[^}]+\}\}", re.I)
_TOKEN_METRIC_KEYS = {
    "token_usage", "total_tokens", "prompt_tokens", "completion_tokens",
    "input_tokens", "output_tokens", "generated_tokens", "max_tokens", "max_report_tokens",
    "requested_tokens", "context_tokens",
}


def is_sensitive_key(key: str) -> bool:
    """認証tokenとtoken使用量metadataを区別する。"""
    normalized = key.strip().lower().replace("-", "_")
    return normalized not in _TOKEN_METRIC_KEYS and _SENSITIVE_KEY.search(normalized) is not None


def collect_sensitive_values(value: Any, key: str = "") -> set[str]:
    """Collect non-empty values that are stored under a sensitive key."""
    found: set[str] = set()
    if is_sensitive_key(key) and isinstance(value, (str, int, float)):
        raw = str(value)
        if raw and raw != "***":
            found.add(raw)
        return found
    if isinstance(value, dict):
        for child_key, child in value.items():
            found.update(collect_sensitive_values(child, str(child_key)))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(collect_sensitive_values(child))
    return found


def redact(value: Any, key: str = "", sensitive_values: set[str] | None = None) -> Any:
    """Return a recursively redacted copy without mutating live executor context."""
    if is_sensitive_key(key):
        return "***"
    if isinstance(value, dict):
        return {
            str(k): ("***" if value.get("sensitive") is True and str(k) == "value" else redact(v, str(k), sensitive_values))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item, sensitive_values=sensitive_values) for item in value]
    if isinstance(value, tuple):
        return [redact(item, sensitive_values=sensitive_values) for item in value]
    if isinstance(value, str):
        result = _SECRET_TEMPLATE.sub("{{secrets.***}}", value)
        for secret in sensitive_values or ():
            if secret:
                result = result.replace(secret, "***")
        return result
    return value
