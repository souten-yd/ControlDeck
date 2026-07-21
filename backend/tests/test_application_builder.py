import asyncio
import io
import json
import zipfile

from tests.conftest import CSRF_HEADERS


def _workflow_definition():
    return {
        "nodes": [
            {"id": "trigger", "type": "trigger", "name": "入力", "config": {"mode": "manual", "inputs": [
                {"key": "query", "label": "質問", "type": "text", "required": True},
            ]}},
            {"id": "http", "type": "http.request", "name": "取得", "config": {
                "url": "https://example.com", "retry_count": 2, "retry_wait": 1, "node_timeout": 20,
                "headers": "Authorization: {{secrets.API_TOKEN}}",
            }},
            {"id": "condition", "type": "condition.if", "name": "成功判定", "config": {
                "left": "{{http.ok}}", "op": "eq", "right": "true",
            }},
            {"id": "output", "type": "output.render", "name": "結果", "config": {
                "name": "answer", "renderer": "markdown", "value": "{{http.body}}", "schema": {"type": "string"},
            }},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "http"},
            {"id": "e2", "source": "http", "sourceHandle": "ok", "target": "condition"},
            {"id": "e3", "source": "condition", "sourceHandle": "true", "target": "output"},
        ],
    }


def test_type_system_parse_assignability_and_mapping():
    from app.application_builder.type_system import is_assignable, parse_type, target_type

    array, issues = parse_type("array<optional<string>>")
    assert not issues
    assert array.canonical() == "array<optional<string>>"
    assert target_type(array, "csharp") == "IReadOnlyList<string?>"
    integer, _ = parse_type("integer")
    number, _ = parse_type("number")
    assert is_assignable(integer, number)
    unknown, issues = parse_type("mystery")
    assert unknown.kind == "any" and issues[0].code == "TYPE_UNRESOLVED"


def test_application_builder_permissions_are_development_only():
    from app.security.permissions import ROLE_PRESETS

    assert {"application_builder.view", "application_builder.edit"} <= set(ROLE_PRESETS["administrator"])
    assert "application_builder.view" not in ROLE_PRESETS["operator"]
    assert "application_builder.view" not in ROLE_PRESETS["viewer"]


def test_workflow_compiler_projects_contract_policy_secret_and_capability():
    from app.application_builder.compiler import compile_workflow

    compiled = compile_workflow(_workflow_definition(), name="Portable", workflow_id=9, target="csharp")
    assert [(port.name, port.type.kind, port.required) for port in compiled.inputs] == [("query", "string", True)]
    assert [(port.name, port.type.kind) for port in compiled.outputs] == [("answer", "string")]
    http = next(node for node in compiled.nodes if node.id == "http")
    assert http.execution.retry_count == 2 and http.execution.timeout_seconds == 20
    assert http.codegen.support == "native" and http.codegen.source_available is True
    assert compiled.required_secrets[0].name == "API_TOKEN"
    assert "network" in compiled.capabilities and "external" in compiled.side_effects
    assert next(edge for edge in compiled.edges if edge.id == "e3").branch == "true"
    assert "secrets.SECRET_001" in json.dumps(http.config)  # sourceには実Secret名ではなくopaque aliasだけを渡す
    assert "secret-value" not in json.dumps(compiled.model_dump())


def test_workflow_compiler_reports_type_mismatch_and_unapproved_cycle():
    from app.application_builder.compiler import compile_workflow

    definition = _workflow_definition()
    definition["edges"][1]["target_type"] = "string"
    definition["edges"].append({"source": "output", "target": "http"})
    compiled = compile_workflow(definition, name="Invalid", target="csharp")
    codes = {item.code for item in compiled.diagnostics}
    assert "TYPE_MISMATCH" in codes
    assert "WORKFLOW_CYCLE_UNSUPPORTED" in codes


def test_spec_validation_references_bindings_targets_and_secrets():
    from app.application_builder.compiler import default_spec, validate_application_spec

    spec = default_spec("TestApp", "", None)
    spec["pages"] = [{"id": "home"}, {"id": "home"}]
    spec["navigation"]["items"] = [{"pageId": "missing", "label": "Missing"}]
    spec["targets"][0]["framework"] = "unknown-framework"
    spec["apiKey"] = "literal-secret"
    spec["pages"][0]["binding"] = "bad-source:value"
    codes = {item.code for item in validate_application_spec(spec)}
    assert {"SPEC_ID_DUPLICATE", "PAGE_REFERENCE_MISSING", "TARGET_UNKNOWN", "SECRET_LITERAL_FORBIDDEN", "BINDING_SOURCE_UNKNOWN"} <= codes


def test_semantic_component_catalog_and_tree_validation():
    from app.application_builder.compiler import default_spec, validate_application_spec

    spec = default_spec("ComponentApp", "", None)
    spec["pages"] = [{"id": "home", "title": "Home", "root": {
        "id": "root", "type": "layout.stack", "children": [
            {"id": "title", "type": "display.text", "properties": {"text": "Hello"}},
            {"id": "run", "type": "action.workflow-run", "binding": "workflow-output:answer"},
        ],
    }}]
    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]
    spec["pages"][0]["root"]["children"][0]["children"] = [{"id": "run", "type": "missing.widget"}]
    codes = {item.code for item in validate_application_spec(spec)}
    assert {"COMPONENT_CHILDREN_FORBIDDEN", "COMPONENT_ID_DUPLICATE", "COMPONENT_TYPE_UNKNOWN"} <= codes
    spec = default_spec("ExternalLlmApp", "", None)
    spec["llmRuntime"] = {"mode": "external", "provider": "ollama", "bundleRuntime": True}
    assert "LLM_RUNTIME_BUNDLE_CONFLICT" in {item.code for item in validate_application_spec(spec)}


def test_design_system_catalog_templates_tokens_and_accessibility():
    from copy import deepcopy

    from app.application_builder.compiler import default_spec, validate_application_spec
    from app.application_builder.design_system.components import component_catalog

    catalog = component_catalog()
    assert catalog["schemaVersion"] == 11
    assert {item["id"] for item in catalog["presets"]} == {
        "control-deck-modern", "compact", "touch", "dashboard", "data-dense", "minimal", "terminal", "media",
    }
    assert {item["id"] for item in catalog["composites"]} == {"kpi-card", "job-status", "log-viewer", "crud-table", "timeline"}
    assert {item["id"] for item in catalog["patterns"]} == {"dashboard", "settings", "wizard", "launcher"}
    assert {"accent", "status", "controlHeight", "motion", "breakpoint", "zIndex"} <= set(catalog["designTokens"])
    assert [item["id"] for item in catalog["previewStates"]] == ["default", "loading", "empty", "error", "disabled"]
    text_component = next(item for item in catalog["components"] if item["type"] == "display.text")
    assert text_component["propertySchema"] == [{"key": "text", "label": "Text", "type": "string"}]
    input_component = next(item for item in catalog["components"] if item["type"] == "input.text")
    assert [item["name"] for item in input_component["eventSchema"]] == ["change", "submit"]
    table_component = next(item for item in catalog["components"] if item["type"] == "data.table")
    assert {item["key"] for item in table_component["propertySchema"]} >= {"enableCreate", "enableUpdate", "enableDelete"}
    assert {item["id"] for item in catalog["bindingDefinitions"]} == set(catalog["bindingSources"])
    assert {item["id"] for item in catalog["eventActions"]} == {"workflow-run", "navigate", "state-set"}
    assert catalog["accessibilityAudit"] == {
        "minimumContrast": 4.5, "minimumLargeTextContrast": 3.0,
        "minimumTouchTarget": 44, "minimumFocusIndicator": 2,
    }
    for template in [*catalog["composites"], *catalog["patterns"]]:
        component_ids: dict[str, dict] = {}

        def collect(component):
            component_ids[component["id"]] = component
            for child in component.get("children", []):
                collect(child)

        collect(template["root"])
        keys = [item["key"] for item in template["parameters"]]
        assert keys and len(keys) == len(set(keys)), template["id"]
        for parameter in template["parameters"]:
            assert parameter["type"] in {"string", "number", "boolean", "enum"}
            assert parameter["targets"]
            for target in parameter["targets"]:
                component = component_ids[target["componentId"]]
                definition = next(item for item in catalog["components"] if item["type"] == component["type"])
                assert target["property"] in {field["key"] for field in definition["propertySchema"]}
        spec = default_spec("TemplateApp", "", None)
        spec["pages"] = [{"id": "home", "title": "Home", "root": deepcopy(template["root"])}]
        assert not [item for item in validate_application_spec(spec) if item.severity == "error"], template["id"]
    dashboard = next(item for item in catalog["patterns"] if item["id"] == "dashboard")
    assert {item["key"]: item["default"] for item in dashboard["parameters"]} == {
        "title": "Dashboard", "metricLabel": "Metric", "chartLabel": "Trend", "tableLabel": "Activity",
    }

    spec = default_spec("TokenApp", "", None)
    spec["theme"] = {"preset": "missing", "tokens": {"accent": "magenta", "rawCss": "body{}"}}
    codes = {item.code for item in validate_application_spec(spec)}
    assert {"THEME_PRESET_UNKNOWN", "DESIGN_TOKEN_VALUE_INVALID", "DESIGN_TOKEN_UNKNOWN"} <= codes
    spec = default_spec("A11yApp", "", None)
    spec["pages"] = [{"id": "home", "root": {
        "id": "root", "type": "layout.stack", "children": [
            {"id": "input", "type": "input.text", "properties": {"label": ""}},
            {"id": "run", "type": "action.workflow-run", "properties": {"label": ""}},
            {"id": "table", "type": "data.table", "properties": {"label": ""}},
            {"id": "chart", "type": "chart.line", "properties": {"label": ""}},
        ],
    }}]
    assert [item.code for item in validate_application_spec(spec)].count("A11Y_LABEL_REQUIRED") == 4
    spec = default_spec("PropertyApp", "", None)
    spec["pages"] = [{"id": "home", "title": "Home", "root": {
        "id": "root", "type": "layout.grid", "properties": {"gap": "huge", "columns": {"mobile": 0, "tablet": 2, "desktop": 13, "wide": 4}}, "children": [
            {"id": "table", "type": "data.table", "properties": {"pageSize": 0, "columns": [
                {"key": "name", "label": "", "type": "binary"}, {"key": "name", "label": "Duplicate", "type": "string"},
            ]}},
            {"id": "chart", "type": "chart.line", "properties": {"series": [{"key": "1bad", "label": "Series", "tone": "pink"}]}},
        ],
    }}]
    codes = {item.code for item in validate_application_spec(spec)}
    assert {"COMPONENT_PROPERTY_VALUE_INVALID", "COMPONENT_PROPERTY_RANGE_INVALID", "COMPONENT_PROPERTY_ITEM_INVALID", "COMPONENT_PROPERTY_ITEM_DUPLICATE", "A11Y_LABEL_REQUIRED"} <= codes

    spec = default_spec("StructuredApp", "", None)
    spec["pages"] = [{"id": "home", "title": "Home", "root": {
        "id": "root", "type": "layout.grid", "properties": {"gap": "md", "columns": {"mobile": 1, "tablet": 2, "desktop": 3}}, "children": [
            {"id": "table", "type": "data.table", "properties": {"label": "Users", "pageSize": 25, "columns": [{"key": "name", "label": "Name", "type": "string"}]}},
            {"id": "chart", "type": "chart.line", "properties": {"label": "Requests", "maxPoints": 500, "series": [{"key": "requests", "label": "Requests", "tone": "accent"}]}},
        ],
    }}]
    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]

    spec = default_spec("BindingEventApp", "", None)
    spec["workflows"] = [{"id": "main"}]
    spec["clientState"] = [{"id": "selectedValue", "type": "string", "initialValue": ""}]
    spec["pages"] = [
        {"id": "home", "title": "Home", "root": {
            "id": "root", "type": "layout.stack", "children": [{
                "id": "input", "type": "input.text", "properties": {"label": "Query"},
                "binding": "workflow-output:answer",
                "events": {
                    "change": {"action": "state-set", "target": "selectedValue"},
                    "submit": {"action": "workflow-run", "target": "main"},
                },
            }],
        }},
        {"id": "results", "title": "Results"},
    ]
    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]

    invalid = deepcopy(spec)
    component = invalid["pages"][0]["root"]["children"][0]
    component["binding"] = "invalid"
    component["events"] = {
        "unknown": {"action": "state-set", "target": "value"},
        "change": {"action": "navigate", "target": "missing"},
    }
    codes = {item.code for item in validate_application_spec(invalid)}
    assert {"BINDING_FORMAT_INVALID", "COMPONENT_EVENT_UNKNOWN", "COMPONENT_EVENT_ACTION_INVALID"} <= codes

    invalid = deepcopy(spec)
    component = invalid["pages"][0]["root"]["children"][0]
    component["binding"] = {"source": "constant", "reference": "{{secrets.token}}"}
    component["events"] = {
        "submit": {"action": "navigate", "target": "missing", "handler": "alert(1)"},
        "change": {"action": "state-set", "target": "1invalid"},
    }
    codes = {item.code for item in validate_application_spec(invalid)}
    assert {"BINDING_SECRET_FORBIDDEN", "COMPONENT_EVENT_INVALID", "COMPONENT_EVENT_TARGET_MISSING", "COMPONENT_EVENT_TARGET_INVALID"} <= codes


