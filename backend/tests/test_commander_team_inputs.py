"""Nexarch の AI チームへの資料の受け渡し（app/commander/team_inputs.py・run/auto API の input_paths）の試験。

目的: 「大量のファイルを受け渡すときは、場所を指定して読み込ませる」（brief E・2026-11-28 利用者
「場所を指定して読み込ませるなど工夫できないか」）を仕組みで守る。
- 選んだファイル・フォルダは作業場所の .nexarch/inputs/ へ読み取り専用の写しとして置く
  （シンボリックリンクでの資料の公開はしない・元資料は書かない）。
- チームリーダーにはパスの一覧だけを渡す（大量の資料をプロンプトへ展開しない）。
- 検証: 作業フォルダと同じ決まり（commander.workdir の許可ルート内・realpath・機密禁止の場所は外・リンク逃げ）。
- 上限: 件数・1 件の大きさ・合計（必要以上にコピーしない・ディスクを食い尽くさない）。
- worktree へ .nexarch（依頼全文・資料）を渡し、frontend/node_modules・.venv は作業場所にあればリンクする
  （今はチームリーダーが手で写している・2026-11-28 の PLAN の要望）。
根拠: 2026-11-28 の依頼（brief E）。隔離テストは env -u CONTROL_DECK_CONFIG で流す。
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.commander import leader_mcp, team_git, team_inputs, team_leader, team_runner, teams, workdir
from app.config import get_config
from tests.conftest import CSRF_HEADERS

API = "/api/v1/commander/teams"


@pytest.fixture(autouse=True)
def _clean_store():
    path = teams._path()
    for target in (path, path.with_name("teams.json.bak")):
        if target.exists():
            target.unlink()
    yield
    for target in (path, path.with_name("teams.json.bak")):
        if target.exists():
            target.unlink()


@pytest.fixture()
def area(monkeypatch, tmp_path):
    """tmp_path を作業フォルダのルートにする。team が作業場所・outside がルートの外。"""
    root = tmp_path / "root"
    (root / "team").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(get_config().commander, "workdir_roots", [str(root)])
    return root, outside


def _published(root: Path) -> Path:
    return root / "team" / ".nexarch" / "inputs"


def test_publish_copies_readonly_and_records_rows(area):
    """目的: 資料が .nexarch/inputs/ へ読み取り専用の写しとして置かれ、板（team.inputs）に記録されること。

    元資料は書かない・動かさない。チームリーダーにはパスの一覧（rows）を渡すだけ。"""
    root, _outside = area
    src = root / "仕様書.md"
    src.write_text("大事な中身", encoding="utf-8")
    src_mode = stat.S_IMODE(src.stat().st_mode)
    rows = team_inputs.publish(str(root / "team"), "依頼の全文", [str(src)])
    published = _published(root) / "仕様書.md"
    assert rows == [{"path": str(published), "source": str(src.resolve()), "label": "仕様書.md"}]
    assert published.read_text(encoding="utf-8") == "大事な中身"
    assert stat.S_IMODE(published.stat().st_mode) & 0o222 == 0  # 読み取り専用
    assert src.read_text(encoding="utf-8") == "大事な中身" and stat.S_IMODE(src.stat().st_mode) == src_mode
    assert (root / "team" / ".nexarch" / "request.md").read_text(encoding="utf-8") == "依頼の全文"
    # 同じ資料をもう一度渡してもコピーしない（行はそのまま）
    rows2 = team_inputs.publish(str(root / "team"), "別の依頼", [str(src)], existing=rows)
    assert rows2 == rows


def test_publish_folder_dereferences_inner_links_and_keeps_structure(area):
    """目的: フォルダの中身も（相対構造を保って）写すこと。中のシンボリックリンクは実体を写す
    （symlink での資料の公開はしない・箱から見える実体のファイルだけ）。"""
    root, outside = area
    folder = root / "資料"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "b.txt").write_text("b", encoding="utf-8")
    (folder / "a.txt").write_text("a", encoding="utf-8")
    (folder / "link.txt").symlink_to(folder / "a.txt")  # 中のリンクは実体として写す
    rows = team_inputs.publish(str(root / "team"), "依頼", [str(folder)])
    base = _published(root) / "資料"
    assert (base / "a.txt").read_text(encoding="utf-8") == "a"
    assert (base / "sub" / "b.txt").read_text(encoding="utf-8") == "b"
    assert (base / "link.txt").is_file() and not (base / "link.txt").is_symlink()
    assert (base / "link.txt").read_text(encoding="utf-8") == "a"
    assert rows[0]["label"] == "資料" and rows[0]["source"] == str(folder.resolve())


def test_publish_rejects_outside_missing_and_duplicates_names(area):
    """目的: ルートの外・リンクで外へ出る道・無い物は断る。名前が重なる資料は別名で置く（上書きしない）。"""
    root, outside = area
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    (root / "escape").symlink_to(outside)
    for bad in (str(outside / "secret.txt"), str(root / "escape"), str(root / "無い.md")):
        with pytest.raises(teams.TeamError):
            team_inputs.publish(str(root / "team"), "依頼", [bad])
    (root / "a.txt").write_text("1", encoding="utf-8")
    (root / "b").mkdir()
    (root / "b" / "a.txt").write_text("2", encoding="utf-8")
    rows = team_inputs.publish(str(root / "team"), "依頼", [str(root / "a.txt"), str(root / "b" / "a.txt")])
    assert [r["label"] for r in rows] == ["a.txt", "a-2.txt"]
    assert (_published(root) / "a.txt").read_text(encoding="utf-8") == "1"
    assert (_published(root) / "a-2.txt").read_text(encoding="utf-8") == "2"


def test_publish_enforces_count_and_size_caps(area, monkeypatch):
    """目的: 件数・1 件の大きさ・合計の上限で、必要以上にコピーしないこと（ディスクを食い尽くさない）。"""
    root, _outside = area
    monkeypatch.setattr(team_inputs, "INPUT_ITEM_MAX", 8)
    big = root / "big.bin"
    big.write_bytes(b"x" * 16)
    with pytest.raises(teams.TeamError) as exc:
        team_inputs.publish(str(root / "team"), "依頼", [str(big)])
    assert "大きすぎます" in str(exc.value)
    monkeypatch.setattr(team_inputs, "INPUT_ITEM_MAX", 1024 * 1024)
    small = [root / f"s{i}.txt" for i in range(3)]
    for i, path in enumerate(small):
        path.write_text(str(i), encoding="utf-8")
    monkeypatch.setattr(team_inputs, "INPUTS_MAX", 2)
    with pytest.raises(teams.TeamError):
        team_inputs.publish(str(root / "team"), "依頼", [str(p) for p in small])
    monkeypatch.setattr(team_inputs, "INPUTS_MAX", 20)
    rows = team_inputs.publish(str(root / "team"), "依頼", [str(p) for p in small])
    assert len(rows) == 3


def test_share_worktree_hardlinks_inputs_and_links_devdirs(area):
    """目的: 仕事ごとの worktree へ .nexarch（依頼全文・資料）が見えること。

    - 資料はハードリンク（同じファイルシステム・ディスクを 2 倍にしない・実体として見える）。
    - node_modules・.venv は（相対の）リンクで渡す（大きいので写さない）。
    - 既にあるものは上書きしない。"""
    root, _outside = area
    src = root / "a.txt"
    src.write_text("a", encoding="utf-8")
    team_inputs.publish(str(root / "team"), "依頼", [str(src)])
    (root / "team" / "frontend").mkdir()
    (root / "team" / "frontend" / "node_modules").mkdir()
    (root / "team" / ".venv").mkdir()
    wt = root / "team" / ".nexarch-wt" / "1"
    wt.mkdir(parents=True)
    team_inputs.share_worktree(str(root / "team"), str(wt))
    assert (wt / ".nexarch" / "request.md").is_file()
    shared = wt / ".nexarch" / "inputs" / "a.txt"
    assert shared.read_text(encoding="utf-8") == "a"
    assert shared.stat().st_ino == (_published(root) / "a.txt").stat().st_ino  # ハードリンク
    assert not shared.is_symlink()
    assert (wt / "frontend" / "node_modules").is_symlink()
    assert (wt / "frontend" / "node_modules").resolve() == (root / "team" / "frontend" / "node_modules").resolve()
    assert (wt / ".venv").is_symlink() and (wt / ".venv").resolve() == (root / "team" / ".venv").resolve()
    # 2 回呼んでも壊さない（冪等）
    team_inputs.share_worktree(str(root / "team"), str(wt))
    assert shared.read_text(encoding="utf-8") == "a"


def test_create_item_worktree_shares_nexarch(area, monkeypatch):
    """目的: implement の仕事の worktree を作るとき、.nexarch が最初から見えること
    （今はチームリーダーが手で写している・2026-11-28 の PLAN の要望）。git の除外に .nexarch を足すので、
    資料が git へ入らない（ユーザーの資料をブランチの歴史へ残さない）ことも確かめる。"""
    root, _outside = area
    src = root / "doc.txt"
    src.write_text("doc", encoding="utf-8")
    (root / "team" / "frontend").mkdir()
    (root / "team" / "frontend" / "node_modules").mkdir()
    team_inputs.publish(str(root / "team"), "依頼", [str(src)])
    info = team_git.ensure_repo(str(root / "team"), "t0123abcd", git_init=True)
    _wt, branch = team_git.create_item_worktree(str(root / "team"), info, "t0123abcd", 1)
    wt = Path(_wt)
    assert (wt / ".nexarch" / "request.md").is_file()
    assert (wt / ".nexarch" / "inputs" / "doc.txt").read_text(encoding="utf-8") == "doc"
    assert (wt / "frontend" / "node_modules").is_symlink()
    code, out = team_git.run(wt, "status", "--porcelain")
    assert code == 0 and ".nexarch" not in out  # git の除外（資料は git へ入らない）
    exclude = (root / "team" / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".nexarch/" in exclude


# ---------------------------------------------------------------- API（run/auto の input_paths）

@pytest.fixture()
def no_spawn(monkeypatch):
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: None)


def test_auto_team_with_input_paths_publishes_and_lists_paths(admin_client, area, no_spawn, monkeypatch):
    """目的: 「チームリーダーに頼む」に資料を添えられること（brief E・FilePicker で選んだパスを input_paths へ）。

    - チームの板（team.inputs）に行が残る。依頼の全文は .nexarch/request.md へ。
    - チームリーダーにはパスの一覧だけを渡す（プロンプトへ資料の中身を展開しない）。"""
    root, _outside = area
    src = root / "brief.md"
    src.write_text("# 依頼の原本", encoding="utf-8")
    made = {"n": 0}

    def make_workdir(name):
        made["n"] += 1
        path = root / f"wd-{made['n']}"
        path.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        return {"workdir": str(path), "workdir_folder": path.name, "workdir_name": name}

    monkeypatch.setattr(teams, "_make_workdir", make_workdir)
    body = {"goal": "資料を踏まえて作る", "input_paths": [str(src)]}
    r = admin_client.post(f"{API}/auto", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 201, r.text
    team = r.json()
    rows = team["inputs"]
    assert len(rows) == 1 and rows[0]["source"] == str(src.resolve())
    published = Path(rows[0]["path"])
    assert published.is_file() and published.read_text(encoding="utf-8") == "# 依頼の原本"
    assert "【資料】" in team["leader_run"]["goal"] and str(published) in team["leader_run"]["goal"]
    assert "# 依頼の原本" not in team["leader_run"]["goal"]  # 中身は展開しない
    # 走らせない試験なので、次の依頼を受けられるようチームリーダーを終わった状態にする
    leader_mcp._mutate(team["id"], lambda t: t["leader_run"].update({"status": "done"}))
    follow = admin_client.post(f"{API}/{team['id']}/leader/run", headers=CSRF_HEADERS,
                               json={"goal": "次の依頼", "input_paths": []})
    assert follow.status_code == 200 and follow.json()["inputs"] == rows
    src2 = root / "追加.md"
    src2.write_text("追加の資料", encoding="utf-8")
    leader_mcp._mutate(team["id"], lambda t: t["leader_run"].update({"status": "done"}))
    follow2 = admin_client.post(f"{API}/{team['id']}/leader/run", headers=CSRF_HEADERS,
                                json={"goal": "さらに", "input_paths": [str(src2)]})
    assert follow2.status_code == 200, follow2.text
    assert [r["label"] for r in follow2.json()["inputs"]] == ["brief.md", "追加.md"]
    # 依頼の全文は最新のものに書き換わる
    assert (Path(team["workdir"]) / ".nexarch" / "request.md").read_text(encoding="utf-8") == "さらに"


def test_input_paths_validation(admin_client, area, no_spawn, monkeypatch):
    """目的: input_paths の形の検証（文字列のリストだけ・ルートの外は 400）。"""
    root, outside = area
    made = {"n": 0}

    def make_workdir(name):
        made["n"] += 1
        path = root / f"wd-{made['n']}"
        path.mkdir()
        return {"workdir": str(path), "workdir_folder": path.name, "workdir_name": name}

    monkeypatch.setattr(teams, "_make_workdir", make_workdir)
    for bad in ("x", [5], ["x", None], [{"a": 1}], ["x"] * 21):
        r = admin_client.post(f"{API}/auto", headers=CSRF_HEADERS,
                              json={"goal": "g", "input_paths": bad})
        assert r.status_code in (400, 422), bad
    (outside / "x.txt").write_text("s", encoding="utf-8")
    r = admin_client.post(f"{API}/auto", headers=CSRF_HEADERS,
                          json={"goal": "g", "input_paths": [str(outside / "x.txt")]})
    assert r.status_code == 400 and "資料" in r.text
