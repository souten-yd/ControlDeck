"""再開できるモデル取得 Job の runner と、Job 基盤への登録。

再起動で中断された Job を続きから作り直す仕組みは Job 基盤（app/jobs/service.py）に
1 つだけ置く。このモジュールは、モデル取得の Job をそこへ載せる（登録は読み込み時）。

保存する引数（resume）は JSON にできる平らな値だけにする。HF のトークンのような
秘密値は入れない（トークンは取得のたびに設定から読む）。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.jobs import service as jobs

Runner = Callable[[jobs.Job], Awaitable[Any]]


def huggingface_download_runner(params: dict[str, Any]) -> Runner:
    """HuggingFace から GGUF を取る Job を、保存した引数から作り直す。"""
    from app.models_mgmt import hf

    repo = str(params.get("repo") or "")
    files = [str(item) for item in params.get("files") or []]
    if not repo or not files:
        raise ValueError("HuggingFace取得の repo または files がありません")
    library_id = str(params.get("library_id") or "")
    revision = str(params.get("revision") or "main")
    expected_bytes = int(params.get("expected_bytes") or 0)
    register = params.get("register")
    register = dict(register) if isinstance(register, dict) else None

    async def run(job: jobs.Job) -> dict:
        return await hf.download(
            job, repo, files, library_id=library_id, revision=revision,
            expected_bytes=expected_bytes, register=register,
        )

    return run


def huggingface_download_title(params: dict[str, Any]) -> str:
    return f"HuggingFace取得: {params.get('repo', '')}"


def strata_start_runner(params: dict[str, Any]) -> Runner:
    """Strata の初回起動（重みの取得を含む）Job を、保存した引数から作り直す。"""
    alias = str(params.get("alias") or "")
    if not alias:
        raise ValueError("Strata起動の alias がありません")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Strataモデルを取得・起動しています。容量と環境により時間がかかります", 0, 1)
        from app.models_mgmt import provider_adapters

        await provider_adapters.load_model("strata", alias)
        job.set_progress("起動完了", 1, 1)
        return {"alias": alias, "loaded": True}

    return run


def strata_start_title(params: dict[str, Any]) -> str:
    return f"Strata初回起動: {params.get('alias', '')}"


jobs.register_resumable(
    "model.hf_download",
    huggingface_download_runner, title=huggingface_download_title,
)
jobs.register_resumable("model.strata.start", strata_start_runner, title=strata_start_title)
