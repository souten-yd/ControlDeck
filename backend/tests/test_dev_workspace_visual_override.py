"""作業場の関門で、画面の見た目（画像を読むモデルの目）の誤検出を、管理者が人の目で確かめて外せること。

目的: 最後の関門が「画面の見た目」だけで落ちたとき、その項目だけ外して流し直せる（--skip-visual）。外したことは結果に残る。
見た目以外も落ちているときは外せない。
根拠: 2026-11-22、利用者として作業場から＋メニューの作り直しを頼んだ通しで、画像を読むモデルが重大の指摘を 3 件出した
（撮影を Opus が見て 3 件とも誤検出と判断）。人が確かめた後も外す口が無く、正しい変更が取り込めなかった。
"""
from app.maintenance import sandbox, workspaces
from tests.test_dev_workspace_adopt import box  # noqa: F401 - 作業場の fixture
from tests.test_dev_workspaces import BOX_CHECK, repo  # noqa: F401

VISUAL_NG = "[合格] テスト: 3 件\n[不合格] 画面の見た目: 重大 3 件（誤検出の可能性）\n\n不合格"
OTHER_NG = "[不合格] テスト: 1 件落ちました\n[不合格] 画面の見た目: 重大 1 件\n\n不合格"


def test_only_visual_failed():
    assert workspaces.only_visual_failed({"passed": False, "items": [
        {"status": "ok", "name": "テスト"}, {"status": "ng", "name": "画面の見た目"}]})
    assert not workspaces.only_visual_failed({"passed": False, "items": [
        {"status": "ng", "name": "テスト"}, {"status": "ng", "name": "画面の見た目"}]})
    assert not workspaces.only_visual_failed({"passed": True, "items": []})
    assert not workspaces.only_visual_failed(None)


def test_run_check_passes_skip_visual_and_records_who(box, monkeypatch):  # noqa: F811
    seen = []
    real = sandbox.in_box_result

    def spy(b, command, **kw):
        if "app.maintenance.dev_check" in " ".join(command):
            seen.append(" ".join(command))
        return real(b, command, **kw)

    monkeypatch.setattr(sandbox, "in_box_result", spy)
    BOX_CHECK["result"] = (1, VISUAL_NG, "")
    first = workspaces.run_check(box.name)
    assert not first["passed"] and workspaces.only_visual_failed(first)
    assert "--skip-visual" not in seen[-1]
    BOX_CHECK["result"] = (0, "[合格] テスト: 3 件\n[未実施] 画面の見た目: 外した\n\n合格", "")
    second = workspaces.run_check(box.name, skip_visual_by="souten")
    assert "--skip-visual" in seen[-1]
    assert second["passed"] and second["visual_skipped_by"] == "souten"


def test_other_failures_cannot_be_skipped(box):  # noqa: F811
    BOX_CHECK["result"] = (1, OTHER_NG, "")
    saved = workspaces.run_check(box.name)
    assert not workspaces.only_visual_failed(saved), "見た目以外も落ちていれば外す口は出さない"
