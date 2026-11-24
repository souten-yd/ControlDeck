"""ニュースのソースの設定と、ソースごとの取得・整形。

- 設定は data_dir/news/sources.json（利用者が RSS・リポジトリ・Discourse・subreddit を足し外しできる）。形と件数の上限を検査する。
- 取得の安全は既存を使い回す: GitHub・Codeberg・Hacker News・Discourse・Reddit は workflows/research_tools（guarded_client。内部アドレスを断る）、
  RSS は knowledge/sources.http_get（https のみ・大きさ上限・出口は同じ）、Hugging Face は models_mgmt/hf_search。新しい生の httpx は書かない。
- 有料・キーが要るものは使わない。Reddit だけは、利用者が設定 → API キーに入れたときだけ OAuth で取る（無ければ取らない）。
- 外から来た文は平文として扱う（HTML は落とす）。url・image_url は http/https だけ。
- どの取得関数も「記事の dict の一覧」を返し、失敗は例外（service が記録して他のソースを止めない）。
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable
from urllib.parse import quote, urljoin, urlsplit

from app.config import data_dir

logger = logging.getLogger("control_deck.news.sources")

KINDS = ("release", "news", "model", "forum", "reddit", "event")
KIND_LABELS = {"release": "更新", "news": "ニュース", "model": "モデル", "forum": "フォーラム", "reddit": "Reddit", "event": "出来事"}
MAX_PER_LIST = 500      # 目録（プリセット 300 件前後）を足しても入る数
MAX_PER_SOURCE = 30
MAX_PAGES = 30           # 一覧のページを巡回するソースの数（1 つあたり 1 時間に 1 回・30 件まで）
SUMMARY_CHARS = 300
TITLE_CHARS = 300

DEFAULT_CONFIG: dict = {
    "enabled": {kind: True for kind in KINDS},
    "github": ["Niko1221/Strata", "ggml-org/llama.cpp", "sst/opencode"],
    "codeberg": ["StillDeadcode/radiance"],
    "rss": [{"name": "GIGAZINE", "url": "https://gigazine.net/news/rss_2.0/"},
            {"name": "Phoronix", "url": "https://www.phoronix.com/rss.php"}],
    "pages": [],           # 記事の一覧のページを巡回するソース（RSS が無いサイト。probe.crawl_list）
    "hackernews": True,
    "huggingface": True,
    "discourse": ["forum.level1techs.com", "discuss.huggingface.co"],
    "reddit": ["LocalLLaMA"],
    "intervals": {},       # ソースごとの取得の間隔（分）。{source_key: 分}。無いソースは設定の既定（channels の interval_minutes）
    "disabled": [],        # 止めているソースの source_key（設定には残すが取らない）
}
INTERVAL_MIN, INTERVAL_MAX = 5, 10080     # ソースごとの取得の間隔（分）の範囲（5 分〜1 週間）
MAX_PER_RUN = 60          # 1 回の取得で取るソースの数の上限（見張り）
MAX_DISABLED = 1000

# 利用者自身のリポジトリの記事は出さない（2026-11-23 利用者「私が作っているコントロールデックとか私のリポジトリのデータは特に表示する必要はない」）。
# source_key の先頭で判定する（貯まった記事の一覧・ホーム・まとめ・チャンネルから除く）。
HIDDEN_SOURCE_PREFIXES = ("github:souten-yd/",)
HIDDEN_REPO_OWNERS = ("souten-yd/",)
MIGRATION_OWN_REPO = "own-repo-removed"

_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SUB = re.compile(r"^[A-Za-z0-9_]{2,21}$")


class ConfigError(ValueError):
    pass


def _path():
    return data_dir() / "news" / "sources.json"


def https_url(value: object, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    parts = urlsplit(text)
    if len(text) > limit or parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ConfigError(f"https の URL だけ使えます: {text[:80]}")
    return text


def safe_url(value: object, base: str = "") -> str | None:
    """http/https の絶対 URL だけ返す（それ以外・長すぎる値は None）。画像・リンクの両方に使う。"""
    text = html.unescape(str(value or "")).strip()
    if base:
        text = urljoin(base, text)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or len(text) > 1000 or re.search(r"[\s<>\"']", text):
        return None
    return text


def normalize(raw: object) -> dict:
    """設定の形を検査して、整えた dict を返す。足りない項目は既定。形が違えば ConfigError。"""
    if not isinstance(raw, dict):
        raise ConfigError("設定の形が違います")
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    enabled = raw.get("enabled", out["enabled"])
    if not isinstance(enabled, dict):
        raise ConfigError("enabled は種類ごとの真偽の表です")
    out["enabled"] = {kind: bool(enabled.get(kind, True)) for kind in KINDS}
    for flag in ("hackernews", "huggingface"):
        if flag in raw:
            out[flag] = bool(raw[flag])

    def names(key: str, check: Callable[[str], str]) -> list[str]:
        values = raw.get(key, out[key])
        if not isinstance(values, list):
            raise ConfigError(f"{key} は一覧です")
        if len(values) > MAX_PER_LIST:
            raise ConfigError(f"{key} は {MAX_PER_LIST} 件までです")
        return list(dict.fromkeys(check(str(v).strip()) for v in values))

    def repo(value: str) -> str:
        if not _REPO.match(value):
            raise ConfigError(f"owner/repo の形で入れてください: {value[:60]}")
        return value

    def sub(value: str) -> str:
        value = value.removeprefix("/r/").removeprefix("r/")
        if not _SUB.match(value):
            raise ConfigError(f"subreddit の名前として使えません: {value[:40]}")
        return value

    def domain(value: str) -> str:
        from app.workflows import research_keys

        try:
            return research_keys.valid_domain(value)
        except research_keys.ResearchKeyError as exc:
            raise ConfigError(str(exc)) from exc

    out["github"] = names("github", repo)
    done = raw.get("migrated", [])
    out["migrated"] = [m for m in done if isinstance(m, str)][:10] if isinstance(done, list) else []
    out["codeberg"] = names("codeberg", repo)
    out["discourse"] = names("discourse", domain)
    out["reddit"] = names("reddit", sub)
    feeds = raw.get("rss", out["rss"])
    if not isinstance(feeds, list):
        raise ConfigError("rss は一覧です")
    if len(feeds) > MAX_PER_LIST:
        raise ConfigError(f"rss は {MAX_PER_LIST} 件までです")
    cleaned, seen = [], set()
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ConfigError("rss の各項目は {name, url} です")
        url = https_url(feed.get("url"))
        if url in seen:
            continue
        seen.add(url)
        cleaned.append({"name": (str(feed.get("name") or "").strip() or urlsplit(url).hostname or "RSS")[:60], "url": url})
    out["rss"] = cleaned
    pages = raw.get("pages", [])
    if not isinstance(pages, list):
        raise ConfigError("pages は一覧です")
    if len(pages) > MAX_PAGES:
        raise ConfigError(f"pages は {MAX_PAGES} 件までです")
    ivs = raw.get("intervals", {})
    if not isinstance(ivs, dict):
        raise ConfigError("intervals は {source_key: 分} の表です")
    out["intervals"] = {}
    for key, minutes in ivs.items():
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise ConfigError(f"取得の間隔は整数（分）です: {str(key)[:60]}") from None
        if not INTERVAL_MIN <= minutes <= INTERVAL_MAX:
            raise ConfigError(f"取得の間隔は {INTERVAL_MIN}〜{INTERVAL_MAX} 分です")
        if len(str(key)) > 600:
            raise ConfigError("source_key が長すぎます")
        out["intervals"][str(key)] = minutes
    off = raw.get("disabled", [])
    if not isinstance(off, list) or len(off) > MAX_DISABLED:
        raise ConfigError("disabled は source_key の一覧です")
    out["disabled"] = list(dict.fromkeys(str(k)[:600] for k in off))
    out["pages"] = []
    for page in pages:
        if not isinstance(page, dict):
            raise ConfigError("pages の各項目は {name, url} です")
        url = https_url(page.get("url"))
        if all(p["url"] != url for p in out["pages"]):
            out["pages"].append({"name": (str(page.get("name") or "").strip() or urlsplit(url).hostname or "サイト")[:60], "url": url})
    return out


def _migrate_own_repo(cfg: dict) -> bool:
    """保存済みの設定から自分のリポジトリを 1 回だけ外す（印は migrated）。外す・印を付けたら True（呼び出し側が書き戻す）。"""
    if MIGRATION_OWN_REPO in cfg["migrated"]:
        return False
    cfg["github"] = [r for r in cfg["github"] if not r.lower().startswith(HIDDEN_REPO_OWNERS)]
    cfg["migrated"] = [*cfg["migrated"], MIGRATION_OWN_REPO]
    return True


def load_config() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        cfg = normalize(raw)
    except (OSError, ValueError):   # 無い・壊れているときは既定（ConfigError も ValueError）
        return normalize(DEFAULT_CONFIG)
    if _migrate_own_repo(cfg):
        try:
            save_config(cfg)
        except OSError:
            logger.warning("ニュースのソースの設定を書き戻せませんでした")
    return cfg


def save_config(raw: object) -> dict:
    cfg = normalize(raw)
    if MIGRATION_OWN_REPO not in cfg["migrated"]:      # 利用者が保存した後は、足した自分のリポジトリを勝手に外さない
        cfg["migrated"] = [*cfg["migrated"], MIGRATION_OWN_REPO]
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return cfg


# ---- 整形 ----

_TAG = re.compile(r"<[^>]+>")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def plain(text: object, limit: int = SUMMARY_CHARS) -> str:
    """HTML・Markdown の飾りを落とした平文（長さで切る）。"""
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", str(text or ""), flags=re.S | re.I)
    value = _TAG.sub(" ", value)
    value = html.unescape(value)
    value = _MD_LINK.sub(r"\1", value)
    value = re.sub(r"[`*#>]+", "", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def parse_time(value: object) -> datetime | None:
    """ISO 8601・RFC 822・epoch 秒のどれかを aware な UTC にする。読めなければ None。"""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc)
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(text)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def make_item(*, kind: str, source: str, source_key: str, title: object, url: object, summary: object = "",
              image: object = None, published: object = None, topics: tuple[str, ...] = ()) -> dict | None:
    """取得した 1 件を、保存する形にそろえる。url が http/https でない・題が空なら None（捨てる）。"""
    link = safe_url(url)
    name = plain(title, TITLE_CHARS)
    if not link or not name:
        return None
    when = parse_time(published) or datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    return {"kind": kind, "source": source[:120], "source_key": source_key[:200], "title": name, "url": link,
            "summary": plain(summary), "image_url": safe_url(image), "published_at": min(when, now + timedelta(minutes=5)),
            "extra_topics": topics}


# ---- RSS / Atom ----

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


_IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
_IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|avif)(?:\?|$)", re.I)


def parse_feed(raw: bytes, *, url: str, name: str) -> list[dict]:
    """RSS 2.0・Atom を記事の一覧にする。画像は media:content・media:thumbnail・enclosure・本文の最初の <img>。"""
    root = ET.fromstring(raw)
    entries = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
    out = []
    for entry in entries[:MAX_PER_SOURCE]:
        fields: dict[str, str] = {}
        image = ""
        for child in entry:
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "link" and not text:
                if child.get("rel") in (None, "alternate"):
                    fields.setdefault("link", child.get("href", ""))
                elif child.get("rel") == "enclosure" and (child.get("type", "").startswith("image/") or _IMAGE_EXT.search(child.get("href", ""))):
                    image = image or child.get("href", "")
            elif tag in ("thumbnail", "content") and child.get("url") and (tag == "thumbnail" or child.get("medium") == "image"
                                                                          or child.get("type", "").startswith("image/") or _IMAGE_EXT.search(child.get("url", ""))):
                image = image or child.get("url", "")
            elif tag == "enclosure" and child.get("url") and (child.get("type", "").startswith("image/") or _IMAGE_EXT.search(child.get("url", ""))):
                image = image or child.get("url", "")
            elif tag in ("description", "summary", "encoded", "content") and text:
                fields.setdefault("summary" if tag in ("description", "summary") else "body", text)
            elif tag in ("title", "link", "pubdate", "published", "updated", "date") and text:
                fields.setdefault(tag, text)
        if not image:
            found = _IMG_SRC.search(fields.get("body", "") + fields.get("summary", ""))
            image = found.group(1) if found else ""
        item = make_item(kind="news", source=name, source_key=f"rss:{url}", title=fields.get("title"),
                         url=urljoin(url, fields.get("link", "")), summary=fields.get("summary") or fields.get("body"),
                         image=urljoin(url, html.unescape(image)) if image else None,
                         published=fields.get("pubdate") or fields.get("published") or fields.get("updated") or fields.get("date"))
        if item:
            out.append(item)
    return out


async def fetch_rss(feed: dict) -> list[dict]:
    from app.knowledge.sources import http_get

    raw = await asyncio.to_thread(http_get, feed["url"], pace=1.0, timeout=25)
    try:
        return parse_feed(raw, url=feed["url"], name=feed["name"])
    except ET.ParseError as exc:
        raise RuntimeError(f"フィードを読めません: {exc}") from exc


async def fetch_page(page: dict) -> list[dict]:
    """RSS が無いサイト: 記事の一覧のページを巡回する（robots.txt を守る・同じサイトだけ・件数と頻度の上限。news/probe.py）。"""
    from app.news import probe

    try:
        return await probe.crawl_list(page["url"], page["name"])
    except probe.ProbeError as exc:
        raise RuntimeError(exc.message) from exc


# ---- GitHub・Codeberg の Release ----

def release_items(rows: object, *, host: str, repo: str) -> list[dict]:
    """GitHub・Codeberg（Gitea）の Release の一覧を記事にする。下書きは除く。話題の語はリポジトリ名。"""
    out = []
    label = f"{host} {repo}"
    for row in (rows if isinstance(rows, list) else [])[:MAX_PER_SOURCE]:
        if not isinstance(row, dict) or row.get("draft"):
            continue
        name = str(row.get("name") or row.get("tag_name") or "")
        tag = str(row.get("tag_name") or "")
        title = f"{repo.split('/')[-1]} {tag}" if not name or name == tag else f"{repo.split('/')[-1]} {name}"
        item = make_item(kind="release", source=label, source_key=f"{host.lower()}:{repo}", title=title + (" (pre-release)" if row.get("prerelease") else ""),
                         url=row.get("html_url"), summary=row.get("body"),
                         published=row.get("published_at") or row.get("created_at") or row.get("created"),
                         topics=(repo.split("/")[-1],))
        if item:
            out.append(item)
    return out


def github_atom_items(raw: bytes, repo: str) -> list[dict]:
    """GitHub の releases.atom（API の上限に当たったときの代わり。認証なしの API は 1 時間 60 回まで）を Release の記事にする。"""
    out = []
    for item in parse_feed(raw, url=f"https://github.com/{repo}/releases.atom", name=f"GitHub {repo}"):
        item.update(kind="release", source=f"GitHub {repo}", source_key=f"github:{repo}", image_url=None, extra_topics=(repo.split("/")[-1],))
        out.append(item)
    return out


async def fetch_github(repo: str) -> list[dict]:
    from app.knowledge.sources import http_get
    from app.workflows import research_tools as rt

    try:
        rows = await rt._get_json(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 10},
                                  headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    except rt.HttpError as exc:
        if exc.status not in (403, 429):      # 上限（認証なしは 1 時間 60 回）なら Atom で取る。ほかの失敗はそのまま
            raise
        raw = await asyncio.to_thread(http_get, f"https://github.com/{repo}/releases.atom", pace=1.0, timeout=25)
        try:
            return github_atom_items(raw, repo)
        except ET.ParseError as exc2:
            raise RuntimeError(f"フィードを読めません: {exc2}") from exc2
    return release_items(rows, host="GitHub", repo=repo)


async def fetch_codeberg(repo: str) -> list[dict]:
    from app.workflows import research_tools as rt

    try:
        rows = await rt._get_json(f"https://codeberg.org/api/v1/repos/{repo}/releases", params={"limit": 10})
    except rt.HttpError as exc:
        # Release の機能を切ったリポジトリ（例 StillDeadcode/radiance）は /releases が 404。タグだけを出しているので、タグを Release として読む
        if exc.status != 404:
            raise
        tags = await rt._get_json(f"https://codeberg.org/api/v1/repos/{repo}/tags", params={"limit": 10})
        rows = codeberg_tags_as_releases(tags, repo)
    return release_items(rows, host="Codeberg", repo=repo)


def codeberg_tags_as_releases(tags: object, repo: str) -> list[dict]:
    """Codeberg（Gitea）のタグの一覧を、release_items が読める形へ。日時はタグのコミットの日時、本文はタグの文。"""
    out = []
    for tag in tags if isinstance(tags, list) else []:
        if not isinstance(tag, dict) or not tag.get("name"):
            continue
        commit = tag.get("commit") if isinstance(tag.get("commit"), dict) else {}
        out.append({"tag_name": tag["name"], "name": tag["name"], "body": str(tag.get("message") or ""),
                    "html_url": f"https://codeberg.org/{repo}/src/tag/{tag['name']}", "created_at": commit.get("created")})
    return out


# ---- Hacker News・Hugging Face・Discourse・Reddit ----

def hn_items(data: object) -> list[dict]:
    out = []
    for hit in (data.get("hits") if isinstance(data, dict) else None) or []:
        if not isinstance(hit, dict) or not hit.get("objectID") or not hit.get("title"):
            continue
        discuss = f"https://news.ycombinator.com/item?id={hit['objectID']}"
        item = make_item(kind="news", source="Hacker News", source_key="hn", title=hit.get("title"), url=hit.get("url") or discuss,
                         summary=f"{hit.get('points') or 0} 点 · {hit.get('num_comments') or 0} コメント",
                         published=hit.get("created_at_i") or hit.get("created_at"))
        if item:
            out.append(item)
    return out[:MAX_PER_SOURCE]


async def fetch_hn() -> list[dict]:
    from app.workflows import research_tools as rt

    return hn_items(await rt._get_json("https://hn.algolia.com/api/v1/search", params={"tags": "front_page", "hitsPerPage": MAX_PER_SOURCE}))


def hf_items(rows: list[dict]) -> list[dict]:
    """話題の GGUF。登録から 90 日以内のものだけ（古いモデルが話題でも「新着」には載せない）。"""
    out = []
    horizon = datetime.now(timezone.utc) - timedelta(days=90)
    for row in rows:
        created = parse_time(row.get("created"))
        if not row.get("id") or not created or created < horizon:
            continue
        size = f" · {row['params_b']}B" if row.get("params_b") else ""
        item = make_item(kind="model", source="Hugging Face", source_key="hf", title=row["id"], url=row.get("url"),
                         summary=f"話題の GGUF{size} · ダウンロード {row.get('downloads', 0):,} · いいね {row.get('likes', 0):,} · {row.get('pipeline') or ''}",
                         published=created, topics=(str(row["id"]).split("/", 1)[0],))
        if item:
            out.append(item)
    return out


async def fetch_hf() -> list[dict]:
    from app.models_mgmt import hf_search

    return hf_items(await hf_search.trending_gguf(MAX_PER_SOURCE))


def discourse_items(data: object, domain: str) -> list[dict]:
    topics = ((data.get("topic_list") or {}).get("topics") if isinstance(data, dict) else None) or []
    out = []
    for topic in topics[:MAX_PER_SOURCE]:
        if not isinstance(topic, dict) or not topic.get("id") or topic.get("pinned"):
            continue
        item = make_item(kind="forum", source=domain, source_key=f"discourse:{domain}", title=topic.get("title"),
                         url=f"https://{domain}/t/{quote(str(topic.get('slug') or 'topic'))}/{topic['id']}",
                         summary=topic.get("excerpt") or f"{max(int(topic.get('posts_count') or 1) - 1, 0)} 返信 · {topic.get('like_count') or 0} いいね",
                         image=topic.get("image_url"), published=topic.get("created_at"))
        if item:
            out.append(item)
    return out


async def fetch_discourse(domain: str) -> list[dict]:
    from app.workflows import research_tools as rt

    return discourse_items(await rt._get_json(f"https://{domain}/latest.json"), domain)


def reddit_items(data: object, sub: str) -> list[dict]:
    out = []
    for child in ((data.get("data") or {}).get("children") if isinstance(data, dict) else None) or []:
        post = child.get("data") if isinstance(child, dict) else None
        if not isinstance(post, dict) or not post.get("permalink") or post.get("over_18") or post.get("stickied"):
            continue
        previews = ((post.get("preview") or {}).get("images") or [{}])[0]
        thumb = post.get("thumbnail") if str(post.get("thumbnail") or "").startswith("http") else None
        item = make_item(kind="reddit", source=f"r/{sub}", source_key=f"reddit:{sub}", title=post.get("title"),
                         url="https://www.reddit.com" + post["permalink"],
                         summary=f"{post.get('score', 0)} 点 · {post.get('num_comments', 0)} コメント — {post.get('selftext') or ''}",
                         image=(previews.get("source") or {}).get("url") or thumb, published=post.get("created_utc"))
        if item:
            out.append(item)
    return out[:MAX_PER_SOURCE]


class SkippedSource(Exception):
    """取得しない理由がある（失敗ではない）。画面には注記として出す。"""


async def fetch_reddit(sub: str) -> list[dict]:
    from app.workflows import research_tools as rt

    data = await rt._reddit_oauth(f"/r/{quote(sub)}/hot", {"limit": MAX_PER_SOURCE})
    if data is None:
        raise SkippedSource("Reddit のキーが未設定のため取得しません（設定 → API キー）")
    return reddit_items(data, sub)


# ---- チャンネルの語での検索（Google ニュースの RSS・HN・Discourse・Reddit） ----

def _search_key(channel: dict, engine: str) -> str:
    return f"search:{channel['id']}:{engine}"


def gnews_items(raw: bytes, *, url: str, channel: dict, lang: str) -> list[dict]:
    """Google ニュースの検索の RSS を記事にする。題は「見出し - 配信元」なので配信元を出どころへ分ける。
    link は Google の転送の URL のことがある（そのまま http/https として扱う）。抜粋は見出しの重複になるので持たない。"""
    out = []
    for item in parse_feed(raw, url=url, name="Google ニュース"):
        title, _, publisher = item["title"].rpartition(" - ")
        if title and publisher and len(publisher) <= 60:
            item["title"], item["source"] = title.strip(), publisher.strip()
        item["source_key"] = _search_key(channel, f"gnews-{lang}")
        item["summary"] = ""
        item["image_url"] = None       # 画像は Google の縮小版で小さく、配信元の画像ではない
        out.append(item)
    return out


async def fetch_gnews(channel: dict, lang: str) -> list[dict]:
    from app.knowledge.sources import http_get
    from app.news import channels as ch

    url = ch.gnews_url(channel, lang)
    raw = await asyncio.to_thread(http_get, url, pace=1.5, timeout=25)
    try:
        return gnews_items(raw, url=url, channel=channel, lang=lang)
    except ET.ParseError as exc:
        raise RuntimeError(f"フィードを読めません: {exc}") from exc


def tag_search(items: list[dict], channel: dict, engine: str) -> list[dict]:
    """検索の結果にチャンネルの印を付ける。Google ニュース以外（フォーラム・HN・Reddit）の検索は、見出しか抜粋に
    チャンネルの語が無いものを捨てる（本文の奥で一致しただけの無関係な投稿が入っていた。2026-11-23 本番で「特許」に
    Level1Techs の「Linux の癖」などが 30 件ずつ入った）。"""
    from app.news import channels as ch

    out = []
    for item in items:
        if not engine.startswith("gnews") and not ch.has_terms(f"{item.get('title') or ''}\n{item.get('summary') or ''}", channel["include"]):
            continue
        item["source_key"] = _search_key(channel, engine)
        out.append(item)
    return out


async def fetch_hn_search(channel: dict) -> list[dict]:
    from app.news import channels as ch
    from app.workflows import research_tools as rt

    data = await rt._get_json("https://hn.algolia.com/api/v1/search_by_date",
                              params={"query": " ".join(channel["include"][:ch.SEARCH_TERMS]), "tags": "story", "hitsPerPage": MAX_PER_SOURCE})
    return tag_search(hn_items(data), channel, "hn")


async def fetch_discourse_search(channel: dict, domain: str) -> list[dict]:
    from app.news import channels as ch
    from app.workflows import research_tools as rt

    data = await rt._get_json(f"https://{domain}/search.json", params={"q": f"{' '.join(channel['include'][:ch.SEARCH_TERMS])} order:latest_topic"})
    topics = (data.get("topics") if isinstance(data, dict) else None) or []
    return tag_search(discourse_items({"topic_list": {"topics": topics}}, domain), channel, f"discourse-{domain}")


async def fetch_reddit_search(channel: dict) -> list[dict]:
    from app.news import channels as ch
    from app.workflows import research_tools as rt

    data = await rt._reddit_oauth("/search", {"q": " OR ".join(channel["include"][:ch.SEARCH_TERMS]), "sort": "new", "limit": MAX_PER_SOURCE, "type": "link"})
    if data is None:
        raise SkippedSource("Reddit のキーが未設定のため検索しません（設定 → API キー）")
    return tag_search(reddit_items(data, "all"), channel, "reddit")


def search_sources(cfg: dict, channels: list[dict], *, reddit_key: bool = False) -> list["Source"]:
    """チャンネルの語で検索するソース。検索は channel.search が真で、含める語があるチャンネルだけ。"""
    from app.news import channels as ch

    on = cfg["enabled"]
    out: list[Source] = []
    for channel in channels:
        if not channel.get("search") or not channel["include"]:
            continue
        name = channel["name"]
        if on.get("news"):
            for lang in ch.langs(channel):
                out.append(Source(_search_key(channel, f"gnews-{lang}"), f"Google ニュース検索 {name}（{'日本語' if lang == 'ja' else '英語'}）",
                                  "news", lambda c=channel, l=lang: fetch_gnews(c, l)))
            if cfg.get("hackernews") and channel["lang"] != "ja":
                out.append(Source(_search_key(channel, "hn"), f"HN 検索 {name}", "news", lambda c=channel: fetch_hn_search(c)))
        if on.get("forum"):
            out += [Source(_search_key(channel, f"discourse-{d}"), f"{d} 検索 {name}", "forum", lambda c=channel, d=d: fetch_discourse_search(c, d))
                    for d in cfg["discourse"]]
        if on.get("reddit") and reddit_key and channel["lang"] != "ja":
            out.append(Source(_search_key(channel, "reddit"), f"Reddit 検索 {name}", "reddit", lambda c=channel: fetch_reddit_search(c)))
    return out


# ---- ソースの一覧 ----

@dataclass(frozen=True)
class Source:
    key: str
    label: str
    kind: str
    fetch: Callable[[], Awaitable[list[dict]]]
    interval: int = 0          # 分。0 は設定の既定


def build_sources(cfg: dict, channels: list[dict] | None = None) -> list[Source]:
    """設定から取得するソースの一覧。止めているソース（disabled）は含めない。間隔はソースごとの値（intervals）。"""
    from dataclasses import replace

    off, ivs = set(cfg.get("disabled", [])), cfg.get("intervals", {})
    sources = [replace(s, interval=ivs.get(s.key, 0)) for s in _configured_sources(cfg) if s.key not in off]
    if channels:
        from app.workflows import research_keys

        sources += search_sources(cfg, channels, reddit_key=research_keys.reddit() is not None)
    return sources


def _configured_sources(cfg: dict) -> list[Source]:
    on = cfg["enabled"]
    sources: list[Source] = []
    if on.get("release"):
        sources += [Source(f"github:{r}", f"GitHub {r}", "release", lambda r=r: fetch_github(r)) for r in cfg["github"]]
        sources += [Source(f"codeberg:{r}", f"Codeberg {r}", "release", lambda r=r: fetch_codeberg(r)) for r in cfg["codeberg"]]
    if on.get("news"):
        sources += [Source(f"rss:{f['url']}", f["name"], "news", lambda f=f: fetch_rss(f)) for f in cfg["rss"]]
        sources += [Source(f"page:{f['url']}", f"{f['name']}（一覧）", "news", lambda f=f: fetch_page(f)) for f in cfg.get("pages", [])]
        if cfg.get("hackernews"):
            sources.append(Source("hn", "Hacker News", "news", fetch_hn))
    if on.get("model") and cfg.get("huggingface"):
        sources.append(Source("hf", "Hugging Face", "model", fetch_hf))
    if on.get("forum"):
        sources += [Source(f"discourse:{d}", d, "forum", lambda d=d: fetch_discourse(d)) for d in cfg["discourse"]]
    if on.get("reddit"):
        sources += [Source(f"reddit:{s}", f"r/{s}", "reddit", lambda s=s: fetch_reddit(s)) for s in cfg["reddit"]]
    return sources
