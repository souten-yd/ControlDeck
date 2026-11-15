"""変更の危険度の区分（docs/POLICY-autonomous-development.md）。"""
import subprocess

import pytest

from app.maintenance import change_policy as cp


def _repo(tmp_path, files: dict[str, str]):
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    (tmp_path / "seed").write_text("x")
    run("add", "-A")
    run("-c", "user.email=a@b.invalid", "-c", "user.name=t", "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    run("add", "-A")
    run("-c", "user.email=a@b.invalid", "-c", "user.name=t", "commit", "-qm", "change")
    return base


@pytest.mark.parametrize("files, tier", [
    ({"docs/a.md": "x", "backend/tests/test_a.py": "def test(): pass"}, "L0"),
    ({"frontend/src/pages/Models.tsx": "export {}"}, "L1"),
    ({"backend/app/models_mgmt/gateway.py": "x = 1"}, "L1"),
    ({"backend/app/security/deps.py": "x = 1"}, "L2"),
    ({"deck.sh": "echo"}, "L2"),
    ({"AGENTS.md": "x"}, "L2"),
    ({"config/change-policy/controldeck.json": "{}"}, "L2"),
    # 変更内容: backend で外部送信や権限の規則に触れると L2。
    ({"backend/app/integrations/opencode/provider.py": 'settings["research_external"] = True'}, "L2"),
    # 同じ語でも画面の文言は境界を強制しないので L1、テストは L0。
    ({"frontend/src/features/x.tsx": "research_external"}, "L1"),
    ({"backend/tests/test_x.py": "csrf require_permission("}, "L0"),
])
def test_controldeck_tiers(tmp_path, files, tier):
    base = _repo(tmp_path, files)
    assert cp.classify(tmp_path, base, "HEAD", "controldeck")["tier"] == tier


def test_overall_tier_is_the_highest_and_reasons_are_reported(tmp_path):
    base = _repo(tmp_path, {"docs/a.md": "x", "backend/app/main.py": "x = 1"})
    result = cp.classify(tmp_path, base, "HEAD", "controldeck")
    assert result["tier"] == "L2"
    assert [g["path"] for g in result["guarded"]] == ["backend/app/main.py"]
    assert "認証" in result["guarded"][0]["reasons"][0]


def test_codedev_default_is_automatic_except_secrets(tmp_path):
    base = _repo(tmp_path, {"src/game.js": "x", "assets/bg.png": "x"})
    assert cp.classify(tmp_path, base, "HEAD", "codedev")["tier"] == "L0"
    other = tmp_path / "b"
    other.mkdir()
    base = _repo(other, {".env": "KEY=1"})
    assert cp.classify(other, base, "HEAD", "codedev")["tier"] == "L2"


def test_patentbuddy_privacy_boundary_is_guarded(tmp_path):
    base = _repo(tmp_path, {"qnaplibrary/research_worker.py": "x = 1", "tests/test_x.py": "confirm_public"})
    assert cp.classify(tmp_path, base, "HEAD", "patentbuddy")["tier"] == "L1"
    other = tmp_path / "c"
    other.mkdir()
    base = _repo(other, {"qnaplibrary/api.py": "if body['confirm_public'] is not True: pass"})
    assert cp.classify(other, base, "HEAD", "patentbuddy")["tier"] == "L2"


def test_glob_semantics():
    assert cp._glob("docs/**").match("docs/a/b.md")
    assert cp._glob("**/.env").match(".env") and cp._glob("**/.env").match("a/b/.env")
    assert not cp._glob("backend/app/*.py").match("backend/app/x/y.py")


def test_bad_policy_is_rejected(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text('{"default": "L9", "rules": []}')
    with pytest.raises(cp.PolicyError):
        cp.load(bad)
