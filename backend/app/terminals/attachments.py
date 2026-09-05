"""ターミナルへ渡す一時的な画像置き場。

携帯から撮った写真を PC 側へ送り、そのパスをターミナルへ打ち込むための置き場である。
ターミナルで動く CLI に渡すのが目的なので、ファイルとして開けるパスが要る。一方で
無限に溜まっても困るため、RAM 上（tmpfs）に置いて期限と上限で自動的に落とす。

- 置き場は tmpfs。ディスクには残らず、再起動でも消える。
- 期限を過ぎたもの、上限を超えたぶんは古い順に消す。
- 掃除は put と resolve のたびに行う。常駐タスクは持たない。
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("control_deck.terminals")

# 携帯の写真は数 MiB になる。1 枚あたりと全体の両方で頭を押さえる。
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ATTACHMENTS = 20
TTL_SECONDS = 60 * 60

# 拡張子は content-type から決める。利用者が付けた名前は使わない（パス操作を通さない）。
IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/avif": ".avif",
}


@dataclass(frozen=True)
class Attachment:
    id: str
    path: Path
    size: int
    expires_at: float


class AttachmentStore:
    """tmpfs 上の一時置き場。ひとつのプロセスが 1 ディレクトリを持つ。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else _default_root()
        # 前のプロセスの残りは引き継がない。「その時だけ参照できれば良い」ため。
        if self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, data: bytes, content_type: str) -> Attachment:
        """画像を置いて、ターミナルへ打ち込むパスを返す。"""
        suffix = IMAGE_SUFFIXES.get(content_type.split(";")[0].strip().lower())
        if suffix is None:
            raise ValueError(f"対応していない画像形式です: {content_type or '不明'}")
        if not data:
            raise ValueError("ファイルが空です")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"画像は{MAX_ATTACHMENT_BYTES // (1024 * 1024)}MiB以内にしてください")

        self._sweep()
        # 名前に空白や記号を入れない。ターミナルへそのまま打ち込むため、引用が要らない形にする。
        identifier = f"{time.strftime('%H%M%S')}-{secrets.token_hex(4)}"
        path = self._root / f"{identifier}{suffix}"
        # 書きかけを見せない。別名で書いてから置き換える。
        staging = path.with_name(f".{path.name}.part")
        staging.write_bytes(data)
        os.chmod(staging, 0o600)
        staging.replace(path)
        self._enforce_limits()
        logger.info("terminal attachment stored: %s (%d bytes)", path.name, len(data))
        return Attachment(identifier, path, len(data), time.time() + TTL_SECONDS)

    def list(self) -> list[Attachment]:
        self._sweep()
        return sorted(self._entries(), key=lambda item: item.path.stat().st_mtime)

    def discard(self, identifier: str) -> bool:
        """1 件だけ消す。id は自前で発行したものだけを受け付ける。"""
        for entry in self._entries():
            if entry.id == identifier:
                entry.path.unlink(missing_ok=True)
                return True
        return False

    def clear(self) -> None:
        for entry in self._entries():
            entry.path.unlink(missing_ok=True)

    def _entries(self) -> list[Attachment]:
        found: list[Attachment] = []
        for path in self._root.iterdir():
            if path.name.startswith(".") or not path.is_file():
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            found.append(Attachment(path.stem, path, stat.st_size, stat.st_mtime + TTL_SECONDS))
        return found

    def _sweep(self) -> None:
        now = time.time()
        for entry in self._entries():
            if entry.expires_at <= now:
                entry.path.unlink(missing_ok=True)

    def _enforce_limits(self) -> None:
        entries = sorted(self._entries(), key=lambda item: item.path.stat().st_mtime)
        total = sum(entry.size for entry in entries)
        # 古いものから落とす。新しく置いたものは必ず残す。
        while entries and (len(entries) > MAX_ATTACHMENTS or total > MAX_TOTAL_BYTES):
            dropped = entries.pop(0)
            total -= dropped.size
            dropped.path.unlink(missing_ok=True)


def _default_root() -> Path:
    """RAM 上の置き場。tmpfs が使えない環境では通常の一時領域へ落とす。"""
    shm = Path("/dev/shm")
    base = shm if shm.is_dir() and os.access(shm, os.W_OK) else Path(tempfile.gettempdir())
    return base / f"control-deck-terminal-{os.getuid()}"


store = AttachmentStore()
