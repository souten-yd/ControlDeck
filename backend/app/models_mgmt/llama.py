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
import time
from collections import deque
from pathlib import Path

import httpx

from app.config import data_dir
from app.models_mgmt import gpu_release

logger = logging.getLogger("control_deck.llama")

RELEASE_REPO = "souten-yd/llama-builder"
# 導入経験のない環境向けの下限。実際の導入・更新は常に最新リリースを解決する
# （latest_tag()）。ここは GitHub へ届かないときの退避先でしかない。
DEFAULT_TAG = "llama-gpu-b10001"
# リリースタグの形。llama-builder は常に llama-gpu-<upstream tag> を発行する。
TAG_PREFIX = "llama-gpu-"
UNIT_PREFIX = "cdapp-llama"  # cdapp- 始まりで systemd ヘルパーの検証を満たす
# 版ディレクトリの保持数。直前の1版はロールバック先として残す。
RETAIN_VERSIONS = 2

# backend 種別 → リリース asset 名のマッチ規則（Linux のみ）
BACKEND_PATTERNS = {
    "vulkan": re.compile(r"linux.*vulkan.*\.tar\.gz$", re.I),
    "rocm": re.compile(r"linux.*rocm.*\.tar\.gz$", re.I),
    "cuda": re.compile(r"linux.*cuda.*\.tar\.gz$", re.I),
}
# llama.cpp としてユーザーに提示するバックエンド。CUDA(NVIDIA)は当面 Ollama を使う方針のため除外。
SELECTABLE_BACKENDS = ("rocm", "vulkan")
# ROCm ビルドは ROCm 10 系へ統一した（llama-builder の linux-rocm-r9700 は
# amdrocm-core-dev10.0-gfx1201 でビルドされる）。ホストの ROCm ユーザースペースが
# 別メジャーだと共有ライブラリを解決できないため、導入前に警告する材料として持つ。
ROCM_SERIES_MAJOR = 10
BACKEND_LABELS = {
    "rocm": f"ROCm {ROCM_SERIES_MAJOR}（AMD）",
    "vulkan": "Vulkan（汎用GPU）",
    "cuda": "CUDA（NVIDIA）",
}
ALIAS_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# 投機デコードの種別。正はバイナリの --spec-type（detect_spec_types() が読む）で、
# ここは保存値の検証に使う既知の上位集合。b10793 で dflash / dspark / eagle3 と
# ngram の各方式が増えた。未知の値を unit 引数へ通さないためのゲートでもある。
SPEC_TYPES = (
    "none",
    "draft-simple", "draft-eagle3", "draft-mtp", "draft-dflash", "draft-dspark",
    "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache",
)


def parse_spec_types(value: str) -> list[str]:
    """--spec-type の値を分解して検証する。カンマ区切りで複数指定できる。

    上流ドキュメントの通り「draft系 + draftless系」を混ぜられる。未知の値は弾く。
    """
    parts = [item.strip() for item in str(value or "none").split(",") if item.strip()]
    if not parts:
        return ["none"]
    unknown = [item for item in parts if item not in SPEC_TYPES]
    if unknown:
        raise ValueError(f"未知の投機デコード種別です: {', '.join(unknown)}")
    if "none" in parts and len(parts) > 1:
        raise ValueError("none は他の種別と同時に指定できません")
    return parts


# 別途ドラフトGGUFが要る方式。MTPはターゲット自身のMTP層を使い、ngram系はモデル不要。
SPEC_TYPES_NEEDING_DRAFT_MODEL = (
    "draft-simple", "draft-eagle3", "draft-dflash", "draft-dspark",
)
# ドラフト側KVキャッシュ型。空文字はターゲット側と同じ（引数を渡さない）。
DRAFT_CACHE_TYPES = ("", "f32", "f16", "bf16", "q8_0", "q4_0")

# モデルの読み込み方。b10793 で --mmap / --no-mmap / --mlock / --direct-io が
# まとめて deprecated になり --load-mode へ集約された（起動時に警告が出る）。
# 空文字は「指定しない」= バイナリ既定の auto。
LOAD_MODES = ("", "auto", "none", "mmap", "mlock", "mmap+mlock", "dio")
MAX_INSTANCES = 8
MAX_VISION_PROJECTORS = 32


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


DEFAULT_INSTANCE = {
        "model_path": "",
        # llm: チャット/生成 / embedding: /v1/embeddings 専用 / reranker: /v1/rerank 専用
        "role": "llm",
        # VLM用 multimodal projector（GGUF）。設定時のみ --mmproj を付ける
        "mmproj_path": "",
        "port": 8080,
        "n_gpu_layers": 999,   # 全層 GPU（VRAM 不足時は下げる）
        "ctx_size": 4096,
        # 0は通常CTXと同じ。異なる値の場合だけDeep Research開始前後に再ロードする。
        "deep_research_ctx_size": 0,
        # 最大同時リクエスト数（server slots）。kv_unified と併用すると
        # CTXを固定分割せず、共有プールから必要な分だけ取る。
        "n_parallel": 1,
        # 単一の共有KVバッファを全sequenceで使う。無効にすると各slotへ
        # ctx_size/n_parallel が固定割当てされ、1本で大きく使えなくなる。
        "kv_unified": True,
        "flash_attn": False,
        "n_predict": 2048,
        "batch_size": 2048,
        "ubatch_size": 512,
        "cache_type_k": "f16",
        "cache_type_v": "f16",
        "threads": -1,
        "threads_batch": -1,
        # 旧 --mmap / --mlock。--load-mode を持つバイナリでは load_mode が優先される。
        "mmap": True,
        "mlock": False,
        # --load-mode。空なら従来どおり mmap/mlock から組み立てる（古いバイナリ互換）。
        "load_mode": "",
        "spec_type": "none",
        # 先読み幅。大きいほど速いわけではなく、外した分の検証コストが効く。
        # Qwen3.8-27B + draft-mtp の実測（日本語/コード）では 8 以上で素の生成より
        # 遅くなり、2〜6 が良く 4 前後が頭打ち。バイナリ既定も 3。
        "draft_max": 4,
        # draft-simple / eagle3 / dflash / dspark で使うドラフトGGUF。
        # MTP と ngram 系は不要（前者はターゲットのMTP層、後者はモデルを使わない）。
        "spec_draft_model_path": "",
        # ドラフトをVRAMへ載せる層数。-1 は auto（引数を渡さずバイナリ既定に任せる）。
        "spec_draft_ngl": -1,
        # 先読みの下限と、採用に必要な最小確率。0 はバイナリ既定と同じ。
        "draft_min": 0,
        "draft_p_min": 0.0,
        # ドラフト側KVキャッシュ型。空ならターゲットと同じ扱い（引数を渡さない）。
        "spec_draft_cache_type_k": "",
        "spec_draft_cache_type_v": "",
        # 思考（reasoning）。auto/off/low/medium/high/xhigh/custom。
        # unit の引数になるため、変更の反映には再起動が要る。
        "think": "auto",
        "think_budget_tokens": 0,
        "cpu_moe": False,
        "n_cpu_moe": 0,
        "temperature": 0.8,
        "top_k": 40,
        "top_p": 0.95,
        "min_p": 0.05,
        "repeat_penalty": 1.0,
        "seed": -1,
        "alias": "llama",
        "auto_start": False,
        "idle_exclude": False,
        "last_used_at": "",
        # 所属エンドポイント。空なら読込時に port から解決して補完する。
        "endpoint_id": "",
        # 一覧の並び順＝優先度。1始まりで小さいほど優先。0は未設定（末尾扱い）。
        "order": 0,
}

# エンドポイント = 127.0.0.1 の待受ポート。複数モデルを束ね、常に1つだけ稼働させる。
DEFAULT_ENDPOINT = {"id": "", "label": "", "port": 8080, "active_alias": ""}
MAX_ENDPOINTS = 8

DEFAULT_CONFIG = {
    "tag": "",
    "backend": "",          # vulkan / rocm / cuda
    "sha256": "",
    "installed_at": "",
    # legacy互換mirror。正はinstances[selected_alias]。
    "instance": dict(DEFAULT_INSTANCE),
    "instances": {},
    "endpoints": {},
    "selected_alias": "",
}


def detect_vision_projectors(model_path: str) -> list[str]:
    """モデルと同じディレクトリにある mmproj GGUF を決定的に返す。

    呼び出し元は先に files.resolve() でモデルパスを許可ルート内へ正規化する。
    検出だけで VISION を有効化せず、登録画面で利用者が明示的に選ぶための候補にする。
    """
    model = Path(model_path)
    candidates: list[Path] = []
    try:
        for child in model.parent.iterdir():
            name = child.name.lower()
            if child == model or child.is_symlink() or not child.is_file():
                continue
            if child.suffix.lower() == ".gguf" and "mmproj" in name:
                resolved = child.resolve()
                if resolved.parent == model.parent:
                    candidates.append(resolved)
    except OSError:
        return []
    ordered = sorted(candidates, key=lambda item: (item.name.lower(), item.name))
    return [str(path) for path in ordered[:MAX_VISION_PROJECTORS]]


def _endpoint_id_for_port(port: int) -> str:
    return f"ep-{int(port)}"