def test_typed_client_state_contract_validates_initial_values_bindings_and_event_targets():
    from copy import deepcopy

    from app.application_builder.compiler import compile_application, default_spec, validate_application_spec

    spec = default_spec("StateApp", "", None)
    spec["clientState"] = [
        {"id": "message", "type": "string", "initialValue": "Ready"},
        {"id": "count", "type": "integer", "initialValue": 1},
        {"id": "ratio", "type": "number", "initialValue": 0.5},
        {"id": "enabled", "type": "boolean", "initialValue": False},
        {"id": "result", "type": "object", "initialValue": {}},
        {"id": "items", "type": "array", "initialValue": []},
        {"id": "optional", "type": "string", "initialValue": None, "nullable": True},
    ]
    spec["pages"] = [{"id": "home", "title": "Home", "root": {
        "id": "root", "type": "layout.stack", "children": [
            {"id": "status", "type": "display.text", "properties": {"text": "Fallback"}, "binding": "state:message"},
            {"id": "input", "type": "input.text", "properties": {"label": "Message"}, "events": {"change": {"action": "state-set", "target": "message"}}},
        ],
    }}]
    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]
    assert compile_application(spec).client_state == spec["clientState"]

    invalid = deepcopy(spec)
    invalid["clientState"][0]["initialValue"] = 1
    invalid["clientState"].append({"id": "message", "type": "number", "initialValue": float("nan")})
    invalid["pages"][0]["root"]["children"][0]["binding"] = "state:missing"
    invalid["pages"][0]["root"]["children"][1]["events"]["change"]["target"] = "missing"
    codes = {item.code for item in validate_application_spec(invalid)}
    assert {"SPEC_ID_DUPLICATE", "CLIENT_STATE_INITIAL_TYPE_INVALID", "BINDING_STATE_MISSING", "COMPONENT_EVENT_TARGET_MISSING"} <= codes

    too_large = default_spec("LargeState", "", None)
    too_large["clientState"] = [{"id": "payload", "type": "string", "initialValue": "x" * 65_537}]
    assert "CLIENT_STATE_INITIAL_TOO_LARGE" in {item.code for item in validate_application_spec(too_large)}


def test_platform_advisor_and_preflight_are_deterministic_and_side_effect_free(monkeypatch):
    from app.application_builder import capabilities
    from app.application_builder.compiler import default_spec, validate_application_spec
    from app.application_builder.platform_advisor import advise_platforms, preflight_application
    from app.schemas.application_builder import PlatformAdvisorRequest

    monkeypatch.setattr(capabilities.shutil, "which", lambda _name: None)
    monkeypatch.setattr(capabilities.platform, "system", lambda: "Linux")
    request = PlatformAdvisorRequest.model_validate({
        "platforms": ["web"], "offline": True, "preferWebReuse": True,
        "preferredLanguage": "csharp",
    })
    first = advise_platforms(request)
    assert first == advise_platforms(request)
    assert first["recommendedId"] == "aspnet-blazor"
    assert first["recommendations"][0]["matrix"]["spec"] == "available"
    assert first["recommendations"][0]["matrix"]["source"] == "available"

    spec = default_spec("PreflightApp", "", None)
    result = preflight_application(spec, {"valid": True, "diagnostics": []})
    assert result["readyForGeneration"] is False
    assert result["sideEffects"] == {
        "executor": False, "network": False, "subprocess": False,
        "filesystemWrite": False, "secretResolution": False,
    }
    assert "GENERATOR_AUTH_ADAPTER_UNAVAILABLE" in {item["code"] for item in result["diagnostics"]}
    spec["targets"] = [{"id": "ios", "platforms": ["ios"], "framework": "avalonia"}]
    apple = preflight_application(spec, {"valid": True, "diagnostics": []})
    assert "APPLE_BUILD_HOST_REQUIRED" in {item["code"] for item in apple["diagnostics"]}
    spec["targets"] = [{"id": "bad", "platforms": ["ios"], "framework": "electron"}]
    assert "TARGET_PLATFORM_UNSUPPORTED" in {item.code for item in validate_application_spec(spec)}


def test_application_api_and_background_job_contracts_are_typed_and_referential():
    from app.application_builder.compiler import default_spec, validate_application_spec

    spec = default_spec("ApiApp", "", 7)
    spec["apiEndpoints"] = [{
        "id": "run-report", "method": "POST", "path": "/api/reports/{reportId}",
        "workflowId": 7, "mode": "async", "authentication": "inherit",
        "requestSchema": {"type": "object"}, "responseSchema": {"type": "object"},
    }]
    spec["backgroundJobs"] = [{
        "id": "daily-report", "workflowId": 7, "trigger": "daily", "schedule": "02:30",
        "timeZone": "Asia/Tokyo", "input": {"kind": "daily"},
        "concurrencyPolicy": "queue-one", "catchUpPolicy": "run-once",
    }]
    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]

    broken = json.loads(json.dumps(spec))
    broken["apiEndpoints"].append({
        "id": "duplicate-route", "method": "POST", "path": "/api/reports/{reportId}",
        "workflowId": 99, "authentication": "anonymous",
    })
    broken["backgroundJobs"].append({
        "id": "bad-interval", "workflowId": 99, "trigger": "interval", "schedule": "never",
    })
    broken["backgroundJobs"].extend([
        {"id": "bad-daily", "workflowId": 7, "trigger": "daily", "schedule": "25:99", "timeZone": "Mars/Olympus"},
        {"id": "bad-cron", "workflowId": 7, "trigger": "cron", "schedule": "not cron"},
    ])
    broken["apiEndpoints"][0]["requestSchema"] = {"type": "string", "pattern": "(?=unsafe-dialect)"}
    codes = {item.code for item in validate_application_spec(broken)}
    assert {
        "API_ROUTE_DUPLICATE", "API_WORKFLOW_REFERENCE_MISSING", "API_ANONYMOUS_EXPLICIT",
        "API_SCHEMA_KEYWORD_UNSUPPORTED", "JOB_WORKFLOW_REFERENCE_MISSING", "JOB_INTERVAL_INVALID",
        "JOB_DAILY_INVALID", "JOB_TIME_ZONE_INVALID", "JOB_CRON_INVALID",
    } <= codes

    invalid_method = json.loads(json.dumps(spec))
    invalid_method["apiEndpoints"][0]["method"] = "GET"
    assert "SPEC_SCHEMA_INVALID" in {item.code for item in validate_application_spec(invalid_method)}


def test_aspnet_api_generator_is_deterministic_authenticated_and_async_capable():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "output", "type": "output.render", "config": {
                "name": "answer", "renderer": "text", "value": "{{trigger.message}}",
            }},
        ],
        "edges": [{"source": "trigger", "target": "output"}],
    }
    workflow_ir = compile_workflow(
        definition, name="Web API", workflow_id=7, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("ApiWeb", "", 7)
    spec["application"]["authentication"] = "api-key"
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux"], "framework": "aspnet-blazor"}]
    spec["apiEndpoints"] = [
        {"id": "run-sync", "method": "POST", "path": "/api/run/{requestId}", "workflowId": 7, "mode": "sync", "authentication": "inherit", "timeoutSeconds": 30,
         "requestSchema": {"type": "object", "required": ["message"], "properties": {"message": {"type": "string", "minLength": 1}}, "additionalProperties": False},
         "responseSchema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}},
        {"id": "run-async", "method": "POST", "path": "/api/jobs", "workflowId": 7, "mode": "async", "authentication": "anonymous", "timeoutSeconds": 60},
    ]
    spec["backgroundJobs"] = [{
        "id": "daily-run", "workflowId": 7, "trigger": "daily", "schedule": "02:30",
        "timeZone": "Asia/Tokyo", "input": {"message": "scheduled"}, "timeoutSeconds": 90,
        "concurrencyPolicy": "queue-one", "catchUpPolicy": "run-once",
    }]
    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    first = generate_source_bundle(spec, workflow_ir, target_id="web")
    second = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert first.archive_bytes == second.archive_bytes
    assert first.manifest["phase"] == "E7"
    assert first.manifest["generator"] == {"id": "controldeck.aspnet-api", "version": "1.0.0"}
    assert first.manifest["input"]["framework"] == "aspnet-blazor"
    with zipfile.ZipFile(io.BytesIO(first.archive_bytes)) as archive:
        names = archive.namelist()
        assert names == sorted(names) and len(names) == 15
        api_name = next(name for name in names if name.endswith("/Generated/Api.generated.cs"))
        api_source = archive.read(api_name).decode()
        schema_source = archive.read(next(name for name in names if name.endswith("/Generated/JsonSchema.generated.cs"))).decode()
        schedule_source = archive.read(next(name for name in names if name.endswith("/Generated/BackgroundJobs.generated.cs"))).decode()
        openapi = json.loads(archive.read(next(name for name in names if name.endswith("/openapi.json"))))
        assert "CryptographicOperations.FixedTimeEquals" in api_source
        assert 'Environment.GetEnvironmentVariable("CONTROLDECK_APP_API_KEY")' in api_source
        assert 'app.MapPost("/api/run/{requestId}"' in api_source
        assert 'app.MapDelete("/api/jobs/{jobId}", GeneratedJobs.Cancel)' in api_source
        assert 'app.MapGet("/api/jobs/{jobId}/events"' in api_source
        assert "job.Cancellation.Cancel()" in api_source
        assert "ApplicationStopping" in api_source
        assert "text/event-stream" in api_source and "Status504GatewayTimeout" in api_source
        assert "Request schema validation failed" in api_source and "Response schema validation failed" in api_source
        assert "MaxArrayItems = 10_000" in schema_source and "additionalProperties" in schema_source
        assert "GeneratedScheduleService" in schedule_source
        assert 'Environment.GetEnvironmentVariable("CONTROLDECK_APP_DATA_DIR")' in schedule_source
        assert "File.Move(temporary, path, overwrite: true)" in schedule_source
        assert "RunPending" in schedule_source and "TimeZoneInfo.ConvertTimeToUtc" in schedule_source
        assert '"daily-run"' in schedule_source and '"Asia/Tokyo"' in schedule_source
        assert openapi["openapi"] == "3.1.0"
        assert openapi["paths"]["/api/run/{requestId}"]["post"]["security"] == [{"ApiKey": []}]
        assert openapi["paths"]["/api/run/{requestId}"]["post"]["requestBody"]["content"]["application/json"]["schema"]["required"] == ["message"]
        assert openapi["paths"]["/api/jobs"]["post"]["security"] == []
        assert openapi["paths"]["/api/jobs/{jobId}"]["delete"]["responses"]["202"] == {
            "description": "Cancellation requested",
        }
        assert "ApiKey" in openapi["components"]["securitySchemes"]
        assert openapi["paths"]["/api/background-jobs/{definitionId}/run"]["post"]["security"] == [{"ApiKey": []}]

    blocked = json.loads(json.dumps(spec))
    blocked["application"]["authentication"] = "local"
    blocked["pages"] = [{"id": "home", "title": "Home"}]
    blocked["apiEndpoints"][0]["requestSchema"] = {"type": "string", "pattern": "(?=unsupported)"}
    codes = {item.code for item in target_generator_diagnostics(blocked, workflow_ir, target_id="web")}
    assert {"GENERATOR_AUTH_ADAPTER_UNAVAILABLE", "API_SCHEMA_KEYWORD_UNSUPPORTED"} <= codes


def test_typed_entities_validate_and_generate_sqlite_crud_source():
    from app.application_builder.compiler import compile_workflow, default_spec, validate_application_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    definition = {"nodes": [{"id": "trigger", "type": "trigger", "config": {"mode": "manual"}}], "edges": []}
    workflow_ir = compile_workflow(definition, name="Entity API", workflow_id=7, target="csharp").model_dump(by_alias=True)
    spec = default_spec("EntityApi", "", 7)
    spec["application"].update({"authentication": "api-key", "database": "sqlite"})
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux"], "framework": "aspnet-blazor"}]
    spec["entities"] = [
        {"id": "Project", "tableName": "projects", "fields": [
            {"id": "name", "type": "string", "maxLength": 120, "unique": True},
            {"id": "active", "type": "boolean", "hasDefault": True, "default": True, "indexed": True},
        ], "crud": {"enabled": True, "basePath": "/api/projects", "operations": ["create", "read", "list", "update", "delete"]}},
        {"id": "Task", "tableName": "tasks", "fields": [
            {"id": "projectId", "type": "string", "reference": {"entityId": "Project", "onDelete": "cascade"}},
            {"id": "payload", "type": "json", "nullable": True},
        ], "crud": {"enabled": False}},
    ]
    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]
    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    bundle = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert bundle.manifest["phase"] == "E7" and len(bundle.files) == 16
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        source = archive.read(next(name for name in archive.namelist() if name.endswith("/Generated/Entities.generated.cs"))).decode()
        project = archive.read(next(name for name in archive.namelist() if name.endswith(".csproj") and "/src/" in name)).decode()
        openapi = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith("/openapi.json"))))
        assert "Microsoft.Data.Sqlite" in project and 'Version="8.0.29"' in project
        assert "PRAGMA foreign_keys=ON" in source and "PRAGMA journal_mode=WAL" in source
        assert "ALTER TABLE" in source and "AddWithValue" in source and "Path.GetFullPath" in source
        assert "BEGIN IMMEDIATE" in source and "Incompatible field migration" in source
        assert "_controldeck_audit" in source and "ON DELETE" in source and "Entity constraint failed" in source
        assert set(openapi["paths"]["/api/projects"]) == {"get", "post"}
        assert set(openapi["paths"]["/api/projects/{id}"]) == {"get", "patch", "delete"}
        assert openapi["paths"]["/api/projects"]["post"]["security"] == [{"ApiKey": []}]

    broken = json.loads(json.dumps(spec))
    broken["entities"][1]["fields"][0].update({"type": "integer", "nullable": False, "reference": {"entityId": "Missing", "onDelete": "set-null"}})
    codes = {item.code for item in validate_application_spec(broken)}
    assert {"ENTITY_REFERENCE_MISSING", "ENTITY_REFERENCE_TYPE_INVALID", "ENTITY_REFERENCE_SET_NULL_REQUIRED"} <= codes

    binding = json.loads(json.dumps(spec))
    binding["pages"] = [{"id": "home", "title": "Projects", "root": {
        "id": "projects-table", "type": "data.table", "properties": {"label": "Projects", "columns": [], "pageSize": 20},
        "binding": "entity:Project.name", "children": [],
    }}]
    assert "BINDING_ENTITY_MISSING" not in {item.code for item in validate_application_spec(binding)}
    binding["pages"][0]["root"]["binding"] = "entity:Project.missing"
    assert "BINDING_ENTITY_FIELD_MISSING" in {item.code for item in validate_application_spec(binding)}
    binding["pages"][0]["root"]["binding"] = "entity:Missing"
    assert "BINDING_ENTITY_MISSING" in {item.code for item in validate_application_spec(binding)}


