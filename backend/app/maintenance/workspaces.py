"""開発の作業場（開発用の箱 sandbox）を、AI アシスタントの画面から選ぶ・作る・消すための処理。

箱の仕組みは sandbox.py（`./deck.sh sandbox`）。ここは「持ち主ごとの一覧」「元（ControlDeck 本体 / CodeDEV の
プロジェクト）から作る」「未保存の変更の確認つきで消す」だけを持つ。会話サーバーを箱の中で起こす処理は
integrations/opencode/chat_server.py。

- 作業場は箱の持ち主（meta の owner）だけが見える・使える・消せる。
- 消すとき、未コミットの変更か、どのリモートにも無いコミットがあれば、確認の印（confirm）が無ければ断る。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from app.maintenance import sandbox
from app.maintenance.sandbox import Sandbox, SandboxError

CONTROLDECK = "controldeck"
CODEDEV_PREFIX = "codedev:"
CONTROLDECK_REF = "origin/main"
AUTO_NAME = re.compile(r"^ws-(\d{8})-(\d+)$")


class UnsavedChanges(SandboxError):
    """消そうとした作業場に、未コミット・未 push の変更がある。"""

    def __init__(self, name: str, dirty: bool, unpushed: int) -> None:
        super().__init__(f"作業場 {name} には保存されていない変更があります")
        self.dirty = dirty
        self.unpushed = unpushed


def controldeck_repo() -> Path:
    """ControlDeck 本体のリポジトリ（動いている本番の checkout）。"""
    return Path(sandbox.__file__).resolve().parents[3]


def box_for_project(project: Path) -> Sandbox | None:
    """プロジェクトが作業場（`<sandbox の root>/<名前>/work/repo`）ならその箱。違えば None。"""
    try:
        resolved = project.resolve()
        relative = resolved.relative_to(sandbox.root())
    except (OSError, ValueError):
        return None
    if len(relative.parts) != 3 or relative.parts[1:] != ("work", "repo") or not sandbox.NAME.fullmatch(relative.parts[0]):
        return None
    if not (sandbox.root() / relative.parts[0] / "sandbox.json").is_file():
        return None
    try:
        return sandbox.load(relative.parts[0])
    except SandboxError:
        return None


def _boxes_of(username: str) -> list[Sandbox]:
    boxes = []
    for entry in sorted(sandbox.root().iterdir()):
        if entry.is_dir() and sandbox.NAME.fullmatch(entry.name) and (entry / "sandbox.json").is_file():
            meta = sandbox._read_meta(entry)
            if username and meta.get("owner") == username:
                boxes.append(sandbox.load(entry.name))
    return boxes


def get(name: str, username: str) -> Sandbox:
    """持ち主の作業場。無い・他人のものは同じ「ありません」（存在を教えない）。"""
    try:
        box = sandbox.load(name)
    except SandboxError as exc:
        raise SandboxError(f"作業場 {name} はありません") from exc
    if not username or box.meta.get("owner") != username:
        raise SandboxError(f"作業場 {name} はありません")
    return box


def unsaved(box: Sandbox) -> dict:
    """未コミットの変更と、リモート追跡ブランチに無いコミットの数。調べられなければ「ある」とみなす（安全側）。"""
    try:
        dirty = bool(sandbox._git("status", "--porcelain", cwd=box.repo))
        unpushed = len([line for line in sandbox._git("log", "--branches", "--not", "--remotes", "--format=%H",
                                                      cwd=box.repo).splitlines() if line.strip()])
    except SandboxError:
        return {"dirty": True, "unpushed": 0, "unknown": True}
    return {"dirty": dirty, "unpushed": unpushed, "unknown": False}


def summary(box: Sandbox) -> dict:
    state = unsaved(box)
    try:
        branch = sandbox._git("rev-parse", "--abbrev-ref", "HEAD", cwd=box.repo)
    except SandboxError:
        branch = ""
    source = str(box.meta.get("workspace_source") or "")
    return {
        "name": box.name, "source": source or "box",
        "source_label": "ControlDeck 本体" if source == CONTROLDECK
        else source[len(CODEDEV_PREFIX):] if source.startswith(CODEDEV_PREFIX) else Path(str(box.meta.get("source") or "")).name,
        "origin": str(box.meta.get("origin") or ""), "branch": branch,
        "dirty": state["dirty"], "unpushed": state["unpushed"],
        "created_at": float(box.meta.get("created_at") or 0),
        "last_used": float(box.meta.get("last_used") or box.meta.get("created_at") or 0),
        "repo_path": str(box.repo),
    }


def list_for(username: str) -> list[dict]:
    items = [summary(box) for box in _boxes_of(username)]
    return sorted(items, key=lambda item: item["last_used"], reverse=True)


def touch(box: Sandbox) -> None:
    box.meta["last_used"] = time.time()
    sandbox._save_meta(box.path, box.meta)


def _auto_name() -> str:
    today = time.strftime("%Y%m%d")
    used = {int(m.group(2)) for p in sandbox.root().iterdir()
            if (m := AUTO_NAME.match(p.name)) and m.group(1) == today}
    number = 1
    while number in used:
        number += 1
    return f"ws-{today}-{number}"


def _codedev_repo(project: str) -> Path:
    from app.integrations.opencode import provider

    if not project or "/" in project or project.startswith("."):
        raise SandboxError("CodeDEV のプロジェクト名が正しくありません")
    folder = (provider.codedev_root() / project).resolve()
    if folder.parent != provider.codedev_root().resolve() or not folder.is_dir():
        raise SandboxError(f"CodeDEV にプロジェクト {project} がありません")
    try:
        top = Path(sandbox._git("rev-parse", "--show-toplevel", cwd=folder)).resolve()
    except SandboxError as exc:
        raise SandboxError(f"{project} は git のリポジトリではないので、作業場にできません") from exc
    if top != folder:
        raise SandboxError(f"{project} は git のリポジトリではないので、作業場にできません")
    return folder


def create(username: str, source: str, name: str = "") -> dict:
    """作業場を作る。source は "controldeck" か "codedev:<プロジェクト名>"。"""
    if source == CONTROLDECK:
        repo, ref = controldeck_repo(), CONTROLDECK_REF
    elif source.startswith(CODEDEV_PREFIX):
        repo, ref = _codedev_repo(source[len(CODEDEV_PREFIX):]), ""
    else:
        raise SandboxError("元は controldeck か codedev:<プロジェクト名> を指定してください")
    name = name.strip() or _auto_name()
    box = sandbox.create(name, str(repo), ref, owner=username)
    box.meta["workspace_source"] = source
    sandbox._save_meta(box.path, box.meta)
    result = summary(box)
    if source.startswith(CODEDEV_PREFIX):
        # 作業場は git の履歴から作る。元のフォルダの未コミットの変更は入らない
        result["source_dirty"] = bool(sandbox._git("status", "--porcelain", cwd=repo))
    return result


def delete(name: str, username: str, *, confirm: bool = False) -> None:
    box = get(name, username)
    state = unsaved(box)
    if (state["dirty"] or state["unpushed"]) and not confirm:
        raise UnsavedChanges(name, state["dirty"], state["unpushed"])
    sandbox.destroy(name)
