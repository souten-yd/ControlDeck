"""PDF の取り込み（docs/research-patent-standards-rag.md §2.5）。特許・規格・一般で共通。

- ページごとの文章: Poppler の pdftotext（-layout）。文字の少ないページは pdftoppm + Tesseract（jpn+eng）で OCR。
- 図: 埋め込み画像（pdfimages）と、図・表・グラフのあるページの画像（pdftoppm、PNG）を原本と別に保存（ハッシュ名）。
- 図・表・グラフの中身: ローカル専用の VLM（vision.analyze）に、図の内容・表（Markdown）・グラフの軸と単位と値を
  書かせる。モデルの推論・未確認として、同じ文書の断片（locator.kind=figure）と注釈に保存する。VLM が使えなければ飛ばす。
- 断片はページ（規格は条項）を locator に持つ。全文は bodies に入れる（全文を入れる回答に使う）。

外部コマンドは配列引数で呼び、時間・大きさに上限を設ける（AGENTS.md 絶対ルール 2）。WebP・レイアウト解析（Docling）は
導入の了承待ち（Pillow・Docling は外部からの取得になる）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.knowledge.pdf")

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PAGES = 400
OCR_MIN_CHARS = 40
MAX_FIGURE_PAGES = 12
MAX_EMBEDDED_IMAGES = 60
MAX_IMAGE_BYTES = 2 * 1024 * 1024
PAGE_DPI = 110
_FIGURE_HINT = re.compile(r"(図\s*[0-9０-９]|表\s*[0-9０-９]|Fig(?:ure)?\.?\s*\d|Table\s*\d|グラフ|波形)", re.I)


class PdfError(ValueError):
    pass


@dataclass
class Page:
    number: int
    text: str
    method: str = "text"          # text / ocr
    has_images: bool = False


@dataclass
class Extracted:
    sha256: str
    page_count: int
    pages: list[Page]
    images: list[dict] = field(default_factory=list)          # {page, png}
    figure_pages: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(f"〔p.{p.number}〕\n{p.text.strip()}" for p in self.pages if p.text.strip())


async def _run(*argv: str, timeout: int = 120, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise PdfError(f"{Path(argv[0]).name} が時間内に終わりませんでした")
    return proc.returncode or 0, out, err


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        from app.features import system_packages

        raise PdfError(f"{tool} がありません。{system_packages.guide('system-ocr')}")
    return path


async def extract(path: Path, *, ocr: bool = True) -> Extracted:
    data = await asyncio.to_thread(path.read_bytes)
    if len(data) > MAX_PDF_BYTES:
        raise PdfError("PDF は 100MiB 以内にしてください")
    if not data.startswith(b"%PDF"):
        raise PdfError("PDF ではありません")
    code, out, err = await _run(_require("pdfinfo"), str(path), timeout=30)
    if code != 0:
        msg = err.decode("utf-8", "replace")
        raise PdfError("パスワード付きの PDF は読めません" if "ncrypt" in msg or "assword" in msg else "PDF を読めませんでした")
    match = re.search(r"^Pages:\s+(\d+)", out.decode("utf-8", "replace"), re.M)
    page_count = int(match.group(1)) if match else 0
    if page_count <= 0:
        raise PdfError("ページがありません")
    if page_count > MAX_PAGES:
        raise PdfError(f"ページが多すぎます（{MAX_PAGES} ページまで）")
    code, out, _ = await _run(_require("pdftotext"), "-layout", "-enc", "UTF-8", str(path), "-", timeout=300)
    texts = out.decode("utf-8", "replace").split("\f") if code == 0 else []
    pages = [Page(i + 1, texts[i] if i < len(texts) else "") for i in range(page_count)]
    image_pages = await _image_pages(path)
    for page in pages:
        page.has_images = page.number in image_pages
    tmp_root = data_dir() / "tmp" / "pdf"
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
        work = Path(tmp)
        if ocr and shutil.which("tesseract"):
            for page in pages:
                if len(re.sub(r"\s", "", page.text)) >= OCR_MIN_CHARS:
                    continue
                png = await render_page(path, page.number, dpi=200, work=work)
                if png is None:
                    continue
                (work / "ocr.png").write_bytes(png)
                code, out, _ = await _run(_require("tesseract"), str(work / "ocr.png"), "-", "-l", "jpn+eng",
                                          timeout=180)
                if code == 0 and out.strip():
                    page.text = out.decode("utf-8", "replace")
                    page.method = "ocr"
        images = await _embedded_images(path, work)
    figure_pages = [p.number for p in pages if p.has_images or _FIGURE_HINT.search(p.text)][:MAX_FIGURE_PAGES]
    return Extracted(hashlib.sha256(data).hexdigest(), page_count, pages, images, figure_pages)


async def _image_pages(path: Path) -> set[int]:
    if not shutil.which("pdfimages"):
        return set()
    code, out, _ = await _run("pdfimages", "-list", str(path), timeout=60)
    pages = set()
    if code == 0:
        for line in out.decode("utf-8", "replace").splitlines()[2:]:
            cols = line.split()
            if len(cols) > 4 and cols[0].isdigit() and cols[2] == "image":
                width, height = int(cols[3]), int(cols[4])
                if width >= 120 and height >= 120:   # 罫線・ロゴのような小さい画像は数えない
                    pages.add(int(cols[0]))
    return pages


async def _embedded_images(path: Path, work: Path) -> list[dict]:
    if not shutil.which("pdfimages"):
        return []
    outdir = work / "img"
    outdir.mkdir()
    code, _, _ = await _run("pdfimages", "-png", "-p", str(path), str(outdir / "i"), timeout=180)
    if code != 0:
        return []
    found = []
    for file in sorted(outdir.glob("i-*.png"))[: MAX_EMBEDDED_IMAGES * 3]:
        m = re.match(r"i-(\d+)-\d+\.png$", file.name)
        size = file.stat().st_size
        if not m or size < 4096 or size > MAX_IMAGE_BYTES:   # 小さな飾り・大きすぎるものは保存しない
            continue
        found.append({"page": int(m.group(1)), "png": file.read_bytes()})
        if len(found) >= MAX_EMBEDDED_IMAGES:
            break
    return found


async def render_page(path: Path, page: int, *, dpi: int = PAGE_DPI, work: Path | None = None) -> bytes | None:
    own = work is None
    if own:
        tmp_root = data_dir() / "tmp" / "pdf"
        tmp_root.mkdir(parents=True, exist_ok=True)
        holder = tempfile.TemporaryDirectory(dir=tmp_root)
        work = Path(holder.name)
    try:
        prefix = work / f"page{page}"
        code, _, _ = await _run(_require("pdftoppm"), "-f", str(page), "-l", str(page), "-r", str(dpi), "-png",
                                "-singlefile", str(path), str(prefix), timeout=120)
        file = prefix.with_suffix(".png")
        if code != 0 or not file.exists():
            return None
        return file.read_bytes()
    finally:
        if own:
            holder.cleanup()


FIGURE_PROMPT = (
    "これは技術文書（特許・規格・技術資料）の 1 ページです。ページにある図・表・グラフ（波形を含む）だけについて、"
    "日本語で次を書いてください。\n"
    "1. 図表ごとに番号（図1・表2 など、読めれば）と種類（回路図・ブロック図・波形・グラフ・表・写真など）\n"
    "2. 図: 何を示しているか。部品・符号・接続・矢印の意味\n"
    "3. 表: 中身を Markdown の表で（読めない値は「不明」）\n"
    "4. グラフ・波形: 横軸と縦軸の量と単位、目盛りの範囲、読み取れる主な値・ピーク・条件\n"
    "読めない・確信がないものは推測と書き、無い値を作らないこと。図表が無ければ「図表なし」とだけ書く。")


async def describe_page(png: bytes, job=None) -> str | None:
    """ローカル専用の VLM でページの図表を書き起こす。使えなければ None。"""
    from app.addon_runtime.ai import _complete_with_host_admission
    from app.models_mgmt.ai_gateway import AITargetUnavailable, require_local_target, resolve_ai_target
    from app.models_mgmt.runtime_provider import RuntimeChatRequest
    from app.patents.service import _JobRequest

    if len(png) > MAX_IMAGE_BYTES:
        return None
    try:
        target = await resolve_ai_target("vision.analyze")
        await asyncio.to_thread(require_local_target, target)
    except (AITargetUnavailable, OSError):
        return None
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    request = RuntimeChatRequest(
        base_url=target.base_url, model=target.model, temperature=0.1, max_tokens=2048, thinking=False,
        disable_thinking=True, timeout_seconds=300, local_only=True,
        messages=[{"role": "user", "content": [{"type": "text", "text": FIGURE_PROMPT},
                                                {"type": "image_url", "image_url": {"url": url}}]}])
    try:
        answer, _ = await _complete_with_host_admission(target, request, _JobRequest(job))
    except Exception as exc:  # noqa: BLE001 - 図の説明は補助。失敗しても取り込みは続ける
        logger.warning("図表の説明に失敗しました: %s", exc)
        return None
    answer = (answer or "").strip()
    return None if not answer or answer.startswith("図表なし") else answer
