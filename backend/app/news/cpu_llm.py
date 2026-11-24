"""ニュースの下ごしらえ（日本語の概要・画像の描写・話題の語）を、CPU と RAM だけで動かす小さな LLM（Gemma 4 E2B）で行う。

2026-11-24 利用者「CPU で動く Gemma とかで記事の翻訳できないか？画像生成と同じような管理で LLM を動かしたい。基本的に CPU と RAM で必ず動かすようにしたい」
→「基本的に CPU で動かした Gemma の小を使う。生成クオリティの問題なかったということなので、それを既定に設定する。なお中と大は不要」。

- 実行は本番の llama.cpp の llama-server を子プロセスで起動する（画像生成の子プロセスと同じ作り）。下ごしらえの 1 回の実行の初めに起動し、その回の
  全バッチを順に流し、終わったら必ず止める（例外・時間切れ・キャンセルでも。プロセスグループごと）。同時に 2 つは起動しない。
- GPU は絶対に使わない: 環境変数（HIP_VISIBLE_DEVICES・ROCR_VISIBLE_DEVICES・CUDA_VISIBLE_DEVICES を空に）と、引数（--device none・-ngl 0）の両方。
  それでも起動のログに GPU を使った印が出たら、その場で止めて失敗にする。
- nice 10・スレッド 8・文脈 16384・127.0.0.1 の空きの port・--no-webui。実測（2026-11-24 本番）: RAM 約 4.1GB・読み込み 214 tok/s・書き出し 25〜27 tok/s・英語 20 件で約 140 秒。
- モデルは data/models/news-llm/gemma-4-E2B-it-Q4_0.gguf（ggml-org/gemma-4-E2B-it-GGUF の Q4_0。版・大きさ・sha256 を固定して取得する）。
  機能「ニュースの下ごしらえ（CPU の LLM）」（registry.py）が導入・状態・削除を持つ。既に置いてあるファイルは導入済みとみなす。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.news.cpu_llm")

FEATURE_ID = "news-cpu-llm"
MODEL_ID = "cpu:gemma-4-e2b"                  # 設定 llm.prep_model の値（既定）
MODEL_LABEL = "CPU（Gemma 4 E2B、GPU を使わない・約 4GB の RAM・1 件約 7 秒）"
REPO = "ggml-org/gemma-4-E2B-it-GGUF"
REVISION = "2a8538c2e2d4687953a86c422c7c623cfd022880"   # 版固定（取得は常にこの版）
FILENAME = "gemma-4-E2B-it-Q4_0.gguf"
SIZE_BYTES = 2841481184
SHA256 = "8e30dff3ac4c8434c49a7036fa15564bdbb6044e42bf04550bf1a096ad7e6a52"
URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{FILENAME}"

NICE = "10"
THREADS = 8
CONTEXT = 16384
START_TIMEOUT = 240.0          # 起動して /health が通るまでの上限（秒）
CALL_TIMEOUT = 400.0           # 1 回の生成の上限（10 件で約 80 秒。余裕を見る）
STOP_GRACE = 5.0
WAIT_OTHER = 1800.0            # 別の下ごしらえが終わるのを待つ上限（秒）

# GPU を見せない環境変数（ここ以外の環境は引き継がない）
GPU_HIDE_ENV = {"HIP_VISIBLE_DEVICES": "", "ROCR_VISIBLE_DEVICES": "", "CUDA_VISIBLE_DEVICES": "", "GGML_VK_VISIBLE_DEVICES": ""}
# 起動のログに GPU を使った印が出たら失敗にする（CPU の印 "CPU_Mapped model buffer" などは対象外）
_GPU_USED = re.compile(r"offloaded [1-9]\d*/\d+ layers to GPU|\b(?:ROCm|CUDA|Vulkan|HIP)\d+\b[^\n]*(?:model buffer|KV buffer|compute buffer)|using device (?:ROCm|CUDA|Vulkan)", re.IGNORECASE)

_run_lock = threading.Lock()          # 同時に 2 つ起動しない
_live: set["CpuLlmServer"] = set()
_live_lock = threading.Lock()
_priority_waiting = 0             # 順番待ちをしているリーダー（利用者が待っている）の数。下ごしらえは次のバッチの前に譲る


class CpuLlmUnavailable(RuntimeError):
    """CPU の LLM が使えない・失敗した（呼び出し側は下ごしらえをせず、理由を返す）。"""


def is_cpu_model(model: object) -> bool:
    return isinstance(model, str) and model.startswith("cpu:")


# ---- 置き場と状態 ----

def models_dir() -> Path:
    return data_dir() / "models" / "news-llm"


def model_path() -> Path:
    return models_dir() / FILENAME


def model_ready() -> bool:
    """モデルのファイルがあるか（置いてあれば導入済みとみなす。大きさ・sha256 は取得のときに確かめた）。"""
    path = model_path()
    return path.is_file() and not path.is_symlink()


def server_binary() -> Path:
    from app.models_mgmt import llama

    return llama.server_path()


def runtime_ready() -> bool:
    binary = server_binary()
    return binary.is_file() and os.access(binary, os.X_OK)


def check_ready() -> tuple[bool, str]:
    """起動できるか。だめなら利用者向けの理由（機能と拡張へ案内）。"""
    if not model_ready():
        return False, "CPU の LLM のモデルが未導入です（設定 → 機能と拡張の「ニュースの下ごしらえ（CPU の LLM）」を導入してください）"
    if not runtime_ready():
        return False, "llama.cpp のランタイムが未導入です（設定 → 機能と拡張の「llama.cpp ランタイム」を導入してください）"
    return True, ""


def info() -> dict:
    """設定の画面用の状態。"""
    return {"id": MODEL_ID, "label": MODEL_LABEL, "installed": model_ready(), "runtime_ready": runtime_ready(),
            "megabytes": round(SIZE_BYTES / 1_000_000), "ram_gb": 4, "seconds_per_item": 7}


def remove_model() -> None:
    """モデルのファイルを消す（置き場の直下の 1 ファイルだけ。リンクは辿らない）。"""
    root = models_dir()
    for path in (model_path(), root / (FILENAME + ".part")):
        if path.parent == root and (path.is_file() or path.is_symlink()):
            path.unlink()
    with contextlib.suppress(OSError):
        root.rmdir()      # 空になったときだけ消える


# ---- 取得 ----

def download_model(progress=None) -> Path:
    """固定した版のモデルを Hugging Face から取得する（続きから・大きさと sha256 を確かめてから置く）。取得済みなら何もしない。"""
    import httpx

    if model_ready():
        return model_path()
    root = models_dir()
    root.mkdir(parents=True, exist_ok=True)
    free = os.statvfs(root)
    if free.f_bavail * free.f_frsize < SIZE_BYTES + 512 * 1024 * 1024:
        raise CpuLlmUnavailable("ディスクの空きが足りません（約 3.4GB 要ります）")
    part = root / (FILENAME + ".part")
    existing = part.stat().st_size if part.is_file() else 0
    if existing > SIZE_BYTES:
        part.unlink()
        existing = 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    try:
        with httpx.stream("GET", URL, headers=headers, follow_redirects=True, timeout=httpx.Timeout(60.0, connect=30.0)) as response:
            if response.status_code == 416:
                existing = SIZE_BYTES
            elif response.status_code >= 400:
                raise CpuLlmUnavailable(f"モデルを取得できませんでした（HTTP {response.status_code}）")
            else:
                mode = "ab"
                if existing and response.status_code != 206:
                    existing, mode = 0, "wb"      # 続きの取得に対応していなければ最初から
                received = existing
                with part.open(mode) as target:
                    for chunk in response.iter_bytes(1024 * 1024):
                        target.write(chunk)
                        received += len(chunk)
                        if progress is not None:
                            progress(received, SIZE_BYTES)
    except httpx.HTTPError as exc:
        raise CpuLlmUnavailable(f"モデルを取得できませんでした: {type(exc).__name__}") from exc
    if part.stat().st_size != SIZE_BYTES:
        raise CpuLlmUnavailable("取得したモデルの大きさが合いません。もう一度導入してください（続きから取得します）")
    digest = hashlib.sha256()
    with part.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SHA256:
        part.unlink()
        raise CpuLlmUnavailable("取得したモデルの sha256 が合いません。取得し直してください")
    os.replace(part, model_path())
    return model_path()


# ---- 起動の引数と環境（試験で確かめる） ----

def build_command(binary: Path, model: Path, port: int, threads: int = THREADS) -> list[str]:
    """GPU を使わない起動の引数。--device none（デバイスを 1 つも使わない）と -ngl 0（層を GPU に載せない）の両方。"""
    return ["nice", "-n", NICE, str(binary), "-m", str(model), "--device", "none", "-ngl", "0", "-t", str(threads), "-c", str(CONTEXT),
            "--host", "127.0.0.1", "--port", str(port), "--no-webui"]


def build_env(binary: Path) -> dict[str, str]:
    """子プロセスの環境。引き継ぐのは PATH だけで、GPU を見せる変数は空にする。共有ライブラリはバイナリと同じ場所。"""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LD_LIBRARY_PATH": str(binary.resolve().parent), "HOME": str(data_dir()), **GPU_HIDE_ENV}


def gpu_used(log_text: str) -> bool:
    """起動のログに GPU を使った印があるか。"""
    return bool(_GPU_USED.search(log_text or ""))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _log_path() -> Path:
    return data_dir() / "news" / "cpu-llm.log"


# ---- サーバー（子プロセス） ----

class CpuLlmServer:
    """llama-server の 1 回分の子プロセス。start → complete（何回でも）→ stop。stop は何度呼んでもよい。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self._closing = False
        self._log = None

    # 同期（スレッドで呼ぶ）
    def start(self) -> None:
        ok, why = check_ready()
        if not ok:
            raise CpuLlmUnavailable(why)
        binary = server_binary()
        self.port = _free_port()
        log = _log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self._log = os.fdopen(fd, "wb")
        try:
            self.proc = subprocess.Popen(build_command(binary, model_path(), self.port), stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
                                         env=build_env(binary), start_new_session=True)
        except OSError as exc:
            self.stop()
            raise CpuLlmUnavailable(f"llama-server を起動できませんでした: {exc}") from exc
        with _live_lock:
            _live.add(self)
        try:
            self._wait_ready()
            if gpu_used(self._read_log()):
                raise CpuLlmUnavailable("起動のログに GPU を使った印がありました。GPU は使わない決まりなので止めました")
        except BaseException:
            self.stop()
            raise

    def _read_log(self) -> str:
        try:
            return _log_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _wait_ready(self) -> None:
        import httpx

        deadline = time.monotonic() + START_TIMEOUT
        url = f"http://127.0.0.1:{self.port}/health"
        while time.monotonic() < deadline:
            if self._closing:
                raise CpuLlmUnavailable("起動の途中で止められました")
            if self.proc is None or self.proc.poll() is not None:
                tail = self._read_log().strip().splitlines()[-1:] or [""]
                raise CpuLlmUnavailable(f"llama-server が起動の途中で終わりました（{tail[0][-200:]}）")
            try:
                if httpx.get(url, timeout=2.0, trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise CpuLlmUnavailable(f"llama-server が {int(START_TIMEOUT)} 秒たっても使えるようになりませんでした")

    def stop(self) -> None:
        """プロセスグループごと止める（まず SIGTERM、待っても残れば SIGKILL）。"""
        self._closing = True
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            for sig, wait in ((signal.SIGTERM, STOP_GRACE), (signal.SIGKILL, STOP_GRACE)):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    proc.wait(timeout=wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.wait(timeout=1)
        if self._log is not None:
            with contextlib.suppress(OSError):
                self._log.close()
            self._log = None
        with _live_lock:
            _live.discard(self)

    # 非同期
    async def complete(self, messages: list[dict], *, max_tokens: int, schema: dict | None = None, temperature: float = 0.2,
                       think: bool | None = None) -> str:
        """1 回の生成。schema があれば llama-server の response_format: json_schema で出力の形を強制する（壊れた JSON が出ない）。"""
        import httpx

        if self.proc is None or self.proc.poll() is not None:
            raise CpuLlmUnavailable("llama-server が止まっています")
        body: dict = {"model": "news-cpu", "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False}
        if think is not None:      # False なら考える過程（reasoning）を出さない（長い入力だと考えるだけで出力の上限を使い切り、答えが空になる）
            body["chat_template_kwargs"] = {"enable_thinking": think}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "prep", "strict": True, "schema": schema}}
        try:
            async with httpx.AsyncClient(timeout=CALL_TIMEOUT, trust_env=False) as client:
                response = await client.post(f"http://127.0.0.1:{self.port}/v1/chat/completions", json=body)
            if response.status_code != 200:
                raise CpuLlmUnavailable(f"llama-server が HTTP {response.status_code} を返しました: {response.text[:200]}")
            text = str(response.json()["choices"][0]["message"].get("content") or "")
        except httpx.HTTPError as exc:
            raise CpuLlmUnavailable(f"llama-server への呼び出しに失敗しました: {type(exc).__name__}") from exc
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise CpuLlmUnavailable("llama-server の返事の形が違います") from exc
        if not text.strip():
            raise CpuLlmUnavailable("LLM の答えが空でした")
        return text


@contextlib.asynccontextmanager
async def session(priority: bool = False) -> AsyncIterator[CpuLlmServer]:
    """下ごしらえの 1 回の実行の間だけ llama-server を起動する。抜けるときは（例外・キャンセルでも）必ず止める。同時に 2 つは起動しない。"""
    global _priority_waiting
    if priority:
        _priority_waiting += 1
    try:
        got = await asyncio.to_thread(_run_lock.acquire, True, WAIT_OTHER)
    finally:
        if priority:
            _priority_waiting -= 1
    if not got:
        raise CpuLlmUnavailable("別の下ごしらえが終わるのを待ちましたが、終わりませんでした")
    server = CpuLlmServer()
    try:
        await asyncio.to_thread(server.start)
        yield server
    finally:
        try:
            await asyncio.shield(asyncio.to_thread(server.stop))
        finally:
            _run_lock.release()


def priority_waiting() -> int:
    """リーダー（利用者が待っている）が CPU の LLM の順番を待っているか。待っていれば下ごしらえは次のバッチの前に譲る。"""
    return _priority_waiting


def stop_all() -> None:
    """残っている子プロセスをすべて止める（本体の終了時）。"""
    with _live_lock:
        servers = list(_live)
    for server in servers:
        server.stop()


import atexit  # noqa: E402

atexit.register(stop_all)
