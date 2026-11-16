"""最後に見えていたローカル LLM（Home の LLM カード用）。

モデルがアンロードされている間も「何が、どれだけの速さで動いていたか」を出せるように、容量 API が
返した 1 台分を覚えておく。ファイルへ書くのは、モデルが変わったときとアンロードされたときだけ
（3 秒ごとの取得のたびには書かない）。再起動しても前回の値を出せるよう `data/llm-last-seen.json` に残す。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("control_deck.llm_last_seen")

# 覚える項目（本文や鍵は持たない）
FIELDS = ("id", "label", "port", "running_alias", "runtime", "ctx_total", "ctx_used", "ctx_active",
          "tokens_per_second", "tokens_per_second_single", "prefill_tokens_per_second", "timed_by_controldeck")

_lock = threading.Lock()
_last: dict | None = None
_saved: dict | None = None
_attempted: dict | None = None  # 最後に書こうとした内容（失敗しても同じ内容を書き直し続けない）
_loaded = False


def _path() -> Path:
    from app.config import data_dir

    return data_dir() / "llm-last-seen.json"


def _load_once() -> None:
    global _last, _saved, _attempted, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.warning("最後に見えていたローカル LLM を読めませんでした（無いものとして続ける）")
        return
    if isinstance(data, dict) and data.get("running_alias"):
        _last = {k: data.get(k) for k in (*FIELDS, "at")}
        _saved = dict(_last)
        _attempted = dict(_last)


def _save(item: dict) -> None:
    global _saved, _attempted
    _attempted = dict(item)
    path = _path()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        _saved = dict(item)
    except OSError as exc:
        # 次にモデルが変わるかアンロードされるまで書き直さない（3 秒ごとにログを出し続けない）。
        logger.warning("最後に見えていたローカル LLM の保存に失敗（表示は続ける）: %s", exc)
        tmp.unlink(missing_ok=True)


def _pick(endpoints: list[dict], prev_alias: str | None) -> dict | None:
    """処理中のものを先に、次に読み込み済みのもの。並んだら前回と同じモデルを選ぶ
    （2 台が同時に動いているとき、処理中の側が入れ替わるたびに書かないため）。"""
    ready = [e for e in endpoints if e.get("available") and int(e.get("slots") or 0) > 0]
    if not ready:
        return None
    return max(ready, key=lambda e: (int(e.get("busy") or 0) > 0, e.get("running_alias") == prev_alias))


def update(endpoints: list[dict], *, now: float | None = None) -> dict | None:
    """容量 API の結果を受け取る。動いているモデルがあれば覚え、無ければ最後に見えていた 1 台を返す。"""
    global _last
    with _lock:
        _load_once()
        item = _pick(endpoints, (_last or {}).get("running_alias"))
        if item is not None:
            prev = _last
            entry = {k: item.get(k) for k in FIELDS}
            # 待機中は速さが 0 になる実行環境がある。直前の速さを 0 で上書きしない。
            if prev and prev.get("running_alias") == entry["running_alias"]:
                for key in ("tokens_per_second", "prefill_tokens_per_second", "ctx_active"):
                    if not entry.get(key) and prev.get(key):
                        entry[key] = prev[key]
            entry["at"] = now or time.time()
            _last = entry
            if not _attempted or _attempted.get("running_alias") != entry["running_alias"]:
                _save(entry)
            return None
        if _last is not None and _attempted != _last:
            _save(_last)  # アンロードされた: 最後の値を残す
        return dict(_last) if _last else None
