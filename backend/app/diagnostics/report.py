"""診断の一式を 1 つにまとめる。AI に渡す前提で、秘密は伏せる。"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.diagnostics import journal, recorder, selftest, store

_KEYWORD = (r"(?:[\w.-]*(?:token|secret|passw(?:or)?d|passwd|api[_-]?key|apikey|auth|cookie|session|credential|"
            r"private[_-]?key|signature|key)[\w.-]*|sig)")
_SECRETS = [
    # Authorization: Bearer/Basic/Token <値>
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    # 既知の形の鍵（ControlDeck のゲートウェイ cdk-、OpenAI/OpenRouter sk-、GitHub、Slack、Hugging Face）
    (re.compile(r"\b(?:cdk|sk|pk|rk|ghp|gho|ghs|ghu|github_pat|xox[abprs]|hf)[-_][A-Za-z0-9_-]{8,}"), "***"),
    # JWT
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "***"),
    # 鍵らしい名前の後の値: key=v / key: v / "key": "v" / 'key': 'v' / ?sig=v
    # （区切りは : か = だけ。空白区切りまで拾うと「SIGTERM received」のような文まで伏せてしまう）
    (re.compile(r"(?i)(?<![A-Za-z])(" + _KEYWORD + r")([\"']?\s*[:=]\s*[\"']?)(?!\*\*\*)[^\s\"',;&}]{4,}"), r"\1\2***"),
    # コマンドの引数: --api-key v / --token v / --password v
    (re.compile(r"(?i)(--[\w-]*(?:token|secret|password|passwd|api-key|apikey|key)\b)(\s+)(?!\*\*\*)[^\s\"']{4,}"), r"\1\2***"),
    # メールアドレス
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value):
    """dict / list をたどって文字列の値ごとに伏せる（JSON 文字列にかけると引用符の escape を壊す）。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: ("***" if isinstance(key, str) and re.fullmatch("(?i)" + _KEYWORD, key)
                      and key not in _SAFE_KEYS and isinstance(item, str) else redact_value(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


# 名前に key/session などを含むが、値が秘密でない欄
_SAFE_KEYS = {"kind", "key", "route", "session_key", "monkey"}


def redact_event(event: dict) -> dict:
    return redact_value(event)


def _fmt(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%m-%d %H:%M:%S%z") if ts else "-"


def _version() -> dict:
    repo = Path(__file__).resolve().parents[3]
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                  timeout=5, check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {"commit": git("rev-parse", "--short", "HEAD"), "describe": git("describe", "--tags", "--always"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "python": platform.python_version(),
            "kernel": platform.release(), "pid": os.getpid()}


def _failed_jobs(limit: int = 20) -> list[dict]:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Job as JobRecord

    with SessionLocal() as db:
        rows = db.execute(select(JobRecord).where(JobRecord.status == "failed")
                          .order_by(JobRecord.created_at.desc()).limit(limit)).scalars().all()
        def ts(value):  # DB の時刻はタイムゾーン無しの UTC
            return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() if value else None

        return [{"ts": ts(row.created_at), "kind": row.kind,
                 "title": row.title, "error": (row.error or "")[:400]} for row in rows]


def build(hours: float = 24.0, include_tests: bool = False) -> dict:
    since = time.time() - hours * 3600
    events = store.read(since=since, limit=None)
    logs = [e for e in events if e.get("kind") == "log"]
    grouped: dict[str, dict] = {}
    for event in logs:
        key = f"{event.get('level')}|{event.get('logger')}|{re.sub(r'[0-9a-f]{8,}|\\d+', '#', event.get('message', ''))[:160]}"
        row = grouped.setdefault(key, {"level": event.get("level"), "logger": event.get("logger"),
                                       "message": event.get("message", "")[:300], "count": 0,
                                       "first": event["ts"], "last": event["ts"], "traceback": ""})
        row["count"] += 1
        row["first"] = min(row["first"], event["ts"])
        row["last"] = max(row["last"], event["ts"])
        if event.get("traceback") and not row["traceback"]:
            row["traceback"] = event["traceback"]
    stalls = [e for e in events if e.get("kind") == "loop_stall"]
    stall_groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0})
    for stall in stalls:
        stack = stall.get("stack") or []
        key = next((line for line in reversed(stack) if "/backend/app/" in line), stack[-1] if stack else "(不明)")
        group = stall_groups[key]
        group["count"] += 1
        group["total_seconds"] += stall.get("seconds", 0)
        group["max_seconds"] = max(group["max_seconds"], stall.get("seconds", 0))
        group["stack"] = stack
    slow = Counter(e.get("route") for e in events if e.get("kind") == "slow_request")
    data = {
        "generated_at": time.time(), "hours": hours, "version": _version(),
        "selftest": selftest.latest() or selftest.run(record=False),
        "journal": journal.cached_summary(hours=max(hours, 72.0)),
        "errors": sorted(grouped.values(), key=lambda row: row["count"], reverse=True)[:40],
        "loop_stalls": sorted(({"where": k, **v} for k, v in stall_groups.items()),
                              key=lambda row: row["total_seconds"], reverse=True)[:15],
        "slow_requests": [{"route": route, "count": count} for route, count in slow.most_common(20)],
        "route_stats": recorder.route_stats()[:25],
        "memory_now": recorder.memory_sample(top=10),
        "memory_low": [e for e in events if e.get("kind") == "memory_low"][-10:],
        "failed_jobs": _failed_jobs(),
        "tests": next((e for e in events if e.get("kind") == "tests"), None),
    }
    if include_tests:
        data["tests"] = selftest.run_tests()
    return redact_value(json.loads(json.dumps(data, ensure_ascii=False, default=str)))


def to_markdown(data: dict) -> str:
    v = data["version"]
    out = [f"# ControlDeck 診断レポート（{_fmt(data['generated_at'])}、直近 {data['hours']:.0f} 時間）", "",
           f"- 版: {v.get('describe')}（{v.get('branch')} {v.get('commit')}）、Python {v.get('python')}、kernel {v.get('kernel')}", ""]
    st = data["selftest"]
    out += [f"## 自己診断: {st['overall']}", ""]
    out += [f"- [{c['status']}] {c['name']}: {c['detail']}" for c in st["checks"]] + [""]
    j = data["journal"]
    out += [f"## 本体の再起動（直近 {j['hours']:.0f} 時間、{len(j['restarts'])} 回）", ""]
    causes = Counter(r["cause"] for r in j["restarts"])
    out += [f"- 内訳: " + "、".join(f"{k} {n}" for k, n in causes.most_common())] if causes else ["- なし"]
    out += [f"- {_fmt(r['ts'])} {r['cause']}：{' / '.join(r.get('details', [])[-2:])}"
            for r in j["restarts"] if r["cause"] not in ("更新", "手動・通常の再起動")][-10:] + [""]
    out += ["## OOM で強制終了されたプロセス", ""]
    out += [f"- {_fmt(o['ts'])} {o['process']}（{o['unit']}）anon {o['anon_rss_gb']} GiB / shmem {o['shmem_rss_gb']} GiB"
            for o in j["oom_kills"]] or ["- なし"]
    out += ["", "## 管理下の常駐の異常終了", ""]
    fails = Counter((f["unit"], f["kind"]) for f in j["unit_failures"] if f["kind"] in ("failed", "oom", "watchdog"))
    out += [f"- {unit}: {kind} × {n}" for (unit, kind), n in fails.most_common(15)] or ["- なし"]
    out += ["", "## event loop の詰まり（画面が遅くなる原因）", ""]
    for s in data["loop_stalls"]:
        out += [f"- {s['where']}: {s['count']} 回、計 {s['total_seconds']:.1f} 秒、最長 {s['max_seconds']:.1f} 秒",
                "  ```", *("  " + line for line in s.get("stack", [])[-8:]), "  ```"]
    if not data["loop_stalls"]:
        out += ["- 記録なし"]
    out += ["", "## 遅い API（p95 の大きい順）", ""]
    out += [f"- {r['route']}: p50 {r['p50']}s / p95 {r['p95']}s / max {r['max']}s（{r['count']} 回）"
            for r in data["route_stats"][:15]] or ["- 記録なし"]
    out += ["", "## 警告・エラー（まとめ）", ""]
    for e in data["errors"][:25]:
        out.append(f"- [{e['level']}] {e['logger']}: {e['message']}（{e['count']} 回、最後 {_fmt(e['last'])}）")
        if e.get("traceback"):
            out += ["  ```", *("  " + line for line in e["traceback"].strip().splitlines()[-12:]), "  ```"]
    if not data["errors"]:
        out += ["- 記録なし"]
    m = data["memory_now"]
    out += ["", f"## メモリ（いま）: 空き {m['available_gb']} GiB / {m['total_gb']} GiB、swap {m['swap_used_gb']} GiB", ""]
    out += [f"- VRAM {g['card']}: {g['used_gb']} / {g['total_gb']} GiB" for g in m["vram"]]
    out += [f"- {p['rss_gb']} GiB {p['name']}（{p['unit'] or '-'}）" for p in m["top"]]
    if data["memory_low"]:
        out += ["", "### 空きが少なかったとき", ""]
        for low in data["memory_low"]:
            top = "、".join(f"{p['name']} {p['rss_gb']}GiB" for p in low.get("top", [])[:4])
            out.append(f"- {_fmt(low['ts'])} 空き {low['available_gb']} GiB: {top}")
    out += ["", "## 失敗したジョブ（新しい順）", ""]
    out += [f"- {_fmt(jb['ts'])} {jb['kind']} {jb['title']}: {jb['error'][:200]}" for jb in data["failed_jobs"]] or ["- なし"]
    if data.get("tests"):
        t = data["tests"]
        out += ["", f"## テスト: {t.get('summary') or t.get('exit_code')}", ""] + [f"- {line}" for line in t.get("failed", [])[:20]]
    return redact("\n".join(out)) + "\n"
