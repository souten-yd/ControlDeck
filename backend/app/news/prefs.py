"""ニュースの利用者ごとの設定（表示のデザイン・好み）。data_dir/news/prefs.json に利用者 id ごとに保存する（端末ではなく利用者ごと）。

表示（display）は画面の CSS の変数へ、好み（taste）はおすすめの点・ミュート・既読の表示へ効く。
形は厳密に検査し、足りない項目は既定で補う。
"""
from __future__ import annotations

import json
import os
import re
import threading

from app.config import data_dir
from app.news.sources import ConfigError

DESIGNS = ("broadcast", "magazine", "newspaper", "finance", "text")
# 旧い名前の移し替え（保存済みの「紙面」は経済紙、「コンパクト」はテキスト一覧として扱う）
LEGACY_DESIGNS = {"paper": "finance", "compact": "text"}
DEFAULT_DISPLAY = {"design": "broadcast", "accent": "", "font_size": "m", "density": "normal", "images": True, "columns": "auto",
                   "ticker": True, "onair": True, "numbers": True, "reduce_motion": False}
DEFAULT_TASTE = {"strength": 0.5, "use_chat": True, "half_life_days": 30, "mute_terms": [], "mute_sources": [], "dim_read": True}
CHOICES = {"design": DESIGNS, "font_size": ("s", "m", "l"), "density": ("relaxed", "normal", "tight"), "columns": ("auto", "1", "2", "3")}
BOOLS_DISPLAY = ("images", "ticker", "onair", "numbers", "reduce_motion")
BOOLS_TASTE = ("use_chat", "dim_read")
HALF_LIFE_MIN, HALF_LIFE_MAX = 3, 180
MAX_MUTES = 50
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_lock = threading.Lock()


def _path():
    return data_dir() / "news" / "prefs.json"


def _all() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{label}は一覧です")
    if len(value) > MAX_MUTES:
        raise ConfigError(f"{label}は {MAX_MUTES} 件までです")
    out = []
    for v in value:
        text = re.sub(r"\s+", " ", str(v or "")).strip()
        if len(text) > 80:
            raise ConfigError(f"{label}は 1 件 80 文字までです")
        if text and text not in out:
            out.append(text)
    return out


def normalize(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError("設定の形が違います")
    display_in, taste_in = raw.get("display", {}), raw.get("taste", {})
    if not isinstance(display_in, dict) or not isinstance(taste_in, dict):
        raise ConfigError("display・taste は表です")
    display = dict(DEFAULT_DISPLAY)
    for key, allowed in CHOICES.items():
        if key in display_in:
            if key == "design":
                display_in = {**display_in, key: LEGACY_DESIGNS.get(str(display_in[key]), str(display_in[key]))}
            if str(display_in[key]) not in allowed:
                raise ConfigError(f"{key} は {'・'.join(allowed)} のどれかです")
            display[key] = str(display_in[key])
    for key in BOOLS_DISPLAY:
        if key in display_in:
            display[key] = bool(display_in[key])
    accent = str(display_in.get("accent", "") or "")
    if accent and not _COLOR.match(accent):
        raise ConfigError("アクセント色は #rrggbb の形です")
    display["accent"] = accent.lower()
    taste = dict(DEFAULT_TASTE)
    if "strength" in taste_in:
        try:
            strength = float(taste_in["strength"])
        except (TypeError, ValueError):
            raise ConfigError("おすすめの強さは 0〜1 の数です") from None
        if not 0.0 <= strength <= 1.0:
            raise ConfigError("おすすめの強さは 0〜1 の数です")
        taste["strength"] = round(strength, 2)
    if "half_life_days" in taste_in:
        try:
            days = int(taste_in["half_life_days"])
        except (TypeError, ValueError):
            raise ConfigError("半減の日数は整数です") from None
        if not HALF_LIFE_MIN <= days <= HALF_LIFE_MAX:
            raise ConfigError(f"半減の日数は {HALF_LIFE_MIN}〜{HALF_LIFE_MAX} です")
        taste["half_life_days"] = days
    for key in BOOLS_TASTE:
        if key in taste_in:
            taste[key] = bool(taste_in[key])
    for key, label in (("mute_terms", "ミュートの語"), ("mute_sources", "ミュートのソース")):
        if key in taste_in:
            taste[key] = _strings(taste_in[key], label)
    return {"display": display, "taste": taste}


def load(user_id: int) -> dict:
    try:
        return normalize(_all().get(str(user_id)) or {})
    except ConfigError:
        return normalize({})


def taste(user_id: int) -> dict:
    return load(user_id)["taste"]


def save(user_id: int, raw: object) -> dict:
    prefs = normalize(raw)
    with _lock:
        everything = _all()
        everything[str(user_id)] = prefs
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(everything, handle, ensure_ascii=False)
        os.replace(tmp, path)
    return prefs
