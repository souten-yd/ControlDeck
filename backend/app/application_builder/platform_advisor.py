from __future__ import annotations

from typing import Any

from app.application_builder.capabilities import (
    FRAMEWORKS, FRAMEWORK_BY_ID, FRAMEWORK_DETAILS, framework_matrix, host_capabilities,
)
from app.schemas.application_builder import PlatformAdvisorRequest
from app.application_builder.source_generator import target_generator_diagnostics

FEATURE_LABELS = {
    "offline": "offline", "localFiles": "local file", "tray": "tray",
    "background": "background", "gpu": "GPU", "embeddedServer": "embedded server", "store": "store",
    "nativeFeel": "native feel", "webReuse": "web reuse", "smallSize": "small package",
}


def advise_platforms(request: PlatformAdvisorRequest) -> dict[str, Any]:
    host = host_capabilities()
    requested_features = {
        "offline": request.offline, "localFiles": request.local_files, "tray": request.tray,
        "background": request.background, "gpu": request.gpu, "embeddedServer": request.embedded_server,
        "store": request.store, "nativeFeel": request.prefer_native_feel,
        "webReuse": request.prefer_web_reuse, "smallSize": request.prefer_small_size,
    }
    recommendations: list[dict[str, Any]] = []
    for order, framework in enumerate(FRAMEWORKS):
        details = FRAMEWORK_DETAILS[framework["id"]]
        score = {"design": 30, "planned": 15, "advisor-only": 5}.get(framework["status"], 0)
        reasons: list[str] = []
        constraints: list[str] = []
        for target_platform in request.platforms:
            if target_platform in framework["platforms"]:
                score += 15
                reasons.append(f"{target_platform}を対象にできます")
            else:
                score -= 60
                constraints.append(f"{target_platform}は対象外です")
        for key, requested in requested_features.items():
            if not requested:
                continue
            if details["features"].get(key):
                score += 8
                reasons.append(f"{FEATURE_LABELS[key]}要件に適合します")
            else:
                score -= 20
                constraints.append(f"{FEATURE_LABELS[key]}要件へ直接対応しません")
        if request.preferred_language != "any":
            if request.preferred_language in framework["language"]:
                score += 10
                reasons.append(f"希望言語{request.preferred_language}に一致します")
            else:
                score -= 4
        missing_sdks = [sdk for sdk in details["sdks"] if not host["sdks"].get(sdk, False)]
        if missing_sdks:
            constraints.append(f"このhostにSDKがありません: {', '.join(missing_sdks)}")
        else:
            score += 3
            reasons.append("必要SDKをhostで検出しました")
        if not framework["source"]:
            constraints.append("source generatorは未実装です")
        if {"ios", "macos"} & set(request.platforms) and host["os"] != "darwin":
            constraints.append("Apple向けbuild／signingにはmacOS hostが必要です")
        recommendations.append({
            "frameworkId": framework["id"], "label": framework["label"], "score": score,
            "platforms": framework["platforms"], "language": framework["language"],
            "status": framework["status"], "reasons": _unique(reasons), "constraints": _unique(constraints),
            "matrix": framework_matrix(framework["id"], request.platforms), "order": order,
        })
    recommendations.sort(key=lambda item: (-item["score"], item["order"]))
    for item in recommendations:
        item.pop("order", None)
    return {
        "phase": "B1", "recommendedId": recommendations[0]["frameworkId"],
        "requestedPlatforms": request.platforms, "recommendations": recommendations, "host": host,
        "note": "推薦は固定registryによる決定的scoreです。overrideと複数targetを許可しますが、preflightを省略できません。",
    }


def preflight_application(spec: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    host = host_capabilities()
    diagnostics = list(validation.get("diagnostics") or [])
    targets = spec.get("targets") if isinstance(spec.get("targets"), list) else []
    profiles: list[dict[str, Any]] = []
    if not targets:
        diagnostics.append(_issue("TARGET_REQUIRED", "error", "targetを1件以上指定してください", "targets"))
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            diagnostics.append(_issue("TARGET_PROFILE_INVALID", "error", "targetはobjectで指定してください", f"targets.{index}"))
            continue
        framework_id = str(target.get("framework") or "")
        framework = FRAMEWORK_BY_ID.get(framework_id)
        if framework is None:
            continue
        platforms = target.get("platforms") if isinstance(target.get("platforms"), list) else []
        selected_platforms = [str(item) for item in platforms]
        unsupported = [item for item in selected_platforms if item not in framework["platforms"]]
        if not selected_platforms:
            diagnostics.append(_issue("TARGET_PLATFORM_REQUIRED", "error", "platformを1件以上指定してください", f"targets.{index}.platforms"))
        if unsupported:
            diagnostics.append(_issue("TARGET_PLATFORM_UNSUPPORTED", "error", f"{framework_id}は次のplatformへ対応しません: {', '.join(unsupported)}", f"targets.{index}.platforms"))
        matrix = framework_matrix(framework_id, selected_platforms)
        if matrix["source"] != "available":
            diagnostics.append(_issue("SOURCE_GENERATOR_UNAVAILABLE", "error", f"{framework['label']} source generatorは未実装です", f"targets.{index}.framework"))
        if matrix["source"] == "available":
            workflow_ir = validation.get("workflowIr") or {}
            diagnostics.extend(
                item.model_dump(by_alias=True)
                for item in target_generator_diagnostics(spec, workflow_ir or None, target_id=str(target.get("id") or framework_id))
            )
        details = FRAMEWORK_DETAILS[framework_id]
        missing_sdks = [sdk for sdk in details["sdks"] if not host["sdks"].get(sdk, False)]
        if missing_sdks:
            diagnostics.append(_issue("HOST_SDK_MISSING", "warning", f"local build SDKがありません: {', '.join(missing_sdks)}", f"targets.{index}.framework"))
        if {"ios", "macos"} & set(selected_platforms) and host["os"] != "darwin":
            diagnostics.append(_issue("APPLE_BUILD_HOST_REQUIRED", "error", "Apple向けbuild／signingにはmacOS hostが必要です", f"targets.{index}.platforms"))
        profiles.append({
            "id": str(target.get("id") or framework_id), "frameworkId": framework_id,
            "label": framework["label"], "platforms": selected_platforms, "matrix": matrix,
            "requiredSdks": details["sdks"], "missingSdks": missing_sdks,
        })
    unique = _deduplicate_diagnostics(diagnostics)
    errors = [item for item in unique if item.get("severity") == "error"]
    return {
        "phase": "B1", "validSpec": bool(validation.get("valid")), "readyForGeneration": not errors and bool(profiles),
        "readyForLocalBuild": not errors and bool(profiles) and all(item["matrix"]["localBuild"] == "available" and not item["missingSdks"] for item in profiles),
        "targets": profiles, "diagnostics": unique, "host": host,
        "sideEffects": {"executor": False, "network": False, "subprocess": False, "filesystemWrite": False, "secretResolution": False},
    }


def _issue(code: str, severity: str, message: str, path: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "path": path, "source": "platform-preflight", "suggestedFix": "", "autoFix": False}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _deduplicate_diagnostics(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        key = (str(item.get("code")), str(item.get("path")), str(item.get("message")))
        if key not in seen:
            seen.add(key); result.append(item)
    return result
