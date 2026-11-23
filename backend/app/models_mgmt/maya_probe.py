"""Maya の量子化の一覧と、指定フォルダにファイルがそろっているかを調べる（読むだけ）。

ControlDeck 本体からは import しない。Maya の venv の Python で次のように実行する:

    <engine>/.venv/bin/python -I maya_probe.py <engine> [<folder>]

Maya 自身の一覧（tools/maya_quants.py、maya.py の glm_downloads、tools/ds4・mm3 の
download_gguf.json）をそのまま使い、ファイル名・大きさの食い違いを作らない。何もダウンロード
しないし、書き込まない。結果は JSON 1 行で標準出力へ出す。

フォルダの決まり（`hf download <repo> --local-dir <folder>` で取った形のまま）:
- GLM-5.3-Flash: <folder>/<量子化のフォルダ>/*.gguf（例 Maya-S24/）。量子化のフォルダそのものでもよい
- DeepSeek-V4.1-Flash: <folder> は DeepSeek-V4.1-Flash-1017-Maya-GGUF（Maya の n-gram 表をそこから
  共有するため、この名前が要る）。<folder>/Maya-XS/ と <folder>/dspark/
- MiniMax-M3.1-Flash: <folder>/Maya-XS/ など
- Hy4-Flash: <folder>/Maya-XS/（trunk・n-gram 表・mmproj、repo の XS フォルダと同じ形）。
  S・M・L の n-gram 表（全サイズで同じ 1 ファイル）と mmproj は XS のフォルダに在り、
  S・M・L の setup が --models-dir から hard-link する
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

FAMILY_LABEL = {"glm": "GLM-5.3-Flash", "deepseek": "DeepSeek-V4.1-Flash", "minimax": "MiniMax-M3.1-Flash",
                "hy4": "Hy4-Flash"}


def _quiet(fn, *args):
    """Maya's helpers print and call sys.exit on a damaged manifest: keep both out of the JSON."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return fn(*args)
        except SystemExit:
            return None


def _size_ok(path: Path, size: int | None) -> bool:
    try:
        return path.is_file() and (size is None or path.stat().st_size == int(size))
    except OSError:
        return False


def _files(base: Path, rows: dict, *, required: bool = True, what: str = "", sha: dict | None = None) -> list[dict]:
    sha = sha or {}
    return [{"path": str(base / name), "name": name, "size": size, "present": _size_ok(base / name, size),
             "required": required, "what": what, "sha256": sha.get(name) or sha.get(Path(name).name) or ""}
            for name, size in rows.items()]


def _hf(repo: str, revision: str, includes: list[str], local_dir: str) -> str:
    rev = f" --revision {revision}" if revision and revision != "main" else ""
    inc = "".join(f' --include "{item}"' for item in includes)
    return f"hf download {repo}{rev}{inc} --local-dir {local_dir}"


def glm_entry(maya, key: str, tier: str, setup_id: str, folder: Path | None) -> dict:
    m = maya.glm_downloads().get(key) or {}
    shards = int(m.get("shards") or 0)
    names = [m["file"].format(i=i, n=shards) for i in range(1, shards + 1)] if m.get("file") else []
    sizes = m.get("bytes") or {}
    sub = m.get("folder") or key
    row = {"family": "glm", "setup_id": setup_id, "tier": tier, "quant": key,
           "size_gb": round(float(m.get("download_gb") or 0), 1), "memory_gb": round(float(m.get("download_gb") or 0)),
           "repo": m.get("repo", ""), "folder_name": "", "subfolder": sub}
    row["revision"] = str(m.get("revision") or "main")
    row["hf"] = _hf(row["repo"], str(m.get("revision") or ""), [f"{sub}/*"], "<フォルダ>")
    if folder is not None:
        d = folder if folder.name == sub else folder / sub
        row["model_dir"] = str(d)
        # ダウンロードの --local-dir（リポジトリの中の相対パスがこの下にそのまま並ぶ）。
        row["repo_root"] = str(d.parent)
        sha = m.get("sha256") if isinstance(m.get("sha256"), dict) else {}
        row["files"] = _files(d, {n: sizes.get(n) for n in names}, what="model", sha=sha)
        vision = m.get("vision") or {}
        if vision:
            vd = d / vision.get("folder", "vision")
            row["files"] += _files(vd, {vision[k]: None for k in ("mmproj", "vocab") if vision.get(k)},
                                   required=False, what="vision")
        row["hf"] = _hf(row["repo"], str(m.get("revision") or ""), [f"{sub}/*"], str(d.parent))
    return row


