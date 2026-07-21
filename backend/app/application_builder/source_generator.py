from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.application_builder.diagnostics import Diagnostic, diagnostic
from app.application_builder.csharp_generator_runtime import render_workflow_source

GENERATOR_ID = "controldeck.csharp-console"
GENERATOR_VERSION = "1.4.0"
MAX_GENERATED_NODES = 500
MAX_WORKFLOW_IR_BYTES = 4 * 1024 * 1024
SUPPORTED_NODES = frozenset({
    "trigger", "condition.if", "control.loop", "control.merge", "util.wait", "util.now", "var.set", "string.op",
    "data.transform", "data.template", "data.filter", "data.aggregate", "file.read", "file.write", "file.exists",
    "file.glob", "http.request", "output.render", "signal.display",
})
SUPPORTED_STRING_OPERATIONS = frozenset({"upper", "lower", "trim", "replace", "split", "length", "template"})
SUPPORTED_DATA_TRANSFORMS = frozenset({"json_parse", "json_get", "json_set"})
SUPPORTED_FILTER_OPERATORS = frozenset({"exists", "truthy", "equals", "not_equals", "contains", "gt", "gte", "lt", "lte"})
SUPPORTED_AGGREGATIONS = frozenset({"count", "sum", "avg", "min", "max"})
SUPPORTED_DATE_TOKENS = frozenset({"%Y", "%m", "%d", "%H", "%M", "%S", "%%"})
SUPPORTED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
SECRET_ALIAS_REF = re.compile(r"\{\{\s*secrets\.(SECRET_[0-9]{3})\s*\}\}")
SENSITIVE_NAME = re.compile(r"password|passwd|token|secret|authorization|cookie|api[_-]?key", re.I)
RESTRICTED_HTTP_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "proxy-connection", "keep-alive", "upgrade"})
_CSHARP_KEYWORDS = frozenset({
    "abstract", "as", "base", "bool", "break", "byte", "case", "catch", "char", "checked",
    "class", "const", "continue", "decimal", "default", "delegate", "do", "double", "else",
    "enum", "event", "explicit", "extern", "false", "finally", "fixed", "float", "for",
    "foreach", "goto", "if", "implicit", "in", "int", "interface", "internal", "is", "lock",
    "long", "namespace", "new", "null", "object", "operator", "out", "override", "params",
    "private", "protected", "public", "readonly", "ref", "return", "sbyte", "sealed", "short",
    "sizeof", "stackalloc", "static", "string", "struct", "switch", "this", "throw", "true",
    "try", "typeof", "uint", "ulong", "unchecked", "unsafe", "ushort", "using", "virtual",
    "void", "volatile", "while",
})


@dataclass(frozen=True)
class SourceBundle:
    archive_name: str
    archive_bytes: bytes
    archive_checksum: str
    source_checksum: str
    manifest: dict[str, Any]
    files: tuple[dict[str, Any], ...]


class SourceGenerationError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]):
        super().__init__("source generation preflight failed")
        self.diagnostics = diagnostics


def generator_diagnostics(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
) -> list[Diagnostic]:
    return framework_generator_diagnostics(
        spec, workflow_ir, target_id=target_id, framework="csharp-console",
        allowed_platforms={"linux", "windows"}, generator_label="C# Console",
    )


