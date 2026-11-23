"""SQLite の全文検索（FTS5、trigram）。日本語は単語に分けられないので、3 文字ずつの索引にする（部分一致で探せる）。

外部コンテンツ表（content='news_items'）なので本文は二重に持たない。news_items への挿入・更新・削除はトリガーが追う。
ニュースの DB を開くとき（app/news/db.py）に呼ぶ。冪等。
SQLite 以外・FTS5 が無いときは何もせず、検索は LIKE になる。"""
from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger("control_deck.news.fts")

_COLUMNS = "title, summary, source"
_NEW = "new.title, new.summary, new.source"
_OLD = "old.title, old.summary, old.source"


def has_fts(conn) -> bool:
    if conn.dialect.name != "sqlite":
        return False
    return conn.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'news_items_fts'")).first() is not None


def create_fts(conn) -> bool:
    """作れたら True。すでにあれば作り直さない。作った直後は既存の行を索引へ入れる。"""
    if conn.dialect.name != "sqlite":
        return False
    if has_fts(conn):
        return True
    try:
        conn.execute(text(
            f"CREATE VIRTUAL TABLE news_items_fts USING fts5({_COLUMNS}, content='news_items', content_rowid='id', tokenize='trigram')"))
    except Exception as exc:  # noqa: BLE001 - FTS5・trigram が無い SQLite では LIKE にする
        logger.warning("全文検索（FTS5）を作れません。LIKE で探します: %s", exc)
        return False
    conn.execute(text(
        f"CREATE TRIGGER news_items_ai AFTER INSERT ON news_items BEGIN "
        f"INSERT INTO news_items_fts(rowid, {_COLUMNS}) VALUES (new.id, {_NEW}); END"))
    conn.execute(text(
        f"CREATE TRIGGER news_items_ad AFTER DELETE ON news_items BEGIN "
        f"INSERT INTO news_items_fts(news_items_fts, rowid, {_COLUMNS}) VALUES ('delete', old.id, {_OLD}); END"))
    conn.execute(text(
        f"CREATE TRIGGER news_items_au AFTER UPDATE ON news_items BEGIN "
        f"INSERT INTO news_items_fts(news_items_fts, rowid, {_COLUMNS}) VALUES ('delete', old.id, {_OLD}); "
        f"INSERT INTO news_items_fts(rowid, {_COLUMNS}) VALUES (new.id, {_NEW}); END"))
    conn.execute(text("INSERT INTO news_items_fts(news_items_fts) VALUES ('rebuild')"))
    return True


def drop_fts(conn) -> None:
    if conn.dialect.name != "sqlite":
        return
    for name in ("news_items_ai", "news_items_ad", "news_items_au"):
        conn.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
    conn.execute(text("DROP TABLE IF EXISTS news_items_fts"))
