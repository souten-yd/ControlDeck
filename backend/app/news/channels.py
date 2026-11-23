"""チャンネル＝検索テーマ（設定 data_dir/news/channels.json）と、記事がどのチャンネルに属するかの判定。

2026-11-23 利用者「設定で検索テーマや表示のデザインなど、ニュースを便利に使うための設定を詳細にできるように」。
- チャンネルは 名前・色・含める語・外す語・ソースの絞り込み（記事の種類）・言語・件数。並び順は一覧の順（CH 01 から）。
- 所属は **問い合わせ時に判定する**（記事に印は持たない）:
  1. 外す語（題・抜粋に含む）に合えば所属しない。
  2. 種類の絞り込みがあれば、その種類だけ。
  3. 検索で取った記事（source_key が search:<id>: で始まる）は、そのチャンネルのもの。
  4. それ以外は、題・抜粋に含める語のどれかが入っていれば（大文字小文字は区別しない）。
     含める語が空なら、種類の絞り込みに合う記事すべて（例: 使っているソフトの更新 = release 全部）。
- 同じ url は 1 行しか持てないので、複数のチャンネルが同じ記事を検索で取ったときは、最後に取ったチャンネルの印が残る
  （語の判定は常に働くので、題に語が入っていれば他のチャンネルにも属する）。
- 他に、アーカイブの残す件数と取得の間隔（全体の設定）もここに持つ。
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from app.config import data_dir
from app.news.sources import ConfigError

MAX_CHANNELS = 12
MAX_TERMS = 20
TERM_CHARS = 60
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 3, 30, 6
KEEP_MIN, KEEP_MAX, KEEP_DEFAULT = 1_000, 200_000, 50_000
INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 15, 360, 30
SEARCH_TERMS = 6          # 検索に使う語の数（長い問い合わせは嫌われる）
LANGS = ("ja", "en", "both")
PALETTE = ("#f5c518", "#38bdf8", "#f472b6", "#34d399", "#a78bfa", "#fb923c", "#f87171", "#2dd4bf", "#c084fc", "#facc15", "#60a5fa", "#4ade80")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_CHANNELS: list[dict] = [
    {"id": "local-llm", "name": "ローカル LLM", "color": PALETTE[0], "include": ["llama.cpp", "Strata", "Qwen", "Inkling", "GGUF"],
     "exclude": [], "kinds": [], "lang": "both", "limit": LIMIT_DEFAULT, "search": True},
    {"id": "amd-gpu", "name": "AMD・GPU", "color": PALETTE[1], "include": ["ROCm", "Radeon", "RDNA"],
     "exclude": [], "kinds": [], "lang": "both", "limit": LIMIT_DEFAULT, "search": True},
    {"id": "patent", "name": "特許", "color": PALETTE[2], "include": ["特許", "知財", "patent"],
     "exclude": [], "kinds": [], "lang": "ja", "limit": LIMIT_DEFAULT, "search": True},
    {"id": "dev-tools", "name": "開発ツール", "color": PALETTE[3], "include": ["OpenCode", "Codex", "Claude Code"],
     "exclude": [], "kinds": [], "lang": "both", "limit": LIMIT_DEFAULT, "search": True},
    {"id": "updates", "name": "使っているソフトの更新", "color": PALETTE[4], "include": [], "exclude": [], "kinds": ["release"],
     "lang": "both", "limit": LIMIT_DEFAULT, "search": False},
]


def _path():
    return data_dir() / "news" / "channels.json"


def _terms(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field} は語の一覧です")
    if len(value) > MAX_TERMS:
        raise ConfigError(f"{field} は {MAX_TERMS} 語までです")
    out = []
    for raw in value:
        word = re.sub(r"\s+", " ", str(raw or "")).strip()
        if len(word) > TERM_CHARS:
            raise ConfigError(f"{field} の語は {TERM_CHARS} 文字までです: {word[:20]}…")
        if word and word.lower() not in [w.lower() for w in out]:
            out.append(word)
    return out


def normalize_channels(raw: object, kinds_allowed: tuple[str, ...]) -> list[dict]:
    if not isinstance(raw, list):
        raise ConfigError("channels は一覧です")
    if len(raw) > MAX_CHANNELS:
        raise ConfigError(f"チャンネルは {MAX_CHANNELS} 個までです")
    out: list[dict] = []
    used: set[str] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ConfigError("チャンネルの形が違います")
        name = re.sub(r"\s+", " ", str(row.get("name") or "")).strip()
        if not name or len(name) > 40:
            raise ConfigError("チャンネルの名前は 1〜40 文字です")
        cid = str(row.get("id") or "").strip().lower()
        if not cid:
            cid = f"ch{index + 1}"
        if not _ID.match(cid):
            raise ConfigError(f"チャンネルの id は英小文字・数字・- の 30 文字までです: {cid[:30]}")
        base, n = cid, 2
        while cid in used:
            cid, n = f"{base[:26]}-{n}", n + 1
        used.add(cid)
        color = str(row.get("color") or PALETTE[index % len(PALETTE)])
        if not _COLOR.match(color):
            raise ConfigError(f"色は #rrggbb の形です: {color[:20]}")
        include, exclude = _terms(row.get("include"), "含める語"), _terms(row.get("exclude"), "外す語")
        kinds = row.get("kinds") or []
        if not isinstance(kinds, list) or any(k not in kinds_allowed for k in kinds):
            raise ConfigError("ソースの絞り込みは記事の種類の一覧です")
        if not include and not kinds:
            raise ConfigError(f"「{name}」には含める語か、ソースの絞り込みのどちらかが要ります")
        lang = row.get("lang", "both")
        if lang not in LANGS:
            raise ConfigError("言語は ja・en・both のどれかです")
        try:
            limit = int(row.get("limit", LIMIT_DEFAULT))
        except (TypeError, ValueError):
            raise ConfigError("件数は整数です") from None
        if not LIMIT_MIN <= limit <= LIMIT_MAX:
            raise ConfigError(f"1 チャンネルの件数は {LIMIT_MIN}〜{LIMIT_MAX} です")
        out.append({"id": cid, "name": name, "color": color.lower(), "include": include, "exclude": exclude, "kinds": list(dict.fromkeys(kinds)),
                    "lang": lang, "limit": limit, "search": bool(row.get("search", True)) and bool(include)})
    return out


def _int(raw: dict, key: str, low: int, high: int, default: int, label: str) -> int:
    try:
        value = int(raw.get(key, default))
    except (TypeError, ValueError):
        raise ConfigError(f"{label}は整数です") from None
    if not low <= value <= high:
        raise ConfigError(f"{label}は {low}〜{high} です")
    return value


def normalize(raw: object) -> dict:
    from app.news.sources import KINDS

    if not isinstance(raw, dict):
        raise ConfigError("設定の形が違います")
    return {"channels": normalize_channels(raw.get("channels", DEFAULT_CHANNELS), KINDS),
            "archive_keep": _int(raw, "archive_keep", KEEP_MIN, KEEP_MAX, KEEP_DEFAULT, "アーカイブの残す件数"),
            "interval_minutes": _int(raw, "interval_minutes", INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT, "取得の間隔（分）")}


def load() -> dict:
    try:
        return normalize(json.loads(_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize({})


def save(raw: object) -> dict:
    cfg = normalize(raw)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return cfg


def numbered(channels: list[dict]) -> list[dict]:
    """一覧の順に number（1 から）を付ける。"""
    return [dict(c, number=i + 1) for i, c in enumerate(channels)]


# ---- 所属の判定 ----

def _has(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def matches(channel: dict, *, kind: str, source_key: str, title: str, summary: str = "") -> bool:
    text = f"{title}\n{summary}"
    if channel["exclude"] and _has(text, channel["exclude"]):
        return False
    if channel["kinds"] and kind not in channel["kinds"]:
        return False
    if source_key.startswith(f"search:{channel['id']}:"):
        return True
    if not channel["include"]:
        return bool(channel["kinds"])
    return _has(text, channel["include"])


def first_channel(channels: list[dict], row) -> str | None:
    for channel in channels:
        if matches(channel, kind=row.kind, source_key=row.source_key, title=row.title, summary=row.summary or ""):
            return channel["id"]
    return None


# ---- 検索の問い合わせ ----

def query_text(channel: dict) -> str:
    """検索サイトへ渡す語（含める語の先頭から。空白を含む語は引用符で囲む。OR でつなぐ。外す語は - で除く）。"""
    words = channel["include"][:SEARCH_TERMS]
    parts = [f'"{w}"' if " " in w else w for w in words]
    q = " OR ".join(parts)
    return q


def gnews_url(channel: dict, lang: str) -> str:
    """Google ニュースの検索の RSS（無料・キー不要）。語は URL に符号化する。"""
    q = query_text(channel)
    ui = "hl=ja&gl=JP&ceid=JP:ja" if lang == "ja" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote(q)}&{ui}"


def langs(channel: dict) -> list[str]:
    return ["ja", "en"] if channel["lang"] == "both" else [channel["lang"]]
