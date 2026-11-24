"""取得先の管理（/news/sources のページ用）: 設定にある全ソースの一覧（形式・分野・間隔・有効/無効・実績）と、一括の操作。

2026-11-24 利用者「設定の中にボタンを置き、別の管理ページに飛ばして、疎通確認だったりとか、これまでの取得実績、最終取得日時などを…サイトごとに確認できたりとか、新しいサイトの追加、削除などができるように」。
設定（sources.json）が持つのは: rss/pages/github/codeberg/discourse/reddit/hackernews/huggingface と intervals（ソースごとの間隔）・disabled（止めているソース）。
ここは設定の読み書きだけ（取得の実績は fetchlog）。権限の判定は router。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from app.news import presets, sources

CONFIG_KINDS = ("rss", "page", "github", "codeberg", "discourse", "reddit", "hackernews", "huggingface")
FORMAT_LABELS = {"rss": "RSS/Atom", "page": "一覧の巡回", "github": "GitHub", "codeberg": "Codeberg", "discourse": "フォーラム", "reddit": "Reddit",
                 "hackernews": "Hacker News", "huggingface": "Hugging Face", "search": "検索", "steam": "Steam", "arxiv": "arXiv"}


def key_of(kind: str, value: str = "") -> str:
    return {"rss": f"rss:{value}", "page": f"page:{value}", "hackernews": "hn", "huggingface": "hf"}.get(kind, f"{kind}:{value}")


def format_of(kind: str, value: str) -> str:
    """表示用の形式（rss は URL から Steam・arXiv・検索・GitHub の Atom を見分ける）。"""
    if kind == "rss":
        host = (urlsplit(value).hostname or "").lower()
        if host == "store.steampowered.com":
            return "steam"
        if host == "rss.arxiv.org":
            return "arxiv"
        if host == "news.google.com":
            return "search"
        if host == "github.com":
            return "github"
    return kind


def entries(cfg: dict) -> list[dict]:
    """設定にあるソース全部（止めているものも）。{key, kind(設定の種類), format, name, value, domain, enabled, interval(分・0=既定), groups}。"""
    off, ivs = set(cfg.get("disabled", [])), cfg.get("intervals", {})
    rows: list[tuple[str, str, str]] = []
    rows += [("rss", f["url"], f["name"]) for f in cfg["rss"]]
    rows += [("page", f["url"], f["name"]) for f in cfg.get("pages", [])]
    rows += [("github", r, f"GitHub {r}") for r in cfg["github"]]
    rows += [("codeberg", r, f"Codeberg {r}") for r in cfg["codeberg"]]
    rows += [("discourse", d, d) for d in cfg["discourse"]]
    rows += [("reddit", s, f"r/{s}") for s in cfg["reddit"]]
    if cfg.get("hackernews"):
        rows.append(("hackernews", "", "Hacker News"))
    if cfg.get("huggingface"):
        rows.append(("huggingface", "", "Hugging Face"))
    out = []
    for kind, value, name in rows:
        key = key_of(kind, value)
        domain = (urlsplit(value).hostname or "") if value.startswith("https://") else {"github": "github.com", "codeberg": "codeberg.org", "reddit": "reddit.com",
                                                                                         "discourse": value, "hackernews": "news.ycombinator.com", "huggingface": "huggingface.co"}.get(kind, "")
        out.append({"key": key, "kind": kind, "format": format_of(kind, value), "name": name, "value": value, "domain": domain,
                    "enabled": key not in off, "interval": ivs.get(key, 0), "groups": presets.groups_of(kind, value)})
    return out


def _set_off(cfg: dict, keys: set[str], off: bool) -> None:
    cur = [k for k in cfg.get("disabled", []) if k not in keys]
    cfg["disabled"] = cur + (sorted(keys) if off else [])


def remove(cfg: dict, keys: set[str]) -> None:
    cfg["rss"] = [f for f in cfg["rss"] if key_of("rss", f["url"]) not in keys]
    cfg["pages"] = [f for f in cfg.get("pages", []) if key_of("page", f["url"]) not in keys]
    for kind in ("github", "codeberg", "discourse", "reddit"):
        cfg[kind] = [v for v in cfg[kind] if key_of(kind, v) not in keys]
    if "hn" in keys:
        cfg["hackernews"] = False
    if "hf" in keys:
        cfg["huggingface"] = False
    cfg["intervals"] = {k: v for k, v in cfg.get("intervals", {}).items() if k not in keys}
    cfg["disabled"] = [k for k in cfg.get("disabled", []) if k not in keys]


def add(cfg: dict, items: list[dict]) -> int:
    """items: [{kind, value, name?, interval?}]。すでにあるものは足さない（間隔だけ付ける）。足した数を返す。形の検査は save_config（normalize）。"""
    added = 0
    for it in items:
        kind, value = str(it.get("kind", "")), str(it.get("value", "")).strip()
        if kind not in CONFIG_KINDS:
            raise sources.ConfigError(f"知らない種類です: {kind[:20]}")
        key = key_of(kind, value)
        if key not in {e["key"] for e in entries(cfg)}:
            if kind in ("rss", "page"):
                cfg["rss" if kind == "rss" else "pages"].append({"name": str(it.get("name") or "").strip()[:60], "url": value})
            elif kind == "hackernews":
                cfg["hackernews"] = True
            elif kind == "huggingface":
                cfg["huggingface"] = True
            else:
                cfg[kind].append(value)
            added += 1
        if it.get("interval"):
            cfg.setdefault("intervals", {})[key] = int(it["interval"])
    return added


def apply(cfg: dict, action: str, keys: list[str], interval: int | None = None) -> dict:
    """一括の操作（enable・disable・delete・interval）を cfg に反映して返す（保存は呼び出し側）。interval=0 は「既定に戻す」。"""
    chosen = set(keys)
    known = {e["key"] for e in entries(cfg)}
    if not chosen or not chosen <= known:
        raise sources.ConfigError("知らないソースが含まれています")
    if action == "delete":
        remove(cfg, chosen)
    elif action in ("enable", "disable"):
        _set_off(cfg, chosen, action == "disable")
    elif action == "interval":
        if interval is None or not (interval == 0 or sources.INTERVAL_MIN <= interval <= sources.INTERVAL_MAX):
            raise sources.ConfigError(f"取得の間隔は {sources.INTERVAL_MIN}〜{sources.INTERVAL_MAX} 分です（0 は既定）")
        ivs = dict(cfg.get("intervals", {}))
        for k in chosen:
            if interval:
                ivs[k] = interval
            else:
                ivs.pop(k, None)
        cfg["intervals"] = ivs
    else:
        raise sources.ConfigError("知らない操作です")
    return cfg
