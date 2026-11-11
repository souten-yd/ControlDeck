"""Strata add-on tests never download or execute real upstream artifacts."""
import asyncio
import json
import subprocess

import pytest

from app.features import strata_runtime as strata


def test_runtime_status_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "root", lambda: tmp_path / "runtime")
    assert strata.runtime_status()["installed"] is False
    assert not strata.server_path().exists()


def test_install_uses_isolated_home_and_does_not_launch_models(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(strata, "_release", lambda: ("v0.2.13", b"#!/bin/sh\nexit 0\n"))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        exe = strata.server_path()
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o700)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(strata.subprocess, "run", fake_run)
    result = asyncio.run(strata.install())
    assert result["version"] == "v0.2.13"
    assert len(calls) == 1 and calls[0][0][0] == "sh"
    assert calls[0][1]["cwd"] == str(strata._home())
    assert calls[0][1]["env"]["HOME"] == str(strata._home())
    assert "launch" not in calls[0][0]
    assert strata.launch_script().is_file()
    assert json.loads(strata._state_path().read_text())["tag"] == "v0.2.13"


def test_install_refuses_arbitrary_options(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "root", lambda: tmp_path / "runtime")
    with pytest.raises(strata.StrataError):
        asyncio.run(strata.install(options={"url": "https://attacker.invalid"}))


def test_uninstall_refuses_running_model_and_preserves_data(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "root", lambda: tmp_path / "runtime")
    exe = strata.server_path()
    exe.parent.mkdir(parents=True)
    exe.write_text("fake", encoding="utf-8")
    exe.chmod(0o700)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.gguf").write_bytes(b"model")
    monkeypatch.setattr(
        strata.subprocess, "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"running":[{"port":8080}]}', ""),
    )
    with pytest.raises(strata.StrataError, match="停止"):
        strata.uninstall()
    assert exe.is_file() and (model_dir / "weights.gguf").is_file()


def test_uninstall_removes_only_managed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "root", lambda: tmp_path / "runtime")
    exe = strata.server_path()
    exe.parent.mkdir(parents=True)
    exe.write_text("fake", encoding="utf-8")
    exe.chmod(0o700)
    outside = tmp_path / "personal-model.gguf"
    outside.write_bytes(b"mine")
    monkeypatch.setattr(
        strata.subprocess, "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"running":[]}', ""),
    )
    assert strata.uninstall()["removed"] is True
    assert outside.read_bytes() == b"mine"
    assert not exe.exists()
