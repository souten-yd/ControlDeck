"""Project Lab の見張り役 `app.project_lab.live`（見張りモードの本体）。

目的:
- 保存のたびに build → command を回す。まとめて 1 回（debounce）、build が失敗したら command を起こさず
  failed、rerun は最後まで流して exited と exit_code、restart は前の子を止めてから起こす。
- `.controldeck/` などの変更では回らない（状態ファイル自身の書き込みで回り続けない）。
- glob の当たり方（paths・ignore、`/` を含まない pattern はどの深さにも当たる）。
根拠: 2026-11-21 の設計（Vite 以外の Web・Python・C++ でも、保存したら結果をすぐ確かめる）。
状態ファイルへの書き込みを見張ると無限に回るので、常に見張らない場所を決めている。
実際に watchfiles と子プロセスを使う（本物の systemd・podman は使わない）。子は偽の python スクリプト。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.project_lab.live import Matcher

BACKEND = Path(__file__).resolve().parents[1]

CHILD = r'''
import os, signal, sys, time
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build")   # build/ は見張らない
os.makedirs(out, exist_ok=True)
log = os.path.join(out, "events.log")
def note(text):
    with open(log, "a") as f:
        f.write(text + "\n")
note("start")
mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
if mode == "serve":
    def term(*_):
        note("term")
        sys.exit(0)
    signal.signal(signal.SIGTERM, term)
    while True:
        time.sleep(0.05)
print("child says hello", file=sys.stderr)
sys.exit(int(sys.argv[2]))
'''
BUILD = r'''
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(here, "build"), exist_ok=True)
with open(os.path.join(here, "build", "builds.log"), "a") as f:
    f.write("b\n")
if os.path.exists(os.path.join(here, "fail.flag")):
    print("boom: compile error", file=sys.stderr)
    sys.exit(1)
'''


def wait_for(predicate, timeout: float = 15.0, what: str = ""):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"時間切れ: {what}")


class Live:
    def __init__(self, tmp_path: Path, *, mode: str, child_args: list[str], build: bool = False,
                 paths: list[str] | None = None, ignore: list[str] | None = None) -> None:
        self.project = (tmp_path / "proj").resolve()
        (self.project / ".controldeck").mkdir(parents=True)
        (self.project / "child.py").write_text(CHILD, encoding="utf-8")
        (self.project / "build.py").write_text(BUILD, encoding="utf-8")
        (self.project / "src.txt").write_text("v0", encoding="utf-8")
        self.state = self.project / ".controldeck" / "live" / "7.jsonl"
        spec = {
            "run_id": 7, "project": str(self.project), "cwd": str(self.project),
            "command": [sys.executable, "child.py", *child_args],
            "build": [sys.executable, "build.py"] if build else [], "mode": mode,
            "paths": paths or ["**/*"], "ignore": ignore or [], "debounce_ms": 150,
            "state_file": str(self.state), "child_pythonpath": None,
        }
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(BACKEND)}
        # 本番と同じく作業フォルダはプロジェクト。利用者の app.py があっても ControlDeck の app を読む（-P。
        # 2026-11-21 実機: -P が無いと利用者の app.py が先に読まれ、見張り役が起動しなかった）
        (self.project / "app.py").write_text("raise SystemExit('利用者の app.py が読まれた')\n", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-P", "-m", "app.project_lab.live", "--spec", str(spec_file)],
            cwd=str(self.project), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

    def phases(self) -> list[dict]:
        if not self.state.exists():
            return []
        return [json.loads(line) for line in self.state.read_text(encoding="utf-8").splitlines() if line]

    def names(self) -> list[str]:
        return [item["phase"] for item in self.phases()]

    def log(self) -> list[str]:
        path = self.project / "build" / "events.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def builds(self) -> int:
        path = self.project / "build" / "builds.log"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            out, _ = self.proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, _ = self.proc.communicate()
        return out.decode("utf-8", errors="replace")


@pytest.fixture()
def live(tmp_path):
    made: list[Live] = []

    def make(**kwargs) -> Live:
        item = Live(tmp_path, **kwargs)
        made.append(item)
        return item

    yield make
    for item in made:
        item.stop()


def test_rerun_runs_to_the_end_and_reports_exit_code(live):
    run = live(mode="rerun", child_args=["once", "3"])
    wait_for(lambda: "exited" in run.names(), what="exited")
    last = run.phases()[-1]
    assert run.names()[:3] == ["idle", "running", "exited"]
    assert last["exit_code"] == 3 and "child says hello" in last["error_tail"]
    assert run.log() == ["start"]
    output = run.stop()
    assert "child says hello" in output          # journal にもそのまま出る


def test_a_burst_of_saves_runs_build_and_command_once(live):
    run = live(mode="rerun", child_args=["once", "0"], build=True)
    wait_for(lambda: run.names().count("exited") == 1, what="1 回目")
    time.sleep(0.4)                              # 見張りが張られるのを待つ
    for name in ("a.txt", "b.txt", "c.txt"):
        (run.project / name).write_text("x", encoding="utf-8")
        time.sleep(0.03)
    wait_for(lambda: run.names().count("exited") == 2, what="2 回目")
    time.sleep(1.0)
    assert run.names().count("exited") == 2 and run.builds() == 2     # 最初の 1 回 + まとめた 1 回
    building = [item for item in run.phases() if item["phase"] == "building"][-1]
    assert building["changed"] == ["a.txt", "b.txt", "c.txt"]
    # 終わりの記録にも、どの変更の後の結果かを残す（2026-11-21 実機: exited だけ空だった）
    exited = [item for item in run.phases() if item["phase"] == "exited"][-1]
    assert exited["changed"] == ["a.txt", "b.txt", "c.txt"]


def test_a_failed_build_never_starts_the_command(live):
    run = live(mode="rerun", child_args=["once", "0"], build=True)
    wait_for(lambda: "exited" in run.names(), what="最初は成功")
    (run.project / "fail.flag").write_text("", encoding="utf-8")
    wait_for(lambda: "failed" in run.names(), what="failed")
    failed = [item for item in run.phases() if item["phase"] == "failed"][-1]
    assert failed["exit_code"] == 1 and "boom: compile error" in failed["error_tail"]
    starts = run.log().count("start")
    assert starts == 1                            # 失敗した build の後では command を起こしていない
    (run.project / "fail.flag").unlink()          # 直すと、また回る
    wait_for(lambda: run.log().count("start") == 2, what="直したら起動")
    wait_for(lambda: run.names()[-1] == "exited", what="終わる")


def test_restart_stops_the_previous_child_before_starting_again(live):
    run = live(mode="restart", child_args=["serve"])
    wait_for(lambda: run.names().count("running") == 1 and run.log() == ["start"], what="最初の起動")
    time.sleep(0.4)
    (run.project / "src.txt").write_text("v1", encoding="utf-8")
    wait_for(lambda: run.log() == ["start", "term", "start"], what="止めてから起こす")
    assert run.names().count("running") == 2
    run.stop()
    assert run.log() == ["start", "term", "start", "term"]   # unit の停止でも子へ SIGTERM が届く


def test_changes_under_controldeck_and_build_dirs_do_not_loop(live):
    run = live(mode="rerun", child_args=["once", "0"], build=True)
    wait_for(lambda: "exited" in run.names(), what="1 回目")
    time.sleep(0.4)
    count = len(run.phases())
    for rel in (".controldeck/feedback/1.jsonl", "build/out.o", "node_modules/x/index.js",
                "__pycache__/m.pyc", ".git/HEAD"):
        target = run.project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    time.sleep(1.2)
    assert len(run.phases()) == count and run.builds() == 1
    (run.project / "src.txt").write_text("v1", encoding="utf-8")   # 対照: 本物の変更では回る
    wait_for(lambda: len(run.phases()) > count, what="本物の変更")


def test_ignore_patterns_keep_listed_files_from_triggering(live):
    run = live(mode="rerun", child_args=["once", "0"], build=True, ignore=["*.log", "docs/**"])
    wait_for(lambda: "exited" in run.names(), what="1 回目")
    time.sleep(0.4)
    (run.project / "docs").mkdir()
    (run.project / "docs" / "a.md").write_text("x", encoding="utf-8")
    (run.project / "noise.log").write_text("x", encoding="utf-8")
    time.sleep(1.0)
    assert run.builds() == 1


def test_glob_matching_rules(tmp_path):
    root = tmp_path.resolve()
    matcher = Matcher(root, ["src/**/*.py", "*.toml"], ["src/gen/**"])
    ok = lambda rel: matcher.accepts(str(root / rel))  # noqa: E731
    assert ok("src/a.py") and ok("src/x/y/b.py") and ok("pyproject.toml") and ok("sub/pyproject.toml")
    assert not ok("src/gen/a.py") and not ok("README.md") and not ok("src/a.txt")
    assert not Matcher(root, ["**/*"], []).accepts(str(root / "node_modules" / "a.js"))
    assert not Matcher(root, ["**/*"], []).accepts("/elsewhere/a.py")
