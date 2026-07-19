import asyncio
import json

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
    assert http.codegen.support == "manual" and http.codegen.planned_support == "native"
    assert http.codegen.source_available is False
    assert compiled.required_secrets[0].name == "API_TOKEN"
    assert "network" in compiled.capabilities and "external" in compiled.side_effects
    assert next(edge for edge in compiled.edges if edge.id == "e3").branch == "true"
    assert "secrets.***" in json.dumps(http.config)  # node config側は参照名もredact
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
    semantic = schema.json()["semanticComponents"]
    assert any(item["type"] == "layout.stack" and item["container"] for item in semantic["components"])
    assert any(item["type"] == "chart.line" for item in semantic["components"])
    capabilities = admin_client.get("/api/v1/application-builder/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["generationAvailable"] is False
    assert capabilities.json()["buildAvailable"] is False
    assert capabilities.json()["designProposalAvailable"] is True
    assert any(item["id"] == "avalonia" and item["status"] == "planned" for item in capabilities.json()["frameworks"])
    http_capability = next(item for item in capabilities.json()["nodes"] if item["type"] == "http.request")["targets"]["csharp"]
    assert http_capability["support"] == "manual"
    assert http_capability["planned_support"] == "native"
    assert http_capability["source_available"] is False
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
    assert payload["capability"]["generationAvailable"] is False
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
