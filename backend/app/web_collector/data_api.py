"""公開データ API の直接利用（fetch_method="data_api"）。

HTML を読むより、そのサイトが公開している API で取る方が正確で負荷も小さい（GitHub の README は Trafilatura が見出しを拾えない・
OpenAlex の画面はブラウザを 403 で断り「API を使って」と案内する）。
`fetch.collect` の前段で、URL の形から次の 2 つを判定して API へ置き換える。

- GitHub: リポジトリ（README＋説明・スター・更新日）・blob（raw の内容）・issue / PR（本文＋コメントの上位）
- OpenAlex: works / authors（題・著者・年・抄録の再構成・引用数・DOI）

実測（2026-11-26・~/ControlDeck-評価/2026-11-26-公開データAPIの直接利用.md）で、Wikipedia の REST は本文が大きく減り（en 30,753 → 13,574 字・
ja 4,498 → 1,626 字・infobox も落ちる）、arXiv の Atom は今の HTML の取得と同程度だったので、どちらも API に置き換えない（HTML のまま）。

守ること: 通信は全部 `egress.guarded_client`（検査済み IP・ポート 80/443）。API の鍵は使わない（無認証の範囲）。UA は fetch.UA（詐称しない）。
リダイレクトは同じ許可リストのホストの中だけ・最大 3 段。応答の大きさに上限。403/429 は止めて理由を返す（別経路で取り直さない）。
404 や形が合わないときは None 相当（呼び出し側が通常の HTML の取得へ戻る）。秘密の会話は egress の接続の直前で止まる。
`github_document` は Deep Research の `research_tools.read_github` も使う（二重に作らない。通信は `get` で差し替える）。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from app.web_collector import egress

logger = logging.getLogger(__name__)

JSON_MAX_BYTES = 4 * 1024 * 1024
TEXT_MAX_BYTES = 1024 * 1024          # README・blob
MAX_REDIRECTS = 3
COMMENTS_MAX = 20
COMMENT_CHARS = 1500
BODY_CHARS = 20000
AUTHORS_MAX = 30
TOP_WORKS = 10
_GH_RESERVED = frozenset({
    "orgs", "settings", "search", "topics", "marketplace", "sponsors", "features", "pricing", "login", "join", "about",
    "explore", "notifications", "pulls", "issues", "collections", "trending", "enterprise", "apps", "users", "organizations",
    "site", "security", "readme", "customer-stories", "events", "codespaces", "new", "dashboard", "account", "contact"})
_GH = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/(.*))?$")
_OPENALEX = re.compile(r"^/(?:(works|authors)/)?([WA]\d{3,12})$", re.I)


class DataApiError(RuntimeError):
    """`stop` が True なら別経路で取り直さない（403/429・内部アドレス）。False なら通常の HTML の取得へ戻ってよい。"""

    def __init__(self, message: str, *, status: str = "http_error", http_status: int = 0, stop: bool = False):
        super().__init__(message)
        self.status, self.http_status, self.stop = status, http_status, stop


@dataclass
class Resp:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)
    truncated: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        import json

        if self.truncated:
            raise DataApiError("応答が大きすぎます", status="too_large")
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise DataApiError(f"JSON を読めません: {exc}"[:200]) from exc


@dataclass
class Doc:
    title: str
    text: str
    date: str = ""
    links: list[dict] = field(default_factory=list)
    kind: str = ""
    language: str = ""
    warnings: list[str] = field(default_factory=list)


# ---- 判定 ----

def match(url: str) -> tuple[str, dict] | None:
    """URL の形から (種類, 部品)。API に置き換えられないものは None。"""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = unquote(parts.path).rstrip("/") or "/"
    if parts.scheme not in ("http", "https") or (parts.port not in (None, 80, 443)):
        return None
    if host == "github.com":
        m = _GH.match(path)
        if not m or m.group(1).lower() in _GH_RESERVED:
            return None
        owner, repo, rest = m.group(1), m.group(2).removesuffix(".git"), m.group(3) or ""
        if not repo or not (rest == "" or re.fullmatch(r"tree/[^/]+", rest) or re.fullmatch(r"(?:issues|pull)/\d+", rest)
                            or re.fullmatch(r"blob/[^/]+/.+", rest)):
            return None
        return "github", {"owner": owner, "repo": repo, "rest": rest}
    if host == "openalex.org":
        m = _OPENALEX.match(path)
        if m:
            ident = m.group(2).upper()
            return ("openalex_work" if ident[0] == "W" else "openalex_author"), {"id": ident}
    return None


# ---- 通信（出口は egress。許可リストのホストの中だけ・鍵なし） ----

def _allowed_host(host: str) -> bool:
    host = host.lower()
    return host in {"api.github.com", "raw.githubusercontent.com", "api.openalex.org"}


async def http_get(url: str, headers: dict, max_bytes: int, *, policy: egress.Policy = egress.DEFAULT_POLICY,
                   timeout: float = 25) -> Resp:
    from app.web_collector.fetch import UA

    current = url
    try:
        async with egress.guarded_client(timeout=timeout, policy=policy, headers={"User-Agent": UA, **headers}) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                parts = urlsplit(current)
                if parts.scheme != "https" or not _allowed_host(parts.hostname or ""):
                    raise DataApiError(f"API の許可リスト外へのリダイレクト: {parts.hostname}", status="security_rejected", stop=True)
                async with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    chunks, size, truncated = [], 0, False
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            chunks.append(chunk[: max(0, len(chunk) - (size - max_bytes))])
                            truncated = True
                            break
                        chunks.append(chunk)
                    return Resp(response.status_code, b"".join(chunks), {k.lower(): v for k, v in response.headers.items()}, truncated)
            raise DataApiError("リダイレクトが多すぎます")
    except egress.EgressRejected as exc:
        raise DataApiError(str(exc), status="security_rejected", stop=True) from exc
    except httpx.TimeoutException as exc:
        raise DataApiError(f"{timeout} 秒で応答がありません", status="timeout") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise DataApiError(f"{type(exc).__name__}: {exc}"[:300]) from exc


def _check(resp: Resp, what: str) -> Resp:
    """403/429 は止める（無認証の上限・拒否）。それ以外の 4xx/5xx は戻ってよい。"""
    if resp.status in (403, 429):
        extra = ""
        if resp.headers.get("x-ratelimit-remaining") is not None:
            extra = f"（残り {resp.headers.get('x-ratelimit-remaining')}・解除 {resp.headers.get('x-ratelimit-reset', '不明')}。無認証は 1 時間 60 回）"
        elif resp.headers.get("retry-after"):
            extra = f"（{resp.headers['retry-after']} 秒後に再試行）"
        raise DataApiError(f"{what} の API が断りました HTTP {resp.status}{extra}。回避しません", status="blocked",
                           http_status=resp.status, stop=True)
    if resp.status >= 400:
        raise DataApiError(f"{what} HTTP {resp.status}", http_status=resp.status)
    return resp


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def md_links(text: str, base: str) -> list[dict]:
    return [{"text": m.group(1), "url": urljoin(base, m.group(2))}
            for m in re.finditer(r"\[([^\]]{1,100})\]\(([^)\s]+)\)", text)][:500]


# ---- GitHub ----

_GH_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


async def github_document(owner: str, repo: str, rest: str, get) -> Doc:
    """`get(url, headers, max_bytes) -> Resp`。リポジトリの先頭・issue/PR・blob。形が合わなければ DataApiError。"""
    api = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
    if rest == "" or re.fullmatch(r"tree/[^/]+", rest):
        meta_r, readme_r = await asyncio.gather(
            get(api, _GH_HEADERS, JSON_MAX_BYTES),
            get(f"{api}/readme", {**_GH_HEADERS, "Accept": "application/vnd.github.raw+json"}, TEXT_MAX_BYTES))
        meta = _check(meta_r, "GitHub").json()
        readme = _check(readme_r, "GitHub README")
        text = readme.text
        base = f"https://github.com/{owner}/{repo}/blob/{meta.get('default_branch', 'HEAD')}/"
        head = (f"{meta.get('full_name')} — {_clip(meta.get('description'), 200)}\n★{meta.get('stargazers_count', 0)} · "
                f"{meta.get('language') or ''} · 更新 {str(meta.get('pushed_at') or '')[:10]}\n\n")
        warn = ["README が大きいので先頭だけです"] if readme.truncated else []
        return Doc(f"{owner}/{repo} README", head + text, str(meta.get("pushed_at") or "")[:10], md_links(text, base), "github_repo",
                   warnings=warn)
    issue = re.fullmatch(r"(?:issues|pull)/(\d+)", rest)
    if issue:
        number = issue.group(1)
        item_r, comments_r = await asyncio.gather(get(f"{api}/issues/{number}", _GH_HEADERS, JSON_MAX_BYTES),
                                                  get(f"{api}/issues/{number}/comments?per_page=50", _GH_HEADERS, JSON_MAX_BYTES))
        item = _check(item_r, "GitHub issue").json()
        comments = _check(comments_r, "GitHub コメント").json()
        if not isinstance(comments, list):
            comments = []
        top = sorted(comments, key=lambda c: -int((c.get("reactions") or {}).get("total_count") or 0))[:COMMENTS_MAX]
        lines = [f"- ({(c.get('reactions') or {}).get('total_count', 0)}反応・{(c.get('user') or {}).get('login', '')}) "
                 f"{_clip(c.get('body'), COMMENT_CHARS)}" for c in top]
        total = int(item.get("comments") or len(comments))
        labels = ", ".join(label.get("name", "") for label in item.get("labels") or [])
        text = (f"{item.get('state')} · {total}コメント · ラベル: {labels}\n\n{_clip(item.get('body'), BODY_CHARS)}\n\n"
                f"## コメント（反応の多い順に上位 {len(lines)} 件／全 {total} 件）\n" + "\n".join(lines))
        kind = "github_pull" if "pull_request" in item else "github_issue"
        return Doc(f"{owner}/{repo}#{number} {item.get('title', '')}", text, str(item.get("updated_at") or "")[:10],
                   md_links(text, f"https://github.com/{owner}/{repo}/"), kind)
    blob = re.fullmatch(r"blob/([^/]+)/(.+)", rest)
    if blob:
        ref, path = blob.group(1), blob.group(2)
        raw = _check(await get(f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/{quote(ref)}/{quote(path)}",
                               {"Accept": "text/plain"}, TEXT_MAX_BYTES), "GitHub ファイル")
        if b"\x00" in raw.body[:8192]:
            raise DataApiError("バイナリのファイルです", status="unsupported")
        return Doc(f"{owner}/{repo} {path}", raw.text, "", md_links(raw.text, f"https://github.com/{owner}/{repo}/blob/{ref}/"),
                   "github_blob", warnings=["ファイルが大きいので先頭 1 MB だけです"] if raw.truncated else [])
    raise DataApiError("GitHub の API に置き換えられない形です")



def abstract_from_index(index: dict | None) -> str:
    """abstract_inverted_index（語 → 位置の並び）から抄録を戻す。"""
    if not isinstance(index, dict):
        return ""
    slots: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions if isinstance(positions, list) else []:
            if isinstance(pos, int) and 0 <= pos < 100000:
                slots[pos] = str(word)
    return " ".join(slots[i] for i in sorted(slots))


def _oa_short(identifier: str | None) -> str:
    return (identifier or "").rsplit("/", 1)[-1]


def openalex_work_text(w: dict) -> Doc:
    title = w.get("display_name") or w.get("title") or ""
    authors = [(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []]
    authors = [a for a in authors if a]
    shown = "、".join(authors[:AUTHORS_MAX]) + (f" ほか（全 {len(authors)} 名）" if len(authors) > AUTHORS_MAX else "")
    venue = (((w.get("primary_location") or {}).get("source")) or {}).get("display_name") or ""
    topics = "、".join(t.get("display_name", "") for t in (w.get("topics") or [])[:5] if t.get("display_name"))
    oa = (w.get("open_access") or {}).get("oa_url") or ""
    lines = [f"# {title}", ""]
    for label, value in (("著者", shown), ("年", w.get("publication_year") or ""), ("発行日", w.get("publication_date") or ""),
                         ("掲載", venue), ("種類", w.get("type") or ""), ("DOI", w.get("doi") or ""),
                         ("被引用数", w.get("cited_by_count", 0)), ("参考文献数", w.get("referenced_works_count") or 0),
                         ("主題", topics), ("OA の本文", oa), ("OpenAlex", w.get("id") or "")):
        if value not in ("", None):
            lines.append(f"- {label}: {value}")
    abstract = abstract_from_index(w.get("abstract_inverted_index"))
    lines += ["", "## 抄録", "", abstract or "（抄録は OpenAlex にありません）"]
    links = [{"url": u, "text": t} for t, u in (("DOI", w.get("doi")), ("OA の本文", oa)) if u]
    return Doc(title, "\n".join(lines), str(w.get("publication_date") or ""), links, "openalex_work",
               language=str(w.get("language") or ""))


def openalex_author_text(a: dict, works: list[dict]) -> Doc:
    name = a.get("display_name") or ""
    stats = a.get("summary_stats") or {}
    inst = "、".join(i.get("display_name", "") for i in (a.get("last_known_institutions") or [])[:3] if i.get("display_name"))
    topics = "、".join(t.get("display_name", "") for t in (a.get("topics") or [])[:8] if t.get("display_name"))
    lines = [f"# {name}", ""]
    for label, value in (("所属", inst), ("論文数", a.get("works_count")), ("被引用数", a.get("cited_by_count")),
                         ("h指数", stats.get("h_index")), ("ORCID", a.get("orcid") or ""), ("主題", topics), ("OpenAlex", a.get("id") or "")):
        if value not in ("", None):
            lines.append(f"- {label}: {value}")
    if works:
        lines += ["", f"## 被引用数の多い論文（上位 {len(works)} 件）", ""]
        for w in works:
            lines.append(f"- {w.get('publication_year') or ''} {w.get('display_name') or ''}（被引用 {w.get('cited_by_count', 0)}）{w.get('doi') or ''}")
    return Doc(name, "\n".join(lines), "", [], "openalex_author")


async def openalex_document(kind: str, ident: str, get) -> Doc:
    if kind == "openalex_work":
        data = _check(await get(f"https://api.openalex.org/works/{quote(ident)}", {"Accept": "application/json"}, JSON_MAX_BYTES), "OpenAlex").json()
        return openalex_work_text(data)
    author = _check(await get(f"https://api.openalex.org/authors/{quote(ident)}", {"Accept": "application/json"}, JSON_MAX_BYTES), "OpenAlex").json()
    works: list[dict] = []
    try:   # 代表作は付け足し。取れなくても著者の情報は返す（ただし 403/429 は止める）
        url = (f"https://api.openalex.org/works?filter=author.id:{quote(_oa_short(author.get('id')) or ident)}"
               f"&sort=cited_by_count:desc&per_page={TOP_WORKS}&select=display_name,publication_year,cited_by_count,doi")
        works = _check(await get(url, {"Accept": "application/json"}, JSON_MAX_BYTES), "OpenAlex").json().get("results") or []
    except DataApiError as exc:
        if exc.stop:
            raise
    return openalex_author_text(author, works)



async def fetch(url: str, *, policy: egress.Policy = egress.DEFAULT_POLICY, timeout: float = 25, limit_chars: int = 2_000_000,
                get=None) -> dict | None:
    """URL が対象でなければ None。対象なら {"status": "success" | "blocked" | ... | "fallback", ...}。
    "fallback" は API で取れなかった（404・形が合わない・通信の失敗）ので、呼び出し側が通常の HTML の取得へ戻る。"""
    found = match(url)
    if found is None:
        return None
    kind, part = found
    if get is None:
        async def get(u, headers, max_bytes):  # noqa: E306
            return await http_get(u, headers, max_bytes, policy=policy, timeout=timeout)
    try:
        if kind == "github":
            doc = await github_document(part["owner"], part["repo"], part["rest"], get)
        else:
            doc = await openalex_document(kind, part["id"], get)
    except DataApiError as exc:
        if exc.stop:
            logger.info("data_api %s: %s", kind, exc)
            return {"status": exc.status, "error": str(exc), "http_status": exc.http_status, "kind": kind}
        logger.info("data_api %s は使えません（HTML の取得へ戻します）: %s", kind, exc)
        return {"status": "fallback", "error": str(exc), "kind": kind}
    text = doc.text.strip()
    if not text:
        return {"status": "fallback", "error": "API の本文が空です", "kind": kind}
    return {"status": "success", "kind": kind, "title": doc.title, "markdown": text[:limit_chars], "links": doc.links,
            "language": doc.language, "warnings": doc.warnings, "metadata": {"title": doc.title, "date": doc.date, "api": doc.kind},
            "extractor": f"data_api:{doc.kind}", "http_status": 200}
