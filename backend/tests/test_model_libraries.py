"""モデルライブラリ（複数ドライブのモデル保存先）。"""
from __future__ import annotations

import json

import pytest

from app.models_mgmt import libraries


def _volume(tmp_path, uuid="test-uuid-1234", mountpoint=None):
    return {
        "uuid": uuid, "device": "/dev/testp1", "mountpoint": mountpoint or str(tmp_path),
        "fstype": "ext4", "transport": "nvme", "model": "TEST SSD", "rotational": False,
        "total_bytes": 1_000_000_000_000, "free_bytes": 900_000_000_000,
        "writable": True, "is_system": False,
    }


def _use_libraries(monkeypatch, entries, volumes=()):
    monkeypatch.setattr(libraries, "detect_volumes", lambda: list(volumes))
    monkeypatch.setattr(libraries, "_configured", lambda: [dict(e) for e in entries])


def test_detect_volumes_skips_boot_pseudo_and_uuidless(monkeypatch):
    """ブート領域・擬似FS・UUIDなしはモデル置き場の候補にしない。"""
    payload = {"blockdevices": [{
        "name": "sda", "path": "/dev/sda", "type": "disk", "tran": "nvme",
        "model": "TEST SSD", "rota": False, "children": [
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "vfat",
             "uuid": "AAAA-BBBB", "mountpoint": "/boot/efi"},
            {"name": "sda2", "path": "/dev/sda2", "type": "part", "fstype": "squashfs",
             "uuid": "cccc", "mountpoint": "/snap/x"},
            {"name": "sda3", "path": "/dev/sda3", "type": "part", "fstype": "ext4",
             "uuid": "", "mountpoint": "/mnt/nouuid"},
            {"name": "sda4", "path": "/dev/sda4", "type": "part", "fstype": "cifs",
             "uuid": "dddd", "mountpoint": "/mnt/net"},
            {"name": "sda5", "path": "/dev/sda5", "type": "part", "fstype": "ext4",
             "uuid": "good-uuid", "mountpoint": "/"},
        ],
    }]}

    class _Result:
        stdout = json.dumps(payload)

    monkeypatch.setattr(libraries.subprocess, "run", lambda *a, **k: _Result())
    volumes = libraries.detect_volumes()
    assert [v["uuid"] for v in volumes] == ["good-uuid"]
    found = volumes[0]
    assert found["is_system"] is True
    # transport / model はディスク側から引き継ぐ
    assert found["transport"] == "nvme"
    assert found["model"] == "TEST SSD"


def test_library_resolves_through_volume_uuid_not_mount_name(tmp_path, monkeypatch):
    """マウント名が変わっても UUID で追従する。"""
    moved = tmp_path / "renamed"
    moved.mkdir()
    entry = {"id": "main", "label": "M", "volume_uuid": "u1", "subpath": "LLM",
             "path": "", "default": True}
    _use_libraries(monkeypatch, [entry], [_volume(moved, uuid="u1", mountpoint=str(moved))])
    assert libraries.library_path("main") == moved / "LLM"


def test_unmounted_volume_does_not_fall_back_to_system_drive(monkeypatch):
    """未接続ドライブは「未接続」として扱い、/ 側へ暗黙に落ちない。"""
    entry = {"id": "main", "label": "M", "volume_uuid": "missing", "subpath": "LLM",
             "path": "", "default": True}
    _use_libraries(monkeypatch, [entry], [])  # 該当UUIDはマウントされていない
    with pytest.raises(libraries.LibraryError):
        libraries.library_path("main")
    listed = libraries.list_libraries()
    assert listed[0]["mounted"] is False
    assert listed[0]["path"] == ""


def test_default_falls_back_to_mounted_library(tmp_path, monkeypatch):
    """既定ライブラリが未接続なら、接続済みのものへ退避する。"""
    available = tmp_path / "avail"
    available.mkdir()
    entries = [
        {"id": "gone", "label": "外付け", "volume_uuid": "missing", "subpath": "",
         "path": "", "default": True},
        {"id": "here", "label": "内蔵", "volume_uuid": "", "subpath": "",
         "path": str(available), "default": False},
    ]
    _use_libraries(monkeypatch, entries, [])
    assert libraries.default_library_id() == "here"


def test_no_available_library_raises(monkeypatch):
    entries = [{"id": "gone", "label": "外付け", "volume_uuid": "missing",
                "subpath": "", "path": "", "default": True}]
    _use_libraries(monkeypatch, entries, [])
    with pytest.raises(libraries.LibraryError):
        libraries.default_library_id()


