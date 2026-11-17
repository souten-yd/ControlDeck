"""OpenCode の TUI: 閉じても一覧に残り、続きから開ける。"""
import pytest

from tests.test_chat_persist import CSRF_HEADERS


def test_tui_command_adds_resume_flags(monkeypatch, tmp_path):
    from app.integrations.opencode import provider as op

    seen = {}

    def fake_launch(runtime, argv, project, config, interactive):
        seen["argv"] = list(argv)
        return argv, {}

    monkeypatch.setattr(op, "_launch", fake_launch)
    monkeypatch.setattr(op, "_runtime_config", lambda *a, **k: tmp_path)
    monkeypatch.setattr(op, "_api_key_for", lambda base_url: "cdk-test")
    monkeypatch.setattr(op, "active_binary", lambda runtime: ("v2", tmp_path / "opencode2"))
    monkeypatch.setattr(op, "get_settings", lambda: {"base_url": "http://127.0.0.1:1/v1", "model": "auto"})
    monkeypatch.setattr(op.files, "resolve", lambda raw: __import__("pathlib").Path(raw))
    monkeypatch.setattr(op, "_managed_project_id", lambda project: None, raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    for resume, flags in (("", []), ("last", ["--continue"]), ("ses_abcdEFGH1234", ["--session", "ses_abcdEFGH1234"])):
        op.tui_command(project_path=str(project), resume=resume, runtime="v2")
        argv = seen.pop("argv")
        for flag in ("--continue", "--session"):
            assert (flag in argv) == (flag in flags)
        if flags[:1] == ["--session"]:
            assert argv[argv.index("--session") + 1] == "ses_abcdEFGH1234"
    with pytest.raises(op.CodeAgentError):
        op.tui_command(project_path=str(project), resume="ses_x; rm -rf /", runtime="v2")


def test_closed_session_stays_listed_and_can_be_forgotten(monkeypatch, tmp_path):
    """OpenCode のルーターは機能を入れたときだけ載るので、手続きを直に呼ぶ。"""
    from types import SimpleNamespace

    from app.integrations.opencode import router as oc_router
    from app.integrations.opencode import tui_history
    from app.terminals.manager import manager

    monkeypatch.setattr(tui_history, "data_dir", lambda: tmp_path / "data")
    project, other = tmp_path / "Train", tmp_path / "Other"
    project.mkdir()
    other.mkdir()
    alive: dict = {}
    monkeypatch.setattr(manager, "list_sessions", lambda: list(alive.values()))
    user = SimpleNamespace(id=1)

    def start(tid, path, resume=""):
        alive[tid] = {"id": tid, "attached": False, "persistent": True}
        tui_history.record(tid, str(path), 1, resume=resume)

    start("t1", project)
    start("t2", other)
    tui_history.record("t9", str(project), 2)                      # 別の利用者のものは出さない
    listed = oc_router.list_tui_sessions(user=user)["sessions"]
    assert sorted(s["terminal_id"] for s in listed) == ["t1", "t2"] and all(s["running"] for s in listed)

    alive.pop("t1")                                                 # TUI を閉じた（tmux ごと終わる）
    listed = oc_router.list_tui_sessions(user=user)["sessions"]
    assert [(s["project_name"], s["running"]) for s in listed if not s["running"]] == [("Train", False)]

    start("t3", project, resume="last")                             # 続きから開いた
    listed = oc_router.list_tui_sessions(user=user)["sessions"]
    assert all(s["running"] for s in listed) and len(listed) == 2   # 閉じた同じプロジェクトは隠す

    alive.clear()
    oc_router.forget_tui_sessions(oc_router.ForgetBody(project_path=str(project)), user=user)
    listed = oc_router.list_tui_sessions(user=user)["sessions"]
    assert [s["project_name"] for s in listed] == ["Other"]


def test_resume_last_skips_assistant_sessions(admin_client, monkeypatch, tmp_path):
    """「続きから開く」は AI アシスタントの会話を掴まない（同じ会話を 2 つのサーバーが書かない）。"""
    import json

    from fastapi import HTTPException

    from app.database import SessionLocal
    from app.integrations.opencode import provider as op
    from app.integrations.opencode import router as oc_router
    from app.models import ChatMessage, Conversation

    project = tmp_path / "Train"
    project.mkdir()
    monkeypatch.setattr("app.files.service.resolve", lambda raw, **kw: __import__("pathlib").Path(raw))
    rows = [{"id": "ses_chatchatchat01", "directory": str(project), "updated": 3},
            {"id": "ses_tuituituitui01", "directory": str(project), "updated": 2}]
    monkeypatch.setattr(op, "project_sessions", lambda project, runtime="": rows)
    with SessionLocal() as db:
        db.add(Conversation(id="convresume000001", owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id="msgresume0000001", conversation_id="convresume000001", role="assistant",
                           content="x", meta_json=json.dumps({"opencode_session": "ses_chatchatchat01"})))
        db.commit()
        assert oc_router._resolve_resume("last", str(project), "v2", db) == "ses_tuituituitui01"
        with pytest.raises(HTTPException) as refused:
            oc_router._resolve_resume("ses_chatchatchat01", str(project), "v2", db)
        assert refused.value.status_code == 409
        rows.pop()                                                  # TUI の会話が無ければ新しく始める
        assert oc_router._resolve_resume("last", str(project), "v2", db) == ""
