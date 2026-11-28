"""アドオン（オプトインfeature）管理 API。

導入はnpmのユーザー空間インストールでsudo/パスワード不要。有効化/無効化は
ルート登録が起動時ゲートのため、適用後にプラットフォーム再読み込みが必要
（requires_reload で通知し、UI側が既存の /system/platform/reload を実行する）。
"""
import asyncio
import os
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
    # Android SDK だけ: 画面でライセンスへの同意にチェックを入れたときだけ true（同意なしでは取得しない）
    accept_licenses: bool = False
    # Android SDK だけ: 目録（android_sdk.CATALOG）の範囲の中から選ぶ。空なら既定
    android_platform: str | None = Field(default=None, max_length=8)
    android_build_tools: str | None = Field(default=None, max_length=16)


class MayaSetupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gpu_index: int = Field(default=0, ge=0, le=15)
    # ダウンロードの保存先（既存のフォルダ）。空なら既定の置き場。
    download_dir: str = Field(default="", max_length=4096)


@router.get("")
def features(user: User = Depends(require_permission("settings.manage"))):
    return list_features()


class AutoUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    time: str | None = Field(default=None, max_length=5)


def _auto_update_view() -> dict:
    from app.features import auto_update

    return {**auto_update.get_settings(), "last": auto_update.get_state(), "running": auto_update.running()}


# 固定のパスは /{feature_id}/{action} より先に置く（後ろだと feature_id="auto-update" として扱われる）。
@router.get("/auto-update")
def auto_update_settings(user: User = Depends(require_permission("settings.manage"))):
    """オプション機能の自動更新（1 日 1 回）の設定と、前回の結果。"""
    return _auto_update_view()


@router.put("/auto-update")
def auto_update_save(body: AutoUpdateBody, request: Request,
                     user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.features import auto_update

    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=422, detail="enabled か time を指定してください")
    try:
        auto_update.save_settings(patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "feature.auto_update_settings", user=user, resource_type="feature",
                 request=request, metadata=auto_update.get_settings())
    return _auto_update_view()


