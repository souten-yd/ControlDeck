"""JavaScript が要るページかの判定（W2）。**静的な HTTP の取得を先に行い、足りないときだけブラウザへ進む**ための材料。

入力は取得した HTML と、そこから抽出した本文（Markdown）。通信はしない（純粋な関数。固定の HTML の見本で評価する）。

判定の材料（docs/PLAN-web-collector.md §7 W2、HANDOFF「W2 で最初にやること」）:
- 本文の短さ
- `noscript` や本文の「JavaScript が必要」の案内（OpenAlex は本文が 254 字の「needs JavaScript」の案内だけだった）
- SPA の殻（空の `#root` / `#app` / `#__next` / `#__nuxt` / `<app-root>`）、大きな script だけで本文が無い
- 初期 JSON（`__NEXT_DATA__`・`window.__NUXT__`・`__INITIAL_STATE__`・`application/ld+json`・`application/json` の script）。
  本文に当たる文字列が十分あれば**ブラウザを使わず**そこから取る（Scrapy 公式文書「裏の公開 JSON を直接使うほうが効率的」）。

本文が十分長いページ（SSR 済み）は、script が多くても判定しない（ブラウザを起こす費用を避ける）。
"""
from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field

# 本文（抽出後の Markdown）の長さの目安
EMPTY_CHARS = 120
SHORT_CHARS = 600
JSON_MIN_CHARS = 500          # 初期 JSON の本文が最低これだけ無いと使わない
JSON_MAX_CHARS = 200_000

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_NOSCRIPT = re.compile(r"<noscript\b[^>]*>(.*?)</noscript\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_NEEDS_JS = re.compile(
    r"(?:enable|requires?|needs?|must\s+have|turn\s+on|activate)\s+(?:java\s?script|js)|"
    r"java\s?script\s+(?:is\s+)?(?:required|disabled|must|needed|not\s+enabled|turned\s+off)|"
    r"(?:works?|run|load|display)s?\s+(?:properly\s+|correctly\s+)?without\s+java\s?script|"
    r"without\s+java\s?script.{0,40}(?:not|n't)\s+(?:work|run|load|display)|"
    r"browser.{0,40}java\s?script.{0,30}(?:disabled|off|enable)|"
    r"java\s?script\s*を\s*(?:有効|オン|ON)|java\s?script.{0,12}(?:必要|無効|オフ|使用できません|利用できません)|"
    r"(?:ブラウザ|お使いの).{0,16}java\s?script.{0,20}(?:有効|オン)",
    re.I | re.S)
# 空の殻の根（中身が空白だけ）
_EMPTY_ROOT = re.compile(
    r"""<(div|main|section)\b[^>]*\bid\s*=\s*["']?(?:root|app|__next|__nuxt|__sapper__|svelte|react-root|q-app|application|main-app)["']?[^>]*>\s*(?:<!--.*?-->\s*)*</\1\s*>""",
    re.I | re.S)
_EMPTY_CUSTOM_ROOT = re.compile(r"<(app-root|my-app|ion-app|vue-app|root-app)\b[^>]*>\s*</\1\s*>", re.I)
# 本文と関係のない計測・広告の script（数えない）
_ANALYTICS = re.compile(r"googletagmanager|google-analytics|gtag|doubleclick|googlesyndication|facebook\.net|hotjar|clarity\.ms|"
                        r"analytics|adsbygoogle|segment\.|plausible|matomo|piwik", re.I)
_JS_ASSIGN = re.compile(
    r"(?:window\s*\.\s*)?(__NUXT__|__INITIAL_STATE__|__PRELOADED_STATE__|__APOLLO_STATE__|__INITIAL_DATA__|__DATA__|__remixContext|__REDUX_STATE__)\s*=\s*", re.I)
_SKIP_KEYS = frozenset({"buildid", "assetprefix", "css", "style", "styles", "classname", "script", "scripts", "href", "url", "src",
                        "image", "images", "thumbnail", "icon", "favicon", "canonical", "locale", "locales", "gsp", "gssp", "runtimeconfig"})


@dataclass
class Verdict:
    needs_js: bool = False
    reasons: list[str] = field(default_factory=list)
    json_text: str = ""          # 初期 JSON から取れた本文（あれば、ブラウザを使わずこれを使う）
    json_source: str = ""        # どの JSON か（__NEXT_DATA__ など）
    text_chars: int = 0
    script_bytes: int = 0

    def as_dict(self) -> dict:
        return {"needs_js": self.needs_js, "reasons": self.reasons, "json_source": self.json_source,
                "json_chars": len(self.json_text), "text_chars": self.text_chars, "script_bytes": self.script_bytes}


def _scripts(html: str) -> list[tuple[str, str]]:
    return [(attrs, body) for attrs, body in _SCRIPT.findall(html[:3_000_000])]


def _plain(markup: str) -> str:
    return " ".join(htmllib.unescape(_TAG.sub(" ", markup)).split())


