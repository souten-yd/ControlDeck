"""./deck.sh knowledge-storage — 保存先・保存対象・バックアップの操作。

  status                         保存先の状態と置き場所の一覧
  add-target NAME PATH [LABEL]   保存先を登録して目印を置く（PATH は OS でマウント済みのディレクトリ）
  place CATEGORY TARGET          保存対象の置き場所を変える（originals / figures / extractions / graphs）
                                 DB は db.general などの形。既に DB があるときは move-db を使う
  backup-targets [NAME ...]      バックアップ先を設定（空にすると無効）。--keep N で世代数
  backup [NAME]                  いますぐバックアップ
  list NAME                      世代の一覧
  verify NAME STAMP              世代の照合
  restore-to NAME STAMP DIR      空のディレクトリへ戻して照合（稼働中のデータは上書きしない）
  relocate [--apply] [--keep-source]  blobs を設定どおりの保存先へ移す（既定は計画の表示だけ）
  move-db SHARD TARGET           分野の DB を移す（ControlDeck を止めてから）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from app.knowledge import backup, storage


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def _service_active() -> bool:
    try:
        result = subprocess.run(["systemctl", "--user", "is-active", "control-deck-web.service"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "active"


def overview() -> dict:
    cfg = storage.load()
    return {"targets": [storage.status(name) for name in cfg["targets"]], "placement": cfg["placement"],
            "backup": cfg["backup"], "categories": storage.CATEGORIES}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="./deck.sh knowledge-storage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("add-target")
    p.add_argument("name")
    p.add_argument("path")
    p.add_argument("label", nargs="?", default="")
    p = sub.add_parser("place")
    p.add_argument("category")
    p.add_argument("target")
    p = sub.add_parser("backup-targets")
    p.add_argument("names", nargs="*")
    p.add_argument("--keep", type=int)
    p = sub.add_parser("backup")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("list")
    p.add_argument("name")
    p = sub.add_parser("verify")
    p.add_argument("name")
    p.add_argument("stamp")
    p = sub.add_parser("restore-to")
    p.add_argument("name")
    p.add_argument("stamp")
    p.add_argument("dest", type=Path)
    p = sub.add_parser("relocate")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--keep-source", action="store_true")
    p = sub.add_parser("move-db")
    p.add_argument("shard")
    p.add_argument("target")
    p.add_argument("--force", action="store_true", help="ControlDeck が動いていても実行する（非推奨）")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "status":
            _print(overview())
        elif args.cmd == "add-target":
            cfg = storage.load()
            cfg["targets"][args.name] = {"path": args.path, "label": args.label, "id": ""}
            storage.save(cfg)
            _print(storage.init_target(args.name))
        elif args.cmd == "place":
            cfg = storage.load()
            if args.category.startswith("db."):
                cfg["placement"]["db"][args.category[3:]] = args.target
            elif args.category in ("graphs", *storage.BLOB_CATEGORIES):
                cfg["placement"][args.category] = args.target
            else:
                raise storage.StorageError(f"保存対象がありません: {args.category}")
            _print(storage.save(cfg)["placement"])
        elif args.cmd == "backup-targets":
            cfg = storage.load()
            cfg["backup"]["targets"] = args.names
            if args.keep is not None:
                cfg["backup"]["keep"] = args.keep
            _print(storage.save(cfg)["backup"])
        elif args.cmd == "backup":
            _print(backup.run_backup(args.name, progress=lambda m: print(m, file=sys.stderr)))
        elif args.cmd == "list":
            _print(backup.snapshots(args.name))
        elif args.cmd == "verify":
            _print(backup.verify(args.name, args.stamp))
        elif args.cmd == "restore-to":
            _print(backup.restore_to(args.name, args.stamp, args.dest))
        elif args.cmd == "relocate":
            _print(backup.relocate_blobs(dry_run=not args.apply, keep_source=args.keep_source))
        elif args.cmd == "move-db":
            if _service_active() and not args.force:
                raise storage.StorageError("ControlDeck が動いています。止めてから実行してください（./deck.sh stop）")
            _print(backup.move_db(args.shard, args.target))
    except (storage.StorageError, backup.BackupError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
