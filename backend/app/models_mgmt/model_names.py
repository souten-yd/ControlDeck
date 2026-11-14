"""One way to name a model in the UI, whatever runtime serves it.

The row title is the model (e.g. "Qwen3.8-27B"), the subtitle the runtime, quantisation and size.
Gateway aliases stay as they are (clients use them); they are identifiers, not display names.
"""
from __future__ import annotations

import re
from pathlib import Path

# A quantisation marker ends the model name: UD-Q4_K_M, IQ5R-V4, Q8_0, MXFP4, FP8, BF16, IQ2_XXS ...
_QUANT = re.compile(
    r"[-_.](?:UD[-_])?(?:I?Q\d[\w-]*|IQ\dR[\w-]*|MXFP4[\w-]*|NVFP4[\w-]*|FP8[\w-]*|BF16|F16|F32|W4A\d+[\w-]*|AWQ[\w-]*)$",
    re.IGNORECASE)
_SHARD = re.compile(r"-\d{5}-of-\d{5}$")


def _canonical(name: str) -> str:
    """qwen3.8-27b -> Qwen3.8-27B, glm-5.3-flash -> GLM-5.3-Flash (known families only)."""
    parts = name.split("-")
    out = []
    for part in parts:
        low = part.lower()
        if low in ("glm", "gsq", "rco", "moe", "vl", "a3b"):
            out.append(part.upper())
        elif re.fullmatch(r"\d+(\.\d+)?[bm]", low):
            out.append(low[:-1] + low[-1].upper())
        elif re.fullmatch(r"a\d+b", low):
            out.append(low.upper())
        elif low.startswith(("qwen", "gemma", "llama", "deepseek", "mistral", "phi", "minimax", "ornith", "nemotron")):
            out.append(part[0].upper() + part[1:])
        else:
            out.append(part[0].upper() + part[1:] if part[:1].islower() and part.isalpha() else part)
    return "-".join(out)


def from_file(path: str | Path) -> tuple[str, str]:
    """(model name, quantisation) from a model file name."""
    stem = _SHARD.sub("", Path(str(path)).name.rsplit(".", 1)[0]) if path else ""
    match = _QUANT.search(stem)
    if not match:
        return _canonical(stem), ""
    return _canonical(stem[:match.start()]), match.group(0)[1:].upper()


def file_size(path: str | Path) -> int:
    """Bytes of a model file, all shards of a split GGUF included."""
    p = Path(str(path)) if path else None
    if p is None or not p.is_file():
        return 0
    shard = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})\.gguf", p.name)
    if not shard:
        return p.stat().st_size
    total = 0
    for i in range(1, int(shard[3]) + 1):
        part = p.with_name(f"{shard[1]}-{i:05d}-of-{shard[3]}.gguf")
        total += part.stat().st_size if part.is_file() else 0
    return total


STRATA_FAMILIES = {"qwen": "Qwen3.8-Flash-Next", "unsloth": "Qwen3.8-Flash-Next", "deepseek41": "DeepSeek-V4.1-Flash"}
