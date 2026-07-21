from __future__ import annotations

import copy
from typing import Any


COMPONENTS: tuple[dict[str, Any], ...] = (
    {"type": "layout.stack", "label": "Stack", "category": "layout", "container": True, "defaults": {"gap": "md", "direction": "vertical"}},
    {"type": "layout.row", "label": "Row", "category": "layout", "container": True, "defaults": {"gap": "md", "wrap": True}},
    {"type": "layout.grid", "label": "Responsive Grid", "category": "layout", "container": True, "defaults": {"columns": {"mobile": 1, "tablet": 2, "desktop": 3}, "gap": "md"}},
    {"type": "layout.card", "label": "Card", "category": "layout", "container": True, "defaults": {"padding": "lg"}},
    {"type": "display.text", "label": "Text", "category": "display", "container": False, "defaults": {"text": "Text"}},
    {"type": "display.markdown", "label": "Markdown", "category": "display", "container": False, "defaults": {"value": ""}},
    {"type": "display.metric", "label": "Metric", "category": "display", "container": False, "defaults": {"label": "Metric", "value": 0}},
    {"type": "input.text", "label": "Text Input", "category": "input", "container": False, "defaults": {"label": "Input", "required": False}, "accessibility": {"requiredProperties": ["label"], "minimumTouchTarget": 44}},
    {"type": "action.workflow-run", "label": "Run Workflow", "category": "action", "container": False, "defaults": {"label": "Run", "workflowBinding": "main", "endpointId": "", "resultLabel": "Result"}, "accessibility": {"requiredProperties": ["label", "resultLabel"], "minimumTouchTarget": 44}},
    {"type": "data.table", "label": "Data Table", "category": "data", "container": False, "defaults": {"label": "Data table", "columns": [], "pageSize": 20, "enableCreate": False, "enableUpdate": False, "enableDelete": False}, "accessibility": {"requiredProperties": ["label"]}},
    {"type": "chart.line", "label": "Line Chart", "category": "chart", "container": False, "defaults": {"label": "Line chart", "series": [], "maxPoints": 1000}, "accessibility": {"requiredProperties": ["label"]}},
)

PROPERTY_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "layout.stack": [
        {"key": "gap", "label": "Gap", "type": "enum", "options": ["xs", "sm", "md", "lg", "xl"]},
        {"key": "direction", "label": "Direction", "type": "enum", "options": ["vertical", "horizontal"]},
    ],
    "layout.row": [
        {"key": "gap", "label": "Gap", "type": "enum", "options": ["xs", "sm", "md", "lg", "xl"]},
        {"key": "wrap", "label": "Wrap", "type": "boolean"},
    ],
    "layout.grid": [
        {"key": "columns", "label": "Responsive columns", "type": "responsive-columns", "breakpoints": ["mobile", "tablet", "desktop"], "minimum": 1, "maximum": 12},
        {"key": "gap", "label": "Gap", "type": "enum", "options": ["xs", "sm", "md", "lg", "xl"]},
    ],
    "layout.card": [{"key": "padding", "label": "Padding", "type": "enum", "options": ["xs", "sm", "md", "lg", "xl"]}],
    "display.text": [{"key": "text", "label": "Text", "type": "string"}],
    "display.markdown": [{"key": "value", "label": "Markdown", "type": "multiline"}],
    "display.metric": [
        {"key": "label", "label": "Label", "type": "string"},
        {"key": "value", "label": "Value", "type": "number"},
    ],
    "input.text": [
        {"key": "label", "label": "Label", "type": "string", "required": True},
        {"key": "placeholder", "label": "Placeholder", "type": "string"},
        {"key": "required", "label": "Required", "type": "boolean"},
    ],
    "action.workflow-run": [
        {"key": "label", "label": "Label", "type": "string", "required": True},
        {"key": "workflowBinding", "label": "Workflow binding", "type": "string", "required": True},
        {"key": "endpointId", "label": "API endpoint ID (optional)", "type": "string"},
        {"key": "resultLabel", "label": "Result label", "type": "string", "required": True},
    ],
    "data.table": [
        {"key": "label", "label": "Accessible label", "type": "string", "required": True},
        {"key": "columns", "label": "Columns", "type": "table-columns", "maximumItems": 50, "columnTypes": ["string", "number", "boolean", "date", "datetime", "json"]},
        {"key": "pageSize", "label": "Page size", "type": "number", "minimum": 1, "maximum": 1000},
        {"key": "enableCreate", "label": "Allow create", "type": "boolean"},
        {"key": "enableUpdate", "label": "Allow update", "type": "boolean"},
        {"key": "enableDelete", "label": "Allow delete", "type": "boolean"},
    ],
    "chart.line": [
        {"key": "label", "label": "Accessible label", "type": "string", "required": True},
        {"key": "series", "label": "Series", "type": "chart-series", "maximumItems": 20, "tones": ["accent", "success", "warning", "danger", "neutral"]},
        {"key": "maxPoints", "label": "Maximum points", "type": "number", "minimum": 1, "maximum": 10000},
    ],
}

