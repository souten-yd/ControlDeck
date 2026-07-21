from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.application_builder.compiler import validate_application_spec
from app.application_builder.diagnostics import Diagnostic, diagnostic
from app.schemas.application_builder import ApplicationPatchOperation

ALLOWED_ROOTS = {
    "application", "theme", "navigation", "pages", "entities", "apiEndpoints",
    "backgroundJobs", "clientState", "queries", "workflows", "permissions", "targets",
}
FORBIDDEN_TOKENS = {"__proto__", "prototype", "constructor"}


@dataclass(slots=True)
class PatchFailure(Exception):
    code: str
    message: str
    path: str


def spec_checksum(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preview_patches(spec: dict[str, Any], patches: list[ApplicationPatchOperation]) -> dict[str, Any]:
    working = copy.deepcopy(spec)
    diagnostics: list[Diagnostic] = []
    applied: list[dict[str, Any]] = []
    for index, operation in enumerate(patches):
        try:
            _validate_scope_and_locks(working, operation)
            working = _apply(working, operation)
            applied.append(operation.model_dump(by_alias=True, exclude_none=True))
        except PatchFailure as exc:
            diagnostics.append(diagnostic(
                exc.code, "error", exc.message, path=exc.path,
                source="application-patch-validator", details={"operationIndex": index},
            ))
            break
    if not diagnostics:
        diagnostics.extend(validate_application_spec(working))
    return {
        "valid": not any(item.severity == "error" for item in diagnostics),
        "baseChecksum": spec_checksum(spec),
        "resultChecksum": spec_checksum(working),
        "patchedSpec": working,
        "appliedPatches": applied,
        "diagnostics": [item.model_dump(by_alias=True) for item in diagnostics],
    }


def _tokens(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise PatchFailure("PATCH_PATH_INVALID", "JSON Pointerはroot以外の絶対pathで指定してください", path)
    raw = path[1:].split("/")
    if len(raw) > 64:
        raise PatchFailure("PATCH_PATH_TOO_DEEP", "Patch pathが深すぎます", path)
    if any(re.search(r"~(?![01])", item) for item in raw):
        raise PatchFailure("PATCH_PATH_INVALID", "JSON Pointerのescapeが不正です", path)
    tokens = [item.replace("~1", "/").replace("~0", "~") for item in raw]
    if any(item in FORBIDDEN_TOKENS for item in tokens):
        raise PatchFailure("PATCH_PATH_FORBIDDEN", "このpath tokenは使用できません", path)
    if not tokens or tokens[0] not in ALLOWED_ROOTS:
        raise PatchFailure("PATCH_SCOPE_FORBIDDEN", "Application Specの許可されたsectionだけを変更できます", path)
    return tokens


def _validate_scope_and_locks(spec: dict[str, Any], operation: ApplicationPatchOperation) -> None:
    tokens = _tokens(operation.path)
    if operation.op == "move":
        if not operation.from_path:
            raise PatchFailure("PATCH_FROM_REQUIRED", "moveにはfromが必要です", operation.path)
        source_tokens = _tokens(operation.from_path)
        _assert_unlocked(spec, source_tokens, "move", operation.from_path)
    elif operation.from_path is not None:
        raise PatchFailure("PATCH_FROM_FORBIDDEN", "fromはmoveでのみ指定できます", operation.path)
    _assert_unlocked(spec, tokens, operation.op, operation.path)


def _assert_unlocked(spec: dict[str, Any], tokens: list[str], operation: str, path: str) -> None:
    current: Any = spec
    component_chain: list[tuple[dict[str, Any], int]] = []
    if _is_component(current):
        component_chain.append((current, 0))
    for index, token in enumerate(tokens):
        try:
            current = _child(current, token)
        except PatchFailure:
            break  # add先がまだ存在しない場合もancestor lockは検査済み
        if _is_component(current):
            component_chain.append((current, index + 1))
    for component, start in component_chain:
        locks = component.get("locked") if isinstance(component.get("locked"), dict) else {}
        relative = tokens[start:]
        for category in _lock_categories(relative, operation):
            if category == "locked" or bool(locks.get(category)):
                component_id = str(component.get("id") or "component")
                raise PatchFailure("PATCH_LOCK_VIOLATION", f"{component_id} の{category} lockにより変更できません", path)


def _lock_categories(relative: list[str], operation: str) -> set[str]:
    if not relative:
        if operation == "move":
            return {"structure", "position"}
        if operation == "replace":
            return {"structure", "binding", "style", "position", "content"}
        return {"structure"}
    if relative[0] == "locked":
        return {"locked"}
    if operation == "move":
        return {"position", "structure"} if relative[0] == "children" else {"position"}
    if relative[0] in {"id", "type", "children"}:
        return {"structure"}
    if relative[0] in {"binding", "events"}:
        return {"binding"}
    if relative[0] == "responsive":
        return {"position"}
    if relative[0] in {"style", "styles", "className"}:
        return {"style"}
    if relative[0] == "properties" and len(relative) == 1:
        return {"content", "style"}
    if relative[0] == "properties" and len(relative) > 1 and relative[1] in {"style", "color", "spacing", "radius", "className"}:
        return {"style"}
    return {"content"}


def _is_component(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("id"), str) and isinstance(value.get("type"), str)


def _child(container: Any, token: str) -> Any:
    if isinstance(container, dict):
        if token not in container:
            raise PatchFailure("PATCH_PATH_MISSING", "Patch pathが存在しません", token)
        return container[token]
    if isinstance(container, list):
        index = _index(token, len(container), allow_end=False)
        return container[index]
    raise PatchFailure("PATCH_PATH_INVALID", "objectまたはarray以外を辿れません", token)


def _parent(document: dict[str, Any], tokens: list[str]) -> tuple[Any, str]:
    current: Any = document
    for token in tokens[:-1]:
        current = _child(current, token)
    return current, tokens[-1]


def _index(token: str, length: int, *, allow_end: bool) -> int:
    if token == "-" and allow_end:
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", token):
        raise PatchFailure("PATCH_ARRAY_INDEX_INVALID", "array indexが不正です", token)
    index = int(token)
    limit = length if allow_end else length - 1
    if index < 0 or index > limit:
        raise PatchFailure("PATCH_ARRAY_INDEX_INVALID", "array indexが範囲外です", token)
    return index


def _apply(document: dict[str, Any], operation: ApplicationPatchOperation) -> dict[str, Any]:
    tokens = _tokens(operation.path)
    if operation.op == "move":
        source_tokens = _tokens(operation.from_path or "")
        value = copy.deepcopy(_get(document, source_tokens))
        _remove(document, source_tokens)
        _add(document, tokens, value)
    elif operation.op == "add":
        _add(document, tokens, copy.deepcopy(operation.value))
    elif operation.op == "replace":
        _replace(document, tokens, copy.deepcopy(operation.value))
    else:
        _remove(document, tokens)
    return document


def _get(document: dict[str, Any], tokens: list[str]) -> Any:
    current: Any = document
    for token in tokens:
        current = _child(current, token)
    return current


def _add(document: dict[str, Any], tokens: list[str], value: Any) -> None:
    parent, token = _parent(document, tokens)
    if isinstance(parent, dict):
        parent[token] = value
    elif isinstance(parent, list):
        parent.insert(_index(token, len(parent), allow_end=True), value)
    else:
        raise PatchFailure("PATCH_PATH_INVALID", "add先がobjectまたはarrayではありません", "/".join(tokens))


def _replace(document: dict[str, Any], tokens: list[str], value: Any) -> None:
    parent, token = _parent(document, tokens)
    if isinstance(parent, dict):
        if token not in parent:
            raise PatchFailure("PATCH_PATH_MISSING", "replace対象が存在しません", "/".join(tokens))
        parent[token] = value
    elif isinstance(parent, list):
        parent[_index(token, len(parent), allow_end=False)] = value
    else:
        raise PatchFailure("PATCH_PATH_INVALID", "replace先がobjectまたはarrayではありません", "/".join(tokens))


def _remove(document: dict[str, Any], tokens: list[str]) -> None:
    parent, token = _parent(document, tokens)
    if isinstance(parent, dict):
        if token not in parent:
            raise PatchFailure("PATCH_PATH_MISSING", "remove対象が存在しません", "/".join(tokens))
        del parent[token]
    elif isinstance(parent, list):
        del parent[_index(token, len(parent), allow_end=False)]
    else:
        raise PatchFailure("PATCH_PATH_INVALID", "remove先がobjectまたはarrayではありません", "/".join(tokens))
