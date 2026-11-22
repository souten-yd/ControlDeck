"""開発の作業場 4 段目（行ごとの差分・画面からのマージ）。

目的: (1) 差分は箱の作業ツリーでホストの git を動かさず、ホストが管理する export.git の ref から取る。1 ファイル 256KB で
打ち切り、バイナリは中身を出さない。(2) 取り込んだ PR のマージは、PR が開いている・マージできる・PR の head が export の ref と
同じで関門で確かめた tree と同じ・最後の関門が合格、を全部満たすときだけ。区分 L2 は確認の文言（「L2 を理解した」）が要る。
settings.manage が要り、監査へ残す。マージするのは確認した head だけ（--match-head-commit）。Release はしない。
根拠: 2026-11-22 利用者「マージの判断を Claude（Opus）に頼らず、画面で行えるように」（外部への書き込みと権限に関わる L2）。
git は本物（一時のリポジトリ）、gh・箱の中の実行はモック。実際の PR はマージしない。
"""
import json

import pytest
from sqlalchemy import select

from app.maintenance import sandbox, workspaces
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspace_adopt import (  # noqa: F401  (fixture)
    box, external, git, run_check,
)
from tests.test_dev_workspaces import api, repo  # noqa: F401  (fixture)

PHRASE = "L2 を理解した"


@pytest.fixture
def gh(monkeypatch, box, external):  # noqa: F811
    """gh の代役。pr view の答えは state で差し替え、pr merge の呼び出しを記録する。"""
    state = {"state": "OPEN", "mergeable": "MERGEABLE", "oid": None, "branch": "ws/ws1", "merges": []}

    def fake_gh(*args):
        external["gh"].append(list(args))
        if args[:2] == ("pr", "create"):
            return "https://github.com/example/repo/pull/7"
        if args[:2] == ("pr", "view"):
            oid = state["oid"] or workspaces._host_git("rev-parse", workspaces.ws_ref(box.name), cwd=workspaces.export_repo())
            return json.dumps({"state": state["state"], "mergeable": state["mergeable"], "headRefOid": oid,
                               "headRefName": state["branch"]})
        if args[:2] == ("pr", "merge"):
            state["merges"].append(list(args))
        return ""

    monkeypatch.setattr(workspaces, "_gh", fake_gh)
    return state


def adopted(box):  # noqa: F811
    run_check(box.name)
    workspaces.adopt(box.name, "admin")
    return box


# ---- 差分 ----

def test_file_diff_lists_files_and_reads_one_from_export_without_running_host_git_in_the_box(box, external):  # noqa: F811
    (box.repo / "u.txt").write_text("dirty")
    listing = workspaces.file_diff(box)
    assert [f["path"] for f in listing["files"]] == ["a.txt"] and listing["file"] is None
    assert listing["uncommitted"]["files"] == [{"path": "u.txt", "status": "untracked"}], "未コミットは箱の中の git で名前だけ"
    one = workspaces.file_diff(box, "a.txt")
    assert "+one" in one["file"]["diff"] and "+two" in one["file"]["diff"] and one["file"]["truncated"] is False
    assert external["host"], "ホストの git の呼び出しを記録できている"
    for call in external["host"]:
        assert not call["cwd"].resolve().is_relative_to(sandbox.root().resolve()), call
    with pytest.raises(sandbox.SandboxError, match="差分にありません"):
        workspaces.file_diff(box, "../etc/passwd")
    with pytest.raises(sandbox.SandboxError, match="差分にありません"):
        workspaces.file_diff(box, "--output=/tmp/x")


def test_file_diff_truncates_large_files_and_hides_binary(box, external):  # noqa: F811
    (box.repo / "big.txt").write_text("".join(f"line {i} {'x' * 40}\n" for i in range(8000)))
    (box.repo / "pic.bin").write_bytes(b"\x00\x01\x02" * 100)
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "big")
    big = workspaces.file_diff(box, "big.txt")["file"]
    assert big["truncated"] is True and big["size"] > workspaces.DIFF_LIMIT
    assert len(big["diff"].encode()) <= workspaces.DIFF_LIMIT and big["diff"].endswith("x" * 40), "行の途中で切らない"
    pic = workspaces.file_diff(box, "pic.bin")["file"]
    assert pic["binary"] is True and pic["diff"] == ""


