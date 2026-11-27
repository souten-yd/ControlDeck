"""作業場へ渡す USB 機器（第 1 段: シリアルと adb の USB）。

目的: 既定は何も渡さず、利用者が選んだ機器だけが箱の podman 起動の --device に入ること。選んでいない機器・
存在しない機器・by-id の外のパス・形の悪い指定は入らないこと。選べるのは管理者（settings.manage）だけで、
箱の中継は機器の API を通さないこと。付け外しは監査に残ること。
根拠: 2026-11-23 利用者「USB につながってる装置（Android・M5Stack・ラズパイ）を OpenCode から開発できないか。
安全にできないか。作業場ごとに許可する設定でもよい」。sysfs・/dev はテスト用の置き場、podman は呼ばない。
"""
import json
from pathlib import Path

import pytest

from app.maintenance import devices, sandbox, workspaces
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspaces import api, repo, workspaces_create_box  # noqa: F401

SERIAL = "usb-Silicon_Labs_CP2102_USB_to_UART_0001-if00-port0"
OTHER = "usb-FTDI_FT232R_USB_UART_A1B2C3D4-if00-port0"
ADB = "18d1:4ee7:PIXEL12345"


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    sysroot, dev = tmp_path / "sys", tmp_path / "dev"
    (dev / "serial" / "by-id").mkdir(parents=True)
    for node in ("ttyUSB0", "ttyUSB1"):
        (dev / node).write_text("")
    (dev / "serial" / "by-id" / SERIAL).symlink_to(dev / "ttyUSB0")
    (dev / "serial" / "by-id" / OTHER).symlink_to(dev / "ttyUSB1")
    (dev / "serial" / "by-id" / "evil").symlink_to("/etc/passwd")     # by-id にあっても tty でなければ出さない
    (dev / "bus" / "usb" / "001").mkdir(parents=True)
    (dev / "bus" / "usb" / "001" / "004").write_text("")
    usb = sysroot / "bus" / "usb" / "devices"

    def add(name, vendor, product, serial, bus, num, *, adb):
        d = usb / name
        d.mkdir(parents=True)
        for key, value in (("idVendor", vendor), ("idProduct", product), ("serial", serial), ("busnum", bus),
                           ("devnum", num), ("manufacturer", "Google"), ("product", "Pixel")):
            (d / key).write_text(value + "\n")
        i = d / f"{name}:1.0"
        i.mkdir()
        for key, value in (("bInterfaceClass", "ff" if adb else "02"), ("bInterfaceSubClass", "42" if adb else "02"),
                           ("bInterfaceProtocol", "01" if adb else "00")):
            (i / key).write_text(value + "\n")

    add("1-2", "18d1", "4ee7", "PIXEL12345", "1", "4", adb=True)
    # シリアルの tty は /sys/class/tty の device が USB のインターフェースを指すものだけ出る（by-id の有無に依らない）
    for node, port, serial in (("ttyUSB0", "1-6", "0001"), ("ttyUSB1", "1-7", "A1B2C3D4")):
        add(port, "10c4", "ea60", serial, "1", "9", adb=False)
        (sysroot / "class" / "tty" / node).mkdir(parents=True)
        (sysroot / "class" / "tty" / node / "device").symlink_to(usb / port / f"{port}:1.0")
    add("1-3", "0403", "6001", "A1B2C3D4", "1", "5", adb=False)      # adb ではない
    monkeypatch.setattr(devices, "SYS_ROOT", sysroot)
    monkeypatch.setattr(devices, "DEV_ROOT", dev)
    return dev


def test_listing_shows_only_serial_ttys_and_adb_with_serial(fake_host):
    items = {(d["kind"], d["id"]): d for d in devices.list_devices()}
    assert set(items) == {("serial", SERIAL), ("serial", OTHER), ("adb", ADB)}
    assert items[("adb", ADB)]["path"] == "/dev/bus/usb/001/004" and items[("adb", ADB)]["serial_tail"] == "2345"
    assert "PIXEL12345" not in json.dumps(items[("adb", ADB)]["serial_tail"]), "画面にはシリアルの末尾だけ"


def test_no_devices_by_default_and_no_device_args(repo, fake_host):
    box = workspaces_create_box()
    assert box.devices == [] and workspaces.summary(box)["devices"] == []
    argv = sandbox.container_argv(["true"], workdir=box.repo, writable=[box.path], devices=[])
    assert "--device" not in argv and "keep-groups" not in argv


