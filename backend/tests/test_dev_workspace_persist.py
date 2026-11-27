"""開発の作業場の「保存と中断・再開」（2026-11-22）。

目的: 作業場のコードがいつ保存されるか（1 ターンの終わりの自動保存・起動時の立て直し）、停電や再起動で
どうなるか（残った .git/index.lock を消す・生成中のまま止まった会話は「中断」・未保存の変更は保存）、
main の進み（取り込みの前に箱の中で main を取り込む・ぶつかったら止めてファイルを記録）が指示どおりに
動くこと。どの処理でもホストの git が箱の作業ツリーの中で動かない（external fixture の検査）。
根拠: 2026-11-22 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。途中で停電や再起動した
場合はどうなるのか。中断して再開できるのか。他にも考えられる割り込みのシチュエーションを想定した上で、
問題がないように実装してほしい」。
git は本物（一時のリポジトリ）。箱の中の実行・外部への fetch は fixture の代役（origin はローカルの bare）。
"""
import json
import subprocess

import pytest
from sqlalchemy import select

from app.maintenance import sandbox, workspaces
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspaces import BOX_CHECK, api, repo  # noqa: F401  (fixture)
from tests.test_dev_workspace_adopt import external  # noqa: F401  (fixture: _host_git の検査)

PASS_REPORT = "[合格] テスト: 3 件\n[未実施] 依存: なし\n\n合格"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=a@b.invalid", "-c", "user.name=t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def box(repo, monkeypatch):  # noqa: F811
    made = workspaces.create("admin", "controldeck", "ws1")
    return sandbox.load(made["name"])


def make_origin(tmp_path, ahead: int) -> str:
    """ローカルの origin（bare）を作る。元のリポジトリの過去から ahead 件進めた main を入れる（外へ出ない）。"""
    source = tmp_path / "src"
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(source), str(bare))
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "clone", "-q", str(bare), ".")
    for i in range(ahead):
        (seed / f"origin{i}.txt").write_text(f"main の進み {i}\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", f"main の進み {i}")
    git(seed, "push", "-q", "origin", "main")
    return str(bare)


def run_check(name, report=PASS_REPORT, passed=True):
    BOX_CHECK["result"] = (0 if passed else 1, report, "")
    return workspaces.run_check(name)


# ---- ターンの終わりの自動保存 ----

def test_autosave_commits_each_turn_with_sequence_and_leaves_clean(box, external):  # noqa: F811
    assert workspaces.autosave(box, "conv1")["skipped"] == "clean", "変更が無ければしない"
    (box.repo / "w.txt").write_text("work\n")
    first = workspaces.autosave(box, "conv1")
    assert first["committed"] is True and first["count"] == 1 and first["skipped"] == ""
    assert git(box.repo, "log", "-1", "--format=%s") == "作業途中の保存 #1（会話 conv1 のターン）"
    assert git(box.repo, "log", "-1", "--format=%an") == "ControlDeck Sandbox (ws1)", "作者は箱の設定済みの作者"
    assert git(box.repo, "status", "--porcelain") == ""
    assert git(workspaces.export_repo(), "rev-parse", "refs/ws/ws1/head") == git(box.repo, "rev-parse", "HEAD"), \
        "結果のカードの差分（export.git）に途中の保存が入る"
    assert workspaces.autosave(box, "conv1")["skipped"] == "clean", "コミット後は何もしない"
    (box.repo / "w.txt").write_text("more\n")
    second = workspaces.autosave(box, "conv2")
    assert second["count"] == 2, "連番は箱ごとに続く"
    assert "作業途中の保存 #2" in git(box.repo, "log", "-3", "--format=%s")


def test_autosave_at_startup_names_the_reason(box, external):  # noqa: F811
    (box.repo / "w.txt").write_text("power cut\n")
    first = workspaces.autosave(box)
    assert first["committed"] is True
    assert git(box.repo, "log", "-1", "--format=%s") == "作業途中の保存 #1（起動時の立て直し）"


