"""全ランタイム共通のモデル目録（Models の「モデル登録」の目録）の唯一の情報源。

2026-11-28 利用者「目録はまず大きな種類（Qwen・Hy4・Inkling など）を選ばせ、検閲・無検閲、量子化を選べる
ように。載るかどうかと容量を調査して、Strata・Maya・Radiance それぞれの目録を作って。llama.cpp は目録を
やめて従来のファイル選択に戻す」。調べた結果と出どころは docs/research-model-catalog-20261128.md。

形: ランタイム → 系列（画面の 1 枚のカード・group）→ 検閲あり／無検閲 → 量子化（variant）。
- Strata: 本体の目録をその場で読む（strata_live。読めなければ手の表 strata_catalog）。本体の判定
  （fits / slower / tight / no）をそのまま使う。無検閲版は量子化の作りと大きさを確かめた第三者の HF の行。
- Maya: 本体の probe（maya_s.catalog）をその場で読む（読めなければ調査の表）。取得は Maya の
  「ダウンロードして登録」（acquire="maya"）。
- Radiance・Sly-Radiance: 作者の公開している文章の LLM を、HF のファイル一覧で大きさを確かめて載せる。
- llama.cpp: 目録なし（手元のファイルを選んで登録する）。

大きさ・必要メモリの単位は GB（10^9 バイト。Strata・Maya・HF の表示と同じ）。推測の値は書かない。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("control_deck.models.runtime_catalog")

RUNTIMES: tuple[dict, ...] = (
    {"id": "llama", "name": "llama.cpp", "formats": ("gguf",), "acquire": "none"},
    {"id": "radiance", "name": "Radiance", "formats": ("rad",), "acquire": "hf"},
    {"id": "sly-radiance", "name": "Sly-Radiance", "formats": ("safetensors",), "acquire": "hf"},
    {"id": "strata", "name": "Strata", "formats": ("strata-quant", "gguf"), "acquire": "runtime"},
    {"id": "maya", "name": "Maya", "formats": ("maya-quant",), "acquire": "maya"},
)

# Maya-M Derisked の repo（公式 docs/MODELS.md の owner は peasantsmith）。
MAYA_DERISKED_REPO = "peasantsmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF"
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
    size_gib: float | None = None   # ダウンロード量（GB）。None = 未確認（推測しない）
    memory_gib: float | None = None  # 必要メモリの目安（GB。ランタイム本体の値）
    uncensored: bool = False
    supported: bool = True
    note: str = ""                 # 開発者向けのメモ（画面はエキスパートだけ）
    acquire: str = "none"          # "hf" | "runtime"（初回の起動でランタイムが取る）| "maya" | "none"
    source: str = "catalog"
    family: str = ""               # 登録先の系列（Strata の family・Maya の tier）
    files: tuple[str, ...] = ()
    # 画面のまとめ（系列のカード）と表示
    group: str = ""
    group_name: str = ""
    label: str = ""                # 量子化の名前（カードの中の行）
    maker: str = ""                # 第三者の版のとき作った人（例: SC117）
    fit: str | None = None         # ランタイム本体の判定（Strata・Maya）。None なら大きさから計算
    recommended: bool = False      # ランタイム本体のおすすめ（Strata）
    tags: tuple[str, ...] = ()
    registered: str = ""           # 登録済みの名前（Maya の probe）
    folder_name: str = ""          # Maya の取得先のフォルダ名

    def api_row(self, fit: str) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "format": self.format,
            "repo": self.repo, "quantization": self.quantization,
            "size_gib": self.size_gib, "memory_gib": self.memory_gib,
            "uncensored": self.uncensored, "supported": self.supported, "note": self.note,
            "acquire": self.acquire, "source": self.source, "fit": fit,
            "family": self.family, "files": list(self.files),
        }


# ---------------------------------------------------------------- Radiance（StillDeadcode の .rad・文章の LLM）
# 大きさは HF のファイル一覧（2026-11-28）。動く版は README の「Needs radiance」と本番の 2.3.0 で確認。
_R = "StillDeadcode/"
_IQR_V4 = (("iq2r", 10.9), ("iq2r-l", 12.0), ("iq3r-s", 13.2), ("iq3r", 14.3), ("iq3r-l", 15.4),
           ("iq4r-s", 16.5), ("iq4r", 17.6), ("iq4r-l", 18.7), ("iq5r-s", 19.6), ("iq5r", 20.9),
           ("iq6r-s", 23.0), ("iq6r", 24.2), ("iq6r-l", 25.9), ("iq7r", 27.5), ("iq8r", 29.6))
# (id, group, 系列名, 量子化の名前, repo, file, GB, 無検閲, tags)
_RADIANCE: tuple[tuple, ...] = (
    *[(f"radiance-qwen38-27b-{q}-v4", "qwen38-27b", "Qwen3.8-27B", f"{q.upper()}（IQR-V4）",
       _R + "qwen3.8-27b-iqr-v4", f"qwen3.8-27b-{q}-v4.rad", gb, False, ()) for q, gb in _IQR_V4],
    ("radiance-qwen38-27b-mxfp4", "qwen38-27b", "Qwen3.8-27B", "MXFP4", _R + "qwen3.8-27b-mxfp4",
     "qwen3.8-27b-mxfp4.rad", 19.4, False, ()),
    ("radiance-qwen38-27b-fp8", "qwen38-27b", "Qwen3.8-27B", "FP8", _R + "qwen3.8-27b-fp8",
     "qwen3.8-27b-fp8.rad", 30.8, False, ()),
    ("radiance-qwen38-27b-iq8r-dflash", "qwen38-27b", "Qwen3.8-27B", "IQ8R＋先読み（DFlash）",
     _R + "qwen3.8-27b-iq8r-dflash", "qwen3.8-27b-iq8r-dflash.rad", 30.9, False, ()),
    ("radiance-qwen38-27b-uncensored-fp8", "qwen38-27b", "Qwen3.8-27B", "FP8", _R + "qwen3.8-27b-uncensored-fp8",
     "qwen3.8-27b-uncensored-fp8.rad", 30.8, True, ()),
    ("radiance-qwen38-27b-uncensored-iq8r", "qwen38-27b", "Qwen3.8-27B", "IQ8R", _R + "qwen3.8-27b-uncensored-iq8r",
     "qwen3.8-27b-uncensored-iq8r.rad", 30.9, True, ()),
    ("radiance-swift15-27b-fp8", "swift15-27b", "Swift 1.5（Qwen3.8-27B）", "FP8", _R + "swift1.5-qwen3.8-27b-fp8",
     "swift1.5-qwen3.8-27b-fp8.rad", 30.8, False, ()),
    ("radiance-swift15-27b-iq8r", "swift15-27b", "Swift 1.5（Qwen3.8-27B）", "IQ8R", _R + "swift1.5-qwen3.8-27b-iq8r",
     "swift1.5-qwen3.8-27b-iq8r.rad", 30.9, False, ()),
    ("radiance-qwen38-next-flash-fp8-iq4r-moe", "qwen38-flash-next", "Qwen3.8-Flash-Next", "FP8＋IQ4R（MoE）",
     _R + "qwen3.8-next-flash-fp8-iq4r-moe", "qwen3.8-next-flash-fp8-iq4r-moe.rad", 122.0, False, ()),
    ("radiance-qwen38-next-flash-iqr-v4", "qwen38-flash-next", "Qwen3.8-Flash-Next", "IQR-V4",
     _R + "qwen3.8-next-flash-iqr-v4", "qwen3.8-next-flash-iqr-v4.rad", 121.7, False, ()),
    ("radiance-qwen38-next-flash-uncensored-fp8-iq4r-moe", "qwen38-flash-next", "Qwen3.8-Flash-Next",
     "FP8＋IQ4R（MoE）", _R + "qwen3.8-next-flash-uncensored-fp8-iq4r-moe",
     "qwen3.8-next-flash-uncensored-fp8-iq4r-moe.rad", 122.0, True, ()),
    ("radiance-qwen38-next-flash-uncensored-iqr-v4", "qwen38-flash-next", "Qwen3.8-Flash-Next", "IQR-V4",
     _R + "qwen3.8-next-flash-uncensored-iqr-v4", "qwen3.8-next-flash-uncensored-iqr-v4.rad", 121.7, True, ()),
    ("radiance-swift15-flash-next-fp8-iq4r-moe", "swift15-flash-next", "Swift 1.5（Qwen3.8-Flash-Next）",
     "FP8＋IQ4R（MoE）", _R + "swift1.5-qwen3.8-next-flash-fp8-iq4r-moe",
     "swift1.5-qwen3.8-next-flash-fp8-iq4r-moe.rad", 122.0, False, ()),
    ("radiance-qwen36-35b-a3b-fp8", "qwen36-35b", "Qwen3.6-35B-A3B", "FP8", _R + "qwen3.6-35b-a3b-fp8",
     "qwen3.6-35b-a3b-fp8.rad", 37.2, False, ()),
    ("radiance-qwen36-35b-a3b-iq8r", "qwen36-35b", "Qwen3.6-35B-A3B", "IQ8R", _R + "qwen3.6-35b-a3b-iq8r",
     "qwen3.6-35b-a3b-iq8r.rad", 37.6, False, ()),
    ("radiance-inkling2-mini-iq4r-v4", "inkling-2-mini", "Inkling-2-Mini", "IQ4R-V4", _R + "inkling-2-mini-iq4r-v4",
     "inkling-2-mini-iq4r-v4.rad", 15.5, False, ("画像も読む",)),
    ("radiance-gemma4-26b-a4b-dflash-fp8", "gemma4-26b", "Gemma4-26B-A4B", "FP8＋先読み（DFlash）",
     _R + "gemma4-26b-a4b-dflash-fp8", "gemma4-26b-a4b-dflash-fp8.rad", 36.4, False, ()),
    ("radiance-gemma4-26b-a4b-dflash-i8", "gemma4-26b", "Gemma4-26B-A4B", "I8＋先読み（DFlash）",
     _R + "gemma4-26b-a4b-dflash-i8", "gemma4-26b-a4b-dflash-i8.rad", 36.7, False, ()),
    ("radiance-minicpm5-2b-fp8", "minicpm5-2b", "MiniCPM5-2B", "FP8", _R + "minicpm5-2b-fp8",
     "minicpm5-2b-fp8.rad", 3.3, False, ()),
    ("radiance-minicpm5-2b-iq8r", "minicpm5-2b", "MiniCPM5-2B", "IQ8R", _R + "minicpm5-2b-iq8r",
     "minicpm5-2b-iq8r.rad", 3.3, False, ()),
    ("radiance-minicpm5-2b-bf16", "minicpm5-2b", "MiniCPM5-2B", "BF16", _R + "minicpm5-2b-bf16",
     "minicpm5-2b-bf16.rad", 5.5, False, ()),
    ("radiance-translategemma-27b-i8", "translategemma-27b", "TranslateGemma-27B", "I8", _R + "translategemma-27b-i8",
     "translategemma-27b-i8.rad", 30.3, False, ("翻訳",)),
    ("radiance-flint-iq8r", "flint", "Flint", "IQ8R", _R + "flint-iq8r", "flint-iq8r.rad", 40.7, False, ("試験的",)),
    ("radiance-ling30-tiny-iq8r", "ling30-tiny", "Ling-3.0-tiny", "IQ8R", _R + "ling-3.0-tiny-iq8r",
     "ling-3.0-tiny-iq8r.rad", 8.5, False, ("試験的",)),
    ("radiance-muse-glimmer-30b-iq8r-dflash", "muse-glimmer-30b", "Muse-Glimmer-30B", "IQ8R＋先読み（DFlash）",
     _R + "muse-glimmer-30b-iq8r-dflash", "muse-glimmer-30b-iq8r-dflash.rad", 38.3, False, ("試験的",)),
)

# ---------------------------------------------------------------- Strata の無検閲版（第三者の HF・調査で確かめたもの）
# (id, 系列, 量子化, repo, files, GB, 作った人)。必要メモリ・判定は Strata 本体の同じ量子化の行から取る。
_SC_QWEN = "SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF"
_SC_SWIFT = "SC117/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF"
_STRATA_UNCENSORED: tuple[tuple, ...] = (
    ("sc117-qwen-abliterated-q2_0", "qwen", "q2_0", _SC_QWEN, (), 66.8, "SC117"),
    ("sc117-qwen-abliterated-iq2_xs", "qwen", "iq2_xs", _SC_QWEN, (), 68.5, "SC117"),
    ("gsq-qwen38-abliterated-iq3_xxs", "qwen", "iq3_xxs", _SC_QWEN, (), 76.1, "SC117"),   # id は以前の目録と同じ（取得の記録と合わせる）
    ("sc117-qwen-abliterated-iq3_s", "qwen", "iq3_s", _SC_QWEN, (), 83.9, "SC117"),
    ("sc117-swift-abliterated-q2_0", "swift", "q2_0", _SC_SWIFT, (), 66.5, "SC117"),
    ("sc117-swift-abliterated-iq2_xs", "swift", "iq2_xs", _SC_SWIFT, (), 68.2, "SC117"),
    ("sc117-swift-abliterated-iq3_xxs", "swift", "iq3_xxs", _SC_SWIFT, (), 75.8, "SC117"),
    ("sc117-swift-abliterated-iq3_s", "swift", "iq3_s", _SC_SWIFT, (), 83.5, "SC117"),
    ("joaozaokk-inkling2-mini-heretic-ud-q4_k_xl", "inkling-2-mini", "ud-q4_k_xl", "JoaoZaokk/Inkling-2-Mini-Heretic-GGUF",
     ("Inkling-2-Mini-Heretic-UD-Q4_K_XL.gguf", "dspark-Inkling-2-Mini-Q8_0.gguf"), 18.0, "JoaoZaokk（heretic）"),
)

# Strata の系列の画面の名前と説明（Strata の題は英語なので、ここで言い換える）
_STRATA_MODEL: dict[str, tuple[str, str]] = {
    "qwen": ("Qwen3.8-Flash-Next", "Strata の標準。GSQ-RCO で小さく速い"),
    "swift": ("Swift 1.5", "思考を短くした Qwen3.8-Flash-Next の fine-tune"),
    "coder": ("Qwen3.8-Flash-Next Coder", "コード向け。コード以外と英語以外の言語は弱い"),
    "unsloth": ("Qwen3.8-Flash-Next（Unsloth 版）", "Unsloth の量子化。大きめで精度寄り"),
    "gyro": ("Qwen3.8-Flash-Next（Gyro 版）", "小さいが遅め。メモリの少ない PC 向け"),
    "qwen36": ("Qwen3.6-35B-A3B", "35B（有効 3B）の MoE。Flash-Next より小さい"),
    "ornith": ("Ornith-1.5-35B-A3B", "Qwen3.6-35B-A3B と同じ形の 35B MoE"),
    "hy4-flash": ("Tencent Hy4-Flash", "Tencent の MoE。n-gram の表は SSD から読む"),
    "inkling-2-mini": ("Inkling-2-Mini", "Thinking Machines の 26B MoE（有効 3.4B）。画像も読む・思考なし"),
}
_STRATA_FIT = {"fits": "fits", "slower": "slow", "tight": "tight", "no": "no", "disk": "tight"}

# Maya のモデルの説明（Maya 本体の docs/MODELS.md から）
_MAYA_MODEL: dict[str, str] = {
    "GLM-5.3-Flash": "Z.ai の 321B MoE（有効 18B）。AMD では画像も読む",
    "DeepSeek-V4.1-Flash": "DeepSeek の 748B MoE。使うエキスパートだけを GPU と RAM に置き、残りは SSD から読む",
    "MiniMax-M3.1-Flash": "MiniMax の MoE。Maya の量子化で 128 GB の PC に収まる",
    "Hy4-Flash": "Tencent の MoE。n-gram の表（63 GB）は SSD から読む",
}


def _radiance_entries() -> list[Entry]:
    return [Entry(
        runtime="radiance", id=row[0], name=f"{row[2]} {row[3]}", format="rad", repo=row[4], quantization="",
        size_gib=row[6], uncensored=row[7], acquire="hf", source="hf", files=(row[5],),
        group=f"radiance:{row[1]}", group_name=row[2], label=row[3], tags=row[8],
    ) for row in _RADIANCE]


def _sly_entries() -> list[Entry]:
    from app.models_mgmt import sly_radiance_catalog

    groups = {"swift-heretic": "Swift-1.5 heretic MXFP4（標準）", "quark-awq": "Quark AWQ MXFP4（AMD）",
              "nvfp4": "NVFP4（Unsloth）", "paroquant": "ParoQuant"}
    out = []
    for item in sly_radiance_catalog.ENTRIES:
        drafter = item.kind == "drafter"
        out.append(Entry(
            runtime="sly-radiance", id=item.id, name=item.name, kind=item.kind,
            format="safetensors", repo=item.repo, quantization=item.quantization,
            size_gib=item.size_gib, uncensored=item.uncensored,
            supported=item.supported, note=item.note,
            acquire="hf" if (item.supported and item.repo) else "none", source="catalog",
            group="sly:dflash2" if drafter else ("sly:qwen38-27b" if item.id in groups else f"sly:{item.id}"),
            group_name="先読み DFlash2" if drafter else ("Qwen3.8-27B" if item.id in groups else item.name),
            label="W4A16" if drafter else groups.get(item.id, "標準"),
            tags=("先読み",) if drafter else (),
        ))
    return out


def _strata_entries() -> list[Entry]:
    """Strata 本体の目録（読めなければ手の表）＋第三者の無検閲版。"""
    from app.models_mgmt import strata_catalog, strata_live

    nvidia = strata_catalog.has_nvidia_gpu()
    out: list[Entry] = []
    official: dict[tuple[str, str], Entry] = {}
    live = None
    try:
        live = strata_live.rows()
    except Exception:  # noqa: BLE001 - 目録の表示で止まらない（手の表へ戻る）
        logger.warning("Strata の目録を読めませんでした。手の表を使います", exc_info=True)
    if live:
        for row in live:
            family = str(row["family"])
            if row.get("offered") is False or row.get("custom"):
                continue                                    # 提供の終わった量子化・自分のビルドは出さない
            if not strata_catalog.visible(family, nvidia):
                continue                                    # NVIDIA だけの系列
            quant = str(row["model"]).casefold()
            name, _desc = _STRATA_MODEL.get(family, (str(row.get("family_title") or family), ""))
            tags = tuple(t for t, on in (("画像も読む", row.get("images")), ("試験的", row.get("experimental"))) if on)
            entry = Entry(
                runtime="strata", id=f"{family}/{quant}", name=f"{name} · {row['model']}", format="strata-quant",
                repo=STRATA_GSQ_REPO if family == "qwen" else "", quantization=quant,
                size_gib=float(row.get("download_gb") or 0) or None, memory_gib=float(row.get("memory_gb") or 0) or None,
                supported=strata_catalog.is_valid(family, quant), note=str(row.get("about") or ""),
                acquire="runtime", family=family, group=f"strata:{family}", group_name=name, label=str(row["model"]),
                fit=_STRATA_FIT.get(str(row.get("verdict") or ""), "unknown"), recommended=bool(row.get("recommended")),
                tags=tags)
            out.append(entry)
            official[(family, quant)] = entry
    else:
        for family in strata_catalog.families():
            if not strata_catalog.visible(family.id, nvidia) or family.manual:
                continue
            name, _desc = _STRATA_MODEL.get(family.id, (family.short_name, ""))
            for quant in family.quantizations:
                entry = Entry(
                    runtime="strata", id=f"{family.id}/{quant.id}", name=f"{name} · {quant.label}",
                    format="strata-quant", repo=STRATA_GSQ_REPO if family.id == "qwen" else "",
                    quantization=quant.id, size_gib=quant.download_gib, memory_gib=quant.memory_gib,
                    uncensored=family.uncensored, note=family.note, acquire="runtime", family=family.id,
                    group=f"strata:{family.id}", group_name=name, label=quant.label)
                out.append(entry)
                official[(family.id, quant.id)] = entry
    for entry_id, family, quant, repo, files, gb, maker in _STRATA_UNCENSORED:
        base = official.get((family, quant))
        if base is None:
            continue                                        # 本体に同じ量子化が無い（この PC で出ない系列）
        out.append(Entry(
            runtime="strata", id=entry_id, name=f"{base.group_name} 無検閲 · {base.label}", format="gguf", repo=repo,
            quantization=quant.upper() if family != "inkling-2-mini" else "UD-Q4_K_XL", size_gib=gb,
            memory_gib=base.memory_gib, uncensored=True, acquire="hf", source="hf", family=family, files=files,
            group=base.group, group_name=base.group_name, label=base.label, maker=maker, fit=base.fit,
            tags=base.tags))
    return out


def _maya_entries() -> list[Entry]:
    """Maya 本体の probe（量子化・大きさ・必要メモリ・登録済み）。読めなければ空（画面は「読めない」と出す）。"""
    from app.models_mgmt import maya_s

    try:
        if not maya_s.installed():
            return []
        data = maya_s.catalog()
    except Exception:  # noqa: BLE001 - 目録の表示で止まらない
        logger.warning("Maya の目録を読めませんでした", exc_info=True)
        return []
    memory = data.get("memory") or {}
    capacity = float(memory.get("ram_gb") or 0) + float(memory.get("vram_gb") or 0)
    out = []
    for item in data.get("quants") or []:
        model = str(item.get("model") or "")
        tier = str(item.get("tier") or "")
        need = item.get("memory_gb")
        fit = "unknown"
        if isinstance(need, (int, float)) and capacity:
            # Maya は使うエキスパートを GPU と RAM に置き、残りを SSD から読む: 超えても動くが遅い
            fit = "fits" if need <= capacity - 8 else "slow"
        published = item.get("published", True) is not False and float(item.get("size_gb") or 0) > 0
        out.append(Entry(
            runtime="maya", id=str(item["setup_id"]), name=f"{model} · {tier}", format="maya-quant",
            repo=str(item.get("repo") or ""), quantization=str(item.get("quant") or tier),
            size_gib=float(item.get("size_gb") or 0) or None, memory_gib=float(need) if isinstance(need, (int, float)) else None,
            uncensored="derisked" in str(item["setup_id"]), supported=published,
            note="" if published else "まだ公開されていません", acquire="maya", family=str(item.get("family") or ""),
            group=f"maya:{model.casefold()}", group_name=model, label=tier, fit=fit,
            registered=str(item.get("registered") or ""), folder_name=str(item.get("folder_name") or "")))
    return out


def entries_for(runtime: str) -> list[Entry]:
    if runtime == "radiance":
        return _radiance_entries()
    if runtime == "sly-radiance":
        return _sly_entries()
    if runtime == "strata":
        return _strata_entries()
    if runtime == "maya":
        return _maya_entries()
    return []                                    # llama.cpp は目録なし（手元のファイルを選ぶ）


def get_entry(runtime: str, entry_id: str) -> Entry | None:
    return next((e for e in entries_for(runtime) if e.id == entry_id), None)


def _fit_for(entry: Entry, ram_gib: float | None, vram_gib: float | None) -> str:
    """載るかの目安: ランタイム本体の判定があればそれ。無ければ大きさから（GPU に置くランタイム）。"""
    if entry.kind != "model" or not entry.supported:
        return "unknown"
    if entry.fit:
        return entry.fit
    return fit_by_size(entry.size_gib, ram_gib, vram_gib)


def fit_by_size(size: float | None, ram: float | None, vram: float | None) -> str:
    """Radiance・Sly は重みを GPU に置く: 大きさ×1.15 が VRAM に収まれば載る、RAM と合わせて収まれば遅い。"""
    if not size or not vram:
        return "unknown"
    if vram >= size * 1.15:
        return "fits"
    if (ram or 0) and vram + float(ram) - 10 >= size:
        return "slow"
    return "no"


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
    except Exception:  # noqa: BLE001 - 目録の表示で止まらない
        return False
    return False


# ---------------------------------------------------------------- 系列ごとのまとめ（画面の 1 枚のカード）

def _description(runtime: str, first: Entry) -> str:
    if runtime == "strata":
        return _STRATA_MODEL.get(first.family, ("", ""))[1]
    if runtime == "maya":
        return _MAYA_MODEL.get(first.group_name, "")
    if runtime == "sly-radiance" and first.kind == "drafter":
        return "Qwen3.8-27B のどの量子化とも組める先読みのモデル"
    return ""


def models_for(runtime: str, ram: float | None, vram: float | None) -> list[dict]:
    """ランタイムの目録を系列ごとにまとめる（画面の 1 枚のカード → 検閲あり／無検閲 → 量子化）。"""
    groups: dict[str, list[Entry]] = {}
    for entry in entries_for(runtime):
        groups.setdefault(entry.group or entry.id, []).append(entry)
    out = []
    for index, (key, group) in enumerate(groups.items()):
        variants = []
        for entry in group:
            variants.append({
                "id": entry.id, "label": entry.label or entry.quantization or "標準", "quantization": entry.quantization,
                "acquire": entry.acquire if entry.supported else "none",
                "size_gib": entry.size_gib, "memory_gib": entry.memory_gib, "fit": _fit_for(entry, ram, vram),
                "supported": entry.supported,
                # 使えない理由は利用者向けの文だけ（開発者のメモはカードの note_expert へ）
                "reason": "" if entry.supported else (
                    entry.note if entry.runtime == "maya" and entry.note else "このランタイムではまだ使えません"),
                "repo": entry.repo, "format": entry.format, "kind": entry.kind, "family": entry.family,
                "files": list(entry.files), "state": "registered" if entry.registered else "available",
                "registered": entry.registered, "folder_name": entry.folder_name,
                "uncensored": entry.uncensored, "maker": entry.maker, "recommended": entry.recommended,
                "note": "",
            })
        first = group[0]
        sizes = [v["size_gib"] for v in variants if v["size_gib"] is not None]
        notes = list(dict.fromkeys(e.note for e in group if e.note))
        tags = list(dict.fromkeys(t for e in group for t in e.tags))
        out.append({
            "id": key, "name": first.group_name or first.name, "description": _description(runtime, first),
            "note_expert": " / ".join(notes)[:600], "tags": tags,
            "uncensored": all(v["uncensored"] for v in variants),
            "has_uncensored": any(v["uncensored"] for v in variants),
            "has_censored": any(not v["uncensored"] for v in variants),
            "gated": False, "kind": first.kind,
            "recommended": any(v["recommended"] for v in variants),
            "recommended_variant": next((v["id"] for v in variants if v["recommended"]), ""),
            "size_min_gib": min(sizes) if sizes else None, "size_max_gib": max(sizes) if sizes else None,
            "variants": variants, "_order": index,
        })
    # 本体がおすすめを出さないとき（この PC の判定が無い・手の表）は、Strata の標準（GSQ-RCO の Qwen3.8-Flash-Next）
    if runtime == "strata" and not any(m["recommended"] for m in out):
        for item in out:
            if item["id"] == "strata:qwen":
                item["recommended"] = True
    # おすすめ → 載るものがある → 本体の目録の順
    out.sort(key=lambda m: (not m["recommended"], not any(v["fit"] == "fits" for v in m["variants"]), m["_order"]))
    for item in out:
        item.pop("_order")
    return out


def api_payload() -> dict:
    """GET /api/v1/models/catalog の本体（同期処理なので endpoint は def にする）。"""
    from app.models_mgmt import strata_catalog

    ram, vram = strata_catalog.machine_gib()
    try:
        from app.models_mgmt import libraries

        download_root = str(libraries.default_models_dir())
    except Exception:  # noqa: BLE001 - 置き場が読めなくても目録は出す（Maya の取得だけ押せない）
        download_root = ""
    blocks = []
    for spec in RUNTIMES:
        rows = [entry.api_row(_fit_for(entry, ram, vram)) for entry in entries_for(spec["id"])]
        blocks.append({
            "id": spec["id"], "name": spec["name"], "installed": _installed(spec["id"]),
            "formats": list(spec["formats"]), "acquire": spec["acquire"], "entries": rows,
            "models": models_for(spec["id"], ram, vram), "download_root": download_root,
        })
    return {"runtimes": blocks, "hardware": {"ram_total_gib": ram, "vram_total_gib": vram}}
