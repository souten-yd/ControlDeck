from __future__ import annotations

import platform
import shutil
from typing import Any

from app.workflows.node_metadata import node_catalog

NATIVE_CSHARP = {
    "trigger", "condition.if", "control.loop", "control.merge", "util.wait", "util.now", "var.set",
    "string.op", "data.transform", "data.template", "data.filter", "data.aggregate", "file.read",
    "file.write", "file.exists", "file.glob", "http.request", "http.download", "db.query",
    "output.render", "signal.display", "notify.webhook",
}
REMOTE_CSHARP = {
    "app.start", "app.stop", "app.restart", "app.status", "llm.chat", "rag.build", "rag.query",
    "research.deep", "web.browser", "code.agent", "cmd.python", "cmd.ssh", "cmd.git", "cmd.cpp_build",
    "flow.call", "human.approval",
}

FRAMEWORKS: list[dict[str, Any]] = [
    {"id": "aspnet-blazor", "label": "ASP.NET Core + Blazor", "language": "csharp", "platforms": ["web", "linux", "windows"], "status": "design", "source": False, "build": False, "package": False, "phase": "B-D"},
    {"id": "aspnet-react", "label": "ASP.NET Core + React/PWA", "language": "csharp", "platforms": ["web", "linux", "windows"], "status": "design", "source": False, "build": False, "package": False, "phase": "C-D"},
    {"id": "csharp-console", "label": "C# Console / Service", "language": "csharp", "platforms": ["linux", "windows"], "status": "design", "source": False, "build": False, "package": False, "phase": "B"},
    {"id": "avalonia", "label": "Avalonia", "language": "csharp", "platforms": ["windows", "linux", "macos", "android", "ios", "web"], "status": "planned", "source": False, "build": False, "package": False, "phase": "G2"},
    {"id": "tauri-react", "label": "Tauri 2 + React", "language": "rust/typescript", "platforms": ["windows", "linux", "macos", "android", "ios"], "status": "planned", "source": False, "build": False, "package": False, "phase": "G2"},
    {"id": "electron", "label": "Electron", "language": "typescript", "platforms": ["windows", "linux", "macos"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "flutter", "label": "Flutter", "language": "dart", "platforms": ["web", "windows", "linux", "macos", "android", "ios"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "compose", "label": "Compose Multiplatform", "language": "kotlin", "platforms": ["windows", "linux", "macos", "android", "ios", "web"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "maui", "label": ".NET MAUI", "language": "csharp", "platforms": ["windows", "macos", "android", "ios"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "qt", "label": "C++ / Qt", "language": "cpp", "platforms": ["windows", "linux", "macos", "android", "ios"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
]


def node_support(node_type: str, target: str) -> dict[str, Any]:
    if target == "csharp":
        if node_type in NATIVE_CSHARP:
            return {
                "support": "manual", "planned_support": "native", "source_available": False,
                "generator": "", "reason": "C# source generatorはPhase Bで実装予定",
            }
        if node_type in REMOTE_CSHARP:
            return {
                "support": "external", "planned_support": "external", "source_available": False,
                "generator": "", "reason": "Phase AではControlDeck境界として分類。adapter生成は未実装",
            }
        return {
            "support": "manual", "planned_support": "runtime", "source_available": False,
            "generator": "", "reason": "runtime adapterは未実装",
        }
    if target == "cpp":
        return {"support": "unsupported", "planned_support": None, "source_available": False, "generator": "", "reason": "C++ generatorは未実装"}
    return {"support": "unsupported", "planned_support": None, "source_available": False, "generator": "", "reason": f"target '{target}' は未登録"}


def capability_catalog() -> dict[str, Any]:
    nodes = []
    for metadata in node_catalog():
        nodes.append({
            "type": metadata["type"],
            "version": metadata["version"],
            "sideEffect": metadata["side_effect"],
            "capabilities": metadata["capabilities"],
            "targets": {target: node_support(metadata["type"], target) for target in ("csharp", "cpp")},
        })
    return {
        "phase": "A",
        "generationAvailable": False,
        "buildAvailable": False,
        "frameworks": FRAMEWORKS,
        "nodes": nodes,
        "host": {
            "os": platform.system().lower(),
            "architecture": platform.machine(),
            "sdks": {
                "dotnet": bool(shutil.which("dotnet")), "node": bool(shutil.which("node")),
                "rust": bool(shutil.which("cargo")), "flutter": bool(shutil.which("flutter")),
            },
            "note": "SDK検出はread-onlyです。Phase Aはsource生成・buildを実行しません。",
        },
    }