def test_blazor_gui_source_is_deterministic_safe_and_entity_bound():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    workflow_ir = compile_workflow(
        {"nodes": [{"id": "trigger", "type": "trigger", "config": {"mode": "manual"}}], "edges": []},
        name="GUI", workflow_id=7, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("GuiApp", "", 7)
    spec["application"].update({"authentication": "none", "database": "sqlite", "displayName": "Safe GUI"})
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["entities"] = [{
        "id": "Project", "fields": [{"id": "name", "type": "string", "maxLength": 120}],
        "crud": {"enabled": True, "operations": ["list", "read"], "basePath": "/api/projects"},
    }]
    spec["pages"] = [{"id": "home", "title": "Projects @ <script>alert(1)</script>", "root": {
        "id": "root", "type": "layout.stack", "properties": {"gap": "md"}, "children": [
            {"id": "intro", "type": "display.text", "properties": {"text": "Overview <b>safe</b>"}, "children": []},
            {"id": "grid", "type": "layout.grid", "properties": {"columns": {"mobile": 1, "tablet": 2, "desktop": 3}}, "children": [
                {"id": "metric", "type": "display.metric", "properties": {"label": "Projects", "value": 0}, "children": []},
            ]},
            {"id": "projects", "type": "data.table", "properties": {"label": "Projects", "columns": [{"key": "name", "label": "Name"}], "pageSize": 20}, "binding": "entity:Project", "children": []},
        ],
    }}]
    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    first = generate_source_bundle(spec, workflow_ir, target_id="web")
    second = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert first.archive_bytes == second.archive_bytes
    assert first.manifest["phase"] == "E7" and first.manifest["runtime"]["gui"] == "blazor-static-ssr"
    assert len(first.files) == 21
    with zipfile.ZipFile(io.BytesIO(first.archive_bytes)) as archive:
        names = archive.namelist()
        app_source = archive.read(next(name for name in names if name.endswith("/Components/App.razor"))).decode()
        page_source = archive.read(next(name for name in names if "/Components/Pages/" in name)).decode()
        program = archive.read(next(name for name in names if name.endswith("/Program.cs"))).decode()
        javascript = archive.read(next(name for name in names if name.endswith("/generated-ui.js"))).decode()
        css = archive.read(next(name for name in names if name.endswith("/generated-ui.css"))).decode()
        assert "MapRazorComponents<App>()" in program and "UseAntiforgery" in program
        assert "GeneratedEphemeralXmlRepository" in program
        assert '@page "/"' in page_source and '@page "/home"' in page_source
        assert "<script>alert(1)</script>" not in page_source and "&lt;script&gt;alert(1)&lt;/script&gt;" in page_source
        assert "Overview <b>safe</b>" not in page_source and "Overview &lt;b&gt;safe&lt;/b&gt;" in page_source
        assert 'data-entity-url="/api/projects"' in page_source
        assert "textContent" in javascript and "innerHTML" not in javascript
        assert "safe-area-inset-bottom" in css and "--columns-mobile" in css
        assert "Router AppAssembly" in app_source

    blocked = json.loads(json.dumps(spec))
    blocked["application"]["authentication"] = "api-key"
    blocked["pages"][0]["root"]["children"][2]["binding"] = "entity:Project.name"
    blocked["pages"][0]["root"]["children"].append({
        "id": "run", "type": "action.workflow-run", "properties": {"label": "Run", "workflowBinding": "main"},
        "events": {"click": {"action": "workflow-run", "target": "main"}}, "children": [],
    })
    codes = {item.code for item in target_generator_diagnostics(blocked, workflow_ir, target_id="web")}
    assert {"GENERATOR_GUI_TABLE_FIELD_BINDING_UNSUPPORTED", "GENERATOR_GUI_ENDPOINT_MISSING", "GENERATOR_GUI_EVENT_UNSUPPORTED"} <= codes
    assert "GENERATOR_GUI_AUTH_UNAVAILABLE" not in codes


def test_blazor_gui_api_key_session_and_entity_mutations_are_generated_safely():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    workflow_ir = compile_workflow(
        {"nodes": [{"id": "trigger", "type": "trigger", "config": {"mode": "manual"}}], "edges": []},
        name="CRUD GUI", workflow_id=7, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("CrudGui", "", 7)
    spec["application"].update({"authentication": "api-key", "database": "sqlite"})
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["entities"] = [{
        "id": "Project", "fields": [
            {"id": "name", "type": "string", "maxLength": 120},
            {"id": "priority", "type": "integer", "nullable": True},
            {"id": "active", "type": "boolean", "hasDefault": True, "default": True},
            {"id": "metadata", "type": "json", "nullable": True},
        ],
        "crud": {"enabled": True, "operations": ["list", "read", "create", "update", "delete"], "basePath": "/api/projects"},
    }]
    spec["pages"] = [{"id": "home", "title": "Projects", "root": {
        "id": "projects", "type": "data.table", "binding": "entity:Project", "children": [],
        "properties": {
            "label": "Projects", "columns": [{"key": "name", "label": "Name"}], "pageSize": 20,
            "enableCreate": True, "enableUpdate": True, "enableDelete": True,
        },
    }}]
    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    bundle = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert bundle.manifest["phase"] == "E7"
    assert bundle.manifest["generator"]["version"] == "1.0.0"
    assert bundle.manifest["runtime"]["browserAuth"] == "ephemeral-http-only-api-key-session"
    assert len(bundle.files) == 21
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        names = archive.namelist()
        program = archive.read(next(name for name in names if name.endswith("/Program.cs"))).decode()
        api = archive.read(next(name for name in names if name.endswith("/Generated/Api.generated.cs"))).decode()
        app = archive.read(next(name for name in names if name.endswith("/Components/App.razor"))).decode()
        page = archive.read(next(name for name in names if "/Components/Pages/" in name)).decode()
        javascript = archive.read(next(name for name in names if name.endswith("/generated-ui.js"))).decode()
        assert "GeneratedBrowserSessions.Map(app)" in program
        assert "Content-Security-Policy" in program and "frame-ancestors 'none'" in program
        assert "HttpOnly = true" in api and "SameSite = SameSiteMode.Strict" in api
        assert "Sessions[Hash(token)]" in api and "MaxSessions = 1_000" in api
        assert 'request.Headers["X-Requested-With"]' in api and '"GeneratedApp"' in api
        assert "CryptographicOperations.FixedTimeEquals" in api and "Status429TooManyRequests" in api
        assert "IPAddress.IsLoopback" in api and "Status403Forbidden" in api
        assert 'type="password"' in app and 'autocomplete="current-password"' in app
        assert 'data-can-create="true"' in page and 'data-can-update="true"' in page and 'data-can-delete="true"' in page
        assert 'data-field-type="integer"' in page and '<textarea' in page
        assert 'method: updating ? "PATCH" : "POST"' in javascript
        assert 'method: "DELETE"' in javascript and "confirm(\"Delete this item?" in javascript
        assert "textContent" in javascript and "innerHTML" not in javascript

    missing_operation = json.loads(json.dumps(spec))
    missing_operation["entities"][0]["crud"]["operations"].remove("delete")
    codes = {item.code for item in target_generator_diagnostics(missing_operation, workflow_ir, target_id="web")}
    assert "GENERATOR_GUI_ENTITY_MUTATION_UNAVAILABLE" in codes
    unbound = json.loads(json.dumps(spec))
    unbound["pages"][0]["root"].pop("binding")
    codes = {item.code for item in target_generator_diagnostics(unbound, workflow_ir, target_id="web")}
    assert "GENERATOR_GUI_MUTATION_BINDING_REQUIRED" in codes
    public = json.loads(json.dumps(spec))
    public["application"]["authentication"] = "none"
    diagnostics = target_generator_diagnostics(public, workflow_ir, target_id="web")
    assert any(item.code == "GENERATOR_GUI_PUBLIC_MUTATION" and item.severity == "warning" for item in diagnostics)


def test_blazor_workflow_form_resolves_sync_endpoint_and_renders_typed_results_safely():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    workflow_ir = compile_workflow(
        {
            "nodes": [
                {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
                {"id": "output", "type": "output.render", "config": {"name": "result", "value": "{{trigger.message}}"}},
            ],
            "edges": [{"source": "trigger", "target": "output"}],
        },
        name="Workflow form", workflow_id=7, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("WorkflowGui", "", 7)
    spec["application"]["authentication"] = "api-key"
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["apiEndpoints"] = [{
        "id": "run-sync", "method": "POST", "path": "/api/run", "workflowId": 7,
        "mode": "sync", "authentication": "inherit", "timeoutSeconds": 30,
        "requestSchema": {
            "type": "object", "required": ["message", "count", "enabled", "metadata", "items"],
            "properties": {
                "message": {"type": "string", "title": "Message", "description": "Text to process", "minLength": 1, "maxLength": 120},
                "mode": {"type": "string", "enum": ["short", "full"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "optionalFlag": {"type": "boolean"},
                "metadata": {"type": "object"},
                "items": {"type": "array"},
            },
            "additionalProperties": False,
        },
        "responseSchema": {"type": "object", "properties": {"result": {"type": "string"}}},
    }]
    spec["pages"] = [
        {"id": "home", "title": "Run", "root": {
            "id": "run", "type": "action.workflow-run", "children": [],
            "properties": {"label": "Run safely", "workflowBinding": "main", "endpointId": "run-sync", "resultLabel": "Typed result"},
            "events": {"success": {"action": "navigate", "target": "results"}, "error": {"action": "navigate", "target": "errors"}},
        }},
        {"id": "results", "title": "Results"},
        {"id": "errors", "title": "Errors"},
    ]

    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    bundle = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert bundle.manifest["phase"] == "E7"
    assert bundle.manifest["generator"]["version"] == "1.0.0"
    assert bundle.manifest["runtime"]["workflowForms"] == "sync-json-schema-typed-result"
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        pages = [archive.read(name).decode() for name in archive.namelist() if "/Components/Pages/" in name]
        page = next(source for source in pages if 'data-workflow-form' in source)
        javascript = archive.read(next(name for name in archive.namelist() if name.endswith("/generated-ui.js"))).decode()
        css = archive.read(next(name for name in archive.namelist() if name.endswith("/generated-ui.css"))).decode()
        assert 'data-endpoint="/api/run"' in page
        assert 'data-success-route="/results"' in page and 'data-error-route="/errors"' in page
        assert 'name="message"' in page and 'minlength="1"' in page and 'maxlength="120"' in page
        assert '<option value="short">short</option>' in page
        assert 'type="number" step="1"' in page and 'min="1"' in page and 'max="10"' in page
        assert 'data-field-type="boolean" data-required="true" />' in page
        assert '<textarea rows="5"' in page and "Typed result" in page
        assert 'method: "POST"' in javascript and "workflowPayload" in javascript
        assert "document.createElement(\"dl\")" in javascript and "document.createElement(\"table\")" in javascript
        assert "textContent" in javascript and "innerHTML" not in javascript
        assert "window.location.assign(form.dataset.successRoute)" in javascript
        assert "window.location.assign(form.dataset.errorRoute)" in javascript
        assert ".workflow-result dl" in css and "safe-area-inset-bottom" in css

    invalid_cases = [
        ("GENERATOR_GUI_ASYNC_ENDPOINT_UNSUPPORTED", lambda candidate: candidate["apiEndpoints"][0].update({"mode": "async"})),
        ("GENERATOR_GUI_ROUTE_PARAMETER_UNSUPPORTED", lambda candidate: candidate["apiEndpoints"][0].update({"path": "/api/run/{id}"})),
        ("GENERATOR_GUI_FORM_SCHEMA_UNSUPPORTED", lambda candidate: candidate["apiEndpoints"][0]["requestSchema"]["properties"].update({"when": {"type": "null"}})),
        ("GENERATOR_GUI_ENDPOINT_MISSING", lambda candidate: candidate["pages"][0]["root"]["properties"].update({"endpointId": "missing"})),
        ("GENERATOR_GUI_WORKFLOW_BINDING_MISSING", lambda candidate: candidate["pages"][0]["root"]["properties"].update({"workflowBinding": "missing"})),
    ]
    for expected, mutate in invalid_cases:
        candidate = json.loads(json.dumps(spec)); mutate(candidate)
        assert expected in {item.code for item in target_generator_diagnostics(candidate, workflow_ir, target_id="web")}

    ambiguous = json.loads(json.dumps(spec))
    ambiguous["pages"][0]["root"]["properties"]["endpointId"] = ""
    ambiguous["apiEndpoints"].append({**ambiguous["apiEndpoints"][0], "id": "run-again", "path": "/api/run-again"})
    assert "GENERATOR_GUI_ENDPOINT_AMBIGUOUS" in {
        item.code for item in target_generator_diagnostics(ambiguous, workflow_ir, target_id="web")
    }


def test_typed_client_state_generates_safe_consumers_and_state_set_runtime():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    workflow_ir = compile_workflow(
        {
            "nodes": [
                {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
                {"id": "output", "type": "output.render", "config": {"name": "result", "value": "{{trigger.query}}"}},
            ],
            "edges": [{"source": "trigger", "target": "output"}],
        },
        name="State runtime", workflow_id=7, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("StateGui", "", 7)
    spec["application"]["authentication"] = "none"
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["clientState"] = [
        {"id": "query", "type": "string", "initialValue": "seed", "nullable": False},
        {"id": "result", "type": "object", "initialValue": {"status": "idle"}, "nullable": False},
        {"id": "failure", "type": "object", "initialValue": {}, "nullable": False},
    ]
    spec["apiEndpoints"] = [{
        "id": "run", "method": "POST", "path": "/api/run", "workflowId": 7,
        "mode": "sync", "authentication": "inherit", "timeoutSeconds": 30,
        "requestSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "responseSchema": {"type": "object", "properties": {"result": {"type": "string"}}},
    }]
    spec["pages"] = [{"id": "home", "title": "State", "root": {
        "id": "root", "type": "layout.stack", "children": [
            {"id": "query-input", "type": "input.text", "properties": {"label": "Query"}, "binding": "state:query", "events": {"change": {"action": "state-set", "target": "query"}}},
            {"id": "query-copy", "type": "display.text", "properties": {"text": "fallback"}, "binding": "state:query"},
            {"id": "run", "type": "action.workflow-run", "properties": {"label": "Run", "workflowBinding": "main", "endpointId": "run", "resultLabel": "Result"}, "events": {"success": {"action": "state-set", "target": "result"}, "error": {"action": "state-set", "target": "failure"}}},
            {"id": "result", "type": "display.markdown", "properties": {"value": ""}, "binding": "state:result"},
            {"id": "failure", "type": "display.markdown", "properties": {"value": ""}, "binding": "state:failure"},
        ],
    }}]

    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    bundle = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert bundle.manifest["phase"] == "E7"
    assert bundle.manifest["generator"]["version"] == "1.0.0"
    assert bundle.manifest["runtime"]["clientState"] == "browser-memory-typed"
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        page = archive.read(next(name for name in archive.namelist() if "/Components/Pages/" in name)).decode()
        app = archive.read(next(name for name in archive.namelist() if name.endswith("/Components/App.razor"))).decode()
        javascript = archive.read(next(name for name in archive.namelist() if name.endswith("/generated-ui.js"))).decode()
        assert 'data-state-key="query" data-change-state="query"' in page
        assert 'value="seed"' in page and 'data-state-key="result"' in page and '{&quot;status&quot;:&quot;idle&quot;}' not in page
        assert '{"status":"idle"}' in page
        assert 'data-success-state="result" data-error-state="failure"' in page
        assert 'data-client-state-initial data-state-key="result"' in app
        assert 'data-state-value="{&quot;status&quot;:&quot;idle&quot;}"' in app
        assert "const clientState = new Map()" in javascript
        assert "initializeClientState" in javascript and "JSON.parse(item.dataset.stateValue)" in javascript
        assert 'document.querySelectorAll("[data-state-key]")' in javascript
        assert "setClientState(form.dataset.successState, value)" in javascript
        assert 'message: "Request failed."' in javascript and 'message: "Unable to run the workflow."' in javascript
        assert "textContent" in javascript and "innerHTML" not in javascript

    invalid = json.loads(json.dumps(spec))
    invalid["clientState"][1]["type"] = "string"
    codes = {item.code for item in target_generator_diagnostics(invalid, workflow_ir, target_id="web")}
    assert "GENERATOR_GUI_STATE_TYPE_MISMATCH" in codes

    missing_consumer = json.loads(json.dumps(spec))
    missing_consumer["pages"][0]["root"]["children"] = [
        item for item in missing_consumer["pages"][0]["root"]["children"] if item["id"] != "failure"
    ]
    assert "GENERATOR_GUI_STATE_CONSUMER_MISSING" in {
        item.code for item in target_generator_diagnostics(missing_consumer, workflow_ir, target_id="web")
    }

    missing_schema = json.loads(json.dumps(spec))
    missing_schema["apiEndpoints"][0]["responseSchema"] = {}
    assert "GENERATOR_GUI_STATE_RESPONSE_SCHEMA_REQUIRED" in {
        item.code for item in target_generator_diagnostics(missing_schema, workflow_ir, target_id="web")
    }


def test_typed_entity_query_generates_loading_cache_refresh_and_error_runtime():
    from app.application_builder.compiler import default_spec, validate_application_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    spec = default_spec("QueryGui", "", None)
    spec["application"].update({"authentication": "none", "database": "sqlite"})
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["entities"] = [{
        "id": "Project", "displayName": "Projects", "fields": [
            {"id": "name", "type": "string", "maxLength": 120},
            {"id": "active", "type": "boolean"},
        ],
        "crud": {"enabled": True, "operations": ["list"], "basePath": "/api/entities/projects"},
    }]
    spec["queries"] = [{
        "id": "recentProjects", "source": "entity", "entityId": "Project", "limit": 25,
        "autoLoad": False, "cachePolicy": "memory", "staleTimeSeconds": 45, "pagination": "offset",
        "filters": [{"field": "name", "operator": "contains", "value": "active"}],
        "sort": [{"field": "name", "direction": "desc"}],
    }]
    spec["pages"] = [{"id": "home", "title": "Projects", "root": {
        "id": "root", "type": "layout.stack", "children": [{
            "id": "projects", "type": "data.table", "binding": "query:recentProjects",
            "properties": {"label": "Recent projects", "columns": [{"key": "name", "label": "Name"}]},
        }],
    }}]

    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]
    assert not target_generator_diagnostics(spec, None, target_id="web")
    bundle = generate_source_bundle(spec, None, target_id="web")
    assert bundle.manifest["phase"] == "E7"
    assert bundle.manifest["generator"]["version"] == "1.0.0"
    assert bundle.manifest["runtime"]["queries"] == "typed-entity-api-collection-filter-sort-pagination"
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        page = archive.read(next(name for name in archive.namelist() if "/Components/Pages/" in name)).decode()
        javascript = archive.read(next(name for name in archive.namelist() if name.endswith("/generated-ui.js"))).decode()
        entities_source = archive.read(next(name for name in archive.namelist() if name.endswith("/Generated/Entities.generated.cs"))).decode()
        openapi = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith("/openapi.json"))))
        assert 'data-query-id="recentProjects"' in page
        assert 'data-query-cache="memory"' in page and 'data-query-stale-ms="45000"' in page
        assert 'data-query-autoload="false"' in page and 'data-page-size="25"' in page
        assert 'data-query-filters="[{&quot;field&quot;:&quot;name&quot;,&quot;operator&quot;:&quot;contains&quot;,&quot;value&quot;:&quot;active&quot;}]"' in page
        assert 'data-query-sort="[{&quot;direction&quot;:&quot;desc&quot;,&quot;field&quot;:&quot;name&quot;}]"' in page
        assert 'class="query-refresh">Refresh</button>' in page
        assert 'class="query-previous"' in page and 'class="query-next"' in page
        assert "Select Refresh to load data." in page
        assert "const queryCache = new Map()" in javascript and "const queryPending = new Map()" in javascript
        assert "Date.now() - cached.loadedAt <= staleMs" in javascript
        assert 'parameters.set("filter", table.dataset.queryFilters)' in javascript
        assert "requestedOffset" in javascript and "query-previous" in javascript
        assert 'Unable to load data. Select Refresh to try again.' in javascript
        assert "textContent" in javascript and "innerHTML" not in javascript
        assert "TryBuildListQuery" in entities_source and "command.Parameters.AddWithValue(parameter" in entities_source
        assert {item["name"] for item in openapi["paths"]["/api/entities/projects"]["get"]["parameters"]} == {"limit", "offset", "filter", "sort"}

    missing = json.loads(json.dumps(spec)); missing["queries"][0]["entityId"] = "Missing"
    assert "QUERY_ENTITY_MISSING" in {item.code for item in validate_application_spec(missing)}
    unavailable = json.loads(json.dumps(spec)); unavailable["entities"][0]["crud"]["operations"] = ["read"]
    assert "QUERY_ENTITY_LIST_UNAVAILABLE" in {item.code for item in validate_application_spec(unavailable)}
    bad_field = json.loads(json.dumps(spec)); bad_field["pages"][0]["root"]["children"][0]["properties"]["columns"][0]["key"] = "missing"
    assert "BINDING_QUERY_FIELD_MISSING" in {item.code for item in validate_application_spec(bad_field)}
    bad_filter = json.loads(json.dumps(spec)); bad_filter["queries"][0]["filters"] = [{"field": "name", "operator": "gt", "value": 1}]
    assert "QUERY_FILTER_OPERATOR_INVALID" in {item.code for item in validate_application_spec(bad_filter)}
    bad_value = json.loads(json.dumps(spec)); bad_value["queries"][0]["filters"] = [{"field": "active", "operator": "eq", "value": "true"}]
    assert "QUERY_FILTER_VALUE_INVALID" in {item.code for item in validate_application_spec(bad_value)}
    duplicate_sort = json.loads(json.dumps(spec)); duplicate_sort["queries"][0]["sort"] = [{"field": "name", "direction": "asc"}, {"field": "name", "direction": "desc"}]
    assert "QUERY_SORT_FIELD_DUPLICATE" in {item.code for item in validate_application_spec(duplicate_sort)}
    missing_query = json.loads(json.dumps(spec)); missing_query["pages"][0]["root"]["children"][0]["binding"] = "query:missing"
    assert "BINDING_QUERY_MISSING" in {item.code for item in validate_application_spec(missing_query)}


def test_typed_api_query_validates_input_result_and_generates_post_collection_runtime():
    from app.application_builder.compiler import compile_workflow, default_spec, validate_application_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    workflow_ir = compile_workflow(
        {"nodes": [{"id": "trigger", "type": "trigger", "config": {"mode": "manual"}}, {"id": "output", "type": "output.render", "config": {"name": "items", "value": []}}], "edges": [{"source": "trigger", "target": "output"}]},
        name="API collection", workflow_id=8, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("ApiQueryGui", "", 8)
    spec["application"]["authentication"] = "none"
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["apiEndpoints"] = [{
        "id": "listItems", "method": "POST", "path": "/api/items/query", "workflowId": 8, "mode": "sync", "authentication": "inherit",
        "requestSchema": {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"], "additionalProperties": False},
        "responseSchema": {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "rank": {"type": "integer"}}, "required": ["name", "rank"]}}}, "required": ["results"]},
    }]
    spec["queries"] = [{"id": "items", "source": "api", "endpointId": "listItems", "input": {"category": "recent"}, "resultPath": "results", "filters": [], "sort": [], "pagination": "none", "limit": 50, "autoLoad": True, "cachePolicy": "network-only", "staleTimeSeconds": 0}]
    spec["pages"] = [{"id": "home", "title": "Items", "root": {"id": "root", "type": "layout.stack", "children": [{"id": "items", "type": "data.table", "binding": "query:items", "properties": {"label": "Items", "columns": []}}]}}]

    assert not [item for item in validate_application_spec(spec) if item.severity == "error"]
    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    bundle = generate_source_bundle(spec, workflow_ir, target_id="web")
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        page = archive.read(next(name for name in archive.namelist() if "/Components/Pages/" in name)).decode()
        javascript = archive.read(next(name for name in archive.namelist() if name.endswith("/generated-ui.js"))).decode()
        assert 'data-query-url="/api/items/query"' in page and 'data-query-method="POST"' in page
        assert 'data-query-input="{&quot;category&quot;:&quot;recent&quot;}"' in page and 'data-query-result-path="results"' in page
        assert '<th scope="col">name</th>' in page and '<th scope="col">rank</th>' in page and "query-pagination" not in page
        assert 'body: table.dataset.queryInput || "{}"' in javascript

    invalid_input = json.loads(json.dumps(spec)); invalid_input["queries"][0]["input"] = {}
    assert "QUERY_API_INPUT_INVALID" in {item.code for item in validate_application_spec(invalid_input)}
    invalid_result = json.loads(json.dumps(spec)); invalid_result["queries"][0]["resultPath"] = "missing"
    assert "QUERY_API_RESULT_NOT_COLLECTION" in {item.code for item in validate_application_spec(invalid_result)}
    invalid_options = json.loads(json.dumps(spec)); invalid_options["queries"][0]["pagination"] = "offset"
    assert "QUERY_API_COLLECTION_OPTIONS_UNSUPPORTED" in {item.code for item in validate_application_spec(invalid_options)}
    mutation = json.loads(json.dumps(spec)); mutation["pages"][0]["root"]["children"][0]["properties"]["enableDelete"] = True
    assert "BINDING_QUERY_MUTATION_UNSUPPORTED" in {item.code for item in validate_application_spec(mutation)}


def test_csharp_console_source_generator_is_deterministic_safe_and_manifested():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import (
        SourceGenerationError, generate_csharp_console, generator_diagnostics,
    )

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual", "inputs": [{"key": "message", "type": "text"}]}},
            {"id": "format", "type": "string.op", "config": {"op": "upper", "text": "{{trigger.message}}"}},
            {"id": "output", "type": "output.render", "config": {"name": "answer", "renderer": "text", "value": "{{format.result}}"}},
        ],
        "edges": [{"source": "trigger", "target": "format"}, {"source": "format", "target": "output"}],
    }
    workflow_ir = compile_workflow(definition, name="Generated", workflow_id=7, target="csharp").model_dump(by_alias=True)
    spec = default_spec("My-App", "deterministic", 7)
    spec["targets"] = [{"id": "console", "platforms": ["linux", "windows"], "framework": "csharp-console"}]
    first = generate_csharp_console(spec, workflow_ir, target_id="console")
    second = generate_csharp_console(spec, workflow_ir, target_id="console")
    assert first.archive_bytes == second.archive_bytes
    assert first.archive_checksum == second.archive_checksum
    assert first.manifest["generator"] == {"id": "controldeck.csharp-console", "version": "1.4.0"}
    assert first.manifest["input"]["targetId"] == "console"
    assert len(first.manifest["sourceChecksum"]) == 64
    with zipfile.ZipFile(io.BytesIO(first.archive_bytes)) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        assert "MyApp/.controldeck/generation-manifest.json" in names
        generated = archive.read("MyApp/src/MyApp/Generated/Workflow.generated.cs").decode()
        assert generated.index('new GeneratedNode("trigger"') < generated.index('new GeneratedNode("format"') < generated.index('new GeneratedNode("output"')
        assert "shell=True" not in generated and "ControlDeck" not in generated
        assert json.loads(archive.read("MyApp/.controldeck/generation-manifest.json"))["sourceChecksum"] == first.source_checksum

    literal_secret = json.loads(json.dumps(definition))
    literal_secret["nodes"][-1]["config"]["token"] = "must-not-leak"
    literal_ir = compile_workflow(literal_secret, name="Literal secret", target="csharp").model_dump(by_alias=True)
    assert "WORKFLOW_SECRET_LITERAL_FORBIDDEN" in {item["code"] for item in literal_ir["diagnostics"]}
    assert "must-not-leak" not in json.dumps(literal_ir)

    unsupported = _workflow_definition()
    unsupported["nodes"].insert(1, {"id": "llm", "type": "llm.chat", "config": {"prompt": "hello"}})
    unsupported["edges"] = [{"source": "trigger", "target": "llm"}, {"source": "llm", "target": "output"}]
    unsupported_ir = compile_workflow(unsupported, name="Blocked", target="csharp").model_dump(by_alias=True)
    codes = {item.code for item in generator_diagnostics(spec, unsupported_ir, target_id="console")}
    assert "GENERATOR_NODE_UNSUPPORTED" in codes
    try:
        generate_csharp_console(spec, unsupported_ir, target_id="console")
    except SourceGenerationError as exc:
        assert "API_TOKEN" not in json.dumps([item.model_dump() for item in exc.diagnostics])
    else:
        raise AssertionError("unsupported workflow must not generate source")
    unsafe_name = json.loads(json.dumps(spec))
    unsafe_name["application"]["name"] = "../../class/9-app"
    safe_bundle = generate_csharp_console(unsafe_name, workflow_ir, target_id="console")
    with zipfile.ZipFile(io.BytesIO(safe_bundle.archive_bytes)) as archive:
        assert all(not name.startswith(("/", "..")) and "/../" not in name for name in archive.namelist())
    marker_name = json.loads(json.dumps(spec))
    marker_name["application"]["name"] = "__CD_NODES__"
    marker_bundle = generate_csharp_console(marker_name, workflow_ir, target_id="console")
    with zipfile.ZipFile(io.BytesIO(marker_bundle.archive_bytes)) as archive:
        marker_source = archive.read("__CD_NODES__/src/__CD_NODES__/Generated/Workflow.generated.cs").decode()
        assert "namespace __CD_NODES__.Generated;" in marker_source
        assert 'new GeneratedNode("trigger"' in marker_source
    oversized_ir = json.loads(json.dumps(workflow_ir))
    oversized_ir["nodes"] = oversized_ir["nodes"] * 251
    assert "GENERATOR_NODE_LIMIT_EXCEEDED" in {
        item.code for item in generator_diagnostics(spec, oversized_ir, target_id="console")
    }