def test_only_selected_devices_reach_podman_args(repo, fake_host, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(sandbox, "_rootfs", lambda: Path("/rootfs"))
    box = workspaces_create_box()
    workspaces.set_devices(box.name, "admin", [{"kind": "serial", "id": SERIAL}, {"kind": "adb", "id": ADB}])
    box = sandbox.load(box.name)
    argv = sandbox.podman_argv(box, ["true"])
    dev = fake_host
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--device"]
    assert pairs == [f"{dev}/ttyUSB0:{dev}/ttyUSB0:rw", f"{dev}/bus/usb/001/004:{dev}/bus/usb/001/004:rw"]
    assert not any("ttyUSB1" in p for p in pairs), "選んでいない機器は入らない"
    assert argv[argv.index("--group-add") + 1] == "keep-groups"


def test_missing_or_unplugged_device_is_not_passed_and_warns(repo, fake_host, caplog):
    box = workspaces_create_box()
    workspaces.set_devices(box.name, "admin", [{"kind": "serial", "id": SERIAL}])
    (fake_host / "serial" / "by-id" / SERIAL).unlink()
    paths, warnings = devices.resolve(sandbox.load(box.name).devices)
    assert paths == [] and "見つからない" in warnings[0]


def test_bad_entries_are_refused(repo, fake_host):
    box = workspaces_create_box()
    for bad in ({"kind": "serial", "id": "../../etc/passwd"}, {"kind": "serial", "id": "a/b"},
                {"kind": "serial", "id": "evil"}, {"kind": "disk", "id": SERIAL},
                {"kind": "serial", "id": SERIAL, "path": "/dev/sda"}, {"kind": "adb", "id": "18d1:4ee7:"},
                {"kind": "serial", "id": "usb-NotPlugged-if00"}):
        with pytest.raises(sandbox.SandboxError):
            workspaces.set_devices(box.name, "admin", [bad])
    assert sandbox.load(box.name).devices == []


def test_tampered_meta_devices_is_rejected_on_load(repo, fake_host):
    box = workspaces_create_box()
    meta = json.loads(sandbox._meta_path(box.name).read_text())
    meta["devices"] = [{"kind": "serial", "id": "../../dev/sda"}]
    sandbox._meta_path(box.name).write_text(json.dumps(meta))
    with pytest.raises(sandbox.SandboxError, match="devices"):
        sandbox.load(box.name)


def test_other_users_workspace_cannot_be_changed(repo, fake_host):
    box = workspaces_create_box()
    with pytest.raises(sandbox.SandboxError, match="ありません"):
        workspaces.set_devices(box.name, "someone", [{"kind": "serial", "id": SERIAL}])


def test_api_list_set_audit_and_gateway_blocks(repo, fake_host, api):
    from app.database import SessionLocal
    from app.models import AuditLog

    name = workspaces_create_box().name
    r = api.get("/api/v1/opencode/devices")
    assert r.status_code == 200 and {d["id"] for d in r.json()["devices"]} == {SERIAL, OTHER, ADB}
    r = api.put(f"/api/v1/opencode/workspaces/{name}/devices", json={"devices": [{"kind": "serial", "id": SERIAL}]},
                headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["added"] == [{"kind": "serial", "id": SERIAL}]
    r = api.put(f"/api/v1/opencode/workspaces/{name}/devices", json={"devices": []}, headers=CSRF_HEADERS)
    assert r.json()["removed"] == [{"kind": "serial", "id": SERIAL}]
    with SessionLocal() as db:
        actions = [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]
    assert "opencode.workspace.device_attach" in actions and "opencode.workspace.device_detach" in actions
    r = api.put(f"/api/v1/opencode/workspaces/{name}/devices", json={"devices": [{"kind": "serial", "id": "x/y"}]},
                headers=CSRF_HEADERS)
    assert r.status_code >= 400


def test_box_gateway_does_not_expose_device_api():
    """箱の中（AI）から届く道は、中継が許したものだけ。機器の API はその中に無い。"""
    from app.maintenance import sandbox_gateway as gw

    source = Path(gw.__file__).read_text()
    assert "/opencode/" not in source and "devices" not in source


def test_api_requires_settings_manage():
    import inspect

    from app.integrations.opencode import router as r

    for fn in (r.list_usb_devices, r.set_workspace_devices):
        assert 'require_permission("settings.manage")' in inspect.getsource(fn)
