"""Embed/Reranker の推奨モデルプリセット（ワンタップ導入）。

HuggingFace から GGUF をダウンロードし、llama.cpp の role instance として
自動登録する。導入後は RAG API 呼び出し時にオンデマンド起動される。
"""
from __future__ import annotations

from pathlib import Path

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
        # 注意: コミュニティ変換の多くは cls.output.weight（rankヘッド）欠落で
        # near-zeroスコアになる。公式convert_hf_to_gguf.pyで変換された本repoを使う
        "repo": "Voodisss/Qwen3-Reranker-4B-GGUF-llama_cpp",
        "file": "Qwen3-Reranker-4B-Q4_K_M.gguf",
        "alias": "rerank-qwen3-4b",
        "port": 8095,
        "instance": {"ctx_size": 8192, "n_parallel": 1, "flash_attn": False,
                     "n_gpu_layers": 999, "spec_type": "none"},
    },
}


def _models_dir():
    """導入先。既定ライブラリ（F）へ置き、未設定環境では従来の data_dir 配下になる。"""
    from app.models_mgmt import libraries

    try:
        return libraries.default_models_dir()
    except libraries.LibraryError:
        # ライブラリのドライブが未接続でもプリセット導入を止めない。
        root = data_dir() / "models" / "gguf"
        root.mkdir(parents=True, exist_ok=True)
        return root


def _existing_file(preset: dict) -> Path | None:
    """導入済みGGUFを探す。

    保存先は既定ライブラリだが、既存環境のファイルは別のライブラリ（旧 data_dir 配下）に
    あることがある。レイアウトも hf.download の repo 別サブディレクトリと、以前の
    ライブラリ直下の平置きが混在する。再ダウンロードにならないよう全部見る。
    """
    from app.models_mgmt import libraries

    roots: list[Path] = []
    for library in libraries.list_libraries():
        if library.get("mounted") and library.get("path"):
            roots.append(Path(str(library["path"])))
    # 既定ライブラリが未接続でも従来の場所は見る
    roots.append(data_dir() / "models" / "gguf")
    sub = str(preset["repo"]).replace("/", "--")
    for root in roots:
        for candidate in (root / sub / str(preset["file"]), root / str(preset["file"])):
            if candidate.is_file():
                return candidate
    return None


def preset_status() -> list[dict]:
    """各プリセットの導入・稼働状態（UI 表示用）。"""
    instances = {str(item["alias"]): item for item in llama.list_instances()}
    result = []
    for preset_id, preset in ROLE_PRESETS.items():
        existing = _existing_file(preset)
        instance = instances.get(str(preset["alias"]))
        result.append({
            "id": preset_id,
            "label": preset["label"],
            "description": preset["description"],
            "role": preset["role"],
            "alias": preset["alias"],
            "file_exists": existing is not None,
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
    destination = _existing_file(preset)
    if destination is None:
        # 取得処理は hf.py に一本化する（レジューム・空き容量検査を共通で受けられる）。
        from app.models_mgmt import hf

        result = await hf.download(job, str(preset["repo"]), [str(preset["file"])])
        destination = Path(result["files"][0])
    job.set_progress("instance登録中", 0, 1)
    llama.save_instance(str(preset["alias"]), {
        "alias": preset["alias"], "model_path": str(destination),
        "role": preset["role"], "port": preset["port"], **preset["instance"],
    })
    job.set_progress("完了", 1, 1)
    return {"preset": preset_id, "alias": preset["alias"], "path": str(destination)}
