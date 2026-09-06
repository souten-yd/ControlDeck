"""プロジェクトをまとめて持ち出すための選別とZIP化。

持ち出す経路では、画面で1つずつ開くときより widely にfileを触る。artifact
previewは拡張子でallowlistしているが、ここはソース一式が対象なので同じ手は
使えない。方針を逆にして「危ないものを落とす」形で選び、落とした物は必ず
呼び出し側へ返す。黙って入れるのも、黙って落とすのも困る。

判定は二段にする。名前や拡張子だけでは、`config.py` に貼られた生の鍵は
見つけられない。逆に中身だけ見ると、鍵の入っていない `.env.example` まで
落としてしまう。名前で落とし、それを抜けた text は既知の鍵の書式で落とす。
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# 実行環境の産物。中身は再生成できるうえ、.git は履歴ごと鍵を運ぶことがある。
# dist / build は落とさない。作った物を持ち出したい場面がある。
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache", ".parcel-cache",
    ".gradle", ".terraform", ".serverless", ".vagrant", ".ipynb_checkpoints",
}
# 置いてあるだけで鍵とみなすfile名。
EXCLUDED_NAMES = {
    ".env", ".envrc", ".npmrc", ".netrc", "_netrc", ".pypirc", ".git-credentials",
    ".htpasswd", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
    "service-account.json", "serviceaccount.json", "gha-creds.json",
}
# 鍵そのものを入れる拡張子。
EXCLUDED_SUFFIXES = {
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk", ".asc", ".gpg",
    ".kdbx", ".ovpn", ".crt", ".cer", ".der",
}
# `.env.local` `api-key.txt` `secrets.yaml` のたぐい。区切りで挟まれた語だけを見るので、
# `tokenizer.json` や `keyboard.ts` は落とさない。
EXCLUDED_NAME_RE = re.compile(
    r"(^\.env(\.|$)|(^|[._-])(secret|secrets|credential|credentials|private[_-]?key|"
    r"api[_-]?key|apikey|access[_-]?key|token|passwd|password)([._-]|$))",
    re.I,
)
# 雛形は名前の規則から外す。`.env.example` や `secrets.sample.yaml` は配布物の
# 一部で、これが落ちると受け取った側が何を用意すべきか分からなくなる。中身は
# 下の本文スキャンに掛かるので、実際の鍵が貼ってあれば結局落ちる。
TEMPLATE_NAME_RE = re.compile(
    r"(^|[._-])(example|examples|sample|samples|template|templates|dist|placeholder|default)([._-]|$)",
    re.I,
)
# 名前では分からない、本文に貼られた鍵。書式が決まっている物だけを見る。
# 「変数名が API_KEY」程度では落とさない——それはソースの大半に当たってしまう。
SECRET_CONTENT_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{16,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bASIA[0-9A-Z]{16}\b"
    r"|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{35}\b"
    r"|\bglpat-[A-Za-z0-9_-]{20,}"
)
# 本文を見る上限。これを超えるfileは text ではないとみなして中身を見ない。
MAX_SCAN_BYTES = 2 * 1024 * 1024
# 1プロジェクトの上限。これを超えたら数える前に断る。
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 20_000


class ExportError(ValueError):
    pass


@dataclass
class ExportPlan:
    """何を入れ、何を落としたか。ZIP化の前に画面へ見せるためのもの。"""

    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    excluded: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False

    def add_exclusion(self, relative: str, reason: str) -> None:
        # 一覧が長くなりすぎると画面で読めない。落とした事実は truncated で伝える。
        if len(self.excluded) < 200:
            self.excluded.append({"path": relative, "reason": reason})
        else:
            self.truncated = True


def _looks_binary(chunk: bytes) -> bool:
    return b"\x00" in chunk


def _content_reason(path: Path) -> str | None:
    """本文に鍵が貼られていないか。text だけを見る。"""
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        # 読めないものは持ち出せない。落とした事実は呼び出し側で拾う。
        return "読み取りに失敗しました"
    if _looks_binary(raw[:8192]):
        return None
    text = raw.decode("utf-8", errors="ignore")
    match = SECRET_CONTENT_RE.search(text)
    return f"本文に鍵らしき値が含まれます（{match.group(0)[:12]}…）" if match else None


def _name_reason(path: Path) -> str | None:
    name = path.name
    if TEMPLATE_NAME_RE.search(name):
        return None
    if name in EXCLUDED_NAMES:
        return "鍵を置くfile名です"
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return f"鍵を入れる拡張子です（{path.suffix.lower()}）"
    if EXCLUDED_NAME_RE.search(name):
        return "file名が秘密情報を示します"
    return None


def plan(project: Path) -> ExportPlan:
    """持ち出す対象を決める。落とした物は理由付きで残す。"""
    result = ExportPlan()
    for path in sorted(_walk(project, result)):
        relative = path.relative_to(project).as_posix()
        reason = _name_reason(path)
        if reason is None:
            reason = _content_reason(path)
        if reason is not None:
            result.add_exclusion(relative, reason)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            result.add_exclusion(relative, "読み取りに失敗しました")
            continue
        if result.total_bytes + size > MAX_TOTAL_BYTES:
            raise ExportError("プロジェクトが大きすぎます（上限 2GB）")
        result.files.append(path)
        result.total_bytes += size
    if len(result.files) > MAX_FILES:
        raise ExportError(f"file数が多すぎます（上限 {MAX_FILES}）")
    return result


def _walk(project: Path, result: ExportPlan) -> Iterator[Path]:
    """symlink は辿らない。project の外を指していたら中身ごと持ち出してしまう。"""
    stack = [project]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                result.add_exclusion(
                    entry.relative_to(project).as_posix(), "symlinkは辿りません",
                )
                continue
            if entry.is_dir():
                if entry.name in EXCLUDED_DIRS:
                    result.add_exclusion(
                        entry.relative_to(project).as_posix(), "実行環境の産物です",
                    )
                    continue
                stack.append(entry)
            elif entry.is_file():
                yield entry


def write_archive(project: Path, target: Path, export_plan: ExportPlan) -> None:
    """選別済みの file だけを ZIP に入れる。中身は project 名の下へ置く。

    展開したときに散らからないよう、単一の root を作る。何を落としたかは
    EXCLUDED.txt として同梱する——手元に落ちた後で気づけるのは、この file だけ。
    """
    root = project.name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in export_plan.files:
            archive.write(path, f"{root}/{path.relative_to(project).as_posix()}")
        archive.writestr(f"{root}/EXCLUDED.txt", _exclusion_note(export_plan))


def _exclusion_note(export_plan: ExportPlan) -> str:
    lines = [
        "このZIPから除外したfileの一覧です。",
        "秘密情報が混ざるのを防ぐため、Control Deck が自動で落としています。",
        "",
    ]
    if not export_plan.excluded:
        lines.append("（除外したfileはありません）")
    else:
        lines += [f"{item['path']}\t{item['reason']}" for item in export_plan.excluded]
    if export_plan.truncated:
        lines.append("… 一覧が長いため以降は省略しました")
    return "\n".join(lines) + "\n"
