"""Add-on の frame が event loop を止めないこと。

実測（2026-09-06）: モバイルで SonicForge を開くと 1 画面から何十本も同時に
要求が飛び、pool（既定 5+10）が空になった。async の endpoint から ORM を触ると
接続待ちは event loop の上で起きるので、1 本の要求ではなくプロセス全体が
30 秒止まり、systemd の watchdog に SIGABRT で落とされていた。3 日で 68 回。
"""

from __future__ import annotations

import inspect

from app.addons import proxy
from app.database import engine
from app.security import sessions


def test_the_session_is_handed_back_before_the_upstream_round_trip():
    """上流へ行く間、pool の接続を抱えたままにしない。"""
    source = inspect.getsource(proxy._frame_user)
    assert "db.close()" in source


def test_the_role_is_loaded_with_the_user():
    """あとから user.role を触ると、そこで問い合わせが走る。

    権限の判定は async の endpoint の中なので、その問い合わせは loop の上で
    同期的に待つことになる。
    """
    for source in (inspect.getsource(sessions.resolve_session), inspect.getsource(proxy._frame_user)):
        assert "joinedload(User.role)" in source


def test_waiting_for_a_connection_cannot_outlast_the_watchdog():
    """枯渇しても落とされない。watchdog は 30 秒で SIGABRT を送る。"""
    assert engine.pool._timeout <= 10
    assert engine.pool.size() >= 20