def test_generated_secret_http_and_file_boundaries_are_typed_and_value_free():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_csharp_console, generator_diagnostics

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "request", "type": "http.request", "config": {
                "method": "POST", "url": "https://example.com/api/items",
                "headers": {"Authorization": "Bearer {{secrets.API_TOKEN}}"},
                "body": '{"credential":"{{secrets.BODY_SECRET}}","message":"{{trigger.message}}"}',
                "expected_status": 200, "node_timeout": 30,
            }},
            {"id": "write", "type": "file.write", "config": {
                "path": "results/latest.json", "content": "{{request.body}}", "append": False,
            }},
            {"id": "output", "type": "output.render", "config": {"name": "result", "value": "{{write.path}}"}},
        ],
        "edges": [
            {"source": "trigger", "target": "request"},
            {"source": "request", "target": "write"},
            {"source": "write", "target": "output"},
        ],
    }
    workflow_ir = compile_workflow(definition, name="Bounded side effects", workflow_id=9, target="csharp").model_dump(by_alias=True)
    request = next(node for node in workflow_ir["nodes"] if node["id"] == "request")
    assert request["config"]["headers"]["Authorization"] == "Bearer {{secrets.SECRET_001}}"
    assert "{{secrets.SECRET_002}}" in request["config"]["body"]
    assert [item["name"] for item in workflow_ir["required_secrets"]] == ["API_TOKEN", "BODY_SECRET"]

    spec = default_spec("BoundedRuntime", "", 9)
    spec["targets"] = [{"id": "console", "platforms": ["linux", "windows"], "framework": "csharp-console"}]
    assert not generator_diagnostics(spec, workflow_ir, target_id="console")
    bundle = generate_csharp_console(spec, workflow_ir, target_id="console")
    assert bundle.manifest["phase"] == "B2.5"
    assert bundle.manifest["runtime"] == {
        "secretInjection": "environment-alias-redacted-output",
        "secretEnvironment": ["CONTROLDECK_SECRET_001", "CONTROLDECK_SECRET_002"],
        "sideEffects": ["external", "write"],
        "auditRoot": "CONTROLDECK_APP_AUDIT_ROOT",
        "fileRoot": "CONTROLDECK_APP_WORK_ROOT",
    }
    assert b"API_TOKEN" not in bundle.archive_bytes and b"BODY_SECRET" not in bundle.archive_bytes
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        workflow = archive.read(next(name for name in archive.namelist() if name.endswith("/Generated/Workflow.generated.cs"))).decode()
        self_test = archive.read(next(name for name in archive.namelist() if name.endswith(".GeneratedTests/Program.cs"))).decode()
        assert "AllowAutoRedirect = false" in workflow and "Generated HTTP requests require HTTPS" in workflow
        assert "CONTROLDECK_APP_WORK_ROOT" in workflow and "Generated file path escaped its work root" in workflow
        assert ".controldeck-side-effects.audit.jsonl" in workflow and "GeneratedSecrets.Redact" in workflow
        assert "ValidateGeneratedSource" in self_test and "RunAsync" not in self_test

    unsafe_secret = json.loads(json.dumps(definition))
    unsafe_secret["nodes"][-1]["config"]["value"] = "{{secrets.API_TOKEN}}"
    unsafe_ir = compile_workflow(unsafe_secret, name="Unsafe secret", target="csharp").model_dump(by_alias=True)
    assert "GENERATOR_SECRET_POSITION_UNSUPPORTED" in {
        item.code for item in generator_diagnostics(spec, unsafe_ir, target_id="console")
    }
    unsafe_http = json.loads(json.dumps(definition)); unsafe_http["nodes"][1]["config"]["url"] = "http://example.com/items"
    unsafe_http_ir = compile_workflow(unsafe_http, name="Unsafe HTTP", target="csharp").model_dump(by_alias=True)
    assert "GENERATOR_HTTP_URL_UNSAFE" in {
        item.code for item in generator_diagnostics(spec, unsafe_http_ir, target_id="console")
    }
    unsafe_path = json.loads(json.dumps(definition)); unsafe_path["nodes"][2]["config"]["path"] = "/etc/passwd"
    unsafe_path_ir = compile_workflow(unsafe_path, name="Unsafe path", target="csharp").model_dump(by_alias=True)
    assert "GENERATOR_FILE_PATH_UNSAFE" in {
        item.code for item in generator_diagnostics(spec, unsafe_path_ir, target_id="console")
    }


