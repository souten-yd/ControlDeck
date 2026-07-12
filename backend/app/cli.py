"""管理 CLI。使用例:

  python -m app.cli create-admin <username>            # パスワードは対話入力
  python -m app.cli reset-password <username>
  python -m app.cli reset-totp <username>              # 二要素認証を解除（ロックアウト復旧用）
  python -m app.cli reset-totp --all                   # 全ユーザーの TOTP を解除
  python -m app.cli register-local-desktop             # この PC への RDP 接続を登録（deck.sh 用）
                                                       # 値は環境変数 RDP_NAME/RDP_HOST/RDP_PORT/
                                                       # RDP_USERNAME/RDP_PASSWORD で渡す
"""
from __future__ import annotations

import getpass
import sys

from sqlalchemy import select

from app.bootstrap import create_admin, init_db, seed_roles
from app.database import SessionLocal
from app.models import User
from app.security.passwords import hash_password


def _read_password() -> str:
    pw = getpass.getpass("パスワード: ")
    if len(pw) < 8:
        print("パスワードは 8 文字以上にしてください", file=sys.stderr)
        sys.exit(1)
    confirm = getpass.getpass("パスワード（確認）: ")
    if pw != confirm:
        print("パスワードが一致しません", file=sys.stderr)
        sys.exit(1)
    return pw


def _register_local_desktop(db) -> None:
    """この PC への RDP 接続を登録/更新する。値は環境変数で受け取る（argv に秘密を載せない）。"""
    import json
    import os

    from sqlalchemy import select

    from app.models import RemoteConnection
    from app.remote_desktop import service

    name = os.environ.get("RDP_NAME", "この PC（ヘッドレス）")
    host = os.environ.get("RDP_HOST", "127.0.0.1")
    port = int(os.environ.get("RDP_PORT", "3389"))
    username = os.environ.get("RDP_USERNAME", "")
    password = os.environ.get("RDP_PASSWORD", "")

    conn = db.execute(select(RemoteConnection).where(RemoteConnection.name == name)).scalar_one_or_none()
    if conn is None:
        conn = RemoteConnection(name=name, protocol="rdp", host=host, port=port, username=username, params_json=json.dumps({}))
        db.add(conn)
    else:
        conn.host, conn.port, conn.username = host, port, username
    service.set_secret_params(conn, {"password": password})
    db.commit()
    print(f"リモート接続「{name}」を登録しました（{host}:{port}）")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    command = sys.argv[1]
    # username を取らないコマンド
    if command not in ("register-local-desktop",):
        if len(sys.argv) < 3:
            print(__doc__)
            sys.exit(1)
        username = sys.argv[2]
    init_db()
    db = SessionLocal()
    try:
        seed_roles(db)
        if command == "register-local-desktop":
            _register_local_desktop(db)
        elif command == "create-admin":
            password = _read_password()
            user = create_admin(db, username, password)
            print(f"管理者 {user.username} を作成しました")
        elif command == "reset-password":
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                print(f"ユーザー {username} が見つかりません", file=sys.stderr)
                sys.exit(1)
            user.password_hash = hash_password(_read_password())
            db.commit()
            print(f"{username} のパスワードを更新しました")
        elif command == "reset-totp":
            if username == "--all":
                users = db.execute(select(User)).scalars().all()
            else:
                user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
                if user is None:
                    print(f"ユーザー {username} が見つかりません", file=sys.stderr)
                    sys.exit(1)
                users = [user]
            count = 0
            for u in users:
                if u.totp_enabled or u.totp_secret_encrypted:
                    u.totp_enabled = False
                    u.totp_secret_encrypted = None
                    u.recovery_codes_encrypted = None
                    count += 1
            db.commit()
            print(f"{count} 人のユーザーの二要素認証を解除しました")
        else:
            print(f"不明なコマンド: {command}", file=sys.stderr)
            sys.exit(1)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
