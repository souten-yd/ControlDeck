"""Safety checks for the optional Strata Terminal and gateway lifecycle."""
import asyncio
import json
import subprocess

import pytest

from app.models_mgmt import llama, strata, strata_gateway


def test_strata_unsupported_platform_fails_without_download(monkeypatch):
    monkeypatch.setattr(strata, "available", lambda: False)
    monkeypatch.setattr(strata, "_download_installer", lambda: pytest.fail("unexpected download"))
    with pytest.raises(strata.StrataError):
        strata._install()


def test_strata_registration_is_opt_in_without_model_download(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(strata_gateway, "_running", lambda: [])
    monkeypatch.setattr(llama, "list_instances", lambda: [])
    monkeypatch.setattr(strata_gateway, "_run", lambda *a, **kw: pytest.fail("no CLI at registration"))
    item = strata_gateway.save_instance("coding", family="qwen", model="iq2_xs", port=8909)
    assert item["activated"] is False
    assert item["runtime"] == "strata"
    assert item["loaded"] is False
    ok, message = strata_gateway._start("coding", explicit=False)
    assert ok is False
    assert "明示" in message
    deleted = strata_gateway.delete_instance("coding")
    assert deleted["model_files_deleted"] is False


def test_strata_rejects_unknown_family_and_port(tmp_path, monkeypatch):
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata_gateway, "_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(llama, "list_instances", lambda: [])
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("a", family="arbitrary", model="iq2_xs", port=8999)
    with pytest.raises(strata_gateway.StrataGatewayError):
        strata_gateway.save_instance("a", family="qwen", model="iq2_xs", port=80)


def test_strata_update_refuses_to_stop_active_model(monkeypatch):
    monkeypatch.setattr(strata, "installed", lambda: True)
    monkeypatch.setattr(strata, "_run", lambda *a, **kw:
                        subprocess.CompletedProcess(args=[], returncode=7, stdout="", stderr="active"))
    with pytest.raises(strata.StrataError, match="稼働中"):
        strata._update()


def test_strata_uninstall_only_managed_cli_and_engine(tmp_path, monkeypatch):
    root = tmp_path / "strata"
    binary = root / "home" / ".local" / "bin" / "strata"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"dummy")
    binary.chmod(0o755)
    (root / "managed.json").write_text(json.dumps({"managed": True}), encoding="utf-8")
    component = root / "home" / ".local" / "share" / "strata" / "components"
    component.mkdir(parents=True)
    (component / "engine.bin").write_bytes(b"engine")
    models = tmp_path / "models"
    models.mkdir()
    (models / "example.gguf").write_bytes(b"weights")
    monkeypatch.setattr(strata, "_root", lambda: root)
    monkeypatch.setattr(strata, "_run", lambda *a, **kw:
                        subprocess.CompletedProcess(args=[], returncode=0,
                                                    stdout='{"running":[]}', stderr=""))
    strata.uninstall()
    assert not binary.exists()
    assert not (root / "managed.json").exists()
    assert not component.exists()
    assert (models / "example.gguf").exists()
