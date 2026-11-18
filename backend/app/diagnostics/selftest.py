"""その場で確かめる自己診断。どれも読むだけで、数秒で終わる。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.config import data_dir
from app.diagnostics import store


def _check(name: str, fn) -> dict:
    started = time.monotonic()
    try:
        ok, detail = fn()
        status = "ok" if ok is True else "warn" if ok == "warn" else "fail"
    except Exception as exc:  # noqa: BLE001
        status, detail = "fail", f"{type(exc).__name__}: {exc}"[:300]
    return {"name": name, "status": status, "detail": detail, "seconds": round(time.monotonic() - started, 2)}


def _database():
    from sqlalchemy import text

    from app.database import engine

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA quick_check")).scalar()
    return result == "ok", f"quick_check: {result}"


def _disk():
    usage = shutil.disk_usage(data_dir())
    free = usage.free / usage.total
    detail = f"空き {usage.free / 2**30:.0f} GiB（{free:.0%}）"
    return (True if free >= 0.10 else "warn" if free >= 0.05 else False), detail


def _memory():
    import psutil

    vm = psutil.virtual_memory()
    detail = f"空き {vm.available / 2**30:.1f} GiB / {vm.total / 2**30:.1f} GiB、swap {psutil.swap_memory().used / 2**30:.1f} GiB"
    return (True if vm.available > 8 * 2**30 else "warn" if vm.available > 3 * 2**30 else False), detail


def _event_loop():
    recent = store.read(since=time.time() - 600, kinds={"loop_stall"})
    worst = max((event.get("seconds", 0) for event in recent), default=0)
    if not recent:
        return True, "直近 10 分に詰まりなし"
    return (True if worst < 3 else "warn"), f"直近 10 分に {len(recent)} 回、最長 {worst} 秒"


def _failed_units():
    try:
        out = subprocess.run(["systemctl", "--user", "--failed", "--no-legend", "--plain"],
                             capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "warn", "systemctl を実行できません"
    units = [line.split()[0] for line in out.splitlines() if line.strip()]
    return (not units) or "warn", ("失敗した unit なし" if not units else "失敗: " + ", ".join(units[:8]))


def _llms():
    from app.models_mgmt import local_llm

    rows = local_llm.list_instances()
    loaded = [row.get("alias") for row in rows if row.get("loaded")]
    return True, f"登録 {len(rows)}、読み込み中 {', '.join(map(str, loaded)) or 'なし'}"


def _frontend():
    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist" / "index.html"
    return dist.is_file(), "画面の build あり" if dist.is_file() else f"{dist} がありません"


def _tools():
    missing = [tool for tool in ("tmux", "podman", "journalctl", "git", "pdftoppm") if not shutil.which(tool)]
    return (not missing) or "warn", "そろっています" if not missing else "見つからない: " + ", ".join(missing)


def _collector():
    from app.maintenance.watchdog import heartbeat_age

    age = heartbeat_age("collector")
    if age is None:
        return "warn", "メトリクス収集の記録なし"
    return age < 120, f"メトリクス収集の最終更新 {age:.0f} 秒前"


def _fan():
    """ファン制御が目標から外れていないか（直近 24 時間の記録）。外れていたら「記録から作り直す」を勧める。"""
    from app.monitoring import fan_control

    if not fan_control._load().enabled:
        return True, "無効（GPU 標準の自動制御）"
    s = fan_control.log_summary(24.0)
    if not s["minutes"]:
        return "warn", "制御中の記録がありません"
    peak = s["peak_c"].get("hot")
    text = (f"直近 {s['minutes']} 分: 目標超え（+{fan_control.fan_log.DEVIATION_C:.0f}°C が 1 分以上）{s['deviations']} 回・"
            f"緊急の最大回転 {s['emergency_minutes']} 分・hotspot 最高 {peak if peak is not None else '—'}°C")
    if s["emergency_minutes"] or s["deviations"] >= DEVIATION_WARN:
        return "warn", text + "。設定のファン制御で「記録から作り直す」を試してください"
    return True, text


def _opencode_box():
    """OpenCode のプロジェクトの箱が、別のプロジェクトを見せていないか（/tmp が専用・CodeDEV を覆う）。
    2026-11-18: 全部の箱が /tmp を共有し、前に開いたプロジェクトのつなぎ口も見えていた（気付かれずに残っていた）。"""
    from app.integrations.opencode import provider
    from app.maintenance import sandbox

    if provider.isolation_mode() != "box":
        return True, "箱を使わない設定（isolation: host）"
    root = provider.codedev_root().resolve()
    probe = root / "selftest-probe"
    argv = sandbox.container_argv(["true"], workdir=probe, writable=[], binds=[(provider.project_tmp_dir(probe), Path("/tmp"))],
                                  hide=provider._sibling_projects(probe))
    shutil.rmtree(provider.project_tmp_dir(probe), ignore_errors=True)
    if not any(a.endswith(":/tmp:rw") for a in argv) or "--tmpfs" not in argv:
        return False, "箱の /tmp か CodeDEV の覆いが付いていません（別のプロジェクトが見えます）"
    tmp_root = data_dir() / "sandboxes" / "project-tmp"
    folders = [p for p in tmp_root.iterdir() if p.is_dir()] if tmp_root.is_dir() else []
    size = sum(f.stat().st_size for d in folders for f in d.rglob("*") if f.is_file())
    text = f"/tmp はプロジェクトごと・他のプロジェクトは見えない。下書きの置き場 {len(folders)} 個・{size / 2**30:.1f} GiB"
    if size > 10 * 2**30:
        return "warn", text + "（大きい。不要なプロジェクトの下書きを data/sandboxes/project-tmp から消してください）"
    return True, text


def _opencode_background():
    """終わった回答に「実行中」のままの裏のエージェントが残っていないか（結果を受け取り損ねた印）。
    2026-11-18: レビューを裏で依頼した後、親のターンの終わりで聞くのをやめ、結果が画面に出なかった。"""
    import json

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import ChatMessage

    db = SessionLocal()
    try:
        rows = db.execute(select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.status == "completed")
                          .order_by(ChatMessage.created_at.desc()).limit(50)).scalars().all()
        lost = 0
        for row in rows:
            try:
                tools = json.loads(row.meta_json or "{}").get("tools") or []
            except ValueError:
                continue
            lost += sum(1 for t in tools if t.get("name") in ("subagent", "task") and t.get("status") == "running")
    finally:
        db.close()
    if lost:
        return "warn", f"終わった回答に、実行中のままの裏のエージェントが {lost} 件あります（結果を受け取れていない可能性）"
    return True, "裏のエージェントの結果は受け取れています"


def _voice_input():
    """音声入力: どのエンジンか、Haas-ja が失敗して控えへ回っていないか。"""
    from app.workflows import asr

    if asr.last_fallback:
        how = "whisper で動きました" if asr.fallback_worked else "文字にできませんでした"
        return "warn", f"Haas-ja が失敗し、{how}: {asr.last_fallback}"
    if asr.last_install_error:
        return "warn", f"Haas-ja を導入できず whisper で動いています: {asr.last_install_error}"
    if not asr.is_ready():
        return True, "音声入力は未導入（初回に導入します）"
    return True, f"音声入力のエンジン: {'Haas-ja' if asr.haas_ready() else 'whisper（Haas-ja は未導入）'}"


def _chat_tool_text():
    """道具の無い回答に、呼び出しの文字（<tool_call>）が出ていないか（実行されたように見えて何も起きない）。"""
    import json

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import ChatMessage

    db = SessionLocal()
    try:
        rows = db.execute(select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.status == "completed")
                          .order_by(ChatMessage.created_at.desc()).limit(50)).scalars().all()
        count = 0
        for row in rows:
            try:
                mode = json.loads(row.meta_json or "{}").get("mode")
            except ValueError:
                mode = None
            count += int(mode != "code" and "<tool_call>" in (row.content or ""))
    finally:
        db.close()
    if count:
        return "warn", f"直近の回答 {count} 件で、実行されない道具の呼び出しが文字のまま出ています"
    return True, "道具の呼び出しの文字は出ていません"


def _opencode_todos():
    """OpenCode のアクションリストが、作業の進みに合わせて更新されているか。"""
    import json

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import ChatMessage

    db = SessionLocal()
    try:
        rows = db.execute(select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.status == "completed")
                          .order_by(ChatMessage.created_at.desc()).limit(50)).scalars().all()
    finally:
        db.close()
    stale = 0
    for row in rows:
        try:
            meta = json.loads(row.meta_json or "{}")
        except ValueError:
            continue
        todos = meta.get("todos") or []
        updated = max((t.get("updated_at") or 0 for t in todos), default=0)
        if updated and any(t.get("status") in ("pending", "in_progress") for t in todos) \
                and sum((t.get("started_at") or 0) > updated for t in meta.get("tools") or []) >= 20:
            stale += 1
    if stale:
        return "warn", f"直近の回答 {stale} 件で、アクションリストが更新されないまま作業が進みました"
    return True, "アクションリストは作業に合わせて更新されています"


def _chat_queue():
    """送信待ちの要望が、送られるはずなのに残っていないか（回答が正常に終わり、生成中のものも無いのに 2 分以上）。
    2026-11-18 生成中に送った要望をサーバーが貯めて、回答の後に送るようにした。送り損ねると気付けないため。"""
    from datetime import timedelta

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import ChatMessage, ChatQueuedRequest, utcnow

    db = SessionLocal()
    try:
        cutoff = utcnow() - timedelta(minutes=2)
        waiting = db.execute(select(ChatQueuedRequest).where(ChatQueuedRequest.status == "queued")).scalars().all()
        stuck: set[str] = set()
        failed = db.query(ChatQueuedRequest).filter(ChatQueuedRequest.status == "failed").count()
        for item in waiting:
            created = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=cutoff.tzinfo)
            if created > cutoff or item.conversation_id in stuck:
                continue
            last = db.execute(select(ChatMessage).where(
                ChatMessage.conversation_id == item.conversation_id, ChatMessage.role == "assistant",
            ).order_by(ChatMessage.created_at.desc()).limit(1)).scalar_one_or_none()
            if last is not None and last.status == "completed":
                stuck.add(item.conversation_id)
        if stuck:
            return "warn", f"回答が終わったのに送られていない送信待ちが {len(stuck)} 会話にあります（画面の「今すぐ送る」で送れます。続くなら不具合）"
        return True, f"送信待ち {len(waiting)} 件（送り損ねなし）" + (f"・送れなかった {failed} 件" if failed else "")
    finally:
        db.close()


def _maya_vision():
    """動いている Maya が画像を読めるかと、ControlDeck の判定（設定ファイル）が合っているか。
    2026-11-18: DeepSeek の画像を「読めない」と判定し続け、ゲートウェイの画像の解析が断っていた（気付かれずに残った）。"""


    import httpx

    from app.models_mgmt import maya_s

    rows = []
    for instance in maya_s.list_instances():
        if not instance.get("loaded"):
            continue
        try:
            state = httpx.get(f"http://127.0.0.1:{instance['port']}/health", timeout=3, trust_env=False).json()
        except (httpx.HTTPError, ValueError):
            continue
        engine = state.get("images")
        if isinstance(engine, bool) and engine != bool(instance.get("vision")):
            rows.append(f"{instance['alias']}: エンジン {'読める' if engine else '読めない'}・判定 {'読める' if instance.get('vision') else '読めない'}")

    if rows:
        return "warn", "画像の読み取りが食い違っています（起動し直すと揃います。揃わなければ不具合）: " + "、".join(rows)
    return True, "動いている Maya の画像の読み取りと判定は一致"


# 目標超えがこの回数を超えたら警告（全力の始まりの 1 回ずつは起こりうるので、数回までは許す）
DEVIATION_WARN = 3


CHECKS = [("データベース", _database), ("ディスク", _disk), ("メモリ", _memory), ("応答（event loop）", _event_loop),
          ("常駐サービス", _failed_units), ("ローカル LLM", _llms), ("画面の build", _frontend),
          ("必要なコマンド", _tools), ("メトリクス収集", _collector), ("ファン制御", _fan), ("OpenCode の箱", _opencode_box),
          ("Maya の画像の読み取り", _maya_vision),
          ("AI アシスタントの送信待ち", _chat_queue),
          ("道具の呼び出しの文字", _chat_tool_text),
          ("OpenCode のアクションリスト", _opencode_todos),
          ("OpenCode の裏のエージェント", _opencode_background),
          ("音声入力", _voice_input)]


_latest: dict | None = None
_tests_lock = threading.Lock()


def latest(max_age: float = 300.0) -> dict | None:
    """直近の自己診断（5 分以内）。概要を開くたびに走らせない。"""
    if _latest and time.time() - _latest["ts"] < max_age:
        return _latest
    return None


def run(record: bool = True) -> dict:
    global _latest
    results = [_check(name, fn) for name, fn in CHECKS]
    overall = "fail" if any(r["status"] == "fail" for r in results) else \
        "warn" if any(r["status"] == "warn" for r in results) else "ok"
    if record:
        store.record("selftest", overall=overall, failed=[r["name"] for r in results if r["status"] != "ok"])
    _latest = {"ts": time.time(), "overall": overall, "checks": results, "pid": os.getpid()}
    return _latest


_SELECTION = re.compile(r"tests/[A-Za-z0-9_/]+\.py(?:::[A-Za-z0-9_\[\]-]+)*")


def check_selection(selection: str) -> list[str]:
    """テストの指定は tests/ の下のファイル（::テスト名 つき可）だけ。pytest のオプションは通さない
    （--basetemp に本番の data を渡すと pytest がそこを消す）。"""
    parts = selection.split()
    for part in parts:
        if not _SELECTION.fullmatch(part) or ".." in part:
            raise ValueError(f"テストの指定は tests/… の .py（::テスト名 つき可）だけです: {part[:60]}")
    return parts


def tests_running() -> bool:
    return _tests_lock.locked()


def run_tests(selection: str = "", timeout: int = 1200) -> dict:
    """backend のテストを、本番のデータに触らない形で流す（CONTROL_DECK_CONFIG を外す）。"""
    backend = Path(__file__).resolve().parents[2]
    venv_python = backend.parent / ".venv" / "bin" / "python"
    python = str(venv_python if venv_python.exists() else Path(os.sys.executable))
    args = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--maxfail=30", *check_selection(selection)]
    if not _tests_lock.acquire(blocking=False):
        raise RuntimeError("テストはもう実行中です")
    import tempfile

    sandbox = tempfile.mkdtemp(prefix="cd-diag-tests-")
    # 本番と同じ機械で動くので、機械全体に効く処理（ファン制御・常駐の復元・自動更新・モデルの自動起動）を
    # 止める CONTROL_DECK_NO_MACHINE_TASKS を付け、HOME や tmux・systemd の置き場も一時の場所へ向ける。
    env = {key: value for key, value in os.environ.items()
           if key not in ("CONTROL_DECK_CONFIG", "NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID",
                          "DBUS_SESSION_BUS_ADDRESS", "TMUX", "TMUX_PANE")}
    env.update({"CONTROL_DECK_NO_MACHINE_TASKS": "1", "HOME": sandbox, "TMUX_TMPDIR": sandbox,
                "XDG_RUNTIME_DIR": sandbox, "XDG_CONFIG_HOME": f"{sandbox}/.config",
                "XDG_DATA_HOME": f"{sandbox}/.local/share", "XDG_STATE_HOME": f"{sandbox}/.local/state"})
    started = time.monotonic()
    try:
        result = subprocess.run(args, cwd=backend, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        output, code = result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        output, code = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw), -1
    finally:
        _tests_lock.release()
        import shutil as _shutil
        _shutil.rmtree(sandbox, ignore_errors=True)
    lines = output.strip().splitlines()
    summary = next((line for line in reversed(lines) if " passed" in line or " failed" in line or " error" in line), "")
    failed = [line for line in lines if line.startswith(("FAILED", "ERROR"))][:50]
    report = {"ts": time.time(), "exit_code": code, "summary": summary, "failed": failed,
              "seconds": round(time.monotonic() - started, 1), "tail": "\n".join(lines[-80:])}
    store.record("tests", exit_code=code, summary=summary, failed=failed[:20])
    return report
