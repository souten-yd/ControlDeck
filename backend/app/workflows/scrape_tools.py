"""Web スクレイピングの事前解析: URL 取得 → 候補セレクタ抽出 + ビューワ用 HTML。

- ビジュアルピッカー用に HTML をサニタイズ（script/style/on* 除去、相対 URL を絶対化、
  base ターゲットを付与）して返す。
- テキストのある要素から代表的な CSS セレクタ候補を生成し、サンプルと一致数を付ける。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

MAX_HTML = 2_000_000  # ビューワへ渡す HTML の上限
UA = "Mozilla/5.0 (compatible; ControlDeck/1.0)"


class ScrapeError(Exception):
    pass


async def fetch(url: str, timeout: float = 20.0) -> tuple[str, int, str]:
    """HTML を取得する（Workflow のページ解析など）。取得は Web 収集エンジンの共通の取得
    （内部のアドレスへ届かない・リダイレクトを各段で検査）。以前は follow_redirects で内部へも届いた。"""
    page = await _collect(url, timeout)
    return page.get("html") or page["markdown"], page["http_status"], page["final_url"]


async def fetch_document(url: str, timeout: float = 30.0) -> tuple[str, str, str]:
    """資料として取り込む本文（Markdown）・題名・最終 URL。ナレッジ・Patent の URL 取り込みが使う。
    以前はページ全体の文字（メニュー・フッターを含む）を取り込んでいた。"""
    page = await _collect(url, timeout)
    if page["status"] != "success":
        raise ScrapeError(f"本文を取り出せませんでした（{page['status']}）")
    text = page["markdown"]
    if page["title"] and not text.lstrip().startswith("#"):
        text = f"# {page['title']}\n\n{text}"
    return text, page["title"], page["final_url"]


async def _collect(url: str, timeout: float) -> dict:
    from app.web_collector import fetch as web_fetch

    if not url.startswith(("http://", "https://")):
        raise ScrapeError("URL は http:// または https:// で始めてください")
    page = await web_fetch.collect(url, timeout=min(timeout, 60), keep_html=True)
    if page["status"] in ("security_rejected", "timeout", "too_large", "captcha") or (
            page["status"] == "http_error" and not page["http_status"]):
        raise ScrapeError(f"取得に失敗しました（{page['status']}）: {page['error']}")
    return page


def _css_selector(el) -> str:
    """要素に対する簡潔で比較的安定した CSS セレクタを生成する。

    優先: #id → tag.class（クラスがあれば最大2つ）→ 祖先を辿って nth-of-type パス。
    """
    if el.get("id"):
        return f"#{el['id']}"
    classes = [c for c in (el.get("class") or []) if _valid_ident(c)]
    tag = el.name
    if classes:
        return f"{tag}." + ".".join(classes[:2])
    # 祖先パス（最大4階層、nth-of-type で一意化）
    parts: list[str] = []
    cur = el
    depth = 0
    while cur is not None and cur.name and cur.name != "[document]" and depth < 4:
        seg = cur.name
        cid = cur.get("id")
        if cid and _valid_ident(cid):
            parts.insert(0, f"#{cid}")
            break
        cclasses = [c for c in (cur.get("class") or []) if _valid_ident(c)]
        if cclasses:
            seg = f"{cur.name}." + ".".join(cclasses[:2])
        else:
            parent = cur.parent
            if parent is not None:
                same = [s for s in parent.find_all(cur.name, recursive=False)]
                if len(same) > 1:
                    seg = f"{cur.name}:nth-of-type({same.index(cur) + 1})"
        parts.insert(0, seg)
        cur = cur.parent
        depth += 1
    return " > ".join(parts)


def _valid_ident(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", s or ""))


def analyze(html: str) -> list[dict]:
    """候補セレクタを抽出する。テキストを持つ代表的要素をまとめて返す。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(selector: str, kind: str) -> None:
        if not selector or selector in seen:
            return
        try:
            matched = soup.select(selector)
        except Exception:
            return
        if not matched:
            return
        seen.add(selector)
        sample = matched[0].get_text(" ", strip=True)[:120]
        candidates.append({
            "selector": selector,
            "kind": kind,
            "count": len(matched),
            "sample": sample,
        })

    # 見出し・リンク・段落・リスト・画像・共通クラス
    for tag in ("h1", "h2", "h3", "title", "p", "a", "li", "td", "th"):
        for el in soup.find_all(tag)[:3]:
            if el.get_text(strip=True) or tag in ("a",):
                add(_css_selector(el), tag)

    # よく使う繰り返しクラス（同一クラスが複数回出るもの）
    class_count: dict[str, int] = {}
    for el in soup.find_all(True):
        for c in el.get("class") or []:
            if _valid_ident(c):
                class_count[f"{el.name}.{c}"] = class_count.get(f"{el.name}.{c}", 0) + 1
    for sel, cnt in sorted(class_count.items(), key=lambda kv: -kv[1])[:15]:
        if cnt >= 2:
            add(sel, "repeat")

    # 一致数が多い（リスト系）→ 少ない（単体）の順、上限 40
    candidates.sort(key=lambda c: (-c["count"], len(c["selector"])))
    return candidates[:40]


def sanitize_for_viewer(html: str, base_url: str) -> str:
    """ビューワ iframe (srcdoc) 用に HTML をサニタイズする。

    - script/style/iframe/object を除去、on* 属性除去
    - 相対 URL（href/src）を絶対化して画像等が表示できるようにする
    - <base target="_blank"> を挿入しリンク遷移を親から切り離す
    """
    from bs4 import BeautifulSoup

    if len(html) > MAX_HTML:
        html = html[:MAX_HTML]
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "iframe", "object", "embed", "noscript"]):
        tag.decompose()
    for el in soup.find_all(True):
        # イベントハンドラ除去
        for attr in list(el.attrs):
            if attr.lower().startswith("on"):
                del el[attr]
        # URL 絶対化
        for attr in ("href", "src"):
            if el.has_attr(attr):
                try:
                    el[attr] = urljoin(base_url, el[attr])
                except Exception:
                    pass
        if el.has_attr("srcset"):
            del el["srcset"]
    head = soup.find("head")
    if head is None:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
    base_tag = soup.new_tag("base", target="_blank")
    head.insert(0, base_tag)
    return str(soup)


def preview(html: str, selector: str, attribute: str, multiple: bool, limit: int = 20) -> dict:
    """セレクタを適用して抽出結果のプレビューを返す（対比表示用）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    try:
        matched = soup.select(selector) if selector else []
    except Exception as e:
        return {"ok": False, "error": f"セレクタが不正です: {e}", "count": 0, "results": []}

    def value_of(el) -> str:
        if attribute in ("", "text"):
            return el.get_text(" ", strip=True)
        if attribute == "html":
            return el.decode_contents()[:500]
        return el.get(attribute, "")

    values = [value_of(el) for el in matched]
    shown = values[:limit] if multiple else values[:1]
    return {"ok": True, "count": len(matched), "results": shown, "truncated": len(values) > len(shown)}
