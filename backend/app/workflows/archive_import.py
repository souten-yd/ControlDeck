"""チャットに添付した圧縮ファイル（ZIP・7z・RAR・tar）を、OpenCode のプロジェクトへ安全に展開する。

2026-11-18 利用者の依頼「OpenCode の開発中に ZIP や RAR を添付し、中身がソースコードならプログラムのフォルダに
置いて参照できるように。Python などのコードを送って、読み解いて改良・編集・別のプログラムやウェブアプリに」。

安全のための決まり（外から来たファイルとして扱う）:
* 添付の時点で中身の一覧を確かめ（inspect）、展開する前に断る: 絶対パス・`..`・Windows のドライブ指定・
  NUL を含む名前、シンボリックリンク・ハードリンク・デバイス、展開後の合計 MAX_TOTAL_BYTES 超、
  MAX_FILES 超。
* 展開は一時フォルダへ行い、終わってから中身をもう一度確かめて（リンクは消す・外へ出ていない）から移す。
* 7z と RAR は 7z コマンドで展開する（引数は配列で渡し、シェルを通さない。時間の上限つき）。
  ZIP と tar は Python の標準ライブラリで、1 ファイルずつ大きさを数えながら書く。
* 展開先は呼び出し側が決めた OpenCode のプロジェクトの中だけ（resolve で確かめる）。既存のファイルは上書きしない。
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 20_000
SEVEN_ZIP_TIMEOUT = 600
TREE_LINES = 200
# 長いものから順に（.tar.gz を .gz より先に）
SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar", ".zip", ".7z", ".rar")
# 展開後に要らないもの（OpenCode に読ませない）
SKIP_NAMES = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


class ArchiveError(ValueError):
    pass


def suffix_of(name: str) -> str | None:
    lower = name.lower()
    return next((s for s in SUFFIXES if lower.endswith(s)), None)


def _check_name(name: str) -> str | None:
    """安全な相対パス（POSIX）を返す。ディレクトリだけ・不要なものは None。危ないものは ArchiveError。"""
    raw = name.replace("\\", "/")
    if "\x00" in raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ArchiveError(f"安全でないパスが含まれています: {name[:120]}")
    parts = [p for p in PurePosixPath(raw).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveError(f"親フォルダへ出るパスが含まれています: {name[:120]}")
    if not parts or any(p in SKIP_NAMES for p in parts):
        return None
    return "/".join(parts)


def _seven_zip() -> str:
    for name in ("7z", "7za", "7zz"):
        path = shutil.which(name)
        if path:
            return path
    raise ArchiveError("7z と RAR の展開には 7z コマンドが要ります（p7zip / 7zip を入れてください）")


def _list_7z(path: Path) -> list[tuple[str, int, bool, bool]]:
    """(名前, 大きさ, ディレクトリか, リンク・特殊ファイルか)"""
    result = subprocess.run([_seven_zip(), "l", "-slt", "-ba", "-p", str(path)], capture_output=True, text=True,
                            timeout=SEVEN_ZIP_TIMEOUT, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["読み取れません"]
        if "password" in (result.stderr + result.stdout).lower() or "Wrong password" in result.stdout:
            raise ArchiveError("パスワード付きの圧縮ファイルは展開できません")
        raise ArchiveError(f"圧縮ファイルを読めません: {detail[0][:200]}")
    entries: list[tuple[str, int, bool, bool]] = []
    block: dict[str, str] = {}
    for line in [*result.stdout.splitlines(), ""]:
        if not line.strip():
            if "Path" in block:
                attrs = block.get("Attributes", "")
                mode = attrs.split()[-1] if " " in attrs else ""
                is_dir = attrs.startswith("D") or block.get("Folder") == "+"
                special = bool(mode) and mode[0] not in ("-", "d")   # l=リンク, c/b/p/s=特殊
                if block.get("Encrypted") == "+":
                    raise ArchiveError("パスワード付きの圧縮ファイルは展開できません")
                entries.append((block["Path"], int(block.get("Size") or 0), is_dir, special))
            block = {}
            continue
        key, _, value = line.partition(" = ")
        block[key.strip()] = value
    return entries


def _entries(path: Path, suffix: str) -> list[tuple[str, int, bool, bool]]:
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                out = []
                for info in archive.infolist():
                    if info.flag_bits & 0x1:
                        raise ArchiveError("パスワード付きの圧縮ファイルは展開できません")
                    # Unix で作った ZIP だけ種類のビットを見る（0 = 種類の情報なし = 通常のファイル）
                    kind = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
                    special = kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                    out.append((info.filename, info.file_size, info.is_dir(), special))
                return out
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"ZIP として読めません: {exc}") from exc
    if suffix in (".7z", ".rar"):
        return _list_7z(path)
    try:
        with tarfile.open(path) as archive:
            return [(m.name, m.size, m.isdir(), not (m.isfile() or m.isdir())) for m in archive.getmembers()]
    except tarfile.TarError as exc:
        raise ArchiveError(f"tar として読めません: {exc}") from exc


def inspect(path: Path, name: str) -> dict:
    """展開する前の確認。危ないもの・大きすぎるものは ArchiveError。"""
    suffix = suffix_of(name)
    if suffix is None:
        raise ArchiveError("ZIP・7z・RAR・tar（.gz・.bz2・.xz）を添付してください")
    files = 0
    total = 0
    for entry_name, size, is_dir, special in _entries(path, suffix):
        if special:
            raise ArchiveError(f"リンクや特殊なファイルが含まれています: {entry_name[:120]}")
        safe = _check_name(entry_name)
        if safe is None or is_dir:
            continue
        files += 1
        total += max(0, size)
        if files > MAX_FILES:
            raise ArchiveError(f"ファイルが多すぎます（{MAX_FILES:,} 個まで）")
        if total > MAX_TOTAL_BYTES:
            raise ArchiveError(f"展開後が大きすぎます（{MAX_TOTAL_BYTES / 2**30:g} GiB まで）")
    if files == 0:
        raise ArchiveError("中にファイルがありません")
    return {"files": files, "bytes": total, "suffix": suffix}


def _copy_limited(source, target: Path, budget: list[int]) -> None:
    with target.open("wb") as out:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise ArchiveError(f"展開後が大きすぎます（{MAX_TOTAL_BYTES / 2**30:g} GiB まで）")
            out.write(chunk)


def _extract_to(path: Path, suffix: str, tmp: Path) -> None:
    budget = [MAX_TOTAL_BYTES]   # 宣言された大きさを信じず、書いた量で数える（圧縮爆弾）
    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                safe = _check_name(info.filename)
                if safe is None or info.is_dir():
                    continue
                target = tmp / safe
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source:
                    _copy_limited(source, target, budget)
        return
    if suffix in (".7z", ".rar"):
        result = subprocess.run([_seven_zip(), "x", "-y", "-bd", "-p", f"-o{tmp}", str(path)],
                                capture_output=True, text=True, timeout=SEVEN_ZIP_TIMEOUT, check=False)
        if result.returncode not in (0, 1):   # 1 = 警告（読めない属性など）
            raise ArchiveError(f"展開に失敗しました: {(result.stderr or result.stdout).strip()[-200:]}")
        return
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            safe = _check_name(member.name)
            if safe is None or not member.isfile():
                continue
            target = tmp / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is not None:
                with source:
                    _copy_limited(source, target, budget)


def _sanitize_tree(tmp: Path) -> None:
    """展開後の確認: リンク・特殊ファイルは消す、外へ出ていない、合計の大きさと数。"""
    root = tmp.resolve()
    files = 0
    total = 0
    for current, dirs, names in os.walk(tmp, followlinks=False):
        for name in [*dirs, *names]:
            item = Path(current) / name
            if item.is_symlink() or (not item.is_dir() and not item.is_file()):
                item.unlink(missing_ok=True)
                continue
            if name in SKIP_NAMES:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
                continue
            if not item.resolve().is_relative_to(root):
                raise ArchiveError("展開先の外へ出るファイルがありました")
            if item.is_file():
                files += 1
                total += item.stat().st_size
        dirs[:] = [d for d in dirs if (Path(current) / d).is_dir() and d not in SKIP_NAMES]
    if files > MAX_FILES or total > MAX_TOTAL_BYTES:
        raise ArchiveError("展開後が大きすぎます")


def _single_top(tmp: Path) -> Path:
    """中身が 1 つのフォルダだけなら、その中を展開先の直下にする（project/project/... を作らない）。"""
    items = [p for p in tmp.iterdir()]
    return items[0] if len(items) == 1 and items[0].is_dir() else tmp


def _is_empty_project(project: Path) -> bool:
    ignore = {".git", ".opencode", ".gitignore", "AGENTS.md", "README.md"}
    return not any(p.name not in ignore and not p.name.startswith(".import-") for p in project.iterdir())


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{i}")
        if not candidate.exists():
            return candidate
    raise ArchiveError("展開先の名前を決められません")


def _folder_name(name: str) -> str:
    suffix = suffix_of(name) or ""
    stem = name[: len(name) - len(suffix)] if suffix else name
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in stem).strip(".-") or "archive"
    return safe[:60]


def extract_into_project(path: Path, name: str, project: Path) -> dict:
    """プロジェクトへ展開する。空のプロジェクトなら直下へ、そうでなければ imports/<名前>/ へ。

    返り値: {"relative": 展開先（プロジェクトからの相対）, "files": 数, "bytes": 合計, "tree": 一覧, "languages": 拡張子の内訳}
    """
    suffix = suffix_of(name)
    if suffix is None:
        raise ArchiveError("圧縮ファイルではありません")
    inspect(path, name)
    project = project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=project, ignore_cleanup_errors=True) as tmp_name:
        tmp = Path(tmp_name)
        _extract_to(path, suffix, tmp)
        _sanitize_tree(tmp)
        top = _single_top(tmp)
        if _is_empty_project(project) and not any((project / p.name).exists() for p in top.iterdir()):
            dest = project
            for item in list(top.iterdir()):
                shutil.move(str(item), str(dest / item.name))
        else:
            dest = _unique(project / "imports" / _folder_name(name))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(top), str(dest))
    if not dest.resolve().is_relative_to(project):
        raise ArchiveError("展開先がプロジェクトの外です")
    files = sorted(p for p in dest.rglob("*") if p.is_file() and not p.relative_to(dest).parts[0].startswith(".import-"))
    rel = dest.relative_to(project).as_posix() or "."
    tree = [p.relative_to(project).as_posix() for p in files[:TREE_LINES]]
    languages = Counter(p.suffix.lower() or "(拡張子なし)" for p in files).most_common(8)
    return {"relative": rel, "files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "tree": tree, "more": max(0, len(files) - TREE_LINES), "languages": languages}


def prompt_note(name: str, result: dict) -> str:
    """OpenCode への依頼の前に付ける説明。"""
    where = "プロジェクトの直下" if result["relative"] == "." else f"`{result['relative']}/`"
    langs = "・".join(f"{ext} {count}" for ext, count in result["languages"])
    lines = [f"## 添付された圧縮ファイル「{name}」",
             f"{where}に展開しました（{result['files']} ファイル。{langs}）。依頼に取りかかる前に、"
             "主要なファイル（README・設定・入口）を読んで構成を把握してください。元のファイルは消さずに残してください。",
             "", "ファイル一覧:", *[f"- {line}" for line in result["tree"]]]
    if result["more"]:
        lines.append(f"- …ほか {result['more']} ファイル")
    return "\n".join(lines)