def test_api_diff_needs_owner_and_returns_json(box, api, external):  # noqa: F811
    base = f"/api/v1/opencode/workspaces/{box.name}/diff"
    assert api.get(base).json()["files"][0]["path"] == "a.txt"
    assert "+one" in api.get(base, params={"path": "a.txt"}).json()["file"]["diff"]
    assert api.get(base, params={"path": "zzz"}).status_code == 422
    assert api.get("/api/v1/opencode/workspaces/nothing/diff").status_code == 422


# ---- マージの前提 ----

def test_merge_succeeds_for_l1_with_squash_delete_branch_and_match_head(box, gh, external):  # noqa: F811
    adopted(box)
    preview = workspaces.merge_preview(box.name, "admin")
    assert preview["ready"] is True and preview["tier"] == "L1" and preview["phrase"] == ""
    assert preview["pr"]["number"] == 7 and preview["check"]["passed"] is True
    result = workspaces.merge(box.name, "admin")
    head = git(workspaces.export_repo(), "rev-parse", workspaces.ws_ref(box.name))
    assert gh["merges"] == [["pr", "merge", "7", "-R", "example/repo", "--squash", "--delete-branch",
                             "--match-head-commit", head]]
    assert result["tier"] == "L1" and workspaces.merged_state(sandbox.load(box.name))["pr_url"].endswith("/pull/7")
    assert workspaces.changes(box)["merged"]["by"] == "admin"
    assert not any(call[:2] == ["release", "create"] or "release" in call for call in external["gh"]), "Release はしない"
    with pytest.raises(workspaces.AdoptRefused, match="すでにマージ済み"):
        workspaces.merge(box.name, "admin")


@pytest.mark.parametrize("change,message", [
    ({"state": "CLOSED"}, "開いていません"),
    ({"state": "MERGED"}, "開いていません"),
    ({"mergeable": "CONFLICTING"}, "競合"),
    ({"mergeable": "UNKNOWN"}, "判定中"),
    ({"oid": "0" * 40}, "先頭の commit が、取り込んだ内容と違います"),
    ({"branch": "other"}, "ブランチが"),
])
def test_merge_refuses_with_409_reason_and_never_calls_gh_merge(box, gh, change, message):  # noqa: F811
    adopted(box)
    gh.update(change)
    with pytest.raises(workspaces.AdoptRefused, match=message):
        workspaces.merge(box.name, "admin")
    assert gh["merges"] == []
    assert workspaces.merge_preview(box.name, "admin")["ready"] is False


def test_merge_refuses_when_the_box_moved_after_adopt_or_the_check_failed(box, gh):  # noqa: F811
    adopted(box)
    gh["oid"] = git(workspaces.export_repo(), "rev-parse", workspaces.ws_ref(box.name))  # PR の head は取り込んだ commit のまま
    git(box.repo, "commit", "-q", "--allow-empty", "-m", "取り込み後")
    workspaces.import_head(box)  # 画面の changes などで export の ref が箱の最新へ動く
    with pytest.raises(workspaces.AdoptRefused, match="先頭の commit が、取り込んだ内容と違います"):
        workspaces.merge(box.name, "admin")
    # 関門が不合格になった（流し直して落ちた）。PR の head と export の ref は同じ（箱が動く前の commit）に戻す
    git(box.repo, "reset", "-q", "--hard", "HEAD~1")
    workspaces.import_head(box)
    workspaces._update_meta(box.name, last_check={**sandbox._read_meta(box.path)["last_check"], "passed": False})
    with pytest.raises(workspaces.AdoptRefused, match="最後の関門が不合格"):
        workspaces.merge(box.name, "admin")
    assert gh["merges"] == []


def test_merge_refuses_when_pr_tree_differs_from_checked_tree(box, gh):  # noqa: F811
    adopted(box)
    meta = sandbox._read_meta(box.path)
    workspaces._update_meta(box.name, last_check={**meta["last_check"], "tree": "f" * 40})
    with pytest.raises(workspaces.AdoptRefused, match="関門で確かめた内容と違います"):
        workspaces.merge(box.name, "admin")
    assert gh["merges"] == []