def test_autosave_reports_box_failures_without_raising(box, monkeypatch):
    (box.repo / "x.txt").write_text("x")

    def probe(box_, **kw):     # 箱の状態は見えるが、中の実行（コミット）だけが失敗する経線
        return {"branch": "sandbox/ws1", "head": "x" * 40, "dirty": 1, "unpushed": 0,
                "unknown": False, "at": 1.0}

    def broken(box_, command, **kw):
        raise sandbox.SandboxError("箱が止まっています")

    monkeypatch.setattr(sandbox, "probe", probe)
    monkeypatch.setattr(sandbox, "in_box_result", broken)
    result = workspaces.autosave(box, "conv1")
    assert result["committed"] is False and result["skipped"] == "error"
    assert "箱が止まっています" in result["error"], "呼び出し元は会話を止めずに結果だけ受け取る"


def test_autosave_skips_while_a_merge_is_unresolved(box, tmp_path, external):  # noqa: F811
    """マージのぶつかりの解決の途中（unmerged が index に残る）はコミットしない — conflict marker 入りの
    まま「解決済み」として履歴に残してしまう（レビュー指摘 2026-11-22）。"""
    bare = make_origin(tmp_path, 1)
    workspaces._update_meta(box.name, origin=bare)
    (box.repo / "a.txt").write_text("箱の版\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "箱の a")
    seed = tmp_path / "seed"
    (seed / "a.txt").write_text("main の版\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "main の a")
    git(seed, "push", "-q", "origin", "main")
    # ぶつかった merge を abort せずに残す（箱の中の OpenCode が解決中に止まった状態）
    sandbox.in_box(box, ["sh", "-c",
                         f"git fetch -q {bare} main && (git merge --no-edit FETCH_HEAD || true)"], timeout=60)
    result = workspaces.autosave(box, "conv1")
    assert result["skipped"] == "merge" and result["committed"] is False, \
        "ぶつかりの解決の途中はコミットしない"
    assert "作業途中の保存" not in git(box.repo, "log", "-1", "--format=%s")
    git(box.repo, "merge", "--abort")


def test_autosave_skips_when_box_is_gone(box, monkeypatch):
    def probe(box_, **kw):
        return {"branch": "", "head": "", "dirty": 1, "unpushed": 0, "unknown": True, "at": 1.0}

    monkeypatch.setattr(sandbox, "probe", probe)
    assert workspaces.autosave(box, "conv1")["skipped"] == "unknown"


# ---- ディスクの空き ----

def test_disk_floor_blocks_create_and_skips_autosave(box, monkeypatch):
    (box.repo / "x.txt").write_text("x")
    monkeypatch.setattr(workspaces, "disk_free_gib", lambda: 5.0)
    assert workspaces.autosave(box, "conv1") == {
        "committed": False, "count": 0, "skipped": "disk", "free_gib": 5.0, "error": "", "sha": ""}
    assert git(box.repo, "status", "--porcelain") == "?? x.txt", "飛ばしたときはコミットしない"
    with pytest.raises(sandbox.SandboxError, match="ディスクの空きが 5.0 GiB"):
        workspaces.create("admin", "controldeck", "ws2")


def test_disk_floor_is_configurable(box):
    from app.integrations.opencode import provider

    path = provider._settings_path()
    saved = path.read_text(encoding="utf-8") if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workspace_min_free_gib": 2}), encoding="utf-8")
    try:
        assert workspaces.min_free_gib() == 2.0
    finally:
        if saved is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(saved, encoding="utf-8")


def test_changes_report_disk_free_and_low_watermark(box, external):  # noqa: F811
    info = workspaces.changes(box)
    assert info["disk_free_gib"] > 0 and info["disk_low"] is False, "テストの tmp は空きがある"
    monkeypatch_disk = None
    original = workspaces.disk_free_gib

    def low():  # noqa: F811 - ローカル関数
        return original() if monkeypatch_disk is None else monkeypatch_disk

    workspaces.disk_free_gib = low  # type: ignore[assignment]
    try:
        workspaces._update_meta(box.name, main_progress={"at": 1.0, "behind": 0})  # fetch をさせない
        monkeypatch_disk = 8.0
        info = workspaces.changes(box)
        assert info["disk_low"] is True, "下限の 2 倍（30 GiB）未満は注意"
    finally:
        workspaces.disk_free_gib = original  # type: ignore[assignment]


# ---- 作業中か（消すときの確認） ----

def test_busy_follows_the_workspace_chat_server(box, monkeypatch):
    from app.integrations.opencode import chat_server

    monkeypatch.setattr(chat_server, "_unit_active", lambda unit: False)
    assert workspaces.busy(box) is False and workspaces.summary(box)["working"] is False
    server = chat_server.Server(key="persistbusy01", port=19000, password="p",
                                unit="cd-opencode-chat-persistbusy01",
                                config=str(box.home / ".config"), project=str(box.repo),
                                permission_file=str(box.home / "perm.json"),
                                started_at=1.0, last_used=1.0, owner=7, workspace=box.name)
    chat_server._save(server)
    units = {server.unit: False}

    def active(unit):
        return units.get(unit, False)

    monkeypatch.setattr(chat_server, "_unit_active", active)
    assert workspaces.busy(box) is False
    units[server.unit] = True
    assert workspaces.busy(box) is True, "会話サーバーが動いていれば作業中"
    assert workspaces.summary(box)["working"] is True
    assert workspaces.list_for("admin")[0]["working"] is True
    chat_server._state_path(server.key).unlink(missing_ok=True)


# ---- main の進み・取り込みの前の merge ----

def test_main_progress_counts_origin_and_caches(box, tmp_path, monkeypatch, external):  # noqa: F811
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 2))
    calls = {"n": 0}
    real = workspaces.fetch_main

    def counted(box_):
        calls["n"] += 1
        return real(box_)

    monkeypatch.setattr(workspaces, "fetch_main", counted)
    assert workspaces.main_progress(box, fresh=True) == 2, "元の revision から origin/main の差"
    assert workspaces.main_progress(box) == 2, "覚えた値を使う"
    assert calls["n"] == 1, "外部への fetch は毎回しない"
    assert workspaces.changes(box)["behind"] == 2, "結果のカードに出す"


