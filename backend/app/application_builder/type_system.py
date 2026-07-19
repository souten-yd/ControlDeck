from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.application_builder.diagnostics import Diagnostic, diagnostic

ATOMIC_TYPES = {
    "any", "null", "boolean", "integer", "number", "decimal", "string", "date",
    "datetime", "duration", "bytes", "file", "directory", "url", "json", "object",
    "image", "audio", "video",
}
GENERIC_TYPES = {"array", "optional", "stream", "table", "map"}


class TypeRef(BaseModel):
    kind: str
    arguments: list["TypeRef"] = Field(default_factory=list)
    fields: dict[str, "TypeRef"] = Field(default_factory=dict)

    def canonical(self) -> str:
        if self.kind == "object" and self.fields:
            body = ",".join(f"{key}:{value.canonical()}" for key, value in sorted(self.fields.items()))
            return f"object{{{body}}}"
        if self.arguments:
            return f"{self.kind}<{','.join(item.canonical() for item in self.arguments)}>"
        return self.kind


def parse_type(value: str | dict[str, Any] | TypeRef | None) -> tuple[TypeRef, list[Diagnostic]]:
    if isinstance(value, TypeRef):
        return value, []
    if isinstance(value, dict):
        return from_json_schema(value)
    raw = str(value or "any").strip().lower()
    if raw == "array":
        return TypeRef(kind="array", arguments=[TypeRef(kind="any")]), [
            diagnostic("TYPE_UNRESOLVED", "warning", "配列要素の型を確定できません", source="type-system")
        ]
    if raw in ATOMIC_TYPES:
        return TypeRef(kind=raw), []
    match = re.fullmatch(r"(array|optional|stream|table)<(.+)>", raw)
    if match:
        inner, issues = parse_type(match.group(2))
        return TypeRef(kind=match.group(1), arguments=[inner]), issues
    match = re.fullmatch(r"map<([^,]+),(.+)>", raw)
    if match:
        key, key_issues = parse_type(match.group(1))
        item, item_issues = parse_type(match.group(2))
        return TypeRef(kind="map", arguments=[key, item]), key_issues + item_issues
    return TypeRef(kind="any"), [diagnostic(
        "TYPE_UNRESOLVED", "warning", f"型 '{raw}' を確定できません", source="type-system",
        suggested_fix="JSON Schemaまたはnode output schemaを設定してください",
    )]


def from_json_schema(schema: dict[str, Any]) -> tuple[TypeRef, list[Diagnostic]]:
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if len(non_null) == 1 and len(non_null) != len(raw_type):
            inner, issues = parse_type(non_null[0])
            return TypeRef(kind="optional", arguments=[inner]), issues
    if raw_type == "array":
        item, issues = from_json_schema(schema.get("items") or {})
        return TypeRef(kind="array", arguments=[item]), issues
    if raw_type == "object":
        fields: dict[str, TypeRef] = {}
        issues: list[Diagnostic] = []
        for key, child in (schema.get("properties") or {}).items():
            field_type, field_issues = from_json_schema(child if isinstance(child, dict) else {})
            fields[str(key)] = field_type
            issues.extend(field_issues)
        return TypeRef(kind="object", fields=fields), issues
    mapping = {"bool": "boolean", "int": "integer", "float": "number"}
    return parse_type(mapping.get(str(raw_type), raw_type))


def is_assignable(source: TypeRef, target: TypeRef) -> bool:
    if source.kind == "any" or target.kind == "any":
        return True
    if target.kind == "optional":
        return source.kind == "null" or is_assignable(source, target.arguments[0])
    if source.kind == "integer" and target.kind in {"number", "decimal"}:
        return True
    if source.kind != target.kind:
        return False
    if source.arguments and target.arguments:
        return len(source.arguments) == len(target.arguments) and all(
            is_assignable(left, right) for left, right in zip(source.arguments, target.arguments, strict=True)
        )
    return True


TARGET_TYPE_MAP: dict[str, dict[str, str]] = {
    "csharp": {
        "any": "object", "null": "object?", "boolean": "bool", "integer": "long",
        "number": "double", "decimal": "decimal", "string": "string", "date": "DateOnly",
        "datetime": "DateTimeOffset", "duration": "TimeSpan", "bytes": "byte[]", "file": "FileInfo",
        "directory": "DirectoryInfo", "url": "Uri", "json": "JsonElement", "object": "object",
        "image": "byte[]", "audio": "byte[]", "video": "byte[]",
    },
    "cpp": {
        "any": "std::any", "null": "std::nullptr_t", "boolean": "bool", "integer": "std::int64_t",
        "number": "double", "decimal": "double", "string": "std::string", "bytes": "std::vector<std::byte>",
        "url": "std::string", "json": "nlohmann::json", "object": "nlohmann::json",
    },
}


def target_type(type_ref: TypeRef, target: str) -> str | None:
    if type_ref.kind == "array" and type_ref.arguments:
        inner = target_type(type_ref.arguments[0], target)
        return f"IReadOnlyList<{inner}>" if target == "csharp" and inner else (f"std::vector<{inner}>" if inner else None)
    if type_ref.kind == "optional" and type_ref.arguments:
        inner = target_type(type_ref.arguments[0], target)
        return f"{inner}?" if target == "csharp" and inner else (f"std::optional<{inner}>" if inner else None)
    if type_ref.kind == "stream" and type_ref.arguments:
        inner = target_type(type_ref.arguments[0], target)
        return f"IAsyncEnumerable<{inner}>" if target == "csharp" and inner else None
    if type_ref.kind == "table" and type_ref.arguments:
        inner = target_type(type_ref.arguments[0], target)
        return f"IReadOnlyList<{inner}>" if target == "csharp" and inner else None
    return TARGET_TYPE_MAP.get(target, {}).get(type_ref.kind)
