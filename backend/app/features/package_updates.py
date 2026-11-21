"""pip・npm で入れたオプション機能の「更新あり」の確認（PyPI / npm の公開情報を読むだけ。取得はしない）。

GPU ランタイムの release-status と同じ形（installed_tag・latest_tag・update_available・published_at・error）で返す。
問い合わせは Web 収集エンジンの安全な取得（egress）を通す。結果は 30 分覚える（開くたびに問い合わせない）。
"""
from __future__ import annotations

import re
import time

from app.features import registry

PACKAGE_KINDS = ("npm", "pip", "python-lib", "opencode-plugin", "opencode-mcp")
_CACHE_S = 1800
_cache: dict[str, tuple[float, dict]] = {}


def _npm_name(spec: dict) -> str:
    package = spec.get("latest_package") or spec.get("package") or ""
    if "git+" in package:
        return ""
    name = package.rsplit("@", 1)[0] if package.count("@") > (1 if package.startswith("@") else 0) else package
    return name


def _pypi_name(spec: dict) -> str:
    if spec["kind"] == "python-lib":
        return spec["distribution"]
    return re.split(r"[=<>!~\[ ]", spec.get("package", ""), maxsplit=1)[0]


def _plain(version: str) -> str:
    match = re.search(r"\d+(?:\.\d+)+(?:[a-z]+\d*)?", version or "")
    return match.group(0) if match else (version or "").strip()


def _newer(latest: str, current: str) -> bool:
    def key(v: str) -> tuple:
        return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", v)[:4])
    return bool(latest and current and key(latest) > key(current))


async def _get_json(url: str) -> dict:
    import json

    from app.web_collector import egress

    async with egress.guarded_client(timeout=15, headers={"Accept": "application/json",
                                                          "User-Agent": "ControlDeck-Features/1.0"}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return json.loads(response.content[:5_000_000])


async def release_status(feature_id: str) -> dict:
    spec = registry.FEATURES[feature_id]
    now = time.monotonic()
    cached = _cache.get(feature_id)
    if cached and now - cached[0] < _CACHE_S:
        return dict(cached[1])
    current = _plain(registry.status(feature_id).get("version", ""))
    out = {"feature_id": feature_id, "installed_tag": current, "latest_tag": "", "update_available": False,
           "published_at": "", "error": ""}
    try:
        if spec["kind"] in ("pip", "python-lib"):
            name = _pypi_name(spec)
            data = await _get_json(f"https://pypi.org/pypi/{name}/json")
            latest = data["info"]["version"]
            files = data.get("urls") or []
            out["published_at"] = files[0].get("upload_time_iso_8601", "") if files else ""
        else:
            name = _npm_name(spec)
            if not name:
                out["error"] = "この機能は版の番号での確認に対応していません（更新ボタンで最新版を入れられます）"
                _cache[feature_id] = (now, out)
                return dict(out)
            data = await _get_json(f"https://registry.npmjs.org/{name.replace('/', '%2F')}/latest")
            latest = data.get("version", "")
        out["latest_tag"] = latest
        out["update_available"] = _newer(_plain(latest), current)
    except Exception as exc:  # noqa: BLE001 - 確認できないだけ（更新ボタンは使える）
        out["error"] = f"最新版を確認できません: {type(exc).__name__}"
    _cache[feature_id] = (now, out)
    return dict(out)


def forget(feature_id: str) -> None:
    _cache.pop(feature_id, None)
