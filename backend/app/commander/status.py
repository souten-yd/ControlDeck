"""AI Commander のコンソールの状態を、LLM を使わずに画面の文字から判定する（C2）。

画面 = tmux の capture-pane の最後の数十行（色の印なし）。CLI ごとの小さな表（正規表現）で
「作業中・入力待ち・上限で停止・エラー・終了」を決め、画面の飾り（枠・入力欄・操作の案内）を
除いた最後の行を添える。

判定の順（先に当たったものが勝つ）:
1. 終了: tmux の pane が死んでいる、または CLI が終わってシェルだけが残っている。
2. 上限で停止: 上限の文言が画面の下の方にある（Claude「You've hit your 5-hour limit · resets …」、
   Codex「You've hit your usage limit … Try again at …」、OpenRouter・Zen の 429 など）。
3. エラー: 画面の一番下の数行に API のエラーの文言がある。
4. 作業中: 中断の案内（「esc to interrupt」・「esc interrupt」）が出ている。
5. 入力待ち: それ以外（CLI は動いていて、入力を待っている）。

根拠: 2026-11-27 に実機の画面で確かめた（Claude Code 2.1.332 の作業中、Codex 0.172.0・
OpenCode v2.4.2 の入力待ち）。上限の文言は C0（c1-spec.md「実機で確かめた事実」）。
読めない・判定できない場合は「不明」にせず「入力待ち」に倒す（誤って「作業中」と出して
自動再開（C3）の判断を狂わせない）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.terminals.manager import IDLE_COMMANDS

WORKING = "working"
WAITING = "waiting"
LIMITED = "limited"
ERROR = "error"
EXITED = "exited"
STATES = (WORKING, WAITING, LIMITED, ERROR, EXITED)

CAPTURE_LINES = 60
# 上限の文言を探す範囲（下から）。古い上限の表示が履歴に残っていても、新しい作業で流れたら外れる。
LIMIT_WINDOW = 15
# エラーを探す範囲（下から）。会話の本文に出た「Error」を拾わないよう狭くする。
ERROR_WINDOW = 6
LAST_LINE_MAX = 160


@dataclass(frozen=True)
class Rules:
    working: re.Pattern[str]
    limit: re.Pattern[str]
    error: re.Pattern[str]
    # 最後の行から外す画面の飾り（入力欄・操作の案内・状態の行）
    chrome: re.Pattern[str]


# 「esc to i…」: 幅が狭いと Claude の案内が途中で切れる（2026-11-27 実機）。
# Claude の作業中の行「· Drizzling… (2m 39s · …」（記号＋動詞…＋経過時間）も作業中の印にする。
_COMMON_WORKING = (r"esc to interrupt|esc interrupt|esc to i…|esc to i\.\.\."
                   r"|^\s*[·✻✽✶✳✢*•]\s+\S+…\s*\(\d+(?:m \d+)?s\b")
_COMMON_LIMIT = (r"hit your (?:\d+-hour |weekly |usage |session )?limit|usage limit (?:reached|exceeded)"
                 r"|rate limit(?:ed| exceeded)|Try again at |quota exceeded|insufficient (?:credits|quota)"
                 # Claude「resets 10:20pm (Asia/Tokyo)」「resets Nov 30, 12am」
                 r"|resets (?:\d{1,2}(?::\d{2})?\s?[ap]m|[A-Z][a-z]{2} \d{1,2})"
                 # 本文の「429 tokens」などを拾わないよう、HTTP の言い方だけ
                 r"|\b429 Too Many|status(?: code)?:? 429|error 429")
_COMMON_ERROR = r"API Error|APIError|Request failed|Connection (?:error|refused)|ECONNREFUSED|Internal Server Error"
_COMMON_CHROME = (r"^[\s─━═╭╮╰╯│┃╹▀▄█▌▐┌┐└┘├┤·•]*$"           # 枠・罫線だけの行
                  r"|^\s*[❯›>]\s*$"                             # 空の入力欄
                  r"|\? for shortcuts|shift\+tab|ctrl\+p commands|auto mode|esc to i"
                  r"|Ask Codex to do anything|Ask anything|for agents"
                  r"|^\s*⏵⏵|bypass permissions|accept edits"
                  r"|Press up to edit queued|Queued · ctrl|Tip: |pre-deny bash")

RULES: dict[str, Rules] = {
    cli: Rules(
        working=re.compile(_COMMON_WORKING, re.IGNORECASE),
        limit=re.compile(_COMMON_LIMIT, re.IGNORECASE),
        error=re.compile(_COMMON_ERROR, re.IGNORECASE),
        chrome=re.compile(_COMMON_CHROME, re.IGNORECASE),
    )
    for cli in ("claude", "codex", "opencode", "gemini")
}


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def last_line(cli: str, text: str) -> str:
    """画面の飾りを除いた最後の行（長いものは切る）。無ければ空。"""
    rules = RULES.get(cli)
    for line in reversed(_lines(text)):
        if rules and rules.chrome.search(line):
            continue
        cleaned = re.sub(r"\s+", " ", line.strip(" │┃"))
        if re.search(r"[0-9A-Za-z぀-ヿ一-鿿]", cleaned):
            return cleaned[:LAST_LINE_MAX]
    return ""


def limit_text(cli: str, text: str) -> str:
    """上限の文言の行（C3 の回復の時刻の読み取りに使う）。無ければ空。"""
    rules = RULES.get(cli)
    if not rules:
        return ""
    for line in reversed(_lines(text)[-LIMIT_WINDOW:]):
        if rules.limit.search(line):
            return line.strip()[:LAST_LINE_MAX]
    return ""


def classify(cli: str, text: str, *, alive: bool = True, program: str = "") -> dict:
    """{state, last_line, detail}。detail は上限・エラーの行（無ければ空）。"""
    rules = RULES.get(cli)
    line = last_line(cli, text)
    if not alive or (program and program in IDLE_COMMANDS):
        return {"state": EXITED, "last_line": line, "detail": ""}
    if rules is None:
        return {"state": WAITING, "last_line": line, "detail": ""}
    lines = _lines(text)
    working = any(rules.working.search(item) for item in lines[-8:])
    limit = limit_text(cli, text)
    # 作業中の表示が出ていれば、履歴に残った上限の文言より作業中を優先する（再開した後）。
    if limit and not working:
        return {"state": LIMITED, "last_line": line, "detail": limit}
    for item in reversed(lines[-ERROR_WINDOW:]):
        if rules.error.search(item) and not working:
            return {"state": ERROR, "last_line": line, "detail": item.strip()[:LAST_LINE_MAX]}
    if working:
        return {"state": WORKING, "last_line": line, "detail": ""}
    return {"state": WAITING, "last_line": line, "detail": ""}
