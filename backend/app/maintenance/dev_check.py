"""開発の関門。作業の完了の前に流し、base からの差分に応じた検査を順に行う。

    ./deck.sh dev-check [--base <ref>] [--full] [--skip-visual]

合否は日本語で 1 行ずつ出し、最後に「合格」または「不合格（直すもの）」を出す。終了コードは合格 0・不合格 1。
各検査は独立した関数で、git や実行を伴うのは gather_* と run_* だけ（判定は文字列・辞書だけで決まる）。
外部へは取りに行かない（pip・npm install はしない。node_modules が無ければ frontend の build は「未実施」と出す）。
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

OK, NG, SKIP = "ok", "ng", "skip"
LABEL = {OK: "合格", NG: "不合格", SKIP: "未実施"}

# 例外の印。この文字列が同じ行にあれば、秘密・shell の検査から外す（理由を同じ行に書く）。
ALLOW_MARK = "dev-check: allow"

DEPENDENCY_FILES = ("backend/requirements.txt", "frontend/package.json", "frontend/package-lock.json")
CODE_PREFIXES = ("backend/app/", "backend/alembic/", "frontend/src/", "deck.sh", "scripts/")
STATUS_DOC = "docs/implementation-status.md"
CATALOG_DOC = "docs/test-catalog.md"


@dataclass
class Result:
    name: str
    status: str
    message: str
    fixes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 判定（純粋な関数）

def select_tests(changed: list[str], new_tests: list[str], test_sources: dict[str, str]) -> list[str]:
    """変わった backend の module を import している tests/ のファイルと、新しく足した・変えたテストを返す。

    test_sources は {tests/ からの相対でなく repo からの相対パス: ソース}。
    """
    modules = {_module_name(p) for p in changed if p.startswith("backend/app/") and p.endswith(".py")}
    modules.discard("")
    picked = {p for p in changed if re.fullmatch(r"backend/tests/test_[^/]*\.py", p) and p in test_sources}
    picked.update(p for p in new_tests if p in test_sources)
    for path, source in test_sources.items():
        if path in picked:
            continue
        if modules & _referenced_modules(source):
            picked.add(path)
    return sorted(picked)


def _module_name(path: str) -> str:
    parts = path[len("backend/"):-len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _referenced_modules(source: str) -> set[str]:
    """テストが import している・名前で指している app の module（`from app.x import y` は app.x と app.x.y の両方）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("app"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("app."):
            # monkeypatch.setattr("app.x.y.func", ...) のように文字列で指す形
            parts = node.value.split(".")
            found.update(".".join(parts[:i]) for i in range(2, len(parts) + 1))
    return found


def check_tests(selected: list[str], code_changed: bool, outcome: tuple[int, str] | None, *, full: bool = False) -> Result:
    """outcome は (pytest の終了コード, 出力の末尾)。流していなければ None。"""
    name = "テスト"
    if outcome is None:
        if code_changed:
            return Result(name, NG, "変えた backend のコードを確かめるテストが見つかりません",
                          ["変えたコードを import するテストを足す（または既存のテストを広げる）"])
        return Result(name, SKIP, "backend のコードの変更が無いので流していません")
    code, tail = outcome
    scope = "backend 全体" if full else f"{len(selected)} ファイル"
    if code == 0:
        return Result(name, OK, f"{scope}が通りました")
    return Result(name, NG, f"{scope}で落ちました: {tail.strip().splitlines()[-1] if tail.strip() else ''}",
                  ["落ちたテストを直す（pytest の出力を読む）"])


def check_frontend(changed: list[str], node_modules: bool, build: tuple[int, str] | None) -> Result:
    name = "frontend の build"
    if not any(p.startswith("frontend/") and not p.startswith("frontend/e2e/") for p in changed):
        return Result(name, SKIP, "frontend の変更が無いので流していません")
    if build is None:
        why = "node_modules が無い" if not node_modules else "npm が見つからない"
        return Result(name, SKIP, f"{why}ので流せませんでした（不合格にはしていません。build は人の確認が要ります）")
    code, tail = build
    if code == 0:
        return Result(name, OK, "npm run build が通りました")
    return Result(name, NG, f"npm run build が失敗しました: {tail.strip().splitlines()[-1] if tail.strip() else ''}",
                  ["build のエラーを直す"])


def check_visual(changed: list[str], shots: list[tuple[str, float]], newest_change: float, review,
                 *, skip: bool = False, error: str = "") -> Result:
    """画面の見た目。frontend を変えたときだけ。撮影（今回の E2E のもの）が無ければ不合格、high で不合格、
    medium は合格のまま注意として出す。モデルが無ければ未実施（不合格にしない）。

    shots は (パス, 更新時刻)。newest_change より古い撮影は「今回の E2E のもの」と見なさない。
    review は visual_review.ReviewResult（撮影があるときだけ渡る）。error は見させるのに失敗した理由。"""
    name = "画面の見た目"
    if not any(p.startswith("frontend/") and not p.startswith("frontend/e2e/") for p in changed):
        return Result(name, SKIP, "frontend の変更が無いので流していません")
    if skip:
        return Result(name, SKIP, "--skip-visual で外しました（流せませんでした。画面の見た目は人の確認が要ります）")
    fresh = [p for p, mtime in shots if mtime >= newest_change]
    if not fresh:
        why = "ありません" if not shots else "すべて frontend の変更より古いです"
        return Result(name, NG, f"撮影が{why}（E2E で撮ってください）",
                      ["E2E で 320 と 1280、ライト・ダークを frontend/test-results/ へ撮る（page.screenshot）",
                       "撮り直したあとで dev-check をもう一度流す"])
    if error:
        return Result(name, NG, f"画面の見た目を確かめられませんでした: {error}",
                      ["ゲートウェイと画像を読めるモデルの状態を見て、dev-check をもう一度流す"])
    if review is None or review.status == "skipped":
        why = review.message if review is not None else "モデルを選べません"
        return Result(name, SKIP, f"{why}ので流せませんでした（不合格にはしていません。画面は人の確認が要ります）")
    high = [i for i in review.issues if i["severity"] == "high"]
    medium = [i for i in review.issues if i["severity"] == "medium"]
    tail = f"（{review.model}、{review.reviewed} 枚" + (f"、飛ばした画像 {len(review.skipped_images)} 枚" if review.skipped_images else "") + "）"
    if high:
        return Result(name, NG, f"high の問題が {len(high)} 件あります{tail}: " + " / ".join(f"{i['where']}: {i['what']}" for i in high),
                      [f"{i['where']}: {i['fix']}" for i in high] + ["直して撮り直し、dev-check をもう一度流す"])
    if medium:
        return Result(name, OK, f"合格（注意: medium が {len(medium)} 件{tail}）: " + " / ".join(f"{i['where']}: {i['what']}" for i in medium))
    return Result(name, OK, f"high・medium の問題はありません{tail}")


def check_dependencies(changed: list[str]) -> Result:
    name = "依存"
    touched = [p for p in changed if p in DEPENDENCY_FILES]
    if touched:
        return Result(name, NG, f"依存のファイルが変わっています: {', '.join(touched)}",
                      ["依存を足すには利用者の了承が要る（了承が無ければ差分を戻す）"])
    return Result(name, OK, "依存（requirements.txt・package.json・package-lock.json）は変わっていません")


def check_new_tests(new_tests: dict[str, str], catalog_text: str) -> Result:
    """新しく足したテストの先頭に「目的」と「根拠」があるか、目録に載っているか。new_tests は {path: ソース}。"""
    name = "新しいテスト"
    if not new_tests:
        return Result(name, SKIP, "新しく足したテストはありません")
    fixes = []
    for path, source in sorted(new_tests.items()):
        try:
            doc = ast.get_docstring(ast.parse(source)) or ""
        except SyntaxError:
            fixes.append(f"{path}: 構文エラーで読めません")
            continue
        for word in ("目的", "根拠"):
            if word not in doc:
                fixes.append(f"{path}: ファイル先頭の docstring に「{word}」を書く")
        if f"### {path}" not in catalog_text:
            fixes.append(f"{path}: {CATALOG_DOC} に載っていない（./deck.sh test-catalog で作り直す）")
    if fixes:
        return Result(name, NG, f"{len(fixes)} 件の不足があります", fixes)
    return Result(name, OK, f"{len(new_tests)} ファイルとも目的・根拠があり、目録に載っています")


def check_catalog_descriptions(base_text: str, now_text: str) -> Result:
    """目録の「 — 説明」・「目的:」が base より減っていないか。消えたテスト・ファイルの分は除く。"""
    from app.maintenance.test_catalog import parse_existing

    name = "目録の説明"
    if not base_text or base_text == now_text:
        return Result(name, OK, "目録の説明は変わっていません")
    before, after = parse_existing(base_text), parse_existing(now_text)
    lost: list[str] = []
    for path, old in before.items():
        new = after.get(path)
        if new is None:
            continue  # ファイルごと消えた
        if old["doc"] and not new["doc"]:
            lost.append(f"{path} の「目的:」")
        for test_name, desc in old["tests"].items():
            if test_name in new["names"] and not new["tests"].get(test_name):
                lost.append(f"{path}::{test_name}")  # テストが残っているのに説明が消えた（消えたテストは除く）
    if lost:
        return Result(name, NG, f"目録から手書きの説明が {len(lost)} 件消えています: {', '.join(lost[:5])}"
                      + (" ほか" if len(lost) > 5 else ""),
                      ["`git diff docs/test-catalog.md` で消えた説明を戻す（作り直しの道具は説明を引き継ぐ）"])
    return Result(name, OK, "既存の説明は減っていません")


def check_status_updated(changed: list[str]) -> Result:
    name = "実装状況の更新"
    code = [p for p in changed if p.startswith(CODE_PREFIXES)]
    if not code:
        return Result(name, OK, "コードの変更が無いので更新は要りません")
    if STATUS_DOC in changed:
        return Result(name, OK, f"{STATUS_DOC} が更新されています")
    return Result(name, NG, f"コードを変えたのに {STATUS_DOC} が更新されていません",
                  [f"{STATUS_DOC} の先頭に今回の変更を書く"])


def _secret_patterns() -> list[re.Pattern]:
    """既存の秘密の伏せ字（diagnostics/report.py の既知の形の鍵・Bearer・JWT）を使い回す。読めなければ同じ形を直接持つ。"""
    try:
        from app.diagnostics.report import _SECRETS

        return [pattern for pattern, _ in _SECRETS[:3]] + [_LITERAL_ASSIGNMENT]
    except Exception:  # noqa: BLE001 - 検査が診断の都合で止まらないようにする
        return [
            re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"),
            re.compile(r"\b(?:cdk|sk|pk|rk|ghp|gho|ghs|ghu|github_pat|xox[abprs]|hf)[-_][A-Za-z0-9_-]{8,}"),
            re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
            _LITERAL_ASSIGNMENT,
        ]


# 鍵らしい名前に、長い値の文字列を直に代入している形（password = "…16 文字以上…"）
_LITERAL_ASSIGNMENT = re.compile(
    r"""(?ix)\b(?:[\w]*(?:secret|passw(?:or)?d|api[_-]?key|private[_-]?key|access[_-]?token|auth[_-]?token))\w*
        ["']?\s*[:=]\s*["'][A-Za-z0-9+/_\-=.]{16,}["']""")


def check_secrets(added: dict[str, dict[int, str]]) -> Result:
    """足した行に秘密の形（鍵・トークン・パスワードの代入）が無いか。added は {path: {行番号: 行}}。"""
    name = "秘密の値"
    patterns = _secret_patterns()
    hits = []
    for path, lines in sorted(added.items()):
        for number, line in lines.items():
            if ALLOW_MARK in line:
                continue
            if any(pattern.search(line) for pattern in patterns):
                hits.append(f"{path}:{number}")
    if hits:
        return Result(name, NG, f"秘密らしい値を足しています: {', '.join(hits[:5])}" + (" ほか" if len(hits) > 5 else ""),
                      ["値を設定・環境変数・鍵ファイルへ出し、コードには書かない（テストの見本なら実行時に組み立てる）"])
    return Result(name, OK, "秘密らしい値は足していません")


def find_shell_calls(source: str, added_lines: set[int]) -> list[int]:
    """足した行にある `shell=True` の呼び出しと `os.system(...)` の行番号（AST で見るので文字列・コメントは拾わない）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        shell = any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                    for k in node.keywords)
        func = node.func
        system = isinstance(func, ast.Attribute) and func.attr == "system" \
            and isinstance(func.value, ast.Name) and func.value.id == "os"
        if (shell or system) and node.lineno in added_lines:
            found.append(node.lineno)
    return sorted(found)


def check_shell(hits: list[str]) -> Result:
    name = "shell 実行"
    if hits:
        return Result(name, NG, f"shell=True・os.system を足しています: {', '.join(hits[:5])}",
                      ["subprocess は配列引数で呼ぶ（AGENTS.md の絶対に守るルール 2）"])
    return Result(name, OK, "shell=True・os.system は足していません")


def alembic_heads(versions: dict[str, str]) -> list[str]:
    """{ファイル名: ソース} から head（誰の down_revision にもなっていない revision）を返す。"""
    revisions: set[str] = set()
    parents: set[str] = set()
    for source in versions.values():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            target, value = None, None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                target, value = node.target.id, node.value
            if target == "revision" and isinstance(value, ast.Constant) and isinstance(value.value, str):
                revisions.add(value.value)
            elif target == "down_revision" and value is not None:
                for sub in ast.walk(value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        parents.add(sub.value)
    return sorted(revisions - parents)


def check_alembic(changed: list[str], heads: list[str]) -> Result:
    name = "Alembic の head"
    if not any(p.startswith("backend/alembic/versions/") for p in changed):
        return Result(name, SKIP, "DB の移行の変更が無いので見ていません")
    if len(heads) == 1:
        return Result(name, OK, f"head は 1 本です（{heads[0]}）")
    return Result(name, NG, f"head が {len(heads)} 本です: {', '.join(heads)}",
                  ["新しい移行の down_revision を最新の head にする（枝を 1 本にそろえる）"])


def render_report(results: list[Result]) -> tuple[str, bool]:
    lines = [f"[{LABEL[r.status]}] {r.name}: {r.message}" for r in results]
    failed = [r for r in results if r.status == NG]
    if failed:
        lines.append("")
        lines.append("不合格（直すもの）:")
        for r in failed:
            lines.append(f"- {r.name}: {r.message}")
            lines.extend(f"    {fix}" for fix in r.fixes)
    else:
        lines.append("")
        lines.append("合格" + ("（未実施の検査があります。上の「未実施」を確かめてください）"
                              if any(r.status == SKIP and "流せません" in r.message for r in results) else ""))
    return "\n".join(lines), not failed


# ---------------------------------------------------------------- git・実行（外の世界に触れる部分）

def _git(*args: str, repo: Path = REPO, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=120, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} に失敗しました: {result.stderr.strip()[:300]}")
    return result.stdout


def resolve_base(base: str = "", repo: Path = REPO) -> str:
    """比べる元。指定があればそれ、無ければ origin/main（無ければ main）との merge-base。"""
    if base:
        _git("rev-parse", "--verify", "-q", base, repo=repo)
        return base
    for ref in ("origin/main", "main"):
        if _git("rev-parse", "--verify", "-q", ref, repo=repo, check=False).strip():
            return _git("merge-base", "HEAD", ref, repo=repo).strip()
    raise RuntimeError("比べる元（origin/main か main）が見つかりません。--base <ref> で指定してください")


@dataclass
class Diff:
    base: str
    repo: Path
    changed: list[str]       # 追加・変更（削除は含まない）
    added_files: list[str]   # 新規（untracked を含む）
    deleted: list[str]
    added_lines: dict[str, dict[int, str]]

    def text_at_base(self, path: str) -> str:
        return _git("show", f"{self.base}:{path}", repo=self.repo, check=False)

    def text_now(self, path: str) -> str:
        file = self.repo / path
        return file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""


def gather_diff(base: str, repo: Path = REPO) -> Diff:
    changed: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    for line in _git("diff", "--name-status", "-M", base, repo=repo).splitlines():
        fields = line.split("\t")
        status, path = fields[0][0], fields[-1]
        if status == "D":
            deleted.append(path)
        else:
            changed.append(path)
            if status in ("A", "C"):
                added.append(path)
    for path in _git("ls-files", "--others", "--exclude-standard", repo=repo).splitlines():
        if path and path not in changed:
            changed.append(path)
            added.append(path)
    lines: dict[str, dict[int, str]] = {}
    current = ""
    number = 0
    for raw in _git("diff", "-U0", "--no-color", "-M", base, repo=repo).splitlines():
        if raw.startswith("+++ "):
            current = raw[6:] if raw.startswith("+++ b/") else ""
        elif raw.startswith("@@") and current:
            match = re.search(r"\+(\d+)", raw)
            number = int(match.group(1)) if match else 0
        elif raw.startswith("+") and current:
            lines.setdefault(current, {})[number] = raw[1:]
            number += 1
    for path in added:
        if path in lines:
            continue
        file = repo / path
        if file.is_file():
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            lines[path] = {i + 1: row for i, row in enumerate(text.splitlines())}
    return Diff(base, repo, sorted(changed), sorted(added), sorted(deleted), lines)


def _clean_env() -> dict[str, str]:
    """本番の設定を指す環境変数は外す（外さないと本番の data に書く）。"""
    env = dict(os.environ)
    env.pop("CONTROL_DECK_CONFIG", None)
    return env


def run_pytest(files: list[str], repo: Path = REPO) -> tuple[int, str]:
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *[str(repo / f) for f in files]]
    result = subprocess.run(argv, cwd=repo / "backend", capture_output=True, text=True, env=_clean_env(),
                            timeout=3600, check=False)
    print(result.stdout[-3000:] if result.returncode else "", end="")
    return result.returncode, result.stdout


def run_build(repo: Path = REPO) -> tuple[int, str] | None:
    import shutil

    if not (repo / "frontend" / "node_modules").is_dir() or not shutil.which("npm"):
        return None
    result = subprocess.run(["npm", "run", "build"], cwd=repo / "frontend", capture_output=True, text=True,
                            timeout=1800, check=False)
    return result.returncode, result.stdout + result.stderr


def run_visual(diff: Diff, repo: Path, skip: bool) -> Result:
    """撮影を集め、あれば visual_review にかける（frontend を変えたときだけ）。"""
    touched = [p for p in diff.changed if p.startswith("frontend/") and not p.startswith("frontend/e2e/")]
    shots: list[tuple[str, float]] = []
    if touched and not skip:
        shots = sorted(((p.relative_to(repo).as_posix(), p.stat().st_mtime)
                        for p in (repo / "frontend" / "test-results").glob("**/*.png") if p.is_file()),
                       key=lambda item: -item[1])
    newest = max((((repo / p).stat().st_mtime) for p in touched if (repo / p).is_file()), default=0.0)
    review, error = None, ""
    if shots and any(m >= newest for _, m in shots):
        from app.maintenance import visual_review

        try:
            review = visual_review.review([repo / p for p, m in shots if m >= newest])
        except visual_review.ReviewError as exc:
            error = str(exc)
    return check_visual(diff.changed, shots, newest, review, skip=skip, error=error)


def run_all(base: str = "", *, full: bool = False, skip_visual: bool = False, repo: Path = REPO) -> tuple[str, bool]:
    base = resolve_base(base, repo)
    diff = gather_diff(base, repo)
    results: list[Result] = []

    # 1. テスト
    code_changed = any(p.startswith("backend/app/") and p.endswith(".py") for p in diff.changed)
    test_sources = {p.relative_to(repo).as_posix(): p.read_text(encoding="utf-8", errors="replace")
                    for p in sorted((repo / "backend" / "tests").glob("test_*.py"))}
    new_tests = [p for p in diff.added_files if re.fullmatch(r"backend/tests/test_[^/]*\.py", p)]
    selected = select_tests(diff.changed, new_tests, test_sources)
    if full:
        results.append(check_tests(selected, code_changed, run_pytest(["backend/tests"], repo), full=True))
    elif selected:
        results.append(check_tests(selected, code_changed, run_pytest(selected, repo)))
    else:
        results.append(check_tests(selected, code_changed, None))
    # 2. frontend
    touched_frontend = any(p.startswith("frontend/") and not p.startswith("frontend/e2e/") for p in diff.changed)
    build = run_build(repo) if touched_frontend else None
    results.append(check_frontend(diff.changed, (repo / "frontend" / "node_modules").is_dir(), build))
    # 2b. 画面の見た目
    results.append(run_visual(diff, repo, skip_visual))
    # 3. 依存
    results.append(check_dependencies(diff.changed))
    # 4. 新しいテスト
    catalog_now = diff.text_now(CATALOG_DOC)
    results.append(check_new_tests({p: diff.text_now(p) for p in new_tests}, catalog_now))
    # 5. 目録の説明
    results.append(check_catalog_descriptions(diff.text_at_base(CATALOG_DOC), catalog_now))
    # 6. 実装状況
    results.append(check_status_updated(diff.changed))
    # 7. 秘密
    results.append(check_secrets(diff.added_lines))
    # 8. shell
    shell_hits = []
    for path in diff.changed:
        if not path.endswith(".py"):
            continue
        marked = {n for n, row in diff.added_lines.get(path, {}).items() if ALLOW_MARK not in row}
        shell_hits += [f"{path}:{n}" for n in find_shell_calls(diff.text_now(path), marked)]
    results.append(check_shell(shell_hits))
    # 9. Alembic
    versions = {p.name: p.read_text(encoding="utf-8", errors="replace")
                for p in (repo / "backend" / "alembic" / "versions").glob("*.py")}
    results.append(check_alembic(diff.changed, alembic_heads(versions)))
    return render_report(results)


def main(argv: list[str]) -> int:
    base, full, skip_visual = "", False, False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--base" and args:
            base = args.pop(0)
        elif arg == "--full":
            full = True
        elif arg == "--skip-visual":
            skip_visual = True
        else:
            print("使い方: ./deck.sh dev-check [--base <ref>] [--full] [--skip-visual]", file=sys.stderr)
            return 2
    try:
        text, passed = run_all(base, full=full, skip_visual=skip_visual)
    except RuntimeError as exc:
        print(f"[不合格] 差分を調べられません: {exc}", file=sys.stderr)
        return 1
    print(text)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