def test_aspnet_secret_side_effect_workflow_requires_authenticated_non_anonymous_api():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_source_bundle, target_generator_diagnostics

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "request", "type": "http.request", "config": {
                "method": "POST", "url": "https://example.com/api/items",
                "headers": {"Authorization": "Bearer {{secrets.API_TOKEN}}"},
                "body": '{"credential":"{{secrets.BODY_SECRET}}"}',
                "expected_status": 200,
            }},
            {"id": "output", "type": "output.render", "config": {
                "name": "result", "value": "{{request.body}}",
            }},
        ],
        "edges": [{"source": "trigger", "target": "request"}, {"source": "request", "target": "output"}],
    }
    workflow_ir = compile_workflow(
        definition, name="Authenticated side effect", workflow_id=10, target="csharp",
    ).model_dump(by_alias=True)
    spec = default_spec("AuthenticatedSideEffect", "", 10)
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux", "windows"], "framework": "aspnet-blazor"}]
    spec["apiEndpoints"] = [{
        "id": "run", "method": "POST", "path": "/api/run", "workflowId": 10,
        "mode": "sync", "authentication": "inherit", "timeoutSeconds": 30,
    }]

    public_spec = json.loads(json.dumps(spec))
    public_spec["application"]["authentication"] = "none"
    assert "GENERATOR_SIDE_EFFECT_AUTH_REQUIRED" in {
        item.code for item in target_generator_diagnostics(public_spec, workflow_ir, target_id="web")
    }

    anonymous_spec = json.loads(json.dumps(spec))
    anonymous_spec["application"]["authentication"] = "api-key"
    anonymous_spec["apiEndpoints"][0]["authentication"] = "anonymous"
    assert "GENERATOR_SIDE_EFFECT_ANONYMOUS_FORBIDDEN" in {
        item.code for item in target_generator_diagnostics(anonymous_spec, workflow_ir, target_id="web")
    }

    spec["application"]["authentication"] = "api-key"
    assert not target_generator_diagnostics(spec, workflow_ir, target_id="web")
    bundle = generate_source_bundle(spec, workflow_ir, target_id="web")
    assert bundle.manifest["phase"] == "E7"
    assert bundle.manifest["runtime"]["secretInjection"] == "environment-alias-redacted-output"
    assert bundle.manifest["runtime"]["secretEnvironment"] == [
        "CONTROLDECK_SECRET_001", "CONTROLDECK_SECRET_002",
    ]
    assert bundle.manifest["runtime"]["workflowSideEffects"] == ["external"]
    assert b"API_TOKEN" not in bundle.archive_bytes and b"BODY_SECRET" not in bundle.archive_bytes
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        api_source = archive.read(next(
            name for name in archive.namelist() if name.endswith("/Generated/Api.generated.cs")
        )).decode()
        workflow_source = archive.read(next(
            name for name in archive.namelist() if name.endswith("/Generated/Workflow.generated.cs")
        )).decode()
        assert "GeneratedApiKey.IsAuthorized(request, anonymous: false)" in api_source
        assert 'Environment.GetEnvironmentVariable($"CONTROLDECK_SECRET_{index:000}")' in workflow_source
        assert "{{secrets.SECRET_001}}" in workflow_source
        assert "GeneratedSecrets.Redact" in workflow_source


