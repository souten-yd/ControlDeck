"""開発の関門 `./deck.sh dev-check`（app.maintenance.dev_check）の各検査。

目的: 検査ごとに「見つけるべきものを見つけ、問題の無い差分は通す」ことを確かめる（判定は文字列・辞書だけで決まる関数なので
git・pytest・npm を呼ばずに確かめられる）。最後に、小さな git リポジトリで差分の収集と終了の判定を通しで確かめる。
根拠: 開発の作業場 1 段目（2026-11-21）。箱の OpenCode（ローカル LLM・GLM）が Opus の監査に頼らず開発するために、Opus の監査で
見つかった機械的な問題（目録の説明の消失・依存や venv の取得・状態記録の未更新・作者の不定）を、完了の前に自分で検出させる。
秘密や shell=True の見本は、この検査自身に引っかからないよう実行時に組み立てる。
"""
import subprocess
from pathlib import Path

from app.maintenance import dev_check as dc

SK = "s" + "k-"  # 鍵の接頭辞を実行時に組み立てる（このファイル自身が秘密の検査に当たらないように）
SHELL = "shell" + "=True"


def test_select_tests_picks_importers_of_changed_modules_and_new_tests():
    sources = {
        "backend/tests/test_a.py": "from app.maintenance.sandbox import create\n",
        "backend/tests/test_b.py": "from app.maintenance import sandbox\n",
        "backend/tests/test_c.py": "import app.other\n",
        "backend/tests/test_d.py": "def test(monkeypatch):\n    monkeypatch.setattr('app.maintenance.sandbox.x', 1)\n",
        "backend/tests/test_new.py": "x = 1\n",
    }
    picked = dc.select_tests(["backend/app/maintenance/sandbox.py"], ["backend/tests/test_new.py"], sources)
    assert picked == ["backend/tests/test_a.py", "backend/tests/test_b.py", "backend/tests/test_d.py",
                      "backend/tests/test_new.py"]


def test_check_tests_fails_when_changed_code_has_no_test_and_when_pytest_fails():
    assert dc.check_tests([], True, None).status == dc.NG
    assert dc.check_tests([], False, None).status == dc.SKIP
    assert dc.check_tests(["a"], True, (0, "1 passed")).status == dc.OK
    failed = dc.check_tests(["a"], True, (1, "x\n1 failed in 0.1s"))
    assert failed.status == dc.NG and "1 failed" in failed.message


def test_frontend_build_is_skipped_not_failed_without_node_modules():
    changed = ["frontend/src/App.tsx"]
    skipped = dc.check_frontend(changed, False, None)
    assert skipped.status == dc.SKIP and "流せませんでした" in skipped.message
    assert dc.check_frontend(changed, True, (0, "")).status == dc.OK
    assert dc.check_frontend(changed, True, (1, "error TS1")).status == dc.NG
    assert dc.check_frontend(["backend/app/x.py"], False, None).status == dc.SKIP


def test_dependency_changes_are_rejected():
    assert dc.check_dependencies(["backend/app/x.py"]).status == dc.OK
    for path in dc.DEPENDENCY_FILES:
        result = dc.check_dependencies([path])
        assert result.status == dc.NG and "了承" in result.fixes[0]


def test_new_tests_need_purpose_basis_and_a_catalog_entry():
    good = '"""x\n\n目的: a\n根拠: b\n"""\n'
    catalog = "### backend/tests/test_good.py\n"
    assert dc.check_new_tests({"backend/tests/test_good.py": good}, catalog).status == dc.OK
    bad = dc.check_new_tests({"backend/tests/test_bad.py": '"""目的: a"""\n'}, catalog)
    assert bad.status == dc.NG
    assert any("根拠" in f for f in bad.fixes) and any("目録" in f or "test-catalog" in f for f in bad.fixes)
    assert dc.check_new_tests({}, catalog).status == dc.SKIP


def test_catalog_descriptions_may_not_shrink_but_removed_tests_are_fine():
    base = "### t.py\n\n目的: 目的です\n\n- `test_a` — 説明 A\n- `test_b` — 説明 B\n"
    kept = "### t.py\n\n目的: 目的です\n\n- `test_a` — 説明 A\n- `test_c`\n"  # test_b は消えたテスト
    assert dc.check_catalog_descriptions(base, kept).status == dc.OK
    lost = "### t.py\n\n目的: **未記入**\n\n- `test_a`\n- `test_b` — 説明 B\n"
    result = dc.check_catalog_descriptions(base, lost)
    assert result.status == dc.NG and "test_a" in result.message and "目的" in result.message