def framework_generator_diagnostics(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
    framework: str, allowed_platforms: set[str], generator_label: str,
) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    targets = spec.get("targets") if isinstance(spec.get("targets"), list) else []
    target = next((item for item in targets if isinstance(item, dict) and item.get("id") == target_id), None)
    if target is None:
        return [diagnostic(
            "GENERATOR_TARGET_MISSING", "error", f"target '{target_id}' が存在しません",
            path="targets", source="source-generator",
        )]
    if target.get("framework") != framework:
        issues.append(diagnostic(
            "GENERATOR_TARGET_UNSUPPORTED", "error", f"{generator_label} generatorは{framework} targetだけに対応します",
            path=f"targets.{target_id}.framework", source="source-generator",
            suggested_fix=f"Platform Advisorで{generator_label} targetを選択してください",
        ))
    platforms = target.get("platforms") if isinstance(target.get("platforms"), list) else []
    if not platforms or any(item not in allowed_platforms for item in platforms):
        issues.append(diagnostic(
            "GENERATOR_PLATFORM_UNSUPPORTED", "error",
            f"{generator_label} generatorの対応platform: {'／'.join(sorted(allowed_platforms))}",
            path=f"targets.{target_id}.platforms", source="source-generator",
        ))
    if workflow_ir is None:
        return issues
    workflow_size = len(_canonical_json(workflow_ir))
    if workflow_size > MAX_WORKFLOW_IR_BYTES:
        issues.append(diagnostic(
            "GENERATOR_INPUT_TOO_LARGE", "error", "Workflow IRが4MiBの生成上限を超えています",
            path="workflow", source="source-generator",
        ))
    for raw in workflow_ir.get("diagnostics") or []:
        if not isinstance(raw, dict) or raw.get("severity") != "error":
            continue
        issues.append(Diagnostic.model_validate(raw))
    bindings = [item for item in spec.get("workflows", []) if isinstance(item, dict)]
    if len(bindings) > 1:
        issues.append(diagnostic(
            "GENERATOR_MULTIPLE_WORKFLOWS_UNSUPPORTED", "error",
            f"{generator_label} generatorは1つのWorkflow bindingだけに対応します",
            path="workflows", source="source-generator",
        ))
    required_secrets = workflow_ir.get("requiredSecrets") or workflow_ir.get("required_secrets") or []
    secret_aliases = {f"SECRET_{index:03d}" for index in range(1, len(required_secrets) + 1)}
    used_secret_aliases: set[str] = set()
    workflow_nodes = workflow_ir.get("nodes") or []
    if len(workflow_nodes) > MAX_GENERATED_NODES:
        issues.append(diagnostic(
            "GENERATOR_NODE_LIMIT_EXCEEDED", "error", "生成対象nodeは500件以下にしてください",
            path="workflow.nodes", source="source-generator",
        ))
    trigger_count = sum(
        str(item.get("nodeType") or item.get("node_type") or "") == "trigger"
        for item in workflow_nodes if isinstance(item, dict)
    )
    if workflow_nodes and trigger_count != 1:
        issues.append(diagnostic(
            "GENERATOR_TRIGGER_INVALID", "error", "生成対象Workflowにはtriggerが1件必要です",
            path="workflow.nodes", source="source-generator",
        ))
    for index, node in enumerate(workflow_nodes):
        node_type = str(node.get("nodeType") or node.get("node_type") or "")
        if node_type not in SUPPORTED_NODES:
            issues.append(diagnostic(
                "GENERATOR_NODE_UNSUPPORTED", "error", f"node '{node_type}' のC# generatorは未実装です",
                path=f"workflow.nodes.{index}", source="source-generator",
                suggested_fix="対応nodeへ置き換えるか、後続generatorを待ってください",
            ))
            continue
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        for config_path, alias in _secret_alias_references(config):
            used_secret_aliases.add(alias)
            if alias not in secret_aliases:
                issues.append(diagnostic(
                    "GENERATOR_SECRET_REFERENCE_INVALID", "error", "Secret aliasがrequired secret一覧と一致しません",
                    path=f"workflow.nodes.{index}.config.{config_path}", source="source-generator",
                ))
            if node_type != "http.request" or config_path.split(".", 1)[0] not in {"headers", "body"}:
                issues.append(diagnostic(
                    "GENERATOR_SECRET_POSITION_UNSUPPORTED", "error",
                    "Secret参照はHTTP requestのheaderまたはbodyだけに使用できます",
                    path=f"workflow.nodes.{index}.config.{config_path}", source="source-generator",
                    suggested_fix="URL、path、出力、制御条件へSecretを使用しないでください",
                ))
        if node_type == "string.op" and str(config.get("op") or "template") not in SUPPORTED_STRING_OPERATIONS:
            issues.append(diagnostic(
                "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error",
                f"string.op '{config.get('op')}' はB2 generatorで未対応です",
                path=f"workflow.nodes.{index}.config.op", source="source-generator",
            ))
        if node_type == "control.loop":
            mode = str(config.get("mode") or "count")
            if mode not in {"count", "foreach"}:
                issues.append(diagnostic(
                    "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error", f"control.loop mode '{mode}' は未対応です",
                    path=f"workflow.nodes.{index}.config.mode", source="source-generator",
                ))
            for key, maximum in (("count", 100), ("parallel", 5)):
                if config.get(key) in (None, ""):
                    continue
                try:
                    int(config[key])
                except (TypeError, ValueError):
                    issues.append(diagnostic(
                        "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error",
                        f"control.loop {key}は整数で指定してください（runtime上限: {maximum}）",
                        path=f"workflow.nodes.{index}.config.{key}", source="source-generator",
                    ))
        if node_type == "data.transform" and str(config.get("operation") or "json_parse") not in SUPPORTED_DATA_TRANSFORMS:
            issues.append(diagnostic(
                "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error",
                f"data.transform '{config.get('operation')}' はportable C# runtimeで未対応です",
                path=f"workflow.nodes.{index}.config.operation", source="source-generator",
                suggested_fix="json_parse／json_get／json_setへ置き換えてください",
            ))
        if node_type == "data.template" and str(config.get("output_format") or "text") not in {"text", "json"}:
            issues.append(diagnostic(
                "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error",
                f"data.template output_format '{config.get('output_format')}' は未対応です",
                path=f"workflow.nodes.{index}.config.output_format", source="source-generator",
            ))
        if node_type == "data.filter":
            operator = str(config.get("operator") or "truthy")
            if operator not in SUPPORTED_FILTER_OPERATORS:
                issues.append(diagnostic(
                    "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error", f"data.filter operator '{operator}' は未対応です",
                    path=f"workflow.nodes.{index}.config.operator", source="source-generator",
                ))
            if str(config.get("sort_order") or "asc") not in {"asc", "desc"}:
                issues.append(diagnostic(
                    "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error", "data.filter sort_orderはasc／descだけを使用できます",
                    path=f"workflow.nodes.{index}.config.sort_order", source="source-generator",
                ))
        if node_type == "data.aggregate" and str(config.get("operation") or "count") not in SUPPORTED_AGGREGATIONS:
            issues.append(diagnostic(
                "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error",
                f"data.aggregate '{config.get('operation')}' は未対応です",
                path=f"workflow.nodes.{index}.config.operation", source="source-generator",
            ))
        if node_type == "util.now":
            fmt = str(config.get("format") or "%Y-%m-%d %H:%M:%S")
            tokens = set(re.findall(r"%.", fmt))
            if tokens - SUPPORTED_DATE_TOKENS:
                issues.append(diagnostic(
                    "GENERATOR_NODE_CONFIG_UNSUPPORTED", "error",
                    f"util.now format tokenは未対応です: {', '.join(sorted(tokens - SUPPORTED_DATE_TOKENS))}",
                    path=f"workflow.nodes.{index}.config.format", source="source-generator",
                ))
        if node_type == "http.request":
            issues.extend(_http_node_issues(config, index))
        if node_type in {"file.read", "file.write", "file.exists", "file.glob"}:
            issues.extend(_file_node_issues(node_type, config, index))
        execution = node.get("execution") if isinstance(node.get("execution"), dict) else {}
        on_error = execution.get("onError", execution.get("on_error", "stop"))
        if on_error not in {"stop", "continue", "branch"}:
            issues.append(diagnostic(
                "GENERATOR_EXECUTION_POLICY_INVALID", "error",
                f"on_error '{on_error}' は生成runtimeで使用できません",
                path=f"workflow.nodes.{index}.execution.onError", source="source-generator",
            ))
        requires_approval = execution.get("requiresApproval", execution.get("requires_approval", False))
        if requires_approval:
            issues.append(diagnostic(
                "GENERATOR_APPROVAL_UNSUPPORTED", "error",
                "human approvalを保持するportable runtimeはまだ未実装です",
                path=f"workflow.nodes.{index}.execution.requiresApproval", source="source-generator",
            ))
    for alias in sorted(secret_aliases - used_secret_aliases):
        issues.append(diagnostic(
            "GENERATOR_SECRET_REFERENCE_MISSING", "error", "required secretを使用するnode位置が見つかりません",
            path="workflow.requiredSecrets", source="source-generator",
            suggested_fix=f"生成用IRの{alias}参照を再compileしてください",
        ))
    for index, edge in enumerate(workflow_ir.get("edges") or []):
        branch = edge.get("branch") if isinstance(edge, dict) else None
        source = str(edge.get("sourceNode") or edge.get("source_node") or "") if isinstance(edge, dict) else ""
        source_node = next((node for node in workflow_nodes if str(node.get("id") or "") == source), None)
        source_type = str((source_node or {}).get("nodeType") or (source_node or {}).get("node_type") or "")
        allowed = {None, "true", "false", "error", "timeout"} | ({"body", "done"} if source_type == "control.loop" else set())
        if branch not in allowed:
            issues.append(diagnostic(
                "GENERATOR_BRANCH_VALUE_UNSUPPORTED", "error", f"branch '{branch}' は生成runtimeで未対応です",
                path=f"workflow.edges.{index}.branch", source="source-generator",
            ))
    return issues