def test_csharp_console_generator_projects_branch_merge_and_execution_policy():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_csharp_console, generator_diagnostics

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "condition", "type": "condition.if", "config": {"left": "{{trigger.message}}", "op": "eq", "right": "yes"}},
            {"id": "accepted", "type": "var.set", "config": {"name": "choice", "value": "accepted", "retry_count": 2, "node_timeout": 0.5, "on_error": "continue"}},
            {"id": "rejected", "type": "var.set", "config": {"name": "choice", "value": "rejected"}},
            {"id": "merge", "type": "control.merge", "config": {"mode": "wait_all"}},
            {"id": "output", "type": "output.render", "config": {"name": "answer", "value": "{{merge.value}}"}},
        ],
        "edges": [
            {"source": "trigger", "target": "condition"},
            {"source": "condition", "sourceHandle": "true", "target": "accepted"},
            {"source": "condition", "sourceHandle": "false", "target": "rejected"},
            {"source": "accepted", "target": "merge"}, {"source": "rejected", "target": "merge"},
            {"source": "merge", "target": "output"},
        ],
    }
    workflow_ir = compile_workflow(definition, name="Branch", target="csharp").model_dump(by_alias=True)
    spec = default_spec("BranchApp", "", None)
    spec["targets"] = [{"id": "console", "platforms": ["linux"], "framework": "csharp-console"}]
    assert not generator_diagnostics(spec, workflow_ir, target_id="console")
    bundle = generate_csharp_console(spec, workflow_ir, target_id="console")
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        source_path = next(name for name in archive.namelist() if name.endswith("/Generated/Workflow.generated.cs"))
        source = archive.read(source_path).decode()
    assert 'new GeneratedEdge("condition", "accepted", "true")' in source
    assert 'new GeneratedEdge("condition", "rejected", "false")' in source
    assert 'new GeneratedNode("accepted", "var.set"' in source
    assert '2, 5.0, 0.5, "continue", "first")' in source
    assert "GeneratedStatus.TimedOut" in source and "timeoutRoute" in source
    assert 'mergeMode is "wait_all" or "collect"' in source
    assert "SemaphoreSlim(4, 4)" in source
    invalid = json.loads(json.dumps(workflow_ir))
    invalid["edges"][1]["branch"] = "body"
    assert "GENERATOR_BRANCH_VALUE_UNSUPPORTED" in {
        item.code for item in generator_diagnostics(spec, invalid, target_id="console")
    }


def test_csharp_console_generator_projects_named_variables_and_pure_data_nodes():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_csharp_console, generator_diagnostics

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "parse", "type": "data.transform", "config": {
                "operation": "json_parse", "input": "{{trigger.message}}", "output_var": "parsed",
            }},
            {"id": "filter", "type": "data.filter", "config": {
                "input": "{{vars.parsed.value}}", "field": "active", "operator": "equals", "value": True,
                "unique_by": "id", "sort_by": "score", "sort_order": "desc", "limit": 2,
                "output_var": "filtered",
            }},
            {"id": "aggregate", "type": "data.aggregate", "config": {
                "input": "{{vars.filtered.items}}", "operation": "sum", "field": "score", "group_by": "team",
                "output_var": "totals",
            }},
            {"id": "template", "type": "data.template", "config": {
                "data": "{{vars.totals.groups}}", "template": "{{data.0.value}}", "output_format": "text",
            }},
            {"id": "output", "type": "output.render", "config": {"name": "answer", "value": "{{template.text}}"}},
        ],
        "edges": [
            {"source": "trigger", "target": "parse"}, {"source": "parse", "target": "filter"},
            {"source": "filter", "target": "aggregate"}, {"source": "aggregate", "target": "template"},
            {"source": "template", "target": "output"},
        ],
    }
    workflow_ir = compile_workflow(definition, name="Data", target="csharp").model_dump(by_alias=True)
    spec = default_spec("DataApp", "", None)
    spec["targets"] = [{"id": "console", "platforms": ["linux"], "framework": "csharp-console"}]
    assert not generator_diagnostics(spec, workflow_ir, target_id="console")
    bundle = generate_csharp_console(spec, workflow_ir, target_id="console")
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        source_path = next(name for name in archive.namelist() if name.endswith("/Generated/Workflow.generated.cs"))
        source = archive.read(source_path).decode()
    assert '\\"output_var\\":\\"parsed\\"' in source
    assert 'parts[0] == "vars"' in source
    assert 'case "data.transform"' in source and 'case "data.template"' in source
    assert 'case "data.filter"' in source and 'case "data.aggregate"' in source
    assert "MaxDataBytes = 2 * 1024 * 1024" in source
    assert "MaxDataItems = 10_000" in source
    assert "Canonical(DataPath(item, uniqueBy))" in source

    unsupported = json.loads(json.dumps(workflow_ir))
    unsupported["nodes"][1]["config"]["operation"] = "schema_validate"
    issues = generator_diagnostics(spec, unsupported, target_id="console")
    assert any(item.code == "GENERATOR_NODE_CONFIG_UNSUPPORTED" and item.path.endswith("config.operation") for item in issues)
    invalid_sort = json.loads(json.dumps(workflow_ir))
    invalid_sort["nodes"][2]["config"]["sort_order"] = "random"
    assert "GENERATOR_NODE_CONFIG_UNSUPPORTED" in {
        item.code for item in generator_diagnostics(spec, invalid_sort, target_id="console")
    }


