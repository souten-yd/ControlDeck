"""Strata must not download a model or alter the active runtime by default."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from app.models_mgmt import strata, strata_gateway


def _managed(monkeypatch, tmp_path):
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(strata_gateway, "_running", lambda: [])


def test_status_parses_upstream_json_even_when_human_lines_follow(monkeypatch):
    monkeypatch.setattr(strata, "installed", lambda: True)
    def fake_run(args, *, timeout=30):
        assert args == ["launch", "--status", "--output", "json"]
        return subprocess.CompletedProcess(args, 0,
            stdout='{"schema_version":1,"ok":true,"running":[{"profile":"launch-cd-qwen","port":8095,"state":"ready"}]}\ncd-qwen: ready\n')
    monkeypatch.setattr(strata_gateway, "_run", fake_run)
    rows = strata_gateway._running()
    assert rows[0]["profile"] == "launch-cd-qwen"


def test_auto_load_refuses_first_download(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    monkeypatch.setattr(strata_gateway, "_read", lambda: {
        "qwen": {"family": "qwen", "model": "iq2_xs", "port": 8095,
                 "context": "32k", "activated": False},
    })
    called = []
    monkeypatch.setattr(strata_gateway, "_run", lambda *a, **kw: called.append(a))
    assert strata_gateway._start("qwen", explicit=False)[0] is False
    assert called == []


def test_explicit_start_uses_fixed_cli_args_and_records_activation(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    models = tmp_path / "models.json"
    models.write_text(json.dumps({
        "qwen": {"family": "qwen", "model": "iq2_xs", "port": 8095,
                 "context": "32k", "activated": False},
    }))
    commands = []
    def fake_run(args, *, timeout=30):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"ok":true}')
    monkeypatch.setattr(strata_gateway, "_run", fake_run)
    monkeypatch.setattr(strata_gateway, "get_instance", lambda _a: {"loaded": False})
    ok, error = strata_gateway.start_instance("qwen")
    assert ok and not error
    assert commands[0][:5] == ["launch", "--family", "qwen", "--model", "iq2_xs"]
    assert "--no-tui" in commands[0] and "--yes" in commands[0]
    assert "--access" in commands[0] and "local" in commands[0]
    assert "--restart" not in commands[0]
    assert json.loads(models.read_text())["qwen"]["activated"] is True


def test_cannot_register_untrusted_family_or_injected_alias(monkeypatch, tmp_path):
    _managed(monkeypatch, tmp_path)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("bad;rm", family="qwen", model="iq2_xs", port=8095)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("qwen", family="evil", model="iq2_xs", port=8095)


def test_uninstall_never_deletes_models(monkeypatch, tmp_path):
    # Stop-check uses upstream 'running' array, not the unrelated launch 'serving' key.
    binary = tmp_path / "home" / ".local" / "bin" / "strata"
    binary.parent.mkdir(parents=True)
    binary.write_text("stub")
    marker = tmp_path / "managed.json"
    marker.write_text("{}")
    monkeypatch.setattr(strata, "_binary", lambda: binary)
    monkeypatch.setattr(strata, "_marker", lambda: marker)
    monkeypatch.setattr(strata, "_home", lambda: tmp_path / "home")
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata, "_run", lambda args, timeout=30: subprocess.CompletedProcess(
        args, 0, stdout='{"schema_version":1,"ok":true,"running":[]}'))
    monkeypatch.setattr(strata, "runtime_status", lambda: {"installed": False})
    models = tmp_path / "models"
    models.mkdir()
    (models / "important.gguf").write_bytes(b"original")
    assert strata.uninstall()["installed"] is False
    assert (models / "important.gguf").read_bytes() == b"original"