def _migrate_endpoints(cfg: dict) -> None:
    """instance の port からエンドポイントを補完する（冪等）。

    移行前は port が instance 内で一意だったので 1:1 で無損失に投影できる。
    order 未設定のものは既存の並び順（JSON の挿入順）で 1..N を振る。
    """
    endpoints: dict = cfg["endpoints"]
    by_port = {int(e.get("port", 0)): eid for eid, e in endpoints.items()}
    for alias, instance in cfg["instances"].items():
        endpoint_id = str(instance.get("endpoint_id") or "")
        if endpoint_id and endpoint_id in endpoints:
            continue
        port = int(instance.get("port", 8080) or 8080)
        endpoint_id = by_port.get(port) or _endpoint_id_for_port(port)
        if endpoint_id not in endpoints:
            endpoints[endpoint_id] = {**DEFAULT_ENDPOINT, "id": endpoint_id,
                                      "label": f"ポート {port}", "port": port}
            by_port[port] = endpoint_id
        instance["endpoint_id"] = endpoint_id
    # port は endpoint を正とする派生値。呼び出し側の互換のため各 instance へ写す。
    for instance in cfg["instances"].values():
        endpoint = endpoints.get(str(instance.get("endpoint_id") or ""))
        if endpoint:
            instance["port"] = int(endpoint.get("port", 8080))
    unordered = [a for a, i in cfg["instances"].items() if not int(i.get("order") or 0)]
    if unordered:
        used = {int(i.get("order") or 0) for i in cfg["instances"].values()}
        next_order = 1
        for alias in unordered:
            while next_order in used:
                next_order += 1
            cfg["instances"][alias]["order"] = next_order
            used.add(next_order)


def list_endpoints() -> list[dict]:
    """エンドポイント一覧。所属モデルと稼働状況を添える。"""
    cfg = get_config()
    instances = list_instances()
    result = []
    for endpoint_id, endpoint in cfg["endpoints"].items():
        members = [i for i in instances if str(i.get("endpoint_id")) == endpoint_id]
        running = next((i for i in members if i.get("loaded")), None)
        result.append({
            **endpoint,
            "base_url": f"http://127.0.0.1:{endpoint.get('port', 8080)}/v1",
            "aliases": [str(i["alias"]) for i in members],
            "running_alias": str(running["alias"]) if running else "",
        })
    result.sort(key=lambda e: int(e.get("port", 0)))
    return result


def save_endpoint(endpoint_id: str, patch: dict) -> dict:
    """エンドポイントを作成／更新する。port は全エンドポイントで一意。"""
    if not ALIAS_RE.fullmatch(endpoint_id):
        raise ValueError("エンドポイントIDは英数字・._:-の1〜128文字で指定してください")
    cfg = get_config()
    exists = endpoint_id in cfg["endpoints"]
    if not exists and len(cfg["endpoints"]) >= MAX_ENDPOINTS:
        raise ValueError(f"エンドポイントは最大{MAX_ENDPOINTS}件です")
    endpoint = dict(cfg["endpoints"].get(endpoint_id, DEFAULT_ENDPOINT))
    endpoint.update({key: value for key, value in patch.items() if key in DEFAULT_ENDPOINT})
    endpoint["id"] = endpoint_id
    port = int(endpoint.get("port", 8080))
    if not 1024 <= port <= 65535:
        raise ValueError("ポートは1024〜65535で指定してください")
    for other_id, other in cfg["endpoints"].items():
        if other_id != endpoint_id and int(other.get("port", 0)) == port:
            raise ValueError(f"ポート {port} はエンドポイント '{other_id}' が使用中です")
    _ensure_port_free_for_other_runtimes(port)
    endpoint["label"] = str(endpoint.get("label") or f"ポート {port}")
    cfg["endpoints"][endpoint_id] = endpoint
    for instance in cfg["instances"].values():
        if str(instance.get("endpoint_id")) == endpoint_id:
            instance["port"] = port
    _write_config(cfg)
    return endpoint


def _ensure_port_free_for_other_runtimes(port: int) -> None:
    """llama.cpp 以外の管理対象と衝突していないか確認する。

    従来は llama instance 同士しか見ておらず、Ollama のポートを指定しても
    保存は通り、起動して初めて失敗していた。
    """
    from urllib.parse import urlsplit

    from app.models_mgmt import ollama

    try:
        ollama_port = urlsplit(ollama.base_url()).port
    except ValueError:
        ollama_port = None
    if ollama_port and int(ollama_port) == int(port):
        raise ValueError(f"ポート {port} は Ollama が使用しています")
    from app.models_mgmt import lucebox

    try:
        lucebox_ports = lucebox.endpoint_ports()
    except OSError:  # 設定ファイルが読めないだけで登録を止めない
        lucebox_ports = set()
    if int(port) in lucebox_ports:
        raise ValueError(f"ポート {port} は Lucebox が使用しています")


def delete_endpoint(endpoint_id: str) -> None:
    cfg = get_config()
    if endpoint_id not in cfg["endpoints"]:
        raise KeyError("エンドポイントが見つかりません")
    members = [a for a, i in cfg["instances"].items() if str(i.get("endpoint_id")) == endpoint_id]
    if members:
        raise ValueError(f"このエンドポイントには {len(members)} 件のモデルが紐づいています: {', '.join(members)}")
    cfg["endpoints"].pop(endpoint_id)
    _write_config(cfg)


def instances_on_endpoint(endpoint_id: str) -> list[dict]:
    """同一エンドポイントのモデルを優先度順で返す。"""
    return [i for i in list_instances() if str(i.get("endpoint_id")) == endpoint_id]


def instance_for_port(port: int) -> dict | None:
    """ポートから「今そのポートを代表しているモデル」を1件に決める。

    同一ポートを複数モデルで共有できるようにしたため、単純な先頭一致では
    誤ったモデルを掴む。稼働中 → 最後に起動したもの → 最優先、の順で解決する。
    list_instances() は優先度順に並んでいるので、最後は先頭を採ればよい。
    """
    members = [i for i in list_instances() if int(i.get("port", 0) or 0) == int(port)]
    if not members:
        return None
    running = next((i for i in members if i.get("loaded")), None)
    if running:
        return running
    if len(members) > 1:
        # 停止中で候補が複数あるときだけ、最後に起動したモデルを手掛かりにする。
        endpoint_id = str(members[0].get("endpoint_id") or "")
        active = str(get_config()["endpoints"].get(endpoint_id, {}).get("active_alias") or "")
        match = next((i for i in members if str(i.get("alias")) == active), None)
        if match:
            return match
    return members[0]


def resolve_instance_by_port(port: int) -> str | None:
    instance = instance_for_port(port)
    alias = str(instance.get("alias") or "") if instance else ""
    return alias or None


def endpoint_ports() -> set[int]:
    """管理下の待受ポート。エンドポイント未定義の設定でも instance 側から拾う。"""
    ports = {int(e.get("port", 0)) for e in get_config()["endpoints"].values()}
    ports |= {int(i.get("port", 0) or 0) for i in list_instances()}
    return {p for p in ports if p}


def get_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = _config_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text())
            cfg.update({k: v for k, v in saved.items() if k in cfg and k not in ("instance", "instances")})
            if isinstance(saved.get("instance"), dict):
                cfg["instance"].update({
                    key: value for key, value in saved["instance"].items()
                    if key in cfg["instance"]
                })
            if isinstance(saved.get("instances"), dict):
                for alias, raw in list(saved["instances"].items())[:MAX_INSTANCES]:
                    if not ALIAS_RE.fullmatch(str(alias)) or not isinstance(raw, dict):
                        continue
                    instance = dict(DEFAULT_INSTANCE)
                    instance.update({key: value for key, value in raw.items() if key in instance})
                    instance["alias"] = str(alias)
                    cfg["instances"][str(alias)] = instance
            if isinstance(saved.get("endpoints"), dict):
                for endpoint_id, raw in list(saved["endpoints"].items())[:MAX_ENDPOINTS]:
                    if not ALIAS_RE.fullmatch(str(endpoint_id)) or not isinstance(raw, dict):
                        continue
                    endpoint = dict(DEFAULT_ENDPOINT)
                    endpoint.update({key: value for key, value in raw.items() if key in endpoint})
                    endpoint["id"] = str(endpoint_id)
                    cfg["endpoints"][str(endpoint_id)] = endpoint
        except (json.JSONDecodeError, OSError):
            pass
    # 旧単一instanceを初回読込時にcatalogへ投影（ファイル保存は次の更新時）。
    if not cfg["instances"] and cfg["instance"].get("model_path"):
        alias = str(cfg["instance"].get("alias") or "llama")
        if not ALIAS_RE.fullmatch(alias):
            alias = "llama"
        cfg["instance"]["alias"] = alias
        cfg["instances"][alias] = dict(cfg["instance"])
    _migrate_endpoints(cfg)
    selected = str(cfg.get("selected_alias") or "")
    if selected not in cfg["instances"]:
        legacy_alias = str(cfg["instance"].get("alias") or "")
        selected = legacy_alias if legacy_alias in cfg["instances"] else next(iter(cfg["instances"]), "")
    cfg["selected_alias"] = selected
    if selected:
        cfg["instance"] = dict(cfg["instances"][selected])
    return cfg


