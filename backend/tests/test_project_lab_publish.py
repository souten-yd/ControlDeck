"""静的サイト公開で、出してはいけない物が出ないことを守る。

公開は取り消しても索引や cache に残る。ダウンロードなら手元で気づけば済むが、
ここは気づいた時点で手遅れになる。選別が export と同じ規則を使い続けること、
index.html の無い場所を公開しないことをテストで固定する。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.project_lab import publish


@pytest.fixture()
def site(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "dist" / "assets").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "dist" / "index.html").write_text("<h1>site</h1>", encoding="utf-8")
    (root / "dist" / "assets" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    (root / "dist" / ".env").write_text("KEY=1\n", encoding="utf-8")
    (root / "src" / "main.ts").write_text("export {}\n", encoding="utf-8")
    return root


def test_detect_directory_prefers_the_built_site(site: Path):
    assert publish.detect_directory(site) == "dist"


def test_detect_directory_falls_back_to_project_root(tmp_path: Path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    assert publish.detect_directory(root) == ""


def test_resolve_target_refuses_to_leave_the_project(site: Path):
    for escape in ("../..", "/etc", "dist/../../outside"):
        with pytest.raises(publish.PublishError):
            publish.resolve_target(site, escape)


def test_plan_uses_the_same_exclusions_as_download(site: Path):
    """公開経路にだけ緩い判定を置くと、緩い方から漏れる。"""
    target = publish.resolve_target(site, "dist")
    plan = publish.plan(target)
    names = {p.relative_to(target.root).as_posix() for p in plan.files}
    assert names == {"index.html", "assets/app.js"}
    assert [item["path"] for item in plan.excluded] == [".env"]


def _stub_gh(monkeypatch, *, logged_in=True, calls=None):
    monkeypatch.setattr(publish.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args, cwd=None, timeout=publish.GIT_TIMEOUT_SECONDS, extra_env=None):
        if calls is not None:
            calls.append((list(args), cwd, extra_env))
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(
                args, 0 if logged_in else 1, "account souten-yd\n", "")
        if args[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(args, 0, '{"name":"demo"}', "")
        if args[:2] == ["gh", "api"] and args[-1] == ".html_url":
            return subprocess.CompletedProcess(args, 0, "https://souten-yd.github.io/demo/\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(publish, "_run", fake_run)
    monkeypatch.setattr(publish, "_save_state", lambda *a, **k: None)


def test_publish_refuses_without_an_index(site: Path, monkeypatch):
    _stub_gh(monkeypatch)
    with pytest.raises(publish.PublishError, match="index.html"):
        publish.publish("demo", site, directory="src",
                        repository="demo", visibility="private")


def test_publish_refuses_when_gh_is_not_logged_in(site: Path, monkeypatch):
    _stub_gh(monkeypatch, logged_in=False)
    with pytest.raises(publish.PublishError, match="ログイン"):
        publish.publish("demo", site, directory="dist",
                        repository="demo", visibility="private")


def test_publish_rejects_an_unknown_visibility(site: Path, monkeypatch):
    _stub_gh(monkeypatch)
    with pytest.raises(publish.PublishError):
        publish.publish("demo", site, directory="dist",
                        repository="demo", visibility="world")


def test_publish_pushes_force_and_never_puts_a_token_in_the_remote(site: Path, monkeypatch):
    calls: list = []
    _stub_gh(monkeypatch, calls=calls)

    entry = publish.publish("demo", site, directory="dist",
                            repository="demo", visibility="private")

    assert entry["url"] == "https://souten-yd.github.io/demo/"
    assert entry["repository"] == "souten-yd/demo"
    assert entry["fileCount"] == 2

    git = [args for args, _cwd, _env in calls if args[0] == "git"]
    push = next(args for args in git if args[1] == "push")
    # 歴史ではなく「いまの姿」を出す。積み上げると混ざった秘密を消せなくなる。
    assert "--force" in push
    # remote URL に token を埋めない。埋めると .git/config と log に残る。
    remote = next(args for args in git if args[1] == "remote")
    assert remote[-1] == "https://github.com/souten-yd/demo.git"
    assert "@" not in remote[-1]
    # 認証は gh に肩代わりさせる
    push_env = next(env for args, _cwd, env in calls if args[0] == "git" and args[1] == "push")
    assert push_env["GIT_CONFIG_VALUE_0"] == "!gh auth git-credential"


def test_publish_stages_only_selected_files_and_adds_nojekyll(site: Path, monkeypatch):
    """staging の中身を、git が走る直前に覗いて確かめる。"""
    seen: dict[str, set[str]] = {}
    monkeypatch.setattr(publish.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(publish, "_save_state", lambda *a, **k: None)

    def fake_run(args, cwd=None, timeout=publish.GIT_TIMEOUT_SECONDS, extra_env=None):
        if args[:2] == ["git", "add"] and cwd is not None:
            seen["staged"] = {
                p.relative_to(cwd).as_posix() for p in Path(cwd).rglob("*") if p.is_file()
            }
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "account souten-yd\n", "")
        if args[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(args, 0, '{"name":"demo"}', "")
        if args[:2] == ["gh", "api"] and args[-1] == ".html_url":
            return subprocess.CompletedProcess(args, 0, "https://x.github.io/demo/\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(publish, "_run", fake_run)
    publish.publish("demo", site, directory="dist", repository="demo", visibility="private")

    # .env は出ない。.nojekyll は足す（無いと Jekyll が `_` 始まりを丸ごと捨てる）
    assert seen["staged"] == {"index.html", "assets/app.js", ".nojekyll"}


def test_unpublish_disables_pages_and_removes_the_branch(monkeypatch):
    """公開を取り下げると URL が 404 になり、中身も残らないこと。

    リポジトリ自体は消さない。公開を止めたいだけの操作で履歴も issue も
    一緒に消えるのは取り返しがつかない。
    """
    calls: list = []
    state = {"demo": {"repository": "souten-yd/demo", "branch": "gh-pages",
                      "visibility": "private", "directory": "dist",
                      "url": "https://souten-yd.github.io/demo/",
                      "fileCount": 2, "excludedCount": 0}}
    monkeypatch.setattr(publish.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(publish, "_load_state", lambda: dict(state))
    cleared: list[str] = []
    monkeypatch.setattr(publish, "_clear_state", lambda pid: cleared.append(pid))

    def fake_run(args, cwd=None, timeout=publish.GIT_TIMEOUT_SECONDS, extra_env=None):
        calls.append(list(args))
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "account souten-yd\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(publish, "_run", fake_run)
    result = publish.unpublish("demo")

    api = [" ".join(a) for a in calls if a[0] == "gh" and a[1] == "api"]
    assert any("DELETE repos/souten-yd/demo/pages" in a for a in api), "Pages を無効にしていない"
    assert any("DELETE repos/souten-yd/demo/git/refs/heads/gh-pages" in a for a in api), \
        "公開していた中身が残ってしまう"
    assert cleared == ["demo"]
    assert result["removed"] == ["pages", "branch"]
    # リポジトリは残る。何が残っているかを黙らない。
    assert result["repositoryRemains"] is True
    assert result["repositoryUrl"] == "https://github.com/souten-yd/demo"
    assert not any("DELETE repos/souten-yd/demo" == " ".join(a[2:]) for a in calls), \
        "リポジトリ自体を消してはいけない"


def test_unpublish_tolerates_an_already_disabled_site(monkeypatch):
    """既に止まっているものを止め直しても失敗させない（404 は想定内）。"""
    monkeypatch.setattr(publish.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(publish, "_load_state", lambda: {
        "demo": {"repository": "souten-yd/demo", "branch": "gh-pages"}})
    monkeypatch.setattr(publish, "_clear_state", lambda pid: None)

    def fake_run(args, cwd=None, timeout=publish.GIT_TIMEOUT_SECONDS, extra_env=None):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "account souten-yd\n", "")
        return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")

    monkeypatch.setattr(publish, "_run", fake_run)
    result = publish.unpublish("demo")
    assert result["removed"] == []


def test_unpublish_refuses_when_nothing_is_published(monkeypatch):
    monkeypatch.setattr(publish, "_load_state", lambda: {})
    with pytest.raises(publish.PublishError, match="公開されていません"):
        publish.unpublish("demo")
