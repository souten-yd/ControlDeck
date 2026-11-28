"""目録のモデルのカードを開いたときに読む詳細（量子化ごとの大きさ・載るか・取得済みか）。

2026-11-28 利用者「なんだか目録が変。追加する機能のところ全てやり直して」。大半の行で大きさと
「載るか」が空だった。目録には推測の大きさを書かない決まり（runtime_catalog の冒頭）なので、
利用者がカードを開いたときだけ HF の tree（既存の守りつきの通信・外部の許可の検査）から読む。

- 結果は data_dir の下に 24 時間覚える（同じカードを開き直すたびに通信しない）。
- Strata が自分で取る行（acquire=runtime）は Strata の目録の値を使い、通信しない。
- HF に量子化のファイルが見つからない行は supported=false（目録の誤りを利用者に選ばせない）。
- 取得済み: 取得先（既定のライブラリと登録済みのライブラリ）に、HF の一覧と同じ大きさの
  ファイルが全部そろっていれば downloaded と、登録の入口のパスを返す。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.models.catalog_details")

CACHE_TTL_S = 24 * 3600
GIB = 1024 ** 3


def _cache_path() -> Path:
    return data_dir() / "cache" / "model-catalog-details.json"


def _cache_read() -> dict:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_write(data: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        logger.warning("目録の大きさの覚えを書けませんでした")


def fit_by_size(runtime: str, size_gib: float | None, ram: float | None, vram: float | None) -> str:
    """大きさからの「載るか」の目安（目録と同じ判定。runtime_catalog.fit_by_size）。"""
    from app.models_mgmt import runtime_catalog

    return runtime_catalog.fit_by_size(size_gib, ram, vram)


def _roots() -> list[Path]:
    from app.models_mgmt import libraries

    out: list[Path] = []
    try:
        out.append(libraries.default_models_dir())
    except Exception:  # noqa: BLE001 - 取得先が読めなくても大きさは返す
        pass
    try:
        for item in libraries.list_libraries():
            if item.get("mounted") and item.get("path"):
                out.append(Path(str(item["path"])))
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(out))


def _local(entry, files: list[dict]) -> str:
    """取得済みなら登録の入口のパス（gguf は先頭の片・rad はファイル・safetensors はフォルダ）。"""
    if not files:
        return ""
    folder_name = entry.repo.replace("/", "--")
    for root in _roots():
        folder = root / folder_name
        if folder.is_symlink() or not folder.is_dir():
            continue
        ok = True
        for item in files:
            target = folder / str(item["path"]).rsplit("/", 1)[-1]
            try:
                if target.is_symlink() or not target.is_file() or target.stat().st_size != int(item["size"]):
                    ok = False
                    break
            except OSError:
                ok = False
                break
        if not ok:
            continue
        if entry.format == "safetensors":
            return str(folder)
        names = sorted(str(item["path"]).rsplit("/", 1)[-1] for item in files)
        first = next((n for n in names if "-00001-of-" in n), names[0])
        return str(folder / first)
    return ""


async def _files_for(entry) -> list[dict]:
    from app.models_mgmt import hf

    if entry.files and entry.format == "gguf":
        # 目録にファイル名を書いた行（Inkling の無検閲版は本体と DSpark の 2 つ）はそのファイルだけ
        wanted = set(entry.files)
        listed = [item for item in await hf.list_model_files(entry.repo) if item["path"] in wanted]
        return listed if len(listed) == len(wanted) else []
    if entry.quantization and entry.format == "gguf":
        return await hf.list_quantization_files(entry.repo, quantization=entry.quantization)
    listed = await hf.list_model_files(entry.repo)
    if entry.format == "rad":
        wanted = {name.rsplit("/", 1)[-1] for name in entry.files}
        return [item for item in listed if item["path"].lower().endswith(".rad")
                and (not wanted or item["path"].rsplit("/", 1)[-1] in wanted)]
    return listed


async def model_details(runtime: str, model_id: str) -> dict:
    """1 つのモデル（カード）の量子化ごとの大きさ・載るか・取得済みか。"""
    import asyncio

    from app.models_mgmt import hf, libraries, runtime_catalog, strata_catalog

    model = next((m for m in runtime_catalog.models_for(runtime, None, None) if m["id"] == model_id), None)
    if model is None:
        raise KeyError(model_id)
    ram, vram = strata_catalog.machine_gib()
    cache = await asyncio.to_thread(_cache_read)
    now = time.time()
    changed = False
    out = []
    for variant in model["variants"]:
        entry = runtime_catalog.get_entry(runtime, variant["id"])
        row = {"id": variant["id"], "size_gib": variant["size_gib"], "fit": variant["fit"],
               "supported": variant["supported"], "reason": variant["reason"], "state": "available", "local_path": ""}
        if entry is None or not entry.supported or entry.acquire != "hf" or not entry.repo:
            # Strata が初回の起動で取る行・Maya の行は本体の値（通信しない）
            if entry is not None:
                row["fit"] = runtime_catalog._fit_for(entry, ram, vram)  # noqa: SLF001 - 同じ判定を使う
            out.append(row)
            continue
        key = f"{runtime}/{entry.id}"
        hit = cache.get(key)
        if not (isinstance(hit, dict) and now - float(hit.get("at") or 0) < CACHE_TTL_S):
            try:
                files = await _files_for(entry)
                hit = {"at": now, "files": [{"path": f["path"], "size": int(f["size"])} for f in files], "missing": not files}
            except hf.HfError as exc:
                text = str(exc)
                missing = "ありません" in text or "見つかりません" in text
                hit = {"at": now, "files": [], "missing": True} if missing else None
                if "認証" in text:
                    row["reason"] = "HF のトークンと規約の承諾が要ります（設定 → API キー）"
                elif not missing:
                    row["reason"] = "大きさを読めませんでした（あとでもう一度開いてください）"
            if hit is not None:
                cache[key] = hit
                changed = True
        if hit:
            if hit.get("missing"):
                row.update({"supported": False, "reason": "HF に見つかりません（目録の誤りの見込み）"})
            else:
                total = sum(int(f["size"]) for f in hit["files"])
                row["size_gib"] = round(total / 1e9, 1) if total else row["size_gib"]
                # 本体の判定がある行（Strata の無検閲版は公式の同じ量子化の判定）はそれを使う
                row["fit"] = entry.fit or fit_by_size(runtime, row["size_gib"], ram, vram)
                local = await asyncio.to_thread(_local, entry, hit["files"])
                if local:
                    row.update({"state": "downloaded", "local_path": local})
        out.append(row)
    if changed:
        await asyncio.to_thread(_cache_write, cache)
    return {"model_id": model_id, "variants": out, "hardware": {"ram_total_gib": ram, "vram_total_gib": vram},
            "libraries": await asyncio.to_thread(libraries.list_libraries)}
