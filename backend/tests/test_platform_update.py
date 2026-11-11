"""ControlDeck self-update checks are intentionally read-only unless explicitly applied."""
import json

from app import platform_update as update


def test_release_tag_allowlist_rejects_branches_and_paths():
    assert update.TAG_PATTERN.fullmatch("ControlDeck_v0.12")
    assert update.TAG_PATTERN.fullmatch("ControlDeck_v0.12.1")
    for unsafe in ("main", "../main", "ControlDeck_v0.12;rm", "https://example.com/x", "ControlDeck_v0.12-rc1"):
        assert update.TAG_PATTERN.fullmatch(unsafe) is None


def test_wrong_remote_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "REPO_ROOT", tmp_path)
    def fake_git(*args, **kwargs):
        lookups = {
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("rev-parse", "HEAD"): "a" * 40,
            ("branch", "--show-current"): "main",
            ("status", "--porcelain", "--untracked-files=normal"): "",
            ("remote", "get-url", "origin"): "https://attacker.invalid/repo.git",
        }
        return lookups[args]
    monkeypatch.setattr(update, "git", fake_git)
    state = update.local_state()
    assert state["clean"] is True
    assert state["trusted_origin"] is False
    assert state["ready"] is False


def test_uncommitted_changes_block_update(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "REPO_ROOT", tmp_path)
    lookups = {
        ("rev-parse", "--show-toplevel"): str(tmp_path),
        ("rev-parse", "HEAD"): "a" * 40,
        ("branch", "--show-current"): "main",
        ("status", "--porcelain", "--untracked-files=normal"): " M frontend/src/pages/Settings.tsx",
        ("remote", "get-url", "origin"): "https://github.com/souten-yd/ControlDeck.git",
    }
    monkeypatch.setattr(update, "git", lambda *args, **kw: lookups[args])
    state = update.local_state()
    assert not state["ready"] and not state["clean"]


def test_status_file_atomic_roundtrip(tmp_path, monkeypatch):
    target = tmp_path / "status.json"
    monkeypatch.setattr(update, "status_file", lambda: target)
    update.write_state({"id": "abc", "status": "queued"})
    assert target.exists()
    assert json.loads(target.read_text())["status"] == "queued"
    assert update.read_state()["id"] == "abc"


def test_release_metadata_rejects_untrusted_origin(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, *args):
            return json.dumps({
                "tag_name": "ControlDeck_v0.12", "draft": False,
                "prerelease": False, "html_url": "https://example.org/release",
            }).encode("utf-8")
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda *a, **kw: Response())
    try:
        update.latest_release()
        assert False, "must reject bad release origin"
    except RuntimeError:
        pass


def test_update_http_does_not_apply_implicitly(admin_client, monkeypatch):
    monkeypatch.setattr(update, "local_state", lambda: {
        "head": "a" * 40, "branch": "main", "clean": True,
        "trusted_origin": True, "ready": True,
    })
    monkeypatch.setattr(update, "latest_release", lambda: {
        "tag": "ControlDeck_v0.12",
        "url": "https://github.com/souten-yd/ControlDeck/releases/tag/ControlDeck_v0.12",
    })
    monkeypatch.setattr(update, "git", lambda *args, **kwargs: "")
    response = admin_client.get("/api/v1/system/updates")
    assert response.status_code == 200, response.text
    assert response.json()["latest"]["tag"] == "ControlDeck_v0.12"


def test_update_apply_rejects_untrusted_checkout(admin_client, monkeypatch):
    from tests.conftest import CSRF_HEADERS
    monkeypatch.setattr(update, "local_state", lambda: {
        "head": "a" * 40, "branch": "other", "clean": True,
        "trusted_origin": False, "ready": False,
        "reason": "untrusted remote",
    })
    response = admin_client.post("/api/v1/system/updates/apply",
                                 json={"tag": "ControlDeck_v0.12"}, headers=CSRF_HEADERS)
    assert response.status_code == 409
