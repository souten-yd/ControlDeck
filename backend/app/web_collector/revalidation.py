"""W5c: conservative, request-local HTTP revalidation policy.

Not a shared cache, URL normalizer, authorization or transport. The caller
owns the instance and discards it at the end of the authorized operation.
No disk storage, stale fallback, network or cookies.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from email.utils import format_datetime, parsedate_to_datetime
import copy
import json
import re
import time
from urllib.parse import urlsplit

MAX_ENTRIES = 16
MAX_BYTES = 8 * 1024 * 1024
MAX_AGE = 600
_ETAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e]*"')
_SENSITIVE = re.compile(r"(?:^|[-_])(?:token|key|signature|sig|auth|password|session|credential|jwt)(?:$|[-_])", re.I)


def eligible_url(url: str) -> bool:
    # Deliberately exclude ALL query strings, fragments and cleartext HTTP.
    # Validators can be user-specific; never normalize a cache identity.
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and bool(p.hostname) and p.username is None
                and p.password is None and not p.query and not p.fragment
                and p.port in (None, 443) and "?" not in url and "#" not in url
                and "\\" not in url and not any(ord(c) <= 32 or ord(c) == 127 for c in url)
                and not any(_SENSITIVE.search(part) for part in p.path.split("/")))
    except (ValueError, TypeError):
        return False


def response_allowed(headers) -> bool:
    # Explicit opt-in from origin; no Vary (even Accept-Encoding), cookies,
    # authentication challenge, partial representation, no-store or private.
    cc = headers.get("cache-control", "").casefold()
    if not cc or len(cc) > 1024 or '"' in cc or "\\" in cc:
        return False
    parts = {x.strip() for x in cc.split(",")}
    directives = {x.split("=", 1)[0].strip() for x in parts}
    return ("public" in parts and not directives & {"private", "no-store"}
            and "vary" not in headers and "set-cookie" not in headers
            and "www-authenticate" not in headers and "proxy-authenticate" not in headers
            and "content-range" not in headers)


def validators(headers) -> dict:
    out = {}
    etag = headers.get("etag", "")
    if len(etag) <= 512 and _ETAG.fullmatch(etag):
        out["If-None-Match"] = etag
    lm = headers.get("last-modified", "")
    if len(lm) <= 80 and "\r" not in lm and "\n" not in lm:
        try:
            parsed = parsedate_to_datetime(lm)
            if format_datetime(parsed, usegmt=True) == lm:
                out["If-Modified-Since"] = lm
        except (ValueError, TypeError, OverflowError):
            pass
    return out


@dataclass
class _Entry:
    page: dict
    headers: dict
    expires: float
    size: int


class RevalidationCache:
    """One authorized research operation, never global or persistent."""
    def __init__(self):
        self._items = OrderedDict()
        self._size = 0
        self._epoch = 0

    def drop(self, key):
        old = self._items.pop(key, None)
        if old:
            self._size -= old.size

    def get(self, key):
        old = self._items.get(key)
        if old and old.expires > time.monotonic():
            self._items.move_to_end(key)
            return copy.deepcopy(old)
        self.drop(key)
        return None

    def take(self, key):
        # Global generation is deliberately conservative under concurrent use:
        # only the newest request may populate/restore an entry.
        self._epoch += 1
        entry = self.get(key)
        self.drop(key)
        return entry, self._epoch

    def _insert(self, key, entry):
        self.drop(key)
        while self._items and (len(self._items) >= MAX_ENTRIES or self._size + entry.size > MAX_BYTES):
            self.drop(next(iter(self._items)))
        self._items[key] = entry
        self._size += entry.size

    def restore(self, key, entry, epoch):
        if epoch == self._epoch and entry.expires > time.monotonic():
            self._insert(key, copy.deepcopy(entry))
            return True
        return False

    def store(self, key, page: dict, headers, epoch) -> bool:
        if epoch != self._epoch:
            return False
        self.drop(key)
        v = validators(headers)
        if (not response_allowed(headers) or not v
                or page.get("status") != "success" or page.get("fetch_method") != "http"
                or page.get("http_status") != 200 or "html" in page
                or not eligible_url(page.get("url", "")) or page.get("final_url") != page.get("url")
                or not re.fullmatch("[0-9a-f]{64}", page.get("content_sha256", ""))):
            return False
        try:
            size = len(json.dumps(page, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError):
            return False
        if size > MAX_BYTES:
            return False
        self._insert(key, _Entry(copy.deepcopy(page), v, time.monotonic()+MAX_AGE, size))
        return True


def accepts_304(entry: _Entry, headers) -> bool:
    # Require explicit public policy on the 304 too (stricter than RFC 9111).
    # No merging of conflicting validators or metadata.
    if not response_allowed(headers):
        return False
    v = validators(headers)
    if ("If-None-Match" in entry.headers or "etag" in headers) and v.get("If-None-Match") != entry.headers.get("If-None-Match"):
        return False
    if "last-modified" in headers and v.get("If-Modified-Since") != entry.headers.get("If-Modified-Since"):
        return False
    return bool(entry.headers)