def _write_config(cfg: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def save_config(patch: dict) -> dict:
    """旧単一instance API互換。instance patchは選択中catalogへ反映する。"""
    cfg = get_config()
    instance_patch: dict | None = None
    for k, v in patch.items():
        if k == "instance" and isinstance(v, dict):
            instance_patch = v
        elif k in cfg and k not in ("instances", "selected_alias"):
            cfg[k] = v
    _write_config(cfg)
    if instance_patch is not None:
        selected = cfg.get("selected_alias") or str(cfg["instance"].get("alias") or "llama")
        return save_instance(str(selected), instance_patch)
    return cfg


def list_instances() -> list[dict]:
    cfg = get_config()
    from app.applications import systemd as sd

    result = []
    for alias, instance in cfg["instances"].items():
        status = sd.query_status(unit_name(alias))
        # 旧単一unitで稼働中の設定は、catalogを初めて保存/起動するまで
        # 選択中instanceの状態として扱う。移行直後に「停止中」と誤表示しない。
        if alias == cfg.get("selected_alias") and status.get("status", "UNKNOWN") == "UNKNOWN":
            legacy = sd.query_status(f"{UNIT_PREFIX}.service")
            if legacy.get("status") in ("RUNNING", "STARTING", "FAILED"):
                status = legacy
        state = status.get("status", "UNKNOWN")
        # 起動に失敗して再試行待ちのループ（sub_state=auto-restart）は「起動中」ではない。
        # モデル読込中と区別できないと、UIが延々と「読み込み待ち」を出し続ける。
        if state == "STARTING" and status.get("sub_state") == "auto-restart":
            state = "FAILED"
        result.append({
            **instance,
            "alias": alias,
            "runtime": "llama.cpp",
            "selected": alias == cfg.get("selected_alias"),
            "unit": unit_name(alias),
            "loaded": state in ("RUNNING", "STARTING"),
            "runtime_status": state,
            # 失敗時だけログ末尾を読む（通常のポーリングでI/Oを増やさない）。
            "last_error": _log_tail(alias) if state == "FAILED" else "",
            "base_url": f"http://127.0.0.1:{instance.get('port', 8080)}/v1",
        })
    # 一覧の並び＝優先度。自動起動・オンデマンド起動・既定モデルの選択もこの順を使う。
    result.sort(key=lambda i: (int(i.get("order") or 10_000), str(i["alias"]).lower()))
    return result


def get_instance(alias: str | None = None) -> dict:
    cfg = get_config()
    selected = alias or cfg.get("selected_alias")
    if not selected or selected not in cfg["instances"]:
        # 未登録の旧初期状態だけlegacy mirrorを返す。
        if alias is None and cfg["instance"].get("model_path"):
            return dict(cfg["instance"])
        raise KeyError("llama.cppモデル設定が見つかりません")
    return dict(cfg["instances"][selected])


def save_instance(alias: str, patch: dict) -> dict:
    """alias単位で型付き設定を保存する。

    ポートは複数モデルで共有できる（同一エンドポイントに束ねる）。共有した場合は
    起動時に排他制御し、外部クライアントは同じendpointのままモデルだけ差し替わる。
    """
    if not ALIAS_RE.fullmatch(alias):
        raise ValueError("aliasは英数字・._:-の1〜128文字で指定してください")
    cfg = get_config()
    exists = alias in cfg["instances"]
    if not exists and len(cfg["instances"]) >= MAX_INSTANCES:
        raise ValueError(f"llama.cppモデル設定は最大{MAX_INSTANCES}件です")
    instance = dict(cfg["instances"].get(alias, DEFAULT_INSTANCE))
    instance.update({key: value for key, value in patch.items() if key in DEFAULT_INSTANCE})
    new_alias = str(instance.get("alias") or alias)
    if not ALIAS_RE.fullmatch(new_alias):
        raise ValueError("aliasは英数字・._:-の1〜128文字で指定してください")
    if new_alias != alias and new_alias in cfg["instances"]:
        raise ValueError(f"alias '{new_alias}' は登録済みです")

    # 所属エンドポイントを決める。port 直接指定は互換のため受け付け、
    # 該当エンドポイントが無ければ作る。
    endpoint_id = str(instance.get("endpoint_id") or "")
    if endpoint_id and endpoint_id not in cfg["endpoints"]:
        raise ValueError(f"エンドポイント '{endpoint_id}' が見つかりません")
    if not endpoint_id:
        port = int(instance.get("port", 8080) or 8080)
        endpoint_id = next(
            (eid for eid, e in cfg["endpoints"].items() if int(e.get("port", 0)) == port), "",
        )
        if not endpoint_id:
            if len(cfg["endpoints"]) >= MAX_ENDPOINTS:
                raise ValueError(f"エンドポイントは最大{MAX_ENDPOINTS}件です")
            if not 1024 <= port <= 65535:
                raise ValueError("ポートは1024〜65535で指定してください")
            _ensure_port_free_for_other_runtimes(port)
            endpoint_id = _endpoint_id_for_port(port)
            cfg["endpoints"][endpoint_id] = {**DEFAULT_ENDPOINT, "id": endpoint_id,
                                             "label": f"ポート {port}", "port": port}
    instance["endpoint_id"] = endpoint_id
    instance["port"] = int(cfg["endpoints"][endpoint_id].get("port", 8080))

    # 同じGGUFの重複登録は禁止しない。ポートが一意だった頃は「同じファイルで2つの
    # サーバーが立つ」ことを避ける意味があったが、エンドポイント内は排他起動になったため
    # 同時に動くことはない。同じGGUFを別CTX・別量子化設定で持って切り替えるのは
    # 複製機能の主目的なので、ここで弾くと用途を塞いでしまう。
    # 識別子としての一意性は alias で担保する。
    instance["alias"] = new_alias
    if not int(instance.get("order") or 0):
        used = {int(i.get("order") or 0) for a, i in cfg["instances"].items() if a != alias}
        order = 1
        while order in used:
            order += 1
        instance["order"] = order
    if new_alias != alias:
        if exists:
            stop_instance(alias)
            from app.applications import systemd as sd

            sd.remove_unit(unit_name(alias))
        cfg["instances"].pop(alias, None)
    cfg["instances"][new_alias] = instance
    # 既定チャット先は新規登録時と、まだ何も選ばれていない時だけ引き継ぐ。
    # 既存モデルの設定を保存しただけで選択が奪われると、利用中のモデルが黙って切り替わる。
    if not cfg.get("selected_alias") or (not exists and str(instance.get("role", "llm")) == "llm"):
        cfg["selected_alias"] = new_alias
        cfg["instance"] = dict(instance)
    elif cfg.get("selected_alias") == new_alias:
        cfg["instance"] = dict(instance)
    _write_config(cfg)
    _sync_endpoint_units(endpoint_id)
    _sync_agent_concurrency(new_alias, instance)
    return cfg


def _sync_agent_concurrency(alias: str, instance: dict) -> None:
    """スロット数を変えたら、OMo の背景タスク同時実行数も追従させる。

    片方だけ変えると、モデルの受け入れ枠とエージェントの投げる本数がずれる。
    OMo 未導入なら何もしない。
    """
    try:
        from app.features.registry import is_enabled

        if not is_enabled("omo"):
            return
        from app.integrations.opencode.provider import get_settings, sync_omo_concurrency

        if str(get_settings().get("model") or "") != alias:
            return  # OpenCode が使っていないモデルの変更は無関係
        sync_omo_concurrency(int(instance.get("n_parallel") or 1))
    except Exception:  # noqa: BLE001 - 追従の失敗でモデル保存を失敗にしない
        logger.exception("OMoの並列数同期に失敗しました")


def reorder_instances(aliases: list[str]) -> list[dict]:
    """一覧の並び＝優先度を設定する。指定漏れは後ろへ残す。"""
    cfg = get_config()
    unknown = [a for a in aliases if a not in cfg["instances"]]
    if unknown:
        raise KeyError(f"未知のモデルです: {', '.join(unknown)}")
    order = 1
    for alias in aliases:
        cfg["instances"][alias]["order"] = order
        order += 1
    for alias in cfg["instances"]:
        if alias not in aliases:
            cfg["instances"][alias]["order"] = order
            order += 1
    _write_config(cfg)
    for endpoint_id in cfg["endpoints"]:
        _sync_endpoint_units(endpoint_id)
    return list_instances()


def duplicate_instance(alias: str, new_alias: str, *, endpoint_id: str | None = None) -> dict:
    """設定を複製する。既定では同じエンドポイントに載せる（切替用途）。"""
    cfg = get_config()
    if alias not in cfg["instances"]:
        raise KeyError("llama.cppモデル設定が見つかりません")
    if new_alias in cfg["instances"]:
        raise ValueError(f"alias '{new_alias}' は登録済みです")
    source = dict(cfg["instances"][alias])
    source.update({
        "alias": new_alias,
        "auto_start": False,      # 複製がいきなり自動起動を奪わない
        "last_used_at": "",
        "order": 0,               # save_instance が末尾へ採番する
        "endpoint_id": endpoint_id or str(source.get("endpoint_id") or ""),
    })
    return save_instance(new_alias, source)


def select_instance(alias: str) -> dict:
    cfg = get_config()
    if alias not in cfg["instances"]:
        raise KeyError("llama.cppモデル設定が見つかりません")
    cfg["selected_alias"] = alias
    cfg["instance"] = dict(cfg["instances"][alias])
    _write_config(cfg)
    return cfg


def delete_instance(alias: str, *, delete_file: bool = False) -> dict:
    """設定を削除する。delete_file 指定時は GGUF 本体も消す。

    本体削除は取り消せないため、許可ルート内であることと、他のモデル設定から
    参照されていないことを確認してから行う。
    """
    cfg = get_config()
    if alias not in cfg["instances"]:
        raise KeyError("llama.cppモデル設定が見つかりません")
    model_path = str(cfg["instances"][alias].get("model_path") or "")
    still_used = [
        other for other, item in cfg["instances"].items()
        if other != alias and str(item.get("model_path") or "") == model_path
    ]
    stop_instance(alias)
    from app.applications import systemd as sd

    sd.remove_unit(unit_name(alias))
    endpoint_id = str(cfg["instances"][alias].get("endpoint_id") or "")
    cfg["instances"].pop(alias)
    if cfg.get("selected_alias") == alias:
        cfg["selected_alias"] = next(iter(cfg["instances"]), "")
    cfg["instance"] = dict(cfg["instances"].get(cfg["selected_alias"], DEFAULT_INSTANCE))
    _write_config(cfg)
    if endpoint_id:
        _sync_endpoint_units(endpoint_id)

    result = {"gguf_deleted": False, "reason": ""}
    if not delete_file:
        return result
    if not model_path:
        result["reason"] = "モデルファイルが設定されていません"
        return result
    if still_used:
        result["reason"] = f"他のモデル設定が同じファイルを参照しています: {', '.join(still_used)}"
        return result
    from app.files import service as files

    try:
        resolved = files.resolve(model_path)
    except (PermissionError, FileNotFoundError) as exc:
        result["reason"] = str(exc)
        return result
    try:
        resolved.unlink()
        result["gguf_deleted"] = True
    except OSError as exc:
        result["reason"] = f"削除できませんでした: {exc}"
    return result


def sync_instance_unit(alias: str) -> None:
    """1件のunitを書き出し、所属エンドポイント全体のauto-startを整える（起動はしない）。"""
    try:
        endpoint_id = str(get_instance(alias).get("endpoint_id") or "")
    except KeyError:
        return
    _sync_endpoint_units(endpoint_id or "")


def _sync_endpoint_units(endpoint_id: str) -> None:
    """エンドポイント内の全unitを書き出し、auto-start を最優先の1件だけに絞る。

    同じポートを複数モデルで共有できるため、auto_start が複数あっても
    boot 時に同時起動させるとポート競合で後発が落ちる。優先度（order）の
    最上位だけを enable し、他は明示的に disable する。
    """
    if not is_installed():
        return
    from app.applications import systemd as sd

    members = instances_on_endpoint(endpoint_id) if endpoint_id else []
    auto_start_winner = next(
        (str(i["alias"]) for i in members
         if i.get("auto_start") and Path(str(i.get("model_path") or "")).is_file()),
        None,
    )
    for instance in members:
        alias = str(instance["alias"])
        if not Path(str(instance.get("model_path") or "")).is_file():
            continue
        name = unit_name(alias)
        sd.write_unit(name, _unit_content(alias))
        sd.set_enabled(name, alias == auto_start_winner)


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


def current_tag() -> str:
    """current が指している版。未導入なら既定タグ。"""
    return str(get_config().get("tag") or "") or DEFAULT_TAG


def installed_tags() -> list[str]:
    """展開済みの版タグ（新しい順）。ロールバック候補になる。"""
    root = runtimes_dir()
    if not root.is_dir():
        return []
    tags = [child.name for child in root.iterdir()
            if child.is_dir() and not child.is_symlink() and child.name.startswith(TAG_PREFIX)]
    return sorted(tags, key=_tag_sort_key, reverse=True)


def _tag_sort_key(tag: str) -> tuple[int, str]:
    """llama-gpu-b10687 の数値部で比較する（b9544 < b10001 を正しく扱う）。"""
    match = re.search(r"b(\d+)", tag)
    return (int(match.group(1)) if match else 0, tag)


def installed_backends(tag: str = "") -> list[str]:
    """ダウンロード済み（展開済み）の backend 一覧。切り替え候補になる。"""
    resolved = tag or current_tag()
    out = []
    for b in BACKEND_PATTERNS:
        if _find_binary(_backend_root(b, resolved), "llama-server") is not None:
            out.append(b)
    return out


def host_rocm_version() -> str:
    """ホストの ROCm ユーザースペース版。取得できなければ空。"""
    for path in (Path("/opt/rocm/.info/version"), Path("/opt/rocm/.info/version-rocm")):
        try:
            value = path.read_text(encoding="ascii", errors="ignore").strip()
        except OSError:
            continue
        match = re.match(r"^(\d+(?:\.\d+)*)", value)
        if match:
            return match.group(1)
    return ""


def backend_warning(backend: str) -> str:
    """ROCm 10 統一に伴う、ホスト環境との不一致の説明（空なら問題なし）。"""
    if backend != "rocm":
        return ""
    version = host_rocm_version()
    if not version:
        return (f"ROCm ビルドは ROCm {ROCM_SERIES_MAJOR} 系へ統一されています。"
                "ホストの ROCm を検出できないため、起動時にライブラリ解決へ失敗する可能性があります")
    if int(version.split(".")[0]) != ROCM_SERIES_MAJOR:
        return (f"ROCm ビルドは ROCm {ROCM_SERIES_MAJOR} 系へ統一されています。"
                f"ホストの ROCm は {version} のため、ROCm {ROCM_SERIES_MAJOR} 系へ更新するか"
                " Vulkan バックエンドを使ってください")
    return ""


def switch_backend(backend: str, tag: str = "") -> dict:
    """導入済みの別 backend へ current を張り替える（再ダウンロード不要）。"""
    tag = tag or current_tag()
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
    instances = list_instances()
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
        "instances": instances,
        "selected_alias": cfg.get("selected_alias", ""),
        "base_url": f"http://127.0.0.1:{inst.get('port', 8080)}/v1" if is_installed() else None,
        "experimental": True,  # ビルド環境依存のため実験的
        "detected_backends": detected,       # {rocm/vulkan/cuda: bool}
        "installed_backends": installed,     # 導入済み（切り替え可能）
        "selectable_backends": selectable,   # UI に出す選択肢
        "backend_labels": dict(BACKEND_LABELS),
        "installed_tags": installed_tags(),   # ロールバック候補（新しい順）
        "rocm_series_major": ROCM_SERIES_MAJOR,
        "host_rocm_version": host_rocm_version(),
        "warning": backend_warning(str(cfg.get("backend") or "")),
    }


