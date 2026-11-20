"""ナレッジの保存先・保存対象・バックアップ（app.knowledge.storage / backup）。

目的:
- 保存対象（DB・図・原本・抽出結果）ごとに保存先を分けられ、許可ルートの外・ネットワーク上の FS の SQLite を拒むこと。
- NAS が外れて目印が無いとき、そこへ黙って書かず「この PC」に置き、後で移せること。
- バックアップの世代を照合でき、稼働中のデータを上書きせずに空のディレクトリへ戻せること。

根拠: 2026-11-19 利用者「ローカル保存・サーバーへのバックアップ・図表などを分けて設定できるように。
保存場所と保存対象を分けて設定できれば柔軟にできる」。SQLite をネットワーク越しに開くとロックが効かず壊れる。
"""
from __future__ import annotations

import shutil

import pytest

from app.config import data_dir


@pytest.fixture()
def nas(monkeypatch):
    from app.knowledge import storage, store

    for sub in ("knowledge", "nas-test"):
        shutil.rmtree(data_dir() / sub, ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    root = data_dir() / "nas-test"
    root.mkdir()
    yield root
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()


def _add_target(path):
    from app.knowledge import storage

    cfg = storage.load()
    cfg["targets"]["nas"] = {"path": str(path), "label": "NAS", "id": ""}
    storage.save(cfg)
    return storage.init_target("nas")


def test_targets_must_be_inside_allowed_roots(nas):
    from app.knowledge import storage

    cfg = storage.load()
    cfg["targets"]["evil"] = {"path": "/etc", "label": ""}
    with pytest.raises(storage.StorageError):
        storage.save(cfg)


def test_sqlite_is_refused_on_network_filesystems(nas, monkeypatch):
    from app.knowledge import storage

    _add_target(nas)
    real = storage.filesystem
    monkeypatch.setattr(storage, "filesystem", lambda path: {"mount": str(nas), "fstype": "nfs4", "network": True, "source": ""}
                        if path.resolve().is_relative_to(nas.resolve()) else real(path))
    cfg = storage.load()
    cfg["placement"]["graphs"] = "nas"
    with pytest.raises(storage.StorageError, match="SQLite"):
        storage.save(cfg)
    cfg = storage.load()
    cfg["placement"]["figures"] = "nas"                      # 図はネットワーク上でもよい
    assert storage.save(cfg)["placement"]["figures"] == "nas"


def test_unmounted_target_is_not_written_and_blobs_move_later(nas):
    from app.knowledge import backup, storage, store

    status = _add_target(nas)
    assert status["available"] and (nas / storage.MARKER).exists()
    cfg = storage.load()
    cfg["placement"]["figures"] = "nas"
    storage.save(cfg)
    (nas / storage.MARKER).unlink()                          # NAS が外れて空のマウント先だけ残った
    store.create_collection("figs")
    digest = store.put_blob("general", b"\x89PNG fig", "image/png", role="figure")
    assert not (nas / "blobs").exists()
    assert store.blob_info(digest)[1]["location"] == "local"
    storage.init_target("nas")                               # つなぎ直した（同じ ID を書き直す）
    assert backup.relocate_blobs(dry_run=True)["planned"][0]["to"] == "nas"
    assert backup.relocate_blobs(dry_run=False)["moved"] == 1
    assert store.blob_path(digest).is_relative_to(nas)


def test_backup_verify_and_restore_to_an_empty_directory(nas, tmp_path):
    from app.knowledge import backup, storage, store

    _add_target(nas)
    cfg = storage.load()
    cfg["backup"]["targets"] = ["nas"]
    cfg["backup"]["keep"] = 1
    storage.save(cfg)
    store.create_collection("bk")
    store.add_document("bk", [store.NewChunk("バックアップの本文")])
    store.put_blob("general", b"%PDF original", "application/pdf", role="original")
    first = backup.run_backup()[0]
    assert first["ok"] and first["blobs"]["count"] == 1
    second = backup.run_backup()[0]
    assert second["blobs"]["copied"] == 0                    # blobs は差分だけ送る
    assert [s["stamp"] for s in backup.snapshots("nas")] == [second["stamp"]]   # 世代数 1
    with pytest.raises(backup.BackupError):
        backup.restore_to("nas", second["stamp"], store.shard_path("general").parent)   # 稼働中の場所へは戻さない
    restored = backup.restore_to("nas", second["stamp"], tmp_path / "restore")
    assert restored["shards"]["general"]["documents"] == 1
    manifest = next((nas / "backups" / second["stamp"]).glob("manifest.json"))
    manifest.write_text(manifest.read_text().replace('"sha256": "', '"sha256": "0', 1))
    assert backup.verify("nas", second["stamp"])["ok"] is False