def test_merge_without_pr_or_for_codedev_is_refused(box, gh):  # noqa: F811
    with pytest.raises(workspaces.AdoptRefused, match="PR をまだ作っていません"):
        workspaces.merge(box.name, "admin")
    adopted(box)
    workspaces._update_meta(box.name, adopted={**workspaces.adopted_state(box), "pr_url": "https://github.com/evil/other/pull/7"})
    with pytest.raises(workspaces.AdoptRefused, match="URL が取り込み先"):
        workspaces.merge(box.name, "admin")
    assert gh["merges"] == []


def test_l2_change_needs_the_confirmation_phrase_and_lists_guarded_files(box, gh):  # noqa: F811
    d = box.repo / "backend" / "app" / "security"
    d.mkdir(parents=True)
    (d / "deps.py").write_text("x = 1\n")
    git(box.repo, "add", "-A")
    git(box.repo, "commit", "-q", "-m", "L2")
    adopted(box)
    preview = workspaces.merge_preview(box.name, "admin")
    assert preview["tier"] == "L2" and preview["phrase"] == PHRASE
    assert [g["path"] for g in preview["guarded"]] == ["backend/app/security/deps.py"]
    # L2 はボタンの印（acknowledge_l2）で承認する（2026-11-23 利用者「文言を入力させずにボタンタップで」）。
    # 印も古い確認の文言も無いマージは断る
    for wrong in ("", "了解", PHRASE + "!"):
        with pytest.raises(workspaces.AdoptRefused, match="L2"):
            workspaces.merge(box.name, "admin", confirm_phrase=wrong)
    assert gh["merges"] == []
    result = workspaces.merge(box.name, "admin", acknowledge_l2=True)
    assert result["tier"] == "L2" and result["guarded"] == ["backend/app/security/deps.py"] and len(gh["merges"]) == 1


# ---- API ----

def test_api_merge_needs_settings_manage_confirm_and_is_audited(box, api, gh, monkeypatch):  # noqa: F811
    from app.database import SessionLocal
    from app.models import AuditLog
    from app.security import deps

    adopted(box)
    url = f"/api/v1/opencode/workspaces/{box.name}/merge"
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use"})
    assert api.get(url).status_code == 403
    assert api.post(url, json={"confirm": True}, headers=CSRF_HEADERS).status_code == 403
    monkeypatch.setattr(deps, "user_permissions", lambda user: {"terminal.use", "settings.manage"})
    assert api.get(url).json()["ready"] is True
    assert api.post(url, json={}, headers=CSRF_HEADERS).status_code == 422, "確認の印が必須"
    gh["state"] = "CLOSED"
    r = api.post(url, json={"confirm": True}, headers=CSRF_HEADERS)
    assert r.status_code == 409 and "開いていません" in r.json()["detail"]
    gh["state"] = "OPEN"
    r = api.post(url, json={"confirm": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["tier"] == "L1" and len(gh["merges"]) == 1
    with SessionLocal() as db:
        rows = list(db.execute(select(AuditLog).where(AuditLog.action == "opencode.workspace.merge")).scalars())
    assert len(rows) == 1
    meta = json.loads(rows[0].metadata_json) if isinstance(rows[0].metadata_json, str) else rows[0].metadata_json
    assert meta["pr_url"].endswith("/pull/7") and meta["tier"] == "L1" and meta["guarded"] == []


def test_tier_is_judged_on_the_pr_diff_from_the_current_main(box, gh, monkeypatch):  # noqa: F811
    """区分（L0/L1/L2）は、PR が実際に入れる差分（今の main との共通の祖先＝change_base から）で決める。
    根拠: 2026-11-22 の通しの U20。作業場が main を取り込んだ後、元の revision から見ていたので、他の PR の
    変更（AGENTS.md・router.py など）まで数え、画面だけの PR が「L2・40 ファイル」と出た。"""
    from app.maintenance import change_policy

    adopted(box)
    seen = {}
    base_of_main = str(box.meta.get("revision"))   # 本物の commit（共通の祖先の代わり）
    asked = []
    monkeypatch.setattr(workspaces, "change_base", lambda b, head: asked.append(head) or base_of_main)

    def classify(repo, base, head, project):
        seen["base"] = base
        return {"tier": "L1", "guarded": [], "files": [{"path": "frontend/src/x.tsx"}]}

    monkeypatch.setattr(change_policy, "classify", classify)
    facts = workspaces._merge_facts(box)
    assert asked and seen["base"] == base_of_main, "区分は change_base（今の main との共通の祖先）から"
    assert facts["tier"] == "L1" and facts["file_count"] == 1
