from __future__ import annotations

import html
import json
import re
from typing import Any

from app.application_builder.aspnet_entity_generator import entity_base_path
from app.application_builder.diagnostics import Diagnostic, diagnostic

SUPPORTED_UI_COMPONENTS = {
    "layout.stack", "layout.row", "layout.grid", "layout.card",
    "display.text", "display.markdown", "display.metric", "input.text", "data.table", "action.workflow-run",
}
MAX_UI_PAGES = 50
MAX_UI_COMPONENTS = 1_000
SUPPORTED_FORM_TYPES = {"string", "integer", "number", "boolean", "object", "array"}
STATE_CONSUMER_TYPES: dict[str, set[str]] = {
    "display.text": {"string", "integer", "number", "boolean", "object", "array"},
    "display.markdown": {"string", "integer", "number", "boolean", "object", "array"},
    "display.metric": {"string", "integer", "number", "boolean"},
    "input.text": {"string"},
}


def _client_state_by_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in spec.get("clientState") or []
        if isinstance(item, dict) and item.get("id")
    }


def _query_by_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in spec.get("queries") or []
        if isinstance(item, dict) and item.get("id")
    }


def _query_endpoint(query: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    endpoint_id = str(query.get("endpointId") or "")
    return next((
        item for item in spec.get("apiEndpoints") or []
        if isinstance(item, dict) and str(item.get("id") or "") == endpoint_id
    ), None)


def _schema_at_path(schema: dict[str, Any], path: str) -> dict[str, Any]:
    current: Any = schema
    for segment in path.split(".") if path else []:
        properties = current.get("properties") if isinstance(current, dict) else None
        current = properties.get(segment) if isinstance(properties, dict) else None
        if not isinstance(current, dict):
            return {}
    return current if isinstance(current, dict) else {}


def _query_item_properties(query: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    endpoint = _query_endpoint(query, spec)
    response_schema = endpoint.get("responseSchema") if isinstance((endpoint or {}).get("responseSchema"), dict) else {}
    collection = _schema_at_path(response_schema, str(query.get("resultPath") or ""))
    items = collection.get("items") if isinstance(collection.get("items"), dict) else {}
    return items.get("properties") if isinstance(items.get("properties"), dict) else {}


def _state_binding_key(component: dict[str, Any]) -> str | None:
    binding = component.get("binding")
    if not isinstance(binding, str) or not binding.startswith("state:"):
        return None
    return binding.removeprefix("state:")


def _state_response_type_compatible(state_type: str, schema_type: Any) -> bool:
    return schema_type == state_type or (state_type == "number" and schema_type == "integer")


def _state_display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _resolve_action_endpoint(
    component: dict[str, Any], spec: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    properties = component.get("properties") if isinstance(component.get("properties"), dict) else {}
    endpoint_id = str(properties.get("endpointId") or "")
    workflow_binding = str(properties.get("workflowBinding") or "")
    workflows = {str(item.get("id")): item for item in spec.get("workflows") or [] if isinstance(item, dict)}
    endpoints = [item for item in spec.get("apiEndpoints") or [] if isinstance(item, dict)]
    workflow = workflows.get(workflow_binding)
    if workflow is None:
        return None, "GENERATOR_GUI_WORKFLOW_BINDING_MISSING", f"Workflow binding '{workflow_binding}' が存在しません"
    workflow_id = workflow.get("workflowId")
    if endpoint_id:
        endpoint = next((item for item in endpoints if str(item.get("id")) == endpoint_id), None)
        if endpoint is None:
            return None, "GENERATOR_GUI_ENDPOINT_MISSING", f"API endpoint '{endpoint_id}' が存在しません"
        if endpoint.get("workflowId") != workflow_id:
            return None, "GENERATOR_GUI_ENDPOINT_WORKFLOW_MISMATCH", "API endpointとWorkflow bindingが一致しません"
    else:
        candidates = [item for item in endpoints if item.get("workflowId") == workflow_id]
        if not candidates:
            return None, "GENERATOR_GUI_ENDPOINT_MISSING", f"Workflow binding '{workflow_binding}' のAPI endpointがありません"
        if len(candidates) != 1:
            return None, "GENERATOR_GUI_ENDPOINT_AMBIGUOUS", "API endpointが複数あります。endpointIdを明示してください"
        endpoint = candidates[0]
    if endpoint.get("mode", "sync") != "sync":
        return None, "GENERATOR_GUI_ASYNC_ENDPOINT_UNSUPPORTED", "E3 GUI formはsync API endpointだけに対応します"
    if "{" in str(endpoint.get("path") or ""):
        return None, "GENERATOR_GUI_ROUTE_PARAMETER_UNSUPPORTED", "GUI formはroute parameter付きendpointへ未対応です"
    schema = endpoint.get("requestSchema") if isinstance(endpoint.get("requestSchema"), dict) else {}
    if schema:
        if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
            return None, "GENERATOR_GUI_FORM_SCHEMA_UNSUPPORTED", "GUI formのrequest schemaはobject propertiesにしてください"
        if len(schema.get("properties", {})) > 50:
            return None, "GENERATOR_GUI_FORM_FIELD_LIMIT_EXCEEDED", "GUI form fieldは50件以下にしてください"
        for name, child in schema.get("properties", {}).items():
            field_type = child.get("type") if isinstance(child, dict) else None
            if field_type not in SUPPORTED_FORM_TYPES:
                return None, "GENERATOR_GUI_FORM_SCHEMA_UNSUPPORTED", f"form field '{name}' のtypeは未対応です"
    return endpoint, None, None


def ui_generator_diagnostics(spec: dict[str, Any]) -> list[Diagnostic]:
    pages = [item for item in spec.get("pages") or [] if isinstance(item, dict)]
    if not pages:
        return []
    issues: list[Diagnostic] = []
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    if len(pages) > MAX_UI_PAGES:
        issues.append(diagnostic(
            "GENERATOR_GUI_PAGE_LIMIT_EXCEEDED", "error", "生成GUIのPageは50件以下にしてください",
            path="pages", source="source-generator",
        ))
    entity_by_id = {str(item.get("id")): item for item in spec.get("entities") or [] if isinstance(item, dict)}
    query_by_id = _query_by_id(spec)
    state_by_id = _client_state_by_id(spec)
    state_consumers: set[str] = set()

    def collect_state_consumers(component: Any) -> None:
        if not isinstance(component, dict):
            return
        state_key = _state_binding_key(component)
        if state_key and str(component.get("type") or "") in STATE_CONSUMER_TYPES:
            state_consumers.add(state_key)
        for child in component.get("children") or []:
            collect_state_consumers(child)

    for page in pages:
        collect_state_consumers(page.get("root"))
    route_keys: set[str] = set()
    class_keys: set[str] = set()
    for index, page in enumerate(pages):
        page_id = str(page.get("id") or f"page{index + 1}")
        route_key = _route_segment(page_id)
        class_key = _csharp_identifier(page_id).casefold()
        if route_key in route_keys or class_key in class_keys:
            issues.append(diagnostic(
                "GENERATOR_GUI_PAGE_ROUTE_DUPLICATE", "error",
                f"Page '{page_id}' の生成routeまたはclass名が重複します",
                path=f"pages.{index}.id", source="source-generator",
            ))
        route_keys.add(route_key); class_keys.add(class_key)
    component_count = 0

    def visit(component: Any, path: str) -> None:
        nonlocal component_count
        if not isinstance(component, dict):
            return
        component_count += 1
        component_type = str(component.get("type") or "")
        if component_type not in SUPPORTED_UI_COMPONENTS:
            issues.append(diagnostic(
                "GENERATOR_GUI_COMPONENT_UNSUPPORTED", "error",
                f"Semantic Component '{component_type}' のBlazor sourceは未対応です",
                path=f"{path}.type", source="source-generator",
            ))
        events = component.get("events") if isinstance(component.get("events"), dict) else {}
        for event_name, config in events.items():
            action = config.get("action") if isinstance(config, dict) else None
            supported = bool(
                component_type == "action.workflow-run"
                and event_name in {"success", "error"}
                and action in {"navigate", "state-set"}
            ) or bool(
                component_type == "input.text" and event_name == "change" and action == "state-set"
            )
            if not supported:
                issues.append(diagnostic(
                    "GENERATOR_GUI_EVENT_UNSUPPORTED", "error",
                    "E6 GUI sourceはWorkflow actionのsuccess/error Navigate・state-setとText Input change state-setだけに対応します",
                    path=f"{path}.events.{event_name}", source="source-generator",
                ))
                continue
            if action == "state-set":
                target = str(config.get("target") or "")
                state = state_by_id.get(target)
                if state is None:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_MISSING", "error", f"client state '{target}' が存在しません",
                        path=f"{path}.events.{event_name}.target", source="source-generator",
                    ))
                    continue
                if target not in state_consumers:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_CONSUMER_MISSING", "error",
                        f"client state '{target}' を表示または入力するconsumerがありません",
                        path=f"{path}.events.{event_name}.target", source="source-generator",
                        suggested_fix="対応componentへstate bindingを追加してください",
                    ))
                state_type = str(state.get("type") or "")
                if component_type == "input.text" and state_type != "string":
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_TYPE_MISMATCH", "error",
                        "Text Input changeはstring stateだけを更新できます",
                        path=f"{path}.events.{event_name}.target", source="source-generator",
                    ))
                elif component_type == "action.workflow-run" and event_name == "error" and state_type != "object":
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_TYPE_MISMATCH", "error",
                        "Workflow error stateは安全なstatus/message objectを受け取るためobject型にしてください",
                        path=f"{path}.events.{event_name}.target", source="source-generator",
                    ))
                elif component_type == "action.workflow-run" and event_name == "success":
                    endpoint, _code, _message = _resolve_action_endpoint(component, spec)
                    response_schema = endpoint.get("responseSchema") if isinstance(endpoint, dict) else None
                    schema_type = response_schema.get("type") if isinstance(response_schema, dict) else None
                    if not schema_type:
                        issues.append(diagnostic(
                            "GENERATOR_GUI_STATE_RESPONSE_SCHEMA_REQUIRED", "error",
                            "Workflow successをstateへ保存するendpointにはresponseSchema typeが必要です",
                            path=f"{path}.events.{event_name}.target", source="source-generator",
                        ))
                    elif not _state_response_type_compatible(state_type, schema_type):
                        issues.append(diagnostic(
                            "GENERATOR_GUI_STATE_TYPE_MISMATCH", "error",
                            f"response schema type '{schema_type}' はclient state '{target}' ({state_type})へ保存できません",
                            path=f"{path}.events.{event_name}.target", source="source-generator",
                        ))
        binding = component.get("binding")
        properties = component.get("properties") if isinstance(component.get("properties"), dict) else {}
        if component_type == "action.workflow-run":
            _endpoint, code, message = _resolve_action_endpoint(component, spec)
            if code and message:
                issues.append(diagnostic(code, "error", message, path=f"{path}.properties", source="source-generator"))
        if binding:
            binding_text = str(binding) if isinstance(binding, str) else ""
            state_key = _state_binding_key(component)
            if state_key is not None:
                state = state_by_id.get(state_key)
                if state is None:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_MISSING", "error", f"client state '{state_key}' が存在しません",
                        path=f"{path}.binding", source="source-generator",
                    ))
                elif component_type not in STATE_CONSUMER_TYPES:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_CONSUMER_UNSUPPORTED", "error",
                        f"Semantic Component '{component_type}' はstate consumerとして未対応です",
                        path=f"{path}.binding", source="source-generator",
                    ))
                elif str(state.get("type") or "") not in STATE_CONSUMER_TYPES[component_type]:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_STATE_TYPE_MISMATCH", "error",
                        f"Semantic Component '{component_type}' は{state.get('type')} stateを表示できません",
                        path=f"{path}.binding", source="source-generator",
                    ))
            elif binding_text.startswith("query:"):
                query_id = binding_text.removeprefix("query:")
                query = query_by_id.get(query_id)
                if query is None:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_QUERY_MISSING", "error", f"query '{query_id}' が存在しません",
                        path=f"{path}.binding", source="source-generator",
                    ))
                elif component_type != "data.table":
                    issues.append(diagnostic(
                        "GENERATOR_GUI_QUERY_CONSUMER_UNSUPPORTED", "error",
                        f"Semantic Component '{component_type}' はcollection queryを表示できません",
                        path=f"{path}.binding", source="source-generator",
                    ))
                else:
                    requested_mutations = [
                        operation for operation, key in (("create", "enableCreate"), ("update", "enableUpdate"), ("delete", "enableDelete"))
                        if properties.get(key) is True
                    ]
                    if str(query.get("source") or "entity") == "api":
                        endpoint = _query_endpoint(query, spec)
                        if endpoint is None:
                            issues.append(diagnostic(
                                "GENERATOR_GUI_QUERY_ENDPOINT_MISSING", "error",
                                f"query '{query_id}' のAPI endpointが存在しません",
                                path=f"{path}.binding", source="source-generator",
                            ))
                        elif endpoint.get("mode", "sync") != "sync" or "{" in str(endpoint.get("path") or ""):
                            issues.append(diagnostic(
                                "GENERATOR_GUI_QUERY_ENDPOINT_UNSUPPORTED", "error",
                                "API queryはroute parameterなしのsync endpointだけを使用できます",
                                path=f"{path}.binding", source="source-generator",
                            ))
                        if not _query_item_properties(query, spec):
                            issues.append(diagnostic(
                                "GENERATOR_GUI_QUERY_RESULT_UNSUPPORTED", "error",
                                "API queryのresultはtyped object collectionにしてください",
                                path=f"{path}.binding", source="source-generator",
                            ))
                        if requested_mutations:
                            issues.append(diagnostic(
                                "GENERATOR_GUI_QUERY_MUTATION_UNSUPPORTED", "error",
                                "API queryへbindingしたData TableではEntity mutationを生成できません",
                                path=f"{path}.properties", source="source-generator",
                            ))
                    else:
                        entity_id = str(query.get("entityId") or "")
                        entity = entity_by_id.get(entity_id)
                        crud = entity.get("crud") if isinstance((entity or {}).get("crud"), dict) else {}
                        operations = crud.get("operations") or []
                        if entity is None:
                            issues.append(diagnostic(
                                "GENERATOR_GUI_QUERY_ENTITY_MISSING", "error",
                                f"query '{query_id}' のEntity '{entity_id}' が存在しません",
                                path=f"{path}.binding", source="source-generator",
                            ))
                        elif not crud.get("enabled") or "list" not in operations:
                            issues.append(diagnostic(
                                "GENERATOR_GUI_QUERY_LIST_UNAVAILABLE", "error",
                                f"query '{query_id}' のEntity list operationが公開されていません",
                                path=f"{path}.binding", source="source-generator",
                            ))
                        for operation in requested_mutations:
                            if entity is not None and (not crud.get("enabled") or operation not in operations):
                                issues.append(diagnostic(
                                    "GENERATOR_GUI_ENTITY_MUTATION_UNAVAILABLE", "error",
                                    f"Entity '{entity_id}' のCRUD {operation} operationが公開されていません",
                                    path=f"{path}.properties.enable{operation.title()}", source="source-generator",
                                ))
                        if requested_mutations and app.get("authentication") == "none":
                            issues.append(diagnostic(
                                "GENERATOR_GUI_PUBLIC_MUTATION", "warning",
                                f"Entity '{entity_id}' のmutation GUIは認証なしで公開されます",
                                path=f"{path}.properties", source="source-generator",
                                suggested_fix="公開操作でない場合はauthenticationをapi-keyにしてください",
                            ))
            elif component_type != "data.table" or not binding_text.startswith("entity:"):
                issues.append(diagnostic(
                    "GENERATOR_GUI_BINDING_UNSUPPORTED", "error",
                    "E6 GUI sourceは対応componentのclient stateとData Tableのtyped Query／Entity collection bindingだけに対応します",
                    path=f"{path}.binding", source="source-generator",
                ))
            else:
                reference = binding_text.removeprefix("entity:")
                entity_id, separator, _field_id = reference.partition(".")
                entity = entity_by_id.get(entity_id)
                crud = entity.get("crud") if isinstance((entity or {}).get("crud"), dict) else {}
                operations = crud.get("operations") or []
                if separator:
                    issues.append(diagnostic(
                        "GENERATOR_GUI_TABLE_FIELD_BINDING_UNSUPPORTED", "error",
                        "Data TableはEntity fieldではなくcollectionへbindingしてください",
                        path=f"{path}.binding", source="source-generator",
                    ))
                if entity is not None and (not crud.get("enabled") or "list" not in operations):
                    issues.append(diagnostic(
                        "GENERATOR_GUI_ENTITY_LIST_UNAVAILABLE", "error",
                        f"Entity '{entity_id}' のCRUD list operationが公開されていません",
                        path=f"{path}.binding", source="source-generator",
                    ))
                requested_mutations = [
                    operation for operation, key in (("create", "enableCreate"), ("update", "enableUpdate"), ("delete", "enableDelete"))
                    if properties.get(key) is True
                ]
                for operation in requested_mutations:
                    if entity is not None and (not crud.get("enabled") or operation not in operations):
                        issues.append(diagnostic(
                            "GENERATOR_GUI_ENTITY_MUTATION_UNAVAILABLE", "error",
                            f"Entity '{entity_id}' のCRUD {operation} operationが公開されていません",
                            path=f"{path}.properties.enable{operation.title()}", source="source-generator",
                        ))
                if requested_mutations and app.get("authentication") == "none":
                    issues.append(diagnostic(
                        "GENERATOR_GUI_PUBLIC_MUTATION", "warning",
                        f"Entity '{entity_id}' のmutation GUIは認証なしで公開されます",
                        path=f"{path}.properties", source="source-generator",
                        suggested_fix="公開操作でない場合はauthenticationをapi-keyにしてください",
                    ))
        elif component_type == "data.table" and any(properties.get(key) is True for key in ("enableCreate", "enableUpdate", "enableDelete")):
            issues.append(diagnostic(
                "GENERATOR_GUI_MUTATION_BINDING_REQUIRED", "error",
                "CRUD mutationを有効にするData TableはEntity collectionへbindingしてください",
                path=f"{path}.binding", source="source-generator",
            ))
        for index, child in enumerate(component.get("children") or []):
            visit(child, f"{path}.children.{index}")

    for index, page in enumerate(pages):
        if page.get("root") is not None:
            visit(page["root"], f"pages.{index}.root")
    if component_count > MAX_UI_COMPONENTS:
        issues.append(diagnostic(
            "GENERATOR_GUI_COMPONENT_LIMIT_EXCEEDED", "error", "生成GUIのComponentは1,000件以下にしてください",
            path="pages", source="source-generator",
        ))
    return issues


