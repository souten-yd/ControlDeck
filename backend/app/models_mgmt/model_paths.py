"""Models 画面での新規のファイル・フォルダ選択の検証と、登録前の読み取り。

利用者の決まり（2026-11-27）「選べるフォルダ（ルート）は /data1tb と /data2tb に統一」。
**新しく選ぶ・登録するときだけ**この検証を使う。既存の登録済みモデル（他のルートにあるもの）
の表示・再保存・起動は既存の各ランタイム API のまま変えない（壊さない）。

検証は commander/workdir.py と同じ考え方: realpath（Path.resolve 相当）で実体を解決してから
ルートの中か確かめるので、シンボリックリンクで外へ出られない。さらに files の拒否場所
（~/.ssh など）と ControlDeck の data_dir の中は選ばせない。

登録前の読み取り（GGUF のヘッダ・config.json）は読取量・件数を制限し、同期 endpoint
（threadpool）で呼ぶことを前提とする（event loop を止めない）。フォルダの中の子
（config.json など）も symlink は読まない（実体がフォルダの外に出るのを許さない）。
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
# GGUF の読み取りの上限（件数・量）。壊れたファイル・細工したヘッダで長く回さない。
_GGUF_MAX_KEYS = 100_000
_GGUF_MAX_BYTES = 8 * 1024 * 1024
# 1 つの文字列（鍵・値）の上限。これを超える文字列は全部は読まず、残りを読み飛ばす。
_GGUF_MAX_TEXT_BYTES = 1 << 20
# 配列 1 つの要素数の上限。実際の語彙の文字列配列は 15 万程度までなので、それより多い
# 件数は壊れた・細工したヘッダとして打ち切る（要素の素通りは件数に寄らず 8MB でも止まる）。
_GGUF_MAX_ARRAY_ITEMS = 200_000
_CONFIG_MAX_BYTES = 2 * 1024 * 1024
# フォルダの大きさを数える走査の件数の上限。模型フォルダは高々数百のファイルしか
# 持たない（大量の項目で inspect を長く回させない）。
_FOLDER_MAX_ENTRIES = 5000


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

    上限: 鍵 100000 個・読み込み（読み飛ばしを含む）8MB・1 つの文字列 1MB・
    配列 1 つ 200000 要素。8MB の上限は配列の要素ごとにも効く（件数を大きく偽った
    ヘッダで、要素の案内だけを延々回させない）。
    general.file_type・general.quantized_by・general.architecture と最初の
    `*.context_length` が揃ったら読むのをやめる。
    読めない・GGUF でないときは {} を返す（壊れたファイルで登録画面を止めない）。
    上限で打ち切ったときは、その時点で読めた鍵だけを返す。
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
                take = min(n, _GGUF_MAX_TEXT_BYTES)
                data = handle.read(take)
                read_bytes += len(data)
                if n > take:
                    # 長すぎる文字列は全部読まない。残りを読み飛ばして位置は厳密に保つ
                    # （細工した長さで、この後の鍵をずらして読ませない）。
                    rest = n - take
                    handle.seek(rest, 1)
                    read_bytes += rest
                return data.decode("utf-8", "replace")

            for _ in range(min(count, _GGUF_MAX_KEYS)):
                if read_bytes > _GGUF_MAX_BYTES:
                    return out                 # 総読取の上限（配列の要素の間にも効く）
                key = text()
                (kind,) = struct.unpack("<I", handle.read(4))
                read_bytes += 4
                value = None
                if kind == 8:                  # 文字列
                    value = text()
                elif kind == 9:                # 配列（値は読まずに読み飛ばす）
                    (item,) = struct.unpack("<I", handle.read(4))
                    read_bytes += 4
                    (n,) = struct.unpack("<Q", handle.read(8))
                    read_bytes += 8
                    if item == 8:
                        if n > _GGUF_MAX_ARRAY_ITEMS:
                            return out         # 語彙（15 万程度）より多い: 壊れた・細工したヘッダ
                        for _ in range(n):
                            if read_bytes > _GGUF_MAX_BYTES:
                                return out     # 要素ごとに上限を効かせて、案内だけで回させない
                            text()
                    elif item in scalar:
                        size = struct.calcsize("<" + scalar[item]) * n
                        if size > _GGUF_MAX_BYTES:
                            return out         # 読み飛ばすだけでも、細工した件数は打ち切る
                        handle.seek(size, 1)
                        read_bytes += size
                    else:
                        return out             # 不明な要素型: これ以上読めない
                elif kind in scalar:
                    size = struct.calcsize("<" + scalar[kind])
                    raw = handle.read(size)
                    read_bytes += size
                    value = struct.unpack("<" + scalar[kind], raw)[0]
                else:
                    return out                 # 不明な型: これ以上読めない
                if key in wanted:
                    out[key] = value
                elif key.endswith(".context_length") and "context_length" not in out:
                    out["context_length"] = value
                if all(name in out for name in wanted) and "context_length" in out:
                    return out
    except (OSError, ValueError, KeyError, struct.error):
        return {}
    return out


def _config_metadata(folder: Path) -> dict | None:
    """config.json（HF 形式の safetensors フォルダ）から登録に要る値だけを読む。

    子の symlink（フォルダの中へ向いたものも含む）・通常ファイルでないもの・読める
    JSON のオブジェクトでないものは None を返す。呼び出し側は None を形式 unknown に
    落とす（名前だけ、config.json がある、で safetensors とは認定しない）。
    読む量は 2MB まで。
    """
    config_path = folder / "config.json"
    if config_path.is_symlink():
        return None            # 子の symlink は実体の所在が保証できないので読まない
    try:
        resolved = normalize(str(config_path))
    except OSError:
        return None
    if not is_within(resolved, folder) or not resolved.is_file():
        return None            # フォルダの外の実体・FIFO など通常ファイルでないもの
    try:
        with resolved.open("rb") as handle:
            raw = handle.read(_CONFIG_MAX_BYTES)
        config = json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, ValueError, RecursionError):
        return None            # 読めない・JSON でない・入れ子が深すぎる
    if not isinstance(config, dict):
        return None
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
    """(kind, format)。kind は file / folder、format は gguf / safetensors / rad / unknown。

    - gguf は先頭 4 バイト（GGUF）で判定する（名前だけでは認定しない）。
    - safetensors は config.json のあるフォルダ。ここは「候補」で、形式の最終判断は
      中身（inspect が config.json を読める JSON オブジェクトか確かめる）。
    - rad は拡張子だけ。Radiance 本体の登録（radiance.py _validate_model_path）も
      拡張子だけの検証で、中身の形式はコンテナ側で読むので、ここだけ中身で断ると
      登録できるものを誤って断ることになる（誤って断らない）。
    """
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
    """大きさ（バイト）。フォルダは最上位の項目を数える。

    子の symlink は数に入れない（実体がフォルダの外のものまで読まない）。走査は
    _FOLDER_MAX_ENTRIES 件で打ち切る（大量の項目で長く回させない。模型フォルダは
    高々数百のファイルなので、打ち切りが実害になることはない）。
    """
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for index, item in enumerate(path.glob("*")):
            if index >= _FOLDER_MAX_ENTRIES:
                break
            if item.is_symlink():
                continue
            if item.is_file():
                total += item.stat().st_size
        return total
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
        config = _config_metadata(resolved)
        if config is None:
            # config.json が読める JSON オブジェクトでない（子 symlink・壊れている・
            # 入れ子が深い）。名前だけでは safetensors と認定しない。
            fmt = "unknown"
        else:
            metadata = config
    return {
        "path": str(resolved),
        "kind": kind,
        "format": fmt,
        "alias_suggestion": suggest_alias(resolved.name),
        "size_bytes": _file_size(resolved),
        "metadata": metadata,
        "runtimes": _runtime_views(resolved, kind, fmt, metadata),
    }
