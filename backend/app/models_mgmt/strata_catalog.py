"""Strata 0.2.22 の系列・量子化の目録（唯一の情報源）。

表示名・大きさ・メモリの目安・エンジン・思考・GPU の制限をここだけに持ち、
gateway・router・表示名・HF 検索・画面（API 経由）はここから読む。
出典: Strata 公式 README と docs/MODELS.md（0.2.22、2026-11-26 に確認）。
小さい unsloth 6 種は README の表に無いが、同梱の gguf_fingerprints.json に載るので
「大きさは未確認」（None）として含める。公式の目録に無い OrcaRouter は無検閲・手作業の
案内行として含め、登録には使えない（manual=True）。

「載るか」は RAM + VRAM の合計とメモリの目安（RAM+VRAM）だけを比べる軽い判定。
合計が目安の 1.1 倍以上なら「fits」、目安以上なら「tight」、足りなければ「no」、
目安が不明なら「unknown」。実際の KV・セッション量は strata_memory の概算を使う。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

GIB = 1024 ** 3
NVIDIA_VENDOR = "0x10de"

FITS = "fits"
TIGHT = "tight"
NO_FIT = "no"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Quantization:
    """1 つの `--model` の値。download_gib / memory_gib が None は「未確認」。"""

    id: str
    label: str
    download_gib: float | None
    memory_gib: float | None


@dataclass(frozen=True)
class Family:
    """1 つの `--family` の値。"""

    id: str
    name: str                       # 画面に出す表示名
    short_name: str                 # 登録済みモデルの一覧など、短い名前が必要な所
    engine: str                     # "qwen" = strata-engine（Qwen 用引数が効く）/"other" = 別エンジン
    thinking: bool
    quantizations: tuple[Quantization, ...]
    note: str = ""
    requires_nvidia: bool = False
    uncensored: bool = False
    manual: bool = False            # 手作業の変換が要る。目録には出すが登録できない


def _q(quant_id: str, download: float | None, memory: float | None) -> Quantization:
    return Quantization(quant_id, quant_id.upper(), download, memory)


# Qwen3.8-Flash-Next（GSQ-RCO）の量子化。Swift 1.5 はメモリの目安が同じ。
_QWEN = (
    _q("q2_0", 66.0, 37.6),
    _q("iq2_xs", 70.0, 39.2),
    _q("iq3_xxs", 76.0, 47.0),
    _q("iq3_s", None, 54.8),
)
_SWIFT = tuple(replace(item, download_gib=None) for item in _QWEN)
_UNSLOTH = (
    _q("ud-iq1_m", None, None), _q("ud-q2_0", None, None), _q("ud-iq2_xs", None, None),
    _q("ud-q2_k_xl", None, None), _q("ud-iq3_xxs", None, None), _q("ud-q3_k_xl", None, None),
    _q("ud-iq4_xs", 94.0, 72.0), _q("ud-q4_k_xl", 111.0, 88.0), _q("ud-q5_k_xl", 158.0, 108.0),
    _q("ud-q6_k_xl", 169.0, 118.0), _q("q8_0", 188.0, 136.0),
)
# Inkling はファイルがそのまま GPU と RAM に載る（公式の表の「File size」）。メモリの目安はファイルの大きさにする。
_INKLING = (
    _q("sq-20", 19.3, 19.3), _q("sq-15", 14.3, 14.3), _q("sq-10", 10.5, 10.5),
    _q("ud-q8_k_xl", 22.9, 22.9), _q("ud-q4_k_xl", 17.6, 17.6), _q("ud-q4_k_m", 16.6, 16.6),
    _q("ud-iq4_xs", 15.7, 15.7), _q("ud-q3_k_xl", 13.6, 13.6), _q("ud-iq3_xxs", 11.7, 11.7),
    _q("ud-q2_k_xl", 9.1, 9.1), _q("ud-iq2_xxs", 8.5, 8.5), _q("ud-iq1_m", 8.0, 8.0),
    _q("ud-iq1_s", 7.8, 7.8),
)

_FAMILIES: tuple[Family, ...] = (
    Family("qwen", "Qwen3.8-Flash-Next（ISTA-DASLab GSQ-RCO・推奨）", "Qwen3.8-Flash-Next",
           "qwen", True, _QWEN),
    Family("coder", "Qwen3.8-Flash-Next Coder（コード向け）", "Qwen3.8-Flash-Next Coder",
           "qwen", True, (_q("iq1_m", None, 32.0),), "コード以外と英語以外の言語は弱い"),
    Family("swift", "Swift 1.5（思考が短い fine-tune）", "Swift 1.5",
           "qwen", True, _SWIFT),
    Family("unsloth", "Qwen3.8-Flash-Next（Unsloth）", "Qwen3.8-Flash-Next",
           "qwen", True, _UNSLOTH),
    Family("gyro", "Gyro（agentionai・小さいが遅い）", "Gyro",
           "qwen", True, (_q("gyro-s", 58.5, 27.6), _q("gyro-m", 92.0, 35.0)), "AMD は実験的"),
    Family("qwen36", "Qwen3.6-35B-A3B（Unsloth）", "Qwen3.6-35B-A3B",
           "other", True, (_q("ud-q4_k_xl", 22.9, 32.0), _q("ud-q6_k_xl", 32.6, 44.0),
                           _q("q8_0", 37.8, 52.0)), "ファイルで選ぶ別の経路。Qwen 用の引数は渡さない"),
    Family("hy4-flash", "Tencent Hy4-Flash", "Tencent Hy4-Flash",
           "other", True, (_q("sq-64", 62.0 + 67.6, 64.0 + 8.0),
                           _q("sq-96", 89.6 + 67.6, 96.0),
                           _q("sq-128", 113.7 + 67.6, 128.0)),
           "Engram の 67.6GB は SSD から読む"),
    Family("inkling-2-mini", "Inkling 2 Mini（Thinking Machines）", "Inkling-2-Mini",
           "other", True, _INKLING,
           "0.2.22 で思考の段（none/minimal/low/medium/high/xhigh/max・0〜0.99）に対応。既定 low。"
           "渡し方は Qwen と同じ経路で、実機での効きは未確認"),
    Family("deepseek41", "DeepSeek V4.1 Flash", "DeepSeek-V4.1-Flash",
           "other", True, (_q("ista-das-coder", None, None), _q("ist-das-2.00bpw", None, None)),
           "エンジンは 0.1.59 で凍結"),
    Family("nvfp4", "NVFP4（NVIDIA だけ）", "NVFP4",
           "qwen", True, (_q("nvfp4", None, None), _q("nvfp4-nvidia", None, None),
                          _q("nvfp4-qad", None, None)), "NVIDIA の GPU が要る", True),
    Family("orca", "OrcaRouter Qwen3.8-Flash-Next-Uncensored IQ3_XXS（85.2GB・RAM 約 50GiB）",
           "OrcaRouter Uncensored", "other", True, (_q("iq3_xxs", 85.2, 50.0),),
           "公式の目録に無い。docs/ORCA.md の手作業の変換が要る（M6 で自動化）",
           False, True, True),
)


def families() -> tuple[Family, ...]:
    """目録の全行（画面では案内だけの行も含む）。"""
    return _FAMILIES


def get(family_id: str) -> Family | None:
    return next((item for item in _FAMILIES if item.id == family_id), None)


def has_family(family_id: str) -> bool:
    return get(family_id) is not None


def quantizations(family_id: str) -> tuple[Quantization, ...]:
    item = get(family_id)
    return item.quantizations if item else ()


def quant_ids(family_id: str) -> frozenset[str]:
    return frozenset(item.id for item in quantizations(family_id))


def is_valid(family_id: str, model_id: str) -> bool:
    """登録できる系列・量子化の組か。手作業の行と未知の id は断る。"""
    item = get(family_id)
    return bool(item) and not item.manual and any(q.id == model_id for q in item.quantizations)


def is_other_engine(family_id: str) -> bool:
    item = get(family_id)
    return bool(item) and item.engine == "other"


def has_thinking(family_id: str) -> bool:
    item = get(family_id)
    return bool(item) and item.thinking


def short_name(family_id: str) -> str:
    item = get(family_id)
    return item.short_name if item else family_id


def has_nvidia_gpu() -> bool:
    """安価に NVIDIA の有無を判定する。sysfs の vendor を優先し、無ければ nvidia-smi を見る。

    monitoring.gpu.detect_provider() は起動のたびに外部 CLI を呼んでログを残すので、目録の
    表示判定には使わない（AMD の sysfs vendor と同じ読み方をした既存コードに合わせる）。
    """
    for device in sorted(Path("/sys/class/drm").glob("card[0-9]*/device")):
        try:
            if (device / "vendor").read_text(encoding="ascii").strip().lower() == NVIDIA_VENDOR:
                return True
        except OSError:
            continue
    return shutil.which("nvidia-smi") is not None


def visible(family_id: str, nvidia: bool | None = None) -> bool:
    """この PC の画面に出すか。NVIDIA が要る行は NVIDIA が無ければ出さない。"""
    item = get(family_id)
    if item is None:
        return False
    if item.requires_nvidia and not (has_nvidia_gpu() if nvidia is None else nvidia):
        return False
    return True


def fit(family_id: str, model_id: str, ram_gib: float, vram_gib: float = 0.0) -> str:
    """RAM + VRAM の合計とメモリの目安を比べた結果（FITS / TIGHT / NO_FIT / UNKNOWN）。"""
    wanted = next((q.memory_gib for q in quantizations(family_id) if q.id == model_id), None)
    if wanted is None or not ram_gib:
        return UNKNOWN          # 目安が無い・この PC の RAM を読めない（「載らない」と誤って出さない）
    capacity = float(ram_gib) + float(vram_gib)
    if capacity >= wanted * 1.1:
        return FITS
    if capacity >= wanted:
        return TIGHT
    return NO_FIT


def machine_gib() -> tuple[float | None, float | None]:
    """この PC の RAM 総量と最大 GPU の VRAM 総量（GiB）。取得不能は None。"""
    from app.models_mgmt import strata_memory

    hardware = strata_memory.hardware()
    ram = hardware.get("ram_total")
    gpu = hardware.get("gpu") or {}
    vram = gpu.get("total")
    return (round(ram / GIB, 1) if ram else None, round(vram / GIB, 1) if vram else None)


def api_payload() -> dict:
    """GET /api/v1/models/strata/catalog の本体。同期処理なので endpoint は def にする。"""
    ram, vram = machine_gib()
    nvidia = has_nvidia_gpu()
    rows = []
    for item in _FAMILIES:
        rows.append({
            "id": item.id, "name": item.name, "short_name": item.short_name,
            "engine": item.engine, "thinking": item.thinking,
            "visible": visible(item.id, nvidia), "manual": item.manual,
            "uncensored": item.uncensored, "note": item.note,
            "quantizations": [{
                "id": q.id, "label": q.label,
                "download_gib": q.download_gib, "memory_gib": q.memory_gib,
                "fit": fit(item.id, q.id, ram or 0.0, vram or 0.0),
                "uncensored": item.uncensored, "manual": item.manual,
            } for q in item.quantizations],
        })
    return {"families": rows, "hardware": {"ram_total_gib": ram, "vram_total_gib": vram}}
