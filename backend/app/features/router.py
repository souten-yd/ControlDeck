"""アドオン（オプトインfeature）管理 API。

導入はnpmのユーザー空間インストールでsudo/パスワード不要。有効化/無効化は
ルート登録が起動時ゲートのため、適用後にプラットフォーム再読み込みが必要
（requires_reload で通知し、UI側が既存の /system/platform/reload を実行する）。
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from app.audit import service as audit
from app.database import get_db
from app.features import registry
from app.features.registry import list_features
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/features", tags=["features"])


@router.get("")
def features(user: User = Depends(require_permission("settings.manage"))):
    return list_features()


@router.post("/{feature_id}/install-jobs", status_code=201)
async def install_job(
    feature_id: str, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """ランタイム一式（npmパッケージ）をサーバー側ジョブで導入する。sudo不要。"""
    if feature_id not in registry.KNOWN_FEATURES:
        raise HTTPException(status_code=404, detail="未知のアドオンです")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("npmで導入中（初回は1〜2分かかります）", 0, 1)
        state = await asyncio.to_thread(registry.install, feature_id)
        job.set_progress("完了", 1, 1)
        return state

    job = jobs.create("feature.install", f"アドオン導入: {feature_id}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "feature.install", user=user, resource_type="feature",
                 resource_id=feature_id, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/{feature_id}/{action}")
def apply_action(
    feature_id: str, action: str, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """有効化/無効化/アンインストールをワンタップで適用する。"""
    if action not in ("enable", "disable", "uninstall"):
        raise HTTPException(status_code=404, detail="未対応の操作です")
    try:
        state = registry.apply(action, feature_id)  # type: ignore[arg-type]
    except registry.FeatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, f"feature.{action}", user=user, resource_type="feature",
                 resource_id=feature_id, request=request)
    # ルート/ナビ登録は起動時ゲートのため、反映には再読み込みが必要
    return {**state, "requires_reload": action in ("enable", "disable", "uninstall")}
