"""テストの目録（docs/test-catalog.md）を作る。

テストを足す前に「同じことを確かめるテストがもうあるか」を探すための文書。各テストファイルの
冒頭の docstring（目的・根拠）と、テストごとの docstring の 1 行目を並べる。E2E は spec の先頭の
コメントとテスト名を並べる。

    ./deck.sh test-catalog            # docs/test-catalog.md を書き直す

作り直しても、既存の目録にある手書きの説明は消さない。コードの docstring が空のファイル・テストについて、
既存の目録の「目的:」の行と「 — 説明」を名前で突き合わせて引き継ぐ（docstring があればそちらが正）。
消えたテストの説明だけが消える。
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


_UNFILLED = "**未記入**"
_PATH_HEADING = re.compile(r"^### (?P<path>\S+)\s*$")
_BACKEND_TEST_LINE = re.compile(r"^- `(?P<name>[^`]+)`(?: — (?P<desc>.+))?$")


def parse_existing(text: str) -> dict[str, dict]:
    """既存の目録から、ファイルごとの目的とテストごとの説明を読む。{path: {"doc": str, "tests": {名前: 説明}}}。"""
    result: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        heading = _PATH_HEADING.match(line)
        if heading:
            current = {"doc": "", "tests": {}, "names": set()}
            result[heading.group("path")] = current
            continue
        if current is None:
            continue
        if line.startswith("目的:"):
            doc = line[len("目的:"):].strip()
            current["doc"] = "" if doc == _UNFILLED else doc
            continue
        test = _BACKEND_TEST_LINE.match(line)
        if test:
            current["names"].add(test.group("name"))
            if test.group("desc"):
                current["tests"][test.group("name")] = test.group("desc").strip()
    return result


def merge_existing(rows: list[dict], existing: dict[str, dict]) -> None:
    """コードに説明が無い行へ、既存の目録の手書きの説明を引き継ぐ（rows を書き換える）。"""
    for row in rows:
        old = existing.get(row["path"])
        if not old:
            continue
        if not row["doc"] and old["doc"]:
            row["doc"] = old["doc"]
        merged = []
        for item in row["tests"]:
            if isinstance(item, tuple):
                lineno, name, first = item
                if not first and old["tests"].get(name):
                    first = old["tests"][name]
                merged.append((lineno, name, first))
            else:
                merged.append(item)
        row["tests"] = merged


def render(existing_text: str = "") -> str:
    backend, e2e = _backend(), _e2e()
    if existing_text:
        existing = parse_existing(existing_text)
        merge_existing(backend, existing)
        merge_existing(e2e, existing)
    missing = [row["path"] for row in backend if not row["doc"]]
    out = [
        "# テストの目録",
        "",
        "`./deck.sh test-catalog` が作る。作り直しても既存の手書きの説明は残る（コードに説明が無いものだけ引き継ぐ）。テストを足す前にここを検索し、同じ観点のテストがあれば",
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
    previous = target.read_text(encoding="utf-8") if target.is_file() else ""
    target.write_text(render(previous), encoding="utf-8")
    print(f"wrote {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
