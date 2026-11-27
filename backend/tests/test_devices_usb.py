"""USB 機器の見つけ方（sysfs ベース）と、OpenCode のプロジェクトの箱への受け渡し。

目的: (1) M5Paper の WCH ドライバ（/dev/ttyCH343USB0・by-id 無し）のように、by-id が無い USB シリアルも
/sys/class/tty の「USB の親」から見つかり、抜き差しで番号が変わっても同じ id で解決される。ブロックデバイス・USB でない tty は出ない。
(2) fastboot・iOS（usbmuxd のソケットの有無）が一覧に出る。(3) プロジェクトの箱は既定 off（argv が変わらない）で、
auto / selected のときだけ --device・keep-groups・usbmuxd の bind・環境変数・エージェントへの説明が入る。
入力のパスは --device に渡らない。設定は管理者だけで、監査 opencode.devices に残り、プロジェクトの外（data）に置く。
根拠: 2026-11-27 利用者「OpenCode で開発したソフトが USB 経由で書き込めない（M5Paper）」「M5 や iOS、Android の開発で
アクセス・書き込みできるように」。podman は呼ばず argv を見る。sysfs・/dev は一時フォルダの偽物（devices.SYS_ROOT・DEV_ROOT）。
"""
import json
import os
import socket
from pathlib import Path

import pytest

from app.integrations.opencode import provider, usb
from app.maintenance import devices, sandbox
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspaces import api  # noqa: F401


class Host:
    def __init__(self, root: Path):
        self.sys, self.dev = root / "sys", root / "dev"
        self.usb = self.sys / "bus" / "usb" / "devices"
        self.usb.mkdir(parents=True)
        (self.sys / "class" / "tty").mkdir(parents=True)
        self.dev.mkdir()
        self.sock = root / "usbmuxd"

    def usb_device(self, port, vendor, product, serial="", ifaces=(("02", "02", "00"),), manufacturer="", name=""):
        d = self.usb / port
        d.mkdir(exist_ok=True)
        for key, value in (("idVendor", vendor), ("idProduct", product), ("serial", serial), ("busnum", "1"),
                           ("devnum", "7"), ("manufacturer", manufacturer), ("product", name)):
            if value:
                (d / key).write_text(value + "\n")
        for n, (c, s, p) in enumerate(ifaces):
            i = d / f"{port}:1.{n}"
            i.mkdir(exist_ok=True)
            for key, value in (("bInterfaceClass", c), ("bInterfaceSubClass", s), ("bInterfaceProtocol", p)):
                (i / key).write_text(value + "\n")
        return d

    def tty(self, node, port, by_id=None):
        """/sys/class/tty/<node>/device -> USB のインターフェース、と /dev/<node>。"""
        (self.sys / "class" / "tty" / node).mkdir(exist_ok=True)
        link = self.sys / "class" / "tty" / node / "device"
        if link.is_symlink():
            link.unlink()
        link.symlink_to(self.usb / port / f"{port}:1.0")
        (self.dev / node).write_text("")
        if by_id:
            (self.dev / "serial" / "by-id").mkdir(parents=True, exist_ok=True)
            (self.dev / "serial" / "by-id" / by_id).symlink_to(self.dev / node)


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = Host(tmp_path / "host")
    monkeypatch.setattr(devices, "SYS_ROOT", h.sys)
    monkeypatch.setattr(devices, "DEV_ROOT", h.dev)
    monkeypatch.setattr(devices, "USBMUXD_SOCKET", h.sock)
    return h


M5 = "usb:1a86:55d4:52D4000426"


@pytest.fixture
def m5paper(host):
    host.usb_device("1-2", "1a86", "55d4", "52D4000426", name="USB Single Serial")
    host.tty("ttyCH343USB0", "1-2")
    return host


