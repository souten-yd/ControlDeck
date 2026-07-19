from __future__ import annotations

from typing import Any


COMPONENTS: tuple[dict[str, Any], ...] = (
    {"type": "layout.stack", "label": "Stack", "category": "layout", "container": True, "defaults": {"gap": "md", "direction": "vertical"}},
    {"type": "layout.row", "label": "Row", "category": "layout", "container": True, "defaults": {"gap": "md", "wrap": True}},
    {"type": "layout.grid", "label": "Responsive Grid", "category": "layout", "container": True, "defaults": {"columns": {"mobile": 1, "tablet": 2, "desktop": 3}, "gap": "md"}},
    {"type": "layout.card", "label": "Card", "category": "layout", "container": True, "defaults": {"padding": "lg"}},
    {"type": "display.text", "label": "Text", "category": "display", "container": False, "defaults": {"text": "Text"}},
    {"type": "display.markdown", "label": "Markdown", "category": "display", "container": False, "defaults": {"value": ""}},
    {"type": "display.metric", "label": "Metric", "category": "display", "container": False, "defaults": {"label": "Metric", "value": 0}},
    {"type": "input.text", "label": "Text Input", "category": "input", "container": False, "defaults": {"label": "Input", "required": False}},
    {"type": "action.workflow-run", "label": "Run Workflow", "category": "action", "container": False, "defaults": {"label": "Run", "workflowBinding": "main"}},
    {"type": "data.table", "label": "Data Table", "category": "data", "container": False, "defaults": {"columns": [], "pageSize": 20}},
    {"type": "chart.line", "label": "Line Chart", "category": "chart", "container": False, "defaults": {"series": [], "maxPoints": 1000}},
)

COMPONENT_BY_TYPE = {item["type"]: item for item in COMPONENTS}


def component_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "components": [dict(item) for item in COMPONENTS],
        "designTokens": {
            "spacing": ["xs", "sm", "md", "lg", "xl"],
            "radius": ["none", "sm", "md", "lg"],
            "density": ["compact", "comfortable", "touch"],
        },
        "bindingSources": [
            "workflow-input", "workflow-output", "node-output", "api", "entity", "query",
            "state", "route", "form", "system", "constant",
        ],
    }
