"""推奨スキルの導入・更新・無効化・削除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import require_permission
from app.skills import registry

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillActionBody(BaseModel):
    action: str = Field(pattern="^(install|update|enable|disable|remove)$")


@router.get("")
def list_skills(user: User = Depends(require_permission("settings.manage"))):
    return {"items": registry.list_skills()}


@router.post("/{skill_id}")
def apply(
    skill_id: str,
    body: SkillActionBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("settings.manage")),
):
    try:
        if body.action in {"install", "update"}:
            # 更新は入れ直しと同じ。版ごとに別の場所へ置くので、失敗しても
            # 前のものが残る。
            result = registry.install(skill_id)
        elif body.action == "enable":
            result = registry.set_enabled(skill_id, True)
        elif body.action == "disable":
            result = registry.set_enabled(skill_id, False)
        else:
            result = registry.remove(skill_id)
    except registry.SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(db, f"skill.{body.action}", user=user, resource_type="skill",
                 resource_id=skill_id, request=request)
    return result