# ---- リリース asset ----


async def latest_tag() -> str:
    """llama-builder の最新リリースタグ。取得できなければ現行版へ落とす。"""
    try:
        release = await gpu_release.fetch_release(RELEASE_REPO)
    except gpu_release.ReleaseError:
        return current_tag()
    return release["tag"] or current_tag()


async def list_assets(tag: str = "") -> list[dict]:
    """リリースの Linux 向け asset（backend 判別付き）を返す。tag 未指定は最新。"""
    try:
        release = await gpu_release.fetch_release(RELEASE_REPO, tag=tag)
    except gpu_release.ReleaseError as exc:
        raise RuntimeError(str(exc)) from exc
    return [{**asset, "tag": release["tag"]} for asset in _classify_assets(release["assets"])]


def _classify_assets(assets: list[dict]) -> list[dict]:
    """asset 名から backend を判別して付与する（判別できないものは落とす）。"""
    classified = []
    for asset in assets:
        backend = next((b for b, pat in BACKEND_PATTERNS.items() if pat.search(asset["name"])), None)
        if backend is not None:
            classified.append({**asset, "backend": backend})
    return classified


def _pick_asset(assets: list[dict], backend: str) -> dict | None:
    return next((a for a in assets if a["backend"] == backend), None)


async def available_update() -> dict:
    """導入済みタグと最新リリースを突き合わせる。取得失敗は例外にしない。"""
    installed = str(get_config().get("tag") or "")
    result = {"installed_tag": installed, "latest_tag": "", "published_at": "",
              "update_available": False, "backends": installed_backends(), "error": ""}
    try:
        release = await gpu_release.fetch_release(RELEASE_REPO)
    except gpu_release.ReleaseError as exc:
        result["error"] = str(exc)
        return result
    result["latest_tag"] = release["tag"]
    result["published_at"] = release["published_at"]
    result["update_available"] = bool(
        installed and release["tag"] and _tag_sort_key(release["tag"]) > _tag_sort_key(installed)
    )
    return result


async def _fetch_backend(job, backend: str, tag: str, assets: list[dict],
                         checksums: dict[str, str]) -> dict:
    """1 backend 分をダウンロード・検証・展開する（current は張り替えない）。"""
    asset = _pick_asset(assets, backend)
    if asset is None:
        available = sorted({a["backend"] for a in assets})
        raise RuntimeError(f"{backend} 向けの Linux asset が見つかりません（利用可能: {available}）")
    dest_root = runtimes_dir() / tag / backend
    dest_root.mkdir(parents=True, exist_ok=True)
    archive = dest_root / asset["name"]
    expected = checksums.get(asset["name"], "")
    if job is not None:
        job.log(f"{tag} / {BACKEND_LABELS.get(backend, backend)}: {asset['name']}"
                f"（{asset['size'] // 1024 // 1024}MB{'・SHA256照合あり' if expected else ''}）")
    try:
        digest = await gpu_release.download_asset(
            job, asset, archive, expected_sha256=expected,
            label=f"ダウンロード中（{BACKEND_LABELS.get(backend, backend)}）",
        )
        if job is not None:
            job.set_progress(f"展開中（{BACKEND_LABELS.get(backend, backend)}）")
        extracted = gpu_release.extract_archive(archive, dest_root / "extracted")
    except gpu_release.ReleaseError as exc:
        shutil.rmtree(dest_root, ignore_errors=True)
        raise RuntimeError(str(exc)) from exc
    finally:
        archive.unlink(missing_ok=True)
    if _find_binary(extracted, "llama-server") is None:
        shutil.rmtree(dest_root, ignore_errors=True)
        raise RuntimeError("アーカイブ内に llama-server が見つかりません")
    return {"backend": backend, "tag": tag, "sha256": f"sha256:{digest}"}