@router.post("/auto-update/run-jobs", status_code=201)
async def auto_update_run(request: Request, user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    """時刻を待たずに、導入済みのオプション機能をまとめて更新する。

    async で定義する: jobs.create はイベントループの上で Job を走らせる。同期関数（スレッドで動く）から呼ぶと
    「no running event loop」で 500 になる（2026-11-20 本番で発生）。"""
    from app.features import auto_update

    if auto_update.running():
        raise HTTPException(status_code=409, detail="一括更新はすでに実行中です")
    job = auto_update.start_job(user.id, trigger="manual")
    audit.record(db, "feature.auto_update_run", user=user, resource_type="feature",
                 request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


class BulkInstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=40)
    # 画面でチェックを付けたもの。空なら分類の中の未導入をすべて
    feature_ids: list[str] = Field(default_factory=list, max_length=40)


BULK_KINDS = ("npm", "pip", "python-lib", "opencode-plugin", "opencode-mcp", "managed-script", "playwright-browsers")


@router.post("/install-all-jobs", status_code=201)
async def install_all_job(body: BulkInstallBody, request: Request,
                          user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    """分類の中の未導入の機能を、1 つの Job で順に導入する（npm・pip の機能だけ。GPU ランタイムなどは個別に）。
    依存（requires）のあるものは依存の後に入れる。1 件の失敗で残りを止めず、結果に残す。"""
    if body.category not in registry.CATEGORIES:
        raise HTTPException(status_code=404, detail="知らない分類です")
    unknown = [fid for fid in body.feature_ids if fid not in registry.KNOWN_FEATURES
               or registry.category(fid) != body.category]
    if unknown:
        raise HTTPException(status_code=422, detail=f"この分類に無い機能です: {', '.join(unknown)}")
    wanted = set(body.feature_ids)
    targets = [fid for fid in sorted(registry.KNOWN_FEATURES)
               if registry.category(fid) == body.category and registry.FEATURES[fid]["kind"] in BULK_KINDS
               and (not wanted or fid in wanted) and not registry.status(fid)["installed"]]
    targets.sort(key=lambda fid: 1 if registry.FEATURES[fid].get("requires") in targets else 0)
    if not targets:
        raise HTTPException(status_code=422, detail="まとめて導入できる未導入の機能はありません")

    async def run(job: jobs.Job) -> dict:
        from app.features import package_updates

        done, failed = [], []
        for index, fid in enumerate(targets):
            job.set_progress(f"{registry.FEATURES[fid]['name']} を導入中（{index + 1}/{len(targets)}）", index, len(targets))
            try:
                await asyncio.to_thread(registry.install, fid)
                done.append(fid)
            except registry.FeatureError as exc:
                failed.append({"id": fid, "error": str(exc)})
            package_updates.forget(fid)
        job.set_progress("完了", len(targets), len(targets))
        if failed and not done:
            raise RuntimeError("導入できませんでした: " + " / ".join(f"{f['id']}: {f['error']}" for f in failed))
        return {"installed": done, "failed": failed}

    job = jobs.create("feature.install_all", f"まとめて導入: {registry.CATEGORIES[body.category]}", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "feature.install_all", user=user, resource_type="feature", resource_id=body.category,
                 request=request, metadata={"job_id": job.id, "features": targets})
    return {"job_id": job.id, "features": targets}


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
    if kind == "android-sdk" and not body.accept_licenses:
        raise HTTPException(status_code=422, detail="Android SDK のライセンスへの同意が必要です")
    if kind == "android-sdk":
        from app.features import android_sdk

        try:
            android_sdk.check_choice(body.android_platform or android_sdk.CATALOG["defaults"]["platform"],
                                     body.android_build_tools or android_sdk.CATALOG["defaults"]["build_tools"])
        except android_sdk.AndroidSdkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def run(job: jobs.Job) -> dict:
        if kind == "speech-engine":
            from app.speech import supertonic

            try:
                await supertonic.install(job)
            except supertonic.SupertonicError as exc:
                raise RuntimeError(str(exc)) from exc
            return registry.status(feature_id)
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
        if kind == "android-sdk":
            job.set_progress("Android SDK を取得して導入中（Google の公式・数分かかります）", 0, 1)
            try:
                state = await asyncio.to_thread(
                    lambda: registry.install(feature_id, accept_licenses=body.accept_licenses,
                                             platform=body.android_platform, build_tools=body.android_build_tools))
            except registry.FeatureError as exc:
                raise RuntimeError(str(exc)) from exc
            job.set_progress("完了", 1, 1)
            return state
        state = await asyncio.to_thread(registry.install, feature_id)
        job.set_progress("完了", 1, 1)
        return state

    job = jobs.create("feature.install", f"アドオン導入: {feature_id}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "feature.install", user=user, resource_type="feature",
                 resource_id=feature_id, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/{feature_id}/models/{tier}/install-jobs", status_code=201)
async def install_model_tier_job(
    feature_id: str, tier: str, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """機能に付くモデルの精度の段（ニュースの画像生成: small・medium・large）を、サーバー側ジョブで取得する。"""
    if feature_id not in registry.KNOWN_FEATURES or not registry.FEATURES[feature_id].get("models"):
        raise HTTPException(status_code=404, detail="この機能には段ごとのモデルがありません")
    if tier not in ("small", "medium", "large"):
        raise HTTPException(status_code=404, detail="知らない段です")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("モデルを取得中", 0, 1)
        try:
            state = await asyncio.to_thread(registry.install_model_tier, feature_id, tier)
        except registry.FeatureError as exc:
            raise RuntimeError(str(exc)) from exc
        job.set_progress("完了", 1, 1)
        return state

    job = jobs.create("feature.install", f"モデル取得: {feature_id}/{tier}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "feature.model.install", user=user, resource_type="feature", resource_id=f"{feature_id}/{tier}",
                 request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.delete("/{feature_id}/models/{tier}")
def remove_model_tier(
    feature_id: str, tier: str, request: Request,
    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db),
):
    """精度の段のモデルを消す（小は消せない）。"""
    try:
        state = registry.remove_model_tier(feature_id, tier)
    except registry.FeatureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "feature.model.remove", user=user, resource_type="feature", resource_id=f"{feature_id}/{tier}", request=request)
    return state


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
        from app.features import package_updates

        package_updates.forget(feature_id)
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
    from app.features import package_updates

    kind = registry.FEATURES[feature_id]["kind"]
    if kind in package_updates.PACKAGE_KINDS:
        if not registry.status(feature_id)["managed"]:
            raise HTTPException(status_code=422, detail="Control Deck が導入した機能だけを確認できます")
        return await package_updates.release_status(feature_id)
    if kind != "gpu-runtime":
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
    needs_reload = registry.FEATURES[feature_id]["kind"] not in ("release-bundle", "gpu-runtime", "speech-engine")
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
    try:
        maya_s._download_dir(body.download_dir)
    except maya_s.MayaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Maya-S約96.5GBの明示取得とROCm HIP準備中", 0, 1)
        state = await maya_s.prepare_model_stream(job, gpu_index=body.gpu_index, download_dir=body.download_dir)
        job.set_progress("モデルの準備完了（まだ起動していません）", 1, 1)
        return state

    job = jobs.create("model.maya-s.prepare", "Maya-Sモデル準備", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.prepare", user=user, resource_type="feature",
                 resource_id="maya-s", request=request, metadata={"job_id": job.id, "gpu_index": body.gpu_index,
                                                                 "download_dir": body.download_dir})
    return {"job_id": job.id}


class MayaAliasBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(default="maya-s", pattern=r"^maya-[a-z0-9-]{1,60}$")


_ONDEMAND_HELPER_ROLES = {"embedding", "reranker"}


@router.post("/maya-s/model/start-jobs", status_code=201)
async def maya_s_start_model(
    request: Request, body: MayaAliasBody | None = None,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    from app.models_mgmt import maya_s
    from app.models_mgmt import llama, strata_gateway, ollama

    alias = body.alias if body else maya_s.ALIAS
    if not any(item["alias"] == alias for item in await asyncio.to_thread(maya_s.list_instances)):
        raise HTTPException(status_code=422, detail="先にMayaのモデル準備を完了してください")
    # Never auto-evict another engine or secretly switch the shared runtime.
    from app.models_mgmt import radiance, sly_radiance
    others = await asyncio.gather(
        asyncio.to_thread(llama.list_instances),
        asyncio.to_thread(strata_gateway.list_instances),
        ollama.running_models(),
        asyncio.to_thread(lambda: [item for module in (radiance, sly_radiance) if module.installed()
                                   for item in module.list_instances()]),
    )
    # 資料検索の補助（埋め込み・並べ替え）は必要なときに自動で起きる小さなモデル。LLM の競合とは
    # 数えず、Maya の前に降ろす（以前は Strata を止めた直後に自動で起きたこれが起動を 409 で塞いでいた）。
    helpers = [item for item in others[0] if item.get("loaded") and item.get("role") in _ONDEMAND_HELPER_ROLES]
    llms = [item for item in others[0] if item.get("loaded") and item.get("role") not in _ONDEMAND_HELPER_ROLES]
    busy = [str(item.get("alias") or "") for item in llms]
    busy += [str(item.get("alias") or "") for item in others[1] if item.get("loaded")]
    busy += [str(row.get("name") or row.get("model") or "ollama") if isinstance(row, dict) else str(row) for row in (others[2] or [])]
    busy += [str(item.get("alias") or "") for item in others[3] if item.get("loaded")]
    if busy:
        raise HTTPException(status_code=409, detail=(
            f"別のローカルLLMが稼働中です（{', '.join(filter(None, busy))[:120]}）。止めてから起動してください"))
    for item in helpers:
        await asyncio.to_thread(llama.stop_instance, item.get("alias"))

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Mayaを独立systemdユーザーサービスで起動中", 0, 1)
        ok, message = await asyncio.to_thread(maya_s.start_instance, alias)
        if not ok:
            raise RuntimeError(message)
        # Avoid reporting success on a service launch that immediately fails.
        if not await maya_s.wait_ready(timeout_seconds=900, alias=alias):
            raise RuntimeError("MayaのAPIを確認できません。systemd --user のjournalを確認してください")
        job.set_progress("Maya APIの起動を確認", 1, 1)
        return maya_s.runtime_status()

    job = jobs.create("model.maya-s.start", "Maya起動", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.start", user=user, resource_type="feature",
                 resource_id=alias, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/maya-s/model/stop")
async def maya_s_stop_model(
    request: Request, body: MayaAliasBody | None = None,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    from app.models_mgmt import maya_s
    ok, message = await asyncio.to_thread(maya_s.stop_instance, body.alias if body else None)
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


class MayaInstanceSettingsBody(BaseModel):
    """登録したモデル（DS-XS など）の調整値。DeepSeek の欄は DeepSeek のモデルだけで効く。"""
    model_config = ConfigDict(extra="forbid")
    context: int | None = Field(default=None, ge=8192, le=1048576)
    parallel: int | None = Field(default=None, ge=1, le=4)
    gpu_index: int | None = Field(default=None, ge=0, le=15)
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    thinking_budget: int | None = Field(default=None, ge=0, le=131072)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    on_demand_start: bool | None = None
    idle_exclude: bool | None = None
    auto_start: bool | None = None
    dspark: Literal["on", "off"] | None = None
    cpu_threads: int | None = Field(default=None, ge=0, le=256)
    vram_experts: float | None = Field(default=None, ge=0.0, le=1.0)
    mmap_experts: Literal["auto", "on", "off"] | None = None
    ssd_cache: str | None = Field(default=None, pattern=r"^(off|auto|[1-9][0-9]{0,3})$")
    prefill_chunk: int | None = Field(default=None, ge=0, le=65536)
    # 画像の読み取り（vision tower を入れたモデルだけ on にできる）
    vision: Literal["on", "off"] | None = None


@router.get("/maya-s/models/{alias}/settings")
def maya_instance_settings(alias: str, user: User = Depends(require_permission("settings.manage"))):
    from app.models_mgmt import maya_s
    try:
        return maya_s.instance_settings(alias)
    except maya_s.MayaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/maya-s/models/{alias}/settings")
def maya_instance_settings_save(alias: str, body: MayaInstanceSettingsBody, request: Request,
                                user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import maya_s
    values = body.model_dump(exclude_none=True)
    try:
        result = maya_s.save_instance_settings(alias, values)
    except maya_s.MayaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "maya-s.instance_settings", user=user, resource_type="model", resource_id=alias,
                 request=request, metadata={"changed": sorted(values)})
    return result


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


# More Maya models from files already on disk. Nothing here downloads: a missing file is
# reported with the command that fetches it, for the owner to run.
@router.get("/maya-s/catalog")
def maya_catalog(folder: str = "", user: User = Depends(require_permission("settings.manage"))):
    from app.models_mgmt import maya_s
    try:
        return maya_s.catalog(folder)
    except (maya_s.MayaError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class MayaRegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setup_id: str = Field(pattern=r"^[a-z0-9.-]{3,60}$")
    folder: str = Field(min_length=1, max_length=4096)
    context: int = Field(default=65536, ge=1024, le=1048576)
    gpu_index: int = Field(default=0, ge=0, le=15)
    start: bool = False


@router.post("/maya-s/models/register-jobs", status_code=201)
async def maya_register(request: Request, body: MayaRegisterBody,
                        user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import maya_s
    if not maya_s.installed():
        raise HTTPException(status_code=422, detail="先にMayaを導入してください")
    try:
        item = next((q for q in (await asyncio.to_thread(maya_s.catalog, body.folder))["quants"]
                     if q["setup_id"] == body.setup_id), None)
    except (maya_s.MayaError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None or item["registered"] or not item["ready"]:
        raise HTTPException(status_code=422, detail="この量子化は登録できません（登録済み、またはファイル不足）")

    async def run(job: jobs.Job) -> dict:
        state = await maya_s.register_stream(job, setup_id=body.setup_id, folder=body.folder,
                                             context=body.context, gpu_index=body.gpu_index)
        if body.start:
            job.set_progress("登録しました。起動中", 0, 1)
            ok, message = await asyncio.to_thread(maya_s.start_instance, state["alias"])
            if not ok:
                raise RuntimeError(message)
            if not await maya_s.wait_ready(timeout_seconds=900, alias=state["alias"]):
                raise RuntimeError("MayaのAPIを確認できません。systemd --user のjournalを確認してください")
        job.set_progress("登録しました", 1, 1)
        return state

    job = jobs.create("model.maya-s.register", f"Maya登録 {item['model']} · {item['tier']}", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "maya-s.register", user=user, resource_type="feature", resource_id=item["alias"],
                 request=request, metadata={"job_id": job.id, "setup_id": body.setup_id})
    return {"job_id": job.id, "alias": item["alias"]}


@router.delete("/maya-s/models/{alias}")
def maya_unregister(alias: str, request: Request, delete_files: bool = False,
                    user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import maya_s
    try:
        state = maya_s.unregister(alias, delete_files=delete_files)
    except maya_s.MayaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "maya-s.unregister", user=user, resource_type="feature", resource_id=alias, request=request,
                 metadata={"delete_files": delete_files, "deleted_files": state.get("deleted_files", 0)})
    return state


class MayaDownloadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setup_id: str = Field(pattern=r"^[a-z0-9.-]{3,60}$")
    folder: str = Field(min_length=1, max_length=4096)
    context: int = Field(default=65536, ge=1024, le=1048576)
    gpu_index: int = Field(default=0, ge=0, le=15)
    # 登録済みのモデルを新しい版のファイルで準備し直す（その alias）。空なら新しく登録する。
    replace: str = Field(default="", max_length=64)
    # 目録の「取得して登録」: 取得先のフォルダ（<置き場>/<repo の名前>）がまだ無ければ作る（2026-11-28。
    # 利用者「ダウンロードしようとしたら存在しないと出る」）。作れるのは /data1tb・/data2tb の中の、既にある親の直下だけ。
    create_folder: bool = False


def _make_model_folder(folder: str) -> None:
    """取得先のフォルダを作る。親は /data1tb・/data2tb の中の既にあるフォルダ（realpath・symlink 検証込み）、名前は英数字と ._- だけ。"""
    import re
    from pathlib import Path

    from app.files.service import FileAccessError
    from app.models_mgmt import model_paths

    path = Path(folder)
    if (not path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[\w.\-]{1,120}", path.name)
            or path.name in (".", "..")):
        raise HTTPException(status_code=422, detail="取得先のフォルダ名が不正です")
    try:
        parent = model_paths.resolve_selection(str(path.parent))
    except (FileAccessError, PermissionError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    target = parent / path.name
    if target.is_symlink():
        raise HTTPException(status_code=422, detail="取得先がシンボリックリンクです")
    try:
        target.mkdir(exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"取得先のフォルダを作れませんでした: {exc.strerror}") from exc


@router.post("/maya-s/models/download-jobs", status_code=201)
async def maya_download(request: Request, body: MayaDownloadBody,
                        user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    """利用者が「ダウンロードして登録」（または「更新」）を押したときだけ呼ばれる。"""
    from app.models_mgmt import maya_s
    if not maya_s.installed():
        raise HTTPException(status_code=422, detail="先にMayaを導入してください")
    # 取得先が無ければ作る（画面の指示 create_folder に頼らない。更新前に開いた古い画面は指示を送らず、
    # 「存在しません」で止まった。2026-11-28 利用者「存在しないからダウンロードしてほしいのに」）。
    if body.create_folder or not await asyncio.to_thread(os.path.isdir, body.folder):
        await asyncio.to_thread(_make_model_folder, body.folder)
    try:
        item = next((q for q in (await asyncio.to_thread(maya_s.catalog, body.folder))["quants"]
                     if q["setup_id"] == body.setup_id), None)
    except (maya_s.MayaError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None or (item["registered"] and not body.replace):
        raise HTTPException(status_code=422, detail="この量子化はダウンロードできません（登録済み）")
    if body.replace and item["registered"] != body.replace:
        raise HTTPException(status_code=422, detail="更新するモデルが一致しません")
    if item.get("download_files") and not item.get("can_download"):
        raise HTTPException(status_code=422, detail=f"空き容量が足りません（必要 約{item.get('download_gb')}GB・"
                                                    f"空き {item.get('free_gb')}GB）")

    from app.models_mgmt import resume as model_resume

    # 再起動（本体の自動更新など）の後に続きから取り直せるよう、引数を再開の記録に残す（2026-11-28）
    params = {"setup_id": body.setup_id, "folder": body.folder, "context": body.context,
              "gpu_index": body.gpu_index, "replace": body.replace}
    title = f"Mayaダウンロード {maya_s.display_name(item)}"
    job = jobs.create("model.maya-s.download", title, model_resume.maya_download_runner(params), owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), resume=params)
    audit.record(db, "maya-s.download", user=user, resource_type="feature", resource_id=item["alias"],
                 request=request, metadata={"job_id": job.id, "setup_id": body.setup_id,
                                            "download_gb": item.get("download_gb"), "root": item.get("download_root")})
    return {"job_id": job.id, "alias": item["alias"]}
