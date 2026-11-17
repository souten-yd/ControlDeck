"""AI アシスタントの自動判定の正しさを測る（ルール + LLM）。

    cd backend && .venv/bin/python -m app.workflows.routing_bench --base-url http://127.0.0.1:8096/v1 --model auto

tests/data/assistant_routing_cases.json の依頼を decide() に通し、期待した mode との一致率と
判定時間を出す。LLM を使うので、テストではなく手で流す（モデルを変えたときの比較用）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

CASES = Path(__file__).resolve().parents[2] / "tests" / "data" / "assistant_routing_cases.json"


async def run(base_url: str, model: str, api_key: str = "") -> dict:
    from app.workflows.assistant_planner import decide

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        started = time.monotonic()
        plan = await decide(case["q"], base_url, model, api_key)
        rows.append({"q": case["q"], "expected": case["mode"], "got": plan.mode, "by": plan.decided_by,
                     "ok": plan.mode == case["mode"], "seconds": round(time.monotonic() - started, 2)})
    ok = sum(r["ok"] for r in rows)
    llm = [r for r in rows if r["by"] == "llm"]
    return {"accuracy": round(ok / len(rows), 3), "correct": ok, "total": len(rows),
            "llm_accuracy": round(sum(r["ok"] for r in llm) / len(llm), 3) if llm else None,
            "llm_seconds_avg": round(sum(r["seconds"] for r in llm) / len(llm), 2) if llm else None,
            "wrong": [r for r in rows if not r["ok"]]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.base_url, args.model, args.api_key)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
