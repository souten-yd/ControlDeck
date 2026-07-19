"""公開ランタイムと編集デバッグで共有する入出力contract。"""
from __future__ import annotations

import json
from typing import Any


_INPUT_TYPES = {
    "text": "string", "short_text": "string", "paragraph": "string",
    "number": "number", "boolean": "boolean", "select": "string",
    "multi_select": "array", "date": "string", "datetime": "string",
    "file": "string", "file_list": "array", "json": "object",
    "key_value": "object", "secret_reference": "string",
}


def build_input_schema(definition: dict[str, Any]) -> dict[str, Any]:
    trigger = next((node for node in definition.get("nodes", []) if node.get("type") == "trigger"), {})
    config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
    raw_fields = config.get("inputs") if isinstance(config.get("inputs"), list) else []
    fields: list[dict[str, Any]] = []
    properties: dict[str, Any] = {}
    required: list[str] = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        if not key or key in properties:
            continue
        input_type = str(raw.get("type") or "text")
        if input_type not in _INPUT_TYPES:
            input_type = "text"
        field = {
            name: raw[name] for name in (
                "key", "label", "description", "required", "default", "placeholder",
                "maxLength", "sample", "options",
            ) if name in raw
        }
        field["key"] = key
        field["type"] = input_type
        fields.append(field)
        prop: dict[str, Any] = {"type": _INPUT_TYPES[input_type], "title": str(raw.get("label") or key)}
        if raw.get("description"):
            prop["description"] = str(raw["description"])
        if raw.get("default") is not None:
            prop["default"] = raw["default"]
        if raw.get("maxLength") is not None and prop["type"] == "string":
            try:
                prop["maxLength"] = max(1, int(raw["maxLength"]))
            except (TypeError, ValueError):
                pass
        options = [part.strip() for part in str(raw.get("options") or "").replace(",", "\n").splitlines() if part.strip()]
        if input_type == "select" and options:
            prop["enum"] = options
        if input_type in {"multi_select", "file_list"}:
            prop["items"] = {"type": "string"}
            if options:
                prop["items"]["enum"] = options
        if input_type in {"date", "datetime"}:
            prop["format"] = "date" if input_type == "date" else "date-time"
        properties[key] = prop
        if raw.get("required"):
            required.append(key)
    return {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False, "x-control-deck-fields": fields,
    }


def build_output_schema(definition: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    outputs: list[dict[str, Any]] = []
    for node in definition.get("nodes", []):
        if node.get("type") not in {"signal.display", "output.render", "flow.return"}:
            continue
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        name = str(config.get("name") or config.get("signal") or node.get("id") or "output").strip()
        if not name or name in properties:
            continue
        renderer = str(config.get("renderer") or ("text" if node.get("type") == "signal.display" else "auto"))
        declared = config.get("schema")
        if isinstance(declared, str):
            try:
                declared = json.loads(declared)
            except json.JSONDecodeError:
                declared = None
        schema = declared if isinstance(declared, dict) else {"type": _renderer_json_type(renderer)}
        properties[name] = schema
        outputs.append({
            "name": name, "type": renderer, "schema": schema,
            "title": str(config.get("title") or name),
            "description": str(config.get("description") or ""),
        })
    return {
        "type": "object", "properties": properties, "additionalProperties": False,
        "x-control-deck-outputs": outputs,
    }


def validate_public_inputs(schema: dict[str, Any], values: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key in schema.get("required", []):
        if key not in values or values[key] is None or values[key] == "" or values[key] == []:
            errors.append(f"必須入力「{properties.get(key, {}).get('title', key)}」を入力してください")
    unknown = sorted(set(values) - set(properties))
    if unknown and schema.get("additionalProperties") is False:
        errors.append(f"未定義の入力があります: {', '.join(unknown)}")
    for key, value in values.items():
        spec = properties.get(key)
        if not isinstance(spec, dict) or value in (None, ""):
            continue
        expected = spec.get("type")
        valid = (
            (expected == "string" and isinstance(value, str))
            or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (expected == "boolean" and isinstance(value, bool))
            or (expected == "array" and isinstance(value, list))
            or (expected == "object" and isinstance(value, (dict, str)))
        )
        if expected and not valid:
            errors.append(f"入力「{spec.get('title', key)}」の型が正しくありません")
        if isinstance(value, str) and spec.get("maxLength") and len(value) > int(spec["maxLength"]):
            errors.append(f"入力「{spec.get('title', key)}」が最大長を超えています")
        if spec.get("enum") and value not in spec["enum"]:
            errors.append(f"入力「{spec.get('title', key)}」の選択肢が正しくありません")
    return errors


def final_outputs(context: dict[str, Any], *, expose_source: bool = True) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for node_id, entry in context.items():
        if node_id.startswith("__") or not isinstance(entry, dict):
            continue
        output = entry.get("output")
        if not isinstance(output, dict) or not output.get("display"):
            continue
        name = str(output.get("signal") or node_id)
        base = name
        suffix = 2
        while name in outputs:
            name = f"{base}_{suffix}"
            suffix += 1
        item: dict[str, Any] = {
            "type": str(output.get("renderer") or output.get("type") or "text"),
            "value": output.get("value"),
        }
        if expose_source:
            item["source_node_id"] = node_id
        if output.get("output_contract"):
            item.update({key: output.get(key) for key in (
                "title", "description", "downloadable", "copyable", "collapsible",
                "sensitive", "filename", "mime_type",
            )})
        outputs[name] = item
    return outputs


def _renderer_json_type(renderer: str) -> str:
    renderer = renderer.lower()
    if renderer in {"json", "json_tree", "json_raw", "key_value", "status_card", "metric", "progress"}:
        return "object"
    if renderer in {"table", "image_gallery", "citation_list"}:
        return "array"
    return "string"