async def install_stream(job, backend: str, tag: str = ""):
    """指定 backend の llama.cpp を導入する（ジョブ本体）。進捗を job に記録する。

    tag 未指定なら llama-builder の最新リリースを解決する。ROCm ビルドは
    ROCm 10 系へ統一されているため、ホストが別メジャーなら警告を残す。
    """
    resolved_tag = tag or await latest_tag()
    release = await gpu_release.fetch_release(RELEASE_REPO, tag=resolved_tag)
    assets = _classify_assets(release["assets"])
    checksums = await gpu_release.fetch_checksums(release["assets"])
    result = await _fetch_backend(job, backend, resolved_tag, assets, checksums)
    switch_backend(backend, resolved_tag)
    save_config({"sha256": result["sha256"], "installed_at": _now_iso()})
    gpu_release.invalidate_cache(RELEASE_REPO)
    warning = backend_warning(backend)
    if job is not None:
        job.log(f"導入完了: {BACKEND_LABELS.get(backend, backend)} 版 llama-server → {current_link()}")
        if warning:
            job.log(f"注意: {warning}")
    return {**result, "version": resolved_tag, "server": str(server_path()), "warning": warning}


async def update_stream(job, tag: str = ""):
    """導入済み backend をまとめて最新リリースへ更新する（ジョブ本体）。

    現在 current が指している backend は最後に張り替えるので、更新後も同じ
    バックエンドのまま最新版になる。取得・展開が終わってから current を
    差し替えるため、途中で失敗しても現行版はそのまま使える。
    """
    previous = str(get_config().get("tag") or "")
    active = str(get_config().get("backend") or "")
    targets = [b for b in installed_backends(previous) if b in SELECTABLE_BACKENDS] if previous else []
    if not targets:
        raise RuntimeError("Control Deck が導入した llama.cpp がありません")
    resolved_tag = tag or await latest_tag()
    if resolved_tag == previous:
        return {"backend": active, "tag": previous, "version": previous,
                "previous_version": previous, "updated": False,
                "backends": targets, "warning": backend_warning(active)}
    release = await gpu_release.fetch_release(RELEASE_REPO, tag=resolved_tag, use_cache=False)
    assets = _classify_assets(release["assets"])
    checksums = await gpu_release.fetch_checksums(release["assets"])
    results = []
    for backend in targets:
        results.append(await _fetch_backend(job, backend, resolved_tag, assets, checksums))
    # 起動中のモデルは古い版のバイナリを掴んだままなので、次回起動から新版になる。
    activate = active if active in targets else targets[0]
    switch_backend(activate, resolved_tag)
    digest = next((r["sha256"] for r in results if r["backend"] == activate), "")
    save_config({"sha256": digest, "installed_at": _now_iso()})
    # 重複したまま切ると保持数が目減りし、ロールバック先の直前版まで消える。
    ordered = dict.fromkeys([resolved_tag, *installed_tags()])
    keep = list(ordered)[:RETAIN_VERSIONS]
    removed = gpu_release.prune_versions(runtimes_dir(), [*keep, "current"])
    gpu_release.invalidate_cache(RELEASE_REPO)
    warning = backend_warning(activate)
    if job is not None:
        job.log(f"更新完了: {previous} → {resolved_tag}（{', '.join(targets)}）")
        if removed:
            job.log(f"古い版を削除しました: {', '.join(removed)}")
        if warning:
            job.log(f"注意: {warning}")
        job.log("稼働中のモデルは次回起動から新しいバイナリになります")
    return {"backend": activate, "tag": resolved_tag, "version": resolved_tag,
            "previous_version": previous, "updated": True, "backends": targets,
            "warning": warning, "pruned": removed}


def _find_binary(root: Path, name: str) -> Path | None:
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


# ---- インスタンス（systemd） ----


def unit_name(alias: str | None = None) -> str:
    if alias is None:
        alias = str(get_config().get("selected_alias") or "")
    if not alias:
        return f"{UNIT_PREFIX}.service"  # 未移行状態の互換名
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", alias).strip("-.")[:32] or "model"
    digest = hashlib.sha256(alias.encode("utf-8")).hexdigest()[:8]
    return f"{UNIT_PREFIX}-{safe}-{digest}.service"