def test_ch343_without_by_id_is_found_with_synthetic_id(m5paper):
    items = devices.list_devices()
    assert [(d["kind"], d["id"], d["path"]) for d in items] == [("serial", M5, "/dev/ttyCH343USB0")]
    assert items[0]["guess"] == "M5Stack など（CH9102）" and items[0]["serial_tail"] == "0426"


def test_by_id_name_is_used_when_present(host):
    host.usb_device("1-3", "10c4", "ea60", "0001")
    host.tty("ttyUSB0", "1-3", by_id="usb-Silicon_Labs_CP2102-if00-port0")
    assert [d["id"] for d in devices.list_devices()] == ["usb-Silicon_Labs_CP2102-if00-port0"]


def test_id_without_serial_uses_usb_port_path(host):
    host.usb_device("1-4.2", "303a", "1001", "")
    host.tty("ttyACM0", "1-4.2")
    item = devices.list_devices()[0]
    assert item["id"] == "usb:303a:1001@1-4.2" and item["guess"] == "ESP32-S3（USB 直結）"


def test_same_device_resolves_after_replug_with_new_number(m5paper):
    entry = {"kind": "serial", "id": M5}
    assert devices.plan([entry])["devices"] == [str(m5paper.dev / "ttyCH343USB0")]
    (m5paper.dev / "ttyCH343USB0").unlink()
    (m5paper.sys / "class" / "tty" / "ttyCH343USB0" / "device").unlink()
    (m5paper.sys / "class" / "tty" / "ttyCH343USB0").rmdir()
    m5paper.tty("ttyCH343USB3", "1-2")
    assert devices.plan([entry])["devices"] == [str(m5paper.dev / "ttyCH343USB3")]
    (m5paper.dev / "ttyCH343USB3").unlink()
    gone = devices.plan([entry])
    assert gone["devices"] == [] and "見つからない" in gone["warnings"][0]


def test_non_usb_tty_block_device_and_bad_names_are_not_listed(m5paper):
    (m5paper.sys / "class" / "tty" / "ttyS0").mkdir()                      # USB の親を持たない（実機の 8250）
    (m5paper.dev / "ttyS0").write_text("")
    (m5paper.sys / "class" / "tty" / "sda").mkdir()                        # tty 名ではない（USB メモリ）
    (m5paper.sys / "class" / "tty" / "sda" / "device").symlink_to(m5paper.usb / "1-2" / "1-2:1.0")
    (m5paper.dev / "sda").write_text("")
    (m5paper.sys / "class" / "tty" / "tty..x").mkdir()
    assert [d["id"] for d in devices.list_devices()] == [M5]


def test_adb_fastboot_and_ios(host):
    host.usb_device("1-5", "18d1", "4ee0", "FB123", ifaces=(("ff", "42", "03"),), name="Pixel bootloader")
    host.usb_device("1-6", "18d1", "4ee7", "AD123", ifaces=(("ff", "42", "01"),), name="Pixel")
    host.usb_device("1-7", "05ac", "12a8", "00008030AAAA", ifaces=(("ff", "fe", "02"),), name="iPhone")
    host.usb_device("1-8", "0403", "6001", "NOTADB", ifaces=(("ff", "ff", "ff"),))
    items = {(d["kind"], d["id"]): d for d in devices.list_devices()}
    assert set(items) == {("fastboot", "18d1:4ee0:FB123"), ("adb", "18d1:4ee7:AD123"), ("ios", "05ac:12a8:00008030AAAA")}
    ios = items[("ios", "05ac:12a8:00008030AAAA")]
    assert ios["available"] is False and "usbmuxd" in ios["note"]
    assert devices.plan([{"kind": "ios", "id": "05ac:12a8:00008030AAAA"}])["binds"] == [], "usbmuxd が無ければ渡さない"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(host.sock))
    try:
        assert {(d["kind"]): d["available"] for d in devices.list_devices()}["ios"] is True
        planned = devices.plan([{"kind": "ios", "id": "05ac:12a8:00008030AAAA"}])
        assert planned["binds"] == [(host.sock, host.sock)] and planned["devices"] == []
    finally:
        srv.close()


