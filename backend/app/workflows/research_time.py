"""調査の「時」の部品: 今の日時・相対の時の言い方の解決・ページの日付の取り出しと、依頼の期間との照合。

根拠: 2026-11-22 利用者「『今日の横浜の天気を調べて』で、今日がいつか分かっておらず、違う日付・過去の天気を調べることがある」。
LLM は今日の日付を知らない。調べる前に今の日時を渡し、「今日・明日・先週」を絶対の日付にし、読んだページの日付が
依頼の期間と合うかを確かめる。今の日時を入れる所は `now_text()` の 1 か所（依頼の理解・十分か判定・答え・チャットの道具の指示が同じ関数を使う）。
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tokyo")
_WEEKDAYS = "月火水木金土日"


def now_jst() -> dt.datetime:
    return dt.datetime.now(TZ)


def now_text(now: dt.datetime | None = None) -> str:
    """「2026年11月22日（日）15時40分」。"""
    now = (now or now_jst()).astimezone(TZ)
    return f"{now.year}年{now.month}月{now.day}日（{_WEEKDAYS[now.weekday()]}）{now.hour}時{now.minute:02d}分"


def today_note(now: dt.datetime | None = None) -> str:
    return f"今日は{now_text(now)}（日本時間）です。「今日・明日・最新」などは、この日付で考えてください。"


def _span(label: str, start: dt.date, end: dt.date) -> dict:
    return {"label": label, "start": start.isoformat(), "end": end.isoformat()}


# 相対の時の言い方 → (ラベル, 開始, 終了)。上から順に、最初に当たったものを使う（長い言い方を先に）
def resolve_relative(text: str, now: dt.datetime | None = None) -> dict | None:
    today = (now or now_jst()).astimezone(TZ).date()
    monday = today - dt.timedelta(days=today.weekday())
    d = dt.timedelta
    first_next_month = (today.replace(day=1) + d(days=32)).replace(day=1)
    rules = [
        ("一昨日", _span("一昨日", today - d(days=2), today - d(days=2))),
        ("明後日", _span("明後日", today + d(days=2), today + d(days=2))),
        ("明日", _span("明日", today + d(days=1), today + d(days=1))),
        ("昨日", _span("昨日", today - d(days=1), today - d(days=1))),
        ("今日", _span("今日", today, today)), ("本日", _span("今日", today, today)),
        ("今週末", _span("今週末", monday + d(days=5), monday + d(days=6))),
        ("来週", _span("来週", monday + d(days=7), monday + d(days=13))),
        ("先週", _span("先週", monday - d(days=7), monday - d(days=1))),
        ("今週", _span("今週", monday, monday + d(days=6))),
        ("先月", _span("先月", (today.replace(day=1) - d(days=1)).replace(day=1), today.replace(day=1) - d(days=1))),
        ("来月", _span("来月", first_next_month, (first_next_month + d(days=32)).replace(day=1) - d(days=1))),
        ("今月", _span("今月", today.replace(day=1), first_next_month - d(days=1))),
        ("昨年", _span("昨年", dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31))),
        ("去年", _span("昨年", dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31))),
        ("来年", _span("来年", dt.date(today.year + 1, 1, 1), dt.date(today.year + 1, 12, 31))),
        ("今年", _span("今年", dt.date(today.year, 1, 1), dt.date(today.year, 12, 31))),
        ("最新", _span("最新", today - d(days=60), today)), ("直近", _span("直近", today - d(days=60), today)),
        ("最近", _span("最近", today - d(days=60), today)),
        ("現在", _span("現在", today - d(days=14), today)),
    ]
    for word, span in rules:
        if word in text:
            return span
    match = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text)
    if match:
        day = _infer_date(int(match.group(1)), int(match.group(2)), today)
        if day:
            return _span(f"{day.month}月{day.day}日", day, day)
    return None


TIME_WORDS = re.compile(r"今日|本日|明日|明後日|昨日|一昨日|今週|来週|先週|今月|来月|先月|今年|来年|昨年|去年|最新|直近|最近|現在|"
                        r"(?<!\d)\d{1,2}月\d{1,2}日|\btoday\b|\btomorrow\b|\byesterday\b|\blatest\b|\bthis week\b", re.I)
# 時期が大事な話題（これらは検索の要約だけで答えない）
VOLATILE = re.compile(r"天気|気温|予報|価格|値段|相場|在庫|ニュース|速報|番組|営業時間|株価|為替|ランキング|発売日|"
                      r"weather|price|stock|news|forecast", re.I)


def has_time_words(text: str) -> bool:
    return bool(TIME_WORDS.search(text or ""))


def stamp_of(time: dict) -> str:
    """期間を検索語に入れる形: 1 日なら「11月22日」、同じ月なら「2026年11月」、それ以外は「2026年」。"""
    start, end = dt.date.fromisoformat(time["start"]), dt.date.fromisoformat(time["end"])
    if start == end:
        return f"{start.month}月{start.day}日"
    if start.year == end.year and start.month == end.month:
        return f"{start.year}年{start.month}月"
    return f"{end.year}年"


def date_variants(query: str, time: dict | None, *, force: bool = False) -> list[str]:
    """検索語に絶対の日付を足した版を作る（元の語も残す）。時の言葉を含むときだけ。force なら含まなくても足す（理解の段）。"""
    if not time or not (force or has_time_words(query)):
        return [query]
    base = " ".join(TIME_WORDS.sub(" ", query).split()) or query
    return list(dict.fromkeys(v for v in (query, f"{base} {stamp_of(time)}".strip()) if v))


# ---- ページの日付 ----

_YMD = re.compile(r"(?<!\d)(20\d{2})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})(?!\d)")
_MD = re.compile(r"(?<![\d/.\-])(\d{1,2})月\s*(\d{1,2})日")
_EN_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_EN = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I)
_EN2 = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b", re.I)
_URL_DATE = re.compile(r"/(20\d{2})[/\-]?(\d{2})[/\-]?(\d{2})(?:/|$|-|_)")


def _safe(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def _infer_date(month: int, day: int, today: dt.date) -> dt.date | None:
    """年の無い「11月22日」は、今日に最も近い年とみなす。"""
    options = [x for x in (_safe(today.year + k, month, day) for k in (-1, 0, 1)) if x]
    return min(options, key=lambda x: abs((x - today).days)) if options else None


def extract_dates(text: str, today: dt.date, url: str = "") -> list[dt.date]:
    found: list[dt.date] = []
    for m in _YMD.finditer(text):
        found.append(_safe(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _EN.finditer(text):
        found.append(_safe(int(m.group(3)), _EN_MONTHS[m.group(1).lower()[:3]], int(m.group(2))))
    for m in _EN2.finditer(text):
        found.append(_safe(int(m.group(3)), _EN_MONTHS[m.group(2).lower()[:3]], int(m.group(1))))
    stripped = _YMD.sub(" ", text)
    for m in _MD.finditer(stripped):
        found.append(_infer_date(int(m.group(1)), int(m.group(2)), today))
    for m in _URL_DATE.finditer(url):
        found.append(_safe(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return [d for d in dict.fromkeys(found) if d is not None and d.year >= 2000]


def window_for(time: dict | None, freshness: str, today: dt.date) -> tuple[dt.date, dt.date] | None:
    """依頼の期間。time があればそれ、なければ freshness（latest 60 日・recent 180 日）。どちらも無ければ None（照合しない）。"""
    if time:
        return dt.date.fromisoformat(time["start"]), dt.date.fromisoformat(time["end"])
    if freshness == "latest":
        return today - dt.timedelta(days=60), today + dt.timedelta(days=1)
    if freshness == "recent":
        return today - dt.timedelta(days=180), today + dt.timedelta(days=1)
    return None


def judge_page(text: str, url: str, meta_date: str, window: tuple[dt.date, dt.date] | None, today: dt.date) -> tuple[str, str]:
    """(判定, 示す日付)。判定は match（期間に合う）・mismatch（日付はあるが期間に合わない）・unknown（日付が取れない）・any（照合しない）。"""
    dates = extract_dates(f"{meta_date}\n{text[:6000]}", today, url)
    if window is None:
        return "any", (min(dates, key=lambda x: abs((x - today).days)).isoformat() if dates else "")
    start, end = window
    inside = [d for d in dates if start <= d <= end]
    if inside:
        return "match", max(inside).isoformat()
    if dates:
        past = [d for d in dates if d <= today]
        return "mismatch", (max(past) if past else min(dates)).isoformat()
    return "unknown", ""
