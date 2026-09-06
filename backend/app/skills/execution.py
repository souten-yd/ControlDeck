"""Bounded, synchronous prerequisite checks; call only from a worker thread."""
from __future__ import annotations

import json

import httpx

from app.addons import registry as addons
from app.addons.health import approved_health_url
from app.skills.catalog import SkillEntry


def check(entry: SkillEntry) -> dict[str, object]:
    requirement = entry.execution
    if requirement is None:
        return {"state": "ready", "message": "実行前提の追加確認は不要です。"}
    try:
        current = addons.status(requirement.addon_id)
        if not current.get("enabled"):
            return {"state": "unavailable", "message": "必要なAdd-onが未導入または無効です。"}
        declared = {item["id"] for item in current.get("contributions", {}).get("agent_tools", [])}
        if not set(requirement.tool_ids) <= declared:
            return {"state": "unavailable", "message": "必要なAgent toolが不足しています。Add-onを更新してください。"}
        url = approved_health_url(current["runtime"]["base_url"], requirement.capability_path)
        with httpx.Client(timeout=3.0, follow_redirects=False) as client:
            with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    content.extend(chunk)
                    if len(content) > 64 * 1024:
                        raise ValueError("capability response too large")
        capability = json.loads(content).get("capabilities", {}).get(requirement.capability, {})
        if capability.get("state") != "available":
            return {"state": "unavailable", "message": "3D実行環境が利用できません。Add-onの設定でBlender基本環境を導入・修復してください。"}
        if capability.get("schema_version") != requirement.schema_version or capability.get("local_only") is not True:
            return {"state": "unavailable", "message": "実行契約が対応版と一致しません。Add-onの互換性を確認してください。"}
        return {"state": "ready", "message": "型付き3D制作を実行できます。GUI常駐不要。実行時にも権限とcapabilityを確認します。"}
    except (addons.AddonRegistryError, httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"state": "unavailable", "message": "実行先の健全性を確認できません。Add-onの起動状態を確認してください。"}
