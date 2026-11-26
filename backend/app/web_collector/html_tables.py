"""W4a: 取得済み HTML の表構造を保持する。ネットワーク・外部参照・新規依存なし。

セルの座標は0始まり。結合セルは値を複製せず、rowspan/colspanで表す。
元の本文抽出を置き換えない。行列が曖昧な表は確定せず警告する。
"""
from __future__ import annotations

import re

MAX_HTML = 2_000_000
MAX_TABLES = 12
MAX_ROWS = 100
MAX_COLS = 32
MAX_CELLS = 1000
MAX_CELL_TEXT = 500


def _text(tag) -> str:
    if tag is None:
        return ""
    return " ".join(tag.stripped_strings)


def _span(cell, key: str, limit: int) -> int:
    raw = str(cell.get(key, "1"))
    if not re.fullmatch(r"[1-9]\d{0,2}", raw) or int(raw) > limit:
        raise ValueError("unsupported span")
    return int(raw)


def _grid(table) -> dict:
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
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    found = soup.find_all("table", limit=MAX_TABLES + 1)
    result, warnings = [], []
    if len(found) > MAX_TABLES:
        warnings.append("structured_tables_count_limit")
    for index, table in enumerate(found[:MAX_TABLES]):
        if table.find_parent(["table", "nav", "header", "footer", "aside", "template", "form"]) is not None:
            continue
        if str(table.get("role", "")).lower() in ("presentation", "none"):
            continue
        for tag in table.find_all(["script", "style", "template", "noscript"]):
            tag.decompose()
        try:
            result.append({"table_index": index, **_grid(table)})
        except ValueError:
            warnings.append(f"structured_table_{index}_unsupported")
    return result, warnings
