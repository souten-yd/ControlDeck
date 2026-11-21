"""本文抽出の子プロセス（機能「Web 本文抽出」の専用 venv の Python で `-I` で動く）。

標準入力から 1 行 1 件の JSON {"id", "html", "url", "mode"} を読み、標準出力へ 1 行 1 件の JSON を返す。
Trafilatura には**取得済みの HTML だけ**を渡す（fetch_url・sitemaps・spider は使わない。通信は ControlDeck が行う）。
このファイルは ControlDeck の他のモジュールを import しない（専用 venv には ControlDeck が無い）。
"""
from __future__ import annotations

import json
import re
import sys

import trafilatura
from lxml import html as lxml_html

SHORT = 200        # これより短い本文は「抜けすぎ」を疑う
# 精度寄り（favor_precision）は見出しを落とす（2026-11-21 ja.wikipedia「特許」で見出し 0 件。標準は 19 件）。
# 見出しは資料を節に分ける手がかりなので、article も標準の設定で抽出する。
_EDIT_LINK = re.compile(r"^\s*\\?\[(?:編集|ソースを編集|edit|edit source)\\?\]\s*$", re.I | re.M)
VISIBLE_RATIO = 4  # 見えている文字がこの倍以上あれば、網羅寄りで抽出し直す


def _visible_text(tree) -> str:
    for bad in tree.xpath("//script|//style|//noscript|//template"):
        bad.drop_tree()
    return " ".join(tree.text_content().split())


def _links(tree, base: str) -> list[dict]:
    tree.make_links_absolute(base, resolve_base_href=True)
    out, seen = [], set()
    for a in tree.xpath("//a[@href]"):
        href = a.get("href", "").split("#", 1)[0]
        if not href.startswith(("http://", "https://")) or href in seen:
            continue
        seen.add(href)
        out.append({"url": href, "text": " ".join(a.text_content().split())[:200]})
        if len(out) >= 500:
            break
    return out


def extract(html: str, url: str, mode: str) -> dict:
    precision = False
    recall = mode in ("full_text", "documentation")
    options = dict(url=url, output_format="markdown", include_tables=True, include_links=mode == "documentation",
                   include_formatting=True, include_comments=False, favor_precision=precision, favor_recall=recall,
                   deduplicate=True)
    markdown = trafilatura.extract(html, **options) or ""
    warnings: list[str] = []
    tree = lxml_html.fromstring(html) if html.strip() else None
    visible = _visible_text(lxml_html.fromstring(html)) if tree is not None else ""
    if len(markdown) < SHORT and len(visible) > VISIBLE_RATIO * max(len(markdown), 1) and not recall:
        retry = trafilatura.extract(html, **{**options, "favor_precision": False, "favor_recall": True}) or ""
        if len(retry) > len(markdown):
            markdown = retry
            warnings.append("short_extraction_retried_with_recall")
    if not markdown and visible:
        markdown = visible
        warnings.append("fallback_visible_text")
    markdown = re.sub(r"\n{3,}", "\n\n", _EDIT_LINK.sub("", markdown)).strip()
    meta = trafilatura.extract_metadata(html, default_url=url)
    metadata = {}
    if meta is not None:
        for key in ("title", "author", "date", "sitename", "description", "url", "hostname", "language"):
            value = getattr(meta, key, None)
            if value:
                metadata[key] = str(value)[:2000]
    return {"markdown": markdown, "title": metadata.get("title", ""), "metadata": metadata,
            "links": _links(tree, url) if tree is not None else [], "visible_chars": len(visible),
            "warnings": warnings, "extractor": f"trafilatura {trafilatura.__version__}"}


def main() -> int:
    for line in sys.stdin:
        try:
            req = json.loads(line)
            out = {"id": req.get("id"), "ok": True, **extract(req.get("html", ""), req.get("url", ""), req.get("mode", "article"))}
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で worker を止めない
            out = {"id": None, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
            try:
                out["id"] = json.loads(line).get("id")
            except Exception:  # noqa: BLE001
                pass
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(trafilatura.__version__)
        sys.exit(0)
    sys.exit(main())
