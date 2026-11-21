"""テストの目録の作り直しが、手書きの説明を消さないこと。

目的: `./deck.sh test-catalog`（app.maintenance.test_catalog）で作り直しても、既存の docs/test-catalog.md にある
「目的:」の行と「 — 説明」のうち、コードの docstring に無いものが名前で突き合わせて引き継がれること。docstring があれば
そちらが正。消えたテストの説明だけが消える。
根拠: 開発の作業場 1 段目（2026-11-21）の試験で、箱の OpenCode が目録を作り直したら手書きの説明が消えた。
"""
from app.maintenance import test_catalog as catalog

OLD = """# テストの目録

## backend（backend/tests）

### backend/tests/test_a.py

目的: 手で書いた目的

- `test_one` — 手で書いた説明
- `test_two` — コードの説明とは別の古い説明
- `test_gone` — 消えたテストの説明

### backend/tests/test_b.py

目的: **未記入**

- `test_x`
"""


def test_parse_reads_purpose_and_descriptions_by_name():
    parsed = catalog.parse_existing(OLD)
    a = parsed["backend/tests/test_a.py"]
    assert a["doc"] == "手で書いた目的"
    assert a["tests"]["test_one"] == "手で書いた説明"
    assert a["names"] == {"test_one", "test_two", "test_gone"}
    assert parsed["backend/tests/test_b.py"]["doc"] == ""


def test_merge_fills_only_what_the_code_leaves_empty():
    rows = [
        {"path": "backend/tests/test_a.py", "doc": "", "tests": [(1, "test_one", ""), (2, "test_two", "コードの説明"),
                                                                 (3, "test_new", "")]},
        {"path": "backend/tests/test_b.py", "doc": "コードの目的", "tests": [(1, "test_x", "")]},
    ]
    catalog.merge_existing(rows, catalog.parse_existing(OLD))
    assert rows[0]["doc"] == "手で書いた目的"
    assert rows[0]["tests"] == [(1, "test_one", "手で書いた説明"), (2, "test_two", "コードの説明"), (3, "test_new", "")]
    assert rows[1]["doc"] == "コードの目的"


def test_render_with_existing_text_keeps_every_description_in_the_real_catalog():
    """実物の目録で、作り直しても「 — 」の付いた行が 1 つも減らない（docstring の無いものは引き継ぐ）。"""
    from pathlib import Path

    existing = (Path(catalog.REPO) / "docs" / "test-catalog.md").read_text(encoding="utf-8")
    before = catalog.parse_existing(existing)
    after = catalog.parse_existing(catalog.render(existing))
    for path, old in before.items():
        if path not in after:
            continue
        for name, desc in old["tests"].items():
            if name in after[path]["names"]:
                assert after[path]["tests"].get(name), f"{path}::{name} の説明が消えた: {desc}"
        if old["doc"]:
            assert after[path]["doc"], f"{path} の目的が消えた"
