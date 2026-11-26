"""Sly-Radiance (SlyBase/vllm-sly-radiance): vLLM tuned for one R9700, as an opt-in add-on.

Engine: the official container image (`ghcr.io/slybase/vllm-sly-radiance`, ROCm inside), pulled at
a pinned tag and run with rootless podman. Unlike Radiance (a YAML file), the image's entrypoint is
vLLM, so a registration becomes `vllm serve` arguments. Install never downloads model weights;
models are HF-format folders (safetensors + config.json) the user has or downloaded explicitly.

Each registration runs as its own container in a systemd user unit, bound to 127.0.0.1 and reached
through the ControlDeck gateway. The model and drafter folders are mounted read-only; the kernel
compile caches (the first start compiles for about 10 minutes) live under the data directory and
are kept. Only validated values reach the argument list; nothing goes through a shell.
Source of the flags: the project README (checked 2026-11-27).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import urllib.request
from pathlib import Path

import httpx

from app.config import data_dir
from app.models_mgmt import container_runtime as cr

RUNTIME = "sly-radiance"
IMAGE = "ghcr.io/slybase/vllm-sly-radiance"
_RELEASE = "https://api.github.com/repos/SlyBase/vllm-sly-radiance/releases/latest"
PINNED_TAG = "1.13.4-rocm10.1"
_TAG = re.compile(r"^([0-9]+)[.]([0-9]+)[.]([0-9]+)-rocm10[.]1$")
_RELEASE_TAG = re.compile(r"^v?([0-9]+[.][0-9]+[.][0-9]+)$")
_ALIAS = re.compile(r"^[a-z][a-z0-9.-]{0,47}$")
_FOLDER_NAME = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_MIN_PORT, _MAX_PORT = 8081, 65535
_CONTEXT_MIN, _CONTEXT_MAX = 4096, 262_144
KV_TYPES = ("fp8", "kv4")
MAX_SEQS = 8   # 8 x (7 + 1) verify tokens must stay inside RADIANCE_MXFP4_DECODE_MAX_M=128
CHUNKS = (2048, 4096, 8192)
REASONING_EFFORTS = ("", "none", "low", "medium", "high")
LIFECYCLE_KEYS = ("on_demand_start", "idle_exclude", "auto_start")
CONTEXT_SLACK = 8
_ENV_NAME = re.compile(r"^(RADIANCE|R4D)_[A-Z0-9_]{1,64}$")
_ENV_VALUE = re.compile(r"^[A-Za-z0-9._,:+-]{0,64}$")
_MAX_ENV = 32

# Per quantization: (extra vLLM flags, extra environment). "" is the default (compressed-tensors MXFP4).
QUANT_SWITCHES: dict[str, tuple[list[str], dict[str, str]]] = {
    "": ([], {}),
    "quark": (["--quantization", "quark"], {}),
    "nvfp4": (["--quantization", "compressed-tensors"], {"RADIANCE_NVFP4_MXFP4": "1"}),
    "paroquant": ([], {"RADIANCE_PAROQUANT": "1"}),
}
# The README's Quickstart environment (R9700 / gfx1201, W4A8 MXFP4 path).
DEFAULT_ENV = {
    "HIP_VISIBLE_DEVICES": "0", "GPU_MAX_HW_QUEUES": "2",
    "RADIANCE_MXFP4": "1", "RADIANCE_MXFP4_W4A8": "1", "RADIANCE_MXFP4_W4A8_MIN_M": "0",
    "RADIANCE_MXFP4_DECODE_MAX_M": "128", "RADIANCE_MXFP4_A_TILED_MIN_M": "513",
    "RADIANCE_MXFP4_WPERM": "1", "RADIANCE_LMHEAD_INT4": "1", "RADIANCE_FUSED_NORM_QUANT": "1",
    "RADIANCE_KV_GROUP_SIZE": "8", "RADIANCE_EMBED_INT8": "1", "RADIANCE_EMBED_BITS": "4",
    "RADIANCE_MXFP4_WIDE_MAX_M": "192", "RADIANCE_ADAPTIVE_WIDTH": "perseq",
}
_SAMPLING = '{"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0}'


class SlyRadianceError(RuntimeError):
    pass


# ---- paths -------------------------------------------------------------------------------

def root() -> Path:
    return data_dir().resolve() / "runtimes" / "sly-radiance"


def _marker() -> Path:
    return root() / "managed.json"


def _models_path() -> Path:
    return root() / "models.json"


def _log_path(alias: str) -> Path:
    return root() / "logs" / f"{alias}.log"


def _unit(alias: str) -> str:
    return cr.unit_name("sly-radiance", alias)


def _container(alias: str) -> str:
    return cr.container_name("sly-radiance", alias)


def entrypoint() -> Path:
    """What runs the engine (the feature registry shows it): podman, the image is the engine."""
    return Path(cr.podman() or "/usr/bin/podman")


# ---- engine (image) ------------------------------------------------------------------------

_serialized = cr.serialized(SlyRadianceError, "Sly-Radiance", lambda: root())


def available() -> bool:
    return cr.gpu_available()


def _read_marker() -> dict:
    return cr.read_marker(_marker())


def _image_ref(tag: str | None = None) -> str:
    return f"{IMAGE}:{tag or _read_marker().get('tag') or PINNED_TAG}"


def installed() -> bool:
    marker = _read_marker()
    return (_marker().is_file() and not _marker().is_symlink() and bool(marker.get("tag"))
            and cr.image_exists(_image_ref(str(marker["tag"]))))


def _version_key(tag: str) -> tuple[int, int, int]:
    return cr.version_key(_TAG, tag)


def _latest_tag() -> str:
    """The newest GitHub Release `vX.Y.Z` as the image tag `X.Y.Z-rocm10.1`."""
    try:
        request = urllib.request.Request(_RELEASE, headers={"Accept": "application/vnd.github+json",
                                                            "User-Agent": "ControlDeck-SlyRadiance"})
        with urllib.request.urlopen(request, timeout=20) as response:
            name = str(json.load(response).get("tag_name") or "")
    except (OSError, ValueError, AttributeError) as exc:
        raise SlyRadianceError("Sly-Radianceのリリースを取得できません") from exc
    match = _RELEASE_TAG.fullmatch(name)
    if not match:
        raise SlyRadianceError("Sly-Radianceのリリースタグが見つかりません")
    return f"{match[1]}-rocm10.1"


def _write_marker(tag: str, digest: str) -> None:
    cr.write_marker(_marker(), IMAGE, tag, digest)


def _version() -> str:
    return str(_read_marker().get("tag") or "")


@_serialized
def _install() -> dict:
    if not available():
        raise SlyRadianceError("Linux x86_64・podman・AMD GPU（/dev/kfd）が必要です")
    if installed():
        raise SlyRadianceError("Sly-Radianceは導入済みです。更新操作を使ってください")
    root().mkdir(parents=True, exist_ok=True)
    _write_marker(PINNED_TAG, cr.pull(_image_ref(PINNED_TAG), SlyRadianceError, "Sly-Radiance"))
    return runtime_status()


async def install_stream(job=None) -> dict:
    if job:
        job.set_progress(f"Sly-Radiance {PINNED_TAG} の公式イメージを取得中。モデルはダウンロードしません", 0, 1)
    return await asyncio.to_thread(_install)


@_serialized
def _update() -> dict:
    if not installed():
        raise SlyRadianceError("ControlDeckが導入したSly-Radianceがありません")
    if any(_running(alias) for alias in _read()):
        raise SlyRadianceError("Sly-Radianceのモデルを停止してから更新してください")
    tag = _latest_tag()
    previous = _version()
    if previous == tag:
        return runtime_status()
    # The new image is pulled beside the current one; the marker moves only after it arrived.
    _write_marker(tag, cr.pull(_image_ref(tag), SlyRadianceError, "Sly-Radiance"))
    if previous and previous != tag and cr.podman():
        cr.remove_image(_image_ref(previous))
    return runtime_status()


async def update_stream(job=None) -> dict:
    if job:
        job.set_progress("Sly-Radianceの最新リリースのイメージを取得中。モデルは再取得しません", 0, 1)
    return await asyncio.to_thread(_update)


async def available_update() -> dict:
    current = _version()
    try:
        latest = await asyncio.to_thread(_latest_tag)
        newer = bool(current and _version_key(latest) > _version_key(current))
        return {"installed_tag": current, "latest_tag": latest, "update_available": newer,
                "published_at": "", "error": ""}
    except SlyRadianceError as exc:
        return {"installed_tag": current, "latest_tag": "", "update_available": False,
                "published_at": "", "error": str(exc)}


@_serialized
def uninstall() -> dict:
    if not installed():
        raise SlyRadianceError("ControlDeck管理のSly-Radianceがありません")
    if any(_running(alias) for alias in _read()):
        raise SlyRadianceError("モデル稼働中は削除できません。停止してください")
    # The image only: registrations, model folders and the compile caches stay.
    cr.remove_image(_image_ref())
    _marker().unlink(missing_ok=True)
    return runtime_status()


def runtime_status() -> dict:
    state = installed()
    marker = _read_marker()
    return {
        "installed": state,
        "available": available() or state,
        "version": _version() if state else "",
        "binary": _image_ref() if state else "",
        "digest": str(marker.get("digest") or "") if state else "",
        "error": "" if available() else "Linux x86_64・podman・AMD GPU（/dev/kfd）が必要です",
        "warning": "R9700（RDNA4）向けの vLLM。公式イメージ（ROCm 入り）を podman で動かします。"
                   "初回の起動はカーネルの compile で約 10 分かかります。モデル（safetensors のフォルダ）は別に用意します",
    }


# ---- model folders -------------------------------------------------------------------------

def _resolve_folder(value: str, what: str) -> Path:
    from app.files import service as files

    try:
        path = files.resolve(value)
    except (OSError, ValueError) as exc:
        raise SlyRadianceError(f"{what}のフォルダを読み込めません。許可されたパスか確認してください") from exc
    if not path.is_dir() or not (path / "config.json").is_file():
        raise SlyRadianceError(f"{what}は config.json のある HF 形式のフォルダを指定してください")
    # The mount is "<dir>:/models/<name>:ro": a colon or comma would split it into other options.
    if not _FOLDER_NAME.fullmatch(path.name) or ":" in str(path) or "," in str(path):
        raise SlyRadianceError(f"{what}のフォルダ名・パスに使えない文字があります（英数字・.・_・- のみ）")
    return path


def inspect_folder(value: str) -> dict:
    """What the folder holds, and which quantization switch it needs (the registration screen shows this)."""
    path = _resolve_folder(value, "モデル")
    count, size = 0, 0
    for entry in path.glob("*.safetensors"):
        try:
            size += entry.stat().st_size
            count += 1
        except OSError:
            continue
    try:
        with (path / "config.json").open("rb") as handle:
            config = json.loads(handle.read(2_000_000).decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        config = {}
    quant = (config.get("quantization_config") or (config.get("text_config") or {}).get("quantization_config")
             or {}) if isinstance(config, dict) else {}
    quant = quant if isinstance(quant, dict) else {}
    method = str(quant.get("quant_method") or "")
    fmt = str(quant.get("format") or "")
    lowered = f"{method} {fmt}".lower()
    switch = ("quark" if method.lower() == "quark" else "nvfp4" if "nvfp4" in lowered
              else "paroquant" if "paro" in lowered else "")
    return {"path": str(path), "name": path.name, "safetensors_count": count, "total_bytes": size,
            "quant_method": method, "quant_format": fmt, "suggested_quantization": switch,
            "ready": count > 0}


# ---- model registrations -------------------------------------------------------------------

_registry_lock = threading.RLock()


def _read() -> dict[str, dict]:
    try:
        raw = json.loads(_models_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items()
            if isinstance(key, str) and _ALIAS.fullmatch(key) and isinstance(value, dict)}


def _write(items: dict[str, dict]) -> None:
    path = _models_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _running(alias: str) -> bool:
    return cr.unit_running(_unit(alias))


def _present(alias: str, cfg: dict) -> dict:
    loaded = _running(alias)
    return {
        "alias": alias, **cfg, "runtime": RUNTIME, "role": "llm",
        "base_url": f"http://127.0.0.1:{cfg['port']}/v1",
        "loaded": loaded, "runtime_status": "RUNNING" if loaded else "STOPPED",
        "ctx_size": int(cfg.get("context") or 0), "n_parallel": int(cfg.get("max_num_seqs") or 1),
    }


def list_instances() -> list[dict]:
    return [_present(alias, cfg) for alias, cfg in _read().items()]


def get_instance(alias: str) -> dict:
    cfg = _read().get(alias)
    if cfg is None:
        raise KeyError(f"Sly-Radianceモデル {alias} は未登録です")
    return _present(alias, cfg)


def _validate_engine_settings(value: dict | None) -> dict[str, str]:
    value = value or {}
    if not isinstance(value, dict) or len(value) > _MAX_ENV:
        raise SlyRadianceError(f"詳しい設定は {_MAX_ENV} 個までです")
    for key, item in value.items():
        if not isinstance(key, str) or not _ENV_NAME.fullmatch(key) or not isinstance(item, str) \
                or not _ENV_VALUE.fullmatch(item):
            raise SlyRadianceError("詳しい設定は RADIANCE_* / R4D_* の名前と、英数字・._,:+- の値だけです")
    return dict(value)


def save_instance(alias: str, *, model_path: str, port: int, drafter_path: str = "", context: int = 131072,
                  kv_cache: str = "fp8", max_num_seqs: int = 4, speculative_tokens: int = 7,
                  gpu_memory_utilization: float = 0.98, prefill_chunk: int = 2048,
                  prefix_cache: bool = True, reasoning_effort: str = "", quantization: str = "",
                  engine_settings: dict | None = None, on_demand_start: bool = True,
                  idle_exclude: bool = False, auto_start: bool = False, replace: bool = False) -> dict:
    if not installed():
        raise SlyRadianceError("先にSly-Radianceを導入してください（設定 → オプション機能）")
    if not _ALIAS.fullmatch(alias) or alias in {"auto", RUNTIME}:
        raise SlyRadianceError("別名は小文字英数字・ドット・ハイフン（先頭英字、最大48文字）にしてください")
    if not (_MIN_PORT <= int(port) <= _MAX_PORT) or not (_CONTEXT_MIN <= int(context) <= _CONTEXT_MAX):
        raise SlyRadianceError("ポートまたはコンテキストがサポート範囲外です")
    if kv_cache not in KV_TYPES or not 1 <= int(max_num_seqs) <= MAX_SEQS:
        raise SlyRadianceError(f"KV形式または同時に答える数（1〜{MAX_SEQS}）が不正です")
    if not 0 <= int(speculative_tokens) <= 15:
        raise SlyRadianceError("先読みトークン数は 0〜15 にしてください")
    if not 0.50 <= float(gpu_memory_utilization) <= 0.98:
        raise SlyRadianceError("GPU のメモリの使用率は 0.50〜0.98 にしてください")
    if int(prefill_chunk) not in CHUNKS or reasoning_effort not in REASONING_EFFORTS \
            or quantization not in QUANT_SWITCHES:
        raise SlyRadianceError("prefill の chunk・思考の既定・量子化の切り替えが不正です")
    wanted = {"model_path": str(_resolve_folder(model_path, "モデル")),
              "drafter_path": str(_resolve_folder(drafter_path, "先読みモデル")) if drafter_path.strip() else "",
              "port": int(port), "context": int(context), "kv_cache": kv_cache,
              "max_num_seqs": int(max_num_seqs), "speculative_tokens": int(speculative_tokens),
              "gpu_memory_utilization": round(float(gpu_memory_utilization), 2),
              "prefill_chunk": int(prefill_chunk), "prefix_cache": bool(prefix_cache),
              "reasoning_effort": reasoning_effort, "quantization": quantization,
              "engine_settings": _validate_engine_settings(engine_settings),
              "on_demand_start": bool(on_demand_start), "idle_exclude": bool(idle_exclude),
              "auto_start": bool(auto_start)}
    with _registry_lock:
        cr.register(RUNTIME, alias, wanted, replace=replace, read=_read, write=_write, running=_running,
                    error=SlyRadianceError, label="Sly-Radiance", lifecycle_keys=LIFECYCLE_KEYS)
    return get_instance(alias)


def delete_instance(alias: str) -> dict:
    with _registry_lock:
        return cr.unregister(alias, read=_read, write=_write, running=_running,
                             error=SlyRadianceError, label="Sly-Radiance")


# ---- launch ---------------------------------------------------------------------------------

def _cache_dir() -> Path:
    return root() / "cache"


def gpu_utilization(requested: float) -> float:
    """The requested share of the card, cut so the common VRAM margin stays free (runtime_policy.vram_margin_mib)."""
    from app.models_mgmt import strata_catalog
    from app.models_mgmt.runtime_policy import vram_margin_mib

    _ram, vram = strata_catalog.machine_gib()
    total_mib = (vram or 32.0) * 1024
    allowed = (total_mib - vram_margin_mib()) / total_mib
    return max(0.50, min(float(requested), int(allowed * 100) / 100))


def _capture_sizes(seqs: int, speculative: int) -> list[int]:
    """CUDA-graph batch sizes: multiples of 8 up to the verify tokens of all sequences (at most 128)."""
    top = min(128, max(8, -(-seqs * (speculative + 1) // 8) * 8))
    return list(range(8, top + 1, 8))


def environment(cfg: dict) -> dict[str, str]:
    env = {**DEFAULT_ENV, **QUANT_SWITCHES[str(cfg.get("quantization") or "")][1]}
    if cfg.get("kv_cache") == "kv4":
        env["KVCACHE_DTYPE"] = "kv4"   # TetraAttention, 4-bit KV (README: opt-in)
    env.update(cfg.get("engine_settings") or {})
    return env


def serve_args(alias: str, cfg: dict) -> list[str]:
    """The `vllm serve` arguments for one registration (an argument list; exposed for tests)."""
    model = Path(cfg["model_path"])
    seqs, spec = int(cfg["max_num_seqs"]), int(cfg["speculative_tokens"])
    drafter = Path(cfg["drafter_path"]) if cfg.get("drafter_path") and spec > 0 else None
    args = ["--model", f"/models/{model.name}", "--served-model-name", alias,
            "--host", "127.0.0.1", "--port", str(int(cfg["port"])),
            "--max-model-len", str(int(cfg["context"])),
            "--gpu-memory-utilization", f"{gpu_utilization(float(cfg['gpu_memory_utilization'])):g}",
            "--kv-cache-dtype", "fp8", "--mamba-ssm-cache-dtype", "bfloat16"]
    if drafter is not None:
        args += ["--speculative-config.method", "dflash",
                 "--speculative-config.model", f"/drafter/{drafter.name}",
                 "--speculative-config.num_speculative_tokens", str(spec),
                 "--speculative-config.draft_sample_method", "greedy",
                 "--speculative-config.attention_backend", "TRITON_ATTN"]
    args += ["--attention-backend", "R4D_HYBRID", "--max-num-seqs", str(seqs),
             "--max-num-batched-tokens", str(int(cfg["prefill_chunk"])),
             "--compilation-config.cudagraph_mode", "FULL_DECODE_ONLY",
             "--compilation-config.cudagraph_capture_sizes",
             json.dumps(_capture_sizes(seqs, spec if drafter is not None else 0), separators=(",", ":"))]
    if cfg.get("prefix_cache", True):
        args.append("--enable-prefix-caching")
    # Text only: the multimodal profile is skipped and images are refused.
    args += ["--skip-mm-profiling", "--enable-mm-embeds",
             "--limit-mm-per-prompt.image", "0", "--limit-mm-per-prompt.video", "0",
             "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_xml", "--reasoning-parser", "qwen3",
             "--chat-template", "/opt/qwen-fixed.jinja"]
    if cfg.get("reasoning_effort"):
        args += ["--default-chat-template-kwargs", json.dumps({"reasoning_effort": cfg["reasoning_effort"]})]
    args += ["--override-generation-config", _SAMPLING]
    return [*QUANT_SWITCHES[str(cfg.get("quantization") or "")][0], *args]


def launch_args(alias: str, cfg: dict) -> list[str]:
    """The full `podman run` command line (exposed for tests).

    Folders are mounted read-only; the server binds 127.0.0.1 on the host network, so nothing is
    published beyond loopback. The compile caches are the only writable mounts.
    """
    model = Path(cfg["model_path"])
    cache = _cache_dir()
    mounts = [f"{model}:/models/{model.name}:ro",
              f"{cache / 'vllm'}:/root/.cache/vllm", f"{cache / 'triton'}:/root/.triton",
              f"{cache / 'aiter'}:/root/.aiter"]
    if cfg.get("drafter_path") and int(cfg["speculative_tokens"]) > 0:
        drafter = Path(cfg["drafter_path"])
        mounts.append(f"{drafter}:/drafter/{drafter.name}:ro")
    return cr.gpu_run_args(entrypoint().as_posix(), _container(alias), mounts, _image_ref(),
                           serve_args(alias, cfg), env=environment(cfg), ipc_host=True)


def _start(alias: str) -> tuple[bool, str]:
    cfg = _read().get(alias)
    if not cfg:
        return False, "モデル設定が見つかりません"
    if not installed():
        return False, "Sly-Radianceは未導入です"
    if _running(alias):
        return True, ""
    try:
        _resolve_folder(cfg["model_path"], "モデル")
        if cfg.get("drafter_path"):
            _resolve_folder(cfg["drafter_path"], "先読みモデル")
        for name in ("vllm", "triton", "aiter"):
            (_cache_dir() / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    except (SlyRadianceError, OSError) as exc:
        return False, str(exc)
    ok, message = cr.start_unit(_unit(alias), _log_path(alias), launch_args(alias, cfg), "Sly-Radiance")
    if not ok:
        return False, message
    from app.models_mgmt import large_model_lifecycle
    large_model_lifecycle.mark_used(RUNTIME, alias)
    large_model_lifecycle.follow_selection(RUNTIME)
    return True, ""


def start_instance(alias: str) -> tuple[bool, str]:
    with _registry_lock:
        return _start(alias)


def stop_instance(alias: str) -> tuple[bool, str]:
    if alias not in _read():
        return False, "Sly-Radianceモデルが見つかりません"
    return cr.stop_unit(_unit(alias), _container(alias), "Sly-Radiance")


async def health(alias: str) -> dict:
    cfg = _read().get(alias)
    if not cfg or not await asyncio.to_thread(_running, alias):
        return {"ok": False, "status_code": None}
    return await cr.http_health(cfg["port"])


async def wait_ready(alias: str, *, timeout_seconds: int = 1500) -> bool:
    return await cr.wait_ready(alias, health, _running, timeout_seconds=timeout_seconds)


async def ensure_ready(alias: str, *, timeout_seconds: int = 1500, start: bool | None = None) -> bool:
    """Ready for a request. The first start compiles kernels (about 10 minutes), hence the long wait."""
    return await cr.ensure_ready(RUNTIME, alias, config=_read().get, health=health, running=_running,
                                 start=start_instance, timeout_seconds=timeout_seconds, start_allowed=start)


async def prompt_room(alias: str, messages: list) -> tuple[int, int] | None:
    """(prompt tokens, room) via vLLM's /tokenize, so max_tokens can be fitted (vLLM refuses an overlong request)."""
    cfg = _read().get(alias)
    if not cfg:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            res = await client.post(f"http://127.0.0.1:{cfg['port']}/tokenize",
                                    json={"model": alias, "messages": messages})
            if res.status_code != 200:
                return None
            count = int(res.json()["count"])
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
        return None
    return count, int(cfg["context"]) - CONTEXT_SLACK


def residency_key(instance: dict) -> str:
    return cr.residency_key(RUNTIME, instance)


def log_tail(alias: str, lines: int = 60) -> str:
    return cr.log_tail(_log_path(alias), lines)