def test_main_progress_returns_none_without_origin_or_unreachable(box, monkeypatch, external):  # noqa: F811
    assert workspaces.main_progress(box, fresh=True) is None, "origin が GitHub で外へは出ないテストでは取れない"
    workspaces._update_meta(box.name, origin="")
    assert workspaces.main_progress(box, fresh=True) is None


def test_merge_main_pulls_origin_main_into_the_box(box, tmp_path, external):  # noqa: F811
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 2))
    (box.repo / "own.txt").write_text("own\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "箱の変更")
    result = workspaces.merge_main(box)
    assert result["merged"] is True and result["behind"] == 2 and result["conflicts"] == []
    assert (box.repo / "origin0.txt").read_text() == "main の進み 0\n", "箱の中に main の変更が入る"
    assert (box.repo / "own.txt").read_text() == "own\n", "箱の変更も残る"
    assert workspaces.main_progress(box, fresh=True) == 0, "merge 後は進みが 0"
    bundle = workspaces.export_repo().parent / "ws1-main.bundle"
    assert not bundle.exists(), "bundle（ホストだけが書く場所）は使い終わったら消す"
    assert git(box.repo, "rev-parse", "--verify", workspaces.MAIN_REF), "取り込んだ main は箱の中の ref に残る"


def test_merge_main_does_nothing_when_up_to_date(box, tmp_path, external):  # noqa: F811
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 0))
    before = git(box.repo, "rev-parse", "HEAD")
    result = workspaces.merge_main(box)
    assert result["merged"] is False and result["behind"] == 0
    assert git(box.repo, "rev-parse", "HEAD") == before


