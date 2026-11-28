"""全ランタイム共通のモデル目録（推奨モデルのカード）の唯一の情報源。

UI 担当の RuntimeCatalog 部品は `GET /api/v1/models/catalog`（この module の api_payload）を
読む。ランタイムごとの目録は各 module が持つ（Sly は sly_radiance_catalog、Strata は
strata_catalog）ので、ここでは集約と形の統一だけをする（二重持ちをしない）。

目録に載せる行は既存のデータ・出どころの確認できた repo id だけ:
- Sly-Radiance: sly_radiance_catalog.ENTRIES（README 確認済み）。
- Strata: strata_catalog（公式 README・docs/MODELS.md 確認済み。系列×量子化を 1 行に平らげる。
  GSQ-RCO の repo id は ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF）。
- Maya: Derisked（peasmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF。repo id のみ。
  大きさは未確認 → 取得の開始時に HF の API から読む）。
- llama.cpp / Radiance: 推奨行を置かない（llama.cpp は HF 検索の既存フロー、Radiance は
  .rad を選ぶ登録。確定した repo が調べられてから足す。推測で足さない）。

size_gib が None の行は「大きさ未確認」。URL・大きさ・sha を推測で書かない（取得の開始時に
HF の API から読む: POST /api/v1/models/acquire-jobs と GET .../entries/{id}/files）。
"""
from __future__ import annotations

from dataclasses import dataclass

RUNTIMES: tuple[dict, ...] = (
    {"id": "llama", "name": "llama.cpp", "formats": ("gguf",), "acquire": "hf",
     "search_ref": "/api/v1/models/hf/search"},
    {"id": "radiance", "name": "Radiance", "formats": ("rad",), "acquire": "none"},
    {"id": "sly-radiance", "name": "Sly-Radiance", "formats": ("safetensors",), "acquire": "hf"},
    {"id": "strata", "name": "Strata", "formats": ("strata-quant",), "acquire": "runtime"},
    {"id": "maya", "name": "Maya", "formats": ("maya-quant", "gguf"), "acquire": "hf",
     "catalog_ref": "/api/v1/features/maya-s/catalog"},
)

# Maya-M Derisked: 公式 docs/MODELS.md の無検閲寄り版（2026-11-28 に repo id を指定されたもの。
# 大きさは取得の開始時に HF の API から読む。ファイル名の実在は files エンドポイントで確認する）。
MAYA_DERISKED_REPO = "peasmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF"
# Strata 公式 GSQ-RCO の repo（初回起動で Strata 自身が取る。ここでは出どとして示すだけ）。
STRATA_GSQ_REPO = "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF"


@dataclass(frozen=True)
class Entry:
    runtime: str
    id: str
    name: str
    kind: str = "model"            # "model" | "drafter"
    format: str = "gguf"           # gguf | safetensors | rad | strata-quant | maya-quant
    repo: str = ""                 # HF の repo id（空 = HF 以外の経路）
    quantization: str = ""
    size_gib: float | None = None   # None = 未確認（推測しない）
    memory_gib: float | None = None
    uncensored: bool = False
    supported: bool = True
    note: str = ""
    acquire: str = "none"          # "hf" | "runtime" | "none"
    source: str = "catalog"        # "catalog"=既存目録 | "hf"=repo id だけ（大きさ未確認）

    def api_row(self, fit: str) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "format": self.format,
            "repo": self.repo, "quantization": self.quantization,
            "size_gib": self.size_gib, "memory_gib": self.memory_gib,
            "uncensored": self.uncensored, "supported": self.supported, "note": self.note,
            "acquire": self.acquire, "source": self.source, "fit": fit,
        }


def _sly_entries() -> list[Entry]:
    from app.models_mgmt import sly_radiance_catalog

    out = []
    for item in sly_radiance_catalog.ENTRIES:
        out.append(Entry(
            runtime="sly-radiance", id=item.id, name=item.name, kind=item.kind,
            format="safetensors", repo=item.repo, quantization=item.quantization,
            size_gib=item.size_gib, uncensored=item.uncensored,
            supported=item.supported, note=item.note,
            acquire="hf" if (item.supported and item.repo) else "none",
            source="catalog",
        ))
    return out


