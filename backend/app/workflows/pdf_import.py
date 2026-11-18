"""AI アシスタントに添付した PDF を保存し、OpenCode が読める形でプロジェクトへ置く。

2026-11-18 利用者「PDF を添付したが全く読まれていない」「PDF は画像認識して欲しい」。
本番の Plan15.pdf は pypdf の結果が `/uni30E6/uni30CB…` のままで、原本も残していなかった。
原本・文字・ページの PNG を会話の添付に持ち、OpenCode へ送るときにプロジェクトの
`.controldeck/attachments/` へ置く。PNG は OpenCode の read で画像としてモデルへ渡る。

プロジェクトはモデルが書き換えられる場所なので、置くときはリンクをたどらない（箱の外へ書かせない）。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_PAGES = 40
LONG_SIDE_PX = 1600
RENDER_TIMEOUT_S = 180
_GLYPH_NAME = re.compile(r"/uni([0-9A-Fa-f]{4})")
_DOC_ID = re.compile(r"^[0-9a-f]{16}$")
_UNSAFE_NAME = re.compile(r"[^\w.-]+", re.UNICODE)


def repair_text(text: str) -> str:
    """埋め込みフォントの字形名（/uni30E6）のまま取り出された文字を元に戻す。"""
    def replace(match: re.Match[str]) -> str:
        code = int(match.group(1), 16)
        return match.group(0) if 0xD800 <= code <= 0xDFFF else chr(code)

    return _GLYPH_NAME.sub(replace, text)


def extract(data: bytes) -> tuple[str, int]:
    """PDF の文字とページ数。画像だけの PDF は文字が空になる。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return repair_text("\n\n".join((page.extract_text() or "") for page in reader.pages)), len(reader.pages)


def save(folder: Path, name: str, data: bytes, text: str, pages: int) -> str:
    """原本と文字を保存する。同じ内容の PDF は 1 つにまとめる。"""
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}.pdf").write_bytes(data)
    (folder / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    (folder / f"{doc_id}.json").write_text(json.dumps({"name": name, "pages": pages}, ensure_ascii=False), encoding="utf-8")
    return doc_id


def saved(folder: Path) -> list[dict]:
    """保存した PDF（古い順）。"""
    items = []
    if not folder.is_dir():
        return items
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if not _DOC_ID.fullmatch(path.stem) or not path.with_suffix(".pdf").is_file():
            continue
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            items.append({"id": path.stem, "name": str(info["name"]), "pages": int(info["pages"])})
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("PDF の情報を読めません: %s", path.name)
    return items


def brief(folder: Path, limit: int = 1500) -> str:
    """調査の計画に渡す、添付資料の名前と冒頭（Plan15 が何かを取り違えないように）。"""
    lines: list[str] = []
    items = saved(folder)
    each = max(120, limit // max(1, len(items)))
    for item in items:
        try:
            head = " ".join((folder / f"{item['id']}.txt").read_text(encoding="utf-8").split())[:each]
        except OSError:
            head = ""
        lines.append(f"- {item['name']}: {head}")
    return "\n".join(lines)[:limit]


def render(folder: Path, doc_id: str) -> list[Path]:
    """ページを PNG にする（作ったものは使い回す）。pdftoppm が無ければ空。"""
    target = folder / f"{doc_id}-pages"
    existing = sorted(target.glob("page-*.png"))
    if existing:
        return existing
    tool = shutil.which("pdftoppm")
    if not tool:
        logger.warning("pdftoppm が無いため PDF をページの画像にできません")
        return []
    work = folder / f".{doc_id}-{uuid.uuid4().hex[:8]}"
    work.mkdir()
    try:
        subprocess.run([tool, "-png", "-f", "1", "-l", str(MAX_PAGES), "-scale-to", str(LONG_SIDE_PX),
                        str(folder / f"{doc_id}.pdf"), str(work / "page")],
                       check=True, capture_output=True, timeout=RENDER_TIMEOUT_S)
        made = sorted(work.glob("page-*.png"))
        if not made:
            return []
        target.mkdir(exist_ok=True)
        for index, path in enumerate(made, 1):
            path.replace(target / f"page-{index:03d}.png")
        return sorted(target.glob("page-*.png"))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("PDF をページの画像にできませんでした: %s", type(exc).__name__)
        return []
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _open_dir(name: str, parent: int) -> int:
    """リンクをたどらずにフォルダを開く（無ければ作る）。"""
    try:
        os.mkdir(name, 0o755, dir_fd=parent)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _copy_into(source: Path, name: str, parent: int) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
    with os.fdopen(fd, "wb") as out, source.open("rb") as src:
        shutil.copyfileobj(src, out)


def _folder_name(name: str, doc_id: str) -> str:
    stem = _UNSAFE_NAME.sub("_", Path(name).stem).strip("._")[:48] or "document"
    return f"{stem}-{doc_id[:8]}"


def install(folder: Path, item: dict, project: Path) -> dict:
    """プロジェクトへ置く。`new` は今回置いたか。途中で失敗しても半端な置き場を残さない。"""
    doc_id = item["id"]
    pages = render(folder, doc_id)
    dest = _folder_name(item["name"], doc_id)
    relative = f".controldeck/attachments/{dest}"
    fds: list[int] = []
    tmp = f".tmp-{uuid.uuid4().hex[:12]}"
    try:
        fds.append(os.open(project, os.O_RDONLY | os.O_DIRECTORY))
        fds.append(_open_dir(".controldeck", fds[-1]))
        try:
            ignore = os.open(".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fds[-1])
            with os.fdopen(ignore, "w") as out:
                out.write("*\n")
        except FileExistsError:
            pass
        fds.append(_open_dir("attachments", fds[-1]))
        attachments = fds[-1]
        new = False
        try:
            os.stat(dest, dir_fd=attachments, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir(tmp, 0o755, dir_fd=attachments)
            work = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=attachments)
            fds.append(work)
            _copy_into(folder / f"{doc_id}.pdf", "source.pdf", work)
            _copy_into(folder / f"{doc_id}.txt", "text.txt", work)
            for page in pages:
                _copy_into(page, page.name, work)
            os.rename(tmp, dest, src_dir_fd=attachments, dst_dir_fd=attachments)
            new = True
    except BaseException:
        _remove_tmp(fds, tmp)
        raise
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return {"name": item["name"], "relative": relative, "pages": item["pages"], "images": len(pages), "new": new}


def _remove_tmp(fds: list[int], tmp: str) -> None:
    """置きかけのフォルダを片付ける（attachments の fd が開けていれば）。"""
    if len(fds) < 3:
        return
    attachments = fds[2]
    try:
        work = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=attachments)
    except OSError:
        return
    try:
        for name in os.listdir(work):
            os.unlink(name, dir_fd=work)
    finally:
        os.close(work)
    os.rmdir(tmp, dir_fd=attachments)


def prompt_note(items: list[dict]) -> str:
    """OpenCode への依頼の前に付ける説明。"""
    lines = ["## 添付された PDF",
             "利用者が添付した資料です。取りかかる前に必ず読んでください。図面・写真・表は文字だけでは分からないので、"
             "`page-001.png` などのページ画像を read ツールで開いて、画像として確認してください。"
             "文字は `text.txt` を grep や read で探せます。これらは ControlDeck が置いたものです。"]
    for item in items:
        pages = f"{item['pages']} ページ"
        images = (f"画像は page-001.png〜（{item['images']} 枚）" if item["images"] else "ページ画像は作れませんでした")
        lines.append(f"- {item['name']}（{pages}）: `{item['relative']}/` — {images}、text.txt、source.pdf")
    return "\n".join(lines)
