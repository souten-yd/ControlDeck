"""Strata 本体の目録をその場で読む（系列・量子化・ダウンロード量・必要メモリ・この PC での判定）。

2026-11-28 利用者「載るかどうかと容量を調査して目録を作って」。ControlDeck が手で写した表
（strata_catalog.py）は版が進むと古くなる（0.2.23 で Ornith の系列・Inkling の GSQ-RCO-IQ3_S が増えた）。
Strata 本体の `Catalog().rows()` を Strata 自身の Python で動かして読む（何も取得しない）。

- 結果は Strata の版（control の版）ごとに data_dir の下に覚える。版が変わったら読み直す。
- 読めないとき（未導入・時間切れ・形の違い）は None を返す。呼び出し側は手の表へ戻る。
- 引数は argv だけ（shell を使わない）。台本は ControlDeck の中のファイル。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.models.strata_live")

_TIMEOUT_S = 90
_KEEP_S = 6 * 3600          # 同じ版でも 6 時間で読み直す（この PC の RAM・判定が変わることがある）
_FAILED_RETRY_S = 600       # 読めなかったときは 10 分は試さない（画面を開くたびに 90 秒待たせない）
_last_failure = 0.0


def _control_dir() -> Path | None:
    from app.models_mgmt import strata

    base = strata._home() / ".local" / "share" / "strata" / "components" / "control"  # noqa: SLF001
    try:
        active = json.loads((base / "active.json").read_text(encoding="utf-8"))
        version = str(active.get("version") or active.get("active") or "")
    except (OSError, ValueError):
        version = ""
    if not version:
        versions = sorted(p.name for p in base.glob("[0-9]*") if p.is_dir())
        version = versions[-1] if versions else ""
    folder = base / version if version else None
    if folder is None or folder.is_symlink() or not (folder / "strata_cli" / "launch" / "catalog.py").is_file():
        return None
    return folder


def _cache_file(version: str) -> Path:
    return data_dir() / "cache" / f"strata-catalog-{version}.json"


def rows(*, refresh: bool = False) -> list[dict] | None:
    """Strata 本体の目録の行（`strata launch` と同じ）。読めなければ None。"""
    global _last_failure
    from app.models_mgmt import strata

    control = _control_dir()
    if control is None or not strata.installed():
        return None
    cache = _cache_file(control.name)
    if not refresh:
        try:
            if time.time() - cache.stat().st_mtime < _KEEP_S:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except (OSError, ValueError):
            pass
    if time.time() - _last_failure < _FAILED_RETRY_S:
        return None
    python = control / "python" / "bin" / "python3.12"
    script = Path(__file__).with_name("strata_live_dump.py")
    if not python.is_file():
        return None
    try:
        result = subprocess.run([str(python), "-I", str(script), str(control)], env=strata._env(),  # noqa: SLF001
                                capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        logger.warning("Strata の目録を読めませんでした (%s)", type(exc).__name__)
        data = None
    if not isinstance(data, list) or not all(isinstance(r, dict) and r.get("family") and r.get("model") for r in data):
        _last_failure = time.time()
        logger.warning("Strata の目録の形が違うので手の表を使います")
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(cache)
    except OSError:
        pass
    return data