def test_status_doc_must_be_updated_when_code_changes():
    assert dc.check_status_updated(["backend/app/x.py"]).status == dc.NG
    assert dc.check_status_updated(["backend/app/x.py", dc.STATUS_DOC]).status == dc.OK
    assert dc.check_status_updated(["docs/other.md"]).status == dc.OK


def test_secret_shapes_are_found_unless_marked_allowed():
    added = {"a.py": {1: f'key = "{SK}abcdefgh12345678"', 2: 'password = "' + "x" * 20 + '"',
                      3: "ok = 1", 4: f'v = "{SK}abcdefgh12345678"  # {dc.ALLOW_MARK}: 見本'}}
    result = dc.check_secrets(added)
    assert result.status == dc.NG and "a.py:1" in result.message and "a.py:2" in result.message
    assert "a.py:3" not in result.message and "a.py:4" not in result.message
    assert dc.check_secrets({"a.py": {1: "token = get_token()"}}).status == dc.OK


def test_shell_true_and_os_system_are_found_only_in_real_calls_on_added_lines():
    source = (
        "import os, subprocess\n"
        f"# {SHELL} は禁止（コメント）\n"
        f"text = '{SHELL}'\n"
        f"subprocess.run('ls', {SHELL})\n"
        "os.system('ls')\n"
        "subprocess.run(['ls'])\n"
    )
    assert dc.find_shell_calls(source, {1, 2, 3, 4, 5, 6}) == [4, 5]
    assert dc.find_shell_calls(source, {6}) == []
    assert dc.check_shell(["a.py:4"]).status == dc.NG and dc.check_shell([]).status == dc.OK


def test_alembic_needs_exactly_one_head():
    one = {"a.py": "revision = 'a'\ndown_revision = None\n", "b.py": "revision = 'b'\ndown_revision = 'a'\n"}
    assert dc.alembic_heads(one) == ["b"]
    two = dict(one, c="revision = 'c'\ndown_revision = 'a'\n")
    assert dc.alembic_heads(two) == ["b", "c"]
    merged = dict(two, d="revision = 'd'\ndown_revision = ('b', 'c')\n")
    assert dc.alembic_heads(merged) == ["d"]
    changed = ["backend/alembic/versions/c.py"]
    assert dc.check_alembic(changed, ["b", "c"]).status == dc.NG
    assert dc.check_alembic(changed, ["d"]).status == dc.OK
    assert dc.check_alembic(["x"], ["b", "c"]).status == dc.SKIP


def test_report_lists_failures_and_the_verdict():
    text, passed = dc.render_report([dc.Result("依存", dc.OK, "良い"), dc.Result("秘密の値", dc.NG, "悪い", ["直す"])])
    assert not passed and "[合格] 依存" in text and "[不合格] 秘密の値" in text and "不合格（直すもの）" in text
    text, passed = dc.render_report([dc.Result("依存", dc.OK, "良い")])
    assert passed and text.rstrip().endswith("合格")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout


def test_gather_diff_sees_changes_added_lines_and_untracked_files(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "new.py").write_text("z = 3\n")
    base = dc.resolve_base("", tmp_path)
    diff = dc.gather_diff(base, tmp_path)
    assert diff.changed == ["a.py", "new.py"] and diff.added_files == ["new.py"]
    assert diff.added_lines["a.py"] == {2: "y = 2"} and diff.added_lines["new.py"] == {1: "z = 3"}


def test_clean_env_does_not_pass_the_trusted_gate_path_to_tests(monkeypatch):
    """U21: 関門を信頼できる版で動かすときの PYTHONPATH を子（作業場の pytest）へ渡さない。渡すと作業場のテストが
    関門側の app を読み、作業場の変更を試さない。"""
    monkeypatch.setenv("PYTHONPATH", "/gate/backend")
    monkeypatch.setenv("CONTROL_DECK_DEV_CHECK_REPO", "/box/repo")
    env = dc._clean_env()
    assert "PYTHONPATH" not in env and "CONTROL_DECK_DEV_CHECK_REPO" not in env
