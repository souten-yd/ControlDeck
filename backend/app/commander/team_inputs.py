"""Nexarch の AI チームへの資料の受け渡し（brief E・2026-11-28）。

「チームリーダーに頼む」に資料を添える: 選んだファイル・フォルダを作業場所の `.nexarch/inputs/` へ
読み取り専用の写しとして置き、チームリーダーにはパスの一覧だけを渡す（大量の資料をプロンプトへ
展開しない）。シンボリックリンクでの資料の公開はしない（箱から見える実体のファイルだけ）。

- 検証: 作業フォルダと同じ決まり（commander.workdir の許可ルート内・realpath・機密禁止の場所は外・リンク逃げ）。
- 上限: 件数・1 件の大きさ・合計・1 フォルダのファイル数（必要以上にコピーしない・ディスクを食い尽くさない）。
- 元資料は書かない・動かさない（読むだけ・写しを置くだけ）。
- 依頼の全文は `.nexarch/request.md` へ（worktree の担い手も読める）。
- worktree への受け渡し: `.nexarch`（request.md・inputs のハードリンク）と、作業場所にあれば
  `frontend/node_modules`・`.venv` の（相対の）リンク（大きいので写さない）。
根拠: 2026-11-28 の依頼（brief E・利用者「大量のファイルを受け渡すときは、場所を指定して読み込ませるなど
工夫できないか」）と docs/PLAN-nexarch-team.md の使いながら見つけた直し
（チームリーダーが worktree へ .nexarch を手で写していた・`env -C backend …` の回避）。
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from app.commander import teams, workdir

logger = logging.getLogger(__name__)

INPUTS_DIR = ".nexarch/inputs"
REQUEST_FILE = ".nexarch/request.md"
INPUTS_MAX = 20                    # 1 チームの資料の件数
INPUT_ITEM_MAX = 200 * 1024 * 1024  # 1 件の大きさ
INPUT_TOTAL_MAX = 1024 * 1024 * 1024  # 合計
INPUT_FILES_MAX = 5000            # 1 フォルダの中のファイル数
PATH_MAX = 4096
CWD_MAX = 200


def clean_input_paths(value: object) -> list[str]:
    """資料のパスの一覧の検証（文字列のリストだけ・制御文字なし・重複はまとめる）。"""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise teams.TeamError("資料は文字列のリストで指定してください")
    if len(value) > INPUTS_MAX:
        raise teams.TeamError(f"資料は {INPUTS_MAX} 件までです")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise teams.TeamError("資料のパスは文字列で指定してください")
        if len(item) > PATH_MAX:
            raise teams.TeamError(f"資料のパスは {PATH_MAX} 文字以内にしてください")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in item):
            raise teams.TeamError("資料のパスに制御文字は使えません")
        if item not in out:
            out.append(item)
    return out


def clean_cwd(value: object) -> str:
    """確認のコマンドの cwd（実行場所の中の相対パス）の形の検証。実行時にも realpath で確かめる。"""
    if not isinstance(value, str):
        raise teams.TeamError("cwd は文字列で指定してください")
    text = value.strip()
    if not text:
        return ""
    if len(text) > CWD_MAX or "\\" in text or text.startswith(("/", "~")):
        raise teams.TeamError("cwd は作業フォルダの中の相対パスで指定してください")
    if "\x00" in text or any(ord(c) < 0x20 for c in text):
        raise teams.TeamError("cwd に制御文字は使えません")
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise teams.TeamError("cwd は作業フォルダの中の相対パスで指定してください")
    return text


def _measure(path: Path) -> tuple[int, int]:
    """(合計バイト, ファイル数)。中のシンボリックリンクは数えない（写さないので）。"""
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    total, count = 0, 0
    for current, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            item = Path(current) / name
            try:
                if item.is_symlink() or not item.is_file():
                    continue
                total += item.stat().st_size
                count += 1
            except OSError:
                continue
    return total, count


def _skip_links(folder: str, names: list[str]) -> list[str]:
    return [n for n in names if (Path(folder) / n).is_symlink()]


def _copy_ro(src: Path, dest: Path) -> None:
    """src（ファイルかフォルダ）を dest へ読み取り専用で写す。中のシンボリックリンクは写さない（たどらない）。

    たどると、許可されたフォルダの中のリンクから許可の外（~/.ssh・data_dir など）の実体が写り、箱と LLM から
    読めてしまう（2026-11-28 Claude のレビューで見つけた）。src そのものは workdir.resolve_path で realpath と
    許可の範囲を確かめ済み。"""
    if src.is_file():
        shutil.copy2(src, dest)
        os.chmod(dest, 0o444)
        return
    shutil.copytree(src, dest, symlinks=True, ignore=_skip_links, copy_function=shutil.copy2)
    for current, dirs, files in os.walk(dest):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
        for name in files:
            target = Path(current) / name
            try:
                if not target.is_symlink():
                    os.chmod(target, 0o444)
            except OSError:
                continue


def _unique_name(name: str, taken: set[str]) -> str:
    base = name or "資料"
    if base not in taken:
        return base
    stem, dot, suffix = base.rpartition(".")
    for n in range(2, 100):
        candidate = f"{stem or base}-{n}{('.' + suffix) if (dot and stem) else ''}"
        if candidate not in taken:
            return candidate
    return base + "-" + os.urandom(2).hex()


def _write_request(root: Path, goal: str) -> None:
    """依頼の全文を .nexarch/request.md へ（tmp → os.replace で原子に）。"""
    nexarch = root / ".nexarch"
    nexarch.mkdir(parents=True, exist_ok=True)
    target = nexarch / "request.md"
    temp = nexarch / ".request.tmp"
    temp.write_text(goal, encoding="utf-8")
    os.chmod(temp, 0o644)
    os.replace(temp, target)


def _existing_bytes(inputs: Path) -> int:
    total, _count = _measure(inputs)
    return total


def publish(workdir_path: str, goal: str, input_paths: object, existing: list[dict] | None = None) -> list[dict]:
    """資料を作業場所の .nexarch/inputs/ へ置いて、板の行（team.inputs）を返す（brief E）。

    既に置いた資料（existing の source）はもう写さない。goal は依頼の全文として request.md へ書く。
    上限（件数・1 件・合計・ファイル数）を超えるものは断る（必要以上にコピーしない）。
    """
    paths = clean_input_paths(input_paths)
    root = workdir.resolve_dir(workdir_path)
    rows = [row for row in (existing or []) if isinstance(row, dict)
            and isinstance(row.get("path"), str) and isinstance(row.get("source"), str)]
    if len(rows) + len(paths) > INPUTS_MAX:
        raise teams.TeamError(f"資料は {INPUTS_MAX} 件までです")
    inputs = root / INPUTS_DIR
    inputs.mkdir(parents=True, exist_ok=True)
    known = {row["source"] for row in rows}
    taken = {row.get("label") for row in rows} | {p.name for p in inputs.iterdir()}
    total = _existing_bytes(inputs)
    for raw in paths:
        try:
            src = workdir.resolve_path(raw)
        except (OSError, ValueError) as exc:
            raise teams.TeamError(f"資料を使えません: {exc}") from exc
        if str(src) in known:
            continue  # 既に置いてある（もう写さない）
        size, files = _measure(src)
        if size > INPUT_ITEM_MAX:
            raise teams.TeamError(f"資料 {src.name} が大きすぎます（{INPUT_ITEM_MAX // (1024 * 1024)}MB まで）")
        if files > INPUT_FILES_MAX:
            raise teams.TeamError(f"資料 {src.name} の中のファイルが多すぎます（{INPUT_FILES_MAX} まで）")
        total += size
        if total > INPUT_TOTAL_MAX:
            raise teams.TeamError(f"資料の合計が大きすぎます（{INPUT_TOTAL_MAX // (1024 * 1024)}MB まで）")
        label = _unique_name(src.name, {t for t in taken if isinstance(t, str)})
        try:
            _copy_ro(src, inputs / label)
        except OSError as exc:
            raise teams.TeamError(f"資料 {src.name} をコピーできませんでした: {exc}") from exc
        rows.append({"path": str(inputs / label), "source": str(src), "label": label})
        known.add(str(src))
        taken.add(label)
    if goal:
        _write_request(root, goal)
    return rows


def _hardlink_tree(src: Path, dest: Path) -> None:
    """inputs を worktree へハードリンクで渡す（同じファイルシステムならディスクを 2 倍にしない・
    実体として見える）。リンクが張れなければ写す。中のリンクはスキップ（inputs にリンクは無いはず）。"""
    dest.mkdir(parents=True, exist_ok=True)
    for current, dirs, files in os.walk(src, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
        rel = Path(current).relative_to(src)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            item = Path(current) / name
            target = dest / rel / name
            if item.is_symlink() or target.exists():
                continue
            try:
                os.link(item, target)
            except OSError:
                try:
                    shutil.copy2(item, target)
                except OSError:
                    continue


def share_worktree(workdir_path: str, wt_path: str) -> None:
    """worktree（仕事ごとの作業場所）へ .nexarch（依頼全文・資料）を渡し、
    frontend/node_modules・.venv は作業場所にあれば（相対の）リンクで渡す（brief E）。

    - 資料はハードリンク（実体・ディスクを 2 倍にしない）。依頼全文（request.md）は小さいので写す。
      シンボリックリンクでの資料の公開はしない。
    - node_modules・.venv は開発の依存で大きいのでリンクで足す（箱でも相対リンクは解決できる）。
    - 既にあるものは上書きしない（壊れにくく・何度呼んでも同じ形）。
    """
    root = Path(workdir_path)
    wt = Path(wt_path)
    source = root / ".nexarch"
    if source.is_dir():
        dest = wt / ".nexarch"
        try:
            same = dest.resolve() == source.resolve()
        except OSError:
            same = False
        if not same:
            dest.mkdir(exist_ok=True)
            for item in source.iterdir():
                if item.is_symlink() or not item.is_file():
                    continue
                target = dest / item.name
                if not target.exists():
                    shutil.copy2(item, target)
            inputs_src = source / "inputs"
            if inputs_src.is_dir():
                dest_inputs = dest / "inputs"
                if not dest_inputs.exists():
                    _hardlink_tree(inputs_src, dest_inputs)
    for rel in ("frontend/node_modules", ".venv"):
        src_dir = root / rel
        if not src_dir.is_dir():
            continue
        target = wt / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(os.path.relpath(src_dir, target.parent), target_is_directory=True)
