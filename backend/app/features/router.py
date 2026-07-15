from fastapi import APIRouter, Depends

from app.features.registry import list_features
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/features", tags=["features"])


@router.get("")
def features(user: User = Depends(require_permission("settings.manage"))):
    return list_features()