def _strata_entries() -> list[Entry]:
    from app.models_mgmt import strata_catalog

    out = []
    nvidia = strata_catalog.has_nvidia_gpu()      # sysfs の走査は 1 回だけ（系列ごとに繰り返さない）
    for family in strata_catalog.families():
        if not strata_catalog.visible(family.id, nvidia):
            continue
        for quant in family.quantizations:
            out.append(Entry(
                runtime="strata", id=f"{family.id}/{quant.id}",
                name=f"{family.short_name} · {quant.label}",
                format="strata-quant",
                repo=STRATA_GSQ_REPO if family.id == "qwen" else "",
                quantization=quant.id, size_gib=quant.download_gib,
                memory_gib=quant.memory_gib, uncensored=family.uncensored,
                supported=not family.manual,
                note=family.note, acquire="runtime", source="catalog",
            ))
    return out


def _maya_entries() -> list[Entry]:
    return [Entry(
        runtime="maya", id="glm-m-derisked", name="GLM-5.3-Flash Maya-M Derisked（無検閲寄り）",
        format="gguf", repo=MAYA_DERISKED_REPO, quantization="IQ2_S",
        size_gib=None, uncensored=True,
        note="拒否を減らす重みの編集（Derisked）・実験的。大きさは取得の開始時に HF から読み、"
             "登録は Maya 本体の目録（probe）から行う",
        acquire="hf", source="hf",
    )]


def entries_for(runtime: str) -> list[Entry]:
    if runtime == "sly-radiance":
        return _sly_entries()
    if runtime == "strata":
        return _strata_entries()
    if runtime == "maya":
        return _maya_entries()
    return []


def get_entry(runtime: str, entry_id: str) -> Entry | None:
    return next((e for e in entries_for(runtime) if e.id == entry_id), None)


def _fit_for(entry: Entry, ram_gib: float | None, vram_gib: float | None) -> str:
    from app.models_mgmt import sly_radiance_catalog, strata_catalog

    if entry.kind != "model" or not entry.supported:
        return sly_radiance_catalog.UNKNOWN
    if entry.runtime == "sly-radiance":
        return sly_radiance_catalog.fit(entry.size_gib, vram_gib)
    if entry.runtime == "strata" and "/" in entry.id:
        family, quant = entry.id.split("/", 1)
        return strata_catalog.fit(family, quant, ram_gib or 0.0, vram_gib or 0.0)
    return sly_radiance_catalog.UNKNOWN


def _installed(runtime: str) -> bool:
    try:
        if runtime == "llama":
            from app.models_mgmt import llama

            return bool(llama.installed_backends())
        if runtime == "radiance":
            from app.models_mgmt import radiance

            return radiance.installed()
        if runtime == "sly-radiance":
            from app.models_mgmt import sly_radiance

            return sly_radiance.installed()
        if runtime == "strata":
            from app.models_mgmt import strata

            return strata.installed()
        if runtime == "maya":
            from app.models_mgmt import maya_s

            return maya_s.installed()
    except Exception:  # noqa: BLE001 - 目録の表示で止まらない（sensor と同じ扱い。N/A は AGENTS.md 7）
        return False
    return False


def api_payload() -> dict:
    """GET /api/v1/models/catalog の本体（同期処理なので endpoint は def にする）。"""
    from app.models_mgmt import strata_catalog

    ram, vram = strata_catalog.machine_gib()
    blocks = []
    for spec in RUNTIMES:
        rows = [entry.api_row(_fit_for(entry, ram, vram)) for entry in entries_for(spec["id"])]
        block = {
            "id": spec["id"], "name": spec["name"], "installed": _installed(spec["id"]),
            "formats": list(spec["formats"]), "acquire": spec["acquire"], "entries": rows,
        }
        if spec.get("catalog_ref"):
            block["catalog_ref"] = spec["catalog_ref"]
        if spec.get("search_ref"):
            block["search_ref"] = spec["search_ref"]
        blocks.append(block)
    return {"runtimes": blocks, "hardware": {"ram_total_gib": ram, "vram_total_gib": vram}}
