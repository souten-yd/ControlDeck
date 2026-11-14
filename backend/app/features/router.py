"""アドオン（オプトインfeature）管理 API。

導入はnpmのユーザー空間インストールでsudo/パスワード不要。有効化/無効化は
ルート登録が起動時ゲートのため、適用後にプラットフォーム再読み込みが必要
（requires_reload で通知し、UI側が既存の /system/platform/reload を実行する）。
"""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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
    定義済みの識別子（backend）に限って受け付ける。任意のURLやパスは
    受け取らない。
    """

    model_config = ConfigDict(extra="forbid")

    backends: list[Literal["rocm", "vulkan"]] | None = None


class MayaSetupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gpu_index: int = Field(default=0, ge=0, le=15)


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
    options = {"backends": body.backends} if body.backends is not None else {}

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


# Project Maya-S is opt-in. Installing engine code NEVER downloads its 96.5 GB model.
@router.get("/maya-s/model")
def maya_s_model_status(user: User = Depends(require_permission("settings.manage"))):
    """Models画面向け: 導入・準備・稼働の状態と個別設定。取得や起動はしない。"""
    from app.models_mgmt import maya_s

    return maya_s.runtime_status()


@router.post("/maya-s/model/prepare-jobs", status_code=201)
async def maya_s_prepare_model(
    request: Request, body: MayaSetupBody,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    from app.models_mgmt import maya_s

    if not maya_s.installed():
        raise HTTPException(status_code=422, detail="先にMaya-Sをオプション機能から導入してください")
    if maya_s.runtime_status()["configured"]:
        raise HTTPException(status_code=422, detail="Maya-Sモデルはセットアップ済みです")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Maya-S約96.5GBの明示取得とROCm HIP準備中", 0, 1)
        state = await maya_s.prepare_model_stream(job, gpu_index=body.gpu_index)
        job.set_progress("モデルの準備完了（まだ起動していません）", 1, 1)
        return state

    job = jobs.create("model.maya-s.prepare", "Maya-Sモデル準備", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.prepare", user=user, resource_type="feature",
                 resource_id="maya-s", request=request, metadata={"job_id": job.id, "gpu_index": body.gpu_index})
    return {"job_id": job.id}


@router.post("/maya-s/model/start-jobs", status_code=201)
async def maya_s_start_model(
    request: Request, user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    from app.models_mgmt import maya_s
    from app.models_mgmt import llama, strata_gateway, ollama

    if not maya_s.runtime_status()["configured"]:
        raise HTTPException(status_code=422, detail="先にMaya-Sのモデル準備を完了してください")
    # Never auto-evict another engine or secretly switch the shared runtime.
    others = await asyncio.gather(
        asyncio.to_thread(llama.list_instances),
        asyncio.to_thread(strata_gateway.list_instances),
        ollama.running_models(),
    )
    if any(item.get("loaded") for item in others[0]) or any(item.get("loaded") for item in others[1]) or others[2]:
        raise HTTPException(status_code=409, detail="別のローカルLLMが稼働中です。GPU競合を解消してから明示起動してください")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Maya-Sを独立systemdユーザーサービスで起動中", 0, 1)
        ok, message = await asyncio.to_thread(maya_s.start_instance)
        if not ok:
            raise RuntimeError(message)
        # Avoid reporting success on a service launch that immediately fails.
        if not await maya_s.wait_ready(timeout_seconds=900):
            raise RuntimeError("Maya-SのAPIを確認できません。systemd --user のjournalを確認してください")
        job.set_progress("Maya-S APIの起動を確認", 1, 1)
        return maya_s.runtime_status()

    job = jobs.create("model.maya-s.start", "Maya-S起動", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.start", user=user, resource_type="feature",
                 resource_id="maya-s", request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/maya-s/model/stop")
async def maya_s_stop_model(
    request: Request, user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    from app.models_mgmt import maya_s
    ok, message = await asyncio.to_thread(maya_s.stop_instance)
    if not ok:
        raise HTTPException(status_code=422, detail=message)
    audit.record(db, "maya-s.stop", user=user, resource_type="feature",
                 resource_id="maya-s", request=request)
    return maya_s.runtime_status()


class MayaSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_path: str = Field(default="", max_length=4096)
    # GLM-5.3-Flash supports up to 1M tokens; Maya streams KV beyond VRAM.
    context: int = Field(default=8192, ge=1024, le=1048576)
    gpu_index: int = Field(default=0, ge=0, le=15)
    parallel: int = Field(default=1, ge=1, le=8)
    ram_headroom_gb: int = Field(default=16, ge=1, le=256)
    drafting: Literal["on", "off"] = "on"
    # Lifecycle, as llama.cpp's per-model settings. Changeable while running.
    on_demand_start: bool = True
    idle_exclude: bool = False
    auto_start: bool = False
    # Maya's thinking cap (output length follows the context for every model).
    thinking_budget: int = Field(default=32768, ge=0, le=131072)
    reasoning_effort: Literal["low", "medium", "high", "max"] = "high"


@router.put("/maya-s/settings")
def maya_settings(request: Request, body: MayaSettingsBody,
                  user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import maya_s
    try:
        state = maya_s.save_settings(body.model_dump())
    except (maya_s.MayaError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "maya-s.settings", user=user, resource_type="feature",
                 resource_id="maya-s", request=request)
    return state


@router.post("/maya-s/model/build-jobs", status_code=201)
async def maya_build(request: Request,
               user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import maya_s
    if not maya_s.installed():
        raise HTTPException(status_code=422, detail="先にMaya-Sを導入してください")
    async def run(job: jobs.Job) -> dict:
        job.set_progress("HIPビルド中（モデル取得・読み込みなし）", 0, 1)
        state = await asyncio.to_thread(maya_s.build_engine)
        job.set_progress("HIPビルド完了", 1, 1)
        return state
    job = jobs.create("maya-s.build", "Maya-S HIPビルド", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.build", user=user, resource_type="feature",
                 resource_id="maya-s", request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/maya-s/model/existing-jobs", status_code=201)
async def maya_existing(request: Request,
                        user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import maya_s
    async def run(job: jobs.Job) -> dict:
        job.set_progress("既存GGUFを準備中（モデル取得・起動なし）", 0, 1)
        state = await asyncio.to_thread(maya_s.prepare_existing)
        job.set_progress("既存モデル準備完了（停止中）", 1, 1)
        return state
    job = jobs.create("model.maya-s.existing", "Maya-S既存モデル準備", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.existing", user=user, resource_type="feature",
                 resource_id="maya-s", request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}