def _secret_alias_references(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_secret_alias_references(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_secret_alias_references(child, f"{path}.{index}" if path else str(index)))
    elif isinstance(value, str):
        found.extend((path, match.group(1)) for match in SECRET_ALIAS_REF.finditer(value))
        if "{{" in value and "secrets." in value and not SECRET_ALIAS_REF.search(value):
            found.append((path, "INVALID"))
    return found


def _http_node_issues(config: dict[str, Any], index: int) -> list[Diagnostic]:
    path = f"workflow.nodes.{index}.config"
    issues: list[Diagnostic] = []
    raw_url = config.get("url")
    url = raw_url.strip() if isinstance(raw_url, str) else ""
    if not url or len(url) > 2048 or "{{" in url or "}}" in url:
        issues.append(diagnostic(
            "GENERATOR_HTTP_URL_UNSAFE", "error",
            "HTTP URLは2048文字以内の固定absolute URLにしてください",
            path=f"{path}.url", source="source-generator",
            suggested_fix="動的な値はheaderまたはbodyへ移してください",
        ))
    else:
        parsed = urlsplit(url)
        loopback = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.scheme not in {"https", "http"} or (parsed.scheme == "http" and not loopback):
            issues.append(diagnostic(
                "GENERATOR_HTTP_URL_UNSAFE", "error",
                "HTTPS、またはloopbackへのHTTPだけを使用でき、userinfoは指定できません",
                path=f"{path}.url", source="source-generator",
            ))
        if parsed.fragment or any(SENSITIVE_NAME.search(name) for name, _value in parse_qsl(parsed.query, keep_blank_values=True)):
            issues.append(diagnostic(
                "GENERATOR_HTTP_URL_SENSITIVE", "error",
                "HTTP URLのfragmentや秘密らしいquery parameterは使用できません",
                path=f"{path}.url", source="source-generator",
                suggested_fix="credentialはSecret参照付きheaderまたはbodyへ移してください",
            ))
    method = str(config.get("method") or "GET").upper()
    if method not in SUPPORTED_HTTP_METHODS:
        issues.append(diagnostic(
            "GENERATOR_HTTP_METHOD_UNSUPPORTED", "error", "HTTP methodはGET／POST／PUT／PATCH／DELETE／HEADから選択してください",
            path=f"{path}.method", source="source-generator",
        ))
    headers = config.get("headers")
    if headers is not None and not isinstance(headers, (str, dict)):
        issues.append(diagnostic(
            "GENERATOR_HTTP_HEADERS_INVALID", "error", "HTTP headersはobjectまたは1行1headerの文字列にしてください",
            path=f"{path}.headers", source="source-generator",
        ))
    for name, value in _configured_headers(headers):
        normalized = name.strip().lower()
        if normalized in RESTRICTED_HTTP_HEADERS or not normalized or any(character <= " " or character >= "\x7f" for character in name) or "\r" in value or "\n" in value:
            issues.append(diagnostic(
                "GENERATOR_HTTP_HEADER_UNSAFE", "error", "restrictedまたは不正なHTTP headerは使用できません",
                path=f"{path}.headers", source="source-generator",
            ))
        if SENSITIVE_NAME.search(name) and SECRET_ALIAS_REF.search(value) is None:
            issues.append(diagnostic(
                "GENERATOR_HTTP_CREDENTIAL_LITERAL_FORBIDDEN", "error",
                "credential headerにはSecret参照が必要です",
                path=f"{path}.headers", source="source-generator",
            ))
    if len(json.dumps(headers, ensure_ascii=False).encode()) > 32 * 1024:
        issues.append(diagnostic(
            "GENERATOR_HTTP_HEADERS_TOO_LARGE", "error", "HTTP headersは32KiB以下にしてください",
            path=f"{path}.headers", source="source-generator",
        ))
    body = config.get("body")
    if body is not None and len(str(body).encode()) > 2 * 1024 * 1024:
        issues.append(diagnostic(
            "GENERATOR_HTTP_BODY_TOO_LARGE", "error", "HTTP bodyは2MiB以下にしてください",
            path=f"{path}.body", source="source-generator",
        ))
    expected = config.get("expected_status", config.get("expect_status"))
    if expected not in (None, ""):
        try:
            status = int(expected)
        except (TypeError, ValueError):
            status = 0
        if status < 100 or status > 599:
            issues.append(diagnostic(
                "GENERATOR_HTTP_STATUS_INVALID", "error", "expected statusは100〜599にしてください",
                path=f"{path}.expected_status", source="source-generator",
            ))
    return issues


def _configured_headers(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [(str(name), str(header_value)) for name, header_value in value.items()]
    if not isinstance(value, str):
        return []
    rows: list[tuple[str, str]] = []
    for line in value.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        name, separator, header_value = line.partition(":")
        rows.append((name, header_value.strip()) if separator else ("", line))
    return rows


def _file_node_issues(node_type: str, config: dict[str, Any], index: int) -> list[Diagnostic]:
    path = f"workflow.nodes.{index}.config"
    issues: list[Diagnostic] = []
    key = "base_path" if node_type == "file.glob" else "path"
    value = config.get(key)
    raw = value.strip() if isinstance(value, str) else ""
    literal_parts = [part for part in raw.replace("\\", "/").split("/") if "{{" not in part]
    if not raw or len(raw) > 1024 or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw) or ".." in literal_parts:
        issues.append(diagnostic(
            "GENERATOR_FILE_PATH_UNSAFE", "error",
            "file pathは1024文字以内の許可root相対pathにしてください",
            path=f"{path}.{key}", source="source-generator",
        ))
    if node_type == "file.write":
        content = config.get("content")
        if content is not None and len(str(content).encode()) > 2 * 1024 * 1024:
            issues.append(diagnostic(
                "GENERATOR_FILE_CONTENT_TOO_LARGE", "error", "write contentは2MiB以下にしてください",
                path=f"{path}.content", source="source-generator",
            ))
    if node_type == "file.glob":
        pattern = config.get("pattern")
        raw_pattern = pattern.strip() if isinstance(pattern, str) else ""
        parts = raw_pattern.replace("\\", "/").split("/")
        if not raw_pattern or len(raw_pattern) > 256 or "{{" in raw_pattern or raw_pattern.startswith(("/", "\\")) or ".." in parts:
            issues.append(diagnostic(
                "GENERATOR_FILE_GLOB_UNSAFE", "error", "glob patternは256文字以内の固定relative patternにしてください",
                path=f"{path}.pattern", source="source-generator",
            ))
        if str(config.get("kind") or "all") not in {"all", "files", "directories"}:
            issues.append(diagnostic(
                "GENERATOR_FILE_GLOB_KIND_INVALID", "error", "glob kindはall／files／directoriesから選択してください",
                path=f"{path}.kind", source="source-generator",
            ))
        try:
            limit = int(config.get("limit") or 100)
        except (TypeError, ValueError):
            limit = 0
        if limit < 1 or limit > 1000:
            issues.append(diagnostic(
                "GENERATOR_FILE_GLOB_LIMIT_INVALID", "error", "glob limitは1〜1000にしてください",
                path=f"{path}.limit", source="source-generator",
            ))
    return issues


def generate_csharp_console(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
) -> SourceBundle:
    issues = generator_diagnostics(spec, workflow_ir, target_id=target_id)
    if issues:
        raise SourceGenerationError(issues)
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    project_name = _csharp_identifier(str(app.get("name") or "GeneratedApplication"))
    namespace = project_name
    spec_bytes = _canonical_json(spec)
    workflow_payload = workflow_ir or {
        "schemaVersion": 1, "name": "", "inputs": [], "outputs": [], "nodes": [], "edges": [],
        "requiredSecrets": [], "capabilities": [], "sideEffects": [], "diagnostics": [],
    }
    required_secrets = workflow_payload.get("requiredSecrets") or workflow_payload.get("required_secrets") or []
    workflow_bytes = _canonical_json(workflow_payload)
    spec_checksum = _sha256(spec_bytes)
    workflow_checksum = _sha256(workflow_bytes)
    root = project_name
    files: dict[str, tuple[bytes, str]] = {
        f"{root}/README.md": (_readme(project_name, workflow_payload).encode(), "config"),
        f"{root}/src/{project_name}/{project_name}.csproj": (_project_file(namespace).encode(), "managed"),
        f"{root}/src/{project_name}/Program.cs": (_program(namespace).encode(), "managed"),
        f"{root}/src/{project_name}/Generated/Application.generated.cs": (
            _application_source(namespace, app, spec_checksum, workflow_checksum).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Generated/Workflow.generated.cs": (
            render_workflow_source(namespace, workflow_payload, _topological_nodes(workflow_payload), _csharp_string).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Extensions/WorkflowExtensions.cs": (_extensions(namespace).encode(), "extension"),
        f"{root}/src/{project_name}/appsettings.json": (b'{"schemaVersion":1}\n', "config"),
        f"{root}/tests/{project_name}.GeneratedTests/{project_name}.GeneratedTests.csproj": (
            _test_project(project_name).encode(), "managed",
        ),
        f"{root}/tests/{project_name}.GeneratedTests/Program.cs": (_test_program(namespace).encode(), "managed"),
    }
    file_rows = tuple(
        {"path": path, "sha256": _sha256(content), "bytes": len(content), "kind": kind}
        for path, (content, kind) in sorted(files.items())
    )
    source_checksum = _source_checksum(files)
    manifest = {
        "schemaVersion": 1, "phase": "B2.5",
        "generator": {"id": GENERATOR_ID, "version": GENERATOR_VERSION},
        "input": {
            "specChecksum": spec_checksum, "workflowChecksum": workflow_checksum,
            "targetId": target_id, "framework": "csharp-console",
        },
        "sourceChecksum": source_checksum,
        "runtime": {
            "secretInjection": "environment-alias-redacted-output" if required_secrets else "none",
            "secretEnvironment": [f"CONTROLDECK_SECRET_{index:03d}" for index in range(1, len(required_secrets) + 1)],
            "sideEffects": sorted(set(workflow_payload.get("sideEffects") or workflow_payload.get("side_effects") or [])),
            "auditRoot": "CONTROLDECK_APP_AUDIT_ROOT" if (workflow_payload.get("sideEffects") or workflow_payload.get("side_effects")) else "none",
            "fileRoot": "CONTROLDECK_APP_WORK_ROOT" if any(
                str(node.get("nodeType") or node.get("node_type") or "").startswith("file.")
                for node in workflow_payload.get("nodes") or [] if isinstance(node, dict)
            ) else "none",
        },
        "files": list(file_rows),
        "managedFiles": [row["path"] for row in file_rows if row["kind"] == "managed"],
        "extensionFiles": [row["path"] for row in file_rows if row["kind"] == "extension"],
        "configFiles": [row["path"] for row in file_rows if row["kind"] == "config"],
    }
    manifest_path = f"{root}/.controldeck/generation-manifest.json"
    manifest_bytes = _canonical_json(manifest)
    files[manifest_path] = (manifest_bytes, "manifest")
    archive = _deterministic_zip({path: content for path, (content, _kind) in files.items()})
    return SourceBundle(
        archive_name=f"{project_name}-source.zip", archive_bytes=archive,
        archive_checksum=_sha256(archive), source_checksum=source_checksum,
        manifest=manifest, files=tuple([*file_rows, {
            "path": manifest_path, "sha256": _sha256(manifest_bytes),
            "bytes": len(manifest_bytes), "kind": "manifest",
        }]),
    )


def bundle_metadata(bundle: SourceBundle) -> dict[str, Any]:
    return {
        "phase": str(bundle.manifest.get("phase") or "B2.5"), "generator": bundle.manifest["generator"],
        "deterministic": True, "archiveName": bundle.archive_name,
        "archiveChecksum": bundle.archive_checksum, "sourceChecksum": bundle.source_checksum,
        "archiveBytes": len(bundle.archive_bytes), "files": list(bundle.files), "manifest": bundle.manifest,
        "sideEffects": {
            "executor": False, "network": False, "subprocess": False,
            "filesystemWrite": False, "secretResolution": False,
        },
    }


def target_generator_diagnostics(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
) -> list[Diagnostic]:
    framework = _target_framework(spec, target_id)
    if framework == "aspnet-blazor":
        from app.application_builder.aspnet_source_generator import aspnet_generator_diagnostics

        return aspnet_generator_diagnostics(spec, workflow_ir, target_id=target_id)
    return generator_diagnostics(spec, workflow_ir, target_id=target_id)


def generate_source_bundle(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
) -> SourceBundle:
    framework = _target_framework(spec, target_id)
    if framework == "aspnet-blazor":
        from app.application_builder.aspnet_source_generator import generate_aspnet_api

        return generate_aspnet_api(spec, workflow_ir, target_id=target_id)
    return generate_csharp_console(spec, workflow_ir, target_id=target_id)


def _target_framework(spec: dict[str, Any], target_id: str) -> str:
    target = next((
        item for item in spec.get("targets") or []
        if isinstance(item, dict) and item.get("id") == target_id
    ), None)
    return str((target or {}).get("framework") or "")


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_checksum(files: dict[str, tuple[bytes, str]]) -> str:
    digest = hashlib.sha256()
    for path, (content, _kind) in sorted(files.items()):
        digest.update(path.encode("utf-8")); digest.update(b"\0"); digest.update(content); digest.update(b"\0")
    return digest.hexdigest()


def _deterministic_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def _csharp_identifier(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9_]+", value) if part]
    identifier = "_".join(parts) or "GeneratedApplication"
    if identifier[0].isdigit():
        identifier = f"App_{identifier}"
    if identifier.lower() in _CSHARP_KEYWORDS:
        identifier = f"App_{identifier}"
    return identifier[:120]


def _csharp_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _project_file(namespace: str) -> str:
    return f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <RootNamespace>{namespace}</RootNamespace>
    <Deterministic>true</Deterministic>
  </PropertyGroup>
</Project>
'''


def _program(namespace: str) -> str:
    return f'''using System.Text.Json;
using System.Text.Json.Nodes;
using {namespace}.Generated;

var input = args.Length > 0
    ? JsonNode.Parse(args[0]) as JsonObject ?? new JsonObject()
    : new JsonObject();
var result = await GeneratedWorkflow.RunAsync(input);
Console.WriteLine(result.ToJsonString(new JsonSerializerOptions {{ WriteIndented = true }}));
'''


def _application_source(namespace: str, app: dict[str, Any], spec_checksum: str, workflow_checksum: str) -> str:
    return f'''namespace {namespace}.Generated;

public static class GeneratedApplication
{{
    public const string Name = {_csharp_string(str(app.get("name") or namespace))};
    public const string DisplayName = {_csharp_string(str(app.get("displayName") or app.get("name") or namespace))};
    public const string Generator = "{GENERATOR_ID}/{GENERATOR_VERSION}";
    public const string SpecChecksum = "{spec_checksum}";
    public const string WorkflowChecksum = "{workflow_checksum}";
}}
'''


def _topological_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [item for item in workflow.get("nodes") or [] if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in nodes}
    order = {node_id: index for index, node_id in enumerate(by_id)}
    indegree = {node_id: 0 for node_id in by_id}
    downstream: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in workflow.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("sourceNode") or edge.get("source_node") or "")
        target = str(edge.get("targetNode") or edge.get("target_node") or "")
        if source in by_id and target in by_id:
            downstream[source].append(target); indegree[target] += 1
    ready = sorted((node_id for node_id, count in indegree.items() if count == 0), key=order.get)
    result: list[dict[str, Any]] = []
    while ready:
        node_id = ready.pop(0); result.append(by_id[node_id])
        for target in sorted(downstream[node_id], key=order.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target); ready.sort(key=order.get)
    if len(result) != len(nodes):
        raise SourceGenerationError([diagnostic(
            "GENERATOR_WORKFLOW_CYCLE", "error", "循環を含むWorkflowはC# generatorで未対応です",
            path="workflow.edges", source="source-generator",
        )])
    return result


def _extensions(namespace: str) -> str:
    return f'''namespace {namespace}.Generated;

internal static partial class GeneratedNodes
{{
    // Add user-owned extension methods in this file. Managed files are safe to regenerate separately.
}}
'''


def _test_project(project_name: str) -> str:
    return f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><Deterministic>true</Deterministic></PropertyGroup>
  <ItemGroup><ProjectReference Include="../../src/{project_name}/{project_name}.csproj" /></ItemGroup>
</Project>
'''


def _test_program(namespace: str) -> str:
    return f'''using System.Text.Json.Nodes;
using {namespace}.Generated;

if (string.IsNullOrWhiteSpace(GeneratedApplication.SpecChecksum)) throw new Exception("Spec checksum is missing");
GeneratedWorkflow.ValidateGeneratedSource();
Console.WriteLine("Generated source self-test passed");
'''


def _readme(project_name: str, workflow: dict[str, Any]) -> str:
    secret_count = len(workflow.get("requiredSecrets") or workflow.get("required_secrets") or [])
    secret_note = ""
    if secret_count:
        variables = "\n".join(f"- `CONTROLDECK_SECRET_{index:03d}`: required Secret #{index}" for index in range(1, secret_count + 1))
        secret_note = f"""
Required Secret values are injected only at process start through these environment aliases:

{variables}

The aliases intentionally do not disclose ControlDeck Secret names. Missing or over-64-KiB values stop execution. Final JSON output is redacted against loaded values.
"""
    file_note = ""
    if any(
        str(node.get("nodeType") or node.get("node_type") or "").startswith("file.")
        for node in workflow.get("nodes") or [] if isinstance(node, dict)
    ):
        file_note = """
File nodes require `CONTROLDECK_APP_WORK_ROOT` to reference an existing application-owned directory. Paths are relative, normalized, contained, and symbolic-link traversal is rejected. Writes are size-bounded, overwrite atomically, and append metadata to `.controldeck-side-effects.audit.jsonl` without file content.
"""
    side_effect_note = ""
    if workflow.get("sideEffects") or workflow.get("side_effects"):
        side_effect_note = """
Side-effect execution requires an existing `CONTROLDECK_APP_AUDIT_ROOT` (or the file work root as fallback). HTTP audit records include only method/origin/request size/result; header, body, query, response, and Secret values are never recorded.
"""
    return f'''# {project_name}

Deterministically generated by `{GENERATOR_ID}/{GENERATOR_VERSION}`.

- Managed source: `src/{project_name}/Generated/` and project files
- User extension boundary: `src/{project_name}/Extensions/`
- Non-secret config: `src/{project_name}/appsettings.json`
- Generation manifest: `.controldeck/generation-manifest.json`

```bash
dotnet run --project src/{project_name}/{project_name}.csproj -- '{{"message":"hello"}}'
dotnet run --project tests/{project_name}.GeneratedTests/{project_name}.GeneratedTests.csproj
```

No Secret value is embedded. Regeneration tooling must compare managed-file checksums before overwriting files.
{secret_note}{file_note}{side_effect_note}
'''