def test_merge_main_reports_conflict_files_and_aborts(box, tmp_path, external):  # noqa: F811
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 1))
    (box.repo / "a.txt").write_text("箱の版\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "箱の a")
    seed = tmp_path / "seed"
    (seed / "a.txt").write_text("main の版\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "main の a")
    git(seed, "push", "-q", "origin", "main")
    result = workspaces.merge_main(box)
    assert result["merged"] is False and result["conflicts"] == ["a.txt"]
    assert (box.repo / "a.txt").read_text() == "箱の版\n", "merge --abort で作業ツリーを箱のままへ帰す"
    assert git(box.repo, "status", "--porcelain") == "", "ぶつかりの跡を残さない"
    state = workspaces.merge_conflict_state(box)
    assert state["conflicts"] == ["a.txt"], "カードへ出すために記録する"
    bundle = workspaces.export_repo().parent / "ws1-main.bundle"
    assert not bundle.exists() and "bundle" not in state, \
        "bundle は残さない（常駐の OpenCode の箱には見えない）。直させるときは箱の中の ref を merge させる"
    assert git(box.repo, "rev-parse", "--verify", workspaces.MAIN_REF), "ぶつかっても取り込んだ main は ref に残る"


def test_adopt_refuses_on_conflict_and_reports_files(box, tmp_path, external, monkeypatch):  # noqa: F811
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 1))
    (box.repo / "a.txt").write_text("箱の版\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "箱の a")
    seed = tmp_path / "seed"
    (seed / "a.txt").write_text("main の版\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "main の a")
    git(seed, "push", "-q", "origin", "main")
    run_check(box.name)
    with pytest.raises(workspaces.AdoptRefused, match="ぶつかりました"):
        workspaces.adopt(box.name, "admin")
    assert workspaces.merge_conflict_state(box)["conflicts"] == ["a.txt"]


def test_adopt_merges_main_then_asks_to_rerun_the_check(box, tmp_path, external):  # noqa: F811
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 1))
    (box.repo / "own.txt").write_text("own\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "箱の変更")
    run_check(box.name)
    with pytest.raises(workspaces.AdoptRefused, match="関門を流し直して"):
        workspaces.adopt(box.name, "admin")      # main を取り込んだら tree が変わるので関門を流し直させる
    run_check(box.name)
    result = workspaces.adopt(box.name, "admin")
    assert result["pr_url"].endswith("/pull/7")
    assert "origin0.txt" in git(workspaces.export_repo(), "ls-tree", "-r", "--name-only", "refs/ws/ws1/head"), \
        "PR の内容に main の変更も入る"


# ---- 起動時の立て直し ----

def test_restore_interrupted_clears_stale_lock_and_saves_unsaved(box, monkeypatch, external):  # noqa: F811
    (box.repo / ".git" / "index.lock").write_text("")
    (box.repo / "late.txt").write_text("停電の前に書いた\n")
    monkeypatch.setattr(sandbox, "_pod_running_containers", lambda name: False)
    workspaces.restore_interrupted()
    assert not (box.repo / ".git" / "index.lock").exists(), "pod が止まっていれば残った lock を消す"
    assert git(box.repo, "log", "-1", "--format=%s") == "作業途中の保存 #1（起動時の立て直し）"


def test_restore_interrupted_keeps_lock_while_git_runs(box, monkeypatch, external):  # noqa: F811
    (box.repo / ".git" / "index.lock").write_text("")
    monkeypatch.setattr(sandbox, "_pod_running_containers", lambda name: True)

    def fake_run(argv, **kw):
        if argv[1:3] == ["pod", "top"]:
            return subprocess.CompletedProcess(argv, 0, "UID PID ... COMMAND\n1000 12 1 ... git merge FETCH_HEAD", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(workspaces.subprocess, "run", fake_run)
    workspaces.restore_interrupted()
    assert (box.repo / ".git" / "index.lock").exists(), "git が動いているときは消さない"


def test_restore_interrupted_marks_generating_messages_of_workspaces(box, monkeypatch, external, client):  # noqa: F811
    from datetime import timedelta

    from app.database import SessionLocal
    from app.models import ChatMessage, Conversation, utcnow

    monkeypatch.setattr(sandbox, "_pod_running_containers", lambda name: False)
    past = utcnow() - timedelta(hours=1)     # 再起動より前（止まっていた分）
    fresh = utcnow() + timedelta(hours=1)   # 再起動より後（サーバーが受け付けた新しい分）
    with SessionLocal() as db:
        db.add(Conversation(id="convrestore00001", owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id="msgrestore000001", conversation_id="convrestore00001", role="assistant",
                           content="途中", status="generating", created_at=past,
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo)})))
        db.add(ChatMessage(id="msgrestore000002", conversation_id="convrestore00001", role="assistant",
                           content="他", status="generating", created_at=past,
                           meta_json=json.dumps({"mode": "code", "project_path": "/home/other/project"})))
        db.add(ChatMessage(id="msgrestore000003", conversation_id="convrestore00001", role="assistant",
                           content="新", status="generating", created_at=fresh,
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo)})))
        db.commit()
    workspaces.restore_interrupted()
    with SessionLocal() as db:
        row = db.get(ChatMessage, "msgrestore000001")
        assert row.status == "interrupted" and "再起動" in row.error
        other = db.get(ChatMessage, "msgrestore000002")
        assert other.status == "generating", "作業場ではない会話は触らない"
        new = db.get(ChatMessage, "msgrestore000003")
        assert new.status == "generating", "起動の後に始まった回は、利用者が送り直した可能性があるので触らない"