def ds4_entry(maya, key: str, tier: str, setup_id: str, folder: Path | None) -> dict:
    m = _quiet(maya.ds4_gguf_model, key) or {}
    man = json.loads(maya.DS4_GGUF_MANIFEST.read_text(encoding="utf-8"))
    my = man.get("maya") or {}
    sub = maya.DS4_QUANTS[key]
    repo_folder = f"{maya.DS4}-Maya-GGUF"
    own = _quiet(maya.ds4_quant_drafter, key)
    eg = (man.get("abliterated") or {}).get("engram") or {}
    table_names = [Path(n).name for n in (eg.get("from") or {}).get(sub, [])]
    tier_files = maya.ds4_tier_files(my, sub)
    tables = {Path(k).name: v[0] for k, v in tier_files.items() if Path(k).name in table_names}
    includes = [f"{sub}/*"] + ([f"dspark/{Path(own['json']).stem}*"] if own else [])
    size = sum((m.get("files") or {}).values()) + sum(tables.values()) + sum((own or {}).get("files", {}).values())
    raw = ((my.get("quants") or {}).get(sub) or {})
    sha = {Path(k).name: v[1] for k, v in (raw.get("files") or {}).items() if isinstance(v, list) and len(v) > 1}
    sha.update({Path(k).name: v[1] for k, v in tier_files.items() if isinstance(v, list) and len(v) > 1})
    sha.update({Path(k).name: v[1] for k, v in ((raw.get("drafter") or {}).get("files") or {}).items()
                if isinstance(v, list) and len(v) > 1})
    row = {"family": "deepseek", "setup_id": setup_id, "tier": tier, "quant": sub, "revision": my.get("revision", ""),
           "size_gb": round(size / 1e9, 1) if size else 0.0,
           "memory_gb": round(float(_quiet(maya.ds4_memory_gb, key) or 0)),
           "published": bool(m), "repo": my.get("repo", ""), "folder_name": repo_folder, "subfolder": sub,
           "hf": _hf(my.get("repo", ""), my.get("revision", ""), includes, f"<フォルダ>/{repo_folder}")}
    if folder is not None:
        d = folder / sub
        row["model_dir"] = str(d)
        row["models_dir"] = str(folder.parent)
        row["repo_root"] = str(folder)
        row["files"] = _files(d, dict(m.get("files") or {}), what="model", sha=sha)
        row["files"] += _files(d, tables, what="tables", sha=sha)
        if own:
            row["files"] += _files(folder / "dspark", dict(own["files"]), required=False, what="drafter", sha=sha)
        row["hf"] = _hf(my.get("repo", ""), my.get("revision", ""), includes, str(folder))
        if folder.name != repo_folder:
            row["folder_error"] = f"DeepSeek はフォルダ名を {repo_folder} にしてください（Maya の n-gram 表をそこから読みます）"
    return row


def mm3_entry(maya, key: str, tier: str, setup_id: str, folder: Path | None) -> dict:
    man = json.loads((maya.ROOT / "tools" / "mm3" / "download_gguf.json").read_text(encoding="utf-8"))
    name = maya.MM3_QUANTS[key]
    q = (man.get("quants") or {}).get(name) or {}
    files = {k: v[0] for k, v in (q.get("files") or {}).items()}
    sha = {Path(k).name: v[1] for k, v in (q.get("files") or {}).items() if isinstance(v, list) and len(v) > 1}
    sub = Path(next(iter(files))).parent.name if files else tier
    row = {"family": "minimax", "setup_id": setup_id, "tier": tier, "quant": name,
           "size_gb": round(sum(files.values()) / 1e9, 1), "memory_gb": int(q.get("memory_gb") or 0),
           "published": bool(files), "repo": man.get("repo", ""), "folder_name": "", "subfolder": sub,
           "revision": man.get("revision", ""),
           "hf": _hf(man.get("repo", ""), man.get("revision", ""), [f"{sub}/*"], "<フォルダ>")}
    if folder is not None:
        d = folder / sub
        row["model_dir"] = str(d)
        row["models_dir"] = str(folder.parent)
        row["repo_root"] = str(folder)
        row["files"] = _files(d, {Path(k).name: v for k, v in files.items()}, what="model", sha=sha)
        row["hf"] = _hf(man.get("repo", ""), man.get("revision", ""), [f"{sub}/*"], str(folder))
    return row