def render_app_component(
    namespace: str, app: dict[str, Any], pages: list[dict[str, Any]], authentication: str,
    client_states: list[dict[str, Any]],
) -> str:
    title = _text(str(app.get("displayName") or app.get("name") or namespace))
    navigation = "".join(
        f'<a href="/{_route_segment(str(page.get("id") or "page"))}">{_text(str(page.get("title") or page.get("id") or "Page"))}</a>'
        for page in pages
    )
    authentication_required = authentication == "api-key"
    auth_panel = '''<section id="generated-auth" class="auth-shell" aria-labelledby="auth-title">
    <form id="generated-login" class="auth-card"><h1 id="auth-title">Sign in</h1>
      <label>Application API key<input id="generated-api-key" type="password" autocomplete="current-password" required /></label>
      <button type="submit">Sign in</button><p id="generated-auth-status" role="status"></p>
    </form></section>''' if authentication_required else ""
    logout = '<button id="generated-logout" type="button" class="secondary-button">Sign out</button>' if authentication_required else ""
    hidden = " hidden" if authentication_required else ""
    state_initializers = "".join(
        f'<data data-client-state-initial data-state-key="{_attribute(str(state.get("id") or ""))}" data-state-value="{_attribute(json.dumps(state.get("initialValue"), ensure_ascii=False, sort_keys=True, separators=(",", ":")))}"></data>'
        for state in client_states
    )
    state_container = f'<div id="generated-client-state" hidden>{state_initializers}</div>' if state_initializers else ""
    return f'''@using Microsoft.AspNetCore.Components.Routing
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>{title}</title>
  <link rel="stylesheet" href="/generated-ui.css" />
  <HeadOutlet />
</head>
<body>
  {state_container}
  {auth_panel}
  <div id="generated-app-content"{hidden}>
    <header class="app-header"><strong>{title}</strong><nav aria-label="Primary navigation">{navigation}</nav>{logout}</header>
    <Router AppAssembly="@typeof(App).Assembly">
      <Found Context="routeData"><RouteView RouteData="routeData" /></Found>
      <NotFound><main class="generated-page"><h1>Page not found</h1></main></NotFound>
    </Router>
  </div>
  <script src="/generated-ui.js"></script>
</body>
</html>
'''


