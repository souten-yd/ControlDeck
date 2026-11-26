"""W3a: 同一 origin の発見と SQLite 永続キュー（通信なし）。

URL の整形は egress の安全検査の代わりではない。ネットワークの実行・robots/
Retry-After・Job/認可との統合は W3b。ユーザー入力から DB path を作らない。
"""
from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_DEPTH = 3
MAX_PAGES = 200
MAX_XML_CHARS = 1_000_000
MAX_URL_CHARS = 2048
KINDS = {"page", "map"}
TERMINAL = {"success", "empty", "blocked", "timeout", "security_rejected",
            "http_error", "too_large", "robots_denied", "captcha", "unsupported",
            "login_required", "cancelled"}


def _url(value: str) -> tuple[str, tuple[str, str, int]]:
    if (not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS
            or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("invalid URL")
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.hostname or p.username is not None or p.password is not None:
        raise ValueError("invalid URL")
    host = p.hostname.encode("idna").decode("ascii").lower()
    default = 443 if p.scheme == "https" else 80
    port = p.port if p.port is not None else default
    if port != default:
        raise ValueError("non-default port")
    netloc = f"[{host}]" if ":" in host else host
    normalized = urlunsplit((p.scheme, netloc, p.path or "/", p.query, ""))
    return normalized, (p.scheme, host, port)


def scoped_url(root: str, parent: str, link: str) -> str | None:
    """同一 origin のみ。外部 URL・認証情報・非 HTTP・別ポートは候補にも入れない。
    クエリは保持し、フラグメントだけ除く。DNS は通信直前に egress が検査する。
    """
    try:
        _, scope = _url(root)
        parent, parent_scope = _url(parent)
        if parent_scope != scope or not isinstance(link, str) or not link or link.startswith("#"):
            return None
        if "\\" in link or any(ord(c) <= 32 or ord(c) == 127 for c in link):
            return None
        target, origin = _url(urljoin(parent, link))
        return target if origin == scope else None
    except (ValueError, UnicodeError):
        return None


def discover_xml(root: str, parent: str, xml: str) -> list[tuple[str, str]]:
    """Sitemap index / urlset / RSS / Atom の公開リンク候補。DTD/ENTITY は拒否。
    取得した文字列だけを解析し、外部参照や XML の展開を行わない。
    """
    if len(xml) > MAX_XML_CHARS:
        raise ValueError("XML too large")
    upper = xml.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper or "\x00" in xml:
        raise ValueError("unsafe XML")
    tree = ET.fromstring(xml)
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
    kind = local(tree.tag)
    raw: list[tuple[str, str]] = []
    if kind in ("sitemapindex", "urlset"):
        child = "sitemap" if kind == "sitemapindex" else "url"
        for entry in tree:
            if local(entry.tag) == child:
                raw.extend(((loc.text or "").strip(), "map" if child == "sitemap" else "page")
                           for loc in entry if local(loc.tag) == "loc")
    elif kind == "rss":
        for channel in tree:
            if local(channel.tag) == "channel":
                for item in channel:
                    if local(item.tag) == "item":
                        raw.extend(((n.text or "").strip(), "page") for n in item if local(n.tag) == "link")
    elif kind == "feed":
        for item in tree:
            if local(item.tag) == "entry":
                raw.extend((n.attrib.get("href", ""), "page") for n in item
                           if local(n.tag) == "link" and n.attrib.get("rel", "alternate") == "alternate")
    else:
        raise ValueError("unsupported discovery XML")
    out: dict[tuple[str, str], None] = {}
    for href, target_kind in raw:
        url = scoped_url(root, parent, href)
        if url:
            out[(url, target_kind)] = None
        if len(out) >= MAX_PAGES:
            break
    return list(out)


def discover_links(root: str, parent: str, links: Iterable[dict]) -> list[tuple[str, str]]:
    out: dict[tuple[str, str], None] = {}
    for entry in links:
        url = scoped_url(root, parent, entry.get("url", "")) if isinstance(entry, dict) else None
        if url:
            out[(url, "page")] = None
        if len(out) >= MAX_PAGES:
            break
    return list(out)


class CrawlQueue:
    """DB は呼び出し側の管理領域に置く。1 job につき最大200候補・深さ3。
    claim はトランザクションで1件だけ確保。recover は停止済みの単一 worker 専用。
    """
    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS crawl_jobs (
                    id TEXT PRIMARY KEY, root TEXT NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS crawl_queue (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, url TEXT NOT NULL,
                    kind TEXT NOT NULL, depth INTEGER NOT NULL, status TEXT NOT NULL,
                    UNIQUE(job_id, url, kind),
                    FOREIGN KEY(job_id) REFERENCES crawl_jobs(id)
                );
                CREATE INDEX IF NOT EXISTS crawl_pending ON crawl_queue(job_id, status, depth, id);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _job(db: sqlite3.Connection, job_id: str):
        job = db.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise ValueError("unknown crawl job")
        return job

    def create(self, job_id: str, root: str, *, kind: str = "page") -> None:
        root, _ = _url(root)
        if not job_id or len(job_id) > 128 or kind not in KINDS:
            raise ValueError("invalid crawl job")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO crawl_jobs VALUES (?, ?, 'active')", (job_id, root))
            db.execute("INSERT INTO crawl_queue(job_id,url,kind,depth,status) VALUES (?,?,?,0,'queued')",
                       (job_id, root, kind))

    def enqueue(self, job_id: str, parent_id: int, candidates: Iterable[tuple[str, str]]) -> int:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, job_id)
            parent = db.execute("SELECT * FROM crawl_queue WHERE job_id=? AND id=?", (job_id, parent_id)).fetchone()
            if parent is None or parent["status"] not in ("running", "success"):
                raise ValueError("invalid discovery parent")
            if job["state"] == "cancelled" or parent["depth"] >= MAX_DEPTH:
                return 0
            room = MAX_PAGES - db.execute("SELECT count(*) FROM crawl_queue WHERE job_id=?", (job_id,)).fetchone()[0]
            count = 0
            for link, kind in candidates:
                if count >= room:
                    break
                url = scoped_url(job["root"], parent["url"], link)
                if not url or kind not in KINDS:
                    continue
                cur = db.execute("INSERT OR IGNORE INTO crawl_queue(job_id,url,kind,depth,status) VALUES (?,?,?,?,'queued')",
                                 (job_id, url, kind, parent["depth"] + 1))
                count += cur.rowcount
            return count

    def claim(self, job_id: str) -> dict | None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self._job(db, job_id)["state"] != "active":
                return None
            row = db.execute("SELECT * FROM crawl_queue WHERE job_id=? AND status='queued' ORDER BY depth,id LIMIT 1",
                             (job_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE crawl_queue SET status='running' WHERE id=?", (row["id"],))
            return {**dict(row), "status": "running"}

    def finish(self, job_id: str, item_id: int, status: str) -> None:
        if status not in TERMINAL:
            raise ValueError("invalid terminal status")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, job_id)
            if job["state"] == "cancelled":
                status = "cancelled"
            result = db.execute("UPDATE crawl_queue SET status=? WHERE job_id=? AND id=? AND status='running'",
                                (status, job_id, item_id))
            if result.rowcount != 1:
                raise ValueError("item not running")

    def state(self, job_id: str, action: str) -> None:
        target = {"pause": "paused", "resume": "active", "cancel": "cancelled"}.get(action)
        if target is None:
            raise ValueError("invalid action")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self._job(db, job_id)["state"] == "cancelled":
                raise ValueError("job cancelled")
            db.execute("UPDATE crawl_jobs SET state=? WHERE id=?", (target, job_id))
            if target == "cancelled":
                db.execute("UPDATE crawl_queue SET status='cancelled' WHERE job_id=? AND status='queued'", (job_id,))

    def recover(self, job_id: str) -> int:
        """前 worker が完全に停止したことを実行側が確認してから呼ぶ。"""
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, job_id)
            status = "cancelled" if job["state"] == "cancelled" else "queued"
            return db.execute("UPDATE crawl_queue SET status=? WHERE job_id=? AND status='running'",
                              (status, job_id)).rowcount

    def counts(self, job_id: str) -> dict[str, int]:
        with closing(self._connect()) as db:
            self._job(db, job_id)
            return dict(db.execute("SELECT status,count(*) FROM crawl_queue WHERE job_id=? GROUP BY status",
                                   (job_id,)).fetchall())
