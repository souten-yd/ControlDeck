"""全ランタイム共通のモデル目録（推奨モデルのカード）の唯一の情報源。

UI 担当の RuntimeCatalog 部品は `GET /api/v1/models/catalog`（この module の api_payload）を
読む。ランタイムごとの既存目録は各 module が持つ（Sly は sly_radiance_catalog、Strata は
strata_catalog）ので、ここでは集約と形の統一だけをする（二重持ちをしない）。

目録に載せる行は出どころの確認できた repo id とファイル名だけ:
- 調査済みの行（_RESEARCHED）: docs/PLAN-model-catalog.md「目録候補」（2026-11-28 に Web で
  確認した repo id とファイル名）。llama.cpp・Radiance の空目録を解消し、Strata の新行は
  GSQ-RCO・Unsloth UD-*・Q8_0 だけ、Maya は peasantsmith の Maya 量子化だけを載せる。
- Sly-Radiance: sly_radiance_catalog.ENTRIES（README 確認済み）。
- Strata の既存行: strata_catalog（公式 README・docs/MODELS.md 確認済み。系列×量子化を
  1 行に平らげる。既存の登録 id を壊さない）。

size_gib が None の行は「大きさ未確認」。URL・大きさ・sha を推測で書かない（取得の開始時に
HF の API から読む: POST /api/v1/models/acquire-jobs と GET .../entries/{id}/files）。
files は調査で確認したファイル名のみ（取得時に HF の一覧と突き合わせる）。
"""
from __future__ import annotations

from dataclasses import dataclass

RUNTIMES: tuple[dict, ...] = (
    {"id": "llama", "name": "llama.cpp", "formats": ("gguf",), "acquire": "hf",
     "search_ref": "/api/v1/models/hf/search"},
    {"id": "radiance", "name": "Radiance", "formats": ("rad",), "acquire": "hf"},
    {"id": "sly-radiance", "name": "Sly-Radiance", "formats": ("safetensors",), "acquire": "hf"},
    {"id": "strata", "name": "Strata", "formats": ("strata-quant",), "acquire": "runtime"},
    {"id": "maya", "name": "Maya", "formats": ("maya-quant", "gguf"), "acquire": "hf",
     "catalog_ref": "/api/v1/features/maya-s/catalog"},
)

