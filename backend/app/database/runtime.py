"""Control Deck本体DBのURL・EnvironmentFile安全境界。"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy.engine import URL, make_url

_ENV_KEY = "CONTROL_DECK_DB_URL"
_MAX_ENV_BYTES = 4096
_SUPPORTED_BACKENDS = {"sqlite", "postgresql"}


def validate_database_url(raw: str) -> URL:
    value = raw.strip()
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("database URLが空、または不正な制御文字を含んでいます")
    try:
        url = make_url(value)
    except Exception as exc:
        raise ValueError("database URLの形式が不正です") from exc
    backend = url.get_backend_name()
    decoded = tuple(unquote(component) if component else component
                    for component in (url.username, url.password, url.host, url.database))
    if any(component and any(marker in component for marker in ("\x00", "\n", "\r")) for component in decoded):
        raise ValueError("database URLの構成要素に不正な制御文字を含んでいます")
    if backend not in _SUPPORTED_BACKENDS:
        raise ValueError("Control Deck本体DBはSQLiteまたはPostgreSQLだけを利用できます")
    if backend == "postgresql" and url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("PostgreSQL driverはpsycopgだけを利用できます")
    if backend == "postgresql" and not url.database:
        raise ValueError("PostgreSQL database名が必要です")
    if backend == "sqlite" and not url.database:
        raise ValueError("SQLite database pathが必要です")
    return url


def normalized_database_url(raw: str) -> str:
    url = validate_database_url(raw)
    if url.get_backend_name() == "postgresql":
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
    elif url.database != ":memory:":
        database = Path(str(url.database)).expanduser()
        if not database.is_absolute():
            raise ValueError("SQLite database pathは絶対pathで指定してください")
        url = url.set(database=str(database.resolve(strict=False)))
    return url.render_as_string(hide_password=False)


def read_database_env(path: Path, *, required: bool = False) -> str | None:
    """固定形式のEnvironmentFileをshell評価せず読み込む。"""
    candidate = path.expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(candidate, flags)
    except FileNotFoundError:
        if required:
            raise ValueError("database EnvironmentFileが見つかりません")
        return None
    except OSError as exc:
        raise ValueError("database EnvironmentFileはsymlinkではない通常fileにしてください") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("database EnvironmentFileは通常fileにしてください")
        if metadata.st_uid != os.getuid():
            raise ValueError("database EnvironmentFileのownerが実行ユーザーではありません")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("database EnvironmentFileはmode 0600にしてください")
        if metadata.st_size > _MAX_ENV_BYTES:
            raise ValueError("database EnvironmentFileが大きすぎます")
        payload = os.read(fd, _MAX_ENV_BYTES + 1)
    finally:
        os.close(fd)
    if b"\x00" in payload:
        raise ValueError("database EnvironmentFileにNULを含めないでください")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("database EnvironmentFileはUTF-8で保存してください") from exc
    prefix = f"{_ENV_KEY}="
    if len(lines) != 1 or not lines[0].startswith(prefix):
        raise ValueError(f"database EnvironmentFileは{_ENV_KEY}=...の1行だけにしてください")
    return normalized_database_url(lines[0][len(prefix):])


def describe_database_url(raw: str) -> str:
    """passwordを含めず運用診断用の接続先を返す。"""
    url = validate_database_url(raw)
    if url.get_backend_name() == "sqlite":
        return f"backend=sqlite database={url.database}"
    host = url.host or "local-socket"
    port = url.port or 5432
    return f"backend=postgresql host={host} port={port} database={url.database}"