def test_last_conversation_finds_the_latest_of_the_workspace(box, client):
    from app.database import SessionLocal
    from app.models import ChatMessage, Conversation

    with SessionLocal() as db:
        db.add(Conversation(id="convlast0000001", owner_user_id=None))
        db.add(Conversation(id="convlast0000002", owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id="msglast000000001", conversation_id="convlast0000001", role="assistant",
                           content="前", status="completed",
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo),
                                                 "opencode_session": "ses_aaaa"})))
        db.add(ChatMessage(id="msglast000000002", conversation_id="convlast0000002", role="assistant",
                           content="後", status="completed",
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo),
                                                 "opencode_session": "ses_bbbb"})))
        db.commit()
    assert workspaces.last_conversation(box) == "convlast0000002"
    (box.repo / "gone").mkdir()
    assert workspaces.last_conversation(sandbox.load(box.name)) == "convlast0000002"


# ---- API ----

def test_api_resolve_conflict_sends_the_files_to_the_last_conversation(box, api, monkeypatch, tmp_path,
                                                                       external):  # noqa: F811
    from app.database import SessionLocal
    from app.models import AuditLog, ChatMessage, Conversation, User

    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 1))
    (box.repo / "a.txt").write_text("箱の版\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "箱の a")
    seed = tmp_path / "seed"
    (seed / "a.txt").write_text("main の版\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "main の a")
    git(seed, "push", "-q", "origin", "main")
    workspaces.merge_main(box)
    with SessionLocal() as db:
        me = db.execute(select(User).where(User.username == "admin")).scalar_one()
        db.add(Conversation(id="convresolve0001", owner_user_id=me.id, title="作業場"))
        db.flush()
        db.add(ChatMessage(id="msgresolve000001", conversation_id="convresolve0001", role="assistant",
                           content="前", status="completed",
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo),
                                                 "opencode_session": "ses_aaaa"})))
        db.commit()

    sent = {}

    async def fake_start_send(db_, conv, body, owner_user_id):
        sent.update(content=body.content, mode=body.mode, project=body.code_project_path, conv=conv.id)
        return {"assistant_message_id": "newmsg", "opencode_session": "ses_aaaa"}

    monkeypatch.setattr("app.workflows.chat_persist._start_send", fake_start_send)
    base = f"/api/v1/opencode/workspaces/{box.name}"
    r = api.post(f"{base}/resolve-conflict", headers=CSRF_HEADERS)
    assert r.status_code == 202, r.text
    assert sent["mode"] == "code" and sent["project"] == str(box.repo)
    assert "a.txt" in sent["content"], "ぶつかったファイルの一覧を伝える"
    assert f"git merge {workspaces.MAIN_REF}" in sent["content"], \
        "箱の中に取り込み済みの main の ref を merge させる（箱の AI から見えないホストのパスは伝えない）"
    assert "bundle" not in sent["content"]
    with SessionLocal() as db:
        rows = list(db.execute(select(AuditLog).where(
            AuditLog.action == "opencode.workspace.resolve_conflict")).scalars())
    assert len(rows) == 1 and json.loads(rows[0].metadata_json)["files"] == 1
    # 記録が無いときは断る（取り込みでぶつかっていない）
    workspaces._update_meta(box.name, merge_state=None)
    assert api.post(f"{base}/resolve-conflict", headers=CSRF_HEADERS).status_code == 409