# 調査済みの行（repo id とファイル名だけ。大きさ・sha は書かない）。
# (runtime, id, name, repo, quantization, family, files, uncensored, note)
# 出どころ: docs/PLAN-model-catalog.md「目録候補（repo id とファイル名のみ・2026-11-28）」。
# Strata の新行は GSQ-RCO・Unsloth UD-*・Q8_0 だけ（他形式は AGENTS.md の実測で未対応）。
_RESEARCHED: tuple[tuple, ...] = (
    # ---- llama.cpp（GGUF。無検閲の版も調査済みの repo だけ） ----
    ("llama", "unsloth-qwen38-27b-q8_0", "Qwen3.8-27B Q8_0",
     "unsloth/Qwen3.8-27B-GGUF", "Q8_0", "",
     ("Qwen3.8-27B-Q8_0.gguf",), False, ""),
    ("llama", "unsloth-qwen38-27b-ud-q4_k_xl", "Qwen3.8-27B UD-Q4_K_XL",
     "unsloth/Qwen3.8-27B-GGUF", "UD-Q4_K_XL", "",
     ("Qwen3.8-27B-UD-Q4_K_XL.gguf",), False, ""),
    ("llama", "ggml-glm47-flash-q4_k", "GLM-4.7-Flash Q4_K",
     "ggml-org/GLM-4.7-Flash-GGUF", "Q4_K", "",
     ("GLM-4.7-Flash-Q4_K.gguf",), False, ""),
    ("llama", "ggml-glm47-flash-q8_0", "GLM-4.7-Flash Q8_0",
     "ggml-org/GLM-4.7-Flash-GGUF", "Q8_0", "",
     ("GLM-4.7-Flash-Q8_0.gguf",), False, ""),
    ("llama", "ggml-gemma4-e4b-it-q8_0", "gemma-4-E4B-it Q8_0",
     "ggml-org/gemma-4-E4B-it-GGUF", "Q8_0", "",
     ("gemma-4-E4B-it-Q8_0.gguf",), False, ""),
    ("llama", "ggml-gemma4-e4b-it-q4_0", "gemma-4-E4B-it Q4_0",
     "ggml-org/gemma-4-E4B-it-GGUF", "Q4_0", "",
     ("gemma-4-E4B-it-Q4_0.gguf",), False, ""),
    ("llama", "google-gemma4-12b-it-qat-q4_0", "gemma-4-12B-it QAT Q4_0",
     "google/gemma-4-12B-it-qat-q4_0-gguf", "Q4_0", "",
     ("gemma-4-12b-it-qat-q4_0.gguf",), False, ""),
    ("llama", "unsloth-llama4-scout-iq4_nl", "Llama-4-Scout-17B-16E IQ4_NL",
     "unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF", "IQ4_NL", "",
     ("IQ4_NL/Llama-4-Scout-17B-16E-Instruct-IQ4_NL-00001-of-00002.gguf",
      "IQ4_NL/Llama-4-Scout-17B-16E-Instruct-IQ4_NL-00002-of-00002.gguf"), False, ""),
    ("llama", "huihui-qwen38-27b-abliterated-q8_0", "Qwen3.8-27B abliterated Q8_0（無検閲）",
     "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF", "Q8_0", "",
     ("Huihui-Qwen3.8-27B-abliterated-Q8_0.gguf",), True, "abliterated（無検閲）"),
    ("llama", "observerx-qwen38-27b-heretic-q4_k_m", "Qwen3.8-27B Heretic Q4_K_M（無検閲）",
     "0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF", "Q4_K_M", "",
     ("Qwen3.8-27B-Heretic-Q4_K_M.gguf",), True, "heretic / abliterated / uncensored"),
    ("llama", "observerx-qwen38-27b-heretic-rvn_q8_0", "Qwen3.8-27B Heretic RVN-Q8_0（無検閲）",
     "0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF", "RVN-Q8_0", "",
     ("RVN-Q8_0.gguf",), True, "heretic / abliterated / uncensored"),
    ("llama", "huihui-glm53-flash-abliterated-ud-iq1_m",
     "GLM-5.3-Flash abliterated UD-IQ1_M（無検閲）",
     "huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF", "UD-IQ1_M", "",
     ("UD-IQ1_M/GLM-5.3-Flash-UD-IQ1_M-00001-of-00003.gguf",
      "UD-IQ1_M/GLM-5.3-Flash-UD-IQ1_M-00002-of-00003.gguf",
      "UD-IQ1_M/GLM-5.3-Flash-UD-IQ1_M-00003-of-00003.gguf"), True, "abliterated（無検閲）"),
    ("llama", "huihui-deepseek-v41-flash-abliterated-ud-iq1_m",
     "DeepSeek-V4.1-Flash abliterated UD-IQ1_M（無検閲）",
     "huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF", "UD-IQ1_M", "",
     ("UD-IQ1_M/DeepSeek-V4.1-Flash-1017-UD-IQ1_M-00001-of-00005.gguf",
      "UD-IQ1_M/DeepSeek-V4.1-Flash-1017-UD-IQ1_M-00002-of-00005.gguf",
      "UD-IQ1_M/DeepSeek-V4.1-Flash-1017-UD-IQ1_M-00003-of-00005.gguf",
      "UD-IQ1_M/DeepSeek-V4.1-Flash-1017-UD-IQ1_M-00004-of-00005.gguf",
      "UD-IQ1_M/DeepSeek-V4.1-Flash-1017-UD-IQ1_M-00005-of-00005.gguf"),
     True, "abliterated（無検閲）"),
    ("llama", "llmfan46-gemma4-e4b-it-ultra-uncensored-heretic-q4_k_m",
     "gemma-4-E4B-it ultra uncensored heretic Q4_K_M（無検閲）",
     "llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF", "Q4_K_M", "",
     ("gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M.gguf",), True, "ultra uncensored / heretic"),
    ("llama", "atomicchat-inkling2-mini-uncensored-ad-iq3_s",
     "Inkling-2-Mini Uncensored AD-IQ3_S（無検閲）",
     "AtomicChat/Inkling-2-Mini-Uncensored-GGUF", "AD-IQ3_S", "",
     ("Inkling-2-Mini-Uncensored-AD-IQ3_S.gguf",), True, "Uncensored"),
    # ---- Radiance（.rad コンテナ。公式 README の 5 件） ----
    ("radiance", "radiance-qwen38-next-flash-fp8-iq4r-moe",
     "Qwen3.8-Flash-Next FP8 IQ4R MoE",
     "StillDeadcode/qwen3.8-next-flash-fp8-iq4r-moe", "", "rad",
     ("qwen3.8-next-flash-fp8-iq4r-moe.rad",), False, ""),
    ("radiance", "radiance-qwen38-27b-fp8", "Qwen3.8-27B FP8",
     "StillDeadcode/qwen3.8-27b-fp8", "", "rad",
     ("qwen3.8-27b-fp8.rad",), False, ""),
    ("radiance", "radiance-qwen36-35b-a3b-fp8", "Qwen3.6-35B-A3B FP8",
     "StillDeadcode/qwen3.6-35b-a3b-fp8", "", "rad",
     ("qwen3.6-35b-a3b-fp8.rad",), False, ""),
    ("radiance", "radiance-minicpm5-2b-fp8", "MiniCPM5-2B FP8",
     "StillDeadcode/minicpm5-2b-fp8", "", "rad",
     ("minicpm5-2b-fp8.rad",), False, ""),
    ("radiance", "radiance-minicpm5-2b-bf16", "MiniCPM5-2B BF16",
     "StillDeadcode/minicpm5-2b-bf16", "", "rad",
     ("minicpm5-2b-bf16.rad",), False, ""),
    # ---- Strata の新行（GSQ-RCO / Unsloth UD-* / Q8_0 だけ・分割片は全片を記載） ----
    ("strata", "gsq-qwen38-q2_0", "Qwen3.8-Flash-Next GSQ-RCO Q2_0",
     "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "Q2_0", "qwen",
     ("Q2_0/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00001-of-00002.gguf",
      "Q2_0/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-qwen38-iq2_xs", "Qwen3.8-Flash-Next GSQ-RCO IQ2_XS",
     "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "IQ2_XS", "qwen",
     ("IQ2_XS/Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00001-of-00002.gguf",
      "IQ2_XS/Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-qwen38-iq3_xxs", "Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS",
     "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "IQ3_XXS", "qwen",
     ("IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf",
      "IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-qwen38-iq3_s", "Qwen3.8-Flash-Next GSQ-RCO IQ3_S",
     "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "IQ3_S", "qwen",
     ("IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf",
      "IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-coder-iq1_m", "Qwen3.8-Flash-Next Coder GSQ-RCO IQ1_M",
     "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF", "IQ1_M", "coder",
     ("IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf",
      "IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-swift-iq2_xs", "Swift 1.5 GSQ-RCO IQ2_XS",
     "ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "IQ2_XS", "swift",
     ("Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00001-of-00002.gguf",
      "Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-swift-q2_0", "Swift 1.5 GSQ-RCO Q2_0",
     "ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "Q2_0", "swift",
     ("Swift-Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00001-of-00002.gguf",
      "Swift-Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-swift-iq3_xxs", "Swift 1.5 GSQ-RCO IQ3_XXS",
     "ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "IQ3_XXS", "swift",
     ("Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf",
      "Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00002-of-00002.gguf"), False, ""),
    ("strata", "gsq-swift-iq3_s", "Swift 1.5 GSQ-RCO IQ3_S",
     "ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF", "IQ3_S", "swift",
     ("Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf",
      "Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf"), False, ""),
    ("strata", "unsloth-flash-next-ud-q4_k_xl", "Unsloth Qwen3.8-Flash-Next UD-Q4_K_XL",
     "unsloth/Qwen3.8-Flash-Next-GGUF", "UD-Q4_K_XL", "unsloth",
     tuple(f"UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-{i:05d}-of-00004.gguf"
           for i in range(1, 5)), False, ""),
    ("strata", "unsloth-flash-next-q8_0", "Unsloth Qwen3.8-Flash-Next Q8_0",
     "unsloth/Qwen3.8-Flash-Next-GGUF", "Q8_0", "unsloth",
     tuple(f"Q8_0/Qwen3.8-Flash-Next-Q8_0-{i:05d}-of-00006.gguf"
           for i in range(1, 7)), False, ""),
    ("strata", "unsloth-qwen36-35b-q8_0", "Qwen3.6-35B-A3B Q8_0",
     "unsloth/Qwen3.6-35B-A3B-GGUF", "Q8_0", "qwen36",
     ("Qwen3.6-35B-A3B-Q8_0.gguf",), False, ""),
    ("strata", "gsq-qwen38-abliterated-iq3_xxs",
     "Qwen3.8-Flash-Next abliterated GSQ-RCO IQ3_XXS（無検閲）",
     "SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF", "IQ3_XXS", "qwen",
     ("IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_XXS-00001-of-00002.gguf",
      "IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_XXS-00002-of-00002.gguf"),
     True, "abliterated（無検閲）"),
    ("strata", "gsq-qwen38-27b-huihui-iq3_xxs",
     "Qwen3.8-27B Huihui abliterated GSQ-RCO IQ3_XXS（無検閲）",
     "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF", "IQ3_XXS", "qwen",
     ("Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_XXS.gguf",),
     True, "abliterated（無検閲）"),
    # ---- Maya（Maya 量子化。peasantsmith が正しい owner。Derisked は無検閲寄り） ----
    ("maya", "glm53-flash-maya-s-v2-iq2_xxs", "GLM-5.3-Flash Maya-S v2 IQ2_XXS",
     "peasantsmith/GLM-5.3-Flash-Maya-GGUF", "Maya-S-v2-IQ2_XXS", "Maya-S",
     tuple(f"Maya-S-v2-IQ2_XXS/GLM-5.3-Flash-Maya-S-v2-IQ2_XXS-{i:05d}-of-00003.gguf"
           for i in range(1, 4)), False, ""),
    ("maya", "glm53-flash-maya-s24-iq2_xxs_s", "GLM-5.3-Flash Maya-S24 IQ2_XXS_S",
     "peasantsmith/GLM-5.3-Flash-Maya-GGUF", "Maya-S24", "Maya-S24",
     tuple(f"Maya-S24/GLM-5.3-Flash-Maya-S24-IQ2_XXS_S-{i:05d}-of-00003.gguf"
           for i in range(1, 4)), False, ""),
    ("maya", "glm53-flash-maya-xs-iq1_s", "GLM-5.3-Flash Maya-XS IQ1_S",
     "peasantsmith/GLM-5.3-Flash-Maya-GGUF", "Maya-XS", "Maya-XS",
     tuple(f"Maya-XS/GLM-5.3-Flash-Maya-XS-IQ1_S-{i:05d}-of-00002.gguf"
           for i in range(1, 3)), False, ""),
    ("maya", "glm53-flash-maya-m-iq2_s", "GLM-5.3-Flash Maya-M IQ2_S",
     "peasantsmith/GLM-5.3-Flash-Maya-GGUF", "Maya-M", "Maya-M",
     tuple(f"Maya-M/GLM-5.3-Flash-Maya-M-IQ2_S-{i:05d}-of-00003.gguf"
           for i in range(1, 4)), False, ""),
    ("maya", "glm53-flash-maya-l-iq3_s", "GLM-5.3-Flash Maya-L IQ3_S",
     "peasantsmith/GLM-5.3-Flash-Maya-GGUF", "Maya-L", "Maya-L",
     tuple(f"Maya-L/GLM-5.3-Flash-Maya-L-IQ3_S-{i:05d}-of-00004.gguf"
           for i in range(1, 5)), False, ""),
    ("maya", "glm-m-derisked",
     "GLM-5.3-Flash Maya-M Derisked IQ2_S（無検閲寄り）",
     "peasantsmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF", "IQ2_S", "Maya-M",
     tuple(f"GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-{i:05d}-of-00003.gguf"
           for i in range(1, 4)), True,
     "拒否を減らす重みの編集（Derisked）・実験的。登録は Maya 本体の目録（probe）から行う"),
    ("maya", "glm53-maya-xs", "GLM-5.3 Maya-XS（744B）",
     "peasantsmith/GLM-5.3-Maya-GGUF", "Maya-XS", "Maya-XS",
     tuple(f"Maya-XS/GLM-5.3-Maya-XS-{i:05d}-of-00004.gguf"
           for i in range(1, 5)), False, ""),
    ("maya", "deepseek-v41-flash-maya-s-iq2_xxs", "DeepSeek-V4.1-Flash Maya-S IQ2_XXS",
     "peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF", "Maya-S", "Maya-S",
     tuple(f"Maya-S/DeepSeek-V4.1-Flash-1017-Maya-S-IQ2_XXS-{i:05d}-of-00006.gguf"
           for i in range(1, 7)), False, ""),
    ("maya", "deepseek-v41-flash-maya-xs-v2-iq1_s", "DeepSeek-V4.1-Flash Maya-XS v2 IQ1_S",
     "peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF", "Maya-XS", "Maya-XS",
     tuple(f"Maya-XS/DeepSeek-V4.1-Flash-1017-Maya-XS-v2-IQ1_S-{i:05d}-of-00003.gguf"
           for i in range(1, 4)), False, ""),
    ("maya", "minimax-m31-flash-maya-xs-iq1_s", "MiniMax-M3.1-Flash Maya-XS IQ1_S",
     "peasantsmith/MiniMax-M3.1-Flash-Maya-GGUF", "Maya-XS", "Maya-XS",
     tuple(f"Maya-XS/MiniMax-M3.1-Flash-Maya-XS-IQ1_S-{i:05d}-of-00002.gguf"
           for i in range(1, 3)), False, ""),
    ("maya", "minimax-m31-flash-maya-s-iq2_xxs", "MiniMax-M3.1-Flash Maya-S IQ2_XXS",
     "peasantsmith/MiniMax-M3.1-Flash-Maya-GGUF", "Maya-S", "Maya-S",
     tuple(f"Maya-S/MiniMax-M3.1-Flash-Maya-S-IQ2_XXS-{i:05d}-of-00003.gguf"
           for i in range(1, 4)), False, ""),
    ("maya", "hy4-flash-maya-xs-iq2_xxs", "Hy4-Flash Maya-XS IQ2_XXS",
     "peasantsmith/Hy4-Flash-Maya-GGUF", "Maya-XS", "Maya-XS",
     tuple(f"Maya-XS/Hy4-Flash-Maya-XS-IQ2_XXS-{i:05d}-of-00002.gguf"
           for i in range(1, 3)), False, ""),
    ("maya", "hy4-flash-maya-s-iq2_s", "Hy4-Flash Maya-S IQ2_S",
     "peasantsmith/Hy4-Flash-Maya-GGUF", "Maya-S", "Maya-S",
     tuple(f"Maya-S/Hy4-Flash-Maya-S-IQ2_S-{i:05d}-of-00002.gguf"
           for i in range(1, 3)), False, ""),
    ("maya", "ling32-air-maya-s-iq2_xxs", "Ling-3.2-air Maya-S IQ2_XXS",
     "peasantsmith/Ling-3.2-air-Maya-GGUF", "Maya-S", "Maya-S",
     ("Ling-3.2-air-Maya-S-IQ2_XXS.gguf",), False, ""),
    ("maya", "ling32-air-maya-m-iq2_s", "Ling-3.2-air Maya-M IQ2_S",
     "peasantsmith/Ling-3.2-air-Maya-GGUF", "Maya-M", "Maya-M",
     ("Ling-3.2-air-Maya-M-IQ2_S.gguf",), False, ""),
    ("maya", "inkling2-mini-maya-l-nvfp4", "Inkling-2-Mini Maya-L NVFP4",
     "peasantsmith/Inkling-2-Mini-Maya-GGUF", "NVFP4", "Maya-L",
     ("Maya-L/Inkling-2-Mini-Maya-L-NVFP4.gguf",), False, ""),
    ("maya", "deepseek-v41-flash-abliterated-mix128g",
     "DeepSeek-V4.1-Flash abliterated Mix-128G（無検閲・gated）",
     "orcarouter/DeepSeek-V4.1-Flash-1017-abliterated-GGUF", "Mix-128G", "Maya-S",
     tuple(f"Mix-128G/DeepSeek-V4.1-Flash-1017-abliterated-Mix-128G-{i:05d}-of-00005.gguf"
           for i in range(1, 6)), True,
     "HF の規約の承諾とトークンが要る（gated）。AMD は未検証"),
    ("maya", "deepseek-v41-flash-abliterated-mix256g",
     "DeepSeek-V4.1-Flash abliterated Mix-256G（無検閲・gated）",
     "orcarouter/DeepSeek-V4.1-Flash-1017-abliterated-GGUF", "Mix-256G", "Maya-S",
     tuple(f"Mix-256G/DeepSeek-V4.1-Flash-1017-abliterated-Mix-256G-{i:05d}-of-00007.gguf"
           for i in range(1, 8)), True,
     "HF の規約の承諾とトークンが要る（gated）。AMD は未検証"),
)

# Maya-M Derisked の repo（公式 docs/MODELS.md の owner は peasantsmith。旧表記は誤り）。
MAYA_DERISKED_REPO = "peasantsmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF"
STRATA_GSQ_REPO = "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF"
# 共通目録の Strata 行は、公式目録のうちこの PC で取得して起動できるものだけ:
# GSQ-RCO（ISTA-DASLab / Coder / Swift）と Unsloth の UD-* と Q8_0。
# Gyro・SQ-*・NVFP4・Inkling などは旧 strata_catalog に残す（登録 API の互換は壊さない）。
_STRATA_GSQ_FAMILIES = frozenset({"qwen", "coder", "swift"})
_STRATA_UNSLOTH_FAMILIES = frozenset({"unsloth", "qwen36"})


def _strata_row_allowed(family_id: str, quant_id: str) -> bool:
    """旧 strata_catalog の行のうち共通目録に載せるものだけを通す。"""
    if family_id in _STRATA_GSQ_FAMILIES:
        return True                      # GSQ-RCO の量子化はすべて対象
    if family_id in _STRATA_UNSLOTH_FAMILIES:
        quant = quant_id.casefold()
        return quant.startswith("ud-") or quant == "q8_0"
    return False


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
    source: str = "catalog"        # "catalog"=既存目録 | "hf"=repo id とファイル名だけ
    family: str = ""               # 登録先の系列（Strata の family・Maya の tier）
    files: tuple[str, ...] = ()    # 調査で確認したファイル名（推測の size は持たない）

    def api_row(self, fit: str) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "format": self.format,
            "repo": self.repo, "quantization": self.quantization,
            "size_gib": self.size_gib, "memory_gib": self.memory_gib,
            "uncensored": self.uncensored, "supported": self.supported, "note": self.note,
            "acquire": self.acquire, "source": self.source, "fit": fit,
            "family": self.family, "files": list(self.files),
        }


def _researched_entries(runtime: str) -> list[Entry]:
    """調査済みの行（PLAN「目録候補」）を Entry へ。大きさは必ず None（推測しない）。"""
    return [Entry(
        runtime=row[0], id=row[1], name=row[2], repo=row[3], quantization=row[4],
        family=row[5], files=row[6], uncensored=row[7], note=row[8],
        format="rad" if runtime == "radiance" else "gguf",
        acquire="hf", source="hf", size_gib=None,
    ) for row in _RESEARCHED if row[0] == runtime]


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
            if not _strata_row_allowed(family.id, quant.id):
                continue
            out.append(Entry(
                runtime="strata", id=f"{family.id}/{quant.id}",
                name=f"{family.short_name} · {quant.label}",
                format="strata-quant",
                repo=STRATA_GSQ_REPO if family.id == "qwen" else "",
                quantization=quant.id, size_gib=quant.download_gib,
                memory_gib=quant.memory_gib, uncensored=family.uncensored,
                supported=not family.manual,
                note=family.note, acquire="runtime", source="catalog",
                family=family.id,
            ))
    return out


def _maya_entries() -> list[Entry]:
    """既存の Derisked 行（glm-m-derisked）は id を変えず先頭に置く（互換）。

    全一覧は Maya 本体の probe（catalog_ref）から読む。ここは調査済みの repo 行だけ。
    """
    rows = _researched_entries("maya")
    return [entry for entry in rows if entry.id == "glm-m-derisked"] + [
        entry for entry in rows if entry.id != "glm-m-derisked"
    ]


def entries_for(runtime: str) -> list[Entry]:
    if runtime == "llama":
        return _researched_entries("llama")
    if runtime == "radiance":
        return _researched_entries("radiance")
    if runtime == "sly-radiance":
        return _sly_entries()
    if runtime == "strata":
        return _strata_entries() + _researched_entries("strata")
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
