"""ニュースの「更新・LLM・RAG」の設定（全体で 1 つ。data_dir/news/settings.json）。

2026-11-23 利用者「ニュースページ及びフィードの更新タイミング、その際に利用する LLM は個別に設定できること。このデータは基本公開情報だと思うので
OpenRouter の無料 LLM の利用を想定している。もちろんローカル LLM でも良い。検索した情報については、RAG に蓄積し…」。

- 更新: ホームのフィードの読み直しの間隔・日刊のまとめの時刻・週刊のまとめの曜日と時刻・取得しない時間帯。
  ニュースのページの取得の間隔（分）は従来どおり channels.json の interval_minutes（設定の「アーカイブ」）。
- LLM: 用途ごと（まとめ・一言要約と話題の語・記事の下ごしらえ〔概要・画像の描写・話題の語を 20 件ずつ 1 回で〕）にモデルを選ぶ。まとめの重点（digest_focus）は利用者が書く自由記述。"auto"・登録済みのローカルモデル・"cloud:openrouter/…"。
- RAG: 取った記事を知識基盤の分野 news へ入れる（本文も入れるか・古い記事を消すとき RAG も消すか）。
形は厳密に検査し、足りない項目は既定で補う。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta

from app.config import data_dir
from app.news.cpu_llm import MODEL_ID as CPU_MODEL_ID
from app.news.sources import ConfigError

WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")     # 0 = 月曜（datetime.weekday と同じ）
FEED_MIN, FEED_MAX = 1, 240
ON_PRUNE = ("keep", "delete")
FOCUS_MAX = 1000       # まとめの重点（自由記述）の上限の文字数
DEFAULTS: dict = {
    "feed_refresh_minutes": 15,
    "digest_daily": {"enabled": True, "time": "06:30"},
    "digest_weekly": {"enabled": True, "weekday": 0, "time": "07:00"},
    "quiet": {"enabled": False, "start": "01:00", "end": "05:00"},
    "llm": {"digest_model": "auto", "gist_enabled": False, "gist_model": "auto", "digest_focus": "", "prep_enabled": True, "prep_model": CPU_MODEL_ID, "prep_max_per_run": 100},
    "rag": {"enabled": True, "fulltext": False, "on_prune": "keep"},
    # 画像の無い記事に AI でイメージ画像を作る（CPU だけ。機能「ニュースの画像生成（CPU）」が要る。既定は切）
    "imagegen": {"enabled": False, "tier": "small", "threads": 8, "max_per_run": 30},
    # QNAP ニュースハブとつなぐ（app/news/hub_*.py。2026-11-25 利用者の案 C）。既定はローカル（今の動きを変えない）。ハブの設定とは別。
    "hub": {"source": "local", "sync_minutes": 5, "kinds": ["items", "entities", "events"], "channels": [], "categories": [],
            "features": {"imagegen": False, "reader": True, "rag": True},
            "jobs": {"enabled": False, "kinds": ["summarize_hq", "extract_retry", "image"], "max_per_run": 5, "interval_minutes": 15}},
}
HUB_SOURCES = ("local", "hub", "auto")        # データの出どころ: 自分で集める・ハブ・自動（ハブが答えなければローカル、戻ったらハブ）
HUB_KINDS = ("items", "entities", "events")   # 受け取る種類（ハブの /changes の kinds）
HUB_CATEGORIES = ("release", "news", "model", "forum", "reddit")    # 記事の種類（ハブの categories）
HUB_JOB_KINDS = ("summarize_hq", "extract_retry", "image")          # 借りられる仕事（embed・event_merge はまだ受けない）
_HUB_ID = re.compile(r"^[\w\-.]{1,60}$")
IMAGEGEN_TIERS = ("small", "medium", "large")   # news/imagegen_worker.TIERS と同じ（設定は重い部品を読み込まないよう、ここに持つ）
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")
_lock = threading.Lock()


def _path():
    return data_dir() / "news" / "settings.json"


def _clock(value: object, label: str) -> str:
    text = str(value or "")
    if not _CLOCK.match(text):
        raise ConfigError(f"{label}は HH:MM の形です")
    return text


def _table(raw: dict, key: str) -> dict:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{key} は表です")
    return value


def _bool(table: dict, key: str, default: bool) -> bool:
    return bool(table[key]) if key in table else default


def _int(value: object, label: str, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise ConfigError(f"{label}は整数です") from None
    if not low <= number <= high:
        raise ConfigError(f"{label}は {low}〜{high} です")
    return number


def _model(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _MODEL.match(text):
        raise ConfigError(f"{label}のモデル名の形が違います")
    return text


def _cloud_or_local_model(value: object, label: str) -> str:
    """CPU の LLM（cpu:...）は下ごしらえだけで使える。まとめ・一言要約では選べない。"""
    text = _model(value, label)
    if text.startswith("cpu:"):
        raise ConfigError(f"{label}には CPU の LLM は選べません（下ごしらえだけで使えます）")
    return text


def _prep_model(value: object) -> str:
    text = _model(value, "下ごしらえ")
    if text.startswith("cpu:") and text != CPU_MODEL_ID:
        raise ConfigError(f"下ごしらえの CPU のモデルは {CPU_MODEL_ID} だけです")
    return text


def _focus(value: object) -> str:
    """まとめの重点（利用者が日本語で書く自由記述）。改行は残し、制御文字は除く。上限を超えたら黙って切らずに断る。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError("まとめの重点は文字列です")
    text = "".join(ch for ch in value.replace("\r\n", "\n").replace("\r", "\n") if ch == "\n" or (ch >= " " and ch != "\x7f")).strip()
    if len(text) > FOCUS_MAX:
        raise ConfigError(f"まとめの重点は {FOCUS_MAX} 文字までです（いま {len(text)} 文字）")
    return text


