"""Claude Code の statusline のコマンド（Nexarch が開いた Claude のコンソールだけで使う）。

Claude Code は statusline のコマンドへ、画面の状態を JSON で標準入力に渡す。その中の
`rate_limits.five_hour` / `seven_day`（使用率と回復の時刻）と model の名前だけを、引数のファイルへ
書く（0600・置き換え）。ログインの鍵は読まない（Claude Code が渡す JSON に鍵は入っていない）。
標準出力の 1 行は Claude の画面の下に出る（「5h 12% · 7d 40%」）。

呼び出し: `python -I statusline.py <書き出すファイル>`（commander/usage.py が --settings で渡す）。
標準ライブラリだけを使う（ControlDeck の import を避け、起動を速くする）。
"""
from __future__ import annotations

import json
import os
import sys
import time


def _window(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percentage", raw.get("used_percent"))
    resets = raw.get("resets_at")
    if not isinstance(used, (int, float)):
        return None
    return {"used_percent": float(used), "resets_at": float(resets) if isinstance(resets, (int, float)) else None}


def main(argv: list[str]) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    limits = limits if isinstance(limits, dict) else {}
    model = data.get("model") if isinstance(data, dict) else None
    record = {
        "five_hour": _window(limits.get("five_hour")),
        "seven_day": _window(limits.get("seven_day")),
        "model": str((model or {}).get("display_name") or (model or {}).get("id") or "") if isinstance(model, dict) else "",
        "updated_at": time.time(),
    }
    if len(argv) > 1 and (record["five_hour"] or record["seven_day"]):
        path = argv[1]
        temp = f"{path}.tmp{os.getpid()}"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            os.replace(temp, path)
        except OSError:
            pass
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        if record[key]:
            parts.append(f"{label} {record[key]['used_percent']:.0f}%")
    print(" · ".join(parts) or (record["model"] or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
