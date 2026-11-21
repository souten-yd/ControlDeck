"""Project Lab の見張りモードの設定（manifest の `watch`）と、実行のメモリの上限（`run_memory_max_mb`）。

目的:
- `watch` の paths・ignore・build は command・artifacts と同じ検証（相対パスだけ、`..` 禁止、shell の -c 禁止）。
  mode と debounce_ms の範囲。mode の省略値（web は restart、ほかは rerun）。
- watch があって mode が native 以外なら、systemd の unit は command の代わりに見張り役を起こす
  （spec は 0600・プロジェクトの外）。native は今までどおり command を直接起こす。
- 設定 `run_memory_max_mb`（既定 2048、512〜16384）が unit の MemoryMax に入る。
根拠: 2026-11-21 の設計。C++ のビルドは 2G では足りない。見張り役の指示には鍵付きの {base} が入るので置き場に気をつける。
"""
from __future__ import annotations

import json
import stat
import sys

import pytest
from pydantic import ValidationError

from app.database import SessionLocal
from app.models import ProjectRun
from app.project_lab import live_paths, runs, service
from app.schemas.project_lab import ProjectLabSettingsBody, ProjectProfile, ProjectWatch


def _profile(**kwargs) -> ProjectProfile:
    base = {"id": "p", "label": "P", "type": "web", "command": ["python3", "app.py"]}
    return ProjectProfile(**{**base, **kwargs})


def test_watch_accepts_a_full_declaration_and_fills_defaults():
    profile = _profile(watch={"build": ["cmake", "--build", "build"], "ignore": ["docs/**"]})
    watch = profile.watch
    assert watch.paths == ["**/*"] and watch.debounce_ms == 400
    assert watch.mode == "restart"                                  # web の省略値
    assert _profile(type="test", watch={}).watch.mode == "rerun"    # それ以外
    assert _profile(watch={"mode": "native"}).watch.mode == "native"


@pytest.mark.parametrize("bad", [
    {"paths": ["../outside"]}, {"paths": ["/etc/passwd"]}, {"paths": ["~/x"]}, {"ignore": ["a/../../b"]},
    {"build": ["bash", "-c", "make"]}, {"build": ["sh", "-lc", "make"]}, {"build": ["make", ""]},
    {"build": ["make", "API_TOKEN=abc"]},
    {"mode": "poll"}, {"debounce_ms": 99}, {"debounce_ms": 5001}, {"unknown": 1},
])
def test_watch_rejects_unsafe_or_out_of_range_values(bad):
    with pytest.raises(ValidationError):
        ProjectWatch(**bad)


def test_watch_is_only_for_runnable_profiles():
    with pytest.raises(ValidationError):
        ProjectProfile(id="s", label="S", type="static_html", watch={})
    assert ProjectWatch(debounce_ms=100).debounce_ms == 100 and ProjectWatch(debounce_ms=5000).debounce_ms == 5000


def test_run_memory_setting_range_default_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
    assert service.get_settings()["run_memory_max_mb"] == 2048
    assert service.save_settings({"run_memory_max_mb": 8192})["run_memory_max_mb"] == 8192
    assert service.get_settings()["run_memory_max_mb"] == 8192
    for bad in (511, 16385, "4096", True):
        with pytest.raises(service.ProjectLabError):
            service.save_settings({"run_memory_max_mb": bad})
    assert service.get_settings()["run_memory_max_mb"] == 8192       # 断った値は残らない
    (tmp_path / "data" / "project-lab" / "settings.json").write_text('{"run_memory_max_mb": 99999999}')
    assert service.get_settings()["run_memory_max_mb"] == 2048       # 壊れた値は既定へ倒す
    assert ProjectLabSettingsBody(run_memory_max_mb=512).run_memory_max_mb == 512
    for bad in (511, 16385):
        with pytest.raises(ValidationError):
            ProjectLabSettingsBody(run_memory_max_mb=bad)