BINDING_DEFINITIONS: tuple[dict[str, str], ...] = (
    {"id": "workflow-input", "label": "Workflow input", "referenceLabel": "Input name"},
    {"id": "workflow-output", "label": "Workflow output", "referenceLabel": "Output name"},
    {"id": "node-output", "label": "Node output", "referenceLabel": "Node and output path"},
    {"id": "api", "label": "API", "referenceLabel": "Endpoint and field"},
    {"id": "entity", "label": "Entity", "referenceLabel": "Entity and field"},
    {"id": "query", "label": "Query", "referenceLabel": "Query name"},
    {"id": "state", "label": "State", "referenceLabel": "State key"},
    {"id": "route", "label": "Route", "referenceLabel": "Route parameter"},
    {"id": "form", "label": "Form", "referenceLabel": "Field name"},
    {"id": "system", "label": "System", "referenceLabel": "System value"},
    {"id": "constant", "label": "Constant", "referenceLabel": "Non-secret value"},
)
BINDING_SOURCE_IDS = {item["id"] for item in BINDING_DEFINITIONS}

EVENT_ACTIONS: tuple[dict[str, Any], ...] = (
    {"id": "workflow-run", "label": "Run workflow", "targetLabel": "Workflow binding", "targetSection": "workflows"},
    {"id": "navigate", "label": "Navigate", "targetLabel": "Page ID", "targetSection": "pages"},
    {"id": "state-set", "label": "Set state", "targetLabel": "State key", "targetSection": "clientState"},
)
EVENT_ACTION_BY_ID = {item["id"]: item for item in EVENT_ACTIONS}

ACCESSIBILITY_AUDIT: dict[str, int | float] = {
    "minimumContrast": 4.5,
    "minimumLargeTextContrast": 3.0,
    "minimumTouchTarget": 44,
    "minimumFocusIndicator": 2,
}

COMPONENT_EVENTS: dict[str, list[dict[str, Any]]] = {
    "input.text": [
        {"name": "change", "label": "Change", "actions": ["state-set", "workflow-run"]},
        {"name": "submit", "label": "Submit", "actions": ["workflow-run", "navigate", "state-set"]},
    ],
    "action.workflow-run": [
        {"name": "success", "label": "Success", "actions": ["navigate", "state-set", "workflow-run"]},
        {"name": "error", "label": "Error", "actions": ["navigate", "state-set"]},
    ],
    "data.table": [
        {"name": "rowSelect", "label": "Row select", "actions": ["state-set", "navigate", "workflow-run"]},
        {"name": "refresh", "label": "Refresh", "actions": ["workflow-run"]},
    ],
    "chart.line": [{"name": "pointSelect", "label": "Point select", "actions": ["state-set", "navigate", "workflow-run"]}],
}


def _component_definitions() -> tuple[dict[str, Any], ...]:
    return tuple({
        **copy.deepcopy(item),
        "propertySchema": copy.deepcopy(PROPERTY_SCHEMAS.get(item["type"], [])),
        "eventSchema": copy.deepcopy(COMPONENT_EVENTS.get(item["type"], [])),
    } for item in COMPONENTS)


COMPONENT_BY_TYPE = {item["type"]: item for item in _component_definitions()}

