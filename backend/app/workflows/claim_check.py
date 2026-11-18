"""引用の中身の検証: 引用の付いた文が、引用先の本文に支えられているか（LLM を使わない）。

Deep Research の引用の確かめは「番号が実在するか」「段落に引用があるか」の形式だけで、出典が本当に
その文を支えているかを見ていなかった（2026-11-17、検索の統合の提案「出典の確かめ」）。文の語（日本語は
文字の 2 字組、英数字は単語）と数値が、引用先の本文にどれだけ出てくるかで支えの強さを測る。
"""
from __future__ import annotations

import re

_CITE = re.compile(r"\[(\d{1,3})\]")
_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[a-z0-9][a-z0-9.+-]{1,}", re.I)
_CJK = re.compile(r"[぀-ヿ㐀-鿿]+")
# 支えが弱いとみなす境目（文の語のうち、引用先に出てくる割合）
WEAK_SUPPORT = 0.15


def _terms(text: str) -> set[str]:
    text = _CITE.sub(" ", text)
    terms = {word.lower() for word in _WORD.findall(text)}
    for run in _CJK.findall(text):
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(_CITE.sub(" ", text)) if len(n.replace(",", "")) >= 2}


def check_claims(report: str, sources: list[dict]) -> dict:
    """引用の付いた文ごとの支えの強さ。sources は 1 始まりの番号の順。"""
    texts = [f"{s.get('title', '')} {s.get('snippet', '')}" for s in sources]
    term_cache: dict[int, set[str]] = {}
    checked = 0
    weak: list[dict] = []
    for match in _SENTENCE.finditer(report):
        sentence = match.group(0).strip()
        cited = [int(n) for n in _CITE.findall(sentence) if 1 <= int(n) <= len(texts)]
        if not cited or len(_CITE.sub("", sentence).strip()) < 12:
            continue
        checked += 1
        own = _terms(sentence)
        best = 0.0
        missing_numbers: set[str] = _numbers(sentence)
        for number in cited:
            if number not in term_cache:
                term_cache[number] = _terms(texts[number - 1])
            if own:
                best = max(best, len(own & term_cache[number]) / len(own))
            source_text = texts[number - 1].replace(",", "")
            missing_numbers = {n for n in missing_numbers if n not in source_text}
        if best < WEAK_SUPPORT or missing_numbers:
            weak.append({"sentence": sentence[:300], "cited": cited, "support": round(best, 3),
                         "missing_numbers": sorted(missing_numbers)[:5]})
    return {"claims_checked": checked, "weak_claims": len(weak), "weak": weak[:30],
            "supported_ratio": round(1 - len(weak) / checked, 3) if checked else 1.0}


def weak_claims_note(result: dict, limit: int = 8) -> str:
    """直しきれなかった弱い主張をレポートの末尾に正直に並べる。"""
    if not result.get("weak"):
        return ""
    lines = ["", "## 根拠の確認が弱い主張", "",
             f"引用先の本文と照らし合わせて、支えが弱いと判定した主張です（{result['weak_claims']} / {result['claims_checked']} 件）。"
             "出典を開いて確かめてください。", ""]
    for item in result["weak"][:limit]:
        reason = f"数値 {', '.join(item['missing_numbers'])} が出典に見当たらない" if item["missing_numbers"] \
            else f"出典との一致 {int(item['support'] * 100)}%"
        lines.append(f"- {item['sentence']}（{reason}）")
    return "\n".join(lines)
