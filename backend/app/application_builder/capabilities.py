from __future__ import annotations

import platform
import shutil
from typing import Any

from app.workflows.node_metadata import node_catalog
from app.application_builder.source_generator import GENERATOR_ID, GENERATOR_VERSION, SUPPORTED_NODES

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
    {"id": "aspnet-blazor", "label": "ASP.NET Core + Blazor", "language": "csharp", "platforms": ["web", "linux", "windows"], "status": "design", "source": True, "build": True, "package": False, "phase": "E7 source + isolated local build"},
    {"id": "aspnet-react", "label": "ASP.NET Core + React/PWA", "language": "csharp", "platforms": ["web", "linux", "windows"], "status": "design", "source": False, "build": False, "package": False, "phase": "C-D"},
    {"id": "csharp-console", "label": "C# Console / Service", "language": "csharp", "platforms": ["linux", "windows"], "status": "design", "source": True, "build": True, "package": False, "phase": "B2.5 source + isolated local build"},
    {"id": "avalonia", "label": "Avalonia", "language": "csharp", "platforms": ["windows", "linux", "macos", "android", "ios", "web"], "status": "planned", "source": False, "build": False, "package": False, "phase": "G2"},
    {"id": "tauri-react", "label": "Tauri 2 + React", "language": "rust/typescript", "platforms": ["windows", "linux", "macos", "android", "ios"], "status": "planned", "source": False, "build": False, "package": False, "phase": "G2"},
    {"id": "electron", "label": "Electron", "language": "typescript", "platforms": ["windows", "linux", "macos"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "flutter", "label": "Flutter", "language": "dart", "platforms": ["web", "windows", "linux", "macos", "android", "ios"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "compose", "label": "Compose Multiplatform", "language": "kotlin", "platforms": ["windows", "linux", "macos", "android", "ios", "web"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "maui", "label": ".NET MAUI", "language": "csharp", "platforms": ["windows", "macos", "android", "ios"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
    {"id": "qt", "label": "C++ / Qt", "language": "cpp", "platforms": ["windows", "linux", "macos", "android", "ios"], "status": "advisor-only", "source": False, "build": False, "package": False, "phase": "later"},
]
FRAMEWORK_BY_ID = {item["id"]: item for item in FRAMEWORKS}

FRAMEWORK_DETAILS: dict[str, dict[str, Any]] = {
    "aspnet-blazor": {"sdks": ["dotnet"], "features": {"offline": True, "localFiles": False, "tray": False, "background": True, "gpu": False, "embeddedServer": True, "store": False, "nativeFeel": False, "webReuse": True, "smallSize": False}},
    "aspnet-react": {"sdks": ["dotnet", "node"], "features": {"offline": True, "localFiles": False, "tray": False, "background": True, "gpu": False, "embeddedServer": True, "store": False, "nativeFeel": False, "webReuse": True, "smallSize": False}},
    "csharp-console": {"sdks": ["dotnet"], "features": {"offline": True, "localFiles": True, "tray": False, "background": True, "gpu": True, "embeddedServer": True, "store": False, "nativeFeel": False, "webReuse": False, "smallSize": True}},
    "avalonia": {"sdks": ["dotnet"], "features": {"offline": True, "localFiles": True, "tray": True, "background": True, "gpu": True, "embeddedServer": True, "store": True, "nativeFeel": True, "webReuse": False, "smallSize": False}},
    "tauri-react": {"sdks": ["node", "rust"], "features": {"offline": True, "localFiles": True, "tray": True, "background": True, "gpu": False, "embeddedServer": True, "store": True, "nativeFeel": True, "webReuse": True, "smallSize": True}},
    "electron": {"sdks": ["node"], "features": {"offline": True, "localFiles": True, "tray": True, "background": True, "gpu": True, "embeddedServer": True, "store": True, "nativeFeel": False, "webReuse": True, "smallSize": False}},
    "flutter": {"sdks": ["flutter"], "features": {"offline": True, "localFiles": True, "tray": False, "background": True, "gpu": True, "embeddedServer": False, "store": True, "nativeFeel": True, "webReuse": False, "smallSize": False}},
    "compose": {"sdks": ["java"], "features": {"offline": True, "localFiles": True, "tray": True, "background": True, "gpu": True, "embeddedServer": True, "store": True, "nativeFeel": True, "webReuse": False, "smallSize": False}},
    "maui": {"sdks": ["dotnet"], "features": {"offline": True, "localFiles": True, "tray": False, "background": True, "gpu": True, "embeddedServer": True, "store": True, "nativeFeel": True, "webReuse": False, "smallSize": False}},
    "qt": {"sdks": ["cmake"], "features": {"offline": True, "localFiles": True, "tray": True, "background": True, "gpu": True, "embeddedServer": True, "store": True, "nativeFeel": True, "webReuse": False, "smallSize": True}},
}


def host_capabilities() -> dict[str, Any]:
    from app.application_builder.builds import dotnet_sdk_path

    return {
        "os": platform.system().lower(), "architecture": platform.machine(),
        "sdks": {
            "dotnet": dotnet_sdk_path() is not None, "node": bool(shutil.which("node")),
            "rust": bool(shutil.which("cargo")), "flutter": bool(shutil.which("flutter")),
            "java": bool(shutil.which("java")), "cmake": bool(shutil.which("cmake")),
        },
        "note": "SDK検出はread-onlyです。Source生成は副作用なし、Buildは明示操作時だけ隔離systemd user unitで実行します。",
    }


def framework_matrix(framework_id: str, platforms: list[str] | None = None) -> dict[str, str]:
    framework = FRAMEWORK_BY_ID[framework_id]
    selected_platforms = platforms or list(framework["platforms"])
    apple = bool({"ios", "macos"} & set(selected_platforms))
    return {
        "spec": "available", "source": "available" if framework["source"] else "unavailable",
        "localBuild": "available" if framework["build"] else ("requires-macos" if apple else "unavailable"),
        "remoteBuild": "unavailable", "package": "available" if framework["package"] else "unavailable",
        "signing": "requires-macos" if apple else "unavailable",
        "store": "unavailable", "stability": str(framework["status"]),
    }


def node_support(node_type: str, target: str) -> dict[str, Any]:
    if target == "csharp":
        if node_type in SUPPORTED_NODES:
            return {
                "support": "native", "planned_support": "native", "source_available": True,
                "generator": f"{GENERATOR_ID}/{GENERATOR_VERSION}", "reason": "B2 C# Console generatorで生成できます",
            }
        if node_type in NATIVE_CSHARP:
            return {
                "support": "manual", "planned_support": "native", "source_available": False,
                "generator": "", "reason": "このnodeのC# source generatorは後続B2で実装予定",
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
    from app.application_builder.builds import build_capability

    build = build_capability()
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
        "phase": "B2.5",
        "generationAvailable": True,
        "buildAvailable": build["available"],
        "build": build,
        "designProposalAvailable": True,
        "frameworks": [{
            **item,
            "build": bool(build["available"] and item["source"] and item["id"] in {"csharp-console", "aspnet-blazor"}),
            "details": FRAMEWORK_DETAILS[item["id"]],
            "matrix": {
                **framework_matrix(item["id"]),
                "localBuild": "available" if build["available"] and item["id"] in {"csharp-console", "aspnet-blazor"}
                else framework_matrix(item["id"])["localBuild"],
            },
        } for item in FRAMEWORKS],
        "nodes": nodes,
        "host": host_capabilities(),
    }