def render_page_component(
    namespace: str, page: dict[str, Any], index: int, entities: list[dict[str, Any]], spec: dict[str, Any],
) -> tuple[str, str]:
    page_id = str(page.get("id") or f"page{index + 1}")
    route = _route_segment(page_id)
    class_name = _csharp_identifier(page_id) + "Page"
    directives = f'@page "/{route}"\n' + ('@page "/"\n' if index == 0 else "")
    title = _text(str(page.get("title") or page_id))
    entity_by_id = {str(item.get("id")): item for item in entities}
    body = _render_component(page.get("root"), entity_by_id, spec) if page.get("root") else '<p class="empty-state">This page has no components.</p>'
    source = f'''{directives}@namespace {namespace}.Components.Pages

<PageTitle>{title}</PageTitle>
<main class="generated-page" data-page-id="{_attribute(page_id)}">
  <h1>{title}</h1>
  {body}
</main>
'''
    return class_name, source


def render_ui_css() -> str:
    return '''html { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; background: #f4f4f5; color: #18181b; }
.auth-shell { min-height: 100dvh; display: grid; place-items: center; padding: max(1rem, env(safe-area-inset-top)) max(1rem, env(safe-area-inset-right)) max(1rem, env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-left)); }
.auth-card { width: min(100%, 26rem); display: grid; gap: 1rem; padding: 1.25rem; border: 1px solid #e4e4e7; border-radius: 1rem; background: #fff; }
.auth-card label { display: grid; gap: .35rem; }
button { min-height: 44px; border: 0; border-radius: .75rem; padding: 0 1rem; background: #2563eb; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
button:disabled { cursor: wait; opacity: .55; }
.secondary-button { background: transparent; color: inherit; border: 1px solid #a1a1aa; }
.app-header { position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; padding: max(.75rem, env(safe-area-inset-top)) max(1rem, env(safe-area-inset-right)) .75rem max(1rem, env(safe-area-inset-left)); background: #fff; border-bottom: 1px solid #e4e4e7; }
.app-header nav { display: flex; flex-wrap: wrap; gap: .5rem; margin-left: auto; }
.app-header a { min-height: 44px; display: inline-flex; align-items: center; padding: 0 .75rem; border-radius: .75rem; color: inherit; }
.generated-page { width: min(100%, 72rem); margin: 0 auto; padding: 1rem max(1rem, env(safe-area-inset-right)) max(5rem, env(safe-area-inset-bottom)) max(1rem, env(safe-area-inset-left)); }
.layout-stack, .layout-row { display: flex; gap: var(--gap, 1rem); }
.layout-stack { flex-direction: column; }
.layout-row { flex-direction: row; flex-wrap: wrap; }
.layout-grid { display: grid; grid-template-columns: repeat(var(--columns-mobile, 1), minmax(0, 1fr)); gap: var(--gap, 1rem); }
.layout-card, .metric, .table-shell, .workflow-form { min-width: 0; border: 1px solid #e4e4e7; border-radius: 1rem; background: #fff; padding: 1rem; }
.metric strong { display: block; margin-top: .25rem; font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.field { display: grid; gap: .35rem; }
input, select, textarea { min-height: 44px; width: 100%; border: 1px solid #a1a1aa; border-radius: .75rem; padding: .6rem .75rem; font: inherit; background: transparent; color: inherit; }
.table-heading, .form-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; }
.table-heading h2 { margin-right: auto; }
.query-refresh { background: transparent; color: inherit; border: 1px solid #a1a1aa; }
.query-pagination { display: flex; justify-content: flex-end; align-items: center; gap: .5rem; margin-top: .75rem; }
.query-pagination button { background: transparent; color: inherit; border: 1px solid #a1a1aa; }
.entity-form { display: grid; gap: .75rem; margin: 1rem 0; padding: 1rem; border: 1px solid #d4d4d8; border-radius: .75rem; }
.entity-form label { display: grid; gap: .35rem; }
.entity-form-status { min-height: 1.5rem; margin: 0; }
.workflow-form { display: grid; gap: .75rem; }
.workflow-form label { display: grid; gap: .35rem; min-width: 0; }
.workflow-form small { color: #71717a; overflow-wrap: anywhere; }
.workflow-status { min-height: 1.5rem; margin: 0; }
.workflow-result { min-width: 0; padding-top: .5rem; border-top: 1px solid #e4e4e7; }
.workflow-result h3 { margin: 0 0 .75rem; }
.workflow-result dl { display: grid; grid-template-columns: minmax(6rem, auto) minmax(0, 1fr); gap: .5rem 1rem; margin: 0; }
.workflow-result dt { font-weight: 600; overflow-wrap: anywhere; }
.workflow-result dd { min-width: 0; margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
details { position: relative; }
summary { min-height: 44px; display: inline-flex; align-items: center; cursor: pointer; }
details[open] { display: grid; gap: .25rem; }
details button { width: 100%; margin-top: .25rem; }
.destructive-button { background: #b91c1c; }
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: .7rem; border-bottom: 1px solid #e4e4e7; text-align: left; white-space: nowrap; }
.empty-state, .table-status { color: #71717a; }
pre.generated-markdown { white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; }
@media (min-width: 768px) { .layout-grid { grid-template-columns: repeat(var(--columns-tablet, 2), minmax(0, 1fr)); } .generated-page { padding-top: 1.5rem; } }
@media (min-width: 1024px) { .layout-grid { grid-template-columns: repeat(var(--columns-desktop, 3), minmax(0, 1fr)); } }
@media (prefers-color-scheme: dark) { body { background: #09090b; color: #fafafa; } .app-header, .auth-card, .layout-card, .metric, .table-shell, .workflow-form { background: #18181b; border-color: #3f3f46; } .workflow-result, th, td { border-color: #3f3f46; } }
'''


