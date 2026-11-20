"""CPU の reranker（ruri-v3-reranker-310m を ONNX int8 で動かす）。導入・保管庫・並べ替え。

2026-11-20 利用者「reranker はなるべく軽量・高精度・高速に。GPU に置かず CPU で動かせるとさらに良い」。
評価（docs/reranker-evaluation.md）で、日本語の並べ替えは ruri-v3-reranker-310m が最良（JQaRA nDCG@10 0.877、
今の Qwen3-Reranker-4B は 0.743）。利用者が採用を決め、int8 化を求めた。

- 重み（cl-nagoya/ruri-v3-reranker-310m、Apache-2.0）は版を固定して取り、サイズと SHA-256 を照合する
- 導入時に一時の build venv（PyTorch CPU・transformers・onnx）で ONNX へ書き出し、ONNX Runtime で int8 にする。
  PyTorch fp32 と点数の順が同じかを確かめてから使い、build venv は消す
- 重みと int8 の ONNX は data/archives/ruri-v3-reranker-310m に残す（入れ直しで変換し直さない）
- 実行環境は onnxruntime・tokenizers・numpy だけ（torch は要らない）
- worker は使うときに起動して常駐し、しばらく使われなければ止める（RAM を返す）

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

ENGINE_ID = "ruri-reranker"
NAME = "ruri-v3-reranker-310m（CPU）"
LICENSE_NOTE = "Apache-2.0（名古屋大学 cl-nagoya）"
HF_REPO = "cl-nagoya/ruri-v3-reranker-310m"
HF_REVISION = "bb46934ee9ed09f850b9fcff17501b3ef7ddb2b3"
# 変換に要るファイル（2026-11-20 取得・照合）。path: (bytes, sha256)
HF_FILES: dict[str, tuple[int, str]] = {
    "config.json": (1377, "62216cae7ea4b690db21aa8c4086a88f26af6173f2795d4c1a807755f3a899a8"),
    "model.safetensors": (1260829436, "9f747a085a0adba9490205357be306ba3234bc958a30a19baa6b0bb14e9e83fe"),
    "special_tokens_map.json": (968, "30bf8256f9a1eb3287af2a9b7940465e29a38aad4a459a3e773bb3a14bd34c0f"),
    "tokenizer.json": (6724873, "0a94ac9a0a02c067bdef25b72ae9f4ee33f48f552e55988d444f6d25eeb1d062"),
    "tokenizer.model": (1831879, "008293028e1a9d9a1038d9b63d989a2319797dfeaa03f171093a57b33a3a8277"),
    "tokenizer_config.json": (3779, "34004511993472f15c7202074284c57788c161fb6567de164b8e0c0266f155e4"),
}
RUNTIME_PACKAGES = ("onnxruntime==1.32.0", "tokenizers==0.23.3", "numpy==2.5.5", "flatbuffers==25.12.19",
                    "packaging==26.3", "protobuf==7.36.2")
# 変換だけに使う（終わったら消す）。torch は CPU 版を PyTorch の配布元から取る
BUILD_TORCH = "torch==2.15.0"
BUILD_PACKAGES = ("transformers==5.23.0", "onnx==1.23.2", "onnxruntime==1.32.0", "tokenizers==0.23.3",
                  "numpy==2.5.5", "safetensors==0.8.0")
THREADS = 8
# 質問＋資料 1 件の上限（超えた分は資料の後ろを切る）。計算量は件数×長さに比例する。
# 実測（2026-11-20、8 スレッド、約 800 文字の断片）: 256 で 16 件 1.65 秒・32 件 3.3 秒、384 で 16 件 2.9 秒・32 件 5.7 秒。
# 比較: GPU の Qwen3-Reranker-4B（flash-attn on）は同じ 800 文字 × 32 件で 3.8 秒
MAX_TOKENS = 384
FAST_MAX_TOKENS = 256      # 会話の資料（3 秒まで）
MAX_DOCUMENTS = 100
IDLE_STOP_S = 600          # 使われなければ 10 分で止める
_install_job_id: str | None = None
last_error = ""
stats = {"calls": 0, "last_seconds": 0.0, "slowest_seconds": 0.0, "starts": 0}


class CpuRerankerError(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise CpuRerankerError("reranker の領域外のパスは使えません")
    return resolved


def archive_root() -> Path:
    root = (data_dir() / "archives" / "ruri-v3-reranker-310m").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def weights_dir() -> Path:
    root = archive_root()
    return _inside(root / "huggingface" / HF_REVISION, root)


def onnx_dir() -> Path:
    root = archive_root()
    return _inside(root / "onnx-int8" / HF_REVISION, root)


def runtime_root() -> Path:
    root = (data_dir() / "runtimes" / "reranker-ruri" / "v1").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def python_path() -> Path:
    return runtime_root() / "venv" / "bin" / "python"


def _size_ok(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def model_ready() -> bool:
    folder = onnx_dir()
    return (folder / "model_int8.onnx").is_file() and (folder / "tokenizer.json").is_file()


def venv_ready() -> bool:
    try:
        marker = (runtime_root() / "venv-ready").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return python_path().is_file() and marker == ",".join(RUNTIME_PACKAGES)


def ready() -> bool:
    return venv_ready() and model_ready()


def status() -> dict:
    active = jobs.get(_install_job_id) if _install_job_id else None
    installing = bool(active and active.status in ("queued", "running"))
    model = onnx_dir() / "model_int8.onnx"
    return {
        "id": ENGINE_ID,
        "name": NAME,
        "ready": ready(),
        "installed": ready(),
        "installing": installing,
        "model": f"{HF_REPO}@{HF_REVISION[:8]}",
        "model_bytes": model.stat().st_size if model.is_file() else 0,
        "runtime": "ONNX Runtime 1.32.0（CPU・int8）",
        "storage": str(archive_root()),
        "license": LICENSE_NOTE,
        "running": _worker.running(),
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


async def _download_weights(job: jobs.Job) -> None:
    root = archive_root()
    folder = weights_dir()
    folder.mkdir(parents=True, exist_ok=True)
    total = sum(size for size, _ in HF_FILES.values())
    done = 0
    timeout = httpx.Timeout(30.0, read=60.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ControlDeck"}) as client:
        for name, (size, sha256) in HF_FILES.items():
            target = _inside(folder / name, root)
            if _size_ok(target, size):
                done += size
                continue
            part = _inside(folder / f"{name}.part", root)
            part.unlink(missing_ok=True)
            digest = hashlib.sha256()
            received = 0
            url = f"https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}/{name}"
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with part.open("wb") as stream:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            await asyncio.to_thread(stream.write, chunk)
                            digest.update(chunk)
                            received += len(chunk)
                            job.set_progress("reranker の重みを取得中", done + received, total)
                if received != size or digest.hexdigest() != sha256:
                    raise CpuRerankerError(f"{name} のサイズまたは SHA-256 が一致しません")
                part.replace(target)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            done += size


async def _build_onnx(job: jobs.Job) -> dict:
    """一時の build venv で ONNX（int8）へ変換して確かめ、build venv を消す。"""
    build = (data_dir() / "runtimes" / "reranker-ruri" / "build").resolve()
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    out = onnx_dir()
    staging = _inside(out.with_name(out.name + ".part"), archive_root())
    shutil.rmtree(staging, ignore_errors=True)
    try:
        job.set_progress("int8 へ変換する道具を準備中（一時）", 0, 3)
        await _run(job, sys.executable, "-m", "venv", str(build / "venv"), cwd=build, timeout=180)
        pip = (str(build / "venv" / "bin" / "python"), "-m", "pip", "install", "--no-input",
               "--disable-pip-version-check", "--quiet")
        await _run(job, *pip, "--index-url", "https://download.pytorch.org/whl/cpu", BUILD_TORCH, cwd=build, timeout=1800)
        await _run(job, *pip, *BUILD_PACKAGES, cwd=build, timeout=1800)
        job.set_progress("ONNX（int8）へ変換し、元の点数と比べています", 1, 3)
        script = Path(__file__).with_name("cpu_reranker_build.py")
        output = await _run(job, str(build / "venv" / "bin" / "python"), "-I", str(script),
                            str(weights_dir()), str(staging), cwd=build, timeout=1800)
        check = json.loads(output.strip().splitlines()[-1])
        shutil.copy2(weights_dir() / "tokenizer.json", staging / "tokenizer.json")
        shutil.rmtree(out, ignore_errors=True)
        staging.rename(out)
        manifest = {
            "name": NAME, "source": {"repo": HF_REPO, "revision": HF_REVISION}, "license": LICENSE_NOTE,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "check": check,
            "how": "PyTorch → ONNX（opset 17）→ onnxruntime.quantization.quantize_dynamic（QInt8、per-channel）",
            "files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in sorted(out.iterdir())],
        }
        (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        job.log(f"int8 と元の点数の差の最大 {check['max_score_diff']}（順位は同じ）")
        return check
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(build, ignore_errors=True)   # torch など約 1 GB。変換が済めば要らない


async def install(job: jobs.Job) -> dict:
    """重みを取り、int8 の ONNX を作り（保管庫にあれば作らない）、実行環境を入れて、1 回並べ替えて確かめる。"""
    global last_error
    if not model_ready():
        await _download_weights(job)
        await _build_onnx(job)
    runtime = runtime_root()
    marker = runtime / "venv-ready"
    if not venv_ready():
        job.set_progress("reranker の実行環境を準備中", 2, 3)
        marker.unlink(missing_ok=True)
        await _run(job, sys.executable, "-m", "venv", "--clear", str(runtime / "venv"), cwd=runtime, timeout=180)
        await _run(job, str(python_path()), "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                   "--no-deps", "--quiet", *RUNTIME_PACKAGES, cwd=runtime, timeout=900)
        marker.write_text(",".join(RUNTIME_PACKAGES) + "\n", encoding="utf-8")
    job.set_progress("試しに並べ替えて確かめています", 2, 3)
    scores = await score("横浜の明日の天気は？", ["番組表の一覧です。", "横浜市の天気予報 - 明日は晴れ。"])
    if not scores[1] > scores[0]:
        raise CpuRerankerError("試しの並べ替えの順が正しくありません")
    last_error = ""
    job.set_progress("CPU の reranker を利用できます", 3, 3)
    return status()


def uninstall() -> dict:
    """実行環境だけを消す。保管庫（重み・int8 の ONNX）は残す（入れ直しで変換し直さない）。"""
    _worker.kill()
    root = (data_dir() / "runtimes" / "reranker-ruri").resolve()
    if root.exists():
        shutil.rmtree(root)
    return status()


def start_install_job(owner_user_id: int | None) -> jobs.Job:
    global _install_job_id
    current = jobs.get(_install_job_id) if _install_job_id else None
    if current and current.status in ("queued", "running"):
        return current
    job = jobs.create("feature.install", "CPU の reranker を導入", install, owner_user_id=owner_user_id, priority=-5)
    _install_job_id = job.id
    return job


class _Worker:
    """常駐する worker。1 度に 1 依頼（CPU を取り合わない）。使われなければ止める。"""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None

    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _start(self) -> asyncio.subprocess.Process:
        worker = Path(__file__).with_name("cpu_reranker_worker.py")
        process = await asyncio.create_subprocess_exec(
            str(python_path()), "-I", str(worker), str(onnx_dir()), str(THREADS),
            cwd=str(runtime_root()), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=120)
            if not json.loads(line or b"{}").get("ready"):
                raise CpuRerankerError("reranker の worker が起動しませんでした")
        except BaseException:
            process.kill()
            await process.wait()
            raise
        stats["starts"] += 1
        return process

    def kill(self) -> None:
        """同期の文脈（アンインストール）から止める。後始末は次の stop / 起動で行う。"""
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


_worker = _Worker()


async def score(query: str, documents: list[str], *, timeout: float = 120, max_tokens: int = MAX_TOKENS) -> list[float]:
    """質問に対する各資料の点数（0〜1）を、資料の順のまま返す。"""
    global last_error
    if not ready():
        raise CpuRerankerError("CPU の reranker が未導入です")
    if not query.strip() or not documents or len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"質問と 1〜{MAX_DOCUMENTS} 件の資料が要ります")
    started = time.monotonic()
    try:
        result = await _worker.score(query, documents, timeout, max_tokens)
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
    await _worker.stop()


__all__ = ["ENGINE_ID", "NAME", "CpuRerankerError", "install", "uninstall", "ready", "score", "status",
           "start_install_job", "shutdown", "python_path"]
