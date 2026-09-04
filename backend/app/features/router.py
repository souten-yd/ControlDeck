"""アドオン（オプトインfeature）管理 API。

導入はnpmのユーザー空間インストールでsudo/パスワード不要。有効化/無効化は
ルート登録が起動時ゲートのため、適用後にプラットフォーム再読み込みが必要
（requires_reload で通知し、UI側が既存の /system/platform/reload を実行する）。
"""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.audit import service as audit
from app.database import get_db
from app.features import gpu_runtime, registry
from app.features.registry import list_features
from app.jobs import service as jobs
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/features", tags=["features"])


class FeatureJobRequest(BaseModel):
    """Feature selection is path/catalog bound; callers cannot supply a source.

    GPUランタイムだけは「どの構成を入れるか」の選択肢があるため、カタログ側で
    定義済みの識別子（track / backend）に限って受け付ける。任意のURLやパスは
    受け取らない。
    """

    model_config = ConfigDict(extra="forbid")

    track: Literal["rocm10", "rocm7"] | None = None
    backends: list[Literal["rocm", "vulkan"]] | None = None


@router.get("")
def features(user: User = Depends(require_permission("settings.manage"))):
    return list_features()


@router.post("/{feature_id}/install-jobs", status_code=201)
async def install_job(
    feature_id: str, request: Request, body: FeatureJobRequest,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """信頼済みproviderでfeatureをサーバー側ジョブとして導入する。sudo不要。"""
    if feature_id not in registry.KNOWN_FEATURES:
        raise HTTPException(status_code=404, detail="未知のアドオンです")

    kind = registry.FEATURES[feature_id]["kind"]
    if kind == "gpu-runtime" and body.backends is not None and not body.backends:
        raise HTTPException(status_code=422, detail="導入するバックエンドを1つ以上指定してください")
    options = {k: v for k, v in {"track": body.track, "backends": body.backends}.items() if v is not None}

    async def run(job: jobs.Job) -> dict:
        if kind == "gpu-runtime":
            job.set_progress("リリース資材を取得中", 0, 1)
            try:
                state = await gpu_runtime.install(feature_id, job, options=options)
            except gpu_runtime.GpuRuntimeError as exc:
                raise RuntimeError(str(exc)) from exc
            job.set_progress("完了", 1, 1)
            return {**registry.status(feature_id), **state}
        message = "検証済みrelease bundleを取得・検証中" if kind == "release-bundle" else "パッケージを導入中"
        job.set_progress(message, 0, 1)
        state = await asyncio.to_thread(registry.install, feature_id)
        job.set_progress("完了", 1, 1)
        return state

    job = jobs.create("feature.install", f"アドオン導入: {feature_id}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "feature.install", user=user, resource_type="feature",
                 resource_id=feature_id, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/{feature_id}/update-jobs", status_code=201)
async def update_job(
    feature_id: str, request: Request, body: FeatureJobRequest,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """管理導入をtrusted providerの最新版へ更新する。有効/無効の状態は変えない。"""
    if feature_id not in registry.KNOWN_FEATURES:
        raise HTTPException(status_code=404, detail="未知のアドオンです")
    current = registry.status(feature_id)
    if not current["managed"]:
        raise HTTPException(status_code=422, detail="Control Deckが導入したアドオンのみ更新できます")
    kind = registry.FEATURES[feature_id]["kind"]

    async def run(job: jobs.Job) -> dict:
        if kind == "gpu-runtime":
            job.set_progress("最新リリースを確認中", 0, 1)
            try:
                state = await gpu_runtime.update(feature_id, job)
            except gpu_runtime.GpuRuntimeError as exc:
                raise RuntimeError(str(exc)) from exc
            job.set_progress("完了", 1, 1)
            return {**registry.status(feature_id), **state}
        message = "新しいrelease bundleをside-by-side検証中" if kind == "release-bundle" else "最新版を取得中"
        job.set_progress(message, 0, 1)
        state = await asyncio.to_thread(registry.update, feature_id)
        job.set_progress("完了", 1, 1)
        return state

    job = jobs.create("feature.update", f"アドオン更新: {feature_id}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "feature.update", user=user, resource_type="feature",
                 resource_id=feature_id, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.get("/{feature_id}/release-status")
async def release_status(feature_id: str, user: User = Depends(require_permission("settings.manage"))):
    """最新リリースと導入済みの差分。GitHub参照を含むため一覧とは別経路にする。"""
    if feature_id not in registry.KNOWN_FEATURES:
        raise HTTPException(status_code=404, detail="未知のアドオンです")
    if registry.FEATURES[feature_id]["kind"] != "gpu-runtime":
        raise HTTPException(status_code=422, detail="このアドオンはリリース確認に対応していません")
    return await gpu_runtime.release_status(feature_id)


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
    # Add-on v2 registry is revision-driven; legacy route-gated features need reload.
    # GPUランタイムはルートを登録しないため再読み込みは要らない。
    needs_reload = registry.FEATURES[feature_id]["kind"] not in ("release-bundle", "gpu-runtime")
    return {**state, "requires_reload": needs_reload and action in ("enable", "disable", "uninstall")}