def test_csharp_console_generator_projects_nested_loop_runtime_and_limits():
    from app.application_builder.compiler import compile_workflow, default_spec
    from app.application_builder.source_generator import generate_csharp_console, generator_diagnostics

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "loop", "type": "control.loop", "config": {
                "mode": "foreach", "items": '[{"name":"a"},{"name":"b"},{"name":"c"}]',
                "parallel": 3, "output_var": "completed_loop",
            }},
            {"id": "body", "type": "data.template", "config": {
                "data": "{{loop.item}}", "template": "{{loop.index}}:{{data.name}}", "output_format": "text",
                "output_var": "last_item",
            }},
            {"id": "capture", "type": "var.set", "config": {"name": "captured", "value": "{{vars.last_item.text}}"}},
            {"id": "output", "type": "output.render", "config": {
                "name": "answer", "value": "{{capture.value}}/{{vars.completed_loop.total}}",
            }},
        ],
        "edges": [
            {"source": "trigger", "target": "loop"},
            {"source": "loop", "sourceHandle": "body", "target": "body"},
            {"source": "body", "target": "capture"},
            {"source": "loop", "sourceHandle": "done", "target": "output"},
        ],
    }
    workflow_ir = compile_workflow(definition, name="Loop", target="csharp").model_dump(by_alias=True)
    spec = default_spec("LoopApp", "", None)
    spec["targets"] = [{"id": "console", "platforms": ["linux"], "framework": "csharp-console"}]
    assert not generator_diagnostics(spec, workflow_ir, target_id="console")
    bundle = generate_csharp_console(spec, workflow_ir, target_id="console")
    with zipfile.ZipFile(io.BytesIO(bundle.archive_bytes)) as archive:
        source_path = next(name for name in archive.namelist() if name.endswith("/Generated/Workflow.generated.cs"))
        source = archive.read(source_path).decode()
    assert 'new GeneratedEdge("loop", "body", "body")' in source
    assert 'new GeneratedEdge("loop", "output", "done")' in source
    assert 'node.Type == "control.loop"' in source
    assert "ExecuteLoopAsync" in source and "RunIterationAsync" in source
    assert "Math.Clamp(GeneratedNodes.Integer(loop.Config[\"parallel\"], 1), 1, 5)" in source
    assert "if (items.Count > 100)" in source
    assert "parentVariables.Clear()" in source
    assert 'edge.Branch == "body") continue' in source

    invalid_limit = json.loads(json.dumps(workflow_ir))
    invalid_limit["nodes"][1]["config"]["parallel"] = "many"
    assert any(
        item.code == "GENERATOR_NODE_CONFIG_UNSUPPORTED" and item.path.endswith("config.parallel")
        for item in generator_diagnostics(spec, invalid_limit, target_id="console")
    )
    invalid_branch = json.loads(json.dumps(workflow_ir))
    invalid_branch["edges"][2]["branch"] = "body"
    assert "GENERATOR_BRANCH_VALUE_UNSUPPORTED" in {
        item.code for item in generator_diagnostics(spec, invalid_branch, target_id="console")
    }


def test_csharp_console_source_preview_download_and_audit(admin_client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual", "inputs": [{"key": "message", "type": "text"}]}},
            {"id": "output", "type": "output.render", "config": {"name": "answer", "renderer": "text", "value": "{{trigger.message}}"}},
        ],
        "edges": [{"source": "trigger", "target": "output"}],
    }
    workflow = admin_client.post(
        "/api/v1/workflows", json={"name": "B2 source", "definition": definition}, headers=CSRF_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]
    created = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/application-projects",
        json={"source": "draft", "name": "B2 Generated App"}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201
    project = created.json()
    spec = project["spec"]
    spec["targets"] = [{"id": "console", "platforms": ["linux"], "framework": "csharp-console"}]
    updated = admin_client.patch(
        f"/api/v1/application-projects/{project['id']}", json={"spec": spec}, headers=CSRF_HEADERS,
    )
    assert updated.status_code == 200
    preview = admin_client.get(f"/api/v1/application-projects/{project['id']}/source-preview?target_id=console")
    assert preview.status_code == 200 and preview.json()["ready"] is True
    assert preview.json()["deterministic"] is True
    assert preview.json()["sideEffects"] == {
        "executor": False, "network": False, "subprocess": False,
        "filesystemWrite": False, "secretResolution": False,
    }
    first = admin_client.post(
        f"/api/v1/application-projects/{project['id']}/source-archive",
        json={"targetId": "console"}, headers=CSRF_HEADERS,
    )
    second = admin_client.post(
        f"/api/v1/application-projects/{project['id']}/source-archive",
        json={"targetId": "console"}, headers=CSRF_HEADERS,
    )
    assert first.status_code == 200 and first.headers["content-type"] == "application/zip"
    assert first.content == second.content
    assert first.headers["x-controldeck-source-sha256"] == preview.json()["archiveChecksum"]
    assert 'filename="B2GeneratedApp-source.zip"' in first.headers["content-disposition"]
    with SessionLocal() as db:
        records = db.execute(select(AuditLog).where(
            AuditLog.action == "application_project.source_generate",
            AuditLog.resource_id == str(project["id"]),
        )).scalars().all()
        assert len(records) == 2
        assert "source_checksum" in records[0].metadata_json
    assert admin_client.delete(f"/api/v1/application-projects/{project['id']}", headers=CSRF_HEADERS).status_code == 204
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_workflow_application_is_auto_composed_from_contract_and_source_ready(admin_client):
    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual", "inputs": [
                {"key": "message", "label": "Message", "type": "text", "required": True},
                {"key": "count", "label": "Count", "type": "number"},
                {"key": "enabled", "label": "Enabled", "type": "boolean"},
            ]}},
            {"id": "output", "type": "output.render", "config": {
                "name": "answer", "renderer": "text", "value": "{{trigger.message}}",
            }},
        ],
        "edges": [{"source": "trigger", "target": "output"}],
    }
    workflow = admin_client.post(
        "/api/v1/workflows", json={"name": "Auto app contract", "description": "Run this workflow", "definition": definition}, headers=CSRF_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]
    created = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/application-projects",
        json={"source": "draft", "name": "Automatic Workflow App"}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    project = created.json()
    spec = project["spec"]
    assert spec["pages"][0]["root"]["children"][2]["children"][0]["type"] == "action.workflow-run"
    endpoint = spec["apiEndpoints"][0]
    assert endpoint["mode"] == "sync"
    assert list(endpoint["requestSchema"]["properties"]) == ["message", "count", "enabled"]
    assert list(endpoint["responseSchema"]["properties"]) == ["answer"]
    assert spec["application"]["authentication"] == "api-key"
    advisor = spec["xAppAdvisor"]
    assert advisor["status"] == "ready" and advisor["strategy"] == "workflow-contract"
    assert [(item["name"], item["control"]) for item in advisor["inputs"]] == [
        ("message", "text"), ("count", "number"), ("enabled", "boolean"),
    ]
    validation = admin_client.post(
        "/api/v1/application-builder/validate",
        json={"spec": spec, "workflow_id": workflow_id, "target": "csharp"}, headers=CSRF_HEADERS,
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True, validation.json()["diagnostics"]
    preview = admin_client.get(f"/api/v1/application-projects/{project['id']}/source-preview?target_id=web")
    assert preview.status_code == 200, preview.text
    assert preview.json()["ready"] is True
    assert preview.json()["manifest"]["runtime"]["workflowForms"] == "sync-json-schema-typed-result"


def test_aspnet_source_preview_download_and_manifest(admin_client):
    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "output", "type": "output.render", "config": {"name": "answer", "value": "{{trigger.message}}"}},
        ],
        "edges": [{"source": "trigger", "target": "output"}],
    }
    workflow = admin_client.post(
        "/api/v1/workflows", json={"name": "C1 API source", "definition": definition}, headers=CSRF_HEADERS,
    )
    workflow_id = workflow.json()["id"]
    created = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/application-projects",
        json={"source": "draft", "name": "C1 API App"}, headers=CSRF_HEADERS,
    )
    project = created.json()
    spec = project["spec"]
    spec["application"]["authentication"] = "api-key"
    spec["targets"] = [{"id": "web", "platforms": ["web", "linux"], "framework": "aspnet-blazor"}]
    spec["apiEndpoints"] = [{
        "id": "run", "method": "POST", "path": "/api/run", "workflowId": workflow_id,
        "mode": "async", "authentication": "inherit", "timeoutSeconds": 30,
    }]
    # This test covers an API-only async export; remove the auto-composed sync GUI.
    spec["pages"] = []
    spec["navigation"] = {"type": "sidebar", "items": []}
    updated = admin_client.patch(
        f"/api/v1/application-projects/{project['id']}", json={"spec": spec}, headers=CSRF_HEADERS,
    )
    assert updated.status_code == 200, updated.text
    preview = admin_client.get(f"/api/v1/application-projects/{project['id']}/source-preview?target_id=web")
    payload = preview.json()
    assert preview.status_code == 200 and payload["ready"] is True
    assert payload["phase"] == "E7" and len(payload["files"]) == 15
    assert payload["generator"] == {"id": "controldeck.aspnet-api", "version": "1.0.0"}
    assert payload["manifest"]["input"]["framework"] == "aspnet-blazor"
    archive = admin_client.post(
        f"/api/v1/application-projects/{project['id']}/source-archive",
        json={"targetId": "web"}, headers=CSRF_HEADERS,
    )
    assert archive.status_code == 200
    assert 'filename="C1ApiApp-aspnet-source.zip"' in archive.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(archive.content)) as source:
        assert any(name.endswith("/Generated/Api.generated.cs") for name in source.namelist())
        assert any(name.endswith("/openapi.json") for name in source.namelist())
        assert "mcr.microsoft.com/dotnet/aspnet:8.0" in source.read(next(name for name in source.namelist() if name.endswith("/Dockerfile"))).decode()
    assert admin_client.delete(f"/api/v1/application-projects/{project['id']}", headers=CSRF_HEADERS).status_code == 204
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def _component_spec():
    from app.application_builder.compiler import default_spec

    spec = default_spec("PatchApp", "", None)
    spec["pages"] = [{"id": "home", "title": "Home", "root": {
        "id": "root", "type": "layout.stack", "locked": {}, "children": [
            {"id": "title", "type": "display.text", "properties": {"text": "Before"}, "locked": {}},
            {"id": "card", "type": "layout.card", "locked": {}, "children": []},
        ],
    }}]
    return spec


def test_application_patch_preview_applies_move_and_preserves_input():
    from app.application_builder.patch_service import preview_patches, spec_checksum
    from app.schemas.application_builder import ApplicationPatchOperation

    spec = _component_spec()
    before = json.dumps(spec, sort_keys=True)
    operations = [
        ApplicationPatchOperation(op="replace", path="/pages/0/root/children/0/properties/text", value="After"),
        ApplicationPatchOperation.model_validate({
            "op": "move", "from": "/pages/0/root/children/0", "path": "/pages/0/root/children/0/children/-",
        }),
    ]
    result = preview_patches(spec, operations)
    assert result["valid"] is True
    assert result["baseChecksum"] == spec_checksum(spec)
    assert result["resultChecksum"] != result["baseChecksum"]
    assert result["patchedSpec"]["pages"][0]["root"]["children"][0]["children"][0]["properties"]["text"] == "After"
    assert json.dumps(spec, sort_keys=True) == before


def test_application_patch_rejects_locks_scope_and_invalid_result():
    from app.application_builder.patch_service import preview_patches
    from app.schemas.application_builder import ApplicationPatchOperation

    spec = _component_spec()
    spec["pages"][0]["root"]["children"][0]["locked"] = {"content": True}
    locked = preview_patches(spec, [ApplicationPatchOperation(
        op="replace", path="/pages/0/root/children/0/properties/text", value="Blocked",
    )])
    assert locked["valid"] is False
    assert locked["diagnostics"][0]["code"] == "PATCH_LOCK_VIOLATION"
    spec["pages"][0]["root"]["children"][0]["locked"] = {"style": True}
    style_bypass = preview_patches(spec, [ApplicationPatchOperation(
        op="replace", path="/pages/0/root/children/0/properties", value={"text": "Same", "color": "danger"},
    )])
    assert style_bypass["diagnostics"][0]["code"] == "PATCH_LOCK_VIOLATION"
    spec["pages"][0]["root"]["children"][0]["locked"] = {"binding": True}
    binding_bypass = preview_patches(spec, [ApplicationPatchOperation(
        op="replace", path="/pages/0/root/children/0", value={
            "id": "title", "type": "display.text", "binding": "constant:changed", "properties": {"text": "Before"},
        },
    )])
    assert binding_bypass["diagnostics"][0]["code"] == "PATCH_LOCK_VIOLATION"
    forbidden = preview_patches(spec, [ApplicationPatchOperation(op="add", path="/__proto__/polluted", value=True)])
    assert forbidden["diagnostics"][0]["code"] == "PATCH_PATH_FORBIDDEN"
    secret = preview_patches(spec, [ApplicationPatchOperation(op="add", path="/application/apiKey", value="literal-secret")])
    assert secret["valid"] is False
    assert any(item["code"] == "SECRET_LITERAL_FORBIDDEN" for item in secret["diagnostics"])