def test_valid_entry_shapes():
    assert devices.valid_entry({"kind": "serial", "id": M5})
    assert devices.valid_entry({"kind": "fastboot", "id": "18d1:4ee0:FB123"})
    for bad in ({"kind": "serial", "id": "../etc"}, {"kind": "serial", "id": "a/b"}, {"kind": "block", "id": "sda"},
                {"kind": "ios", "id": "x"}, {"kind": "serial", "id": M5, "path": "/dev/sda"}, "x"):
        assert not devices.valid_entry(bad)


# ---- プロジェクトの箱 -----------------------------------------------------------------------

@pytest.fixture
def box(tmp_path, monkeypatch, m5paper):
    project = tmp_path / "CodeDEV" / "paper"
    project.mkdir(parents=True)
    config = tmp_path / "config"
    (config / "opencode").mkdir(parents=True)
    (config / "opencode" / "opencode.json").write_text(json.dumps({"instructions": ["/x/agent-notes.md"]}))
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    monkeypatch.setattr(provider.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox, "_rootfs", lambda: Path("/rootfs"))
    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path / "integration")
    (tmp_path / "integration").mkdir()
    monkeypatch.setattr(provider, "_skill_paths", lambda: [])
    monkeypatch.setattr(provider, "_addon_asset_roots", lambda: [])
    monkeypatch.setattr(sandbox, "_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(provider.Path, "home", lambda: tmp_path / "home")
    return project, config


def launch(project, config):
    return provider._launch("v2", ["opencode2", "serve"], project, config, interactive=False)[0]


def test_default_off_changes_nothing(box, m5paper):
    project, config = box
    argv = launch(project, config)
    assert "--device" not in argv and "keep-groups" not in argv
    assert not any(a.startswith("CONTROLDECK_DEVICES=") for a in argv)
    assert "controldeck-usb.md" not in (config / "opencode" / "opencode.json").read_text()


def test_auto_passes_device_group_env_and_notes(box, m5paper):
    project, config = box
    usb.put(project, "auto", [])
    argv = launch(project, config)
    real = str(m5paper.dev / "ttyCH343USB0")
    assert argv[argv.index("--device") + 1] == f"{real}:{real}:rw"
    assert argv[argv.index("--group-add") + 1] == "keep-groups"
    assert "--privileged" not in argv and "/dev:/dev" not in " ".join(argv)
    env = json.loads(next(a for a in argv if a.startswith("CONTROLDECK_DEVICES=")).split("=", 1)[1])
    assert env == [{"kind": "serial", "name": "M5Stack など（CH9102）", "path": real}]
    conf = json.loads((config / "opencode" / "opencode.json").read_text())
    assert conf["instructions"][0] == "/x/agent-notes.md" and conf["instructions"][-1].endswith("controldeck-usb.md")
    notes = (config / "opencode" / "controldeck-usb.md").read_text()
    assert real in notes and "sudo" in notes and "esptool" in notes


def test_selected_passes_only_chosen_and_unplugged_is_skipped(box, m5paper):
    project, config = box
    m5paper.usb_device("1-9", "10c4", "ea60", "0009")
    m5paper.tty("ttyUSB0", "1-9")
    usb.put(project, "selected", [{"kind": "serial", "id": "usb:10c4:ea60:0009"}])
    argv = launch(project, config)
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--device"]
    assert pairs == [f"{m5paper.dev}/ttyUSB0:{m5paper.dev}/ttyUSB0:rw"]
    (m5paper.dev / "ttyUSB0").unlink()
    assert "--device" not in launch(project, config), "抜けていれば渡さない"


def test_auto_ios_binds_usbmuxd_socket(box, m5paper):
    project, config = box
    m5paper.usb_device("1-7", "05ac", "12a8", "00008030AAAA", ifaces=(("ff", "fe", "02"),))
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(m5paper.sock))
    try:
        usb.put(project, "auto", [])
        argv = launch(project, config)
        assert f"{m5paper.sock}:{m5paper.sock}:rw" in argv
    finally:
        srv.close()


