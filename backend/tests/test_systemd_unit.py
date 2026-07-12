from pathlib import Path

import pytest

from app.applications.systemd import build_unit_content


def _build(**kw):
    defaults = dict(
        name="Test App",
        exec_argv=["/usr/bin/python3", "/opt/app/main.py"],
        working_directory="/opt/app",
        environment={},
        restart_policy="on-failure",
        stop_timeout_seconds=20,
        stdout_path=Path("/tmp/out.log"),
        stderr_path=Path("/tmp/err.log"),
    )
    defaults.update(kw)
    return build_unit_content(**defaults)


def test_basic_unit(snapshot=None):
    content = _build()
    assert 'ExecStart="/usr/bin/python3" "/opt/app/main.py"' in content
    assert "Restart=on-failure" in content
    assert "StandardOutput=append:/tmp/out.log" in content
    assert "WantedBy=default.target" in content


def test_argument_escaping():
    content = _build(
        exec_argv=["/usr/bin/python3", "/opt/app/main.py", 'a b"c', "$HOME", "100%"]
    )
    assert '"a b\\"c"' in content
    assert '"$$HOME"' in content  # 変数展開が無効化される
    assert '"100%%"' in content  # specifier 展開が無効化される


def test_newline_injection_rejected():
    with pytest.raises(ValueError):
        _build(exec_argv=["/usr/bin/python3", "x\nExecStartPre=/bin/evil"])


def test_env_injection_rejected():
    with pytest.raises(ValueError):
        _build(environment={"BAD KEY": "v"})
    with pytest.raises(ValueError):
        _build(environment={"OK": "v\nEvil=1"})


def test_relative_exec_rejected():
    with pytest.raises(ValueError):
        _build(exec_argv=["python3", "/opt/app/main.py"])


def test_invalid_restart_policy_rejected():
    with pytest.raises(ValueError):
        _build(restart_policy="whenever")


def test_description_sanitized():
    content = _build(name="evil\nname")
    assert "evil\nname" not in content
    assert "Description=Control Deck: evil name" in content