def test_application_builder_schema_capability_validate_and_crud(admin_client, monkeypatch):
    from app.workflows import nodes

    schema = admin_client.get("/api/v1/application-builder/schema")
    assert schema.status_code == 200 and schema.json()["schemaVersion"] == 1
    assert "instruction" in schema.json()["designProposalRequestSchema"]["properties"]
    assert "platforms" in schema.json()["platformAdvisorRequestSchema"]["properties"]
    assert "spec" in schema.json()["preflightRequestSchema"]["properties"]
    assert "targetId" in schema.json()["sourceRequestSchema"]["properties"]
    semantic = schema.json()["semanticComponents"]
    assert semantic["schemaVersion"] == 11
    assert any(item["type"] == "layout.stack" and item["container"] for item in semantic["components"])
    assert any(item["type"] == "chart.line" for item in semantic["components"])
    assert len(semantic["presets"]) == 8 and len(semantic["composites"]) == 5 and len(semantic["patterns"]) == 4
    capabilities = admin_client.get("/api/v1/application-builder/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["generationAvailable"] is True
    assert capabilities.json()["buildAvailable"] is False
    assert capabilities.json()["designProposalAvailable"] is True
    assert any(item["id"] == "avalonia" and item["status"] == "planned" for item in capabilities.json()["frameworks"])
    http_capability = next(item for item in capabilities.json()["nodes"] if item["type"] == "http.request")["targets"]["csharp"]
    assert http_capability["support"] == "native"
    assert http_capability["planned_support"] == "native"
    assert http_capability["source_available"] is True
    assert capabilities.json()["phase"] == "B2.5"
    console = next(item for item in capabilities.json()["frameworks"] if item["id"] == "csharp-console")
    assert console["matrix"]["source"] == "available"
    generated_nodes = {
        item["type"]: item["targets"]["csharp"] for item in capabilities.json()["nodes"]
    }
    assert generated_nodes["condition.if"]["support"] == "native"
    assert generated_nodes["control.merge"]["generator"] == "controldeck.csharp-console/1.4.0"
    assert generated_nodes["control.loop"]["generator"] == "controldeck.csharp-console/1.4.0"
    assert generated_nodes["data.transform"]["support"] == "native"
    assert generated_nodes["data.aggregate"]["generator"] == "controldeck.csharp-console/1.4.0"
    assert capabilities.json()["frameworks"][0]["matrix"]["spec"] == "available"
    metadata = admin_client.get("/api/v1/workflows/node-catalog").json()
    http_meta = next(item for item in metadata if item["type"] == "http.request")
    assert http_meta["metadata_version"] == 3
    assert http_meta["config_schema"]["retry_count"]["recommended"] == 1
    assert http_meta["ui_hints"]["variable_picker"] is True

    api_definition = _workflow_definition()
    api_definition["nodes"][1]["config"]["headers"] = ""
    workflow = admin_client.post(
        "/api/v1/workflows", json={"name": "App source", "definition": api_definition}, headers=CSRF_HEADERS,
    )
    workflow_id = workflow.json()["id"]
    published = admin_client.post(f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS)
    assert published.status_code == 200
    published_project = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/application-projects",
        json={"source": "published", "name": "Published App"}, headers=CSRF_HEADERS,
    )
    assert published_project.status_code == 201
    published_binding = published_project.json()["spec"]["workflows"][0]
    assert published_binding["source"] == "published"
    assert published_binding["workflowVersionId"] == published.json()["version_id"]
    created = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/application-projects",
        json={"source": "draft"}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["workflow_id"] == workflow_id and project["status"] == "draft"
    project["spec"]["x-future-field"] = {"keep": True}
    advised = admin_client.post(
        "/api/v1/application-builder/platform-advisor",
        json={"platforms": ["web", "linux"], "offline": True, "preferWebReuse": True}, headers=CSRF_HEADERS,
    )
    assert advised.status_code == 200
    assert advised.json()["recommendedId"] in {"aspnet-blazor", "aspnet-react"}
    assert len(advised.json()["recommendations"]) == 10
    preflight = admin_client.post(
        "/api/v1/application-builder/preflight",
        json={"spec": project["spec"], "workflow_id": workflow_id}, headers=CSRF_HEADERS,
    )
    assert preflight.status_code == 200
    assert preflight.json()["readyForGeneration"] is False
    assert preflight.json()["sideEffects"]["subprocess"] is False
    assert "GENERATOR_AUTH_ADAPTER_UNAVAILABLE" not in {item["code"] for item in preflight.json()["diagnostics"]}

    def forbidden_executor(*args, **kwargs):
        raise AssertionError("validate must not execute a node")

    for node_type in list(nodes.NODE_EXECUTORS):
        monkeypatch.setitem(nodes.NODE_EXECUTORS, node_type, forbidden_executor)
    validated = admin_client.post(
        "/api/v1/application-builder/validate",
        json={"spec": project["spec"], "workflow_id": workflow_id, "target": "csharp"},
        headers=CSRF_HEADERS,
    )
    assert validated.status_code == 200, validated.text
    payload = validated.json()
    assert payload["valid"] is True
    assert payload["workflowIr"]["workflow_id"] == workflow_id
    assert payload["capability"]["generationAvailable"] is True
    assert payload["normalizedSpec"]["x-future-field"] == {"keep": True}
    repeated = admin_client.post(
        "/api/v1/application-builder/validate",
        json={"spec": project["spec"], "workflow_id": workflow_id, "target": "csharp"},
        headers=CSRF_HEADERS,
    )
    assert repeated.json() == payload

    updated = admin_client.patch(
        f"/api/v1/application-projects/{project['id']}",
        json={"name": "Updated App", "spec": project["spec"]}, headers=CSRF_HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["spec"]["x-future-field"] == {"keep": True}
    listed = admin_client.get(f"/api/v1/application-projects?workflow_id={workflow_id}").json()
    assert {item["id"] for item in listed} == {project["id"], published_project.json()["id"]}
    blocked_delete = admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)
    assert blocked_delete.status_code == 409 and "Application Project" in blocked_delete.json()["detail"]
    assert admin_client.delete(f"/api/v1/application-projects/{project['id']}", headers=CSRF_HEADERS).status_code == 204
    assert admin_client.delete(f"/api/v1/application-projects/{published_project.json()['id']}", headers=CSRF_HEADERS).status_code == 204
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200


def test_application_patch_preview_atomic_apply_and_stale_guard(admin_client):
    from app.application_builder.patch_service import spec_checksum

    spec = _component_spec()
    created = admin_client.post(
        "/api/v1/application-projects",
        json={"name": "Patch Project", "spec": spec}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    patches = [{"op": "replace", "path": "/pages/0/root/children/0/properties/text", "value": "Applied"}]
    preview = admin_client.post(
        "/api/v1/application-builder/patches/preview",
        json={"spec": spec, "patches": patches}, headers=CSRF_HEADERS,
    )
    assert preview.status_code == 200 and preview.json()["valid"] is True
    base_checksum = preview.json()["baseChecksum"]
    assert base_checksum == spec_checksum(spec)
    applied = admin_client.post(
        f"/api/v1/application-projects/{project_id}/patches/apply",
        json={"base_checksum": base_checksum, "patches": patches}, headers=CSRF_HEADERS,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["project"]["spec"]["pages"][0]["root"]["children"][0]["properties"]["text"] == "Applied"
    stale = admin_client.post(
        f"/api/v1/application-projects/{project_id}/patches/apply",
        json={"base_checksum": base_checksum, "patches": patches}, headers=CSRF_HEADERS,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "PATCH_BASE_CHANGED"
    assert admin_client.delete(f"/api/v1/application-projects/{project_id}", headers=CSRF_HEADERS).status_code == 204


def test_application_design_proposals_are_structured_redacted_and_prevalidated():
    from app.application_builder.proposal_service import ProposalInputError, generate_design_proposals
    from app.schemas.application_builder import ApplicationDesignProposalRequest

    spec = _component_spec()
    spec["application"]["apiKey"] = "super-secret-value"
    spec["application"]["description"] = "never expose super-secret-value"
    spec["xAppAdvisor"] = {"status": "ready", "inputs": [{"name": "question", "type": "string"}], "outputs": [{"name": "answer", "type": "string"}]}
    spec["apiEndpoints"] = [{
        "id": "run", "method": "POST", "path": "/api/run", "workflowId": 7, "mode": "sync",
        "authentication": "inherit", "requestSchema": {"type": "object", "properties": {"question": {"type": "string"}}},
        "responseSchema": {"type": "object", "properties": {"answer": {"type": "string"}}},
    }]
    captured = {}

    async def complete(messages, schema):
        captured["messages"] = messages
        captured["schema"] = schema
        proposals = []
        for direction, text in (("simple", "Simple"), ("balanced", "Balanced"), ("dense", "Dense")):
            proposals.append({
                "id": direction, "direction": direction, "title": text, "summary": f"{text} proposal",
                "rationale": ["Keep semantic components"], "warnings": [],
                "patches": [{"op": "replace", "path": "/pages/0/root/children/0/properties/text", "from": None, "valueJson": json.dumps(text)}],
            })
        return json.dumps({"proposals": proposals})

    request = ApplicationDesignProposalRequest(
        instruction="Make the page easier to scan", scope="application", mode="balanced",
        base_url="http://127.0.0.1:11434/v1", model="local-model",
    )
    result = asyncio.run(generate_design_proposals(spec, request, complete=complete))
    assert [item["direction"] for item in result["proposals"]] == ["simple", "balanced", "dense"]
    assert all("preview" in item for item in result["proposals"])
    prompt = json.dumps(captured["messages"], ensure_ascii=False)
    assert "super-secret-value" not in prompt and "***" in prompt
    user_context = json.loads(captured["messages"][1]["content"])
    assert user_context["workflowContract"]["requestSchema"]["properties"]["question"]["type"] == "string"
    assert "remain functional" in user_context["workflowContract"]["invariant"]
    assert captured["schema"]["properties"]["proposals"]["minItems"] == 3
    invalid_scope = request.model_copy(update={"scope": "component", "target_id": "missing"})
    try:
        asyncio.run(generate_design_proposals(spec, invalid_scope, complete=complete))
        raise AssertionError("missing component must be rejected before LLM")
    except ProposalInputError:
        pass


def test_application_design_proposal_api_requires_registered_model(admin_client, monkeypatch):
    from app.application_builder import router as application_router
    from app.models_mgmt import providers

    spec = _component_spec()
    created = admin_client.post(
        "/api/v1/application-projects", json={"name": "AI Design", "spec": spec}, headers=CSRF_HEADERS,
    )
    project_id = created.json()["id"]

    async def fake_providers(**_kwargs):
        return [{
            "base_url": "http://127.0.0.1:11434/v1", "models": ["design-model"],
            "provider": "ollama", "available": True,
        }]

    async def fake_generate(current_spec, body):
        assert current_spec == spec and body.model == "design-model"
        return {"proposals": [{"direction": item} for item in ("simple", "balanced", "dense")]}

    monkeypatch.setattr(providers, "list_providers", fake_providers)
    monkeypatch.setattr(application_router, "generate_design_proposals", fake_generate)
    body = {
        "instruction": "Make a dashboard", "scope": "application", "mode": "balanced",
        "base_url": "http://127.0.0.1:11434/v1", "model": "design-model",
    }
    generated = admin_client.post(
        f"/api/v1/application-projects/{project_id}/design-proposals", json=body, headers=CSRF_HEADERS,
    )
    assert generated.status_code == 200, generated.text
    assert len(generated.json()["proposals"]) == 3
    body["model"] = "unregistered"
    blocked = admin_client.post(
        f"/api/v1/application-projects/{project_id}/design-proposals", json=body, headers=CSRF_HEADERS,
    )
    assert blocked.status_code == 422
    assert admin_client.delete(f"/api/v1/application-projects/{project_id}", headers=CSRF_HEADERS).status_code == 204