def test_input_path_is_never_passed_to_podman(box, m5paper):
    project, config = box
    with pytest.raises(ValueError):
        usb.put(project, "selected", [{"kind": "serial", "id": "/dev/sda"}])
    with pytest.raises(ValueError):
        usb.put(project, "selected", [{"kind": "serial", "id": M5, "path": "/dev/sda"}])
    with pytest.raises(ValueError):
        usb.put(project, "selected", [{"kind": "serial", "id": "usb:dead:beef:NOPE"}])   # つながっていない
    # ストアを直接書き換えても、渡るのは解決した実体だけ
    store = usb._store()
    store.write_text(json.dumps({str(project.resolve()): {"mode": "selected", "selected": [
        {"kind": "serial", "id": "/dev/sda"}, {"kind": "serial", "id": M5, "path": "/dev/sda"}]}}))
    assert usb.get(project)["selected"] == []
    assert "--device" not in launch(project, config)


def test_setting_is_outside_project_and_survives_garbage(box):
    project, _ = box
    usb.put(project, "auto", [])
    assert not any(p.name == "usb-projects.json" for p in project.rglob("*")), "プロジェクトの中に置かない（箱の AI が書ける）"
    usb._store().write_text("not json")
    assert usb.get(project) == {"mode": "off", "selected": []}


# ---- API ----------------------------------------------------------------------------------

def test_api_set_audit_and_restart(api, box, monkeypatch):  # noqa: F811
    from app.database import SessionLocal
    from app.integrations.opencode import chat_server, projects
    from app.models import AuditLog

    project, _ = box
    monkeypatch.setattr(projects, "_root", lambda: project.parent.resolve())
    url = "/api/v1/opencode/projects/paper/usb"
    assert api.get(url).json()["mode"] == "off"
    r = api.put(url, json={"mode": "auto", "selected": []}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["mode"] == "auto"
    assert api.put(url, json={"mode": "wild", "selected": []}, headers=CSRF_HEADERS).status_code == 422
    assert api.get("/api/v1/opencode/projects/nope/usb").status_code == 404
    assert api.get("/api/v1/opencode/projects/..%2Fx/usb").status_code in (404, 422)
    r = api.get("/api/v1/opencode/devices").json()
    assert r["devices"][0]["id"] == M5 and {t["name"] for t in r["tools"]} >= {"esptool", "adb"}
    stopped = []
    monkeypatch.setattr(chat_server, "_all", lambda: [chat_server.Server(
        key="abc", port=1, password="x", unit="u", config="c", project=str(project), permission_file="p",
        started_at=0, last_used=0, owner=1)])
    monkeypatch.setattr(chat_server, "stop", lambda key: stopped.append(key))
    assert api.get(url).json()["restart_needed"] is True
    assert api.post(url + "/restart", headers=CSRF_HEADERS).json() == {"stopped": 1} and stopped == ["abc"]
    with SessionLocal() as db:
        rows = [a for a in db.query(AuditLog).order_by(AuditLog.id) if a.action == "opencode.devices"]
    assert len(rows) >= 2


def test_api_requires_settings_manage_and_box_relay_hides_it():
    import inspect

    from app.integrations.opencode import router as r
    from app.maintenance import sandbox_gateway as gw

    for fn in (r.get_project_usb, r.put_project_usb, r.restart_project_usb, r.list_usb_devices):
        assert 'require_permission("settings.manage")' in inspect.getsource(fn)
    assert "/opencode/" not in Path(gw.__file__).read_text(), "箱の中の AI は設定 API を呼べない"
    assert os.path.exists(r.__file__)
