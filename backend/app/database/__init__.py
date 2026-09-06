from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import db_url


class Base(DeclarativeBase):
    pass


_database_url = db_url()
_sqlite = _database_url.startswith("sqlite")
# 同時に開く接続の数。既定（5 + あふれ 10）は、Add-on の frame には足りない。
# 1 画面を開くだけで静的ファイルと API が何十本も同時に飛ぶ。
#
# 足りないときに何が起きるかが問題で、接続待ちは同期的に止まる。async の
# endpoint から ORM を触ると、その待ちは event loop の上で起きるので、1 本の
# 要求ではなくプロセス全体が止まる。実測では 30 秒（pool_timeout の既定）
# 止まり、systemd の watchdog に落とされていた。
#
# 数を増やして起きにくくし、待ち時間を短くして、起きても落ちないようにする。
# SQLite は WAL なので、読み手が増えても互いを塞がない。
_POOL_SIZE = 20
_MAX_OVERFLOW = 40
_POOL_TIMEOUT_SECONDS = 5.0

engine = create_engine(
    _database_url,
    connect_args={"check_same_thread": False} if _sqlite else {},
    pool_pre_ping=not _sqlite,
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    pool_timeout=_POOL_TIMEOUT_SECONDS,
)

if _sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