def test_api_fix_check_sends_the_failures_to_the_last_conversation(box, api, monkeypatch):
    """目的: 確認（テスト）で落ちた項目を、この作業場で動いた同じ会話の OpenCode に「直させる」文として
    送る API を守る（resolve-conflict と同じ流れ・監査ログを残す）。
    根拠: 2026-11-27 利用者「関門を確認（テスト）にそろえ、不合格のときは『直させる』を主の操作に」。
    確認をまだ流していない・合格しているときは断る（送る内容が無い）。"""
    from app.database import SessionLocal
    from app.models import AuditLog, ChatMessage, Conversation, User

    fail_report = "[不合格] テスト: 1 件落ちた\n\n不合格（直すもの）:"
    base = f"/api/v1/opencode/workspaces/{box.name}"
    assert api.post(f"{base}/fix-check", headers=CSRF_HEADERS).status_code == 409, "まだ確認を流していない"
    run_check(box.name)
    assert api.post(f"{base}/fix-check", headers=CSRF_HEADERS).status_code == 409, "合格している"
    run_check(box.name, report=fail_report, passed=False)
    with SessionLocal() as db:
        me = db.execute(select(User).where(User.username == "admin")).scalar_one()
        db.add(Conversation(id="convfix0000001", owner_user_id=me.id, title="作業場"))
        db.flush()
        db.add(ChatMessage(id="msgfix0000001", conversation_id="convfix0000001", role="assistant",
                           content="前", status="completed",
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo),
                                                 "opencode_session": "ses_aaaa"})))
        db.commit()

    sent = {}

    async def fake_start_send(db_, conv, body, owner_user_id):
        sent.update(content=body.content, mode=body.mode, project=body.code_project_path, conv=conv.id)
        return {"assistant_message_id": "newmsg", "opencode_session": "ses_aaaa"}

    monkeypatch.setattr("app.workflows.chat_persist._start_send", fake_start_send)
    r = api.post(f"{base}/fix-check", headers=CSRF_HEADERS)
    assert r.status_code == 202, r.text
    assert sent["mode"] == "code" and sent["project"] == str(box.repo) and sent["conv"] == "convfix0000001"
    assert "テスト: 1 件落ちた" in sent["content"], "落ちた項目の名前と内容を伝える"
    assert "直して" in sent["content"] and "コミット" in sent["content"]
    with SessionLocal() as db:
        rows = list(db.execute(select(AuditLog).where(
            AuditLog.action == "opencode.workspace.fix_check")).scalars())
    assert len(rows) == 1 and json.loads(rows[0].metadata_json)["failures"] == 1
    # 確認の後にコミットが進んでいたら断る（落ちた内容が古いので。画面は stale で出さないが API の縛りとして）
    git(box.repo, "commit", "-q", "--allow-empty", "-m", "確認の後")
    r = api.post(f"{base}/fix-check", headers=CSRF_HEADERS)
    assert r.status_code == 409 and "流し直して" in r.json()["detail"], r.text


# ---- Claude による直し（2026-11-22 レビュー）----

def test_merge_main_shows_the_bundle_to_the_box_only_read_only(box, tmp_path, monkeypatch, external):  # noqa: F811
    """bundle は箱の AI が書ける場所へ置かず、fetch の間だけ readonly で見せる（symlink を辿らされる脅威）。"""
    workspaces._update_meta(box.name, origin=make_origin(tmp_path, 1))
    seen = []
    real = sandbox.in_box_result

    def spy(box_, command, **kw):
        seen.append((command[:2], kw.get("readonly")))
        return real(box_, command, **kw)

    monkeypatch.setattr(sandbox, "in_box_result", spy)
    workspaces.merge_main(box)
    bundle = workspaces.export_repo().parent / "ws1-main.bundle"
    fetch = [ro for command, ro in seen if command == ["git", "fetch"]]
    assert fetch == [[bundle]], "fetch の呼び出しだけが bundle を readonly で見せる"
    assert not bundle.parent.resolve().is_relative_to(sandbox.root().resolve()), "箱の書ける場所の外"


@pytest.mark.parametrize("origin", ["ext::sh -c 'touch /tmp/x'", "fd::3", "--upload-pack=evil", ""])
def test_fetch_main_refuses_unsafe_sources(box, monkeypatch, origin, external):  # noqa: F811
    """実行を伴うリモート補助（ext::・fd::）とオプションに読める先頭の `-` は fetch しない（git も動かさない）。"""
    workspaces._update_meta(box.name, origin=origin)
    called = []
    monkeypatch.setattr(workspaces, "_host_git", lambda *a, **k: called.append(a) or "")
    assert workspaces.fetch_main(box) is None
    assert called == []


