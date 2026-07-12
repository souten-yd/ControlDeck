import os
from pathlib import Path

import pytest

from app.security.paths import ensure_within_roots


@pytest.fixture()
def sandbox(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "ok.txt").write_text("ok")
    (outside / "secret.txt").write_text("secret")
    return allowed, outside


def test_inside_allowed(sandbox):
    allowed, _ = sandbox
    p = ensure_within_roots(str(allowed / "ok.txt"), [str(allowed)])
    assert p.read_text() == "ok"


def test_traversal_rejected(sandbox):
    allowed, outside = sandbox
    with pytest.raises(PermissionError):
        ensure_within_roots(str(allowed / ".." / "outside" / "secret.txt"), [str(allowed)])


def test_symlink_escape_rejected(sandbox):
    allowed, outside = sandbox
    link = allowed / "link"
    os.symlink(outside / "secret.txt", link)
    with pytest.raises(PermissionError):
        ensure_within_roots(str(link), [str(allowed)])


def test_prefix_sibling_rejected(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data-evil").mkdir()
    with pytest.raises(PermissionError):
        ensure_within_roots(str(tmp_path / "data-evil"), [str(tmp_path / "data")])


def test_secret_masking():
    from app.security.crypto import mask_env

    masked = mask_env({"API_TOKEN": "x", "MY_PASSWORD": "y", "PORT": "8080"})
    assert masked["API_TOKEN"] == "••••••"
    assert masked["MY_PASSWORD"] == "••••••"
    assert masked["PORT"] == "8080"