def render_ui_javascript() -> str:
    return '''"use strict";
const auth = document.querySelector("#generated-auth");
const appContent = document.querySelector("#generated-app-content");
const authStatus = document.querySelector("#generated-auth-status");
const showApplication = () => { if (auth) auth.hidden = true; if (appContent) appContent.hidden = false; };
const showLogin = (message = "") => { if (auth) auth.hidden = false; if (appContent && auth) appContent.hidden = true; if (authStatus) authStatus.textContent = message; };
const request = async (url, options = {}) => {
  const response = await fetch(url, { credentials: "same-origin", ...options, headers: { "X-Requested-With": "GeneratedApp", ...(options.headers || {}) } });
  if (response.status === 401 && auth) showLogin("Your session has expired. Sign in again.");
  return response;
};
const clientState = new Map();
const stateText = (value) => value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
const initializeClientState = () => {
  for (const item of document.querySelectorAll("[data-client-state-initial]")) {
    try { clientState.set(item.dataset.stateKey, JSON.parse(item.dataset.stateValue)); } catch { /* Generated values are validated JSON; keep malformed external edits isolated. */ }
  }
};
const setClientState = (key, value) => {
  if (!key) return;
  clientState.set(key, value);
  for (const element of document.querySelectorAll("[data-state-key]")) {
    if (element.dataset.stateKey !== key) continue;
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) element.value = stateText(value);
    else element.textContent = stateText(value);
  }
};
const setupStateInputs = () => {
  for (const input of document.querySelectorAll("[data-change-state]")) {
    if (input.dataset.stateBound === "true") continue;
    input.dataset.stateBound = "true";
    input.addEventListener("input", () => setClientState(input.dataset.changeState, input.value));
  }
};
const tableRows = new WeakMap();
const queryCache = new Map();
const queryPending = new Map();
const invalidateTableQuery = (table) => {
  const prefix = `${table.dataset.queryId || ""}:`;
  for (const key of queryCache.keys()) if (key.startsWith(prefix)) queryCache.delete(key);
};
const closeEntityForm = (form) => { form.hidden = true; form.reset(); delete form.dataset.mode; delete form.dataset.entityId; };
const openEntityForm = (table, mode, row = null) => {
  const shell = table.closest(".table-shell");
  const form = shell?.querySelector("[data-entity-form]");
  if (!form) return;
  form.reset(); form.hidden = false; form.dataset.mode = mode;
  const heading = form.querySelector("h3"); const submit = form.querySelector('button[type="submit"]');
  if (heading) heading.textContent = mode === "update" ? "Edit item" : "New item";
  if (submit) submit.textContent = mode === "update" ? "Save changes" : "Create";
  if (row) {
    form.dataset.entityId = String(row.id || "");
    for (const field of JSON.parse(table.dataset.fields || "[]")) {
      const input = form.elements.namedItem(field.name); const value = row[field.name];
      if (!input) continue;
      if (input.type === "checkbox") input.checked = Boolean(value);
      else if (field.type === "json") input.value = value === null || value === undefined ? "" : JSON.stringify(value, null, 2);
      else if (field.type === "datetime" && value) { const date = new Date(value); input.value = Number.isNaN(date.valueOf()) ? "" : new Date(date.valueOf() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
      else input.value = value === null || value === undefined ? "" : String(value);
    }
  }
  form.querySelector("input,textarea")?.focus();
};
const entityPayload = (form, fields) => {
  const payload = {};
  for (const field of fields) {
    const input = form.elements.namedItem(field.name); if (!input) continue;
    if (field.type === "boolean" && input.type === "checkbox") { payload[field.name] = input.checked; continue; }
    const raw = input.value;
    if (raw === "") {
      if (field.nullable) payload[field.name] = null;
      else if (form.dataset.mode === "create" && field.hasDefault) continue;
      else if (field.type === "string") payload[field.name] = "";
      else throw new Error(`${field.name} is required.`);
      continue;
    }
    if (field.type === "boolean") payload[field.name] = raw === "true";
    else if (field.type === "integer" || field.type === "number") { const value = Number(raw); if (!Number.isFinite(value) || (field.type === "integer" && !Number.isInteger(value))) throw new Error(`${field.name} is not a valid number.`); payload[field.name] = value; }
    else if (field.type === "datetime") { const value = new Date(raw); if (Number.isNaN(value.valueOf())) throw new Error(`${field.name} is not a valid date.`); payload[field.name] = value.toISOString(); }
    else if (field.type === "json") { try { payload[field.name] = JSON.parse(raw); } catch { throw new Error(`${field.name} is not valid JSON.`); } }
    else payload[field.name] = raw;
  }
  return payload;
};
const setupEntityTable = (table) => {
  if (table.dataset.bound === "true") return;
  table.dataset.bound = "true";
  const shell = table.closest(".table-shell"); const form = shell?.querySelector("[data-entity-form]");
  shell?.querySelector(".entity-new")?.addEventListener("click", () => openEntityForm(table, "create"));
  form?.querySelector(".entity-cancel")?.addEventListener("click", () => closeEntityForm(form));
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formStatus = form.querySelector(".entity-form-status"); const submit = form.querySelector('button[type="submit"]');
    try {
      const payload = entityPayload(form, JSON.parse(table.dataset.fields || "[]"));
      const updating = form.dataset.mode === "update"; const id = form.dataset.entityId || "";
      if (submit) submit.disabled = true; if (formStatus) formStatus.textContent = updating ? "Saving changes…" : "Creating…";
      const response = await request(updating ? `${table.dataset.entityUrl}/${encodeURIComponent(id)}` : table.dataset.entityUrl, {
        method: updating ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(response.status === 409 ? "The item conflicts with existing data." : "Review the field values and try again.");
      closeEntityForm(form); await renderEntityTable(table, true);
    } catch (error) { if (formStatus) formStatus.textContent = error instanceof Error ? error.message : "Unable to save the item."; }
    finally { if (submit) submit.disabled = false; }
  });
  table.addEventListener("click", async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("button[data-entity-action]"); if (!button) return;
    const row = tableRows.get(table)?.get(button.closest("tr")?.dataset.entityId || ""); if (!row) return;
    if (button.dataset.entityAction === "edit") { openEntityForm(table, "update", row); return; }
    if (button.dataset.entityAction !== "delete" || !confirm("Delete this item? This cannot be undone.")) return;
    button.disabled = true;
    try {
      const response = await request(`${table.dataset.entityUrl}/${encodeURIComponent(String(row.id))}`, { method: "DELETE" });
      if (!response.ok) throw new Error(); await renderEntityTable(table, true);
    } catch { const status = shell?.querySelector(".table-status"); if (status) status.textContent = "Unable to delete the item."; }
    finally { button.disabled = false; }
  });
};
const renderWorkflowResult = (container, value) => {
  container.replaceChildren();
  if (Array.isArray(value)) {
    const table = document.createElement("table"); const body = document.createElement("tbody");
    const objectRows = value.slice(0, 1000).filter((item) => item && typeof item === "object" && !Array.isArray(item));
    const columns = [...new Set(objectRows.flatMap((item) => Object.keys(item)))].slice(0, 20);
    if (columns.length) {
      const head = document.createElement("thead"); const row = document.createElement("tr");
      for (const column of columns) { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = column; row.append(cell); }
      head.append(row); table.append(head);
      for (const item of objectRows) { const row = document.createElement("tr"); for (const column of columns) { const cell = document.createElement("td"); const field = item[column]; cell.textContent = field === null || field === undefined ? "" : typeof field === "object" ? JSON.stringify(field) : String(field); row.append(cell); } body.append(row); }
    } else { const row = document.createElement("tr"); const cell = document.createElement("td"); cell.textContent = JSON.stringify(value); row.append(cell); body.append(row); }
    table.append(body); const scroll = document.createElement("div"); scroll.className = "table-scroll"; scroll.append(table); container.append(scroll); return;
  }
  if (value && typeof value === "object") {
    const list = document.createElement("dl");
    for (const [key, field] of Object.entries(value).slice(0, 1000)) { const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = key; detail.textContent = field === null || field === undefined ? "" : typeof field === "object" ? JSON.stringify(field) : String(field); list.append(term, detail); }
    container.append(list); return;
  }
  const text = document.createElement("p"); text.textContent = value === null || value === undefined ? "" : String(value); container.append(text);
};
const workflowPayload = (form) => {
  const payload = {};
  for (const input of form.querySelectorAll("[data-workflow-field]")) {
    const type = input.dataset.fieldType; const required = input.dataset.required === "true";
    if (type === "boolean" && input.type === "checkbox") { payload[input.name] = input.checked; continue; }
    const raw = input.value;
    if (raw === "" && !required) continue;
    if (type === "boolean") payload[input.name] = raw === "true";
    else if (type === "integer" || type === "number") { const value = Number(raw); if (!Number.isFinite(value) || (type === "integer" && !Number.isInteger(value))) throw new Error(`${input.name} is not a valid number.`); payload[input.name] = value; }
    else if (type === "object" || type === "array") { try { const value = JSON.parse(raw); if ((type === "array") !== Array.isArray(value) || (type === "object" && (!value || typeof value !== "object" || Array.isArray(value)))) throw new Error(); payload[input.name] = value; } catch { throw new Error(`${input.name} is not valid ${type} JSON.`); } }
    else payload[input.name] = raw;
  }
  return payload;
};
const setupWorkflowForms = () => {
  for (const form of document.querySelectorAll("[data-workflow-form]")) {
    if (form.dataset.bound === "true") continue; form.dataset.bound = "true";
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); const status = form.querySelector(".workflow-status"); const button = form.querySelector('button[type="submit"]'); const result = form.querySelector(".workflow-result");
      try {
        const payload = workflowPayload(form); button.disabled = true; if (status) status.textContent = "Running…"; if (result) result.hidden = true;
        const response = await request(form.dataset.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        let value = null; try { value = await response.json(); } catch { value = null; }
        if (!response.ok) { if (status) status.textContent = `Request failed (${response.status}).`; setClientState(form.dataset.errorState, { status: response.status, message: "Request failed." }); if (form.dataset.errorRoute) window.location.assign(form.dataset.errorRoute); return; }
        if (status) status.textContent = "Completed."; setClientState(form.dataset.successState, value); if (result) { renderWorkflowResult(result.querySelector("div"), value); result.hidden = false; }
        if (form.dataset.successRoute) window.location.assign(form.dataset.successRoute);
      } catch (error) { const message = error instanceof Error ? error.message : "Unable to run the workflow."; if (status) status.textContent = message; setClientState(form.dataset.errorState, { status: 0, message: "Unable to run the workflow." }); if (form.dataset.errorRoute) window.location.assign(form.dataset.errorRoute); }
      finally { button.disabled = false; }
    });
  }
};
const fetchTablePayload = async (table, force, offset) => {
  const queryId = table.dataset.queryId || "";
  const cacheKey = `${queryId}:${offset}`;
  const memoryCache = table.dataset.queryCache === "memory";
  const staleMs = Number(table.dataset.queryStaleMs || "0");
  if (force && queryId) invalidateTableQuery(table);
  const cached = queryId ? queryCache.get(cacheKey) : null;
  if (!force && memoryCache && cached && Date.now() - cached.loadedAt <= staleMs) return cached.payload;
  if (!force && queryId && queryPending.has(cacheKey)) return queryPending.get(cacheKey);
  const task = (async () => {
    const limit = Number(table.dataset.pageSize || "20");
    let response;
    if (table.dataset.queryMethod === "POST") {
      response = await request(table.dataset.queryUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: table.dataset.queryInput || "{}" });
    } else {
      const parameters = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (table.dataset.queryFilters && table.dataset.queryFilters !== "[]") parameters.set("filter", table.dataset.queryFilters);
      if (table.dataset.querySort && table.dataset.querySort !== "[]") parameters.set("sort", table.dataset.querySort);
      response = await request(`${table.dataset.queryUrl}?${parameters}`);
    }
    if (!response.ok) throw new Error(String(response.status));
    const raw = await response.json();
    let items = raw;
    for (const segment of (table.dataset.queryResultPath || "").split(".").filter(Boolean)) items = items?.[segment];
    if (table.dataset.queryMethod !== "POST") items = raw?.items;
    if (!Array.isArray(items)) throw new Error("Query result is not an array");
    const payload = { items: items.slice(0, limit), limit, offset };
    if (queryId && memoryCache) queryCache.set(cacheKey, { loadedAt: Date.now(), payload });
    return payload;
  })();
  if (queryId) queryPending.set(cacheKey, task);
  try { return await task; } finally { if (queryId && queryPending.get(cacheKey) === task) queryPending.delete(cacheKey); }
};
const renderEntityTable = async (table, force = false, requestedOffset = null) => {
  const shell = table.closest(".table-shell"); const status = shell?.querySelector(".table-status");
  const refresh = shell?.querySelector(".query-refresh"); const previous = shell?.querySelector(".query-previous"); const next = shell?.querySelector(".query-next");
  const body = table.querySelector("tbody");
  try {
    const offset = Math.max(0, requestedOffset ?? Number(table.dataset.queryOffset || "0"));
    table.setAttribute("aria-busy", "true"); for (const button of [refresh, previous, next]) if (button) button.disabled = true; if (status) status.textContent = "Loading…";
    const payload = await fetchTablePayload(table, force, offset);
    const columns = JSON.parse(table.dataset.columns || "[]");
    const rows = new Map(); tableRows.set(table, rows); setupEntityTable(table);
    body.replaceChildren();
    for (const row of Array.isArray(payload.items) ? payload.items : []) {
      const tr = document.createElement("tr"); const rowId = String(row.id || ""); tr.dataset.entityId = rowId; rows.set(rowId, row);
      for (const column of columns) { const td = document.createElement("td"); const value = row[column.key]; td.textContent = value === null || value === undefined ? "" : typeof value === "object" ? JSON.stringify(value) : String(value); tr.append(td); }
      if (table.dataset.canUpdate === "true" || table.dataset.canDelete === "true") {
        const cell = document.createElement("td"); const menu = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = "More"; menu.append(summary);
        if (table.dataset.canUpdate === "true") { const edit = document.createElement("button"); edit.type = "button"; edit.dataset.entityAction = "edit"; edit.textContent = "Edit"; menu.append(edit); }
        if (table.dataset.canDelete === "true") { const remove = document.createElement("button"); remove.type = "button"; remove.dataset.entityAction = "delete"; remove.className = "destructive-button"; remove.textContent = "Delete"; menu.append(remove); }
        cell.append(menu); tr.append(cell);
      }
      body.append(tr);
    }
    table.dataset.queryOffset = String(offset);
    if (status) status.textContent = body.children.length ? `${body.children.length} item${body.children.length === 1 ? "" : "s"}.` : "No items.";
    if (previous) previous.disabled = offset === 0;
    if (next) next.disabled = body.children.length < Number(table.dataset.pageSize || "20");
  } catch { body.replaceChildren(); if (status) status.textContent = "Unable to load data. Select Refresh to try again."; }
  finally { table.setAttribute("aria-busy", "false"); if (refresh) refresh.disabled = false; }
};
const setupQueryTables = () => {
  for (const table of document.querySelectorAll("table[data-query-url]")) {
    setupEntityTable(table);
    const shell = table.closest(".table-shell"); const refresh = shell?.querySelector(".query-refresh"); const previous = shell?.querySelector(".query-previous"); const next = shell?.querySelector(".query-next");
    if (refresh && refresh.dataset.bound !== "true") { refresh.dataset.bound = "true"; refresh.addEventListener("click", () => renderEntityTable(table, true)); }
    if (previous && previous.dataset.bound !== "true") { previous.dataset.bound = "true"; previous.addEventListener("click", () => renderEntityTable(table, false, Math.max(0, Number(table.dataset.queryOffset || "0") - Number(table.dataset.pageSize || "20")))); }
    if (next && next.dataset.bound !== "true") { next.dataset.bound = "true"; next.addEventListener("click", () => renderEntityTable(table, false, Number(table.dataset.queryOffset || "0") + Number(table.dataset.pageSize || "20"))); }
    if (table.dataset.queryAutoload !== "false") renderEntityTable(table);
  }
};
const loadApplication = () => { initializeClientState(); setupStateInputs(); setupWorkflowForms(); setupQueryTables(); };
const initializeAuth = async () => {
  if (!auth) { loadApplication(); return; }
  try {
    const response = await request("/auth/session");
    if (response.ok) { showApplication(); loadApplication(); } else showLogin();
  } catch { showLogin("Unable to contact the application."); }
};
document.querySelector("#generated-login")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#generated-api-key");
  const apiKey = input?.value || "";
  if (input) input.value = "";
  if (authStatus) authStatus.textContent = "Signing in…";
  try {
    const response = await request("/auth/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ apiKey }) });
    if (!response.ok) { showLogin(response.status === 429 ? "Too many attempts. Try again later." : "Sign in failed."); return; }
    showApplication(); loadApplication();
  } catch { showLogin("Unable to contact the application."); }
});
document.querySelector("#generated-logout")?.addEventListener("click", async () => {
  await request("/auth/session", { method: "DELETE" }); showLogin("Signed out.");
});
initializeAuth();
'''


