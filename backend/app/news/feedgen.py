"""フィードを作る手伝い（設定 → ニュース → 取得先の管理 →「フィードを作る」）。入力の検査と URL の組み立てだけ（通信はしない。確認は probe.check）。

2026-11-24 利用者「情報収集の蓄積、ナレッジ化を効率的に」→ キーワード（Google ニュースの RSS）・GitHub（Release・コミット・タグの Atom）・Steam（ゲーム別のニュース）・arXiv（分野の組み合わせ）を作る。
結果は {"kind": "rss"|"github", "value": URL か owner/repo, "name": 名前, "lang": "ja"|"en"}。入力が不正なら ValueError（メッセージは画面に出す）。
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
BRANCH = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
ARXIV_CATS = (
    "cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.SD", "eess.AS", "cs.RO", "cs.IR", "cs.NE", "cs.DC", "cs.AR", "cs.NA", "cs.SE", "cs.CR", "cs.MM", "cs.GR",
    "cs.MA", "cs.PF", "cs.HC", "cs.DB", "cs.OS", "cs.PL", "cs.NI", "cs.CY", "cs.MS", "stat.ML", "stat.ME", "math.OC", "eess.IV", "eess.SP", "q-bio.NC", "physics.comp-ph",
)
MAX_ARXIV = 8
GITHUB_KINDS = ("releases", "commits", "tags")


def google_news(keyword: str, lang: str = "ja") -> dict:
    word = " ".join(str(keyword or "").split())
    if not 1 <= len(word) <= 100 or re.search(r"[\x00-\x1f<>]", word):
        raise ValueError("キーワードは 1〜100 文字で入れてください")
    if lang not in ("ja", "en"):
        raise ValueError("言語は ja か en です")
    ui = "hl=ja&gl=JP&ceid=JP:ja" if lang == "ja" else "hl=en-US&gl=US&ceid=US:en"
    return {"kind": "rss", "value": f"https://news.google.com/rss/search?q={quote(word)}&{ui}", "name": f"Google ニュース: {word}（{'日本語' if lang == 'ja' else '英語'}）", "lang": lang}


def github(target: str, what: str = "releases", branch: str = "main") -> dict:
    text = str(target or "").strip()
    if text.startswith(("http://", "https://")):
        parts = urlsplit(text)
        if (parts.hostname or "").lower() not in ("github.com", "www.github.com"):
            raise ValueError("github.com の URL か owner/repo で入れてください")
        segs = [s for s in parts.path.split("/") if s]
        text = "/".join(segs[:2])
    text = text.removesuffix(".git")
    if not REPO.match(text):
        raise ValueError("owner/repo の形（例 ggml-org/llama.cpp）か GitHub の URL で入れてください")
    if what not in GITHUB_KINDS:
        raise ValueError("種類は releases・commits・tags のどれかです")
    name = text.split("/")[1]
    if what == "releases":
        return {"kind": "github", "value": text, "name": name, "lang": "en"}      # Release は GitHub の取得（API。上限のときは releases.atom）で取る
    if what == "commits":
        branch = (branch or "main").strip()
        if not BRANCH.match(branch) or ".." in branch:
            raise ValueError("ブランチ名として使えません")
        return {"kind": "rss", "value": f"https://github.com/{text}/commits/{quote(branch, safe='/')}.atom", "name": f"{name}（コミット {branch}）", "lang": "en"}
    return {"kind": "rss", "value": f"https://github.com/{text}/tags.atom", "name": f"{name}（タグ）", "lang": "en"}


def steam(target: str) -> dict:
    text = str(target or "").strip()
    m = re.fullmatch(r"\d{1,9}", text) or re.search(r"store\.steampowered\.com/app/(\d{1,9})", text) or re.search(r"steamcommunity\.com/app/(\d{1,9})", text)
    if not m:
        raise ValueError("Steam の App ID（数字）かストアの URL（store.steampowered.com/app/…）で入れてください")
    app = m.group(1) if m.groups() else m.group(0)
    return {"kind": "rss", "value": f"https://store.steampowered.com/feeds/news/app/{app}/", "name": f"Steam {app} のニュース", "lang": "en"}


def arxiv(cats: list[str]) -> dict:
    if not isinstance(cats, list) or not cats:
        raise ValueError("分野を 1 つ以上選んでください")
    chosen = list(dict.fromkeys(str(c).strip() for c in cats))
    if len(chosen) > MAX_ARXIV:
        raise ValueError(f"分野は {MAX_ARXIV} 個までです")
    bad = [c for c in chosen if c not in ARXIV_CATS]
    if bad:
        raise ValueError(f"知らない分野です: {bad[0][:30]}")
    return {"kind": "rss", "value": "https://rss.arxiv.org/rss/" + "+".join(chosen), "name": "arXiv " + "+".join(chosen), "lang": "en"}


def generate(body: dict) -> dict:
    kind = body.get("type")
    if kind == "keyword":
        return google_news(body.get("keyword", ""), body.get("lang", "ja"))
    if kind == "github":
        return github(body.get("target", ""), body.get("what", "releases"), body.get("branch", "main"))
    if kind == "steam":
        return steam(body.get("target", ""))
    if kind == "arxiv":
        return arxiv(body.get("cats", []))
    raise ValueError("種類は keyword・github・steam・arxiv のどれかです")


def app_of(value: str) -> str:
    """URL の復号（表示用）。"""
    return unquote(value)