def _balanced_json(text: str, start: int) -> object | None:
    """text[start:] の先頭の JSON の値（オブジェクト・配列）を 1 つ読む。"""
    try:
        value, _end = json.JSONDecoder().raw_decode(text, start)
    except (ValueError, RecursionError):
        return None
    return value


def initial_json_blobs(html: str) -> list[tuple[str, object]]:
    """初期状態の JSON（出どころ, 値）。壊れたもの・大きすぎるものは読まない。"""
    blobs: list[tuple[str, object]] = []
    for attrs, body in _scripts(html):
        body = body.strip()
        if not body or len(body) > 5_000_000:
            continue
        lowered = attrs.casefold()
        script_id = re.search(r"""\bid\s*=\s*["']?([\w\-]+)""", attrs, re.I)
        name = script_id.group(1) if script_id else ""
        if "json" in lowered and "src=" not in lowered:
            try:
                blobs.append((name or ("ld+json" if "ld+json" in lowered else "application/json"), json.loads(body)))
            except (ValueError, RecursionError):
                pass
            continue
        match = _JS_ASSIGN.search(body[:2000])
        if match and "src=" not in lowered:
            start = match.end()
            if body[start:start + 1] in ("{", "["):
                value = _balanced_json(body, start)
                if value is not None:
                    blobs.append((match.group(1), value))
    return blobs


def _looks_like_prose(value: str) -> bool:
    if len(value) < 40 or value.startswith(("http://", "https://", "/", "data:", "{", "[")):
        return False
    if value.count(" ") + value.count("　") >= 5 and re.search(r"[A-Za-z]{3,}", value):
        return True
    cjk = sum(1 for c in value if "぀" <= c <= "ヿ" or "一" <= c <= "鿿" or "가" <= c <= "힯")
    return cjk >= 20 and cjk / len(value) > 0.4


def _walk(node: object, out: list[str], depth: int = 0) -> None:
    if depth > 40 or len(out) > 4000:
        return
    if isinstance(node, str):
        text = node
        if "<" in text and ">" in text:  # HTML の断片（本文を HTML で持つ API）は文字だけにする
            text = _plain(text)
        text = " ".join(text.split())
        if _looks_like_prose(text):
            out.append(text)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out, depth + 1)
    elif isinstance(node, dict):
        for key, value in node.items():
            if str(key).casefold() in _SKIP_KEYS:
                continue
            _walk(value, out, depth + 1)


def json_prose(html: str) -> tuple[str, str]:
    """初期 JSON から、本文らしい文字列を（出た順・重複なしで）集める。(本文, 出どころ)。"""
    best_text, best_source = "", ""
    for source, value in initial_json_blobs(html):
        found: list[str] = []
        _walk(value, found)
        text = "\n\n".join(dict.fromkeys(found))[:JSON_MAX_CHARS]
        if len(text) > len(best_text):
            best_text, best_source = text, source
    return best_text, best_source


def assess(html: str, text: str) -> Verdict:
    """取得した HTML と抽出した本文から、JavaScript（ブラウザ）が要るかを判定する。"""
    verdict = Verdict(text_chars=len(text.strip()))
    scripts = _scripts(html)
    inline_bytes = sum(len(body) for attrs, body in scripts if "src=" not in attrs.casefold())
    external = sum(1 for attrs, _ in scripts if "src=" in attrs.casefold() and not _ANALYTICS.search(attrs))
    verdict.script_bytes = inline_bytes
    chars = verdict.text_chars
    if chars >= SHORT_CHARS * 4:
        return verdict  # 十分な本文がある（SSR 済み）

    reasons: list[str] = []
    noscript_text = " ".join(_plain(m) for m in _NOSCRIPT.findall(html[:3_000_000]))
    if noscript_text and _NEEDS_JS.search(noscript_text):
        reasons.append("noscript が JavaScript を求めている")
    if chars < SHORT_CHARS and _NEEDS_JS.search(text):
        reasons.append("本文が「JavaScript が必要」の案内")
    spa_root = bool(_EMPTY_ROOT.search(html[:3_000_000]) or _EMPTY_CUSTOM_ROOT.search(html[:3_000_000]))
    if spa_root and chars < SHORT_CHARS:
        reasons.append("SPA の殻（空の根要素）")
    script_heavy = (inline_bytes >= 3000 or external >= 3) and chars < SHORT_CHARS and inline_bytes + external * 500 > chars * 5
    if script_heavy and not reasons:
        reasons.append("script が大きく本文が短い")
    if chars < EMPTY_CHARS and (external >= 2 or inline_bytes >= 800) and not reasons:
        reasons.append("本文がほとんど無く script がある")

    # 初期 JSON に本文があれば、ブラウザを使わずそこから取る（本文が短いときだけ見る）
    if reasons or chars < SHORT_CHARS:
        prose, source = json_prose(html)
        if len(prose) >= JSON_MIN_CHARS and len(prose) >= chars * 2:
            verdict.json_text, verdict.json_source = prose, source
            verdict.reasons = reasons + [f"初期 JSON（{source}）に本文がある"]
            return verdict
    verdict.reasons = reasons
    verdict.needs_js = bool(reasons)
    return verdict