def test_autosave_runs_one_at_a_time_per_workspace(box, external):  # noqa: F811
    """2 つのターンの終わり・起動時の立て直しが重なっても、番号が重ならず index.lock を取り合わない。"""
    import threading

    results = []

    def work(i):
        (box.repo / f"w{i}.txt").write_text(f"{i}\n")
        results.append(workspaces.autosave(box, f"conv{i}"))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not [r for r in results if r["skipped"] == "error"], results
    counts = sorted(r["count"] for r in results if r["committed"])
    assert counts == sorted(set(counts)), "番号が重ならない"
    assert git(box.repo, "status", "--porcelain") == ""


def test_restore_interrupted_does_not_wake_untouched_workspaces(box, monkeypatch, external):  # noqa: F811
    """止まっていた作業場まで全部起こさない（podman の起動）。中断にした・lock の残った作業場だけを保存する。"""
    (box.repo / "late.txt").write_text("未保存だが、会話は中断していない\n")
    called = []
    monkeypatch.setattr(workspaces, "autosave", lambda box_, *a, **k: called.append(box_.name) or {
        "committed": False, "skipped": "clean", "count": 0, "free_gib": 100.0, "error": ""})
    workspaces.restore_interrupted()
    assert called == []


# ---- 保存の時点へ戻す（ROADMAP U15） ----
# 目的: 自動保存の時点へコードを戻せること、戻す前の状態も残ること（やり直せる）、戻せない指定は拒むこと、
# 戻したことを AI へ一度だけ伝えること。根拠: ROADMAP U15（Claude Code の /rewind と同じ考え）。
# 戻す操作は未保存の変更を失いうるので、先に保存できないときは戻さない（変更を失わない）。

def _two_saves(box):
    (box.repo / "w.txt").write_text("one\n")
    first = workspaces.autosave(box, "conv1")
    (box.repo / "w.txt").write_text("two\n")
    second = workspaces.autosave(box, "conv1")
    return first["sha"], second["sha"]


def test_autosave_returns_the_committed_head(box, external):  # noqa: F811
    first, second = _two_saves(box)
    assert first != second and second == git(box.repo, "rev-parse", "HEAD")
    assert workspaces.autosave(box, "conv1")["sha"] == "", "保存しなかったら空"


def test_restore_resets_code_keeps_before_ref_records_meta_and_note_once(box, external):  # noqa: F811
    first, second = _two_saves(box)
    (box.repo / "w.txt").write_text("unsaved\n")       # 未保存の変更も先に保存され、失われない
    result = workspaces.restore(box, first, "admin")
    assert (box.repo / "w.txt").read_text() == "one\n"
    assert git(box.repo, "rev-parse", "HEAD") == first
    kept = git(box.repo, "rev-parse", result["ref"])
    assert kept == result["before"] and git(box.repo, "show", f"{kept}:w.txt") == "unsaved"
    meta = sandbox._read_meta(box.path)
    assert meta["restores"][-1]["from"] == kept and meta["restores"][-1]["to"] == first
    assert git(workspaces.export_repo(), "rev-parse", "refs/ws/ws1/head") == first
    note = workspaces.take_restore_note(box)
    assert first[:7] in note and "読み直して" in note
    assert workspaces.take_restore_note(box) == "", "一度きり"
    # 「戻す前」へは、祖先でなくなっていても戻せる（やり直し）
    kinds = [(c["kind"], c["sha"]) for c in workspaces.checkpoints(box)]
    assert ("before_restore", kept) in kinds
    workspaces.restore(box, kept, "admin")
    assert (box.repo / "w.txt").read_text() == "unsaved\n"


def test_checkpoints_list_saves_newest_first_with_count_and_conversation(box, external):  # noqa: F811
    first, second = _two_saves(box)
    items = workspaces.checkpoints(box)
    assert [c["sha"] for c in items] == [second, first]
    assert items[0]["count"] == 2 and items[0]["conversation_id"] == "conv1" and items[0]["kind"] == "save"


@pytest.mark.parametrize("bad", ["", "abc", "HEAD", "g" * 40, "--hard", "a" * 41])
def test_restore_rejects_malformed_sha(box, external, bad):  # noqa: F811
    with pytest.raises(sandbox.SandboxError, match="正しくありません"):
        workspaces.restore(box, bad, "admin")


