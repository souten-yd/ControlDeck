"""ニュースの専用 DB（data_dir/news/news.sqlite3）。本体の DB・知識基盤の DB とは別のファイル。

- 記事・操作・全文検索（FTS5）・まとめを持つ。ニュースの量（最大 5 万件）と作り直しやすさのため、本体の移行（Alembic）に載せない。
- 知識基盤（app/knowledge/store.py）に倣い、開くとき表・索引を作り（冪等）、後から足した列はそのファイルを最初に開いたとき 1 回足す。
- user_id は整数のまま持つ（本体の users への外部キーは張らない。ファイルをまたぐ制約は作らない）。
  利用者は削除されない（無効にするだけ）ので、行の掃除は要らない。存在しない利用者の行は、その人の id で引かない限り誰にも見えない。
- 同期の SQLAlchemy。async から呼ぶときは asyncio.to_thread を使う（AGENTS.md 絶対ルール 9）。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import data_dir

logger = logging.getLogger("control_deck.news.db")


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NewsItem(Base):
    """ニュースの専門ページの記事（アーカイブ。ふだんは消さない）。同じ url は 1 行（更新だけ）。
    kind は release・news・model・event・forum・reddit（後から social などを足せる）。
    user_id は event のときだけ宛先（他は NULL＝全員）。全文検索の仮想表 news_items_fts は app/news/fts.py。"""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(120))
    source_key: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # 話題の語の JSON 配列（日本語をそのまま持つので LIKE で絞れる。ensure_ascii=False で書く）
    topics: Mapped[str] = mapped_column(Text, default="[]")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 一言要約（LLM。任意）。空なら使わない
    gist: Mapped[str] = mapped_column(Text, default="")
    # 記事のページから画像を取ろうとした印（取れなくても True。再試行しない）。app/news/images.py
    image_tried: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class NewsAction(Base):
    """利用者が記事にした操作（open・like・dislike・hide・star）。好みの計算の元。1 人・1 記事・1 種類で 1 行（時刻だけ更新）。"""

    __tablename__ = "news_actions"
    __table_args__ = (UniqueConstraint("user_id", "item_id", "action", name="uq_news_action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsDigest(Base):
    """日刊・週刊のまとめ。period=daily/weekly、day はその日（週刊は週の初めの月曜）の YYYY-MM-DD。同じ（period, day）は 1 行（作り直しで置き換え）。
    fallback=True は LLM を使えず作った「見出しの一覧」。error はそのときの理由（LLM で作れたときは空）。"""

    __tablename__ = "news_digests"
    __table_args__ = (UniqueConstraint("period", "day", name="uq_news_digest"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(8), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    item_ids: Mapped[str] = mapped_column(Text, default="[]")
    model: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str] = mapped_column(Text, default="")
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)


# 後から足した列（既にあるファイルにも足す。そのファイルを初めて開いたとき 1 回）。（表, 列, 宣言）
_LATE_COLUMNS: list[tuple[str, str, str]] = [("news_items", "image_tried", "BOOLEAN NOT NULL DEFAULT 0")]

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def path() -> Path:
    return data_dir() / "news" / "news.sqlite3"


def _add_missing_columns(conn) -> None:
    have = inspect(conn)
    for table, column, decl in _LATE_COLUMNS:
        if column not in {c["name"] for c in have.get_columns(table)}:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))


def _open(file: Path) -> Engine:
    file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{file}", connect_args={"timeout": 10, "check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi, _record):  # noqa: ANN001
        cur = dbapi.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    from app.news.fts import create_fts

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _add_missing_columns(conn)
        try:
            create_fts(conn)
        except Exception as exc:  # noqa: BLE001 - 検索は LIKE に落ちる
            logger.warning("全文検索の索引を用意できません: %s", exc)
    return engine


def engine() -> Engine:
    """いまの data_dir のニュース DB。ファイルが消えた（作り直した）ら開き直す。"""
    file = path()
    key = str(file)
    with _lock:
        found = _engines.get(key)
        if found is not None and not file.exists():
            found.dispose()
            found = None
        if found is None:
            found = _engines[key] = _open(file)
        return found


def session() -> Session:
    return sessionmaker(bind=engine(), expire_on_commit=True)()


def get_news_db() -> Iterator[Session]:
    """FastAPI の依存（本体の get_db のニュース版）。"""
    with session() as db:
        yield db
