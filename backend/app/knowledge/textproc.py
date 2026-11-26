"""日本語向けの正規化・FTS 用トークン化・検索式の変換。

ResearchDataHub（qnaplibrary/textproc.py, 2026-11-19 main b4f7039）から統合のため移植した。
ControlDeck 側のチャンク分割は app.workflows.chunkers を使うので chunk_text は持ってこない。


Japanese has no spaces, so CJK runs are indexed as overlapping character bigrams
("半導体装置" -> "半導 導体 体装 装置"). A query run is turned into a phrase of the same
bigrams, which matches the exact substring. ASCII words are indexed as whole words.
Works with the plain FTS5 'unicode61' tokenizer, so no trigram tokenizer (SQLite >= 3.34)
or external morphological analyser is needed.
"""
import re
import unicodedata

_CJK = (r"぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ"
        r"々〆ー가-힯")
_TOKEN_RE = re.compile(r"[%s]+|[0-9a-z]+(?:[._\-/][0-9a-z]+)*" % _CJK)
_CJK_RE = re.compile(r"^[%s]+$" % _CJK)


_STD_RE = re.compile(r"\b(jis|jas|iso|iec|astm|din|bs|en|ieee|jasos|jesd|ul|ansi|api|asme|sae)\s*[-/]?\s*([a-z])?\s*-?\s*(\d{1,6})")


def normalize(text):
    """NFKC + lower case; standard numbers are collapsed ("JIS B 1194" / "JISB1194" -> "jisb1194")."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    return _STD_RE.sub(lambda m: m.group(1) + (m.group(2) or "") + m.group(3), t)


def _bigrams(run):
    if len(run) == 1:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def tokens(text):
    """Return the index tokens of a text (list of str)."""
    out = []
    for match in _TOKEN_RE.finditer(normalize(text)):
        word = match.group(0)
        if _CJK_RE.match(word):
            out.extend(_bigrams(word))
        else:
            # keep compound ids like h01l-21/02 as one token and also their parts
            parts = re.split(r"[._\-/]", word)
            out.append(word.replace(".", "_").replace("-", "_").replace("/", "_"))
            if len(parts) > 1:
                out.extend(p for p in parts if p)
    return out


def index_text(text):
    return " ".join(tokens(text))


def _phrase(segment):
    toks = tokens(segment)
    if not toks:
        return None
    return '"' + " ".join(t.replace('"', "") for t in toks) + '"'


def parse_query(query):
    """Convert a user query to an FTS5 MATCH expression.

    Syntax: space = AND, `OR` between terms, `-term` = NOT, "quoted phrase" kept together.
    Returns None when the query has no searchable token.
    """
    query = normalize(query)
    parts = re.findall(r'-?"[^"]+"|\S+', query)
    positive, negative, pending_or = [], [], False
    for part in parts:
        if part == "or":
            pending_or = True
            continue
        neg = part.startswith("-") and len(part) > 1
        body = part[1:] if neg else part
        body = body.strip('"')
        phrase = _phrase(body)
        if not phrase:
            continue
        if neg:
            negative.append(phrase)
        elif pending_or and positive:
            positive[-1] = "(%s OR %s)" % (positive[-1], phrase)
        else:
            positive.append(phrase)
        pending_or = False
    if not positive:
        return None
    expr = " AND ".join(positive)
    for n in negative:
        expr = "%s NOT %s" % (expr, n)
    return expr


def has_explicit_syntax(query):
    """Explicit operators/quotes must not be weakened by candidate expansion."""
    q = normalize(query)
    return '"' in q or bool(re.search(r'(?:^|\s)-\S|(?:^|\s)or(?:\s|$)', q))


def or_query(text, max_terms=48):
    """Bag-of-tokens query (each index token OR'ed) for ranking documents by similarity to a text."""
    seen, out = set(), []
    for t in tokens(text):
        if t not in seen and len(t) >= 2:
            seen.add(t)
            out.append('"%s"' % t)
        if len(out) >= max_terms:
            break
    return " OR ".join(out)


def query_terms(query):
    """Plain terms of a query, for highlighting and LIKE fallback."""
    query = normalize(query)
    return [p.strip('"') for p in re.findall(r'"[^"]+"|\S+', query)
            if p != "or" and not p.startswith("-") and p.strip('"')]


_CLAIM_HEAD = re.compile(r"【請求項\s*([0-9０-９]+)】")


def split_claims(text):
    """Split Japanese/English claim text into [(number, text)]."""
    text = text or ""
    heads = list(_CLAIM_HEAD.finditer(text))
    if heads:
        out = []
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            out.append((int(normalize(m.group(1))), text[m.end():end].strip()))
        return out
    eng = list(re.finditer(r"(?m)^\s*(\d{1,3})\s*[.)]\s+", text))
    if eng:
        out = []
        for i, m in enumerate(eng):
            end = eng[i + 1].start() if i + 1 < len(eng) else len(text)
            out.append((int(m.group(1)), text[m.end():end].strip()))
        return out
    return [(1, text.strip())] if text.strip() else []


def snippet(text, terms, width=160):
    text = text or ""
    low = normalize(text)
    pos = -1
    for t in terms:
        pos = low.find(normalize(t))
        if pos >= 0:
            break
    if pos < 0:
        return text[:width]
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")