PREVIEW_STATES: tuple[dict[str, str], ...] = (
    {"id": "default", "label": "Default", "description": "Normal populated state."},
    {"id": "loading", "label": "Loading", "description": "Data and actions are waiting."},
    {"id": "empty", "label": "Empty", "description": "No rows, series, or result are available."},
    {"id": "error", "label": "Error", "description": "A recoverable user-facing failure."},
    {"id": "disabled", "label": "Disabled", "description": "Controls are unavailable."},
)

# Application Specへ保存できる値はこのregistryだけを正とする。framework固有classや任意CSSは保存しない。
DESIGN_TOKENS: dict[str, list[str]] = {
    "color": ["neutral", "slate", "stone"],
    "surface": ["flat", "layered", "elevated"],
    "text": ["muted", "balanced", "high-contrast"],
    "accent": ["blue", "violet", "emerald", "amber"],
    "status": ["semantic", "muted", "high-contrast"],
    "spacing": ["xs", "sm", "md", "lg", "xl"],
    "radius": ["none", "sm", "md", "lg"],
    "shadow": ["none", "subtle", "medium", "strong"],
    "typography": ["system", "sans", "mono"],
    "density": ["compact", "comfortable", "touch"],
    "controlHeight": ["compact", "standard", "touch"],
    "motion": ["none", "reduced", "standard"],
    "breakpoint": ["mobile-first", "balanced", "desktop-first"],
    "zIndex": ["flat", "layered", "overlay"],
}

PRESETS: tuple[dict[str, Any], ...] = (
    {"id": "control-deck-modern", "label": "Modern", "description": "Balanced defaults for general applications.", "tokens": {"color": "slate", "surface": "layered", "text": "balanced", "accent": "blue", "status": "semantic", "spacing": "md", "radius": "md", "shadow": "subtle", "typography": "system", "density": "comfortable", "controlHeight": "standard", "motion": "standard", "breakpoint": "balanced", "zIndex": "layered"}},
    {"id": "compact", "label": "Compact", "description": "More information with compact controls.", "tokens": {"spacing": "sm", "radius": "sm", "shadow": "none", "density": "compact", "controlHeight": "compact", "motion": "reduced"}},
    {"id": "touch", "label": "Touch", "description": "Large controls and spacing for touch devices.", "tokens": {"spacing": "lg", "radius": "lg", "density": "touch", "controlHeight": "touch", "breakpoint": "mobile-first"}},
    {"id": "dashboard", "label": "Dashboard", "description": "Layered cards and dense metrics.", "tokens": {"surface": "layered", "accent": "violet", "spacing": "md", "shadow": "subtle", "density": "compact"}},
    {"id": "data-dense", "label": "Data Dense", "description": "Compact tables and operational data.", "tokens": {"surface": "flat", "spacing": "xs", "radius": "sm", "shadow": "none", "density": "compact", "controlHeight": "compact"}},
    {"id": "minimal", "label": "Minimal", "description": "Quiet surfaces with restrained motion.", "tokens": {"surface": "flat", "accent": "blue", "spacing": "lg", "radius": "sm", "shadow": "none", "motion": "reduced", "zIndex": "flat"}},
    {"id": "terminal", "label": "Terminal", "description": "High-contrast monospace operations view.", "tokens": {"color": "neutral", "surface": "flat", "text": "high-contrast", "accent": "emerald", "typography": "mono", "density": "compact", "motion": "none"}},
    {"id": "media", "label": "Media", "description": "Elevated content with generous spacing.", "tokens": {"surface": "elevated", "accent": "violet", "spacing": "xl", "radius": "lg", "shadow": "medium", "density": "comfortable"}},
)
PRESET_BY_ID = {item["id"]: item for item in PRESETS}


def _resolved_presets() -> list[dict[str, Any]]:
    base = PRESETS[0]["tokens"]
    return [{**copy.deepcopy(item), "tokens": {**base, **item["tokens"]}} for item in PRESETS]