def _unit_content(alias: str | None = None) -> str:
    from app.applications.systemd import _escape_exec_arg

    inst = get_instance(alias)
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
    # b10001 以降は --flash-attn が on|off|auto の値必須（旧フラグ形式はエラーで即終了する）
    args += ["--flash-attn", "on" if inst.get("flash_attn") else "off"]
    # 共有KV。無効時は各slotへ ctx_size/n_parallel を固定割当てする。
    args += ["--kv-unified" if inst.get("kv_unified", True) else "--no-kv-unified"]
    # 空き容量を観測して受け入れを制御するため常時有効化する（読み取り専用）。
    args += ["--metrics"]
    # --load-mode を持つバイナリでは deprecated な --mmap/--no-mmap/--mlock を使わない。
    # 明示指定が無ければ、既存の mmap/mlock 設定を等価な load-mode へ写す。
    load_mode = str(inst.get("load_mode") or "")
    if load_mode and load_mode not in LOAD_MODES:
        raise RuntimeError(f"未知の読み込みモードです: {load_mode}")
    if supports_load_mode():
        resolved = load_mode or _load_mode_from_legacy(inst)
        if resolved:
            args += ["--load-mode", resolved]
    else:
        if not inst.get("mmap", True):
            args += ["--no-mmap"]
        if inst.get("mlock"):
            args += ["--mlock"]
    role = str(inst.get("role", "llm"))
    if role == "llm" and inst.get("mmproj_path"):
        args += ["--mmproj", str(inst["mmproj_path"])]
    if role == "embedding":
        # 埋め込み専用（BGE-M3等）。/v1/embeddings を提供する
        args += ["--embedding", "--pooling", "mean"]
    elif role == "reranker":
        # 再ランク専用（Qwen3-Reranker等）。/v1/rerank を提供する
        args += ["--rerank"]
    if role == "llm":
        # 思考（reasoning）はモデル個別設定。auto は何も指定せずモデル既定に任せる。
        # b10001 は --reasoning on|off|auto と --reasoning-budget N（-1=無制限 / 0=即終了）を持つ。
        from app.models_mgmt import thinking

        think = thinking.spec(inst.get("think"), inst.get("think_budget_tokens"))
        if think.mode == "off":
            args += ["--reasoning", "off"]
        elif think.mode != "auto":
            args += ["--reasoning", "on", "--reasoning-budget", str(thinking.effective_budget(think))]
    spec_type = str(inst.get("spec_type", "none"))
    try:
        spec_parts = parse_spec_types(spec_type)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if spec_parts != ["none"] and role == "llm":
        # --draft-max は削除済み。後継は --spec-draft-n-max
        args += ["--spec-type", ",".join(spec_parts),
                 "--spec-draft-n-max", str(inst.get("draft_max", 16))]
        draft_model = str(inst.get("spec_draft_model_path") or "")
        needing = [item for item in spec_parts if item in SPEC_TYPES_NEEDING_DRAFT_MODEL]
        if needing:
            if not draft_model:
                raise RuntimeError(f"{', '.join(needing)} にはドラフトGGUFの指定が必要です")
            args += ["--spec-draft-model", draft_model]
            ngl = int(inst.get("spec_draft_ngl", -1))
            if ngl >= 0:  # -1 は auto。バイナリ既定に任せる
                args += ["--spec-draft-ngl", str(ngl)]
        if int(inst.get("draft_min", 0) or 0) > 0:
            args += ["--spec-draft-n-min", str(int(inst["draft_min"]))]
        if float(inst.get("draft_p_min", 0.0) or 0.0) > 0:
            args += ["--spec-draft-p-min", str(float(inst["draft_p_min"]))]
        for key, flag in (("spec_draft_cache_type_k", "--spec-draft-type-k"),
                          ("spec_draft_cache_type_v", "--spec-draft-type-v")):
            value = str(inst.get(key) or "")
            if value:
                args += [flag, value]
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
        f"Description=Control Deck llama.cpp server ({inst.get('alias', 'llama')})",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        # 共有ライブラリ（libllama-server-impl.so 等）はバイナリと同じ場所にある
        f'Environment="LD_LIBRARY_PATH={_lib_dir()}"',
    ]
    # ROCm既知バグ: HIPストリームが2本以上あるとアイドルでもGPU busy 100%・
    # 高消費電力が続く（ROCm/ROCm#2625）。spec-type等が追加ストリームを作ると発症する。
    # HWキューを1本に制限すると解消し、生成速度への影響は実測で無し。
    # HIP専用の環境変数のためVulkan等の他バックエンドでは無視される。全unitへ適用する。
    lines.append('Environment="GPU_MAX_HW_QUEUES=1"')
    for preflight in preflight_commands:
        lines.append("ExecStartPre=" + " ".join(_escape_exec_arg(a) for a in preflight))
    lines += [
        "ExecStart=" + " ".join(_escape_exec_arg(a) for a in args),
        "Restart=on-failure",
        "RestartSec=3",
        "TimeoutStopSec=20",
        "KillSignal=SIGTERM",
        f"StandardOutput=append:{log_dir}/{unit_name(str(inst.get('alias'))).removesuffix('.service')}.log",
        f"StandardError=append:{log_dir}/{unit_name(str(inst.get('alias'))).removesuffix('.service')}.log",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def _log_tail(alias: str, max_chars: int = 300) -> str:
    """instanceログ末尾の要点（起動失敗理由をUIへ返すため）。"""
    path = data_dir() / "logs" / f"{unit_name(alias).removesuffix('.service')}.log"
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    error_lines = [ln for ln in lines[-40:] if re.search(r"error|failed|abort", ln, re.I)]
    tail = error_lines[-2:] if error_lines else lines[-2:]
    return " / ".join(tail)[-max_chars:]


def start_instance(alias: str | None = None) -> tuple[bool, str]:
    from app.applications import systemd as sd

    if not is_installed():
        return False, "llama.cpp が未導入です"
    try:
        inst = get_instance(alias)
    except KeyError as exc:
        return False, str(exc)
    alias = str(inst.get("alias") or alias or "llama")
    if not Path(inst.get("model_path", "")).is_file():
        return False, "モデルファイルが存在しません"
    mark_used_by_base_url(f"http://127.0.0.1:{inst.get('port', 8080)}/v1")
    try:
        from app.models_mgmt.runtime_policy import ensure_gpu_profile

        ensure_gpu_profile(force=True)
    except RuntimeError as exc:
        return False, str(exc)
    name = unit_name(alias)
    content = _unit_content(alias)
    sd.write_unit(name, content)
    sd.reset_failed(name)
    sd.set_enabled(name, bool(inst.get("auto_start")))
    # 旧単一unitが残っている場合はport競合を避ける。catalog unit以外には触れない。
    if name != f"{UNIT_PREFIX}.service":
        sd.stop(f"{UNIT_PREFIX}.service")
    # 同一エンドポイント（＝同じポート）の他モデルを先に止める。
    # 止めずに起動すると後発が bind に失敗し、原因の分かりにくい起動失敗になる。
    endpoint_id = str(inst.get("endpoint_id") or "")
    if endpoint_id:
        for other in instances_on_endpoint(endpoint_id):
            other_alias = str(other["alias"])
            if other_alias != alias and other.get("loaded"):
                logger.info("エンドポイント %s のモデルを切り替えます: %s -> %s",
                            endpoint_id, other_alias, alias)
                sd.stop(unit_name(other_alias))
    # 稼働中なら必ず restart する。
    # unit ファイルとの差分で判定していたが、save_instance が先に unit を書き出すため
    # ここでは常に「変更なし」となり、start（稼働中は no-op）に落ちて設定が反映されなかった。
    # この関数は「設定を適用して起動する」意図で呼ばれるので、稼働中は作り直すのが正しい。
    # 単なる起動保証（ensure_ready）は health 済みなら手前で返るため、無駄な再起動にはならない。
    active = sd.query_status(name).get("status") in ("RUNNING", "STARTING")
    ok, err = sd.restart(name) if active else sd.start(name)
    if not ok:
        return ok, err
    # Type=simple は起動成功が即返るため、引数エラー等の即時クラッシュを検知できない。
    # 短時間監視し、クラッシュ（FAILED / 終了 / 自動再起動）を検出したらログ末尾を添えて
    # 失敗として返す。ExecStartPre 実行中（STARTING）は待つ。
    stable = 0
    for _ in range(10):
        time.sleep(1)
        state = sd.query_status(name)
        crashed = state.get("status") in ("FAILED", "STOPPED") or state.get("sub_state") == "auto-restart"
        if crashed:
            detail = _log_tail(alias)
            return False, "llama-server が起動直後に停止しました" + (f": {detail}" if detail else "")
        if state.get("status") == "RUNNING":
            stable += 1
            if stable >= 3:
                _set_active_alias(endpoint_id, alias)
                return True, ""
        else:
            stable = 0
    _set_active_alias(endpoint_id, alias)
    return True, ""


def _set_active_alias(endpoint_id: str, alias: str) -> None:
    """エンドポイントで最後に起動したモデルを記録する。

    停止後も「そのポートを代表するモデル」を決めるために使う
    （resolve_instance_by_port の 2 番目の手掛かり）。
    """
    if not endpoint_id:
        return
    cfg = get_config()
    if endpoint_id not in cfg["endpoints"]:
        return
    if cfg["endpoints"][endpoint_id].get("active_alias") == alias:
        return
    cfg["endpoints"][endpoint_id]["active_alias"] = alias
    _write_config(cfg)


def stop_instance(alias: str | None = None) -> tuple[bool, str]:
    from app.applications import systemd as sd

    selected = str(get_config().get("selected_alias") or "")
    resolved = str(alias or selected or "llama")
    instance = None
    was_loaded = False
    try:
        instance = get_instance(resolved)
        was_loaded = any(
            str(item.get("alias") or "llama") == resolved and bool(item.get("loaded"))
            for item in list_instances()
        )
    except (KeyError, OSError):
        pass
    current = sd.stop(unit_name(resolved))
    # catalog移行前の旧単一unitも、選択中モデルの停止操作に含める。
    if resolved == selected:
        legacy = sd.stop(f"{UNIT_PREFIX}.service")
        if legacy[0]:
            current = legacy
    if current[0] and was_loaded and instance is not None:
        try:
            from app.resources.broker import broker as resource_broker

            resource_broker.telemetry.record_unload(residency_key(instance))
        except Exception:  # noqa: BLE001 - telemetry must never block a stop
            logger.exception("llama unload telemetry recording failed")
    return current


async def health(alias: str | None = None) -> dict:
    """llama-server の /health を叩く。"""
    try:
        inst = get_instance(alias)
    except KeyError:
        return {"ok": False, "status_code": None}
    url = f"http://127.0.0.1:{inst.get('port', 8080)}/health"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(url)
        return {"ok": r.status_code == 200, "status_code": r.status_code}
    except httpx.HTTPError:
        return {"ok": False, "status_code": None}


def residency_key(instance: dict) -> str:
    """Stable non-path identity shared by load telemetry and resource leases."""
    identity = hashlib.sha256(
        str(instance.get("model_path") or instance.get("alias") or "llama").encode("utf-8")
    ).hexdigest()[:16]
    return f"llama:{identity}"


def find_role_instance(role: str) -> dict | None:
    """指定roleの最初のinstance設定を返す（未登録ならNone）。"""
    for item in list_instances():
        if str(item.get("role", "llm")) == role and item.get("model_path"):
            return item
    return None


async def ensure_ready(alias: str, *, timeout_seconds: int = 240) -> bool:
    """instanceを必要ならオンデマンド起動し、/health 200（モデル読込完了）まで待つ。

    チャット・埋め込み・再ランクの全経路で共通に使う（Ollamaの暗黙ロード相当）。
    """
    import asyncio

    try:
        inst = get_instance(alias)
        # 利用時刻を記録し、使用中のembedding/rerankerがidle unloadで落ちないようにする
        mark_used_by_base_url(f"http://127.0.0.1:{inst.get('port', 8080)}/v1")
    except KeyError:
        return False
    if (await health(alias)).get("ok"):
        return True
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    listen_at: float | None = None
    start_task = asyncio.create_task(asyncio.to_thread(start_instance, alias))
    while not start_task.done():
        state = await health(alias)
        if listen_at is None and state.get("status_code") is not None:
            listen_at = loop.time()
        await asyncio.sleep(0.25)
    ok, error = await start_task
    if not ok:
        logger.warning("llama instance %s の自動起動に失敗: %s", alias, error)
        return False
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        state = await health(alias)
        now = loop.time()
        if listen_at is None and state.get("status_code") is not None:
            listen_at = now
        if state.get("ok"):
            # A server that becomes ready before a non-200 health observation has
            # an indistinguishable listen/load boundary. Keep the total measured
            # cost exact and conservatively assign it to process startup.
            observed_listen = listen_at if listen_at is not None else now
            try:
                from app.resources.broker import broker as resource_broker

                resource_broker.telemetry.record_load_measurement(
                    residency_key(inst),
                    process_start_sec=max(0.0, observed_listen - started_at),
                    model_load_sec=max(0.0, now - observed_listen),
                )
            except Exception:  # noqa: BLE001 - telemetry must never block LLM readiness
                logger.exception("llama cold-load telemetry recording failed")
            return True
        await asyncio.sleep(2)
    logger.warning("llama instance %s のモデル読込が時間内に完了しませんでした", alias)
    return False


async def ensure_ready_by_base_url(base_url: str, *, timeout_seconds: int = 240) -> bool:
    """base_urlのportが管理instanceならensure_readyする。対象外はTrue（素通し）。"""
    from urllib.parse import urlsplit

    try:
        port = urlsplit(base_url).port
    except ValueError:
        return True
    alias = resolve_instance_by_port(port) if port else None
    if alias is None:
        return True
    return await ensure_ready(alias, timeout_seconds=timeout_seconds)


async def ensure_role_ready(role: str, *, timeout_seconds: int = 240) -> str | None:
    """指定roleのinstanceを起動保証し、base_urlを返す（未登録/失敗はNone）。"""
    instance = find_role_instance(role)
    if instance is None:
        return None
    if await ensure_ready(str(instance["alias"]), timeout_seconds=timeout_seconds):
        return str(instance["base_url"])
    return None


def mark_used_by_base_url(base_url: str) -> str | None:
    """Control Deck経由の生成直前にinstanceの最終利用時刻を記録する。"""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(base_url)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    alias = resolve_instance_by_port(port)
    if alias is None:
        return None
    cfg = get_config()
    if alias not in cfg["instances"]:
        return None
    cfg["instances"][alias]["last_used_at"] = _now_iso()
    if cfg.get("selected_alias") == alias:
        cfg["instance"] = dict(cfg["instances"][alias])
    _write_config(cfg)
    return alias


def _has_connected_clients(port: int) -> bool:
    """そのportへ接続中の *外部* clientがあるか。

    OpenCodeのような外部clientはControl Deckを経由しないため`last_used_at`が
    更新されない。利用中のinstanceを止めてしまわないよう、実接続を直接見る。

    自分自身の接続は数えない。HTTP clientは応答後も接続を保持するので、
    数えると「1度でもチャットしたら二度と降ろせない」になる。実測: AI
    アシスタントで1往復した後、解放は常に clients_connected で拒否され、
    画像生成は insufficient_capacity で落ちていた。
    ControlDeck自身の要求は release_on_request が drain で待つので、
    ここで重ねて見る必要がない。見ているのは外から来ている人だけである。
    """
    try:
        import os

        import psutil

        mine = {os.getpid()}
        try:
            mine.update(child.pid for child in psutil.Process().children(recursive=True))
        except psutil.Error:
            pass
        # server 側の socket（laddr.port == port）の pid は常に llama 自身なので、
        # そこからは誰が繋いでいるか分からない。client 側（raddr.port == port）を
        # 見る。そちらの pid が接続元である。
        for connection in psutil.net_connections(kind="tcp"):
            if (connection.status == psutil.CONN_ESTABLISHED and connection.raddr
                    and connection.raddr.port == port and connection.pid not in mine):
                # pid が読めない接続は外部として扱う。読めないことを「自分の
                # ものだ」と解釈すると、他人が使っている model を降ろしてしまう。
                return True
    except (OSError, RuntimeError, PermissionError):
        return False
    except Exception:  # noqa: BLE001 - 監視の失敗でunloadを止めない
        return False
    return False


def _opencode_session_uses(port: int, *, window_seconds: float, require_attached: bool = False) -> bool:
    """OpenCodeのTUIが「実際に使われている」か。

    セッションが存在するだけで保持し続けると、放置したTUIのせいでinstanceが
    永久にアンロードされない。tmuxのpane活動時刻を見て、idle判定と同じ窓に
    入っているものだけを利用中とみなす。
    """
    try:
        from app.features.registry import is_enabled

        if not is_enabled("opencode"):
            return False
        from urllib.parse import urlsplit

        from app.integrations.opencode.provider import resolve_backend_port
        from app.terminals.manager import manager as terminals

        # ゲートウェイ経由だと base_url は ControlDeck のポートになるため、
        # 転送先まで解決した実ポートで判定する。直結時は従来どおり。
        if resolve_backend_port() != port:
            return False
        now = time.time()
        for session in terminals.list_sessions():
            if not session.get("alive", True):
                continue
            if "opencode" not in str(session.get("program") or "").lower():
                continue
            if require_attached and not session.get("attached"):
                continue
            activity = float(session.get("activity_at") or 0)
            if activity and now - activity <= window_seconds:
                return True
        return False
    except Exception:  # noqa: BLE001 - 監視の失敗でunloadを止めない
        return False


async def _revive_endpoint_for_opencode(window_seconds: float) -> None:
    """直結設定のOpenCode TUIが生きているのにendpointが落ちていたら起こし直す。

    直結だと停止中は「呼んでも応答がない」状態になるため、セッションがある間は起動を
    保つ。ゲートウェイ経由ならリクエスト時にオンデマンド起動されるので、使っていない
    間に起こし直さない（意図しないモデルのロードを増やさない）。
    """
    try:
        from app.features.registry import is_enabled

        if not is_enabled("opencode"):
            return
        from app.integrations.opencode.provider import get_settings, is_gateway_url, resolve_backend_port

        if is_gateway_url(str(get_settings().get("base_url") or "")):
            return
        port = resolve_backend_port()
        # 見ていない（detachされた）セッションのために勝手に起動しない。
        if not port or not _opencode_session_uses(int(port), window_seconds=window_seconds, require_attached=True):
            return
        if any(int(item.get("port") or 0) == int(port) and item.get("loaded") for item in list_instances()):
            return
        logger.info("OpenCodeセッションのためllama.cppを再起動します: port=%s", port)
        await ensure_ready_by_base_url(f"http://127.0.0.1:{int(port)}/v1")
    except Exception:  # noqa: BLE001 - 監視ループを落とさない
        logger.exception("OpenCode endpoint revive failed")


async def idle_unload_loop() -> None:
    """共通runtime policyに従い、Control Deck利用のないllama instanceを停止する。"""
    import asyncio
    from datetime import datetime

    while True:
        try:
            await asyncio.sleep(60)
            from app.models_mgmt.runtime_policy import get_policy

            policy = get_policy()
            if not policy.idle_unload_enabled:
                continue
            deadline = time.time() - policy.idle_unload_minutes * 60
            for item in list_instances():
                if not item.get("loaded") or item.get("idle_exclude"):
                    continue
                raw = str(item.get("last_used_at") or "")
                try:
                    last = datetime.fromisoformat(raw).timestamp()
                except ValueError:
                    # 利用時刻不明の既存unitを勝手に止めない。
                    continue
                if last >= deadline:
                    continue
                port = int(item.get("port") or 0)
                # Control Deck経由でない利用（OpenCode等）を止めない。使用中なら時計を進める。
                if port and (
                    await asyncio.to_thread(_has_connected_clients, port)
                    or await asyncio.to_thread(
                        _opencode_session_uses, port, window_seconds=policy.idle_unload_minutes * 60,
                    )
                ):
                    mark_used_by_base_url(f"http://127.0.0.1:{port}/v1")
                    continue
                await asyncio.to_thread(stop_instance, str(item["alias"]))
                logger.info("idle llama.cpp instance unloaded: %s", item["alias"])
            await _revive_endpoint_for_opencode(policy.idle_unload_minutes * 60)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llama idle unload loop error")


# --help の出力は版ごとに固定。起動のたびに読み直さず、実体のパスで memo 化する。
_HELP_CACHE: dict[str, str] = {}


def _help_text_sync() -> str:
    """--help を同期で読む（unit生成など同期経路用）。実体パスで memo 化する。"""
    import subprocess

    if not is_installed():
        return ""
    key = str(server_path().resolve())
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    try:
        result = subprocess.run(
            [str(server_path()), "--help"], capture_output=True, timeout=15, check=False,
            env={**os.environ, "LD_LIBRARY_PATH": str(_lib_dir())},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (result.stdout or b"").decode(errors="replace")
    _HELP_CACHE.clear()  # 保持するのは現行版だけ
    _HELP_CACHE[key] = text
    return text


async def _help_text() -> str:
    """稼働バイナリの --help を返す（取得失敗時は空）。"""
    import asyncio

    if not is_installed():
        return ""
    key = str(server_path().resolve())
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    try:
        env = {**os.environ, "LD_LIBRARY_PATH": str(_lib_dir())}
        proc = await asyncio.create_subprocess_exec(
            str(server_path()), "--help",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception:
        return ""
    text = out.decode(errors="replace")
    _HELP_CACHE.clear()  # 版が変わったら捨てる（保持するのは現行版だけ）
    _HELP_CACHE[key] = text
    return text


async def detect_options() -> list[str]:
    """稼働バイナリの --help から利用可能なオプション（--xxx）を抽出する（UI 用）。

    実在しないオプションを UI に出さないため。取得失敗時は空。
    """
    text = await _help_text()
    return sorted(set(re.findall(r"(--[a-z][a-z0-9\-]+)", text))) if text else []


def _load_mode_from_legacy(instance: dict) -> str:
    """旧 mmap/mlock 設定を --load-mode の値へ写す。既定どおりなら空（指定しない）。"""
    mmap_on = bool(instance.get("mmap", True))
    mlock_on = bool(instance.get("mlock", False))
    if mmap_on and mlock_on:
        return "mmap+mlock"
    if mlock_on:
        return "mlock"
    if not mmap_on:
        return "none"
    return ""  # mmap のみ = バイナリ既定の auto と同じ


def supports_load_mode() -> bool:
    """稼働バイナリが --load-mode を持つか。

    unit 生成（同期）から呼ぶため、実体パスで memo 化した --help を同期で読む。
    版ごとに1回だけプロセスを起こす。読めなければ「持たない」に倒し、
    従来の --mmap / --mlock を使う（古いバイナリでも起動できる側へ倒す）。
    """
    return "--load-mode" in _help_text_sync()


async def detect_spec_types() -> list[str]:
    """稼働バイナリが受け付ける --spec-type の値を返す。

    種別は版ごとに増える（b10793 で dflash / dspark / eagle3 等が追加された）。
    UI へはバイナリが実際に持つものだけを出し、こちらのハードコードで古くならない
    ようにする。読めなければ既知の上位集合へ落とす。
    """
    flags_text = await _help_text()
    if not flags_text:
        return list(SPEC_TYPES)
    match = re.search(r"--spec-type\s+([a-z0-9,\-]+)", flags_text)
    if not match:
        return list(SPEC_TYPES)
    found = [value for value in match.group(1).split(",") if value in SPEC_TYPES]
    return found or list(SPEC_TYPES)


# ---- 空き容量（KVプール）の観測と受け入れ制御 ----

# 実測: ctx_size の 97% まで詰めると "Context size has been exceeded" で
# 実行中のリクエストごと失敗する。74% は問題なく通った。
# 枯渇は待機ではなく即エラーなので、安全側の余白を既定で確保する。
KV_HEADROOM_RATIO = 0.85


# エンドポイント別の (観測時刻, 生成トークン累計) の履歴。
# llama.cpp の predicted_tokens_seconds は「直近に完了した1リクエストの速度」で、
# 並列で回したときの全体量が見えないうえ、完了までずっと0のままになる。
# 累計の差分から全slot合算のスループットを出し、並列化の効果を見えるようにする。
_THROUGHPUT_SAMPLES: dict[int, deque[tuple[float, float]]] = {}
# 直前の1点だけを基準にすると、画面を複数開いてポーリング間隔が詰まったときに
# 「差分が短すぎて計算できない」が続く。一定の窓で最古の点と比べる。
THROUGHPUT_WINDOW_SECONDS = 8.0
THROUGHPUT_MIN_INTERVAL_SECONDS = 2.0


def _throughput(port: int, tokens_total: float) -> float:
    """生成トークン累計の差分から、全slot合算の tok/s を出す。

    呼び出し間隔に依存しないよう、窓の中で最も古い観測点と比較する。
    """
    import time

    now = time.monotonic()
    samples = _THROUGHPUT_SAMPLES.setdefault(int(port), deque(maxlen=64))
    # 大きく巻き戻ったらサーバー再起動とみなして基準を捨てる。
    if samples and tokens_total < samples[-1][1] * 0.5:
        samples.clear()
    # 窓から出た点は捨てる。長く見に来なかった後は一度空になり、次の観測から再開する
    # （空白期間をまたいで平均すると、実際に出ている速度より低く見えてしまう）。
    while samples and now - samples[0][0] > THROUGHPUT_WINDOW_SECONDS:
        samples.popleft()
    samples.append((now, tokens_total))
    if len(samples) < 2:
        return 0.0
    oldest_at, oldest_total = samples[0]
    elapsed = now - oldest_at
    if elapsed < THROUGHPUT_MIN_INTERVAL_SECONDS:
        return 0.0
    return max(0.0, (tokens_total - oldest_total) / elapsed)


async def endpoint_capacity(port: int) -> dict:
    """エンドポイントのKVプール使用状況。

    kv_unified では ctx_size が「全sequenceで共有するプール総量」になる。
    /slots の n_ctx は各slotの上限であって予約ではないため、
    使用量は稼働中slotの prompt + 生成済みトークンの合計で見る。
    """
    result = {
        "port": int(port), "available": False, "slots": 0, "busy": 0,
        "ctx_total": 0, "ctx_used": 0, "ctx_free": 0, "usable": 0,
        "deferred": 0, "accepting": False,
        "tokens_per_second": 0.0, "tokens_per_second_single": 0.0,
    }
    base = f"http://127.0.0.1:{int(port)}"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            slots_response = await client.get(f"{base}/slots")
            slots = slots_response.json()
            if not isinstance(slots, list) or not slots:
                return result
            used = 0
            busy = 0
            decoding = 0
            for slot in slots:
                if not slot.get("is_processing"):
                    continue
                busy += 1
                decoded = 0
                nxt = slot.get("next_token")
                if isinstance(nxt, list) and nxt:
                    decoded = int(nxt[0].get("n_decoded") or 0)
                decoding += decoded
                used += int(slot.get("n_prompt_tokens") or 0) + decoded
            total = int(slots[0].get("n_ctx") or 0)
            usable = int(total * KV_HEADROOM_RATIO)
            result.update({
                "available": True, "slots": len(slots), "busy": busy,
                "ctx_total": total, "ctx_used": used,
                "ctx_free": max(0, usable - used), "usable": usable,
                "accepting": busy < len(slots) and used < usable,
            })
            try:
                metrics = (await client.get(f"{base}/metrics")).text
                values: dict[str, float] = {}
                for line in metrics.splitlines():
                    if not line.startswith("llamacpp:"):
                        continue
                    key, _, raw = line.partition(" ")
                    try:
                        values[key] = float(raw)
                    except ValueError:
                        continue
                result["deferred"] = int(values.get("llamacpp:requests_deferred", 0))
                # 合算と1本あたりを分けて出す。並列にすると1本は遅くなるが合算は伸びる、
                # という並列化の効き方をそのまま見せる。
                # tokens_predicted_total はリクエスト完了時にしか増えないので、
                # 生成中のslotが持つ n_decoded を足して進行分まで含めた累計にする。
                rate = _throughput(port, values.get("llamacpp:tokens_predicted_total", 0.0) + decoding)
                result["tokens_per_second"] = round(rate, 1)
                # 1本あたりは合算を同時実行数で割る。llama.cpp の
                # predicted_tokens_seconds は完了するまで0のままで、生成中に見えない。
                result["tokens_per_second_single"] = round(rate / busy, 1) if busy else 0.0
            except (httpx.HTTPError, ValueError, IndexError):
                pass
    except (httpx.HTTPError, ValueError, TypeError):
        return result
    return result


async def await_capacity(port: int, needed_tokens: int = 0, *, timeout_seconds: float = 120) -> dict:
    """KVプールに空きが出るまで待つ。

    llama.cpp はプールが尽きると待たずに 500 を返し、しかも**実行中の他の
    リクエストごと**失敗させる。投げる前に空くのを待つ方が、全体の失敗を防げる。
    slot が空いていても総量が足りなければ待つ。
    """
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last = await endpoint_capacity(port)
    if not last.get("available"):
        return last  # 管理外/停止中は素通し（呼び出し側が従来どおり扱う）
    while asyncio.get_event_loop().time() < deadline:
        if last["busy"] == 0:
            return last  # 誰も使っていないなら待つ意味がない
        if last["accepting"] and last["ctx_free"] >= needed_tokens:
            return last
        await asyncio.sleep(1)
        last = await endpoint_capacity(port)
        if not last.get("available"):
            return last
    logger.warning("KVプールの空き待ちがtimeoutしました: port=%s needed=%s", port, needed_tokens)
    return last


def release_reason(item: dict, *, include_helpers: bool = False) -> str:
    """Why an explicit release must be refused, or "" when it may proceed.

    An explicit release is not the idle unload, and must not reuse the idle
    unload's 30-minute "somebody used this recently" window.

    The idle loop asks "did anyone touch this lately?" because nobody asked for
    the memory, so the safe answer is to keep the model warm. An explicit
    release is the opposite situation: a consumer needs that memory *now* for
    its own work. On a single GPU, honouring a 30-minute recency window would
    mean that while an OpenCode session is open no add-on can ever obtain the
    GPU, and the capability would be dead in exactly the case it exists for.
    Measured on this machine: the resident model holds 31,495,229,440 bytes of
    VRAM, and coming back costs 10.038 s through ensure_ready.

    What still refuses:
      the caller drains in-flight gateway requests first, so no running
      inference is ever cut (that guard lives in the resource provider)
      a live TCP connection means somebody is mid-stream right now
      idle_exclude is the operator's explicit "never release this"
      non-llm roles (embedding / reranker), unless include_helpers says the
      caller has already found that releasing the LLMs was not enough. Keeping
      a 1.16GB helper resident must not be the reason a 33.35GB workload
      cannot run on a 34.2GB card.
    """
    role = str(item.get("role", "llm"))
    if role != "llm" and not (include_helpers and role in {"embedding", "reranker"}):
        return "not_an_llm_instance"
    if item.get("idle_exclude"):
        return "idle_excluded"
    port = int(item.get("port") or 0)
    if not port:
        return "unknown_port"
    if _has_connected_clients(port):
        return "clients_connected"
    return ""


async def release_loaded_llms(*, include_helpers: bool = False) -> tuple[bool, str, int]:
    """Unload every llm instance that nobody is using right now.

    ``include_helpers`` extends this to the embedding and reranker instances.
    They are excluded by default because they are small and RAG leans on them
    constantly, so evicting them for every turn would be pure churn. But small
    is not free: a 1.16GB embedding model resident on a 34.2GB card leaves
    33.05GB, and an image model measured at 33.35GB then cannot be admitted at
    all. Observed exactly that — bge-m3 loaded, FLUX refused with
    insufficient_capacity while the GPU looked idle. Keeping a helper resident
    must not be the reason a real workload cannot run.

    In-flight requests are drained by the caller before this runs. Whatever is
    released comes back on demand through ensure_ready, so a consumer that
    needs the model again pays a reload rather than losing its work.

    The returned byte count is the size of the model files that were unloaded.
    Actual VRAM released is larger because the KV cache goes with it: measured
    16,464,440,224 bytes of model against 31,495,229,440 bytes of VRAM.
    """
    import asyncio

    wanted = {"llm", "embedding", "reranker"} if include_helpers else {"llm"}
    running = [item for item in list_instances() if item.get("loaded")]
    running = [item for item in running if str(item.get("role", "llm")) in wanted]
    if not running:
        return True, "already_released", 0

    for item in running:
        reason = release_reason(item, include_helpers=include_helpers)
        if reason:
            return False, reason, 0
    released_model_bytes = 0
    for item in running:
        try:
            released_model_bytes += Path(str(item.get("model_path") or "")).stat().st_size
        except OSError:
            pass
        ok, detail = await asyncio.to_thread(stop_instance, str(item.get("alias") or "llama"))
        if not ok:
            logger.warning("explicit llama release failed for %s: %s", item.get("alias"), detail)
            return False, "stop_failed", 0
    return True, "released", released_model_bytes
