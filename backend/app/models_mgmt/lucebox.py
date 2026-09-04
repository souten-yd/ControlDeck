"""Lucebox（AMDLucebox 配布物）ランタイム管理。

souten-yd/AMDLucebox は Radeon AI PRO R9700（gfx1201）向けに Lucebox の
`dflash_server` をビルドして配布する。DFlash 投機デコードで llama.cpp より
大幅に速い代わりに、ターゲット GGUF と対になるドラフトが要る点が異なる。

ControlDeck 側の扱いは llama.cpp と揃える:
- systemd ユーザーユニットで常駐（Web プロセスの子にしない）。
- OpenAI 互換エンドポイント（http://127.0.0.1:<port>/v1）として公開し、
  ゲートウェイ経由で内部チャットにも OpenCode にも同じ形で見せる。

トラックは ROCm 10 を既定にする（`DEFAULT_TRACK`）。ROCm 7.2.4 は公開実測との
比較用リファレンスで、ホストの ROCm ユーザースペースが 7 系のときの退避先。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from copy import deepcopy
from pathlib import Path

import httpx

from app.config import data_dir
from app.models_mgmt import gpu_release

logger = logging.getLogger("control_deck.lucebox")

RELEASE_REPO = "souten-yd/AMDLucebox"
UNIT_PREFIX = "cdapp-lucebox"  # cdapp- 始まりで systemd ヘルパーの検証を満たす
BINARY_NAME = "dflash_server"
# 対応 GPU。AMDLucebox は gfx1201 のみをコンパイルする（gfx1200 は R9700 と非互換）。
SUPPORTED_GFX = "gfx1201"
GFX_TARGET_VERSION = 120001  # /sys の gfx_target_version 表現（12.0.1）

# トラック → リリース asset のマッチ規則。ROCm メジャーで分ける。
TRACKS: dict[str, dict] = {
    "rocm10": {
        "label": "ROCm 10",
        "rocm_major": 10,
        "pattern": re.compile(r"^lucebox-r9700-rocm10[.\d]*-gfx1201-.*\.tar\.zst$", re.I),
        "summary": "本番候補。ホストの ROCm ユーザースペースも 10 系を前提にする",
    },
    "rocm7": {
        "label": "ROCm 7.2",
        "rocm_major": 7,
        "pattern": re.compile(r"^lucebox-r9700-rocm7[.\d]*-gfx1201-.*\.tar\.zst$", re.I),
        "summary": "公開実測との比較用リファレンス。ホストが ROCm 7 系のときの退避先",
    },
}
DEFAULT_TRACK = "rocm10"
# 版ディレクトリの保持数。ロールバック先として直前の1版だけ残す。
RETAIN_VERSIONS = 2
MAX_INSTANCES = 4
ALIAS_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

CACHE_TYPES = ("f16", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "tq3_0")
DRAFT_RESIDENCY = ("auto", "persistent", "request-scoped")

# AMDLucebox README の実測プロファイルをそのまま初期値にする。
# 利用者が数値を詰めなくても、公開されている 180+ tok/s の条件で動き始める。
DEFAULT_INSTANCE: dict = {
    "alias": "lucebox",
    "model_path": "",          # ターゲット GGUF
    "draft_path": "",          # DFlash2 ドラフト GGUF
    "port": 8216,
    "max_ctx": 131072,
    "draft_block_size": 16,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    # 0 = 全注意（dflash_server の既定）。>0 にすると長いコンテキストで
    # システムプロンプトとツール定義が注意から外れ、ツール呼び出しが壊れる
    # （--help に "Use 0 for tools." と明記。実測でも13,958トークン/22ツールで
    # 2048だと必須引数の欠けた呼び出しになった）。速度差は実測で無かった。
    "fa_window": 0,
    "ddtree": True,
    "ddtree_budget": 22,
    "default_max_tokens": 0,   # 0 はサーバー既定（モデルカード / 16000）に任せる
    "draft_residency": "auto",
    "fast_rollback": True,
    # 生成したツール呼び出しの先までprefixキャッシュを延ばす。これが無いと
    # prefix_lenが初回プロンプトで止まり、ターンが進むほど再prefillが増える
    # （実測: 20,081トークンの3ターン目で 12.2秒 → 7.7秒）。ツールを使わない
    # 用途では効かないだけなので既定でON。
    "agent_turn_cache": True,
    # DFlash2 の検証は厳密グリーディのみで、temperature>0 のリクエストは投機経路を
    # 使わず自己回帰へ落ちる（実測 142 tok/s → 29 tok/s）。既定では送信直前に
    # temperature を 0 へ固定して、Lucebox を選んだ意味が消えないようにする。
    # 出力は決定的になるので、多様性が要る用途では利用者が切れるようにしてある。
    "prefer_speculative": True,
    "auto_start": False,
    "idle_exclude": False,
    "last_used_at": "",
    # 一覧の並び順＝優先度。1始まりで小さいほど優先。0は未設定（末尾扱い）。
    "order": 0,
}

# 設定ファイルの移行済みマーカー。過去に既定として書き込まれた fa_window=2048 は
# 利用者が選んだ値ではなくこちら側の誤りなので、一度だけ 0 へ直す。
CONFIG_REVISION = 2

DEFAULT_CONFIG: dict = {
    "revision": 0,
    "tag": "",
    "track": "",
    "sha256": "",
    "installed_at": "",
    "upstream": "",            # 元 Lucebox のコミット（BUILD_INFO.json 由来）
    "binary_relpath": "",      # current からの相対パス
    "previous_tag": "",
    "instances": {},
    "selected_alias": "",
}


class LuceboxError(RuntimeError):
    """利用者向けメッセージを持つ Lucebox 操作エラー。"""


# ---- パス ----


def runtimes_dir() -> Path:
    return data_dir() / "runtimes" / "lucebox"


def current_link() -> Path:
    return runtimes_dir() / "current"


def _version_root(tag: str, track: str) -> Path:
    return runtimes_dir() / tag / track


def server_path() -> Path:
    """現在版の dflash_server の想定パス。"""
    relative = str(get_config().get("binary_relpath") or "")
    if relative:
        return current_link() / relative
    return current_link() / "server" / "build" / BINARY_NAME


def _library_dirs() -> list[Path]:
    """同梱共有ライブラリの探索先。バイナリと同じ場所 + 配布物の lib/。"""
    root = current_link()
    candidates = [server_path().parent, root / "lib", root / "server" / "build"]
    seen: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.append(path)
    return seen


def _config_path() -> Path:
    return data_dir() / "lucebox-runtime.json"


# ---- 設定 ----


def _normalize(cfg: dict) -> dict:
    # DEFAULT_CONFIG は module レベルの共有物。浅くマージすると instances が
    # 既定値そのものを指し、以後の保存が既定値を汚す（プロセス内の全設定が混ざる）。
    merged = {**deepcopy(DEFAULT_CONFIG), **{k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}}
    instances = merged.get("instances")
    merged["instances"] = dict(instances) if isinstance(instances, dict) else {}
    for alias, raw in list(merged["instances"].items()):
        if not isinstance(raw, dict):
            merged["instances"].pop(alias, None)
            continue
        merged["instances"][alias] = {**DEFAULT_INSTANCE, **raw, "alias": alias}
    unordered = [a for a, i in merged["instances"].items() if not int(i.get("order") or 0)]
    if unordered:
        used = {int(i.get("order") or 0) for i in merged["instances"].values()}
        next_order = 1
        for alias in unordered:
            while next_order in used:
                next_order += 1
            merged["instances"][alias]["order"] = next_order
            used.add(next_order)
    if int(merged.get("revision") or 0) < 2:
        # fa_window=2048 は ControlDeck 側が誤って書いた既定値。ツール利用を壊すので
        # 0 へ戻す。利用者が意図して選んだ他の値（1024 等）はそのまま残す。
        for instance in merged["instances"].values():
            if int(instance.get("fa_window", 0)) == 2048:
                instance["fa_window"] = 0
        merged["revision"] = CONFIG_REVISION
    selected = str(merged.get("selected_alias") or "")
    if selected not in merged["instances"]:
        merged["selected_alias"] = next(iter(merged["instances"]), "")
    return merged


def get_config() -> dict:
    path = _config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    cfg = _normalize(raw if isinstance(raw, dict) else {})
    # 移行は読み取りのたびに走らせず、一度だけ書き戻して確定させる。
    if isinstance(raw, dict) and raw and int(raw.get("revision") or 0) < CONFIG_REVISION:
        try:
            _write_config(cfg)
        except OSError:  # 書けなくても今回の読み取り結果は正しい
            logger.warning("lucebox設定の移行を保存できませんでした")
    return cfg


def _write_config(cfg: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def save_config(patch: dict) -> dict:
    cfg = get_config()
    for key, value in patch.items():
        if key in DEFAULT_CONFIG and key != "instances":
            cfg[key] = value
    _write_config(cfg)
    return cfg


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


# ---- インスタンス設定 ----


def _validate_instance(patch: dict) -> dict:
    """保存前に値域を検査する。API 層の Pydantic と二重だが、内部呼び出しも通す。"""
    value = dict(patch)
    if "port" in value and not 1024 <= int(value["port"]) <= 65535:
        raise LuceboxError("ポートは1024〜65535で指定してください")
    if "max_ctx" in value and not 512 <= int(value["max_ctx"]) <= 1_048_576:
        raise LuceboxError("最大コンテキストは512〜1048576で指定してください")
    if "draft_block_size" in value and not 2 <= int(value["draft_block_size"]) <= 32:
        raise LuceboxError("ドラフトブロック幅は2〜32で指定してください")
    if "ddtree_budget" in value and not 1 <= int(value["ddtree_budget"]) <= 256:
        raise LuceboxError("DDTree予算は1〜256で指定してください")
    if "fa_window" in value and not 0 <= int(value["fa_window"]) <= 131_072:
        raise LuceboxError("FAウィンドウは0〜131072で指定してください")
    if "default_max_tokens" in value and not 0 <= int(value["default_max_tokens"]) <= 1_048_576:
        raise LuceboxError("既定の出力上限は0〜1048576で指定してください")
    for key in ("cache_type_k", "cache_type_v"):
        if key in value and str(value[key]) not in CACHE_TYPES:
            raise LuceboxError(f"{key}は{', '.join(CACHE_TYPES)}のいずれかで指定してください")
    if "draft_residency" in value and str(value["draft_residency"]) not in DRAFT_RESIDENCY:
        raise LuceboxError("ドラフト常駐は auto / persistent / request-scoped で指定してください")
    return value


def unit_name(alias: str | None = None) -> str:
    if alias is None:
        alias = str(get_config().get("selected_alias") or "")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", alias or "lucebox").strip("-.")[:32] or "model"
    digest = hashlib.sha256((alias or "lucebox").encode("utf-8")).hexdigest()[:8]
    return f"{UNIT_PREFIX}-{safe}-{digest}.service"


def _runtime_state(alias: str) -> dict:
    from app.applications import systemd as sd

    return sd.query_status(unit_name(alias))


def list_instances() -> list[dict]:
    """登録済みインスタンスを優先度順に返す（稼働状況付き）。"""
    cfg = get_config()
    items = []
    for alias, instance in cfg["instances"].items():
        state = _runtime_state(alias)
        port = int(instance.get("port", DEFAULT_INSTANCE["port"]))
        items.append({
            **instance,
            "alias": alias,
            "runtime": "lucebox",
            "role": "llm",
            "loaded": state.get("status") in ("RUNNING", "STARTING"),
            "runtime_status": state.get("status", "UNKNOWN"),
            "unit": unit_name(alias),
            "base_url": f"http://127.0.0.1:{port}/v1",
            "selected": alias == cfg.get("selected_alias"),
        })
    return sorted(items, key=lambda item: (int(item.get("order") or 10**6), str(item["alias"])))


def get_instance(alias: str | None = None) -> dict:
    cfg = get_config()
    resolved = str(alias or cfg.get("selected_alias") or "")
    if resolved not in cfg["instances"]:
        raise KeyError(f"Luceboxモデル設定が見つかりません: {resolved or '(未選択)'}")
    return dict(cfg["instances"][resolved])


def instance_for_port(port: int) -> dict | None:
    return next((item for item in list_instances() if int(item.get("port", 0)) == int(port)), None)


def pins_greedy_sampling(*, alias: str = "", port: int = 0) -> bool:
    """このモデルが temperature を 0 に固定する設定かどうか。

    内部チャット（runtime_provider）はポート、ゲートウェイ（外部クライアント）は
    alias で引くので、両方の入口をここへ集約する。Lucebox に無い alias/port は
    False（他ランタイムのサンプリングには触らない）。
    """
    try:
        instances = get_config()["instances"]
    except OSError:  # 設定が読めないだけで生成を止めない
        return False
    instance = instances.get(alias) if alias else None
    if instance is None and port:
        instance = next((item for item in instances.values()
                         if int(item.get("port", DEFAULT_INSTANCE["port"])) == int(port)), None)
    return bool(instance and instance.get("prefer_speculative", True))


def instance_config_for_port(port: int) -> dict | None:
    """ポートから設定だけを引く（稼働状況を見ないので systemctl を呼ばない）。

    生成のたびに呼ばれる経路で使う。list_instances() は instance ごとに
    `systemctl show` するため、リクエストごとに通すには重い。
    """
    for alias, instance in get_config()["instances"].items():
        if int(instance.get("port", DEFAULT_INSTANCE["port"])) == int(port):
            return {**instance, "alias": alias}
    return None


def endpoint_ports() -> set[int]:
    return {int(item.get("port", 0)) for item in list_instances() if item.get("port")}


def save_instance(alias: str, patch: dict) -> dict:
    """インスタンスを作成・更新する。ポート重複は他ランタイムも含めて弾く。"""
    if not ALIAS_RE.match(alias):
        raise LuceboxError("別名は英数字と . _ : - のみ、1〜128文字で指定してください")
    cfg = get_config()
    creating = alias not in cfg["instances"]
    if creating and len(cfg["instances"]) >= MAX_INSTANCES:
        raise LuceboxError(f"Luceboxモデル設定は最大{MAX_INSTANCES}件です")
    validated = _validate_instance(patch)
    current = cfg["instances"].get(alias, {**DEFAULT_INSTANCE, "alias": alias})
    merged = {**DEFAULT_INSTANCE, **current, **validated, "alias": alias}
    port = int(merged.get("port", DEFAULT_INSTANCE["port"]))
    for other_alias, other in cfg["instances"].items():
        if other_alias != alias and int(other.get("port", 0)) == port:
            raise LuceboxError(f"ポート {port} は {other_alias} が使用しています")
    _ensure_port_free_for_other_runtimes(port)
    if creating and not int(merged.get("order") or 0):
        used = {int(i.get("order") or 0) for i in cfg["instances"].values()}
        merged["order"] = next(n for n in range(1, MAX_INSTANCES + 2) if n not in used)
    cfg["instances"][alias] = merged
    if not cfg.get("selected_alias"):
        cfg["selected_alias"] = alias
    _write_config(cfg)
    # 稼働中なら設定を反映する。停止中は次回起動時に書き出される。
    if merged.get("model_path") and _runtime_state(alias).get("status") in ("RUNNING", "STARTING"):
        start_instance(alias)
    else:
        _sync_auto_start(alias)
    return dict(merged)


def _ensure_port_free_for_other_runtimes(port: int) -> None:
    """llama.cpp のエンドポイントと衝突させない。bind 失敗は原因が見えにくい。"""
    from app.models_mgmt import llama

    try:
        used = llama.endpoint_ports()
    except OSError:  # 他ランタイム設定が読めないだけで登録を止めない
        return
    if port in used:
        raise LuceboxError(f"ポート {port} は llama.cpp が使用しています。別のポートを指定してください")


def _sync_auto_start(alias: str) -> None:
    from app.applications import systemd as sd

    try:
        instance = get_instance(alias)
    except KeyError:
        return
    name = unit_name(alias)
    if not (sd.user_unit_dir() / name).is_file():
        return
    sd.set_enabled(name, bool(instance.get("auto_start")))


def select_instance(alias: str) -> dict:
    cfg = get_config()
    if alias not in cfg["instances"]:
        raise LuceboxError(f"Luceboxモデル設定が見つかりません: {alias}")
    cfg["selected_alias"] = alias
    _write_config(cfg)
    return dict(cfg["instances"][alias])


def reorder_instances(aliases: list[str]) -> list[dict]:
    cfg = get_config()
    unknown = [a for a in aliases if a not in cfg["instances"]]
    if unknown:
        raise LuceboxError(f"未知のLuceboxモデル設定です: {', '.join(unknown)}")
    for index, alias in enumerate(aliases, start=1):
        cfg["instances"][alias]["order"] = index
    remaining = [a for a in cfg["instances"] if a not in aliases]
    for index, alias in enumerate(remaining, start=len(aliases) + 1):
        cfg["instances"][alias]["order"] = index
    _write_config(cfg)
    return list_instances()


def delete_instance(alias: str, *, delete_file: bool = False) -> dict:
    from app.applications import systemd as sd

    cfg = get_config()
    if alias not in cfg["instances"]:
        raise LuceboxError(f"Luceboxモデル設定が見つかりません: {alias}")
    instance = cfg["instances"].pop(alias)
    if cfg.get("selected_alias") == alias:
        cfg["selected_alias"] = next(iter(cfg["instances"]), "")
    _write_config(cfg)
    name = unit_name(alias)
    sd.stop(name)
    sd.set_enabled(name, False)
    sd.remove_unit(name)
    deleted = False
    reason = ""
    if delete_file:
        # 他の設定が同じGGUFを参照していたら消さない（共有ターゲットがありうる）。
        paths = {str(instance.get("model_path") or ""), str(instance.get("draft_path") or "")}
        shared = {
            str(other.get(key) or "")
            for other in cfg["instances"].values() for key in ("model_path", "draft_path")
        }
        for path in sorted(p for p in paths if p):
            if path in shared:
                reason = "他の設定が同じファイルを使用しています"
                continue
            try:
                Path(path).unlink()
                deleted = True
            except OSError as exc:
                reason = str(exc)
    return {"alias": alias, "gguf_deleted": deleted, "reason": reason}


# ---- 環境検出 ----


def _gfx_targets() -> list[int]:
    """KFD トポロジから GPU の gfx_target_version を読む。

    rocminfo はハングし得る（AMDLucebox の Troubleshooting に明記）ため、
    検出には sysfs だけを使う。
    """
    targets: list[int] = []
    root = Path("/sys/class/kfd/kfd/topology/nodes")
    try:
        nodes = sorted(root.iterdir())
    except OSError:
        return targets
    for node in nodes:
        try:
            text = (node / "properties").read_text(encoding="ascii", errors="ignore")
        except OSError:
            continue
        match = re.search(r"^gfx_target_version\s+(\d+)$", text, re.MULTILINE)
        if match and int(match.group(1)) > 0:
            targets.append(int(match.group(1)))
    return targets


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


def host_rocm_major() -> int | None:
    version = host_rocm_version()
    return int(version.split(".")[0]) if version else None


def recommended_track() -> str:
    """ホストの ROCm メジャーに合うトラック。判定できなければ既定（ROCm 10）。"""
    major = host_rocm_major()
    if major is None:
        return DEFAULT_TRACK
    for track, spec in TRACKS.items():
        if spec["rocm_major"] == major:
            return track
    return DEFAULT_TRACK


def detect() -> dict:
    """このマシンで Lucebox が動く見込みかを返す。"""
    targets = _gfx_targets()
    gpu_ok = GFX_TARGET_VERSION in targets
    kfd = os.path.exists("/dev/kfd")
    rocm = host_rocm_version()
    return {
        "gpu_supported": gpu_ok,
        "gfx": SUPPORTED_GFX if gpu_ok else "",
        "gfx_targets": targets,
        "kfd": kfd,
        "rocm_version": rocm,
        "rocm_major": host_rocm_major(),
        "available": gpu_ok and kfd,
        "reason": (
            "" if gpu_ok and kfd
            else "/dev/kfd がありません（ROCm カーネルドライバ未導入）" if not kfd
            else f"対応GPU（{SUPPORTED_GFX} / Radeon AI PRO R9700）が見つかりません"
        ),
    }


def track_warning(track: str) -> str:
    """トラックとホスト ROCm の不一致を利用者向けに説明する（空なら問題なし）。"""
    spec = TRACKS.get(track)
    if spec is None:
        return ""
    major = host_rocm_major()
    if major is None:
        return f"ホストの ROCm を検出できません。{spec['label']} 版は ROCm {spec['rocm_major']} 系のユーザースペースを前提にします"
    if major != spec["rocm_major"]:
        return (f"ホストの ROCm は {host_rocm_version()} です。{spec['label']} 版はライブラリを解決できない可能性があります"
                f"（ROCm {spec['rocm_major']} 系への更新、または別トラックの導入を検討してください）")
    return ""


# ---- 導入・更新 ----


def is_installed() -> bool:
    path = server_path()
    return path.is_file() and os.access(path, os.X_OK)


def installed_versions() -> list[dict]:
    """導入済みの (tag, track) 一覧。切り替え・ロールバック候補になる。"""
    root = runtimes_dir()
    found: list[dict] = []
    if not root.is_dir():
        return found
    cfg = get_config()
    for tag_dir in sorted(root.iterdir()):
        if not tag_dir.is_dir() or tag_dir.is_symlink():
            continue
        for track in TRACKS:
            binary = gpu_release.find_binary(tag_dir / track / "extracted", BINARY_NAME)
            if binary is None:
                continue
            found.append({
                "tag": tag_dir.name, "track": track, "label": TRACKS[track]["label"],
                "current": cfg.get("tag") == tag_dir.name and cfg.get("track") == track,
            })
    return found


def _activate(tag: str, track: str, *, sha256: str = "", upstream: str = "") -> dict:
    """展開済みの版を current へ張り替える（再ダウンロード不要）。"""
    extracted = _version_root(tag, track) / "extracted"
    binary = gpu_release.find_binary(extracted, BINARY_NAME)
    if binary is None:
        raise LuceboxError(f"{tag} / {TRACKS.get(track, {}).get('label', track)} は未導入です")
    binary.chmod(0o755)
    # 配布物のレイアウト（server/build/ と同梱ライブラリの相対関係）は崩さない。
    # current は展開物の最上位ディレクトリへ向け、バイナリは相対パスで覚える。
    relative = binary.relative_to(extracted)
    root = extracted / relative.parts[0] if len(relative.parts) > 1 else extracted
    gpu_release.relink(current_link(), root)
    previous = get_config()
    save_config({
        "tag": tag, "track": track,
        "sha256": sha256 or str(previous.get("sha256") or ""),
        "upstream": upstream or str(previous.get("upstream") or ""),
        "binary_relpath": str(binary.relative_to(root)),
        "installed_at": _now_iso(),
        "previous_tag": (str(previous.get("tag") or "") if previous.get("tag") and previous.get("tag") != tag
                         else str(previous.get("previous_tag") or "")),
    })
    return {"tag": tag, "track": track, "server": str(server_path())}


def switch_version(tag: str, track: str) -> dict:
    return _activate(tag, track)


def _read_upstream(extracted: Path) -> str:
    """BUILD_INFO.json から元 Lucebox のコミットを拾う（無ければ空）。"""
    for path in sorted(extracted.rglob("BUILD_INFO.json"))[:1]:
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        for key in ("upstream_commit", "upstream_sha", "upstream_ref", "lucebox_commit"):
            value = info.get(key)
            if isinstance(value, str) and value:
                return value[:40]
        upstream = info.get("upstream")
        if isinstance(upstream, dict):
            value = upstream.get("commit") or upstream.get("sha")
            if isinstance(value, str) and value:
                return value[:40]
    return ""


async def available_update(*, track: str = "") -> dict:
    """最新リリースと導入済みを突き合わせる。取得に失敗しても例外にしない。"""
    cfg = get_config()
    selected_track = track or str(cfg.get("track") or "") or DEFAULT_TRACK
    result = {
        "installed_tag": str(cfg.get("tag") or ""), "track": selected_track,
        "latest_tag": "", "update_available": False, "published_at": "", "error": "",
    }
    try:
        release = await gpu_release.fetch_release(RELEASE_REPO)
    except gpu_release.ReleaseError as exc:
        result["error"] = str(exc)
        return result
    result["latest_tag"] = release["tag"]
    result["published_at"] = release["published_at"]
    result["update_available"] = bool(release["tag"] and release["tag"] != result["installed_tag"])
    return result


async def install_stream(job, *, track: str = "", tag: str = "") -> dict:
    """指定トラックの Lucebox を導入する（ジョブ本体）。

    ダウンロード → SHA256SUMS 照合 → 展開 → current 張り替え、の順。
    展開まで済ませてから current を差し替えるので、失敗しても現行版は生き残る。
    """
    selected = track or str(get_config().get("track") or "") or DEFAULT_TRACK
    spec = TRACKS.get(selected)
    if spec is None:
        raise LuceboxError(f"未知のトラックです: {selected}")
    if job is not None:
        job.set_progress("リリース情報を取得中", 0, 1)
    release = await gpu_release.fetch_release(RELEASE_REPO, tag=tag, use_cache=False)
    asset = gpu_release.pick_asset(release["assets"], spec["pattern"])
    if asset is None:
        names = ", ".join(a["name"] for a in release["assets"]) or "（なし）"
        raise LuceboxError(f"{spec['label']} 向けの asset が見つかりません（{names}）")
    checksums = await gpu_release.fetch_checksums(release["assets"])
    expected = checksums.get(asset["name"], "")
    if job is not None:
        job.log(f"{release['tag']} / {spec['label']}: {asset['name']}"
                f"（{asset['size'] // 1024 // 1024}MB{'・SHA256照合あり' if expected else ''}）")

    version_root = _version_root(release["tag"], selected)
    version_root.mkdir(parents=True, exist_ok=True)
    archive = version_root / asset["name"]
    try:
        digest = await gpu_release.download_asset(job, asset, archive, expected_sha256=expected)
        if job is not None:
            job.set_progress("展開中")
        extracted = gpu_release.extract_archive(archive, version_root / "extracted")
    except gpu_release.ReleaseError as exc:
        shutil.rmtree(version_root, ignore_errors=True)
        raise LuceboxError(str(exc)) from exc
    finally:
        archive.unlink(missing_ok=True)

    upstream = _read_upstream(extracted)
    result = _activate(release["tag"], selected, sha256=f"sha256:{digest}", upstream=upstream)
    # 最新を必ず残し、直前の1版だけロールバック先として保持する。
    ordered = dict.fromkeys([release["tag"], *sorted(
        {entry["tag"] for entry in installed_versions()}, reverse=True)])
    keep = list(ordered)[:RETAIN_VERSIONS]
    removed = gpu_release.prune_versions(runtimes_dir(), [*keep, "current"])
    gpu_release.invalidate_cache(RELEASE_REPO)
    if job is not None:
        job.log(f"導入完了: {release['tag']} / {spec['label']} → {server_path()}")
        if removed:
            job.log(f"古い版を削除しました: {', '.join(removed)}")
    warning = track_warning(selected)
    if warning and job is not None:
        job.log(f"注意: {warning}")
    return {**result, "version": release["tag"], "upstream": upstream,
            "sha256": f"sha256:{digest}", "warning": warning, "pruned": removed}


async def update_stream(job) -> dict:
    """導入済みトラックを最新リリースへ更新する。トラックは変えない。"""
    cfg = get_config()
    if not is_installed():
        raise LuceboxError("Lucebox が未導入です")
    previous = str(cfg.get("tag") or "")
    result = await install_stream(job, track=str(cfg.get("track") or DEFAULT_TRACK))
    return {**result, "previous_version": previous}


def uninstall() -> dict:
    """導入物と systemd unit を消す。モデルファイルと設定は残す。"""
    from app.applications import systemd as sd

    for item in list_instances():
        name = unit_name(str(item["alias"]))
        sd.stop(name)
        sd.set_enabled(name, False)
        sd.remove_unit(name)
    link = current_link()
    if link.is_symlink() or link.exists():
        link.unlink()
    root = runtimes_dir()
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    save_config({"tag": "", "track": "", "sha256": "", "binary_relpath": "",
                 "installed_at": "", "upstream": "", "previous_tag": ""})
    return {"installed": False}


def runtime_status() -> dict:
    cfg = get_config()
    environment = detect()
    track = str(cfg.get("track") or "") or recommended_track()
    return {
        "installed": is_installed(),
        "tag": str(cfg.get("tag") or ""),
        "track": track,
        "track_label": TRACKS.get(track, {}).get("label", track),
        "tracks": [{"id": key, **{k: v for k, v in spec.items() if k != "pattern"}}
                   for key, spec in TRACKS.items()],
        "recommended_track": recommended_track(),
        "default_track": DEFAULT_TRACK,
        "sha256": str(cfg.get("sha256") or ""),
        "upstream": str(cfg.get("upstream") or ""),
        "installed_at": str(cfg.get("installed_at") or ""),
        "installed_versions": installed_versions(),
        "server_path": str(server_path()) if is_installed() else None,
        "instances": list_instances(),
        "selected_alias": str(cfg.get("selected_alias") or ""),
        "environment": environment,
        "warning": track_warning(track),
        "tool_warnings": {
            alias: "fa_window が 0 より大きいと、長いコンテキストでツール定義が注意から"
                   "外れ、ツール呼び出しが壊れます（OpenCode 等で使うなら 0 にしてください）"
            for alias, instance in cfg["instances"].items()
            if int(instance.get("fa_window", 0)) > 0
        },
        "experimental": True,
        "defaults": dict(DEFAULT_INSTANCE),
    }


# ---- systemd ----


def _unit_content(alias: str) -> str:
    from app.applications.systemd import _escape_exec_arg

    instance = get_instance(alias)
    model = str(instance.get("model_path") or "")
    if not model:
        raise LuceboxError("ターゲットGGUFが未設定です")
    args: list[str] = [str(server_path()), model]
    draft = str(instance.get("draft_path") or "")
    if draft:
        args += ["--draft", draft,
                 "--draft-block-size", str(instance.get("draft_block_size", 16)),
                 "--draft-residency", str(instance.get("draft_residency", "auto"))]
    args += [
        "--host", "127.0.0.1",
        "--port", str(instance.get("port", DEFAULT_INSTANCE["port"])),
        "--max-ctx", str(instance.get("max_ctx", DEFAULT_INSTANCE["max_ctx"])),
        "--cache-type-k", str(instance.get("cache_type_k", "q8_0")),
        "--cache-type-v", str(instance.get("cache_type_v", "q8_0")),
        "--fa-window", str(instance.get("fa_window", 2048)),
        # /v1/models と応答の model 名。ControlDeck 側の別名と一致させる。
        "--model-name", alias,
    ]
    if int(instance.get("default_max_tokens", 0) or 0) > 0:
        args += ["--default-max-tokens", str(int(instance["default_max_tokens"]))]
    if instance.get("ddtree", True) and draft:
        args += ["--ddtree", "--ddtree-budget", str(instance.get("ddtree_budget", 22))]
    args += ["--fast-rollback" if instance.get("fast_rollback", True) else "--no-fast-rollback"]
    if instance.get("agent_turn_cache"):
        args += ["--agent-turn-cache"]

    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_name = unit_name(alias).removesuffix(".service")
    from app.models_mgmt import amd_gpu
    from app.models_mgmt.runtime_policy import get_policy

    lines = [
        "[Unit]",
        f"Description=Control Deck Lucebox dflash_server ({alias})",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f'Environment="LD_LIBRARY_PATH={":".join(str(p) for p in _library_dirs())}"',
        # ROCm既知バグ（ROCm/ROCm#2625）: HIPストリームが2本以上あるとアイドルでも
        # GPU busy 100%が続く。llama.cpp 側と同じくHWキューを1本へ固定する。
        'Environment="GPU_MAX_HW_QUEUES=1"',
    ]
    for preflight in amd_gpu.preflight_argvs(get_policy().amd_gpu):
        lines.append("ExecStartPre=" + " ".join(_escape_exec_arg(a) for a in preflight))
    lines += [
        "ExecStart=" + " ".join(_escape_exec_arg(a) for a in args),
        "Restart=on-failure",
        "RestartSec=3",
        "TimeoutStopSec=20",
        "KillSignal=SIGTERM",
        f"StandardOutput=append:{log_dir}/{log_name}.log",
        f"StandardError=append:{log_dir}/{log_name}.log",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def _log_tail(alias: str, max_chars: int = 300) -> str:
    path = data_dir() / "logs" / f"{unit_name(alias).removesuffix('.service')}.log"
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    errors = [ln for ln in lines[-40:] if re.search(r"error|failed|abort", ln, re.I)]
    tail = errors[-2:] if errors else lines[-2:]
    return " / ".join(tail)[-max_chars:]


def start_instance(alias: str | None = None) -> tuple[bool, str]:
    from app.applications import systemd as sd

    if not is_installed():
        return False, "Lucebox が未導入です"
    try:
        instance = get_instance(alias)
    except KeyError as exc:
        return False, str(exc)
    resolved = str(instance.get("alias") or alias or "lucebox")
    if not Path(str(instance.get("model_path") or "")).is_file():
        return False, "ターゲットGGUFが存在しません"
    draft = str(instance.get("draft_path") or "")
    if draft and not Path(draft).is_file():
        return False, "ドラフトGGUFが存在しません"
    mark_used(resolved)
    try:
        from app.models_mgmt.runtime_policy import ensure_gpu_profile

        ensure_gpu_profile(force=True)
    except RuntimeError as exc:
        return False, str(exc)
    name = unit_name(resolved)
    sd.write_unit(name, _unit_content(resolved))
    sd.reset_failed(name)
    sd.set_enabled(name, bool(instance.get("auto_start")))
    # 同じポートの他Luceboxモデルを先に止める。bind失敗は原因が見えにくい。
    port = int(instance.get("port", DEFAULT_INSTANCE["port"]))
    for other in list_instances():
        other_alias = str(other["alias"])
        if other_alias != resolved and int(other.get("port", 0)) == port and other.get("loaded"):
            sd.stop(unit_name(other_alias))
    active = sd.query_status(name).get("status") in ("RUNNING", "STARTING")
    ok, err = sd.restart(name) if active else sd.start(name)
    if not ok:
        return ok, err
    # Type=simple は起動成功が即返る。引数エラー等の即死を短時間監視して拾う。
    stable = 0
    for _ in range(10):
        time.sleep(1)
        state = sd.query_status(name)
        if state.get("status") in ("FAILED", "STOPPED") or state.get("sub_state") == "auto-restart":
            detail = _log_tail(resolved)
            return False, "dflash_server が起動直後に停止しました" + (f": {detail}" if detail else "")
        if state.get("status") == "RUNNING":
            stable += 1
            if stable >= 3:
                return True, ""
        else:
            stable = 0
    return True, ""


def stop_instance(alias: str | None = None) -> tuple[bool, str]:
    from app.applications import systemd as sd

    cfg = get_config()
    resolved = str(alias or cfg.get("selected_alias") or "")
    if not resolved:
        return False, "停止対象のLuceboxモデルがありません"
    was_loaded = any(str(i["alias"]) == resolved and i.get("loaded") for i in list_instances())
    result = sd.stop(unit_name(resolved))
    if result[0] and was_loaded:
        try:
            from app.resources.broker import broker as resource_broker

            resource_broker.telemetry.record_unload(residency_key(get_instance(resolved)))
        except Exception:  # noqa: BLE001 - telemetry must never block a stop
            logger.exception("lucebox unload telemetry recording failed")
    return result


async def health(alias: str | None = None) -> dict:
    try:
        instance = get_instance(alias)
    except KeyError:
        return {"ok": False, "status_code": None}
    port = int(instance.get("port", DEFAULT_INSTANCE["port"]))
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"http://127.0.0.1:{port}/health")
        return {"ok": response.status_code == 200, "status_code": response.status_code}
    except httpx.HTTPError:
        return {"ok": False, "status_code": None}


def residency_key(instance: dict) -> str:
    """ロード実測とGPUリースが共有する、パスに依存しない同一性キー。"""
    identity = hashlib.sha256(
        str(instance.get("model_path") or instance.get("alias") or "lucebox").encode("utf-8")
    ).hexdigest()[:16]
    return f"lucebox:{identity}"


def mark_used(alias: str) -> None:
    cfg = get_config()
    if alias not in cfg["instances"]:
        return
    cfg["instances"][alias]["last_used_at"] = _now_iso()
    _write_config(cfg)


def mark_used_by_base_url(base_url: str) -> str | None:
    from urllib.parse import urlsplit

    parsed = urlsplit(base_url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
        return None
    instance = instance_for_port(parsed.port)
    if instance is None:
        return None
    mark_used(str(instance["alias"]))
    return str(instance["alias"])


async def ensure_ready(alias: str, *, timeout_seconds: int = 300) -> bool:
    """停止中なら起動し、/health 200（モデル読み込み完了）まで待つ。"""
    import asyncio

    state = await health(alias)
    if state.get("ok"):
        mark_used(alias)
        return True
    ok, error = await asyncio.to_thread(start_instance, alias)
    if not ok:
        logger.warning("lucebox start failed for %s: %s", alias, error)
        return False
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        state = await health(alias)
        if state.get("ok"):
            mark_used(alias)
            return True
        await asyncio.sleep(2)
    return False


async def ensure_ready_by_base_url(base_url: str, *, timeout_seconds: int = 300) -> bool:
    from urllib.parse import urlsplit

    parsed = urlsplit(base_url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
        return False
    instance = instance_for_port(parsed.port)
    if instance is None:
        return False
    return await ensure_ready(str(instance["alias"]), timeout_seconds=timeout_seconds)
