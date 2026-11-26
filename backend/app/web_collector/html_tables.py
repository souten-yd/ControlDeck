"""W4a: 取得済み HTML の表構造を保持する。ネットワーク・外部参照・新規依存なし。

セルの座標は0始まり。結合セルは値を複製せず、rowspan/colspanで表す。
元の本文抽出を置き換えない。行列が曖昧な表は確定せず警告する。
"""
from __future__ import annotations

import re
import hashlib
import json

MAX_HTML = 2_000_000
MAX_TABLES = 12
MAX_ROWS = 100
MAX_COLS = 32
MAX_CELLS = 1000
MAX_CELL_TEXT = 500
MAX_TOTAL_CELLS = 1200
MAX_TOTAL_TEXT = 100_000


def _text(tag) -> str:
    if tag is None:
        return ""
    return " ".join(tag.stripped_strings)


def _span(cell, key: str, limit: int) -> int:
    raw = str(cell.get(key, "1"))
    if not re.fullmatch(r"[1-9]\d{0,2}", raw) or int(raw) > limit:
        raise ValueError("unsupported span")
    return int(raw)


def _hidden(tag) -> bool:
    return (tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).casefold() == "true"
            or bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b",
                              str(tag.get("style", "")), re.I)))


def _grid(table) -> dict:
    # Removing a hidden row/cell would shift the grid; reject that table instead.
    if any(_hidden(t) for t in table.find_all(["tr", "td", "th", "thead", "tbody", "tfoot"])):
        raise ValueError("hidden table geometry")
    # Explicit headers= ID associations need a different parser; don't silently
    # replace them with positional guesses.
    if table.find(["td", "th"], attrs={"headers": True}) is not None:
        raise ValueError("explicit header IDs unsupported")
    rows = table.find_all("tr", limit=MAX_ROWS + 1)
    if len(rows) > MAX_ROWS or table.find("table") is not None:
        raise ValueError("nested or oversized table")
    cells = []
    used: set[tuple[int, int]] = set()
    width = 0
    for i, row in enumerate(rows):
        column = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            while (i, column) in used:
                column += 1
            rs, cs = _span(cell, "rowspan", MAX_ROWS), _span(cell, "colspan", MAX_COLS)
            if i + rs > len(rows) or column + cs > MAX_COLS or len(cells) >= MAX_CELLS:
                raise ValueError("table exceeds limits")
            coordinates = {(r, c) for r in range(i, i + rs) for c in range(column, column + cs)}
            if coordinates & used:
                raise ValueError("overlapping cells")
            used.update(coordinates)
            value = _text(cell)
            scope = str(cell.get("scope", ""))
            cells.append({"row": i, "col": column, "rowspan": rs, "colspan": cs,
                          "header": cell.name == "th", "scope": scope if scope in ("row", "col", "rowgroup", "colgroup") else "",
                          "text": value[:MAX_CELL_TEXT], "truncated": len(value) > MAX_CELL_TEXT})
            column += cs
            width = max(width, column)
    if not cells or not any(c["header"] for c in cells):
        raise ValueError("no explicit headers")
    # 欠けたセルの位置を推測しない。明示された空セルは used に含まれる。
    if len(used) != len(rows) * width:
        raise ValueError("ragged table")
    caption = _text(table.find("caption", recursive=False))
    return {"caption": caption[:MAX_CELL_TEXT], "rows": len(rows), "cols": width, "cells": cells}


def extract_tables(html: str) -> tuple[list[dict], list[str]]:
    """元HTMLの明示的なデータ表だけ。座標は表内、table_indexは元の表の順番。
    大きすぎるHTMLを途中で切って誤った構造を返さない。
    """
    if not re.search(r"<table\b", html, re.I):
        return [], []
    if len(html) > MAX_HTML:
        return [], ["structured_tables_html_limit"]
    from bs4 import BeautifulSoup, ParserRejectedMarkup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except (ParserRejectedMarkup, RecursionError, ValueError):
        return [], ["structured_tables_parse_error"]
    found = soup.find_all("table", limit=MAX_TABLES + 1)
    result, warnings = [], []
    cell_count = text_count = 0
    if len(found) > MAX_TABLES:
        warnings.append("structured_tables_count_limit")
    for index, table in enumerate(found[:MAX_TABLES]):
        if any(_hidden(t) for t in (table, *table.parents)):
            continue
        if table.find_parent(["table", "nav", "header", "footer", "aside", "template", "form"]) is not None:
            continue
        if str(table.get("role", "")).lower() in ("presentation", "none"):
            continue
        # Reverse order: nested tags may otherwise have been decomposed already.
        for tag in reversed(table.find_all(["script", "style", "template", "noscript"])):
            tag.decompose()
        try:
            grid = _grid(table)
            n = len(grid["cells"])
            size = len(grid["caption"]) + sum(len(c["text"]) for c in grid["cells"])
            if cell_count + n > MAX_TOTAL_CELLS or text_count + size > MAX_TOTAL_TEXT:
                warnings.append("structured_tables_output_limit")
                break
            result.append({"table_index": index, **grid})
            cell_count += n
            text_count += size
        except (ValueError, RecursionError):
            warnings.append(f"structured_table_{index}_unsupported")
    return result, warnings


TABLE_SCHEMA = "controldeck.html-table.v1"


def table_bundle(html: str) -> tuple[list[dict], dict, list[str]]:
    """Evidence coordinates refer to decoded HTML, not Markdown or raw HTTP bytes.
    Digest is an integrity check, not a signature or authorization.
    """
    tables, warnings = extract_tables(html)
    if not tables:
        return tables, {}, warnings
    payload = json.dumps(tables, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    meta = {"schema": TABLE_SCHEMA,
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "tables_sha256": hashlib.sha256(payload).hexdigest()}
    return tables, meta, warnings
