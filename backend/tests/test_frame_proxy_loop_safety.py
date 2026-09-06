"""Add-on の frame が event loop を止めないこと。

実測（2026-09-06）: モバイルで SonicForge を開くと 1 画面から何十本も同時に
要求が飛び、pool（既定 5+10）が空になった。async の endpoint から ORM を触ると
接続待ちは event loop の上で起きるので、1 本の要求ではなくプロセス全体が
30 秒止まり、systemd の watchdog に SIGABRT で落とされていた。3 日で 68 回。

止まった瞬間の stack:

    addon_frame_proxy (async)
      → user_permissions → user.role の遅延読み込み
        → sqlalchemy pool/queue.py:201 get   ← 接続待ちで同期ブロック

守るのは 3 点。権限の判定が問い合わせを起こさないこと、上流への往復の間 pool の
接続を抱えないこと、枯渇しても watchdog より先に諦めること。
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import event

from app.addons import proxy
from app.database import SessionLocal, engine
from app.security.deps import user_permissions
from app.security.sessions import create_session, resolve_session


@pytest.fixture()
def _db(client):  # client は DB の初期化と admin の作成を済ませる
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _statements_while(db, action) -> int:
    """action の間に飛んだ SQL の本数。"""
    counted = 0

    def _count(*_args, **_kwargs):
        nonlocal counted
        counted += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    return counted


def test_reading_permissions_does_not_query(_db):
    """権限の判定で問い合わせが走らないこと。

    走ると、それは async の endpoint の中で同期的に待つ問い合わせになる。
    pool が空なら event loop ごと止まる。
    """
    from app.models import User

    user = _db.query(User).filter(User.username == "admin").one()
    token = create_session(_db, user, "127.0.0.1", "pytest")
    _db.expunge_all()

    resolved = resolve_session(_db, token)
    assert resolved is not None
    _session, loaded = resolved

    assert _statements_while(_db, lambda: user_permissions(loaded)) == 0


def test_the_session_is_handed_back_before_the_upstream_round_trip():
    """上流へ行く間、pool の接続を抱えたままにしない。"""
    assert "db.close()" in inspect.getsource(proxy._frame_user)


def test_waiting_for_a_connection_cannot_outlast_the_watchdog():
    """枯渇しても落とされない。watchdog は 30 秒で SIGABRT を送る。"""
    assert engine.pool._timeout <= 10
    assert engine.pool.size() >= 20