def test_scan_marks_registered_and_orphan_files(tmp_path, monkeypatch):
    """instance から参照されていない GGUF を孤児として識別する。"""
    root = tmp_path / "lib"
    root.mkdir()
    used = root / "used.gguf"
    orphan = root / "orphan.gguf"
    used.write_bytes(b"x" * 10)
    orphan.write_bytes(b"y" * 20)
    entry = {"id": "main", "label": "M", "volume_uuid": "", "subpath": "",
             "path": str(root), "default": True}
    _use_libraries(monkeypatch, [entry])
    monkeypatch.setattr(
        libraries, "_referenced_paths", lambda: {str(used): ["alias-a"]},
    )
    from app.models_mgmt import ollama

    monkeypatch.setattr(ollama, "scan_gguf", lambda p: [
        {"name": "used.gguf", "path": str(used), "size": 10},
        {"name": "orphan.gguf", "path": str(orphan), "size": 20},
    ])
    result = libraries.scan_library("main")
    by_name = {f["name"]: f for f in result["files"]}
    assert by_name["used.gguf"]["registered"] is True
    assert by_name["used.gguf"]["used_by"] == ["alias-a"]
    assert by_name["orphan.gguf"]["registered"] is False
    assert by_name["orphan.gguf"]["suggest_alias"] == "orphan"
    assert libraries.list_libraries()[0]["orphan_count"] == 1


def test_validate_rejects_path_outside_allowed_roots(tmp_path, monkeypatch):
    entry = {"id": "bad", "label": "B", "volume_uuid": "", "subpath": "",
             "path": "/definitely/not/allowed", "default": True}
    monkeypatch.setattr(libraries, "detect_volumes", lambda: [])
    with pytest.raises(libraries.LibraryError, match="allowed_roots"):
        libraries.validate_entries([entry])


def test_validate_rejects_escaping_subpath(monkeypatch):
    monkeypatch.setattr(libraries, "detect_volumes", lambda: [])
    with pytest.raises(libraries.LibraryError, match=r"\.\."):
        libraries.validate_entries([
            {"id": "x", "label": "X", "volume_uuid": "u", "subpath": "../etc",
             "path": "", "default": True},
        ])


def test_validate_rejects_duplicate_ids(monkeypatch):
    monkeypatch.setattr(libraries, "detect_volumes", lambda: [])
    with pytest.raises(libraries.LibraryError, match="重複"):
        libraries.validate_entries([
            {"id": "x", "label": "A", "volume_uuid": "", "subpath": "", "path": "", "default": True},
            {"id": "x", "label": "B", "volume_uuid": "", "subpath": "", "path": "", "default": False},
        ])


def test_validate_forces_a_default(monkeypatch):
    monkeypatch.setattr(libraries, "detect_volumes", lambda: [])
    entries = libraries.validate_entries([
        {"id": "a", "label": "A", "volume_uuid": "u", "subpath": "", "path": "", "default": False},
    ])
    assert entries[0]["default"] is True


def test_unconfigured_policy_synthesizes_builtin_library(monkeypatch):
    """未設定環境では従来の data_dir/models/gguf が既定になる（後方互換）。"""
    from app.models_mgmt import runtime_policy

    monkeypatch.setattr(runtime_policy, "get_policy", lambda: runtime_policy.RuntimePolicy())
    monkeypatch.setattr(libraries, "detect_volumes", lambda: [])
    entries = libraries._configured()
    assert len(entries) == 1
    assert entries[0]["id"] == libraries.BUILTIN_LIBRARY_ID
    assert entries[0]["path"].endswith("models/gguf")


def test_library_api_routes(admin_client):
    volumes = admin_client.get("/api/v1/models/storage/volumes")
    assert volumes.status_code == 200
    assert isinstance(volumes.json(), list)

    listed = admin_client.get("/api/v1/models/libraries")
    assert listed.status_code == 200
    assert "libraries" in listed.json()

    bad = admin_client.put(
        "/api/v1/models/libraries",
        json=[{"id": "bad", "label": "B", "path": "/definitely/not/allowed"}],
        headers={"X-Requested-With": "ControlDeck"},
    )
    assert bad.status_code == 422

    missing = admin_client.get("/api/v1/models/libraries/does-not-exist/scan")
    assert missing.status_code == 404
