"""Sly-Radiance の使えるモデルの目録（唯一の情報源）。

出典: SlyBase/vllm-sly-radiance の README「Tech stack」と「Other checkpoints」（2026-11-27 に確認）。
大きさは README に書かれているものだけ（他は None＝未確認）。AutoRound・escha は具体的な repo が
README に無いので載せない。MiniCPM-V 4.7 は別の attention backend と多くの引数が違うので、
目録には「未対応」として載せるだけで、登録・取得には使えない。
"""
from __future__ import annotations

from dataclasses import dataclass

FITS, TIGHT, NO_FIT, UNKNOWN = "fits", "tight", "no", "unknown"


@dataclass(frozen=True)
class Entry:
    id: str
    repo: str
    name: str
    kind: str                  # "model" | "drafter"
    quantization: str          # sly_radiance.QUANT_SWITCHES のキー（drafter は ""）
    size_gib: float | None
    uncensored: bool = False
    supported: bool = True
    note: str = ""


ENTRIES: tuple[Entry, ...] = (
    Entry("swift-heretic", "slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ",
          "Swift-1.5 Qwen3.8-27B heretic MXFP4（標準）", "model", "", 19.8, uncensored=True,
          note="README の標準モデル。拒否を減らす編集（heretic）済み。5GiB ごとの分割"),
    Entry("quark-awq", "amd/Qwen3.8-27B-Quark-AWQ-MXFP4", "Qwen3.8-27B Quark AWQ MXFP4（AMD）",
          "model", "quark", 19.8),
    Entry("nvfp4", "unsloth/Qwen3.8-27B-NVFP4", "Qwen3.8-27B NVFP4（unsloth）", "model", "nvfp4", 23.4,
          note="RADIANCE_NVFP4_MXFP4=1 で MXFP4 の経路に載せる"),
    Entry("paroquant", "z-lab/Qwen3.8-27B-PARO", "Qwen3.8-27B ParoQuant", "model", "paroquant", 18.8),
    Entry("dflash2", "syvai/Qwen3.8-27B-DFlash2-W4A16", "先読み DFlash2（W4A16）", "drafter", "", 1.3,
          note="上のどのモデルとも使える先読みモデル（drafter）"),
    Entry("minicpm-v", "", "MiniCPM-V 4.7", "model", "", None, supported=False,
          note="未対応（別の attention backend と RADIANCE_MINICPMV4_7=1 など引数が違う）"),
)


def get(entry_id: str) -> Entry | None:
    return next((e for e in ENTRIES if e.id == entry_id), None)


def fit(size_gib: float | None, vram_gib: float | None) -> str:
    """重みの大きさと VRAM を比べる軽い判定（KV の分に 1.15 倍の余裕を見る）。"""
    if not size_gib or not vram_gib:
        return UNKNOWN
    if vram_gib >= size_gib * 1.15:
        return FITS
    return TIGHT if vram_gib >= size_gib else NO_FIT


def api_payload() -> dict:
    """GET /api/v1/models/sly-radiance/catalog の本体（同期処理なので endpoint は def）。"""
    from app.models_mgmt import strata_catalog

    _ram, vram = strata_catalog.machine_gib()
    return {"entries": [{
        "id": e.id, "repo": e.repo, "name": e.name, "kind": e.kind, "quantization": e.quantization,
        "size_gib": e.size_gib, "uncensored": e.uncensored, "supported": e.supported, "note": e.note,
        "fit": fit(e.size_gib, vram) if e.kind == "model" and e.supported else UNKNOWN,
    } for e in ENTRIES], "hardware": {"vram_total_gib": vram}}