def normalize(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError("設定の形が違います")
    out = json.loads(json.dumps(DEFAULTS))
    if "feed_refresh_minutes" in raw:
        try:
            minutes = int(raw["feed_refresh_minutes"])
        except (TypeError, ValueError):
            raise ConfigError("フィードの更新の間隔は整数（分）です") from None
        if not FEED_MIN <= minutes <= FEED_MAX:
            raise ConfigError(f"フィードの更新の間隔は {FEED_MIN}〜{FEED_MAX} 分です")
        out["feed_refresh_minutes"] = minutes
    daily = _table(raw, "digest_daily")
    out["digest_daily"] = {"enabled": _bool(daily, "enabled", True), "time": _clock(daily.get("time", "06:30"), "日刊の時刻")}
    weekly = _table(raw, "digest_weekly")
    try:
        weekday = int(weekly.get("weekday", 0))
    except (TypeError, ValueError):
        raise ConfigError("週刊の曜日は 0（月）〜6（日）です") from None
    if not 0 <= weekday <= 6:
        raise ConfigError("週刊の曜日は 0（月）〜6（日）です")
    out["digest_weekly"] = {"enabled": _bool(weekly, "enabled", True), "weekday": weekday, "time": _clock(weekly.get("time", "07:00"), "週刊の時刻")}
    quiet = _table(raw, "quiet")
    start, end = _clock(quiet.get("start", "01:00"), "取得しない時間帯の開始"), _clock(quiet.get("end", "05:00"), "取得しない時間帯の終わり")
    out["quiet"] = {"enabled": _bool(quiet, "enabled", False), "start": start, "end": end}
    llm = _table(raw, "llm")
    out["llm"] = {"digest_model": _cloud_or_local_model(llm.get("digest_model", "auto"), "まとめ"), "gist_enabled": _bool(llm, "gist_enabled", False),
                  "gist_model": _cloud_or_local_model(llm.get("gist_model", "auto"), "一言要約"), "digest_focus": _focus(llm.get("digest_focus", "")),
                  "prep_enabled": _bool(llm, "prep_enabled", True), "prep_model": _prep_model(llm.get("prep_model", CPU_MODEL_ID)), "prep_max_per_run": _int(llm.get("prep_max_per_run", 100), "下ごしらえの 1 回の件数", 1, 200)}
    rag = _table(raw, "rag")
    on_prune = rag.get("on_prune", "keep")
    if on_prune not in ON_PRUNE:
        raise ConfigError("古い記事を消すときの RAG は keep（残す）か delete（一緒に消す）です")
    out["rag"] = {"enabled": _bool(rag, "enabled", True), "fulltext": _bool(rag, "fulltext", False), "on_prune": on_prune}
    gen = _table(raw, "imagegen")
    tier = str(gen.get("tier", "small"))
    if tier not in IMAGEGEN_TIERS:
        raise ConfigError("画像生成のモデルの精度は small（小）・medium（中）・large（大）のどれかです")
    out["imagegen"] = {"enabled": _bool(gen, "enabled", False), "tier": tier,
                       "threads": _int(gen.get("threads", 8), "画像生成のスレッド数", 1, 16),
                       "max_per_run": _int(gen.get("max_per_run", 30), "画像生成の 1 回の件数", 1, 100)}
    out["hub"] = _hub(_table(raw, "hub"))
    return out


def _pick(value: object, allowed: tuple[str, ...], label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ConfigError(f"{label}は文字列の一覧です")
    bad = [v for v in value if v not in allowed]
    if bad:
        raise ConfigError(f"{label}に知らない値があります: {bad[0]}")
    return [v for v in allowed if v in value]


def _hub(raw: dict) -> dict:
    """ハブとつなぐ設定。出どころ・受け取る範囲・機能の入切・借りる仕事。形が違えば断る（黙って直さない）。"""
    base = DEFAULTS["hub"]
    source = raw.get("source", base["source"])
    if source not in HUB_SOURCES:
        raise ConfigError("データの出どころは local（自分で集める）・hub（ハブ）・auto（自動）のどれかです")
    channels = raw.get("channels", [])
    if not isinstance(channels, list) or len(channels) > 50 or any(not isinstance(c, str) or not _HUB_ID.match(c) for c in channels):
        raise ConfigError("受け取るチャンネルは、チャンネルの id の一覧（50 件まで）です")
    features = _table(raw, "features")
    jobs = _table(raw, "jobs")
    return {"source": source, "sync_minutes": _int(raw.get("sync_minutes", base["sync_minutes"]), "ハブの同期の間隔（分）", 1, 240),
            "kinds": _pick(raw.get("kinds", base["kinds"]), HUB_KINDS, "受け取る種類") or list(HUB_KINDS)[:1],
            "channels": list(dict.fromkeys(channels)), "categories": _pick(raw.get("categories", []), HUB_CATEGORIES, "受け取る分類"),
            "features": {"imagegen": _bool(features, "imagegen", False), "reader": _bool(features, "reader", True), "rag": _bool(features, "rag", True)},
            "jobs": {"enabled": _bool(jobs, "enabled", False), "kinds": _pick(jobs.get("kinds", base["jobs"]["kinds"]), HUB_JOB_KINDS, "借りる仕事の種類"),
                     "max_per_run": _int(jobs.get("max_per_run", 5), "仕事を借りる 1 回の件数", 1, 20),
                     "interval_minutes": _int(jobs.get("interval_minutes", 15), "仕事を借りる間隔（分）", 1, 240)}}


def load() -> dict:
    try:
        return normalize(json.loads(_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize({})


def save(raw: object) -> dict:
    cfg = normalize(raw)
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return cfg


# ---- 時刻の判定 ----

def _minutes(clock: str) -> int:
    return int(clock[:2]) * 60 + int(clock[3:])


def in_quiet(cfg: dict, now: datetime) -> bool:
    """取得しない時間帯の中か。開始は含み、終わりは含まない。開始 > 終了は日をまたぐ（22:00〜06:00）。"""
    quiet = cfg["quiet"]
    if not quiet["enabled"]:
        return False
    start, end, current = _minutes(quiet["start"]), _minutes(quiet["end"]), now.hour * 60 + now.minute
    if start == end:
        return False
    return start <= current < end if start < end else (current >= start or current < end)


def due_digests(cfg: dict, now: datetime) -> list[tuple[str, str]]:
    """いま作るべきまとめ [(period, day)]。直近の予定の時刻を過ぎていれば（寝ていた後でも）対象。作ったかの判定は呼ぶ側。
    日刊は今日の時刻、週刊は直近の（曜日, 時刻）。day はその予定の日付（YYYY-MM-DD）。"""
    out: list[tuple[str, str]] = []
    daily = cfg["digest_daily"]
    if daily["enabled"] and now.hour * 60 + now.minute >= _minutes(daily["time"]):
        out.append(("daily", now.strftime("%Y-%m-%d")))
    weekly = cfg["digest_weekly"]
    if weekly["enabled"]:
        day = now.date() - timedelta(days=(now.weekday() - weekly["weekday"]) % 7)
        if day == now.date() and now.hour * 60 + now.minute < _minutes(weekly["time"]):
            day -= timedelta(days=7)
        out.append(("weekly", day.isoformat()))
    return out
