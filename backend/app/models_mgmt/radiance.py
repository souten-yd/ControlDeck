"""Radiance (StillDeadcode/radiance): an inference server for AMD GPUs, as an opt-in add-on.

Engine: the official container image (Docker Hub `stilldeadcode/radiance`, which carries its own
ROCm), pulled at a pinned release tag and run with rootless podman. The source repository on
Codeberg was too slow to fetch from this network to build from. Install never downloads model
weights; models are `.rad` containers the user already has (or downloaded explicitly).

Models: registered like Strata's (alias, `.rad` path, port, context, KV type, ...). Each runs as
its own container in a systemd user unit, bound to 127.0.0.1 and reached through the ControlDeck
gateway. The model's folder is mounted read-only. Only fixed, validated arguments are passed;
nothing goes through a shell.
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

RUNTIME = "radiance"
IMAGE = "docker.io/stilldeadcode/radiance"
_TAGS = "https://hub.docker.com/v2/repositories/stilldeadcode/radiance/tags?page_size=50"
# Install takes the tested release; "update" moves to the newest release tag.
# 2.x reads its settings from a YAML file (`radiance --config FILE`) instead of flags.
PINNED_TAG = "2.0.0"
_TAG = re.compile(r"^v?([0-9]+)[.]([0-9]+)[.]([0-9]+)$")
_ALIAS = re.compile(r"^[a-z][a-z0-9.-]{0,47}$")
_MIN_PORT, _MAX_PORT = 8081, 65535
_CONTEXT_MIN, _CONTEXT_MAX = 4096, 1_048_576
# 2.x: bf16 | i8 | fp8. i8 halves a token's KV (K and V as int8, a scale per token and head);
# fp8 is GLM-5.3's latent cache only, so a Qwen container takes bf16 or i8.
KV_TYPES = ("bf16", "i8", "fp8")
# Qwen3.8-27B's training context. Longer windows need YaRN (rope-scaling), set automatically.
TRAINING_CONTEXT = 262_144
# Pinned host RAM for weights when ram_offload lets them leave the card.
OFFLOAD_HOST_POOL_MIB = 22_528
# Default thinking depth for requests that name none. "" keeps the template's own (xhigh on
# Qwen3.8-27B); "none" turns thinking off. Qwen's template takes low, medium and xhigh only, so a
# client asking for high or max (OpenCode, the GLM-style levels) is mapped to xhigh, not refused.
REASONING_EFFORTS = ("", "none", "low", "medium", "xhigh")
_EFFORT_MAP = "high=xhigh,max=xhigh"
LIFECYCLE_KEYS = ("on_demand_start", "idle_exclude", "auto_start")
# Requests ask for prompt + max_tokens; leave this much room like Maya's slack.
CONTEXT_SLACK = 8


class RadianceError(RuntimeError):
    pass


# ---- paths -------------------------------------------------------------------------------

def root() -> Path:
    return data_dir().resolve() / "runtimes" / "radiance"


def _marker() -> Path:
    return root() / "managed.json"


def _models_path() -> Path:
    return root() / "models.json"


def _log_path(alias: str) -> Path:
    return root() / "logs" / f"{alias}.log"


def _unit(alias: str) -> str:
    return cr.unit_name("radiance", alias)


def _container(alias: str) -> str:
    return cr.container_name("radiance", alias)


def _podman() -> str | None:
    return cr.podman()


def entrypoint() -> Path:
    """What runs the engine (the feature registry shows it): podman, the image is the engine."""
    return Path(_podman() or "/usr/bin/podman")


# ---- engine (image) ------------------------------------------------------------------------

_serialized = cr.serialized(RadianceError, "Radiance", lambda: root())


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
    try:
        request = urllib.request.Request(_TAGS, headers={"Accept": "application/json",
                                                         "User-Agent": "ControlDeck-Radiance"})
        with urllib.request.urlopen(request, timeout=20) as response:
            rows = json.load(response).get("results", [])
    except (OSError, ValueError, AttributeError) as exc:
        raise RadianceError("Radianceのリリースタグを取得できません") from exc
    tags = [str(row.get("name")) for row in rows
            if isinstance(row, dict) and _TAG.fullmatch(str(row.get("name")))]
    if not tags:
        raise RadianceError("Radianceのリリースタグが見つかりません")
    return max(tags, key=_version_key)


def _pull(tag: str) -> str:
    return cr.pull(_image_ref(tag), RadianceError, "Radiance")


def _write_marker(tag: str, digest: str) -> None:
    cr.write_marker(_marker(), IMAGE, tag, digest)


def _version() -> str:
    return str(_read_marker().get("tag") or "")


@_serialized
def _install() -> dict:
    if not available():
        raise RadianceError("Linux x86_64・podman・AMD GPU（/dev/kfd）が必要です")
    if installed():
        raise RadianceError("Radianceは導入済みです。更新操作を使ってください")
    root().mkdir(parents=True, exist_ok=True)
    _write_marker(PINNED_TAG, _pull(PINNED_TAG))
    return runtime_status()


async def install_stream(job=None) -> dict:
    if job:
        job.set_progress(f"Radiance {PINNED_TAG} の公式イメージを取得中（約210MB）。モデルはダウンロードしません", 0, 1)
    return await asyncio.to_thread(_install)


@_serialized
def _update() -> dict:
    if not installed():
        raise RadianceError("ControlDeckが導入したRadianceがありません")
    if any(_running(alias) for alias in _read()):
        raise RadianceError("Radianceのモデルを停止してから更新してください")
    tag = _latest_tag()
    previous = _version()
    if previous == tag:
        return runtime_status()
    # The new image is pulled beside the current one; the marker moves only after it arrived.
    _write_marker(tag, _pull(tag))
    if previous and previous != tag and _podman():
        cr.remove_image(_image_ref(previous))
    return runtime_status()


async def update_stream(job=None) -> dict:
    if job:
        job.set_progress("Radianceの最新リリースのイメージを取得中。モデルは再取得しません", 0, 1)
    return await asyncio.to_thread(_update)


async def available_update() -> dict:
    current = _version()
    try:
        latest = await asyncio.to_thread(_latest_tag)
        newer = bool(current and _version_key(latest) > _version_key(current))
        return {"installed_tag": current, "latest_tag": latest, "update_available": newer,
                "published_at": "", "error": ""}
    except RadianceError as exc:
        return {"installed_tag": current, "latest_tag": "", "update_available": False,
                "published_at": "", "error": str(exc)}


@_serialized
def uninstall() -> dict:
    if not installed():
        raise RadianceError("ControlDeck管理のRadianceがありません")
    if any(_running(alias) for alias in _read()):
        raise RadianceError("モデル稼働中は削除できません。停止してください")
    # The image only: registrations and .rad model files stay.
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
        "warning": "R9700（RDNA4）向け。公式イメージ（ROCm 入り）を podman で動かします。モデル（.rad）は別に用意します",
    }


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


def _validate_model_path(value: str) -> str:
    from app.files import service as files

    try:
        path = files.resolve(value)
    except (OSError, ValueError) as exc:
        raise RadianceError("モデルファイルを読み込めません。許可されたパスか確認してください") from exc
    if not path.is_file() or path.suffix.lower() != ".rad":
        raise RadianceError("Radianceのモデルは .rad ファイルを指定してください")
    return str(path)


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
        raise KeyError(f"Radianceモデル {alias} は未登録です")
    return _present(alias, cfg)


def save_instance(alias: str, *, model_path: str, port: int, context: int = 131072,
                  kv_cache: str = "bf16", max_num_seqs: int = 4, speculative_tokens: int | None = None,
                  gpu_headroom_mib: int = 1024, reasoning_effort: str = "", thinking_budget: int = 0,
                  memory_saver: bool = False, ram_offload: bool = False,
                  on_demand_start: bool = True,
                  idle_exclude: bool = False, auto_start: bool = False, replace: bool = False) -> dict:
    if not installed():
        raise RadianceError("先にRadianceを導入してください（設定 → オプション機能）")
    if not _ALIAS.fullmatch(alias) or alias in {"auto", RUNTIME}:
        raise RadianceError("別名は小文字英数字・ドット・ハイフン（先頭英字、最大48文字）にしてください")
    if not (_MIN_PORT <= int(port) <= _MAX_PORT) or not (_CONTEXT_MIN <= int(context) <= _CONTEXT_MAX):
        raise RadianceError("ポートまたはコンテキストがサポート範囲外です")
    if kv_cache not in KV_TYPES or not 1 <= int(max_num_seqs) <= 32:
        raise RadianceError("KV形式または同時実行数が不正です")
    if speculative_tokens is not None and not 0 <= int(speculative_tokens) <= 15:
        raise RadianceError("先読みトークン数は 0〜15 か自動にしてください")
    if not 96 <= int(gpu_headroom_mib) <= 16384:
        raise RadianceError("GPU の空き確保量は 96〜16384 MiB にしてください")
    if reasoning_effort not in REASONING_EFFORTS or not 0 <= int(thinking_budget) <= 262144:
        raise RadianceError("思考の深さまたは思考の上限が不正です")
    wanted = {"model_path": _validate_model_path(model_path), "port": int(port), "context": int(context),
              "kv_cache": kv_cache, "max_num_seqs": int(max_num_seqs),
              "speculative_tokens": None if speculative_tokens is None else int(speculative_tokens),
              "gpu_headroom_mib": int(gpu_headroom_mib), "reasoning_effort": reasoning_effort,
              "thinking_budget": int(thinking_budget),
              "memory_saver": bool(memory_saver), "ram_offload": bool(ram_offload),
              "on_demand_start": bool(on_demand_start),
              "idle_exclude": bool(idle_exclude), "auto_start": bool(auto_start)}
    with _registry_lock:
        cr.register(RUNTIME, alias, wanted, replace=replace, read=_read, write=_write, running=_running,
                    error=RadianceError, label="Radiance", lifecycle_keys=LIFECYCLE_KEYS)
    return get_instance(alias)


def delete_instance(alias: str) -> dict:
    with _registry_lock:
        return cr.unregister(alias, read=_read, write=_write, running=_running,
                             error=RadianceError, label="Radiance")


PREFIX_CACHE_MIB = 8192


def _config_dir() -> Path:
    return root() / "configs"


def config_yaml(alias: str, cfg: dict) -> str:
    """The 2.x settings file for one registration. Values are validated, so plain scalars suffice."""
    from app.models_mgmt.runtime_policy import vram_margin_mib

    headroom = max(int(cfg["gpu_headroom_mib"]), vram_margin_mib())
    model = Path(cfg["model_path"])
    lines = ["server:", "  host: 127.0.0.1", f"  port: {int(cfg['port'])}",
             "memory:", f"  gpu-headroom-mib: {headroom}",
             "models:", f"  {alias}:", f"    model: /models/{model.name}", "    tp: 1",
             f"    max-model-len: {int(cfg['context'])}", f"    max-num-seqs: {int(cfg['max_num_seqs'])}",
             f"    kv-cache-dtype: {cfg['kv_cache']}"]
    context = int(cfg["context"])
    if context > TRAINING_CONTEXT:
        # Past the training context the rotary keys need YaRN; the factor is just what is asked for.
        lines.append(f'    rope-scaling: "yarn:{context / TRAINING_CONTEXT:g}:{TRAINING_CONTEXT}"')
    if cfg.get("memory_saver"):
        # More VRAM for the KV cache: the delta-net state in f16 (half the state pool) and the
        # vision tower in pinned RAM, read once an image. Measured 2026-11-15: 6 seqs x i8 at
        # 327,680 fit all in VRAM with these (337,824 tokens of cache), at full speed.
        lines += ["    state-cache-dtype: f16", "    vision-tower-placement: ram"]
    if cfg.get("ram_offload"):
        # Weights may leave the card so one full-length sequence fits. Slow: 1.7 GiB off the card
        # cut decode from 205 to 50 tok/s, 5.8 GiB (512K) to 32 (2026-11-15).
        lines += ["    placement: layer_offload", f"    host-pool-mib: {OFFLOAD_HOST_POOL_MIB}",
                  f"    flex: {{kv: {{min-tokens: {context}}}}}"]
    # 会話の KV を VRAM から追い出すとき、ホストの RAM へ写して残す（次のターンで読み直さない）。既定は無し
    # （"kv tiers: off"）で、VRAM から外れた会話は最初から読み直していた。Radiance が動く間は Strata・Maya は止まり
    # RAM は空いている（2026-11-20 利用者「高速化の設定があれば入れて」）
    lines.append(f"    prefix-cache-mib: {PREFIX_CACHE_MIB}")
    if cfg.get("speculative_tokens") is not None:
        lines.append(f"    num-speculative-tokens: {int(cfg['speculative_tokens'])}")
    if cfg.get("reasoning_effort"):
        lines.append(f"    reasoning-effort: {cfg['reasoning_effort']}")
    if int(cfg.get("thinking_budget") or 0) > 0:
        lines.append(f"    thinking-token-budget: {int(cfg['thinking_budget'])}")
    lines.append(f'    reasoning-effort-map: "{_EFFORT_MAP}"')
    return "\n".join(lines) + "\n"


def write_config(alias: str, cfg: dict) -> Path:
    directory = _config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{alias}.yaml"
    temp = path.with_suffix(".tmp")
    temp.write_text(config_yaml(alias, cfg), encoding="utf-8")
    os.replace(temp, path)
    return path


def launch_args(alias: str, cfg: dict) -> list[str]:
    """The fixed command line for one registration (exposed for tests).

    The model's folder is mounted read-only at /models and its settings file at /config; the
    server binds 127.0.0.1 on the host network, so nothing is published beyond loopback.
    """
    model = Path(cfg["model_path"])
    config = _config_dir() / f"{alias}.yaml"
    return cr.gpu_run_args(
        entrypoint().as_posix(), _container(alias),
        [f"{model.parent}:/models:ro", f"{config}:/config/radiance.yaml:ro"],
        _image_ref(), ["--config", "/config/radiance.yaml"])


def _start(alias: str) -> tuple[bool, str]:
    cfg = _read().get(alias)
    if not cfg:
        return False, "モデル設定が見つかりません"
    if not installed():
        return False, "Radianceは未導入です"
    if _running(alias):
        return True, ""
    try:
        _validate_model_path(cfg["model_path"])
    except RadianceError as exc:
        return False, str(exc)
    write_config(alias, cfg)
    ok, message = cr.start_unit(_unit(alias), _log_path(alias), launch_args(alias, cfg), "Radiance")
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
        return False, "Radianceモデルが見つかりません"
    return cr.stop_unit(_unit(alias), _container(alias), "Radiance")


async def health(alias: str) -> dict:
    cfg = _read().get(alias)
    if not cfg or not await asyncio.to_thread(_running, alias):
        return {"ok": False, "status_code": None}
    return await cr.http_health(cfg["port"])


async def wait_ready(alias: str, *, timeout_seconds: int = 900) -> bool:
    return await cr.wait_ready(alias, health, _running, timeout_seconds=timeout_seconds)


async def ensure_ready(alias: str, *, timeout_seconds: int = 900, start: bool | None = None) -> bool:
    """Ready for a request; starts a registered model when "要求時に自動起動" is on."""
    return await cr.ensure_ready(RUNTIME, alias, config=_read().get, health=health, running=_running,
                                 start=start_instance, timeout_seconds=timeout_seconds, start_allowed=start)


async def prompt_room(alias: str, messages: list) -> tuple[int, int] | None:
    """(prompt tokens, room) via the server's tokenizer, so max_tokens can be fitted."""
    cfg = _read().get(alias)
    if not cfg:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            res = await client.post(f"http://127.0.0.1:{cfg['port']}/v1/chat/completions/input_tokens",
                                    json={"model": alias, "messages": messages})
            if res.status_code != 200:
                return None
            body = res.json()
            count = int(body.get("input_tokens", body.get("count")))
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None
    return count, int(cfg["context"]) - CONTEXT_SLACK


def residency_key(instance: dict) -> str:
    return cr.residency_key(RUNTIME, instance)


def mark_used_by_base_url(base_url: str) -> str | None:
    from urllib.parse import urlsplit
    from app.models_mgmt import large_model_lifecycle

    try:
        port = urlsplit(base_url).port
    except ValueError:
        return None
    alias = next((key for key, item in _read().items() if item.get("port") == port), None)
    if alias:
        large_model_lifecycle.mark_used(RUNTIME, alias)
    return alias


def log_tail(alias: str, lines: int = 60) -> str:
    return cr.log_tail(_log_path(alias), lines)
