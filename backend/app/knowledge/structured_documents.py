"""Offline structure candidates. No subprocess, network, DB, or ingestion hook."""
from __future__ import annotations

import hashlib
import io
import math
import xml.etree.ElementTree as ET
import zipfile

MAX_XML = 8 * 1024 * 1024
MAX_ZIP = 16 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_NODES = 100_000
MAX_BLOCKS = 10_000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
H = "{http://www.w3.org/1999/xhtml}"
POPPLER_DTD = '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'


def _xml(data: bytes, *, poppler: bool = False):
    if len(data) > MAX_XML:
        raise ValueError("XML size limit")
    text = data.decode("utf-8-sig")
    if poppler and text.startswith(POPPLER_DTD):
        text = text[len(POPPLER_DTD):]
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations not supported")
    root = ET.fromstring(text)
    if sum(1 for _ in root.iter()) > MAX_NODES:
        raise ValueError("XML node limit")
    return root


def _bbox(node):
    values = tuple(float(node.attrib[k]) for k in ("xMin", "yMin", "xMax", "yMax"))
    if not all(math.isfinite(v) for v in values) or values[0] > values[2] or values[1] > values[3]:
        raise ValueError("invalid bounding box")
    return values


def pdf_bbox_blocks(data: bytes) -> dict:
    """Parse already-produced Poppler -bbox-layout XHTML; coordinates are PDF points.

    Hash identifies the XHTML, not the original PDF. No reading-order guarantee,
    table inference, OCR, or figure recognition is claimed.
    """
    root = _xml(data, poppler=True)
    doc = root.find(f"{H}body/{H}doc")
    if root.tag != H + "html" or doc is None:
        raise ValueError("not Poppler bbox layout")
    pages = doc.findall(H + "page")
    if not 1 <= len(pages) <= 400:
        raise ValueError("page limit")
    blocks, empty = [], []
    for page_no, page in enumerate(pages, 1):
        size = tuple(float(page.attrib[k]) for k in ("width", "height"))
        if not all(math.isfinite(v) and v > 0 for v in size):
            raise ValueError("invalid page size")
        found = page.findall(f"{H}flow/{H}block")
        if not found:
            empty.append(page_no)
        for index, block in enumerate(found):
            lines = []
            for line in block.findall(H + "line"):
                words = [{"text": word.text or "", "bbox": _bbox(word)} for word in line.findall(H + "word")]
                lines.append({"bbox": _bbox(line), "words": words})
            blocks.append({"kind": "pdf_text_block", "page": page_no, "index": index,
                           "page_size": size, "bbox": _bbox(block), "lines": lines})
            if len(blocks) > MAX_BLOCKS:
                raise ValueError("block limit")
    return {"format": "pdf_bbox_layout", "layout_sha256": hashlib.sha256(data).hexdigest(),
            "page_count": len(pages), "empty_pages": empty, "blocks": blocks,
            "limitations": ["no_ocr", "no_table_inference", "no_figures", "reading_order_unverified"]}


def _docx_xml(data: bytes):
    if len(data) > MAX_ZIP:
        raise ValueError("archive size limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or sum(i.file_size for i in entries) > MAX_EXPANDED:
            raise ValueError("archive expansion limit")
        names = [i.filename for i in entries]
        if len(set(names)) != len(names) or "word/document.xml" not in names:
            raise ValueError("invalid document package")
        item = archive.getinfo("word/document.xml")
        if item.file_size > MAX_XML or item.flag_bits & 1:
            raise ValueError("unsupported document part")
        with archive.open(item) as stream:
            raw = stream.read(MAX_XML + 1)
    return _xml(raw)


def _paragraph(node):
    # Only direct runs and direct hyperlink runs; no field evaluation or relationship lookup.
    parts = []
    for child in node:
        runs = [child] if child.tag == W + "r" else child.findall(W + "r") if child.tag == W + "hyperlink" else []
        for run in runs:
            for piece in run:
                if piece.tag == W + "t":
                    parts.append(piece.text or "")
                elif piece.tag == W + "tab":
                    parts.append("\t")
                elif piece.tag in (W + "br", W + "cr"):
                    parts.append("\n")
    style = node.find(f"{W}pPr/{W}pStyle")
    return {"text": "".join(parts), "style_id": style.get(W + "val") if style is not None else None}


def docx_blocks(data: bytes) -> dict:
    """Read native paragraph and physical table cells, never execute Office content."""
    root = _docx_xml(data)
    body = root.find(W + "body")
    if root.tag != W + "document" or body is None:
        raise ValueError("unsupported Word document")
    blocks = []
    unsupported = set()
    tags = {W + t for t in ("ins", "del", "drawing", "pict", "object", "sdt", "fldSimple", "fldChar", "instrText", "altChunk")}
    for node in body.iter():
        if node.tag in tags or node.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}"):
            unsupported.add(node.tag.rsplit("}", 1)[-1])
    for index, node in enumerate(body):
        if node.tag == W + "p":
            blocks.append({"kind": "paragraph", "index": index, **_paragraph(node)})
        elif node.tag == W + "tbl":
            rows = []
            for row in node.findall(W + "tr"):
                cells = []
                for cell in row.findall(W + "tc"):
                    span = cell.find(f"{W}tcPr/{W}gridSpan")
                    merge = cell.find(f"{W}tcPr/{W}vMerge")
                    if cell.find(W + "tbl") is not None:
                        unsupported.add("nested_table")
                    cells.append({"paragraphs": [_paragraph(p) for p in cell.findall(W + "p")],
                                  "grid_span_raw": span.get(W + "val") if span is not None else None,
                                  "vertical_merge_raw": merge.get(W + "val", "continue") if merge is not None else None})
                rows.append(cells)
            blocks.append({"kind": "table", "index": index, "rows": rows})
        elif node.tag != W + "sectPr":
            unsupported.add(node.tag.rsplit("}", 1)[-1])
        if len(blocks) > MAX_BLOCKS:
            raise ValueError("block limit")
    return {"format": "docx", "source_sha256": hashlib.sha256(data).hexdigest(), "blocks": blocks,
            "unsupported_detected": sorted(unsupported),
            "limitations": ["main_body_only", "physical_cells_not_logical_grid", "no_field_evaluation", "no_layout_or_visibility_resolution"]}