def hy4_entry(maya, key: str, tier: str, setup_id: str, folder: Path | None) -> dict:
    """Hy4-Flash（Tencent、エンジン maya-hy4）: tools/hy4/download.json（maya.HY4_MANIFEST）から読む。
    Maya 本体 maya.py の hy4_entry と同じ見方: 各サイズ 2 シャード（00001 = モデル本体、00002 = n-gram
    表）。n-gram 表は全サイズで同じ 1 ファイルで XS のフォルダに在る。S・M・L の setup はそれを
    本体の hy4_places（--models-dir 直下の正式名フォルダ Hy4-Flash-Maya-* か、登録済み Hy4 の
    config の model_dir）から hard-link する — hf download が作る <folder>/Maya-XS を拾うには、XS を
    先に登録しておく（その config の model_dir が XS のフォルダ）が最短。mmproj（画像、全サイズ共通）も
    XS のフォルダに在り、本体はモデル自身のフォルダの mmproj を 1 個見つけて使う: S・M・L では手で置く。
    Tencent 自身の Q4_K_M（others）は QUANTS に載らないので出さない。"""
    man = json.loads(maya.HY4_MANIFEST.read_text(encoding="utf-8"))
    q = (man.get("quants") or {}).get(maya.HY4_QUANTS.get(key, key)) or {}
    trunk = {k: v for k, v in (q.get("files") or {}).items()}
    ngram = next(iter((man.get("ngram") or {}).items()), ("", [0, ""]))
    mmproj = next(iter((man.get("vision") or {}).items()), ("", [0, ""]))
    sub = Path(next(iter(trunk), "")).parent.name or tier
    includes = [f"{sub}/*"] + [ngram[0]] * bool(ngram[0] and sub != Path(ngram[0]).parent.name)
    values = list(trunk.values()) + ([ngram[1]] if ngram[0] else [])
    row = {"family": "hy4", "setup_id": setup_id, "tier": tier, "quant": key,
           "size_gb": round(sum(v[0] for v in values) / 1e9, 1), "memory_gb": int(q.get("memory_gb") or 0),
           "ngram_gb": round(ngram[1][0] / 1e9, 1) if ngram[0] else 0.0,
           "published": bool(trunk and ngram[0] and man.get("revision")
                             and all(isinstance(v, list) and len(v) == 2 and v[0] and v[1] for v in values)),
           "repo": man.get("repo", ""), "folder_name": "", "subfolder": sub, "revision": man.get("revision", ""),
           "hf": _hf(man.get("repo", ""), man.get("revision", ""), includes, "<フォルダ>")}
    if folder is not None:
        row["model_dir"] = str(folder / sub)
        row["models_dir"] = str(folder)
        row["repo_root"] = str(folder)
        row["files"] = _files(folder / sub, {Path(k).name: v[0] for k, v in trunk.items()},
                              what="model", sha={Path(k).name: v[1] for k, v in trunk.items()})
        if ngram[0]:      # 全サイズで同じ 1 ファイル（XS のフォルダに置く）
            row["files"] += _files(folder / Path(ngram[0]).parent, {Path(ngram[0]).name: ngram[1][0]},
                                   what="tables", sha={Path(ngram[0]).name: ngram[1][1]})
        if mmproj[0]:     # 画像の部品（全サイズ共通、XS のフォルダ。無ければテキストだけで登録）
            row["files"] += _files(folder / Path(mmproj[0]).parent, {Path(mmproj[0]).name: mmproj[1][0]},
                                   required=False, what="vision", sha={Path(mmproj[0]).name: mmproj[1][1]})
        row["hf"] = _hf(man.get("repo", ""), man.get("revision", ""), includes, str(folder))
    return row


def blockers(family: str) -> list[str]:
    """Things the setup would have to download (and so must not run without the owner's go-ahead)."""
    out = []
    if family == "deepseek":
        try:
            import tokenizers  # noqa: F401
        except ImportError:
            out.append("Maya の Python に tokenizers がありません（DeepSeek の pack に要ります）。"
                       "設定 → 機能と拡張 → Project Maya（R9700）の「更新」で入れてください")
    return out


def main(argv: list[str]) -> int:
    engine = Path(argv[1]).resolve()
    folder = Path(argv[2]).resolve() if len(argv) > 2 and argv[2] not in ("", "-") else None
    sys.path.insert(0, str(engine))
    with contextlib.redirect_stdout(io.StringIO()):
        import maya  # type: ignore[import-not-found]
        from tools import maya_quants as Q  # type: ignore[import-not-found]
    rows = []
    build = {"maya": (engine / "build-hip" / "maya").is_file(),
             "maya-ds4": (engine / "build-hip" / "maya-ds4").is_file()}
    for family, key, tier, setup_id in Q.QUANTS:
        maker = {"glm": glm_entry, "deepseek": ds4_entry, "minimax": mm3_entry, "hy4": hy4_entry}.get(family)
        if maker is None:
            continue
        try:
            row = maker(maya, key, tier, setup_id, folder)
        except Exception as exc:  # noqa: BLE001 - one damaged entry must not hide the others
            row = {"family": family, "setup_id": setup_id, "tier": tier, "quant": key, "error": str(exc)[:200]}
        row["model"] = FAMILY_LABEL.get(family, family)
        row["blockers"] = blockers(family)
        rows.append(row)
    print(json.dumps({"version": (engine / "VERSION").read_text(encoding="utf-8").strip()
                      if (engine / "VERSION").is_file() else "", "build": build, "quants": rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
