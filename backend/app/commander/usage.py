"""Nexarch: アカウントごとの 5 時間・週の上限の使用率と回復の時刻（C4 の一部・LLM なし）。

- Claude Code: Nexarch が開くコンソールに `--settings` で statusline のコマンド（statusline.py）を渡し、
  Claude Code が渡す `rate_limits.five_hour / seven_day` を `data/ai-commander/usage/claude-<アカウント>.json`
  へ書かせて、それを読む。ログインの鍵は読まない。statusline は応答のたびに更新される。
- Codex CLI: アカウントの CODEX_HOME（既定は ~/.codex）の sessions の記録（rollout-*.jsonl）の最後の
  `rate_limits`（primary＝5 時間・secondary＝週、used_percent・resets_at）を読む。
- 回復の時刻を過ぎた窓は使用率 0 と見なす（次の応答で新しい値が来るまで）。
根拠: 2026-11-27 利用者「5 時間制限と週間制限の利用率とリセットまでの時間を追加して」。形は実機で確かめた
（Claude Code 2.1.332 の statusline の説明、Codex 0.172.0 の rollout の rate_limits）。
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

from app.commander import accounts
from app.config import data_dir

STATUSLINE_SCRIPT = Path(__file__).resolve().parent / "statusline.py"
# Codex の記録は大きくなるので、新しいファイルの末尾だけを見る
CODEX_TAIL_BYTES = 256 * 1024
CODEX_FILES = 5


def usage_dir() -> Path:
    path = data_dir() / "ai-commander" / "usage"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def claude_usage_file(account: str) -> Path:
    return usage_dir() / f"claude-{accounts.validate_name(account) if account != accounts.DEFAULT_ACCOUNT else account}.json"


def claude_settings_file(account: str) -> Path:
    """Claude に --settings で渡すファイル（statusline のコマンドだけ）。開くたびに書き直す。"""
    out = claude_usage_file(account)
    command = " ".join(shlex.quote(part) for part in (sys.executable, "-I", str(STATUSLINE_SCRIPT), str(out)))
    path = usage_dir() / f"claude-settings-{out.stem.removeprefix('claude-')}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"statusLine": {"type": "command", "command": command, "padding": 0}}), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def _window(raw: object, now: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent", raw.get("used_percentage"))
    resets = raw.get("resets_at")
    if not isinstance(used, (int, float)):
        return None
    resets_at = float(resets) if isinstance(resets, (int, float)) else None
    if resets_at is not None and resets_at <= now:
        return {"used_percent": 0.0, "resets_at": None}   # 回復した（次の応答で新しい値が来る）
    return {"used_percent": round(float(used), 1), "resets_at": resets_at}


def claude_usage(account: str, now: float | None = None) -> dict | None:
    now = now or time.time()
    try:
        raw = json.loads(claude_usage_file(account).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    five, week = _window(raw.get("five_hour"), now), _window(raw.get("seven_day"), now)
    if not five and not week:
        return None
    return {"five_hour": five, "seven_day": week, "updated_at": raw.get("updated_at"), "source": "claude-statusline"}


def _codex_home(account: str) -> Path:
    if account == accounts.DEFAULT_ACCOUNT:
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return accounts.account_dir(accounts.CLIS["codex"], accounts.validate_name(account))


def codex_usage(account: str, now: float | None = None) -> dict | None:
    now = now or time.time()
    sessions = _codex_home(account) / "sessions"
    if not sessions.is_dir():
        return None
    try:
        files = sorted(sessions.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:CODEX_FILES]:
        try:
            with path.open("rb") as handle:
                size = path.stat().st_size
                handle.seek(max(0, size - CODEX_TAIL_BYTES))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            if '"rate_limits"' not in line:
                continue
            try:
                limits = _find_rate_limits(json.loads(line))
            except ValueError:
                continue
            if limits:
                five, week = _window(limits.get("primary"), now), _window(limits.get("secondary"), now)
                if five or week:
                    return {"five_hour": five, "seven_day": week, "updated_at": path.stat().st_mtime,
                            "source": "codex-sessions"}
    return None


def _find_rate_limits(value: object) -> dict | None:
    """JSON の中の最初の rate_limits（入れ子の深さは記録の版で変わるので探す）。"""
    if isinstance(value, dict):
        found = value.get("rate_limits")
        if isinstance(found, dict):
            return found
        for child in value.values():
            hit = _find_rate_limits(child)
            if hit:
                return hit
    elif isinstance(value, list):
        for child in value:
            hit = _find_rate_limits(child)
            if hit:
                return hit
    return None


def usage_for(cli_id: str, account: str) -> dict | None:
    """そのアカウントの使用率（取れない CLI・まだ記録が無いときは None）。"""
    try:
        if cli_id == "claude":
            return claude_usage(account)
        if cli_id == "codex":
            return codex_usage(account)
    except (OSError, ValueError, KeyError):
        return None
    return None
