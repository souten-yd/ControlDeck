"""テストの目録（docs/test-catalog.md）を作る。

テストを足す前に「同じことを確かめるテストがもうあるか」を探すための文書。各テストファイルの
冒頭の docstring（目的・根拠）と、テストごとの docstring の 1 行目を並べる。E2E は spec の先頭の
コメントとテスト名を並べる。

    ./deck.sh test-catalog            # docs/test-catalog.md を書き直す
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _backend() -> list[dict]:
    rows = []
    for path in sorted((REPO / "backend" / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = (ast.get_docstring(tree) or "").strip()
        tests = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                first = (ast.get_docstring(node) or "").strip().splitlines()
                tests.append((node.lineno, node.name, first[0] if first else ""))
        rows.append({"path": path.relative_to(REPO).as_posix(), "doc": doc, "tests": sorted(tests)})
    return rows


_TITLE = re.compile(r"""\btest\(\s*[`"'](?P<title>[^`"']+)""")


def _e2e() -> list[dict]:
    rows = []
    for path in sorted((REPO / "frontend" / "e2e").glob("*.spec.ts")):
        text = path.read_text(encoding="utf-8")
        head = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")) and not stripped.startswith("*/"):
                head.append(stripped.lstrip("/*").strip())
            elif stripped.startswith("import") or not stripped:
                continue
            else:
                break
        titles = [m.group("title") for m in _TITLE.finditer(text)]
        rows.append({"path": path.relative_to(REPO).as_posix(), "doc": " ".join(h for h in head if h), "tests": titles})
    return rows


def render() -> str:
    backend, e2e = _backend(), _e2e()
    missing = [row["path"] for row in backend if not row["doc"]]
    out = [
        "# テストの目録",
        "",
        "`./deck.sh test-catalog` が作る（手で書き換えない）。テストを足す前にここを検索し、同じ観点のテストがあれば",
        "足さずにそれを直す・広げる。決まりは `AGENTS.md` の「不具合・診断・テストの決まり」。",
        "",
        f"- backend: {len(backend)} ファイル、{sum(len(r['tests']) for r in backend)} テスト"
        f"（目的の未記入 {len(missing)} ファイル）",
        f"- E2E: {len(e2e)} spec、{sum(len(r['tests']) for r in e2e)} テスト",
        "",
        "## backend（backend/tests）",
        "",
    ]
    for row in backend:
        doc = row["doc"].splitlines()
        out.append(f"### {row['path']}")
        out.append("")
        out.append(("目的: " + " ".join(line.strip() for line in doc if line.strip())) if doc else "目的: **未記入**")
        out.append("")
        for _, name, first in row["tests"]:
            out.append(f"- `{name}`" + (f" — {first}" if first else ""))
        out.append("")
    out += ["## E2E（frontend/e2e）", ""]
    for row in e2e:
        out.append(f"### {row['path']}")
        out.append("")
        out.append(f"目的: {row['doc']}" if row["doc"] else "目的: **未記入**")
        out.append("")
        out += [f"- {title}" for title in row["tests"]]
        out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    target = REPO / "docs" / "test-catalog.md"
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