def _render_component(
    component: Any, entity_by_id: dict[str, dict[str, Any]], spec: dict[str, Any],
) -> str:
    if not isinstance(component, dict):
        return ""
    component_type = str(component.get("type") or "")
    component_id = _attribute(str(component.get("id") or "component"))
    properties = component.get("properties") if isinstance(component.get("properties"), dict) else {}
    children = "".join(_render_component(child, entity_by_id, spec) for child in component.get("children") or [])
    gap = {"xs": ".25rem", "sm": ".5rem", "md": "1rem", "lg": "1.5rem", "xl": "2rem"}.get(str(properties.get("gap") or "md"), "1rem")
    if component_type == "layout.stack":
        direction = "row" if properties.get("direction") == "horizontal" else "column"
        return f'<section id="{component_id}" class="layout-stack" style="--gap:{gap};flex-direction:{direction}">{children}</section>'
    if component_type == "layout.row":
        wrap = "wrap" if properties.get("wrap", True) else "nowrap"
        return f'<section id="{component_id}" class="layout-row" style="--gap:{gap};flex-wrap:{wrap}">{children}</section>'
    if component_type == "layout.grid":
        columns = properties.get("columns") if isinstance(properties.get("columns"), dict) else {}
        mobile = _bounded_int(columns.get("mobile"), 1, 1, 12); tablet = _bounded_int(columns.get("tablet"), 2, 1, 12); desktop = _bounded_int(columns.get("desktop"), 3, 1, 12)
        return f'<section id="{component_id}" class="layout-grid" style="--gap:{gap};--columns-mobile:{mobile};--columns-tablet:{tablet};--columns-desktop:{desktop}">{children}</section>'
    if component_type == "layout.card":
        return f'<section id="{component_id}" class="layout-card">{children}</section>'
    if component_type == "display.text":
        state_key = _state_binding_key(component)
        state = _client_state_by_id(spec).get(state_key or "")
        value = _state_display_value(state.get("initialValue")) if state else str(properties.get("text") or "")
        state_attribute = f' data-state-key="{_attribute(state_key)}"' if state_key else ""
        return f'<p id="{component_id}"{state_attribute}>{_text(value)}</p>'
    if component_type == "display.markdown":
        state_key = _state_binding_key(component)
        state = _client_state_by_id(spec).get(state_key or "")
        value = _state_display_value(state.get("initialValue")) if state else str(properties.get("value") or "")
        state_attribute = f' data-state-key="{_attribute(state_key)}"' if state_key else ""
        return f'<pre id="{component_id}" class="generated-markdown"{state_attribute}>{_text(value)}</pre>'
    if component_type == "display.metric":
        state_key = _state_binding_key(component)
        state = _client_state_by_id(spec).get(state_key or "")
        value = _state_display_value(state.get("initialValue")) if state else str(properties.get("value") if properties.get("value") is not None else 0)
        state_attribute = f' data-state-key="{_attribute(state_key)}"' if state_key else ""
        return f'<section id="{component_id}" class="metric"><span>{_text(str(properties.get("label") or "Metric"))}</span><strong{state_attribute}>{_text(value)}</strong></section>'
    if component_type == "input.text":
        label = str(properties.get("label") or "Input"); required = " required" if properties.get("required") else ""
        state_key = _state_binding_key(component)
        state = _client_state_by_id(spec).get(state_key or "")
        value = _state_display_value(state.get("initialValue")) if state else ""
        events = component.get("events") if isinstance(component.get("events"), dict) else {}
        change = events.get("change") if isinstance(events.get("change"), dict) else {}
        change_state = str(change.get("target") or "") if change.get("action") == "state-set" else ""
        state_attributes = (f' data-state-key="{_attribute(state_key)}"' if state_key else "") + (f' data-change-state="{_attribute(change_state)}"' if change_state else "")
        return f'<label id="{component_id}" class="field"><span>{_text(label)}</span><input aria-label="{_attribute(label)}" placeholder="{_attribute(str(properties.get("placeholder") or ""))}" value="{_attribute(value)}"{state_attributes}{required} /></label>'
    if component_type == "action.workflow-run":
        endpoint, _code, _message = _resolve_action_endpoint(component, spec)
        if endpoint is None:
            return ""
        events = component.get("events") if isinstance(component.get("events"), dict) else {}
        page_routes = {
            str(page.get("id")): "/" + _route_segment(str(page.get("id")))
            for page in spec.get("pages") or [] if isinstance(page, dict)
        }
        success = events.get("success") if isinstance(events.get("success"), dict) else {}
        error = events.get("error") if isinstance(events.get("error"), dict) else {}
        return _render_workflow_form(
            component_id, properties, endpoint,
            page_routes.get(str(success.get("target")), "") if success.get("action") == "navigate" else "",
            page_routes.get(str(error.get("target")), "") if error.get("action") == "navigate" else "",
            str(success.get("target") or "") if success.get("action") == "state-set" else "",
            str(error.get("target") or "") if error.get("action") == "state-set" else "",
        )
    if component_type == "data.table":
        columns = properties.get("columns") if isinstance(properties.get("columns"), list) else []
        safe_columns = [item for item in columns if isinstance(item, dict) and item.get("key")][:50]
        binding = str(component.get("binding") or "")
        query_id = binding.removeprefix("query:") if binding.startswith("query:") else ""
        query = _query_by_id(spec).get(query_id)
        query_source = str(query.get("source") or "entity") if query else "entity"
        entity_id = str(query.get("entityId") or "") if query and query_source == "entity" else binding.removeprefix("entity:") if binding.startswith("entity:") else ""
        entity = entity_by_id.get(entity_id)
        if entity and not safe_columns:
            safe_columns = [{"key": "id", "label": "ID"}, *({"key": str(field.get("id")), "label": str(field.get("id"))} for field in entity.get("fields") or [] if isinstance(field, dict))]
        if query and query_source == "api" and not safe_columns:
            safe_columns = [{"key": key, "label": key} for key in list(_query_item_properties(query, spec))[:50]]
        can_create = bool(properties.get("enableCreate")) and bool(entity)
        can_update = bool(properties.get("enableUpdate")) and bool(entity)
        can_delete = bool(properties.get("enableDelete")) and bool(entity)
        has_row_actions = can_update or can_delete
        headers = "".join(f'<th scope="col">{_text(str(item.get("label") or item.get("key")))}</th>' for item in safe_columns)
        if has_row_actions:
            headers += '<th scope="col">Actions</th>'
        column_json = html.escape(json.dumps([{"key": str(item.get("key"))} for item in safe_columns], separators=(",", ":")), quote=True)
        fields_json = html.escape(json.dumps([
            {
                "name": str(field.get("id")), "type": str(field.get("type")),
                "nullable": bool(field.get("nullable")), "hasDefault": bool(field.get("hasDefault")),
            }
            for field in (entity or {}).get("fields") or [] if isinstance(field, dict)
        ], separators=(",", ":")), quote=True)
        endpoint = _query_endpoint(query, spec) if query and query_source == "api" else None
        query_url = str(endpoint.get("path") or "") if endpoint else entity_base_path(entity) if entity else ""
        query_method = "POST" if endpoint else "GET"
        query_input = html.escape(json.dumps(query.get("input") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")), quote=True) if query else "{}"
        query_filters = html.escape(json.dumps(query.get("filters") or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")), quote=True) if query else "[]"
        query_sort = html.escape(json.dumps(query.get("sort") or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")), quote=True) if query else "[]"
        query_attr = (
            f' data-query-id="{_attribute(query_id)}" data-query-cache="{_attribute(str(query.get("cachePolicy") or "memory"))}"'
            f' data-query-stale-ms="{_bounded_int(query.get("staleTimeSeconds"), 30, 0, 3600) * 1000}"'
            f' data-query-autoload="{str(query.get("autoLoad", True) is not False).lower()}"'
            f' data-query-method="{query_method}" data-query-input="{query_input}" data-query-result-path="{_attribute(str(query.get("resultPath") or ""))}"'
            f' data-query-filters="{query_filters}" data-query-sort="{query_sort}" data-query-offset="0"'
            if query else ""
        )
        page_size = _bounded_int(query.get("limit"), 20, 1, 100) if query else _bounded_int(properties.get("pageSize"), 20, 1, 100)
        entity_url_attr = f' data-entity-url="{_attribute(entity_base_path(entity))}"' if entity else ""
        table_attr = (
            f' data-query-url="{_attribute(query_url)}" data-columns="{column_json}" data-fields="{fields_json}"'
            f'{entity_url_attr}'
            f' data-page-size="{page_size}"{query_attr}'
            f' data-can-create="{str(can_create).lower()}" data-can-update="{str(can_update).lower()}" data-can-delete="{str(can_delete).lower()}"'
            if query_url else ""
        )
        label = _text(str(properties.get("label") or "Data table"))
        create_button = '<button type="button" class="entity-new">Add item</button>' if can_create else ""
        refresh_button = '<button type="button" class="query-refresh">Refresh</button>' if query else ""
        pagination = (
            '<div class="query-pagination" aria-label="Table pagination"><button type="button" class="query-previous" disabled>Previous</button><button type="button" class="query-next" disabled>Next</button></div>'
            if query and query_source == "entity" and query.get("pagination", "offset") == "offset" else ""
        )
        form = _render_entity_form(entity, component_id) if entity and (can_create or can_update) else ""
        initial_status = "Loading…" if query_url and (not query or query.get("autoLoad", True) is not False) else "Select Refresh to load data." if query else "No data binding."
        return f'<section id="{component_id}" class="table-shell"><div class="table-heading"><h2>{label}</h2>{refresh_button}{create_button}</div><p class="table-status" role="status">{initial_status}</p>{form}<div class="table-scroll"><table{table_attr} aria-busy="false"><thead><tr>{headers}</tr></thead><tbody></tbody></table></div>{pagination}</section>'
    return ""


def _render_workflow_form(
    component_id: str, properties: dict[str, Any], endpoint: dict[str, Any],
    success_route: str, error_route: str, success_state: str, error_state: str,
) -> str:
    schema = endpoint.get("requestSchema") if isinstance(endpoint.get("requestSchema"), dict) else {}
    schema_properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or [])
    fields: list[str] = []
    for index, (name, raw_field) in enumerate(schema_properties.items()):
        field = raw_field if isinstance(raw_field, dict) else {}
        field_type = str(field.get("type") or "string")
        label = str(field.get("title") or name)
        identifier = f"{component_id}-workflow-{index + 1}"
        # JSON Schema `required` means the key must exist. HTML `required` on a
        # checkbox would instead force the value to true, so boolean fields are
        # always serialized and must remain freely uncheckable.
        required_attribute = " required" if name in required and field_type != "boolean" else ""
        common = (
            f'id="{identifier}" name="{_attribute(str(name))}" data-workflow-field data-field-type="{_attribute(field_type)}"'
            f' data-required="{str(name in required).lower()}"{required_attribute}'
        )
        enum = field.get("enum") if isinstance(field.get("enum"), list) else []
        if field_type == "string" and enum and len(enum) <= 100 and all(isinstance(value, str) for value in enum):
            empty = '<option value="">Select…</option>' if name not in required else ""
            options = "".join(f'<option value="{_attribute(value)}">{_text(value)}</option>' for value in enum)
            control = f'<select {common}>{empty}{options}</select>'
        elif field_type == "string":
            minimum = field.get("minLength"); maximum = field.get("maxLength")
            limits = (f' minlength="{int(minimum)}"' if isinstance(minimum, int) else "") + (f' maxlength="{int(maximum)}"' if isinstance(maximum, int) else "")
            control = f'<input type="text" {common}{limits} />'
        elif field_type in {"integer", "number"}:
            step = "1" if field_type == "integer" else "any"
            minimum = f' min="{field["minimum"]}"' if isinstance(field.get("minimum"), (int, float)) and not isinstance(field.get("minimum"), bool) else ""
            maximum = f' max="{field["maximum"]}"' if isinstance(field.get("maximum"), (int, float)) and not isinstance(field.get("maximum"), bool) else ""
            control = f'<input type="number" step="{step}" {common}{minimum}{maximum} />'
        elif field_type == "boolean" and name in required:
            control = f'<input type="checkbox" {common} />'
        elif field_type == "boolean":
            control = f'<select {common}><option value="">No value</option><option value="true">Yes</option><option value="false">No</option></select>'
        else:
            control = f'<textarea rows="5" {common}></textarea>'
        description = f'<small>{_text(str(field.get("description")))}</small>' if field.get("description") else ""
        fields.append(f'<label for="{identifier}">{_text(label)}{control}{description}</label>')
    label = _text(str(properties.get("label") or "Run"))
    result_label = _text(str(properties.get("resultLabel") or "Result"))
    return (
        f'<form id="{component_id}" class="workflow-form" data-workflow-form data-endpoint="{_attribute(str(endpoint.get("path") or ""))}"'
        f' data-success-route="{_attribute(success_route)}" data-error-route="{_attribute(error_route)}"'
        f' data-success-state="{_attribute(success_state)}" data-error-state="{_attribute(error_state)}">'
        f'{"".join(fields)}<button type="submit">{label}</button><p class="workflow-status" role="status"></p>'
        f'<section class="workflow-result" aria-label="{_attribute(str(properties.get("resultLabel") or "Result"))}" hidden><h3>{result_label}</h3><div></div></section></form>'
    )


def _render_entity_form(entity: dict[str, Any], component_id: str) -> str:
    fields: list[str] = []
    for field in entity.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("id") or "field")
        field_type = str(field.get("type") or "string")
        required = not field.get("nullable") and not field.get("hasDefault")
        required_attribute = " required" if required else ""
        common = f'id="{component_id}-{_attribute(name)}" name="{_attribute(name)}" data-field-type="{_attribute(field_type)}"{required_attribute}'
        if field_type == "boolean" and (field.get("nullable") or field.get("hasDefault")):
            control = f'<select {common}><option value="">Use default / no value</option><option value="true">Yes</option><option value="false">No</option></select>'
        elif field_type == "boolean":
            control = f'<input type="checkbox" {common} />'
        elif field_type in {"integer", "number"}:
            step = "1" if field_type == "integer" else "any"
            control = f'<input type="number" step="{step}" {common} />'
        elif field_type == "datetime":
            control = f'<input type="datetime-local" {common} />'
        elif field_type == "json":
            control = f'<textarea rows="4" {common}></textarea>'
        else:
            maximum = field.get("maxLength")
            max_attribute = f' maxlength="{int(maximum)}"' if isinstance(maximum, int) else ""
            control = f'<input type="text" {common}{max_attribute} />'
        fields.append(f'<label for="{component_id}-{_attribute(name)}">{_text(name)}{control}</label>')
    return (
        f'<form class="entity-form" data-entity-form hidden><h3>New item</h3>{"".join(fields)}'
        '<p class="entity-form-status" role="status"></p><div class="form-actions">'
        '<button type="submit">Create</button><button type="button" class="secondary-button entity-cancel">Cancel</button>'
        '</div></form>'
    )


def _text(value: str) -> str:
    return html.escape(value, quote=False).replace("@", "@@")


def _attribute(value: str) -> str:
    return html.escape(value, quote=True).replace("@", "@@")


def _route_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", value).strip("-").lower() or "page"


def _csharp_identifier(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    result = "".join(part[:1].upper() + part[1:] for part in parts) or "Generated"
    return ("_" + result) if result[0].isdigit() else result


def _bounded_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, value if isinstance(value, int) and not isinstance(value, bool) else fallback))
