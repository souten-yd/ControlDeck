"""テキストのチャンク分割ストラテジ群。

RAG の精度はチャンク設計で大きく変わるため、一般的な戦略を網羅する:
- fixed: 文字数固定 + オーバーラップ
- recursive: 区切り階層（段落→行→文→語）で目標サイズに寄せる
- sentence: 文単位で目標サイズにまとめる
- paragraph: 空行区切りの段落
- markdown: 見出し(#)単位。親見出しをパンくずとして各チャンクへ付与
- parent_child: 子（小）で検索し親（大）を文脈として返すための階層分割
    - parent_mode="paragraph": 段落/大ブロックを親、その中を子に分割
    - parent_mode="full_doc": ドキュメント全体を親、全体を子に分割

戻り値:
- フラット戦略: list[Chunk]（parent=None）
- parent_child: list[Chunk]（各子が parent テキストを保持）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    parent: str | None = None  # parent_child のとき親テキスト
    metadata: dict = field(default_factory=dict)


_SENT_RE = re.compile(r"(?<=[。．.!?！？\n])\s*")


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def fixed_chunks(text: str, size: int, overlap: int) -> list[str]:
    text = _clean(text)
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    step = max(1, size - overlap)
    while start < len(text):
        out.append(text[start : start + size])
        start += step
    return out


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENT_RE.split(text) if s.strip()]


def sentence_chunks(text: str, size: int, overlap: int) -> list[str]:
    sents = _split_sentences(_clean(text))
    out: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) > size:
            out.append(cur.strip())
            # オーバーラップ: 末尾の overlap 文字を次チャンク先頭へ
            cur = (cur[-overlap:] if overlap else "") + s
        else:
            cur += s
    if cur.strip():
        out.append(cur.strip())
    return out


def paragraph_chunks(text: str, size: int) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", _clean(text)) if p.strip()]
    out: list[str] = []
    for p in paras:
        if len(p) <= size:
            out.append(p)
        else:
            out.extend(fixed_chunks(p, size, size // 8))
    return out


def recursive_chunks(text: str, size: int, overlap: int) -> list[str]:
    """区切り階層で分割し、size を超えないよう再帰的に細かくする。"""
    text = _clean(text)
    seps = ["\n\n", "\n", "。", ". ", "！", "？", "!", "?", " ", ""]

    def split(t: str, seps_i: int) -> list[str]:
        if len(t) <= size or seps_i >= len(seps):
            return [t]
        sep = seps[seps_i]
        parts = list(t) if sep == "" else t.split(sep)
        merged: list[str] = []
        cur = ""
        for part in parts:
            piece = part if sep == "" else (part + sep)
            if cur and len(cur) + len(piece) > size:
                merged.append(cur)
                cur = piece
            else:
                cur += piece
        if cur:
            merged.append(cur)
        out: list[str] = []
        for m in merged:
            out.extend(split(m, seps_i + 1) if len(m) > size else [m])
        return out

    raw = [c.strip() for c in split(text, 0) if c.strip()]
    if not overlap or len(raw) <= 1:
        return raw
    # 隣接オーバーラップを付与
    out = []
    for i, c in enumerate(raw):
        prev_tail = raw[i - 1][-overlap:] if i > 0 else ""
        out.append((prev_tail + c) if prev_tail else c)
    return out


def markdown_chunks(text: str, size: int) -> list[Chunk]:
    """見出し単位で分割。親見出しをパンくずとしてメタデータ+本文先頭に付与。"""
    text = _clean(text)
    lines = text.split("\n")
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        crumb = " > ".join(t for _, t in stack)
        prefix = f"[{crumb}]\n" if crumb else ""
        for piece in (fixed_chunks(body, size, size // 8) if len(body) > size else [body]):
            chunks.append(Chunk(text=prefix + piece, metadata={"heading": crumb}))

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            buf = []
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    return chunks


def parent_child_chunks(
    text: str, parent_mode: str, parent_size: int, child_size: int, overlap: int
) -> list[Chunk]:
    text = _clean(text)
    if parent_mode == "full_doc":
        parents = [text]
    else:  # paragraph
        parents = paragraph_chunks(text, parent_size)
    out: list[Chunk] = []
    for parent in parents:
        children = recursive_chunks(parent, child_size, overlap)
        for child in children:
            out.append(Chunk(text=child, parent=parent))
    return out


def chunk(text: str, config: dict) -> list[Chunk]:
    """設定に従ってチャンク列を返す。config.strategy で切替。"""
    strategy = str(config.get("strategy", "recursive"))
    size = int(config.get("size", 800) or 800)
    overlap = int(config.get("overlap", 100) or 0)
    if not _clean(text):
        return []
    if strategy == "fixed":
        return [Chunk(t) for t in fixed_chunks(text, size, overlap)]
    if strategy == "sentence":
        return [Chunk(t) for t in sentence_chunks(text, size, overlap)]
    if strategy == "paragraph":
        return [Chunk(t) for t in paragraph_chunks(text, size)]
    if strategy == "markdown":
        return markdown_chunks(text, size)
    if strategy == "parent_child":
        return parent_child_chunks(
            text,
            parent_mode=str(config.get("parent_mode", "paragraph")),
            parent_size=int(config.get("parent_size", 2000) or 2000),
            child_size=size,
            overlap=overlap,
        )
    # 既定: recursive
    return [Chunk(t) for t in recursive_chunks(text, size, overlap)]


STRATEGIES = ["recursive", "fixed", "sentence", "paragraph", "markdown", "parent_child"]
