"""Models 画面での新規のファイル・フォルダ選択の検証と、登録前の読み取り。

利用者の決まり（2026-11-27）「選べるフォルダ（ルート）は /data1tb と /data2tb に統一」。
**新しく選ぶ・登録するときだけ**この検証を使う。既存の登録済みモデル（他のルートにあるもの）
の表示・再保存・起動は既存の各ランタイム API のまま変えない（壊さない）。

検証は commander/workdir.py と同じ考え方: realpath（Path.resolve 相当）で実体を解決してから
ルートの中か確かめるので、シンボリックリンクで外へ出られない。さらに files の拒否場所
（~/.ssh など）と ControlDeck の data_dir の中は選ばせない。

登録前の読み取り（GGUF のヘッダ・config.json）は読取量・件数を制限し、同期 endpoint
（threadpool）で呼ぶことを前提とする（event loop を止めない）。
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from app.config import data_dir
from app.security.paths import is_within, normalize

# 2026-11-27 利用者の決定。将来ここだけ変えればよい（既存の登録には適用しない）。
SELECTION_ROOTS: tuple[str, ...] = ("/data1tb", "/data2tb")

_ALIAS_BAD = re.compile(r"[^a-z0-9.-]+")
_GGUF_CONTEXT = re.compile(r"\.context_length$")
# GGUF の読み取りの上限（件数・量）。壊れたファイルを読み続けない。
_GGUF_MAX_KEYS = 100_000
_GGUF_MAX_BYTES = 8 * 1024 * 1024
_CONFIG_MAX_BYTES = 2 * 1024 * 1024


def selection_roots() -> list[Path]:
    """新規選択に許すルート（実在するものだけ。実体のパス）。"""
    out: list[Path] = []
    for raw in SELECTION_ROOTS:
        try:
            resolved = normalize(raw)
        except OSError:
            continue
        if resolved.is_dir() and resolved not in out:
            out.append(resolved)
    return out


def _blocked(path: Path) -> bool:
    from app.files import service as files

    data = normalize(str(data_dir()))
    if path == data or is_within(path, data):
        return True
    return any(path == d or is_within(path, d) for d in files._deny_roots())  # noqa: SLF001 - commander/workdir と同じ読み方


def resolve_selection(path: str) -> Path:
    """新規選択のパスを検証して実体のパスを返す（realpath・symlink 検証込み）。

    ルートの外・拒否場所・symlink で外へ抜けるものは PermissionError。
    """
    from app.files.service import FileAccessError

    p = Path(path).expanduser()
    if not p.is_absolute():
        raise FileAccessError("絶対パスで指定してください")
    resolved = normalize(str(p))
    if not resolved.exists():
        raise FileNotFoundError(f"存在しません: {path}")
    roots = selection_roots()
    if not any(is_within(resolved, root) for root in roots):
        raise PermissionError(
            f"新しく選べるのは {' と '.join(str(r) for r in roots)} の中だけです: {resolved}")
    if _blocked(resolved):
        raise PermissionError(f"アクセスが拒否されています: {resolved}")
    return resolved


def suggest_alias(name: str) -> str:
    """ファイル名・フォルダ名から登録名（alias）の候補を作る（英小文字・数字・.・- だけ）。"""
    stem = Path(str(name)).stem or str(name)
    cleaned = _ALIAS_BAD.sub("-", stem.strip().lower()).strip("-.")
    return cleaned[:48] or "model"


def gguf_metadata(path: Path) -> dict:
    """GGUF の先頭のメタデータから、登録に要る鍵だけを読む（テンソルは読まない）。

    上限: 鍵 100000 個・先頭 8MB。general.file_type・general.quantized_by・
    general.architecture と最初の `*.context_length` が揃ったら読むのをやめる。
    読めない・GGUF でないときは {} を返す（壊れたファイルで登録画面を止めない）。
    """
    scalar = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
    wanted = ("general.file_type", "general.quantized_by", "general.architecture")
    out: dict = {}
    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"GGUF":
                return out
            handle.read(4)  # version
            _tensors, count = struct.unpack("<QQ", handle.read(16))
            read_bytes = 24

            def text() -> str:
                nonlocal read_bytes
                (n,) = struct.unpack("<Q", handle.read(8))
                read_bytes += 8
                data = handle.read(min(n, 1 << 20))
                read_bytes += len(data)
                return data.decode("utf-8", "replace")

            for _ in range(min(count, _GGUF_MAX_KEYS)):
                key = text()
                (kind,) = struct.unpack("<I", handle.read(4))
                read_bytes += 4
                value = None
                if kind == 8:                      # 文字列
                    value = text()
                elif kind == 9:                    # 配列（件数だけ読み飛ばす）
                    (item,) = struct.unpack("<I", handle.read(4))
                    read_bytes += 4
                    (n,) = struct.unpack("<Q", handle.read(8))
                    read_bytes += 8
                    if item == 8:
                        for _ in range(n):
                            text()
                    else:
                        size = struct.calcsize("<" + scalar[item]) * n
                        handle.seek(size, 1)
                        read_bytes += size
                elif kind in scalar:
                    size = struct.calcsize("<" + scalar[kind])
                    raw = handle.read(size)
                    read_bytes += size
                    value = struct.unpack("<" + scalar[kind], raw)[0]
                else:
                    return out                     # 不明な型: これ以上読めない
                if key in wanted:
                    out[key] = value
                elif key.endswith(".context_length") and "context_length" not in out:
                    out["context_length"] = value
                if read_bytes > _GGUF_MAX_BYTES:
                    return out
                if all(name in out for name in wanted) and "context_length" in out:
                    return out
    except (OSError, ValueError, KeyError, struct.error):
        return {}
    return out


def _config_metadata(folder: Path) -> dict:
    """config.json（HF 形式の safetensors フォルダ）から登録に要る値だけを読む。"""
    config_path = folder / "config.json"
    try:
        with config_path.open("rb") as handle:
            raw = handle.read(_CONFIG_MAX_BYTES)
        config = json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    text_config = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}
    quant = config.get("quantization_config")
    if not isinstance(quant, dict):
        quant = text_config.get("quantization_config")
    if not isinstance(quant, dict):
        quant = {}
    return {
        "model_type": str(config.get("model_type") or text_config.get("model_type") or ""),
        "quant_method": str(quant.get("quant_method") or ""),
        "quant_format": str(quant.get("format") or ""),
        "max_position_embeddings": (
            config.get("max_position_embeddings") or text_config.get("max_position_embeddings")),
    }


def _detect(path: Path) -> tuple[str, str]:
    """(kind, format)。kind は file / folder、format は gguf / safetensors / rad / unknown。"""
    if path.is_file():
        if path.suffix.lower() == ".gguf":
            try:
                with path.open("rb") as handle:
                    if handle.read(4) == b"GGUF":
                        return "file", "gguf"
            except OSError:
                pass
        if path.suffix.lower() == ".rad":
            return "file", "rad"
        return "file", "unknown"
    if path.is_dir():
        if (path / "config.json").is_file():
            return "folder", "safetensors"
        return "folder", "unknown"
    return "unknown", "unknown"


def _file_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(item.stat().st_size for item in path.glob("*") if item.is_file())
    except OSError:
        return 0


def _runtime_views(path: Path, kind: str, fmt: str, metadata: dict) -> list[dict]:
    """どのランタイムがこのパスを登録に使えるか（できないときは理由つき）。"""
    views: list[dict] = []
    if fmt == "gguf":
        views.append({"runtime": "llama.cpp", "eligible": True, "reason": "",
                      "register_endpoint": "/api/v1/models/llama/instances"})
        # Strata が動かすのは Unsloth の UD-*（quantized_by が Unsloth）・GSQ-RCO・Q8_0。
        # ヘッダだけでは GSQ-RCO と動かない K 量子化を区別できないので、ここでは事実を出して
        # 最終判断は登録時（strata_gateway）に任せる（誤って断らない）。
        quantized_by = str(metadata.get("general.quantized_by") or "")
        file_type = metadata.get("general.file_type")
        if "unsloth" in quantized_by.casefold():
            note = "Unsloth 版（UD-*・Q8_0 は登録可）"
        elif isinstance(file_type, int) and file_type == 7:
            note = "Q8_0（登録可）"
        else:
            note = "GSQ-RCO（ISTA-DASLab）以外は Strata では動きません。登録時に確認します"
        views.append({"runtime": "strata", "eligible": True, "reason": note,
                      "register_endpoint": "/api/v1/models/strata/instances"})
        views.append({"runtime": "maya", "eligible": True,
                      "reason": "Maya 本体の目録が認識するファイルかは登録の候補一覧で確かめる",
                      "register_endpoint": "/api/v1/features/maya-s/models/register-jobs"})
    else:
        views.append({"runtime": "llama.cpp", "eligible": False,
                      "reason": "llama.cpp の登録は GGUF ファイルを指定してください"})
        views.append({"runtime": "strata", "eligible": False,
                      "reason": "Strata の既存モデル登録は GGUF ファイルを指定してください"})
    if fmt == "safetensors":
        views.append({"runtime": "sly-radiance", "eligible": True, "reason": "",
                      "register_endpoint": "/api/v1/models/sly-radiance/instances"})
        views.append({"runtime": "radiance", "eligible": False,
                      "reason": "Radiance の登録は .rad ファイル（形式が違う）"})
    elif fmt == "rad":
        views.append({"runtime": "radiance", "eligible": True, "reason": "",
                      "register_endpoint": "/api/v1/models/radiance/instances"})
        views.append({"runtime": "sly-radiance", "eligible": False,
                      "reason": "Sly-Radiance の登録は config.json のある safetensors フォルダ（形式が違う）"})
    else:
        for runtime in ("sly-radiance", "radiance"):
            views.append({"runtime": runtime, "eligible": False,
                          "reason": "対応している形式ではありません"})
    return views


def inspect(path: str) -> dict:
    """登録の前の読み取り（UI 担当の RuntimeRegisterFromFile 用）。

    返すもの: 検証済みの実体パス・種類・形式・alias の候補・大きさ・メタデータ
    （GGUF はヘッダ、safetensors は config.json）・ランタイムごとの可否。
    例外（FileNotFoundError・PermissionError）は呼び出し側が HTTP の状態へ起こす。
    """
    resolved = resolve_selection(path)
    kind, fmt = _detect(resolved)
    metadata: dict = {}
    if fmt == "gguf":
        metadata = gguf_metadata(resolved)
        # general.file_type の数値を名前へ（strata_gateway の表を共用する）。
        from app.models_mgmt.strata_gateway import _FILE_TYPES

        file_type = metadata.get("general.file_type")
        if isinstance(file_type, int):
            metadata["quantization"] = _FILE_TYPES.get(file_type, f"種類 {file_type}")
        metadata["context_length"] = metadata.get("context_length")
    elif fmt == "safetensors":
        metadata = _config_metadata(resolved)
    return {
        "path": str(resolved),
        "kind": kind,
        "format": fmt,
        "alias_suggestion": suggest_alias(resolved.name),
        "size_bytes": _file_size(resolved),
        "metadata": metadata,
        "runtimes": _runtime_views(resolved, kind, fmt, metadata),
    }