def test_restore_rejects_commits_outside_this_workspace_history(box, external):  # noqa: F811
    first, _ = _two_saves(box)
    start = str(sandbox._read_meta(box.path)["revision"])
    git(box.repo, "checkout", "-q", "-b", "side", start)
    (box.repo / "s.txt").write_text("s\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "side")
    side = git(box.repo, "rev-parse", "HEAD")
    git(box.repo, "checkout", "-q", "-")
    with pytest.raises(sandbox.SandboxError, match="戻せません"):
        workspaces.restore(box, side, "admin")          # HEAD の祖先ではない
    with pytest.raises(sandbox.SandboxError, match="戻せません"):
        workspaces.restore(box, "0" * 40, "admin")      # 存在しない


def test_restore_refuses_when_unsaved_changes_cannot_be_saved(box, monkeypatch, external):  # noqa: F811
    first, second = _two_saves(box)
    (box.repo / "w.txt").write_text("unsaved\n")
    monkeypatch.setattr(workspaces, "disk_free_gib", lambda: 5.0)
    with pytest.raises(sandbox.SandboxError, match="ディスクの空き"):
        workspaces.restore(box, first, "admin")
    assert (box.repo / "w.txt").read_text() == "unsaved\n", "変更を失わない"
    assert git(box.repo, "rev-parse", "HEAD") == second


def test_restore_api_404_409_and_audit(box, api, monkeypatch, external):  # noqa: F811
    from app.database import SessionLocal
    from app.integrations.opencode import router as oc_router
    from app.models import AuditLog, ChatMessage, Conversation

    first, second = _two_saves(box)
    url = f"/api/v1/opencode/workspaces/{box.name}"
    assert api.get("/api/v1/opencode/workspaces/nothere/checkpoints").status_code == 422
    assert api.post("/api/v1/opencode/workspaces/nothere/restore", json={"sha": first},
                    headers=CSRF_HEADERS).status_code == 422, "他人・無い作業場は同じ「ありません」"
    assert api.get(f"{url}/checkpoints").json()["checkpoints"][0]["sha"] == second
    with SessionLocal() as db:
        db.add(Conversation(id="convrestoreapi01", owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id="msgrestoreapi0001", conversation_id="convrestoreapi01", role="assistant",
                           content="途中", status="generating", job_id="job-x",
                           meta_json=json.dumps({"mode": "code", "project_path": str(box.repo)})))
        db.commit()
    running = type("J", (), {"status": "running"})()
    monkeypatch.setattr(oc_router.jobs, "get", lambda job_id: running)
    assert api.post(f"{url}/restore", json={"sha": first}, headers=CSRF_HEADERS).status_code == 409
    assert git(box.repo, "rev-parse", "HEAD") == second, "作業中は戻さない"
    monkeypatch.setattr(oc_router.jobs, "get", lambda job_id: None)    # 止まった「生成中」は作業中とみなさない
    r = api.post(f"{url}/restore", json={"sha": first}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert db.execute(select(AuditLog).where(AuditLog.action == "workspace.restore")).scalars().first() is not None


# 目的: AI が自分でコミットした時点（autosave が作らない）も checkpoints に並び、そこへ戻せること。
# autosave がコミットしたとき、覚えていた probe（dirty）が捨てられ、一覧が古い「未保存」を返さないこと。
# 根拠: 2026-11-23 の評価、ROADMAP U22。

def test_agent_made_commit_is_listed_and_restorable(box, external):  # noqa: F811
    first, _ = _two_saves(box)
    (box.repo / "w.txt").write_text("by agent\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "feat: AI のコミット")
    head = git(box.repo, "rev-parse", "HEAD")
    assert workspaces.checkpoints(box)[0]["sha"] == head
    workspaces.restore(box, first, "admin")
    assert git(box.repo, "rev-parse", "HEAD") == first


def test_autosave_drops_the_stale_probe_cache(box, external):  # noqa: F811
    (box.repo / "w.txt").write_text("work\n")
    assert sandbox.probe(box, fresh=True)["dirty"]          # probe を覚える
    workspaces.autosave(box, "conv1")
    assert sandbox._read_meta(box.path).get("probe") is None
    assert workspaces.summary(box)["dirty"] is False
