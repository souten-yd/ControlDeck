"""認証ポリシーの共通判定。"""
from __future__ import annotations

from app.config import get_config
from app.models import User


def totp_required_for(user: User) -> bool:
    security = get_config().security
    requirement = security.totp_requirement
    if requirement == "all":
        return True
    if requirement == "administrators":
        return user.role.name == "administrator"
    # 旧設定との後方互換。従来の「推奨」表示を要求仕様どおり必須化する。
    return security.require_totp_for_admin and user.role.name == "administrator"
