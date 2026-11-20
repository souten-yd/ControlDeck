"""CPU の reranker（ONNX Runtime）。機能追加からタップ 1 つで、取得・変換・実行環境の用意まで行う。

2026-11-20 利用者「reranker はなるべく軽量・高速に。GPU に置かず CPU で」「ある程度精度が悪くても軽量と高速であれば
問題ない」「他の reranker も含めて、タップしたら初期設定とモデルのダウンロードができるように」。

選べるモデル（評価は docs/reranker-evaluation.md。速さは標準の長さ（約 450〜560 トークン）の資料 32 件、8 スレッド）:
- xsmall: hotchpotch/japanese-reranker-xsmall-v2（MIT）。日本語 JQaRA 0.748（今の GPU の 4B は 0.743）。
  0.7〜1.2 秒、ファイル 0.15 GB・RAM 0.7 GB。公式の ONNX をそのまま使う（変換しない）
- ruri: cl-nagoya/ruri-v3-reranker-310m（Apache-2.0）。日本語 0.877（最良）。8〜12 秒、
  int8 0.73 GB・RAM 2.1 GB。導入時に一時の build venv（PyTorch CPU）で ONNX へ書き出して int8 にする
  （MLP の出力側 Wo だけ fp32。全部 int8 だと 0.834 に落ちる）。PyTorch fp32 と順位が同じかを確かめてから使う

両方あるとき: 会話の資料（3 秒まで）は速い xsmall、Knowledge・Workflow（待てる）は精度の ruri を使う。

- 実行環境（onnxruntime・tokenizers・numpy）は全モデルで 1 つ。torch は要らない
- 取得したファイルと変換した ONNX は data/archives/<モデル> に残す（入れ直しで取り直さない・変換し直さない）
- worker はモデルごとに、使うときに起動して常駐し、10 分使われなければ止める（RAM を返す）

点数は 0〜1 だが、会話の流れを指す質問（「さっき調べた番組表」）では低く出る。点数で入れる・入れないを
決める門には使わず、上位の件数で選ぶ（評価の結論）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import httpx

from app.config import data_dir
from app.jobs import service as jobs

logger = logging.getLogger("control_deck.workflows.cpu_reranker")

# 質問＋資料 1 件で読む上限。資料は切らずに読ませる（2026-11-20 利用者「区切りは一般的な長さで、短く切らないで」）。
# 会話の資料の塊は 800 文字（日本語で約 430 トークン）で、一般的な 512 前後。英語の長い塊（約 560 トークン）も収まる
MAX_TOKENS = 1024
TOKENIZER = (6724873, "0a94ac9a0a02c067bdef25b72ae9f4ee33f48f552e55988d444f6d25eeb1d062")
# path: (bytes, sha256)。2026-11-20 取得・照合
MODELS: dict[str, dict] = {
    "xsmall-reranker": {
        "name": "japanese-reranker-xsmall-v2（CPU・速い）",
        "repo": "hotchpotch/japanese-reranker-xsmall-v2",
        "revision": "de99fd2f16c7b5df1df1bcc1d9ad2c16d88ce93a",
        "license": "MIT（hotchpotch）",
        "build": "download",
        "files": {
            "onnx/model.onnx": (147250318, "4d668ba88cd62107b6d8c0365313ac4d76030873a0ed99dcbfd5c1636f26ada8"),
            "tokenizer.json": TOKENIZER,
        },
        "model_file": "onnx/model.onnx",
        "runtime_label": "ONNX Runtime 1.32.0（CPU・fp32）",
        "accuracy": "日本語 0.748（GPU の 4B は 0.743）",
        "speed": "標準の長さ 32 件で 0.7〜1.2 秒",
        "memory": "ファイル 0.15 GB・RAM 0.7 GB",
        # 速いので会話でも候補を絞らない
        "max_tokens": MAX_TOKENS, "fast_max_tokens": MAX_TOKENS, "fast_pool": 4, "prefer": "fast", "device": "cpu",
    },
    "ruri-reranker": {
        "name": "ruri-v3-reranker-310m（CPU・高精度）",
        "repo": "cl-nagoya/ruri-v3-reranker-310m",
        "revision": "bb46934ee9ed09f850b9fcff17501b3ef7ddb2b3",
        "license": "Apache-2.0（名古屋大学 cl-nagoya）",
        "build": "convert-int8",
        "files": {
            "config.json": (1377, "62216cae7ea4b690db21aa8c4086a88f26af6173f2795d4c1a807755f3a899a8"),
            "model.safetensors": (1260829436, "9f747a085a0adba9490205357be306ba3234bc958a30a19baa6b0bb14e9e83fe"),
            "special_tokens_map.json": (968, "30bf8256f9a1eb3287af2a9b7940465e29a38aad4a459a3e773bb3a14bd34c0f"),
            "tokenizer.json": TOKENIZER,
            "tokenizer.model": (1831879, "008293028e1a9d9a1038d9b63d989a2319797dfeaa03f171093a57b33a3a8277"),
            "tokenizer_config.json": (3779, "34004511993472f15c7202074284c57788c161fb6567de164b8e0c0266f155e4"),
        },
        "model_file": "onnx-int8/model_int8.onnx",
        "runtime_label": "ONNX Runtime 1.32.0（CPU・int8）",
        "accuracy": "日本語 0.877（最良）",
        "speed": "標準の長さ 32 件で 8〜12 秒（会話では候補を半分に絞る）",
        "memory": "ファイル 0.73 GB・RAM 2.5 GB",
        # 実測（8 スレッド、標準の長さ約 450 トークン）: 32 件 8.0 秒。会話（3 秒まで）では候補を top_k×2 に絞る
        "max_tokens": MAX_TOKENS, "fast_max_tokens": MAX_TOKENS, "fast_pool": 2, "prefer": "accurate", "device": "cpu",
    },
    # 2026-11-20 利用者「ruri を GPU で動かす選択肢を」「GPU で量子化して容量を下げられないか。精度と速さは維持」。
    # 実測（R9700、ONNX Runtime MIGraphX、fp16 計算、資料は 1024 トークンまで）:
    #   fp32 のファイル（1.26 GB）: 日本語 0.877 / 0.964・英語 0.582 / 0.935、標準の長さ 32 件 0.37 秒、VRAM 1.52 GB
    #   fp16 のファイル（0.63 GB）: 精度・速さとも同じ。採用（ファイルが半分）
    #   int8（CPU 用、0.73 GB）: 32 件 3.15 秒（8.5 倍遅い。int8 の計算が GPU で速くならない）。VRAM 1.35 GB。不採用
    #   一度に並べる件数: 8 件 VRAM 1.52 GB、4 件 1.27 GB（速さ同じ）、2 件 1.15 GB（0.40 秒）→ 4 件
    "ruri-reranker-gpu": {
        "name": "ruri-v3-reranker-310m（GPU・高精度・速い）",
        "repo": "cl-nagoya/ruri-v3-reranker-310m",
        "revision": "bb46934ee9ed09f850b9fcff17501b3ef7ddb2b3",
        "license": "Apache-2.0（名古屋大学 cl-nagoya）",
        "build": "convert-fp16",
        "files": None,   # ruri-reranker と同じ（下で埋める）
        "model_file": "onnx-fp16/model_fp16.onnx",
        "runtime_label": "ONNX Runtime MIGraphX 1.27.1（AMD GPU・fp16）",
        "accuracy": "日本語 0.877（最良）",
        "speed": "標準の長さ 32 件で約 0.4 秒",
        "memory": "ファイル 0.63 GB・VRAM 約 1.3 GB（使っていなければ 10 分で返す）",
        "max_tokens": MAX_TOKENS, "fast_max_tokens": MAX_TOKENS, "fast_pool": 4, "prefer": "accurate", "device": "gpu",
        "batch": 4,
    },
}
MODELS["ruri-reranker-gpu"]["files"] = MODELS["ruri-reranker"]["files"]
# 自動のときの優先順。GPU の ruri は精度も速さも最良なので、入っていればどちらにも使う
_AUTO_ORDER = {"fast": ("ruri-reranker-gpu", "xsmall-reranker", "ruri-reranker"),
               "accurate": ("ruri-reranker-gpu", "ruri-reranker", "xsmall-reranker")}
_COMMON_PACKAGES = ("tokenizers==0.23.3", "numpy==2.5.5", "flatbuffers==25.12.19", "packaging==26.3", "protobuf==7.36.2")
RUNTIME_PACKAGES = ("onnxruntime==1.32.0", *_COMMON_PACKAGES)
# GPU: AMD の MIGraphX 版（PyPI）。ROCm の MIGraphX（/opt/rocm）を使う。実行環境 約 0.2 GB
GPU_RUNTIME_PACKAGES = ("onnxruntime-migraphx==1.27.1", *_COMMON_PACKAGES)
# 変換だけに使う（終わったら消す）。torch は CPU 版を PyTorch の配布元から取る
BUILD_TORCH = "torch==2.15.0"
BUILD_PACKAGES = ("transformers==5.23.0", "onnx==1.23.2", "onnxruntime==1.32.0", "tokenizers==0.23.3",
                  "numpy==2.5.5", "safetensors==0.8.0")
_BUILD_DIRS = {"convert-int8": ("onnx-int8", "model_int8.onnx", "int8"), "convert-fp16": ("onnx-fp16", "model_fp16.onnx", "fp16")}
THREADS = 8                # 16 スレッドは 8 より遅かった（物理 8 コア）
MAX_DOCUMENTS = 100
IDLE_STOP_S = 600          # 使われなければ 10 分で止める
_install_jobs: dict[str, str] = {}
last_error = ""
stats = {"calls": 0, "last_seconds": 0.0, "slowest_seconds": 0.0, "starts": 0}


class CpuRerankerError(RuntimeError):
    pass


def _spec(model_id: str) -> dict:
    if model_id not in MODELS:
        raise CpuRerankerError(f"未知の reranker です: {model_id}")
    return MODELS[model_id]


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise CpuRerankerError("reranker の領域外のパスは使えません")
    return resolved


def archive_root(model_id: str) -> Path:
    spec = _spec(model_id)
    # 作らない（状態を見るだけでフォルダができないように。2026-11-20 本番で、未導入なのに空のフォルダが残っていた）
    return (data_dir() / "archives" / spec["repo"].split("/")[1]).resolve()


def source_dir(model_id: str) -> Path:
    root = archive_root(model_id)
    return _inside(root / "huggingface" / _spec(model_id)["revision"], root)


def model_path(model_id: str) -> Path:
    spec = _spec(model_id)
    if spec["build"] == "download":
        return source_dir(model_id) / spec["model_file"]
    root = archive_root(model_id)
    folder, name, _ = _BUILD_DIRS[spec["build"]]
    return _inside(root / folder / spec["revision"] / name, root)


def tokenizer_path(model_id: str) -> Path:
    if _spec(model_id)["build"] == "download":
        return source_dir(model_id) / "tokenizer.json"
    return model_path(model_id).parent / "tokenizer.json"


def _disabled_marker(model_id: str) -> Path:
    # ruri の CPU 版と GPU 版は同じ保管庫を使うので、印はモデルごとに分ける
    return archive_root(model_id) / ("uninstalled" if model_id == "ruri-reranker" else f"uninstalled-{model_id}")


def _device(model_id: str | None) -> str:
    return _spec(model_id)["device"] if model_id else "cpu"


def runtime_root(device: str = "cpu") -> Path:
    return (data_dir() / "runtimes" / f"reranker-{device}" / "v1").resolve()   # 作るのは導入のときだけ


def python_path(device: str = "cpu") -> Path:
    return runtime_root(device) / "venv" / "bin" / "python"


def _packages(device: str) -> tuple[str, ...]:
    return GPU_RUNTIME_PACKAGES if device == "gpu" else RUNTIME_PACKAGES


def _size_ok(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def model_ready(model_id: str) -> bool:
    """取得（と変換）が済んでいるか。アンインストールした印があれば使わない（ファイルは保管庫に残る）。"""
    if _disabled_marker(model_id).exists():
        return False
    if _spec(model_id)["build"] in _BUILD_DIRS:
        return model_path(model_id).is_file() and tokenizer_path(model_id).is_file()
    return all(_size_ok(source_dir(model_id) / name, size) for name, (size, _) in _spec(model_id)["files"].items())


def venv_ready(device: str = "cpu") -> bool:
    try:
        marker = (runtime_root(device) / "venv-ready").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return python_path(device).is_file() and marker == ",".join(_packages(device))


def ready(model_id: str | None = None) -> bool:
    """model_id を省くと、どれか 1 つでも使えるか。"""
    if model_id is None:
        return any(ready(m) for m in MODELS)
    return venv_ready(_device(model_id)) and model_ready(model_id)


def pick(fast: bool, *, cpu_only: bool = False) -> str | None:
    """自動のときに使うモデル。GPU の ruri があれば最優先。無ければ会話（fast）は速い xsmall、それ以外は CPU の ruri。
    cpu_only: GPU の ruri が失敗したときの控え（CPU のものだけから選ぶ）。"""
    for model_id in _AUTO_ORDER["fast" if fast else "accurate"]:
        if (not cpu_only or _device(model_id) == "cpu") and ready(model_id):
            return model_id
    return None


# 使う reranker の選択（Models の「Embed / Reranker」）。会話の資料と、Knowledge・Workflow で別に選ぶ。
# auto: 入っているもの（GPU の ruri → 会話は xsmall・それ以外は CPU の ruri）、無ければ llama.cpp の reranker。
# gpu: role=reranker の llama.cpp（Qwen3-Reranker-4B など）。none: 並べ替えない（検索の順のまま）
# （"gpu" は従来の llama.cpp の reranker を指す。GPU の ruri は "ruri-reranker-gpu"）
CHOICES = ("auto", *MODELS, "gpu", "none")
_SELECTION_DEFAULT = {"chat": "auto", "knowledge": "auto"}


def _selection_path() -> Path:
    return data_dir() / "retrieval-settings.json"


def selection() -> dict:
    try:
        saved = json.loads(_selection_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {k: (saved.get(k) if saved.get(k) in CHOICES else v) for k, v in _SELECTION_DEFAULT.items()}


def set_selection(values: dict) -> dict:
    current = selection()
    for key, value in values.items():
        if key not in _SELECTION_DEFAULT or value not in CHOICES:
            raise ValueError(f"選べない値です: {key}={value}")
        current[key] = value
    path = _selection_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return current


def choose(fast: bool) -> str:
    """並べ替えに使うもの: CPU のモデル ID・"gpu"・"none"。選んだ CPU のモデルが使えなければ自動と同じに戻る。"""
    chosen = selection()["chat" if fast else "knowledge"]
    if chosen in ("gpu", "none"):
        return chosen
    if chosen in MODELS and ready(chosen):
        return chosen
    return pick(fast) or "gpu"


def limits(model_id: str, fast: bool) -> tuple[int, int]:
    """(候補を top_k の何倍まで並べ替えるか, 質問＋資料 1 件のトークン上限)。"""
    spec = _spec(model_id)
    return (spec["fast_pool"], spec["fast_max_tokens"]) if fast else (4, spec["max_tokens"])


def status(model_id: str) -> dict:
    spec = _spec(model_id)
    job_id = _install_jobs.get(model_id)
    active = jobs.get(job_id) if job_id else None
    path = model_path(model_id)
    worker = _workers.get(model_id)
    return {
        "id": model_id,
        "name": spec["name"],
        "ready": ready(model_id),
        "installed": ready(model_id),
        "installing": bool(active and active.status in ("queued", "running")),
        "job_id": active.id if active and active.status in ("queued", "running") else None,
        "model": f"{spec['repo']}@{spec['revision'][:8]}",
        "model_bytes": path.stat().st_size if path.is_file() else 0,
        "runtime": spec["runtime_label"],
        "device": spec["device"],
        "accuracy": spec["accuracy"],
        "speed": spec["speed"],
        "memory": spec["memory"],
        "storage": str(archive_root(model_id)),
        "license": spec["license"],
        "running": bool(worker and worker.running()),
        "error": last_error,
        "stats": dict(stats),
    }


async def _run(job: jobs.Job | None, *args: str, cwd: Path, timeout: float) -> str:
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise CpuRerankerError(f"{Path(args[0]).name} が時間内に終わりませんでした")
    text = output.decode("utf-8", errors="replace")
    if job is not None:
        for line in text.splitlines()[-15:]:
            if line.strip():
                job.log(line.strip()[:500])
    if process.returncode != 0:
        raise CpuRerankerError(f"{Path(args[0]).name} が終了コード {process.returncode} で失敗しました")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _download(job: jobs.Job, model_id: str) -> None:
    spec = _spec(model_id)
    root = archive_root(model_id)
    folder = source_dir(model_id)
    total = sum(size for size, _ in spec["files"].values())
    done = 0
    timeout = httpx.Timeout(30.0, read=60.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ControlDeck"}) as client:
        for name, (size, sha256) in spec["files"].items():
            target = _inside(folder / name, root)
            if _size_ok(target, size):
                done += size
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            part = _inside(target.with_name(target.name + ".part"), root)
            part.unlink(missing_ok=True)
            digest = hashlib.sha256()
            received = 0
            url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}"
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with part.open("wb") as stream:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            await asyncio.to_thread(stream.write, chunk)
                            digest.update(chunk)
                            received += len(chunk)
                            job.set_progress("モデルを取得中", done + received, total)
                if received != size or digest.hexdigest() != sha256:
                    raise CpuRerankerError(f"{name} のサイズまたは SHA-256 が一致しません")
                part.replace(target)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            done += size


async def _build(job: jobs.Job, model_id: str) -> dict:
    """一時の build venv で ONNX（CPU 用は int8、GPU 用は fp16）へ変換して確かめ、build venv を消す。"""
    spec = _spec(model_id)
    _, _, mode = _BUILD_DIRS[spec["build"]]
    build = (data_dir() / "runtimes" / "reranker-build").resolve()   # 一時（終わったら消す）
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    out = model_path(model_id).parent
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = _inside(out.with_name(out.name + ".part"), archive_root(model_id))
    shutil.rmtree(staging, ignore_errors=True)
    try:
        job.set_progress(f"{mode} へ変換する道具を準備中（一時）", 0, 3)
        await _run(job, sys.executable, "-m", "venv", str(build / "venv"), cwd=build, timeout=180)
        pip = (str(build / "venv" / "bin" / "python"), "-m", "pip", "install", "--no-input",
               "--disable-pip-version-check", "--quiet")
        await _run(job, *pip, "--index-url", "https://download.pytorch.org/whl/cpu", BUILD_TORCH, cwd=build, timeout=1800)
        await _run(job, *pip, *BUILD_PACKAGES, cwd=build, timeout=1800)
        job.set_progress(f"ONNX（{mode}）へ変換し、元の点数と比べています", 1, 3)
        script = Path(__file__).with_name("cpu_reranker_build.py")
        output = await _run(job, str(build / "venv" / "bin" / "python"), "-I", str(script),
                            str(source_dir(model_id)), str(staging), mode, cwd=build, timeout=1800)
        check = json.loads(output.strip().splitlines()[-1])
        shutil.copy2(source_dir(model_id) / "tokenizer.json", staging / "tokenizer.json")
        shutil.rmtree(out, ignore_errors=True)
        staging.rename(out)
        manifest = {
            "name": spec["name"], "source": {"repo": spec["repo"], "revision": spec["revision"]},
            "license": spec["license"], "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "check": check,
            "how": ("PyTorch → ONNX（opset 17）→ onnxruntime.quantization.quantize_dynamic（QInt8、per-channel、MLP の Wo は fp32）"
                    if mode == "int8" else "PyTorch（fp16 で読む）→ ONNX（opset 17）"),
            "files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in sorted(out.iterdir())],
        }
        (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        job.log(f"{mode} と元の点数の差の最大 {check['max_score_diff']}（順位は同じ）")
        return check
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(build, ignore_errors=True)   # torch など約 1 GB。変換が済めば要らない
        # 元の重み（1.26 GB）も変換が済めば要らない（容量を減らす。もう一方の版を作るときに取り直す）
        weights = source_dir(model_id) / "model.safetensors"
        if out.is_dir() and weights.is_file():
            weights.unlink()


async def install(job: jobs.Job, model_id: str) -> dict:
    """モデルを取り（ruri は int8 へ変換）、実行環境を入れ、1 回並べ替えて確かめる。済んでいる段は飛ばす。"""
    global last_error
    spec = _spec(model_id)
    _disabled_marker(model_id).unlink(missing_ok=True)
    if not model_ready(model_id):
        await _download(job, model_id)
        if spec["build"] in _BUILD_DIRS:
            await _build(job, model_id)
    device = _device(model_id)
    runtime = runtime_root(device)
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / "venv-ready"
    if not venv_ready(device):
        job.set_progress("reranker の実行環境を準備中", 2, 3)
        marker.unlink(missing_ok=True)
        await _run(job, sys.executable, "-m", "venv", "--clear", str(runtime / "venv"), cwd=runtime, timeout=180)
        await _run(job, str(python_path(device)), "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                   "--no-deps", "--quiet", *_packages(device), cwd=runtime, timeout=900)
        if device == "gpu":
            # ROCm の MIGraphX（/opt/rocm）が無いと GPU で動かない。入れた直後に確かめ、分かる言葉で止める
            providers = await _run(job, str(python_path(device)), "-I", "-c",
                                   "import onnxruntime as o; print(','.join(o.get_available_providers()))",
                                   cwd=runtime, timeout=120)
            if "MIGraphXExecutionProvider" not in providers:
                raise CpuRerankerError("AMD GPU（ROCm の MIGraphX）が見つからないため、GPU の reranker を使えません")
        marker.write_text(",".join(_packages(device)) + "\n", encoding="utf-8")
    # GPU は最初の 1 回にコンパイル（30 秒前後、RAM を一時 6 GB ほど使う）が入る。ここで済ませて保存しておく
    job.set_progress("試しに並べ替えて確かめています" + ("（GPU 用のコンパイルを含む）" if device == "gpu" else ""), 2, 3)
    scores = await score("横浜の明日の天気は？", ["番組表の一覧です。", "横浜市の天気予報 - 明日は晴れ。"],
                         model_id=model_id)
    if not scores[1] > scores[0]:
        raise CpuRerankerError("試しの並べ替えの順が正しくありません")
    last_error = ""
    job.set_progress(f"{spec['name']} を利用できます", 3, 3)
    return status(model_id)


def uninstall(model_id: str) -> dict:
    """モデルを使わなくする。取得したファイル・変換した ONNX（保管庫）は残す（入れ直しで取り直さない）。
    実行環境は、ほかに使うモデルが無ければ消す。"""
    worker = _workers.pop(model_id, None)
    if worker is not None:
        worker.kill()
    if archive_root(model_id).is_dir():
        _disabled_marker(model_id).write_text("1\n", encoding="utf-8")
    device = _device(model_id)
    if not any(ready(m) for m in MODELS if m != model_id and _device(m) == device):
        root = (data_dir() / "runtimes" / f"reranker-{device}").resolve()
        if root.exists():
            shutil.rmtree(root)
    return status(model_id)


def start_install_job(model_id: str, owner_user_id: int | None) -> jobs.Job:
    current = jobs.get(_install_jobs[model_id]) if model_id in _install_jobs else None
    if current and current.status in ("queued", "running"):
        return current
    # 進み具合は Models の「Embed / Reranker」のカードに出す（"model." で始めると LLM の画面の上にも出てしまう）
    job = jobs.create("retrieval.install", f"導入: {_spec(model_id)['name']}",
                      lambda job: install(job, model_id), owner_user_id=owner_user_id, priority=-5)
    _install_jobs[model_id] = job.id
    return job


class _Worker:
    """常駐する worker（モデルごと）。1 度に 1 依頼（CPU を取り合わない）。使われなければ止める。"""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None

    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _start(self) -> asyncio.subprocess.Process:
        worker = Path(__file__).with_name("cpu_reranker_worker.py")
        device = _device(self.model_id)
        args = [str(python_path(device)), "-I", str(worker), str(model_path(self.model_id)),
                str(tokenizer_path(self.model_id)), str(THREADS)]
        if device == "gpu":
            cache = runtime_root(device) / "compiled" / self.model_id
            cache.mkdir(parents=True, exist_ok=True)
            args += ["gpu", str(_spec(self.model_id)["batch"]), str(cache)]
        process = await asyncio.create_subprocess_exec(
            *args, cwd=str(runtime_root(device)), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
        try:
            # GPU は読み込み時にコンパイルする（保存済みなら数秒、初回は 30 秒前後）
            line = await asyncio.wait_for(process.stdout.readline(), timeout=300 if device == "gpu" else 120)
            reply = json.loads(line or b"{}")
            if not reply.get("ready"):
                raise CpuRerankerError(str(reply.get("error") or "reranker の worker が起動しませんでした"))
        except BaseException:
            process.kill()
            await process.wait()
            raise
        stats["starts"] += 1
        return process

    def kill(self) -> None:
        """同期の文脈（アンインストール）から止める。"""
        if self.running():
            self._process.kill()

    async def stop(self) -> None:
        if self._idle:
            self._idle.cancel()
            self._idle = None
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()

    def _schedule_stop(self) -> None:
        if self._idle:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(IDLE_STOP_S, lambda: asyncio.ensure_future(self._stop_if_idle()))

    async def _stop_if_idle(self) -> None:
        if not self._lock.locked():
            await self.stop()

    async def score(self, query: str, documents: list[str], timeout: float, max_tokens: int) -> list[float]:
        async with self._lock:
            try:
                if not self.running():
                    self._process = await self._start()
                process = self._process
                request = json.dumps({"query": query, "documents": documents, "max_tokens": max_tokens},
                                     ensure_ascii=False) + "\n"
                process.stdin.write(request.encode("utf-8"))
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
                reply = json.loads(line or b"{}")
            except (TimeoutError, asyncio.CancelledError, OSError, ValueError):
                await self.stop()   # 途中の応答が残った worker は使わない
                raise
            finally:
                if self.running():
                    self._schedule_stop()
        if "error" in reply or len(reply.get("scores", [])) != len(documents):
            raise CpuRerankerError(str(reply.get("error") or "reranker の応答が不正です"))
        return [float(s) for s in reply["scores"]]


_workers: dict[str, _Worker] = {}


async def score(query: str, documents: list[str], *, model_id: str, timeout: float = 120,
                max_tokens: int | None = None) -> list[float]:
    """質問に対する各資料の点数（0〜1）を、資料の順のまま返す。"""
    global last_error
    if not ready(model_id):
        raise CpuRerankerError(f"{_spec(model_id)['name']} が未導入です")
    if not query.strip() or not documents or len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"質問と 1〜{MAX_DOCUMENTS} 件の資料が要ります")
    started = time.monotonic()
    worker = _workers.setdefault(model_id, _Worker(model_id))
    try:
        result = await worker.score(query, documents, timeout, max_tokens or _spec(model_id)["max_tokens"])
    except CpuRerankerError as exc:
        last_error = str(exc)[:300]
        raise
    elapsed = round(time.monotonic() - started, 2)
    stats["calls"] += 1
    stats["last_seconds"] = elapsed
    stats["slowest_seconds"] = max(stats["slowest_seconds"], elapsed)
    last_error = ""
    return result


async def shutdown() -> None:
    for worker in list(_workers.values()):
        await worker.stop()
