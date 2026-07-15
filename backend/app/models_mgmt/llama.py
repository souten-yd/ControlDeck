"""llama.cpp ランタイム管理（第一級のローカル LLM プロバイダー）。

- 指定リリースから backend（Vulkan/ROCm/CUDA）を選んで導入。
- systemd ユーザーユニットで llama-server を常駐（Web プロセスの子にしない）。
- OpenAI 互換エンドポイント（http://127.0.0.1:<port>/v1）として登録し、
  既存のチャット/ワークフロー/RAG から Ollama と同じインターフェースで使える。

方針: バグ取りは深追いしない。バイナリが起動しない環境では experimental として
明示し、他機能（Ollama 等）に影響を与えない。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
from pathlib import Path

import httpx

from app.config import data_dir

logger = logging.getLogger("control_deck.llama")

RELEASE_REPO = "souten-yd/llama-builder"
DEFAULT_TAG = "llama-gpu-b10001"
UNIT_PREFIX = "cdapp-llama"  # cdapp- 始まりで systemd ヘルパーの検証を満たす

# backend 種別 → リリース asset 名のマッチ規則（Linux のみ）
BACKEND_PATTERNS = {
    "vulkan": re.compile(r"linux.*vulkan.*\.tar\.gz$", re.I),
    "rocm": re.compile(r"linux.*rocm.*\.tar\.gz$", re.I),
    "cuda": re.compile(r"linux.*cuda.*\.tar\.gz$", re.I),
}
# llama.cpp としてユーザーに提示するバックエンド。CUDA(NVIDIA)は当面 Ollama を使う方針のため除外。
SELECTABLE_BACKENDS = ("rocm", "vulkan")


def runtimes_dir() -> Path:
    return data_dir() / "runtimes" / "llama.cpp"


def current_link() -> Path:
    return runtimes_dir() / "current"


def server_path() -> Path:
    """現在版の llama-server バイナリの想定パス。"""
    return current_link() / "llama-server"


def _lib_dir() -> Path:
    """共有ライブラリ（libllama-server-impl.so 等）のディレクトリ。バイナリと同じ場所。"""
    return current_link()


def _config_path() -> Path:
    return data_dir() / "llama-runtime.json"


DEFAULT_CONFIG = {
    "tag": "",
    "backend": "",          # vulkan / rocm / cuda
    "sha256": "",
    "installed_at": "",
    # 現在選択中モデルの型付き起動設定。自由extra_argsは受け付けない。
    "instance": {
        "model_path": "",
        "port": 8080,
        "n_gpu_layers": 999,   # 全層 GPU（VRAM 不足時は下げる）
        "ctx_size": 4096,
        "n_parallel": 1,
        "flash_attn": False,
        "n_predict": 2048,
        "batch_size": 2048,
        "ubatch_size": 512,
        "cache_type_k": "f16",
        "cache_type_v": "f16",
        "threads": -1,
        "threads_batch": -1,
        "mmap": True,
        "mlock": False,
        "spec_type": "none",
        "draft_max": 16,
        "cpu_moe": False,
        "n_cpu_moe": 0,
        "temperature": 0.8,
        "top_k": 40,
        "top_p": 0.95,
        "min_p": 0.05,
        "repeat_penalty": 1.0,
        "seed": -1,
        "alias": "llama",
    },
}


def get_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = _config_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text())
            # instanceを丸ごと置換すると、新版で追加した既定項目が旧設定から欠落する。
            cfg.update({k: v for k, v in saved.items() if k in cfg and k != "instance"})
            if isinstance(saved.get("instance"), dict):
                cfg["instance"].update({
                    key: value for key, value in saved["instance"].items()
                    if key in cfg["instance"]
                })
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(patch: dict) -> dict:
    cfg = get_config()
    for k, v in patch.items():
        if k == "instance" and isinstance(v, dict):
            cfg["instance"].update({ik: iv for ik, iv in v.items() if ik in cfg["instance"]})
        elif k in cfg:
            cfg[k] = v
    _config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


def is_installed() -> bool:
    return server_path().exists() and os.access(server_path(), os.X_OK)


# ---- 環境検出 / バックエンド切り替え ----


def detect_backends() -> dict:
    """このマシンで実際に使える GPU バックエンドを検出する。

    使えないものは選択肢に出さない/警告するために使う。
    - rocm: /dev/kfd（AMD ROCm カーネルドライバ）+ rocminfo/ライブラリ
    - vulkan: vulkaninfo または libvulkan
    - cuda: nvidia-smi または /usr/local/cuda
    """
    rocm = os.path.exists("/dev/kfd") and (
        shutil.which("rocminfo") is not None or os.path.isdir("/opt/rocm")
    )
    vulkan = shutil.which("vulkaninfo") is not None or _has_lib("libvulkan.so")
    cuda = shutil.which("nvidia-smi") is not None or any(
        os.path.isdir(p) for p in ("/usr/local/cuda", "/opt/cuda")
    )
    return {"rocm": rocm, "vulkan": vulkan, "cuda": cuda}


def _has_lib(name: str) -> bool:
    import subprocess

    try:
        out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5).stdout
        return name in out
    except Exception:
        return False


def _backend_root(backend: str, tag: str) -> Path:
    return runtimes_dir() / tag / backend / "extracted"


def installed_backends(tag: str = DEFAULT_TAG) -> list[str]:
    """ダウンロード済み（展開済み）の backend 一覧。切り替え候補になる。"""
    out = []
    for b in BACKEND_PATTERNS:
        if _find_binary(_backend_root(b, tag), "llama-server") is not None:
            out.append(b)
    return out


def switch_backend(backend: str, tag: str = DEFAULT_TAG) -> dict:
    """導入済みの別 backend へ current を張り替える（再ダウンロード不要）。"""
    server = _find_binary(_backend_root(backend, tag), "llama-server")
    if server is None:
        raise RuntimeError(f"{backend} は未導入です。先に導入してください")
    server.chmod(0o755)
    link = current_link()
    if link.is_symlink() or link.exists():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(server.parent, target_is_directory=True)
    save_config({"tag": tag, "backend": backend})
    return {"backend": backend, "server": str(server_path())}


def runtime_status() -> dict:
    cfg = get_config()
    inst = cfg["instance"]
    detected = detect_backends()
    installed = installed_backends()
    # 選択肢: rocm/vulkan のうち検出された（=このマシンで動く）+ 導入済み。
    # CUDA(NVIDIA) は当面 Ollama 利用のため llama.cpp の選択肢に出さない。
    selectable = sorted(
        {b for b in SELECTABLE_BACKENDS if detected.get(b)} | {b for b in installed if b in SELECTABLE_BACKENDS}
    )
    return {
        "installed": is_installed(),
        "tag": cfg.get("tag", ""),
        "backend": cfg.get("backend", ""),  # 現在 current が指す backend
        "sha256": cfg.get("sha256", ""),
        "server_path": str(server_path()) if is_installed() else None,
        "port": inst.get("port"),
        "model_path": inst.get("model_path", ""),
        "alias": inst.get("alias", "llama"),
        "instance": dict(inst),
        "base_url": f"http://127.0.0.1:{inst.get('port', 8080)}/v1" if is_installed() else None,
        "experimental": True,  # ビルド環境依存のため実験的
        "detected_backends": detected,       # {rocm/vulkan/cuda: bool}
        "installed_backends": installed,     # 導入済み（切り替え可能）
        "selectable_backends": selectable,   # UI に出す選択肢
    }


# ---- リリース asset ----


async def list_assets(tag: str = DEFAULT_TAG) -> list[dict]:
    """リリースの Linux 向け asset（backend 判別付き）を返す。"""
    url = f"https://api.github.com/repos/{RELEASE_REPO}/releases/tags/{tag}"
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "ControlDeck"}) as client:
        r = await client.get(url)
    if r.status_code >= 400:
        raise RuntimeError(f"リリース情報の取得に失敗しました ({r.status_code})")
    out = []
    for a in r.json().get("assets", []):
        backend = next((b for b, pat in BACKEND_PATTERNS.items() if pat.search(a["name"])), None)
        if backend is None:
            continue
        out.append({
            "name": a["name"], "backend": backend, "size": a["size"],
            "download_url": a["browser_download_url"], "updated_at": a.get("updated_at", ""),
        })
    return out


def _pick_asset(assets: list[dict], backend: str) -> dict | None:
    return next((a for a in assets if a["backend"] == backend), None)


async def install_stream(job, backend: str, tag: str = DEFAULT_TAG):
    """指定 backend の llama.cpp を導入する（ジョブ本体）。進捗を job に記録する。"""
    assets = await list_assets(tag)
    asset = _pick_asset(assets, backend)
    if asset is None:
        raise RuntimeError(f"{backend} 向けの Linux asset が見つかりません（利用可能: {[a['backend'] for a in assets]}）")

    dest_root = runtimes_dir() / tag / backend
    dest_root.mkdir(parents=True, exist_ok=True)
    archive = dest_root.parent / asset["name"]
    total = asset["size"]

    # 1. ダウンロード（進捗）
    job.log(f"ダウンロード開始: {asset['name']}（{total // 1024 // 1024}MB）")
    h = hashlib.sha256()
    done = 0
    async with httpx.AsyncClient(timeout=None, follow_redirects=True, headers={"User-Agent": "ControlDeck"}) as client:
        async with client.stream("GET", asset["download_url"]) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"ダウンロード失敗 ({r.status_code})")
            with archive.open("wb") as f:
                async for chunk in r.aiter_bytes(1024 * 1024):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    job.set_progress("ダウンロード中", done, total)
    digest = f"sha256:{h.hexdigest()}"
    job.log(f"ダウンロード完了。展開します（SHA256 {digest[:20]}…）")

    # 2. 展開（tar.gz を安全に）
    job.set_progress("展開中")
    extract_dir = dest_root / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            # パストラバーサル防止
            target = (extract_dir / member.name).resolve()
            if not str(target).startswith(str(extract_dir.resolve())):
                raise RuntimeError(f"不正なアーカイブメンバー: {member.name}")
        tar.extractall(extract_dir)
    archive.unlink(missing_ok=True)

    # 3. llama-server を探して current にリンク
    server = _find_binary(extract_dir, "llama-server")
    if server is None:
        raise RuntimeError("アーカイブ内に llama-server が見つかりません")
    bin_root = server.parent
    server.chmod(0o755)
    link = current_link()
    if link.is_symlink() or link.exists():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(bin_root, target_is_directory=True)

    save_config({"tag": tag, "backend": backend, "sha256": digest,
                 "installed_at": _now_iso()})
    job.log(f"導入完了: {backend} 版 llama-server → {link}")
    return {"backend": backend, "tag": tag, "server": str(server_path()), "sha256": digest}


def _find_binary(root: Path, name: str) -> Path | None:
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


# ---- インスタンス（systemd） ----


def unit_name() -> str:
    return f"{UNIT_PREFIX}.service"


def _unit_content() -> str:
    from app.applications.systemd import _escape_exec_arg

    cfg = get_config()
    inst = cfg["instance"]
    if not inst.get("model_path"):
        raise RuntimeError("モデルファイルが未設定です")
    args = [
        str(server_path()),
        "--model", inst["model_path"],
        "--host", "127.0.0.1",
        "--port", str(inst.get("port", 8080)),
        "--n-gpu-layers", str(inst.get("n_gpu_layers", 999)),
        "--ctx-size", str(inst.get("ctx_size", 4096)),
        "--parallel", str(inst.get("n_parallel", 1)),
        "--alias", inst.get("alias", "llama"),
        "--n-predict", str(inst.get("n_predict", 2048)),
        "--batch-size", str(inst.get("batch_size", 2048)),
        "--ubatch-size", str(inst.get("ubatch_size", 512)),
        "--cache-type-k", str(inst.get("cache_type_k", "f16")),
        "--cache-type-v", str(inst.get("cache_type_v", "f16")),
        "--threads", str(inst.get("threads", -1)),
        "--threads-batch", str(inst.get("threads_batch", -1)),
        "--temp", str(inst.get("temperature", 0.8)),
        "--top-k", str(inst.get("top_k", 40)),
        "--top-p", str(inst.get("top_p", 0.95)),
        "--min-p", str(inst.get("min_p", 0.05)),
        "--repeat-penalty", str(inst.get("repeat_penalty", 1.0)),
        "--seed", str(inst.get("seed", -1)),
    ]
    if inst.get("flash_attn"):
        args += ["--flash-attn"]
    if not inst.get("mmap", True):
        args += ["--no-mmap"]
    if inst.get("mlock"):
        args += ["--mlock"]
    spec_type = str(inst.get("spec_type", "none"))
    if spec_type != "none":
        args += ["--spec-type", spec_type, "--draft-max", str(inst.get("draft_max", 16))]
    if inst.get("cpu_moe"):
        args += ["--cpu-moe"]
    elif int(inst.get("n_cpu_moe", 0)) > 0:
        args += ["--n-cpu-moe", str(inst["n_cpu_moe"])]
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from app.models_mgmt import amd_gpu
    from app.models_mgmt.runtime_policy import get_policy

    preflight_commands = amd_gpu.preflight_argvs(get_policy().amd_gpu)
    lines = [
        "[Unit]",
        "Description=Control Deck llama.cpp server",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        # 共有ライブラリ（libllama-server-impl.so 等）はバイナリと同じ場所にある
        f'Environment="LD_LIBRARY_PATH={_lib_dir()}"',
    ]
    for preflight in preflight_commands:
        lines.append("ExecStartPre=" + " ".join(_escape_exec_arg(a) for a in preflight))
    lines += [
        "ExecStart=" + " ".join(_escape_exec_arg(a) for a in args),
        "Restart=on-failure",
        "RestartSec=3",
        "TimeoutStopSec=20",
        "KillSignal=SIGTERM",
        f"StandardOutput=append:{log_dir}/llama-server.log",
        f"StandardError=append:{log_dir}/llama-server.log",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def start_instance() -> tuple[bool, str]:
    from app.applications import systemd as sd

    if not is_installed():
        return False, "llama.cpp が未導入です"
    if not Path(get_config()["instance"].get("model_path", "")).is_file():
        return False, "モデルファイルが存在しません"
    try:
        from app.models_mgmt.runtime_policy import ensure_gpu_profile

        ensure_gpu_profile(force=True)
    except RuntimeError as exc:
        return False, str(exc)
    name = unit_name()
    content = _unit_content()
    path = sd.user_unit_dir() / name
    try:
        changed = not path.exists() or path.read_text(encoding="utf-8") != content
    except OSError:
        changed = True
    sd.write_unit(name, content)
    sd.reset_failed(name)
    active = sd.query_status(name).get("status") == "RUNNING"
    # 保存後のloadで既に稼働中なら、新しい型付き設定を確実に反映する。
    return sd.restart(name) if active and changed else sd.start(name)


def stop_instance() -> tuple[bool, str]:
    from app.applications import systemd as sd

    return sd.stop(unit_name())


async def health() -> dict:
    """llama-server の /health を叩く。"""
    inst = get_config()["instance"]
    url = f"http://127.0.0.1:{inst.get('port', 8080)}/health"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(url)
        return {"ok": r.status_code == 200, "status_code": r.status_code}
    except httpx.HTTPError:
        return {"ok": False, "status_code": None}


async def detect_options() -> list[str]:
    """稼働バイナリの --help から利用可能なオプション（--xxx）を抽出する（UI 用）。

    実在しないオプションを UI に出さないため。取得失敗時は空。
    """
    import asyncio

    if not is_installed():
        return []
    try:
        env = {**os.environ, "LD_LIBRARY_PATH": str(_lib_dir())}
        proc = await asyncio.create_subprocess_exec(
            str(server_path()), "--help",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        flags = sorted(set(re.findall(r"(--[a-z][a-z0-9\-]+)", out.decode(errors="replace"))))
        return flags
    except Exception:
        return []
