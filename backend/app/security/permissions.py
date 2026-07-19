"""権限とロール定義。"""
from __future__ import annotations

ALL_PERMISSIONS: list[str] = [
    "apps.view",
    "apps.start",
    "apps.stop",
    "apps.edit",
    "apps.delete",
    "logs.view",
    "logs.delete",
    "files.view",
    "files.edit",
    "files.delete",
    "terminal.use",
    "workflows.edit",
    "workflows.run",
    "application_builder.view",
    "application_builder.edit",
    "system.view",
    "power.manage",
    "remote_desktop.use",
    "users.manage",
    "settings.manage",
    "audit.view",
]

ROLE_PRESETS: dict[str, list[str]] = {
    "administrator": list(ALL_PERMISSIONS),
    "operator": [
        "apps.view",
        "apps.start",
        "apps.stop",
        "apps.edit",
        "logs.view",
        "files.view",
        "files.edit",
        "terminal.use",
        "workflows.run",
        "system.view",
    ],
    "viewer": ["apps.view", "logs.view", "system.view"],
}
