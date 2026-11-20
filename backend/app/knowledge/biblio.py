"""書誌の識別子（公報番号・規格番号と版・DOI）を規則で取り出す。

文書の「自分の識別子」（題名・外部 ID）と、本文に出てくる「参照」（引用文献・引用規格）を分け、
確定した書誌の関係グラフ（store.related_documents）に使う。LLM は使わない（docs/research-patent-standards-rag.md §2.3）。

正規化した値の例:
  特開2026-116114 / 特開２０２６－１１６１１４ / JP2026116114A  → patent  "jp-a-2026-116114"
  特許第7123456号 / JP7123456B2                                → patent  "jp-b-7123456"
  WO2020/123456 / WO2020123456A1                               → patent  "wo-2020-123456"
  US 10,123,456 B2 / US10123456B2                               → patent  "us-10123456"
  US 2020/0123456 A1                                            → patent  "us-a-2020-0123456"
  EP1234567 / EP 1 234 567 A1                                   → patent  "ep-1234567"
  JIS C 61000-4-2:2012                                          → standard "jis c 61000-4-2"（版 2012）
  IEC 61000-4-2:2008 / ISO/IEC 27001:2022 / CISPR 32           → standard "iec 61000-4-2" など
  10.1109/5.771073                                              → doi     "10.1109/5.771073"
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Identifier:
    scheme: str      # patent / standard / doi
    value: str       # 正規化した値（版を含まない）
    edition: str = ""
    raw: str = ""


_JP_PUB = re.compile(r"(特開|特表|再表|特願|実開|実登)\s*(\d{4})\s*[-－‐]\s*(\d{1,6})")
_JP_PAT = re.compile(r"(特許|実用新案登録)\s*第?\s*(\d{6,7})\s*号?")
_JP_CODE = re.compile(r"\bJP\s*(\d{4})\s*[-/]?\s*(\d{6})\s*A\d?\b|\bJP\s*(\d{7})\s*B\d?\b", re.I)
_WO = re.compile(r"\bWO\s*(\d{4})\s*/?\s*(\d{6})\s*(?:A\d)?\b", re.I)
_US_APP = re.compile(r"\bUS\s*(\d{4})\s*/?\s*(\d{7})\s*A\d?\b", re.I)
_US_PAT = re.compile(r"\bUS\s*(?:Pat(?:ent)?\.?\s*(?:No\.?)?\s*)?(\d{1,2}[,.]?\d{3}[,.]?\d{3})\s*(?:B\d)?\b", re.I)
_EP = re.compile(r"\bEP\s*(\d)\s*(\d{3})\s*(\d{3})\s*(?:[AB]\d)?\b", re.I)
_JP_KIND = {"特開": "jp-a", "特表": "jp-t", "再表": "jp-r", "特願": "jp-app", "実開": "jp-u", "実登": "jp-y"}

_STD = re.compile(
    r"\b(ISO\s*/\s*IEC|IEC\s*/\s*IEEE|ISO|IEC|CISPR|EN|IEEE(?:\s+Std)?|UL|ITU-[TR]\s*[A-Z]\.?|ASTM\s+[A-Z]|JIS\s*[A-Z])"
    r"\s*(\d{1,6}(?:[-.]\d{1,4})*)"
    r"(?:\s*[:：]\s*((?:19|20)\d{2}))?", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>）)\]]+)", re.I)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _std_prefix(raw: str) -> str:
    prefix = re.sub(r"\s+", " ", raw.strip()).lower()
    prefix = re.sub(r"\s*/\s*", "/", prefix).replace("ieee std", "ieee")
    prefix = re.sub(r"^(jis|astm)\s*([a-z])$", r"\1 \2", prefix)
    prefix = re.sub(r"^itu-([tr])\s*([a-z])\.?$", r"itu-\1 \2.", prefix)
    return prefix


def extract(text: str) -> list[Identifier]:
    """本文などから識別子を順に取り出す（重複は除く）。"""
    text = _norm(text)
    found: list[Identifier] = []
    seen: set[tuple[str, str]] = set()

    def add(scheme: str, value: str, edition: str = "", raw: str = "") -> None:
        key = (scheme, value)
        if key not in seen:
            seen.add(key)
            found.append(Identifier(scheme, value, edition, raw.strip()))

    for m in _JP_PUB.finditer(text):
        add("patent", f"{_JP_KIND[m.group(1)]}-{m.group(2)}-{int(m.group(3)):06d}", raw=m.group(0))
    for m in _JP_PAT.finditer(text):
        add("patent", f"jp-b-{m.group(2)}", raw=m.group(0))
    for m in _JP_CODE.finditer(text):
        if m.group(3):
            add("patent", f"jp-b-{m.group(3)}", raw=m.group(0))
        else:
            add("patent", f"jp-a-{m.group(1)}-{m.group(2)}", raw=m.group(0))
    for m in _WO.finditer(text):
        add("patent", f"wo-{m.group(1)}-{m.group(2)}", raw=m.group(0))
    for m in _US_APP.finditer(text):
        add("patent", f"us-a-{m.group(1)}-{m.group(2)}", raw=m.group(0))
    for m in _US_PAT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 6 <= len(digits) <= 8:
            add("patent", f"us-{digits}", raw=m.group(0))
    for m in _EP.finditer(text):
        add("patent", f"ep-{m.group(1)}{m.group(2)}{m.group(3)}", raw=m.group(0))
    for m in _STD.finditer(text):
        prefix = _std_prefix(m.group(1))
        number = m.group(2)
        # 年だけ（「ISO 2015」）や小さすぎる番号は誤検出が多い
        if re.fullmatch(r"(19|20)\d{2}", number):
            continue
        add("standard", f"{prefix} {number}".lower(), m.group(3) or "", m.group(0))
    for m in _DOI.finditer(text):
        add("doi", m.group(1).rstrip(".,;").lower(), raw=m.group(0))
    return found


def own_identifiers(*texts: str) -> list[Identifier]:
    """題名・外部 ID・番号の欄など、文書そのものを指す文字から。"""
    out: list[Identifier] = []
    seen: set[tuple[str, str]] = set()
    for text in texts:
        for ident in extract(text or ""):
            if (ident.scheme, ident.value) not in seen:
                seen.add((ident.scheme, ident.value))
                out.append(ident)
    return out


def references(body: str, own: list[Identifier], limit: int = 500) -> list[Identifier]:
    """本文に出てくる参照。自分の識別子は除く。"""
    mine = {(i.scheme, i.value) for i in own}
    return [i for i in extract(body or "") if (i.scheme, i.value) not in mine][:limit]


_TITLE = re.compile(r"【発明の名称】\s*([^\n【]{2,80})")


def head_identifiers(body: str, chars: int = 400) -> list[Identifier]:
    """題名に番号が無いとき（PDF のファイル名など）、本文の冒頭（公報の 1 ページ目の見出し）の番号を自分の番号とみなす。"""
    return extract((body or "")[:chars])


def invention_title(body: str) -> str:
    m = _TITLE.search(_norm(body or "")[:20000])
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


# ---- 公報の書誌（出願人・発明者・IPC・日付・請求項の数） ----
# meta["biblio"] の形（収集元・手入力でも同じ形にする。分析の画面が使う）:
#   {"applicants": [..], "inventors": [..], "ipc": ["H02M 7/48", ..], "filing_date": "2025-01-31",
#    "publication_date": "2026-07-09", "priority_date": "", "country": "JP", "kind": "A",
#    "claims_count": 10, "source": "rule_extract" | "ocr_extract" | "manual" | "epo" ...}

_IPC = re.compile(r"\b([A-H])\s?(\d{2})\s?([A-Z])\s*(\d{1,4})\s*/\s*(\d{2,6})\b")
_DATE = re.compile(r"((?:19|20)\d{2})\s*[.．/年-]\s*(\d{1,2})\s*[.．/月-]\s*(\d{1,2})")
_ORG = re.compile(r"(株式会社|有限会社|合同会社|大学|研究所|機構|法人|Corporation|Corp\.?|Inc\.?|Ltd\.?|LLC|GmbH|AG|S\.A\.|Co\.)", re.I)
_ADDRESS = re.compile(r"(都|道|府|県).{0,6}(市|区|町|村|郡)|丁目|番地|\d+番|Street|Road|Ave")
_INID = re.compile(r"\((\d{2})\)")


def _segment(text: str, code: str) -> str:
    m = re.search(r"\(" + code + r"\)", text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _INID.search(rest[2:])
    return rest[: (nxt.start() + 2) if nxt else 400][:400]


_ORG_NAME = re.compile(r"([^\s　()（）:：]{1,30}(?:株式会社|有限会社|合同会社|大学|研究所)[^\s　()（）内]{0,12})(?!内)")
_NOISE = re.compile(r"株式会社内|ターム|代理人|弁理士|テーマコード|審査請求|優先")


def _names(segment: str) -> list[str]:
    out = []
    for line in segment.splitlines():
        if _NOISE.search(line):
            continue
        line = re.sub(r"^\s*(出願人|発明者|特許権者|代理人|氏名|名称)\s*", "", line).strip(" 　:：")
        line = re.sub(r"\s{2,}.*$", "", line)          # 2 段組みの右側は捨てる
        if not line or re.fullmatch(r"[\d\s()（）-]+", line) or len(line) < 2:
            continue
        if _ADDRESS.search(line) and not _ORG.search(line):
            continue
        out.append(line[:60])
    return out[:6]


def _iso(m: re.Match | None) -> str:
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def patent_fields(text: str, head_chars: int = 6000) -> dict:
    """公報の 1 ページ目（INID コード）と請求項から書誌を規則で読む。読めない項目は空のまま（推測で埋めない）。"""
    text = _norm(text or "")
    head = text[:head_chars]
    ipc = []
    for m in _IPC.finditer(_segment(head, "51") or head):
        code = f"{m.group(1)}{m.group(2)}{m.group(3)} {int(m.group(4))}/{m.group(5)}"
        if code not in ipc:
            ipc.append(code)
    count = None
    m = re.search(r"請求項の数\s*[:：]?\s*(\d{1,3})", head)
    if m:
        count = int(m.group(1))
    else:
        numbers = [int(n) for n in re.findall(r"【請求項\s*(\d{1,3})】", text)]
        count = max(numbers) if numbers else None
    own = head_identifiers(text)
    country, kind = "", ""
    if own and own[0].scheme == "patent":
        parts = own[0].value.split("-")
        country = parts[0].upper()
        kind = {"a": "A", "b": "B", "t": "A", "r": "A", "y": "U", "u": "U"}.get(parts[1], "") if len(parts) > 1 else ""
    applicants = [n for n in (_names(_segment(head, "71")) or _names(_segment(head, "73"))) if _ORG.search(n) or len(n) <= 12]
    if not applicants:
        # 2 段組みで (71) の名前が別の行・列に回ったとき: 1 ページ目の会社名（「…株式会社内」は発明者の住所なので除く）
        for m in _ORG_NAME.finditer(head[:3000]):
            name = m.group(1)
            if name not in applicants and not head[m.end():m.end() + 1] == "内":
                applicants.append(name)
    return {
        "title": invention_title(text),
        "applicants": applicants[:6],
        "inventors": _names(_segment(head, "72")),
        "ipc": ipc[:20],
        "filing_date": _iso(_DATE.search(_segment(head, "22"))),
        "publication_date": _iso(_DATE.search(_segment(head, "43")) or _DATE.search(_segment(head, "45"))),
        "priority_date": _iso(_DATE.search(_segment(head, "32"))),
        "country": country, "kind": kind, "claims_count": count,
    }


def has_front_page(text: str) -> bool:
    head = _norm(text or "")[:6000]
    return bool(re.search(r"\((51|71|22)\)", head))
