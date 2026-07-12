"""管理 CLI。使用例:

  python -m app.cli create-admin <username>            # パスワードは対話入力
  python -m app.cli reset-password <username>
  python -m app.cli reset-totp <username>              # 二要素認証を解除（ロックアウト復旧用）
  python -m app.cli reset-totp --all                   # 全ユーザーの TOTP を解除
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


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    command, username = sys.argv[1], sys.argv[2]
    init_db()
    db = SessionLocal()
    try:
        seed_roles(db)
        if command == "create-admin":
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
