"""Android SDK（コマンドライン）の機能と、箱への受け渡し、ホストの adb の案内。

目的:
- 取得元の目録（URL・sha256）が固定で、公式のホスト（dl.google.com・https）以外へ行かないこと。取得は偽物に差し替える（本物を落とさない）。
- sha256 が違えば取り込まず止まること。zip の中のパスの逃げ出し（.. ・絶対パス・リンク）を拒否すること。
- sdkmanager は argv（shell なし）で呼び、ライセンスの yes はライセンスに同意したときだけ渡すこと。同意なしでは何も取得しない。
- 選べる platforms / build-tools は目録の範囲だけ。入っていなければ箱へ何も足さない（argv・PATH・環境変数が変わらない）。
  入っていれば読み取り専用で bind し、ANDROID_HOME・ANDROID_SDK_ROOT・PATH（platform-tools など）を渡し、エージェントへの前置きに道具の場所を書く。
- ホストの adb サーバー（adb のプロセス・5037 番の待ち受け）の検出と、kill-server（偽の実行・shell なし）。
- 機能の API は同意なしでは 422、同意があればジョブになること。自動更新の対象にならないこと。
根拠: 2026-11-27 利用者「Android とかにも対応してね」（M5・iOS・Android の開発を OpenCode で）。
本物の SDK・npm・pip・apt は取得しない（取得は fetch・run を差し替える）。
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from app.features import android_sdk as sdk
from app.features import auto_update, registry
from app.maintenance import devices, sandbox
from tests.test_dev_workspaces import api  # noqa: F401


def _zip(entries: dict[str, bytes | tuple[bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, value in entries.items():
            data, mode = value if isinstance(value, tuple) else (value, 0o100644)
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            z.writestr(info, data)
    return buffer.getvalue()


GOOD = _zip({"cmdline-tools/bin/sdkmanager": (b"#!/bin/sh\n", 0o100755), "cmdline-tools/lib/a.jar": b"x"})


def fake_fetch(payload: bytes):
    calls: list[str] = []

    def fetch(url: str, destination: Path, max_bytes: int) -> None:
        calls.append(url)
        destination.write_bytes(payload)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


class FakeRun:
    """sdkmanager の実行の偽物。platform-tools/adb を作る。argv と入力を記録する。"""

    def __init__(self, root: Path, returncode: int = 0):
        self.root, self.returncode, self.calls = root, returncode, []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert kwargs.get("shell") is None
        if self.returncode == 0 and not any(a in ("--licenses", "--update") for a in argv):
            (self.root / "platform-tools").mkdir(parents=True, exist_ok=True)
            (self.root / "platform-tools" / "adb").write_text("")
            (self.root / "platform-tools" / "fastboot").write_text("")
            (self.root / "platforms" / "android-34").mkdir(parents=True, exist_ok=True)
            (self.root / "build-tools" / "34.0.0").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr="boom" if self.returncode else "")


@pytest.fixture(autouse=True)
def java(monkeypatch):
    monkeypatch.setattr(sdk.shutil, "which", lambda name, *a, **k: "/usr/bin/java" if name == "java" else None)


@pytest.fixture
def pinned(monkeypatch):
    """目録の sha256 を偽物の zip に合わせる（本物の目録の値は別の試験で固定を確かめる）。"""
    spec = {**sdk.CATALOG["cmdline_tools"], "sha256": hashlib.sha256(GOOD).hexdigest()}
    monkeypatch.setitem(sdk.CATALOG, "cmdline_tools", spec)


def test_catalog_is_pinned_to_google_official():
    spec = sdk.CATALOG["cmdline_tools"]
    assert spec["url"].startswith("https://dl.google.com/android/repository/commandlinetools-linux-")
    assert spec["version"] in spec["url"] and len(spec["sha256"]) == 64 and set(spec["sha256"]) <= set("0123456789abcdef")
    assert spec["hosts"] == ("dl.google.com",)
    assert sdk.packages("34", "34.0.0") == ["platform-tools", "platforms;android-34", "build-tools;34.0.0"]
    for bad in (("33", "34.0.0"), ("34", "30.0.3"), ("34;rm", "34.0.0"), ("34", "34.0.0 --x")):
        with pytest.raises(sdk.AndroidSdkError):
            sdk.packages(*bad)


def test_real_fetch_refuses_hosts_outside_catalog(tmp_path):
    with pytest.raises(sdk.AndroidSdkError, match="目録の外"):
        sdk._fetch("https://example.com/x.zip", tmp_path / "x.zip", 10)
    with pytest.raises(sdk.AndroidSdkError, match="目録の外"):
        sdk._fetch("http://dl.google.com/x.zip", tmp_path / "x.zip", 10)


def test_install_without_consent_fetches_nothing(tmp_path):
    fetch = fake_fetch(GOOD)
    with pytest.raises(sdk.AndroidSdkError, match="同意"):
        sdk.install(accept_licenses=False, root=tmp_path / "android-sdk", fetch=fetch)
    assert fetch.calls == [] and not (tmp_path / "android-sdk").exists()


def test_install_runs_sdkmanager_with_argv_and_licenses_only_when_accepted(tmp_path, pinned):
    root = tmp_path / "tools" / "android-sdk"
    run = FakeRun(root)
    fetch = fake_fetch(GOOD)
    result = sdk.install(accept_licenses=True, root=root, fetch=fetch, run=run)
    assert fetch.calls == [sdk.CATALOG["cmdline_tools"]["url"]]
    assert result["installed"] and result["platforms"] == ["34"] and result["build_tools"] == ["34.0.0"]
    sdkmanager = str(root / "cmdline-tools" / "latest" / "bin" / "sdkmanager")
    licenses, packages = run.calls
    assert licenses[0] == [sdkmanager, f"--sdk_root={root}", "--licenses"] and licenses[1]["input"].startswith("y\n")
    assert packages[0] == [sdkmanager, f"--sdk_root={root}", "platform-tools", "platforms;android-34", "build-tools;34.0.0"]
    assert "input" not in packages[1] or packages[1]["input"] is None
    assert sdk.read_state(root)["licenses_accepted"] is True
    assert not (root / ".staging").exists()
    assert (root / "cmdline-tools" / "latest" / "bin" / "sdkmanager").stat().st_mode & 0o111


def test_sha256_mismatch_stops_before_extract(tmp_path):
    root = tmp_path / "tools" / "android-sdk"
    run = FakeRun(root)
    with pytest.raises(sdk.AndroidSdkError, match="sha256"):
        sdk.install(accept_licenses=True, root=root, fetch=fake_fetch(GOOD), run=run)   # 目録は本物の値なので一致しない
    assert run.calls == [] and not (root / "cmdline-tools").exists() and not (root / ".staging").exists()


@pytest.mark.parametrize("name", ["../evil", "/abs/evil", "cmdline-tools/../../evil", "a\\b"])
def test_zip_path_escape_is_rejected(tmp_path, name):
    archive = tmp_path / "bad.zip"
    archive.write_bytes(_zip({name: b"x"}))
    with pytest.raises(sdk.AndroidSdkError):
        sdk.safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil").exists()


def test_zip_symlink_member_is_rejected(tmp_path):
    archive = tmp_path / "link.zip"
    archive.write_bytes(_zip({"cmdline-tools/link": (b"/etc/passwd", 0o120777)}))
    with pytest.raises(sdk.AndroidSdkError, match="リンク"):
        sdk.safe_extract(archive, tmp_path / "out")


def test_unexpected_zip_layout_is_rejected(tmp_path, monkeypatch):
    payload = _zip({"other/readme": b"x"})
    monkeypatch.setitem(sdk.CATALOG, "cmdline_tools", {**sdk.CATALOG["cmdline_tools"], "sha256": hashlib.sha256(payload).hexdigest()})
    with pytest.raises(sdk.AndroidSdkError, match="想定と違"):
        sdk.install_cmdline_tools(tmp_path / "android-sdk", fetch=fake_fetch(payload))


def test_failed_sdkmanager_does_not_mark_installed(tmp_path, pinned):
    root = tmp_path / "tools" / "android-sdk"
    with pytest.raises(sdk.AndroidSdkError, match="失敗"):
        sdk.install(accept_licenses=True, root=root, fetch=fake_fetch(GOOD), run=FakeRun(root, returncode=1))
    assert not sdk.installed(root) and sdk.read_state(root) == {}


def test_missing_java_stops_before_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(sdk.shutil, "which", lambda *a, **k: None)
    fetch = fake_fetch(GOOD)
    with pytest.raises(sdk.AndroidSdkError, match="Java"):
        sdk.install(accept_licenses=True, root=tmp_path / "android-sdk", fetch=fetch)
    assert fetch.calls == []


def test_update_needs_recorded_consent_and_uninstall_stays_inside(tmp_path, pinned):
    root = tmp_path / "tools" / "android-sdk"
    sdk.install(accept_licenses=True, root=root, fetch=fake_fetch(GOOD), run=FakeRun(root))
    run = FakeRun(root)
    sdk.update(root=root, run=run)
    assert run.calls[0][0][-1] == "--update"
    sdk.uninstall(root)
    assert not root.exists()
    with pytest.raises(sdk.AndroidSdkError, match="管理領域の外"):
        sdk.uninstall(tmp_path)


def test_nothing_added_to_box_when_not_installed(tmp_path):
    root = tmp_path / "tools" / "android-sdk"
    assert sdk.path_entries(root) == [] and sdk.box_env(root) == {} and sdk.notes_lines(root) == []


def _installed_sdk(data: Path) -> Path:
    root = data / "tools" / "android-sdk"
    for rel in ("cmdline-tools/latest/bin/sdkmanager", "platform-tools/adb", "platform-tools/fastboot"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("")
    for rel in ("build-tools/34.0.0", "build-tools/35.0.0", "platforms/android-34"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def test_installed_sdk_goes_to_box_readonly_with_env_and_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "_data_dir", lambda: tmp_path)
    before = sandbox.tool_path()
    assert "android-sdk" not in before and sandbox._android_env() == {}
    root = _installed_sdk(tmp_path)
    assert sandbox._android_env() == {"ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)}
    path = sandbox.tool_path().split(":")
    assert path[:3] == [str(root / "platform-tools"), str(root / "cmdline-tools/latest/bin"), str(root / "build-tools/35.0.0")]
    assert path[3:] == before.split(":")
    assert root in sandbox._tool_dirs()
    notes = "\n".join(sdk.notes_lines(root))
    assert "adb devices" in notes and "sudo" in notes and str(root / "platform-tools") in notes and "gradlew" in notes


def test_usb_notes_include_android_only_when_installed(tmp_path, monkeypatch):
    from app.integrations.opencode import usb

    monkeypatch.setattr(sdk, "sdk_root", lambda data=None: tmp_path / "tools" / "android-sdk")
    items = [{"kind": "adb", "name": "Pixel", "path": "/dev/bus/usb/001/007"}]
    assert "Android の道具" not in usb.notes_text(items)
    _installed_sdk(tmp_path)
    assert "Android の道具" in usb.notes_text(items)


# ---------------------------------------------------------------- ホストの adb


def _proc(tmp_path: Path, comms: dict[int, str], listening: bool, monkeypatch) -> Path:
    proc = tmp_path / "proc"
    for pid, comm in comms.items():
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "comm").write_text(comm + "\n")
    (proc / "net").mkdir(exist_ok=True)
    header = "  sl  local_address rem_address   st\n"
    row = "   0: 0100007F:13AD 00000000:0000 0A 00000000:00000000\n"
    other = "   1: 0100007F:1F90 00000000:0000 0A 00000000:00000000\n"
    (proc / "net" / "tcp").write_text(header + (row if listening else other))
    (proc / "net" / "tcp6").write_text(header)
    monkeypatch.setattr(devices, "PROC_ROOT", proc)
    return proc


def test_host_adb_detected_by_process_or_port(tmp_path, monkeypatch):
    _proc(tmp_path, {10: "bash", 11: "adb"}, False, monkeypatch)
    assert devices.host_adb_status() == {"running": True, "pids": [11], "listening": False}
    _proc(tmp_path / "b", {10: "bash"}, True, monkeypatch)
    assert devices.host_adb_status() == {"running": True, "pids": [], "listening": True}
    _proc(tmp_path / "c", {10: "bash"}, False, monkeypatch)
    assert devices.host_adb_status()["running"] is False


def test_kill_host_adb_uses_argv_without_shell(tmp_path, monkeypatch):
    _proc(tmp_path, {10: "bash"}, False, monkeypatch)
    monkeypatch.setattr(devices.shutil, "which", lambda name, *a, **k: "/usr/bin/adb" if name == "adb" else None)
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = devices.kill_host_adb(run=run)
    assert seen[0][0] == ["/usr/bin/adb", "kill-server"] and not seen[0][1].get("shell")
    assert result == {"ok": True, "status": {"running": False, "pids": [], "listening": False}}
    monkeypatch.setattr(devices.shutil, "which", lambda *a, **k: None)
    with pytest.raises(FileNotFoundError):
        devices.kill_host_adb(run=run)


# ---------------------------------------------------------------- 機能の登録・API


def test_feature_is_registered_and_never_auto_updated(monkeypatch, tmp_path):
    assert registry.FEATURES["android-sdk"]["kind"] == "android-sdk" and registry.category("android-sdk") == "cli"
    monkeypatch.setattr(sdk, "sdk_root", lambda data=None: tmp_path / "tools" / "android-sdk")
    _installed_sdk(tmp_path)
    registry.invalidate_feature_cache()
    item = registry.status("android-sdk")
    assert item["installed"] and item["managed"] and item["android_sdk"]["platforms"] == ["34"]
    assert "android-sdk" not in [t["id"] for t in auto_update.targets()]
    with pytest.raises(registry.FeatureError, match="同意"):
        registry.install("android-sdk")


def test_install_api_requires_consent(admin_client):
    from tests.conftest import CSRF_HEADERS

    denied = admin_client.post("/api/v1/features/android-sdk/install-jobs", json={}, headers=CSRF_HEADERS)
    assert denied.status_code == 422 and "同意" in denied.json()["detail"]
    bad = admin_client.post("/api/v1/features/android-sdk/install-jobs",
                            json={"accept_licenses": True, "android_platform": "99"}, headers=CSRF_HEADERS)
    assert bad.status_code == 422


def test_devices_api_reports_host_adb_only_with_android_device(api, monkeypatch, tmp_path):
    from app.maintenance import sandbox as box

    monkeypatch.setattr(box, "tool_path", lambda: "/nonexistent")
    _proc(tmp_path, {11: "adb"}, False, monkeypatch)
    listing = [{"kind": "adb", "id": "18d1:4ee7:ABC", "label": "Pixel", "path": "/dev/bus/usb/001/007", "vendor": "", "serial_tail": "ABC",
                "guess": "", "available": True, "note": ""}]
    monkeypatch.setattr(devices, "list_devices", lambda: listing)
    body = api.get("/api/v1/opencode/devices").json()
    assert body["host_adb"]["running"] is True and body["android_sdk"] == {"installed": sdk.installed()}
    monkeypatch.setattr(devices, "list_devices", lambda: [])
    assert api.get("/api/v1/opencode/devices").json()["host_adb"]["running"] is False


def test_stop_host_adb_api_runs_kill_server(api, monkeypatch):
    from tests.conftest import CSRF_HEADERS

    seen = []
    monkeypatch.setattr(devices, "kill_host_adb", lambda extra=None, **k: seen.append(extra) or {"ok": True, "status": {"running": False}})
    response = api.post("/api/v1/opencode/devices/host-adb/stop", headers=CSRF_HEADERS)
    assert response.status_code == 200 and response.json()["ok"] is True and len(seen) == 1
    monkeypatch.setattr(devices, "kill_host_adb", lambda extra=None, **k: (_ for _ in ()).throw(FileNotFoundError("adb が見つかりません")))
    assert api.post("/api/v1/opencode/devices/host-adb/stop", headers=CSRF_HEADERS).status_code == 422
