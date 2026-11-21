"""モデルの表示名（利用者が付ける名前）。2026-11-21 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。

- ゲートウェイでの名前（alias）は変えない。既定のモデル（preferred_models）・会話・Workflow・OpenCode の設定が alias を
  覚えているので、alias を変えるとそれらが切れる。画面・モデルの一覧に出す名前だけを変える。
- 置き場: data_dir/model-display-names.json（"<ランタイム>:<alias>" → 名前）。空の名前は既定（ファイル名など）に戻す。
"""
from __future__ import annotations

import json
import os
import re
import threading

from app.config import data_dir

RUNTIMES = ("llama.cpp", "strata", "radiance", "maya-s")
MAX_CHARS = 80
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_lock = threading.Lock()


class DisplayNameError(ValueError):
    pass


def _path():
    return data_dir() / "model-display-names.json"


def _read() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def get(runtime: str, alias: str) -> str:
    return _read().get(f"{runtime}:{alias}", "")


def set_name(runtime: str, alias: str, name: str) -> str:
    if runtime not in RUNTIMES:
        raise DisplayNameError("知らないランタイムです")
    name = " ".join((name or "").split())
    if len(name) > MAX_CHARS or _CONTROL.search(name):
        raise DisplayNameError(f"名前は {MAX_CHARS} 文字まで（改行などは使えません）")
    with _lock:
        data = _read()
        key = f"{runtime}:{alias}"
        if name:
            data[key] = name
        else:
            data.pop(key, None)
        path = _path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return name


def apply(runtime: str, alias: str, default: str) -> str:
    """一覧に出す名前: 利用者の付けた名前、無ければ既定。"""
    return get(runtime, alias) or default
