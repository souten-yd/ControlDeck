"""Embed/Reranker の推奨モデルプリセット（ワンタップ導入）。

HuggingFace から GGUF をダウンロードし、llama.cpp の role instance として
自動登録する。導入後は RAG API 呼び出し時にオンデマンド起動される。
"""
from __future__ import annotations

import httpx

from app.config import data_dir
from app.jobs.service import Job
from app.models_mgmt import llama

# 導入先: data_dir/models/gguf（files.allowed_roots のホーム配下）
ROLE_PRESETS: dict[str, dict] = {
    "bge-m3": {
        "label": "BGE-M3 埋め込み（FP16）",
        "description": "多言語対応の埋め込みモデル。RAG のベクトル検索に使用（約1.2GB）",
        "role": "embedding",
        "repo": "gpustack/bge-m3-GGUF",
        "file": "bge-m3-FP16.gguf",
        "alias": "embed-bge-m3",
        "port": 8094,
        "instance": {"ctx_size": 8192, "n_parallel": 4, "flash_attn": False,
                     "n_gpu_layers": 999, "spec_type": "none"},
    },
    "qwen3-reranker-4b": {
        "label": "Qwen3-Reranker-4B（Q4_K_M）",
        "description": "検索候補を質問との関連度で並べ直す再ランクモデル（約2.5GB）",
        "role": "reranker",
        "repo": "dengcao/Qwen3-Reranker-4B-GGUF",
        "file": "Qwen3-Reranker-4B-q4_k_m.gguf",
        "alias": "rerank-qwen3-4b",
        "port": 8095,
        "instance": {"ctx_size": 8192, "n_parallel": 1, "flash_attn": False,
                     "n_gpu_layers": 999, "spec_type": "none"},
    },
}


def _models_dir():
    root = data_dir() / "models" / "gguf"
    root.mkdir(parents=True, exist_ok=True)
    return root


def preset_status() -> list[dict]:
    """各プリセットの導入・稼働状態（UI 表示用）。"""
    instances = {str(item["alias"]): item for item in llama.list_instances()}
    result = []
    for preset_id, preset in ROLE_PRESETS.items():
        path = _models_dir() / str(preset["file"])
        instance = instances.get(str(preset["alias"]))
        result.append({
            "id": preset_id,
            "label": preset["label"],
            "description": preset["description"],
            "role": preset["role"],
            "alias": preset["alias"],
            "file_exists": path.is_file(),
            "installed": instance is not None,
            "loaded": bool(instance and instance.get("loaded")),
            "idle_exclude": bool(instance and instance.get("idle_exclude")),
            "runtime_status": (instance or {}).get("runtime_status", "UNKNOWN"),
        })
    return result


async def install(job: Job, preset_id: str) -> dict:
    """GGUF をダウンロードして role instance として登録する（既存はスキップ/再利用）。"""
    preset = ROLE_PRESETS.get(preset_id)
    if preset is None:
        raise RuntimeError("未知のプリセットです")
    if not llama.is_installed():
        raise RuntimeError("llama.cpp が未導入です。Model画面の共通設定から導入してください")
    destination = _models_dir() / str(preset["file"])
    if not destination.is_file():
        url = f"https://huggingface.co/{preset['repo']}/resolve/main/{preset['file']}"
        job.set_progress("ダウンロード中", 0, 1)
        temp = destination.with_suffix(".part")
        try:
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise RuntimeError(f"ダウンロード失敗 ({response.status_code}): {url}")
                    total = int(response.headers.get("content-length") or 0)
                    received = 0
                    with temp.open("wb") as target:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            target.write(chunk)
                            received += len(chunk)
                            job.set_progress("ダウンロード中", received, total or None)
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
    job.set_progress("instance登録中", 0, 1)
    llama.save_instance(str(preset["alias"]), {
        "alias": preset["alias"], "model_path": str(destination),
        "role": preset["role"], "port": preset["port"], **preset["instance"],
    })
    job.set_progress("完了", 1, 1)
    return {"preset": preset_id, "alias": preset["alias"], "path": str(destination)}