def _component(component_id: str, component_type: str, properties: dict[str, Any] | None = None, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": component_id, "type": component_type, "properties": properties or {}, "children": children or []}


def _parameter(
    key: str, label: str, parameter_type: str, default: str | int | float | bool,
    component_id: str, property_name: str, **rules: Any,
) -> dict[str, Any]:
    return {
        "key": key, "label": label, "type": parameter_type, "default": default,
        "targets": [{"componentId": component_id, "property": property_name}], **rules,
    }


COMPOSITES: tuple[dict[str, Any], ...] = (
    {"id": "kpi-card", "label": "KPI Card", "category": "dashboard", "description": "Label and prominent metric in a card.", "parameters": [
        _parameter("metricLabel", "Metric label", "string", "Metric", "kpi-value", "label", required=True, maximumLength=80),
        _parameter("initialValue", "Initial value", "number", 0, "kpi-value", "value", minimum=-1_000_000_000, maximum=1_000_000_000),
    ], "root": _component("kpi-card", "layout.card", {"padding": "lg"}, [_component("kpi-value", "display.metric", {"label": "Metric", "value": 0})])},
    {"id": "job-status", "label": "Job Status", "category": "operations", "description": "Job state, summary, and primary action.", "parameters": [
        _parameter("title", "Title", "string", "Job status", "job-title", "text", required=True, maximumLength=120),
        _parameter("statusLabel", "Status label", "string", "Completed", "job-state", "label", required=True, maximumLength=80),
        _parameter("actionLabel", "Action label", "string", "Run job", "job-run", "label", required=True, maximumLength=80),
    ], "root": _component("job-card", "layout.card", {}, [_component("job-title", "display.text", {"text": "Job status"}), _component("job-state", "display.metric", {"label": "Completed", "value": 0}), _component("job-run", "action.workflow-run", {"label": "Run job", "workflowBinding": "main"})])},
    {"id": "log-viewer", "label": "Log Viewer", "category": "operations", "description": "Monospace-friendly log summary card.", "parameters": [
        _parameter("title", "Title", "string", "Recent logs", "log-title", "text", required=True, maximumLength=120),
        _parameter("emptyMessage", "Empty message", "string", "No log entries.", "log-body", "value", required=True, maximumLength=240),
    ], "root": _component("log-card", "layout.card", {}, [_component("log-title", "display.text", {"text": "Recent logs"}), _component("log-body", "display.markdown", {"value": "No log entries."})])},
    {"id": "crud-table", "label": "CRUD Table", "category": "data", "description": "Search, primary action, and data table.", "parameters": [
        _parameter("searchLabel", "Search label", "string", "Search", "crud-search", "label", required=True, maximumLength=80),
        _parameter("actionLabel", "Action label", "string", "Add item", "crud-add", "label", required=True, maximumLength=80),
        _parameter("tableLabel", "Table label", "string", "Items", "crud-table", "label", required=True, maximumLength=120),
    ], "root": _component("crud-stack", "layout.stack", {"gap": "md"}, [_component("crud-search", "input.text", {"label": "Search", "required": False}), _component("crud-add", "action.workflow-run", {"label": "Add item", "workflowBinding": "main"}), _component("crud-table", "data.table", {"label": "Items", "columns": [], "pageSize": 20})])},
    {"id": "timeline", "label": "Timeline", "category": "data", "description": "Ordered operational events.", "parameters": [
        _parameter("title", "Title", "string", "Timeline", "timeline-title", "text", required=True, maximumLength=120),
        _parameter("entryText", "Entry text", "string", "Event", "timeline-text", "text", required=True, maximumLength=200),
    ], "root": _component("timeline-stack", "layout.stack", {"gap": "sm"}, [_component("timeline-title", "display.text", {"text": "Timeline"}), _component("timeline-entry", "layout.card", {"padding": "md"}, [_component("timeline-text", "display.text", {"text": "Event"})])])},
)

PATTERNS: tuple[dict[str, Any], ...] = (
    {"id": "dashboard", "label": "Dashboard", "description": "Responsive KPI grid, chart, and activity table.", "parameters": [
        _parameter("title", "Title", "string", "Dashboard", "dashboard-title", "text", required=True, maximumLength=120),
        _parameter("metricLabel", "Metric label", "string", "Metric", "dashboard-kpi", "label", required=True, maximumLength=80),
        _parameter("chartLabel", "Chart label", "string", "Trend", "dashboard-chart", "label", required=True, maximumLength=120),
        _parameter("tableLabel", "Table label", "string", "Activity", "dashboard-table", "label", required=True, maximumLength=120),
    ], "root": _component("dashboard-stack", "layout.stack", {"gap": "lg"}, [_component("dashboard-title", "display.text", {"text": "Dashboard"}), _component("dashboard-grid", "layout.grid", {"columns": {"mobile": 1, "tablet": 2, "desktop": 3}, "gap": "md"}, [_component("dashboard-kpi", "display.metric", {"label": "Metric", "value": 0})]), _component("dashboard-chart", "chart.line", {"label": "Trend", "series": [], "maxPoints": 1000}), _component("dashboard-table", "data.table", {"label": "Activity", "columns": [], "pageSize": 20})])},
    {"id": "settings", "label": "Settings", "description": "Grouped settings with explicit save action.", "parameters": [
        _parameter("title", "Title", "string", "Settings", "settings-title", "text", required=True, maximumLength=120),
        _parameter("inputLabel", "Input label", "string", "Value", "settings-value", "label", required=True, maximumLength=80),
        _parameter("actionLabel", "Action label", "string", "Save settings", "settings-save", "label", required=True, maximumLength=80),
    ], "root": _component("settings-card", "layout.card", {}, [_component("settings-stack", "layout.stack", {"gap": "md"}, [_component("settings-title", "display.text", {"text": "Settings"}), _component("settings-value", "input.text", {"label": "Value", "required": False}), _component("settings-save", "action.workflow-run", {"label": "Save settings", "workflowBinding": "main"})])])},
    {"id": "wizard", "label": "Wizard", "description": "Guided form step with a clear continuation action.", "parameters": [
        _parameter("title", "Title", "string", "Step 1", "wizard-title", "text", required=True, maximumLength=120),
        _parameter("helpText", "Help text", "string", "Complete the fields to continue.", "wizard-help", "value", required=True, maximumLength=240),
        _parameter("inputLabel", "Input label", "string", "Name", "wizard-input", "label", required=True, maximumLength=80),
        _parameter("actionLabel", "Action label", "string", "Continue", "wizard-next", "label", required=True, maximumLength=80),
    ], "root": _component("wizard-stack", "layout.stack", {"gap": "lg"}, [_component("wizard-title", "display.text", {"text": "Step 1"}), _component("wizard-help", "display.markdown", {"value": "Complete the fields to continue."}), _component("wizard-input", "input.text", {"label": "Name", "required": True}), _component("wizard-next", "action.workflow-run", {"label": "Continue", "workflowBinding": "main"})])},
    {"id": "launcher", "label": "Launcher", "description": "Responsive grid of primary workflow actions.", "parameters": [
        _parameter("title", "Title", "string", "Launcher", "launcher-title", "text", required=True, maximumLength=120),
        _parameter("actionLabel", "Action label", "string", "Run workflow", "launcher-action", "label", required=True, maximumLength=80),
    ], "root": _component("launcher-stack", "layout.stack", {"gap": "lg"}, [_component("launcher-title", "display.text", {"text": "Launcher"}), _component("launcher-grid", "layout.grid", {"columns": {"mobile": 1, "tablet": 2, "desktop": 3}, "gap": "md"}, [_component("launcher-action", "action.workflow-run", {"label": "Run workflow", "workflowBinding": "main"})])])},
)


def component_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": 11,
        "components": copy.deepcopy(tuple(COMPONENT_BY_TYPE.values())),
        "designTokens": copy.deepcopy(DESIGN_TOKENS),
        "presets": _resolved_presets(),
        "composites": copy.deepcopy(COMPOSITES),
        "patterns": copy.deepcopy(PATTERNS),
        "previewStates": copy.deepcopy(PREVIEW_STATES),
        "bindingDefinitions": copy.deepcopy(BINDING_DEFINITIONS),
        "eventActions": copy.deepcopy(EVENT_ACTIONS),
        "accessibilityAudit": copy.deepcopy(ACCESSIBILITY_AUDIT),
        "bindingSources": [
            "workflow-input", "workflow-output", "node-output", "api", "entity", "query",
            "state", "route", "form", "system", "constant",
        ],
    }
