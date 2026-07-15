"""systemd timerから呼ばれる電源予約ワーカー。Webプロセスには属さない。"""
from __future__ import annotations

from app.audit import service as audit
from app.database import SessionLocal
from app.power import scheduler
from app.power.router import _execute


def main() -> int:
    state = scheduler.read_state()
    if state is None:
        return 2
    action = state["action"]
    scheduler.update_status("executing")
    db = SessionLocal()
    try:
        audit.record(
            db, "power.schedule_execute", username=state.get("by", ""),
            resource_type="system", metadata={"action": action, "at": state["at"]},
        )
        ok, error = _execute(action)
        scheduler.update_status("completed" if ok else "failed", error)
        audit.record(
            db, f"power.{action}", username=state.get("by", ""), resource_type="system",
            result="success" if ok else "failure",
            metadata={"scheduled": True, **({} if ok else {"error": error[:300]})},
        )
        # 一度限りの予約なので、結果の状態ファイルだけを残してunitは回収する。
        scheduler.cancel(ignore_errors=True, keep_state=True)
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