def test_memory_setting_api_validates_and_applies(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
    headers = {"X-Requested-With": "ControlDeck"}
    assert admin_client.get("/api/v1/project-lab/settings").json()["run_memory_max_mb"] == 2048
    assert admin_client.put("/api/v1/project-lab/settings", json={"run_memory_max_mb": 100}, headers=headers).status_code == 422
    saved = admin_client.put("/api/v1/project-lab/settings", json={"run_memory_max_mb": 4096}, headers=headers)
    assert saved.status_code == 200 and saved.json()["run_memory_max_mb"] == 4096


class _Capture:
    def __init__(self, monkeypatch, tmp_path):
        self.argv: list[str] = []
        monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(live_paths, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(runs, "_systemd_tools", lambda: ("systemd-run", "systemctl", "journalctl"))
        monkeypatch.setattr(runs, "_artifact_snapshot", lambda project, manifest: {})
        monkeypatch.setattr(runs, "refresh_run", lambda db, row: row)

        def fake_run(argv, **kwargs):
            self.argv = argv

            class Result:
                returncode = 0
                stdout = stderr = ""
            return Result()
        monkeypatch.setattr(runs.subprocess, "run", fake_run)

    def launch(self, project, **kwargs):
        with SessionLocal() as db:
            for row in db.query(ProjectRun).filter(ProjectRun.status.in_(runs.RUNNING_STATES)).all():
                row.status = "SUCCEEDED"
            db.commit()
            row = runs._launch(
                db, project=project, project_id="live-demo", project_name="Live", profile_id="dev",
                profile_type=kwargs.pop("profile_type", "web"), command=kwargs.pop("command"), executable="/usr/bin/python3",
                cwd=project, environment=kwargs.pop("environment", {}), timeout_seconds=60,
                web_port=kwargs.pop("web_port", 40125), created_by=None, manifest=None, **kwargs,
            )
            run_id, base = row.id, runs.preview_base(row)
            row.status = "SUCCEEDED"
            db.commit()
        return run_id, base


def test_watch_run_starts_the_supervisor_with_a_private_spec(tmp_path, monkeypatch):
    capture = _Capture(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    watch = ProjectWatch(mode="restart", paths=["src/**"], ignore=["*.log"], debounce_ms=700)
    run_id, base = capture.launch(
        project, command=["python3", "app.py", "--base", "{base}", "40125"], watch=watch,
        build=["/usr/bin/make", "all"], environment={"PYTHONPATH": "lib"},
    )
    argv = capture.argv
    assert argv[-6:-2] == [sys.executable, "-P", "-m", "app.project_lab.live"] and argv[-2] == "--spec"   # -P: 利用者の app.py に負けない
    spec_path = live_paths.spec_path(run_id)
    assert argv[-1] == str(spec_path) and project not in spec_path.parents          # プロジェクトの外
    assert stat.S_IMODE(spec_path.stat().st_mode) == 0o600
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec["command"] == ["/usr/bin/python3", "app.py", "--base", base, "40125"]     # {base} はここで埋まる
    assert spec["build"] == ["/usr/bin/make", "all"] and spec["mode"] == "restart"
    assert spec["paths"] == ["src/**"] and spec["ignore"] == ["*.log"] and spec["debounce_ms"] == 700
    assert spec["child_pythonpath"] == "lib"                           # 子には利用者の PYTHONPATH だけ
    assert spec["state_file"] == str(live_paths.state_file(project, run_id))
    assert any(item.startswith("--setenv=PYTHONPATH=") and item.endswith("/backend") for item in argv)
    assert "--property=MemoryMax=2048M" in argv
    assert "--property=ReadWritePaths=" + str(project) in argv


def test_native_watch_and_no_watch_start_the_command_directly(tmp_path, monkeypatch):
    capture = _Capture(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    capture.launch(project, command=["python3", "app.py"], watch=ProjectWatch(mode="native"))
    assert capture.argv[-2:] == ["/usr/bin/python3", "app.py"] and "app.project_lab.live" not in capture.argv
    capture.launch(project, command=["python3", "app.py"])
    assert capture.argv[-2:] == ["/usr/bin/python3", "app.py"]


def test_memory_max_follows_the_setting(tmp_path, monkeypatch):
    capture = _Capture(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    service.save_settings({"run_memory_max_mb": 12288})
    capture.launch(project, command=["python3", "app.py"])
    assert "--property=MemoryMax=12288M" in capture.argv
    assert not any(item == "--property=MemoryMax=2G" for item in capture.argv)


def test_start_run_resolves_the_build_executable_and_skips_native(tmp_path, monkeypatch):
    """manifest から起こすとき、build の実行ファイルも許可 SDK だけ。native は見張り役を起こさない。"""
    capture = _Capture(monkeypatch, tmp_path)
    root = tmp_path / "CodeDEV"
    project = root / "demo"
    (project / ".controldeck").mkdir(parents=True)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(runs.shutil, "which", lambda value: f"/usr/bin/{value}")

    def write(watch):
        manifest = {"schemaVersion": 1, "name": "Demo", "profiles": [{
            "id": "dev", "label": "Dev", "type": "test", "command": ["ctest", "--test-dir", "build"], "watch": watch,
        }]}
        (project / ".controldeck" / "project.json").write_text(json.dumps(manifest), encoding="utf-8")

    def start():
        with SessionLocal() as db:
            for row in db.query(ProjectRun).filter(ProjectRun.status.in_(runs.RUNNING_STATES)).all():
                row.status = "SUCCEEDED"
            db.commit()
            row = runs.start_run(db, project_id="demo", profile_id="dev", timeout_seconds=60, created_by=None)
            run_id = row.id
            row.status = "SUCCEEDED"
            db.commit()
        return run_id

    write({"build": ["cmake", "--build", "build"]})
    spec = json.loads(live_paths.spec_path(start()).read_text(encoding="utf-8"))
    assert spec["build"] == ["/usr/bin/cmake", "--build", "build"] and spec["mode"] == "rerun"
    assert spec["command"][0].endswith("ctest")
    write({"build": ["curl", "http://example.com"]})
    with pytest.raises(runs.ProjectRunError):
        start()
    write({"mode": "native", "build": ["cmake", "--build", "build"]})
    start()
    assert capture.argv[-3].endswith("ctest") and "app.project_lab.live" not in capture.argv
