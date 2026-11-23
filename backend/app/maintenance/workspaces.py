"""開発の作業場（開発用の箱 sandbox）を、AI アシスタントの画面から選ぶ・作る・消すための処理。

箱の仕組みは sandbox.py（`./deck.sh sandbox`）。ここは「持ち主ごとの一覧」「元（ControlDeck 本体 / CodeDEV の
プロジェクト）から作る」「未保存の変更の確認つきで消す」だけを持つ。会話サーバーを箱の中で起こす処理は
integrations/opencode/chat_server.py。

- 作業場は箱の持ち主（meta の owner）だけが見える・使える・消せる。
- 消すとき、未コミットの変更か、どのリモートにも無いコミットがあれば、確認の印（confirm）が無ければ断る。
- 3 段目: 元からの差分の要約（changes）・関門の結果の保存（check）・取り込む（adopt）。取り込みは「関門が合格で、
  その後に変更が無い」ときだけ。本体は GitHub へ PR まで（マージはしない）、CodeDEV は元のリポジトリへブランチとして取り込むだけ。
- 脅威（2026-11-22 監査）: 箱の中の AI は box.repo/.git/config・フックを書き換えられる。ホストの git をそこで動かすと
  core.fsmonitor・credential.helper・core.sshCommand・url.*.insteadOf などでホストの権限で実行される。対策: ホストでは
  box.repo の中で git を一切動かさない。状態の確認・コミット・関門は箱の中の箱の git で行い（sandbox.probe / in_box）、
  箱の結果はホストが管理する bare のリポジトリ（export.git）へ fetch で取り込んで、差分・tree の比較・push はそこから行う。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.maintenance import sandbox
from app.maintenance.sandbox import Sandbox, SandboxError

logger = logging.getLogger("control_deck.workspaces")

CONTROLDECK = "controldeck"
CODEDEV_PREFIX = "codedev:"
CONTROLDECK_REF = "origin/main"
# 開発対象（2026-11-22）: 「ControlDeck の改修か」ではなく、機能的な改修対象で権限と取り込み先を分ける。
# adopt: pr は元が ControlDeck 本体のときだけ。branch は元のリポジトリへブランチとして取り込むだけ（PR は作らない）。
PRESETS: dict[str, dict] = {
    "app": {"label": "普通の開発", "short": "普通の開発", "capabilities": ["tools"], "adopt": "branch"},
    "llm_app": {"label": "ローカル LLM を使うアプリ", "short": "LLM アプリ", "capabilities": ["llm", "tools"], "adopt": "branch"},
    "controldeck": {"label": "ControlDeck 本体", "short": "本体", "capabilities": ["llm", "tools", "mirror"], "adopt": "pr"},
}
CAPABILITY_LABELS = {"llm": "ローカル LLM を借りる", "tools": "ControlDeck の道具", "mirror": "ControlDeck の複製",
                     "gpu": "GPU を借りる"}
# 作るのに settings.manage が要る機能と開発対象。ほかは今の OpenCode の権限（terminal.use）
MANAGE_CAPABILITIES = frozenset({"gpu"})
MANAGE_TARGETS = frozenset({"controldeck"})
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
    if not sandbox.has_meta(sandbox.root() / relative.parts[0]):
        return None
    try:
        return sandbox.load(relative.parts[0])
    except SandboxError:
        return None


def _all_boxes() -> list[Sandbox]:
    """全ての作業場の箱（持ち主の絞り込みなし。改ざんの恐れがある箱は飛ばす）。"""
    boxes = []
    for entry in sorted(sandbox.root().iterdir()):
        if entry.is_dir() and sandbox.NAME.fullmatch(entry.name) and sandbox.has_meta(entry):
            try:
                boxes.append(sandbox.load(entry.name))
            except SandboxError:
                continue          # 改ざんの恐れがある箱は一覧に出さない（使わせない）
    return boxes


def _boxes_of(username: str) -> list[Sandbox]:
    """username が空なら全ての箱（起動時の立て直し・管理側の処理用）。空でなければその人の箱だけ。"""
    return [box for box in _all_boxes() if not username or box.meta.get("owner") == username]


def get(name: str, username: str) -> Sandbox:
    """持ち主の作業場。無い・他人のものは同じ「ありません」（存在を教えない）。"""
    try:
        box = sandbox.load(name)
    except SandboxError as exc:
        raise SandboxError(f"作業場 {name} はありません") from exc
    if not username or box.meta.get("owner") != username:
        raise SandboxError(f"作業場 {name} はありません")
    return box


def unsaved(box: Sandbox, *, fresh: bool = False) -> dict:
    """未コミットの変更と、リモート追跡ブランチに無いコミットの数。箱の中で調べる。調べられなければ「ある」とみなす（安全側）。"""
    state = sandbox.probe(box, fresh=fresh)
    return {"dirty": bool(state["dirty"]), "unpushed": state["unpushed"], "unknown": bool(state.get("unknown"))}


def plan(source: str, target: str = "", capabilities: list[str] | None = None) -> tuple[str, list[str], str]:
    """(開発対象, 機能, 取り込み先)。target 省略は今までどおり（元が本体なら controldeck、それ以外は llm_app）。
    capabilities は preset の上書き。元との組み合わせが合わなければ SandboxError。"""
    is_controldeck = source == CONTROLDECK
    target = target.strip() or ("controldeck" if is_controldeck else "llm_app")
    if target not in PRESETS:
        raise SandboxError(f"開発対象は {' / '.join(PRESETS)} のどれかを指定してください")
    if target == "controldeck" and not is_controldeck:
        raise SandboxError("開発対象 controldeck は、元が ControlDeck 本体のときだけ選べます")
    preset = PRESETS[target]
    if capabilities is None:
        caps = list(preset["capabilities"])
    else:
        unknown = [c for c in capabilities if c not in sandbox.CAPABILITIES]
        if unknown:
            raise SandboxError(f"知らない機能です: {', '.join(map(str, unknown))}")
        caps = [c for c in sandbox.CAPABILITIES if c in set(capabilities)]
    if "mirror" in caps and not is_controldeck:
        raise SandboxError("ControlDeck の複製（mirror）は、元が ControlDeck 本体のときだけ使えます")
    return target, caps, str(preset["adopt"])


def required_permission(target: str, capabilities: list[str]) -> str:
    """作業場を作るのに要る権限。controldeck の作業場と gpu は settings.manage、それ以外は terminal.use。"""
    if target in MANAGE_TARGETS or MANAGE_CAPABILITIES & set(capabilities):
        return "settings.manage"
    return "terminal.use"


def summary(box: Sandbox) -> dict:
    probed = sandbox.probe(box)
    state, branch = {"dirty": bool(probed["dirty"]), "unpushed": probed["unpushed"]}, probed["branch"]
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
        "target": box.target, "target_label": PRESETS[box.target]["short"],
        "capabilities": [c for c in sandbox.CAPABILITIES if c in box.capabilities], "adopt": box.adopt_mode,
        # OpenCode（会話サーバー）がこの作業場で作業中か。消すときの確認の文言が強くなる
        "working": busy(box),
        # この作業場のモデルの並び（OpenCode は会話のモデルではなく、この先頭から使う。ヘッダの札に出す）
        "model_chain": sandbox.model_chain(box),
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


def create(username: str, source: str, name: str = "", *, target: str = "",
           capabilities: list[str] | None = None) -> dict:
    """作業場を作る。source は "controldeck" か "codedev:<プロジェクト名>"。target は開発対象（PRESETS）、
    capabilities は機能の上書き。権限の確認（required_permission）は呼ぶ側（router）が済ませる。"""
    if source != CONTROLDECK and not source.startswith(CODEDEV_PREFIX):
        raise SandboxError("元は controldeck か codedev:<プロジェクト名> を指定してください")
    free = disk_free_gib()
    if free < min_free_gib():
        raise SandboxError(
            f"ディスクの空きが {free:.1f} GiB しかありません（下限 {min_free_gib():.0f} GiB）。"
            "必要なものを消すか、下限の設定を変えてください")
    target, caps, adopt_mode = plan(source, target, capabilities)
    if source == CONTROLDECK:
        repo, ref = controldeck_repo(), CONTROLDECK_REF
    elif source.startswith(CODEDEV_PREFIX):
        repo, ref = _codedev_repo(source[len(CODEDEV_PREFIX):]), ""
    else:
        raise SandboxError("元は controldeck か codedev:<プロジェクト名> を指定してください")
    name = name.strip() or _auto_name()
    box = sandbox.create(name, str(repo), ref, owner=username, capabilities=caps, target=target, adopt=adopt_mode)
    box.meta["workspace_source"] = source
    sandbox._save_meta(box.path, box.meta)
    result = summary(box)
    if source.startswith(CODEDEV_PREFIX):
        # 作業場は git の履歴から作る。元のフォルダの未コミットの変更は入らない
        result["source_dirty"] = bool(sandbox._git("status", "--porcelain", cwd=repo))  # 元は利用者のもの（信用する）
    return result


def delete(name: str, username: str, *, confirm: bool = False) -> None:
    box = get(name, username)
    state = unsaved(box, fresh=True)
    if (state["dirty"] or state["unpushed"]) and not confirm:
        raise UnsavedChanges(name, state["dirty"], state["unpushed"])
    sandbox.destroy(name)
    try:
        _host_git("update-ref", "-d", ws_ref(name), cwd=export_repo())
    except (SandboxError, OSError):
        pass


# ---------------------------------------------------------------- 3 段目: 結果・関門・取り込む

MAX_COMMITS = 20
GIT_TIMEOUT = 120
GH_TIMEOUT = 120
CHECK_TIMEOUT = 4000
BRANCH_PREFIX = "ws/"


class AdoptRefused(SandboxError):
    """取り込めない（前提を満たさない）。メッセージは利用者に出す理由。"""


def export_repo() -> Path:
    """ホストが管理する bare のリポジトリ。設定はホストが書いたものだけ。箱の結果はここへ fetch で取り込む。"""
    path = sandbox._data_dir() / "workspaces" / "export.git"
    if not (path / "HEAD").is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        _host_git("init", "--bare", "-q", str(path), cwd=path.parent)
    return path


def ws_ref(name: str) -> str:
    return f"refs/ws/{name}/head"


def _host_git(*args: str, cwd: Path, timeout: int = GIT_TIMEOUT) -> str:
    """ホストの git。箱の作業ツリー（の中）では動かさない（箱の AI が書ける設定・フックで実行されるため）。"""
    try:
        inside = cwd.resolve().is_relative_to(sandbox.root().resolve())
    except OSError:
        inside = True
    if inside:
        raise SandboxError("箱の中ではホストの git を動かしません")
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"git {' '.join(args[:2])} がタイムアウトしました") from exc
    if result.returncode != 0:
        raise SandboxError(f"git {' '.join(args[:2])} に失敗しました: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def import_head(box: Sandbox) -> str:
    """箱の HEAD（コミット済みの分）を export.git の refs/ws/<名前>/head へ取り込み、その sha を返す。

    取り込みは受け取る側（export.git）の設定で動く。箱の設定は読まない（upload-pack の設定は git が信用しない）。
    """
    exp = export_repo()
    _host_git("-c", "protocol.file.allow=always", "fetch", "--no-tags", "--quiet", str(box.repo),
              f"+HEAD:{ws_ref(box.name)}", cwd=exp)
    return _host_git("rev-parse", ws_ref(box.name), cwd=exp)


def _update_meta(name: str, **fields) -> dict:
    """meta を読み直して、指定の項目だけを書く（他の更新を上書きしない）。"""
    path = sandbox.root() / name
    meta = sandbox._read_meta(path)
    meta.update(fields)
    sandbox._save_meta(path, meta)
    return meta


def change_base(box: Sandbox, head: str) -> str:
    """差分の起点。作った時の revision ではなく、今の main との共通の祖先（merge-base）。

    作業場が途中で main を取り込む（merge / rebase）と、元の revision との差には取り込んだ main の分まで入り、
    結果のカードが PR の実際（10 ファイル）より多く（17 ファイル）出ていた（2026-11-22 の通しの U16）。
    本体の作業場は、ホスト自身の checkout の origin/main を export.git へ fetch して（箱の作業ツリーは触らない）
    head との merge-base を取る。main が読めない・共通の祖先が無い・CodeDEV の作業場は、元の revision に戻す。
    """
    base = str(box.meta.get("revision") or "")
    if not _OID.fullmatch(base):
        raise SandboxError("この作業場には元の revision の記録がありません")
    if str(box.meta.get("workspace_source") or "") != CONTROLDECK:
        return base
    exp = export_repo()
    main_ref = f"refs/ws/{box.name}/main"
    try:
        _host_git("-c", "protocol.file.allow=always", "fetch", "--no-tags", "--quiet", str(controldeck_repo()),
                  f"+refs/remotes/{CONTROLDECK_REF}:{main_ref}", cwd=exp)
        found = _host_git("merge-base", main_ref, head, cwd=exp)
    except SandboxError:
        return base
    return found if _OID.fullmatch(found) else base


def _diff_summary(box: Sandbox, head: str) -> dict:
    """main との共通の祖先（change_base）から export.git の head までの差分。ファイルの中身は返さない。"""
    base = change_base(box, head)
    exp = export_repo()
    files = []
    for line in _host_git("diff", "--numstat", "-M", base, head, cwd=exp).splitlines():
        added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
        files.append({"path": path, "added": int(added) if added.isdigit() else 0,
                      "removed": int(removed) if removed.isdigit() else 0, "binary": added == "-", "untracked": False})
    files.sort(key=lambda item: item["path"])
    commits = []
    for line in _host_git("log", f"-n{MAX_COMMITS}", "--format=%H%x1f%an%x1f%ct%x1f%s", f"{base}..{head}",
                          cwd=exp).splitlines():
        sha, author, when, subject = (line.split("\x1f") + ["", "", "", ""])[:4]
        commits.append({"sha": sha[:10], "author": author, "at": float(when or 0), "subject": subject})
    return {"base": base[:10], "head": head[:10], "files": files, "file_count": len(files),
            "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files),
            "commits": commits, "commit_count": int(_host_git("rev-list", "--count", f"{base}..{head}", cwd=exp) or 0)}


def _uncommitted_stats(box: Sandbox) -> list[dict] | None:
    """未コミットの変更のファイルごとの増減（箱の中の git で調べる。ホストの git は箱の作業ツリーで動かさない）。

    `git diff --numstat HEAD` でステージ済み・未ステージの両方の増減を数え、`git status` の
    未追跡の分もファイルとして数える（未追跡は中身を読まないので増減 0 と印だけ。index に
    副作用を残す `git add -N` は使わない）。箱の git が動かなければ None（呼び出し側はコミット済み
    の分だけを数えたことになり、画面は「・未コミット」の印を出す）。
    """
    try:
        numstat = sandbox.in_box(
            box, ["git", "-c", "core.quotepath=false", "diff", "--numstat", "--no-renames", "HEAD"], timeout=60)
        status = sandbox.in_box(
            box, ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"], timeout=60)
    except SandboxError:
        return None
    files: dict[str, dict] = {}
    for line in numstat.splitlines():
        added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
        if path:
            files[path] = {"path": path, "added": int(added) if added.isdigit() else 0,
                           "removed": int(removed) if removed.isdigit() else 0,
                           "binary": added == "-", "untracked": False}
    for entry in status.split("\0"):
        if len(entry) > 3 and entry[2] == " ":
            path = entry[3:]
            item = files.setdefault(path, {"path": path, "added": 0, "removed": 0,
                                           "binary": False, "untracked": False})
            if entry[:2] == "??":
                item["untracked"] = True
    return sorted(files.values(), key=lambda item: item["path"])


def changes(box: Sandbox) -> dict:
    """元からの差分の要約。コミット済みの分は export.git で、未コミットの分は箱の中の git で調べる。

    ファイル数・増減には未コミットの変更も数に入れる（2026-11-22 の通しで「0 ファイル +0 −0・
    未コミット」と出ていた。取り込んだ ref の差分だけを数えていたため）。同じファイルのコミット済みと
    未コミットの増減は足し合わせ、未追跡のファイルは増減 0 でファイルとして数える。
    `uncommitted_files` はそのうち未コミットのファイルの数（箱の git が読めなければ None）。
    main の進み・ぶつかり・ディスクの空きも付く（結果のカードへ出す）。
    """
    state = sandbox.probe(box, fresh=True, with_tree=True)
    if state.get("unknown"):
        raise SandboxError("箱の中の状態を調べられませんでした。箱が動いているか確かめてください")
    summary_ = _diff_summary(box, import_head(box))
    uncommitted = _uncommitted_stats(box)
    if uncommitted is not None:
        merged: dict[str, dict] = {f["path"]: dict(f) for f in summary_["files"]}
        for item in uncommitted:
            entry = merged.setdefault(item["path"], {"path": item["path"], "added": 0, "removed": 0,
                                                     "binary": item["binary"], "untracked": False})
            entry["added"] += item["added"]
            entry["removed"] += item["removed"]
            entry["binary"] = entry.get("binary", False) or item["binary"]
            entry["untracked"] = entry.get("untracked", False) or item["untracked"]
        summary_["files"] = sorted(merged.values(), key=lambda f: f["path"])
        summary_["file_count"] = len(summary_["files"])
        summary_["added"] = sum(f["added"] for f in summary_["files"])
        summary_["removed"] = sum(f["removed"] for f in summary_["files"])
        summary_.update(uncommitted_files=len(uncommitted))
    else:
        summary_.update(uncommitted_files=None)
    summary_.update(dirty=bool(state["dirty"]), uncommitted=state["dirty"],
                    check=check_state(box, state["tree"]), adopted=adopted_state(box), merged=merged_state(box))
    summary_["behind"] = main_progress(box)
    summary_["suggested_title"] = suggest_title(box)
    summary_["merge_conflict"] = merge_conflict_state(box)
    summary_["disk_free_gib"] = round(disk_free_gib(), 1)
    summary_["disk_low"] = disk_free_gib() < min_free_gib() * 2
    return summary_


AUTOSAVE_SUBJECT = re.compile(r"^作業途中の保存 #\d+(?:（会話 (\S+) のターン）)?")
DEFAULT_CONVERSATION_TITLES = {"新しい会話", "新しいチャット", ""}


def suggest_title(box: Sandbox) -> str:
    """取り込み（PR）の題の候補（U18）。前は最後のコミットの件名だったので、自動保存が入ると
    「作業途中の保存 #3（会話 … のターン）」が PR の題になっていた。

    1. 自動保存・マージ以外のコミットの件名（新しいもの。AI か利用者が付けた名前）
    2. 自動保存に記録された会話の題（最後に作業した会話）
    3. 「作業場 <名前> からの変更」
    件名は箱の中で AI が書いたものなので、1 行に畳んで 120 字に切る（gh へは引数で渡す）。"""
    fallback = f"作業場 {box.name} からの変更"
    try:
        # 起点は差分と同じ change_base（途中で取り込んだ main の他の PR の件名を拾わない）
        exp = export_repo()
        head = _host_git("rev-parse", ws_ref(box.name), cwd=exp)
        subjects = _host_git("log", "--no-merges", "-n", "100", "--format=%s", f"{change_base(box, head)}..{head}",
                             cwd=exp).splitlines()
    except SandboxError:
        return fallback
    conversation = ""
    for subject in subjects:
        saved = AUTOSAVE_SUBJECT.match(subject)
        if saved is None:
            return " ".join(subject.split())[:120] or fallback
        conversation = conversation or (saved.group(1) or "")
    if conversation:
        from app.database import SessionLocal
        from app.models import Conversation

        with SessionLocal() as db:
            row = db.get(Conversation, conversation)
            title = " ".join((row.title if row is not None else "").split())
        if title not in DEFAULT_CONVERSATION_TITLES:
            return title[:120]
    return fallback


def check_state(box: Sandbox, current_tree: str | None = None) -> dict | None:
    """最後の関門の結果（無ければ None）。current_tree を渡すと、今の作業ツリーにまだ当てはまるか（stale）も返す。"""
    saved = sandbox._read_meta(box.path).get("last_check")
    if not isinstance(saved, dict):
        return None
    result = {key: saved.get(key) for key in ("passed", "items", "at")}
    result["head"] = str(saved.get("head") or "")[:10]
    result["stale"] = None if current_tree is None else (not saved.get("tree") or saved.get("tree") != current_tree)
    return result


def adopted_state(box: Sandbox) -> dict | None:
    saved = sandbox._read_meta(box.path).get("adopted")
    return saved if isinstance(saved, dict) else None


def parse_report(text: str) -> list[dict]:
    """dev_check の報告（「[合格] 名前: 内容」の行）を行ごとの合否にする。"""
    items = []
    for line in text.splitlines():
        match = re.match(r"^\[(合格|不合格|未実施)\] (.+?): (.*)$", line)
        if match:
            items.append({"status": {"合格": "ok", "不合格": "ng", "未実施": "skip"}[match.group(1)],
                          "name": match.group(2), "message": match.group(3)[:300]})
    return items


GATE_CODE_KEEP = 3


def trusted_gate_code() -> Path:
    """関門のコード（backend/app）を、ホストの本体の main から、箱が書けない所へ取り出す（ROADMAP U21）。

    前は箱の中の dev_check（作業場のコピー）で関門を動かしていたので、箱の AI が関門の仕組みそのものを書き換えて
    「合格」にできた（マージの L2 の警告だけが守りだった）。取り出しはホストの git（本番の checkout・信頼できる）で、
    箱の作業ツリーには触れない。版（main の sha）ごとに作り、新しい 3 つだけ残す。"""
    import tarfile

    repo = controldeck_repo()
    sha = _host_git("rev-parse", f"{CONTROLDECK_REF}^{{commit}}", cwd=repo)
    if not _OID.fullmatch(sha):
        raise SandboxError("本体の main を読めません")
    root = sandbox._data_dir() / "gate-code"
    target = root / sha
    if (target / "backend" / "app" / "maintenance" / "dev_check.py").is_file():
        return target
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{sha}.{os.getpid()}.{threading.get_ident()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        archive = subprocess.run(["git", "archive", "--format=tar", sha, "backend/app"], cwd=repo,
                                 capture_output=True, timeout=120, check=False)
        if archive.returncode != 0:
            raise SandboxError(f"関門のコードを取り出せません: {archive.stderr.decode(errors='replace').strip()[:200]}")
        import io

        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(staging, filter="data")
        try:
            staging.rename(target)
        except OSError:          # 同時に作った方が先に置いた
            shutil.rmtree(staging, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    olds = sorted((d for d in root.iterdir() if d.is_dir() and _OID.fullmatch(d.name) and d != target),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    for old in olds[GATE_CODE_KEEP - 1:]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def run_check(name: str, *, skip_visual_by: str = "") -> dict:
    """関門（dev_check）を作業場へ流し、結果を meta に保存する。重いので Job から呼ぶ（同期の関数）。

    関門は箱の中で流す。理由: (1) ホストで流すと dev_check が box.repo の中でホストの git を動かす。(2) 箱の AI が書いた
    テスト・コードがホストの権限で動く。箱の中なら隔離が保たれ、未コミットの変更も関門が見る。箱の中は元の venv が見える。
    本体の作業場では、関門のコードは箱のコピーではなく trusted_gate_code()（ホストの main から取り出し、読み取り
    専用で見せる）を使う（U21）。テスト・ビルドそのものは作業場のもの（テストの書き換えは L2 の守る場所の警告が見る）。
    """
    box = sandbox.load(name)
    base = str(box.meta.get("revision") or "")
    state = sandbox.probe(box, fresh=True, with_tree=True)
    if state.get("unknown"):
        raise SandboxError("箱の中の状態を調べられませんでした")
    python = Path(str(box.meta.get("source") or "")) / ".venv" / "bin" / "python"
    # skip_visual_by: 画面の見た目（画像を読むモデルの目）を人が確かめたとして外す管理者の名前（2026-11-22: 誤検出で
    # 正しい変更が取り込めなかった。外したことは結果と監査に残る）
    extra = ' --skip-visual' if skip_visual_by else ''
    env: dict[str, str] | None = None
    readonly: list[Path] = []
    flags = ""
    if str(box.meta.get("workspace_source") or "") == CONTROLDECK:
        gate = trusted_gate_code()
        # -P: 今いる場所（箱の backend）を import の先頭に足さない。-s: 箱の HOME の user site（AI が書ける）を読まない
        env = {"PYTHONPATH": str(gate / "backend"), "CONTROL_DECK_DEV_CHECK_REPO": str(box.repo)}
        readonly = [gate]
        flags = " -s -P"
    code, out, err = sandbox.in_box_result(
        box, ["sh", "-c", f'cd backend && exec "$0"{flags} -m app.maintenance.dev_check --base "$1"' + extra,
              str(python), base],
        timeout=CHECK_TIMEOUT, env=env, readonly=readonly)
    items = parse_report(out)
    if code in (0, 1) and items:
        passed = code == 0
    else:
        passed = False
        items = [{"status": "ng", "name": "関門", "message": (err.strip() or out.strip() or f"終了コード {code}")[-300:]}]
    saved = {"passed": passed, "items": items, "at": time.time(), "head": state["head"], "tree": state["tree"]}
    if skip_visual_by:
        saved["visual_skipped_by"] = skip_visual_by
    _update_meta(name, last_check=saved)
    return saved


def only_visual_failed(saved: dict | None) -> bool:
    """最後の関門が「画面の見た目」だけで落ちたか（人が確かめて外す口を出すかの判定）。"""
    if not isinstance(saved, dict) or saved.get("passed"):
        return False
    failed = [i for i in saved.get("items") or [] if i.get("status") == "ng"]
    return bool(failed) and all("見た目" in str(i.get("name") or "") for i in failed)


def mark_check_started(name: str, job_id: str) -> None:
    _update_meta(name, check_job=job_id)


def _origin_slug(origin: str) -> str:
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?", origin.strip())
    if not match:
        raise AdoptRefused("元の origin が GitHub ではないので、PR は作れません")
    return f"{match.group(1)}/{match.group(2)}"


def _trusted_origin() -> str:
    """push 先。箱の meta（箱の AI が書ける）ではなく、ホストの ControlDeck 本体のリポジトリの origin から取る。"""
    return _host_git("remote", "get-url", "origin", cwd=controldeck_repo())


def _require_adoptable(box: Sandbox, commit_message: str) -> dict:
    """取り込める状態か確かめる（必要なら箱の中でコミットする）。返すのは保存済みの関門の結果。"""
    saved = sandbox._read_meta(box.path).get("last_check")
    if not isinstance(saved, dict) or not saved.get("at"):
        raise AdoptRefused("関門をまだ流していません。関門を流してください")
    if not saved.get("passed"):
        raise AdoptRefused("最後の関門が不合格です。直してから関門を流し直してください")
    state = sandbox.probe(box, fresh=True, with_tree=True)
    if state.get("unknown"):
        raise SandboxError("箱の中の状態を調べられませんでした。箱が動いているか確かめてください")
    if state["dirty"]:
        if not commit_message.strip():
            raise AdoptRefused("コミットされていない変更があります。OpenCode にコミットさせるか、コミットメッセージを付けて取り込んでください")
        if saved.get("tree") != state["tree"]:
            raise AdoptRefused("関門の後に変更されています。関門を流し直してください")
        # コミットは箱の中の箱の git で（作者は箱の git の名前）。フックは動かさない
        sandbox.in_box(box, ["sh", "-c", 'git add -A && git commit --no-verify -q -m "$1"', "sh", commit_message.strip()[:200]])
    elif saved.get("head") != state["head"]:
        raise AdoptRefused("関門の後に変更されています。関門を流し直してください")
    return saved


def _body_of(summary_: dict, name: str, saved: dict, username: str, box: Sandbox | None = None) -> str:
    labels = {"ok": "合格", "ng": "不合格", "skip": "未実施"}
    lines = ["AI アシスタントの開発の作業場から作りました。", "",
             f"- 作業場: {name}（元: {summary_['base']} から {summary_['commit_count']} コミット）",
             f"- 変更: {summary_['file_count']} ファイル（+{summary_['added']} / -{summary_['removed']}）",
             f"- 利用者: {username}"]
    autosaved = int((box.meta if box is not None else {}).get("autosave_count") or 0)
    if autosaved:
        lines.append(f"- 途中の自動保存: {autosaved} 回（ターンの終わりごと）")
    lines += ["", "関門（./deck.sh dev-check）の結果:"]
    for item in saved.get("items") or []:
        lines.append(f"- [{labels.get(item['status'], item['status'])}] {item['name']}: {item['message']}")
    lines += ["", "マージと Release は、関門を通してから行ってください（この PR は自動ではマージされません）。"]
    return "\n".join(lines)


def adopt(name: str, username: str, *, commit_message: str = "", title: str = "") -> dict:
    """作業場の結果を取り込む。本体は GitHub に PR（マージしない）、CodeDEV は元のリポジトリへブランチとして。"""
    box = get(name, username)
    source = str(box.meta.get("workspace_source") or "")
    if source != CONTROLDECK and not source.startswith(CODEDEV_PREFIX):
        raise AdoptRefused("この作業場の元は取り込みに対応していません")
    mode = box.adopt_mode
    if mode == "pr" and source != CONTROLDECK:
        raise AdoptRefused("PR で取り込めるのは、元が ControlDeck 本体の作業場だけです")
    saved = _require_adoptable(box, commit_message)
    # main の進み: 取り込みの前に、箱の中で main を取り込む（指示 2026-11-22）。ぶつかったら取り込みを止める
    merged = merge_main(box)
    if merged["conflicts"]:
        raise AdoptRefused(f"main を取り込むときに {len(merged['conflicts'])} ファイルがぶつかりました。"
                           "OpenCode に直させてから、もう一度取り込んでください")
    if merged["merged"] and sandbox.probe(box, fresh=True, with_tree=True)["tree"] != saved.get("tree"):
        raise AdoptRefused(f"main を {merged['behind']} 件取り込みました。関門を流し直してから、もう一度取り込んでください")
    head = import_head(box)
    if _host_git("rev-parse", f"{head}^{{tree}}", cwd=export_repo()) != saved.get("tree"):
        raise AdoptRefused("関門で確かめた内容と取り込む内容が違います。関門を流し直してください")
    branch = f"{BRANCH_PREFIX}{name}"
    exp = export_repo()
    if mode == "pr":
        origin = _trusted_origin()
        slug = _origin_slug(origin)
        if not shutil.which("gh"):
            raise SandboxError("gh（GitHub の CLI）が見つかりません。ホストに gh を入れてログインしてください")
        _gh("auth", "status")
        _host_git("push", origin, f"{ws_ref(name)}:refs/heads/{branch}", cwd=exp, timeout=GH_TIMEOUT)
        previous = adopted_state(box)
        if previous and previous.get("pr_url"):
            url = str(previous["pr_url"])
        else:
            summary_ = _diff_summary(box, head)
            subject = commit_message.strip().splitlines()[0] if commit_message.strip() else suggest_title(box)
            url = _gh("pr", "create", "-R", slug, "--base", "main", "--head", branch,
                      "--title", (title.strip() or subject)[:120],
                      "--body", _body_of(summary_, name, saved, username, box)
                      ).splitlines()[-1].strip()
        result = {"kind": "pr", "branch": branch, "pr_url": url, "slug": slug}
    else:
        # 元のリポジトリへのブランチだけ（PR は作らない・外へは出さない）
        repo = controldeck_repo() if source == CONTROLDECK else _codedev_repo(source[len(CODEDEV_PREFIX):])
        _host_git("fetch", "--no-tags", "--quiet", str(exp), f"{ws_ref(name)}:refs/heads/{branch}", cwd=repo)
        result = {"kind": "branch", "branch": branch, "pr_url": "",
                  "project": "ControlDeck" if source == CONTROLDECK else source[len(CODEDEV_PREFIX):]}
    result.update(at=time.time(), head=head[:10], by=username)
    _update_meta(name, adopted=result)
    return result


def _gh(*args: str) -> str:
    try:
        result = subprocess.run(["gh", *args], cwd=export_repo().parent, capture_output=True, text=True,
                                timeout=GH_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("gh がタイムアウトしました") from exc
    if result.returncode != 0:
        if args[:2] == ("auth", "status"):
            raise SandboxError("gh が GitHub にログインしていません。ホストで gh auth login をしてください")
        raise SandboxError(f"gh {' '.join(args[:2])} に失敗しました: {(result.stderr or result.stdout).strip()[:300]}")
    return result.stdout.strip()


# ---- 4 段目: 行ごとの差分・マージ（2026-11-22 利用者「マージの判断を Claude に頼らず画面で行いたい」。L2: 外部の書き込みと権限） ----

DIFF_LIMIT = 256 * 1024  # 1 ファイルの差分の最大（バイト）。超えたら打ち切りの印を付ける
MERGE_L2_PHRASE = "L2 を理解した"
_OID = re.compile(r"[0-9a-f]{40}")


def _numstat_files(box: Sandbox, head: str) -> list[dict]:
    """main との共通の祖先（change_base）から head までのファイル一覧（export.git で取る。リネームは新しい側の名前）。"""
    base = change_base(box, head)
    files = []
    for line in _host_git("-c", "core.quotepath=false", "diff", "--numstat", "--no-renames", base, head,
                          cwd=export_repo()).splitlines():
        added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
        files.append({"path": path, "added": int(added) if added.isdigit() else 0,
                      "removed": int(removed) if removed.isdigit() else 0, "binary": added == "-"})
    files.sort(key=lambda item: item["path"])
    return files


def _uncommitted(box: Sandbox) -> dict | None:
    """未コミットの変更のファイル名（箱の中の git で。ホストの git は箱の作業ツリーで動かさない）。箱が止まっていれば None。"""
    try:
        out = sandbox.in_box(box, ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], timeout=60)
    except SandboxError:
        return None
    entries = []
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[2] == " ":
            code = entry[:2]
            entries.append({"path": entry[3:], "status": "untracked" if code == "??" else "modified"})
    return {"files": entries[:200], "count": len(entries)}


def file_diff(box: Sandbox, path: str = "") -> dict:
    """元からの差分。path が空ならファイルの一覧だけ、あればそのファイルの unified diff（1 ファイル DIFF_LIMIT まで）。"""
    head = import_head(box)
    base = change_base(box, head)
    files = _numstat_files(box, head)
    result: dict = {"base": base[:10], "head": head[:10], "files": files,
                    "file": None, "limit": DIFF_LIMIT, "uncommitted": _uncommitted(box)}
    if not path:
        return result
    entry = next((item for item in files if item["path"] == path), None)
    if entry is None:
        raise SandboxError("そのファイルは差分にありません")
    detail = {"path": path, "binary": entry["binary"], "truncated": False, "diff": "", "size": 0}
    if not entry["binary"]:
        try:
            text = _host_git("-c", "core.quotepath=false", "diff", "-U3", "--no-color", "--no-ext-diff", "--no-textconv",
                             "--no-renames", base, head, "--", path, cwd=export_repo())
        except UnicodeDecodeError:
            detail["binary"] = True
        else:
            raw = text.encode("utf-8", errors="replace")
            detail["size"] = len(raw)
            if len(raw) > DIFF_LIMIT:
                text = raw[:DIFF_LIMIT].decode("utf-8", errors="ignore")
                text = text[:text.rfind("\n")] if "\n" in text else text
                detail["truncated"] = True
            detail["diff"] = text
    result["file"] = detail
    return result


def merged_state(box: Sandbox) -> dict | None:
    saved = sandbox._read_meta(box.path).get("merged")
    return saved if isinstance(saved, dict) else None


def _merge_facts(box: Sandbox) -> dict:
    """マージの前提を確かめる。満たさない理由は problems に集める（ここでは何も書かない）。PR が無いなどの根本は AdoptRefused。"""
    from app.maintenance import change_policy

    if str(box.meta.get("workspace_source") or "") != CONTROLDECK:
        raise AdoptRefused("ControlDeck 本体の作業場だけマージできます")
    adopted = adopted_state(box)
    if not adopted or adopted.get("kind") != "pr" or not adopted.get("pr_url"):
        raise AdoptRefused("PR をまだ作っていません。先に取り込んでください")
    slug = _origin_slug(_trusted_origin())
    found = re.fullmatch(rf"https://github\.com/{re.escape(slug)}/pull/(\d+)", str(adopted["pr_url"]).strip(), re.IGNORECASE)
    if not found:
        raise AdoptRefused("PR の URL が取り込み先のリポジトリと一致しません")
    number = int(found.group(1))
    problems: list[str] = []
    merged = merged_state(box)
    if merged:
        problems.append("この PR はすでにマージ済みです")
    try:
        view = json.loads(_gh("pr", "view", str(number), "-R", slug, "--json", "mergeable,state,headRefOid,headRefName"))
    except json.JSONDecodeError as exc:
        raise SandboxError("gh の PR の情報を読めませんでした") from exc
    state = str(view.get("state") or "")
    mergeable = str(view.get("mergeable") or "")
    pr_head = str(view.get("headRefOid") or "")
    if state != "OPEN":
        problems.append(f"PR は開いていません（{state or '不明'}）")
    elif mergeable == "CONFLICTING":
        problems.append("PR に競合があり、マージできません")
    elif mergeable != "MERGEABLE":
        problems.append("GitHub がまだマージできるかを判定中です。少し待ってからもう一度確かめてください")
    if view.get("headRefName") and view["headRefName"] != adopted.get("branch"):
        problems.append("PR のブランチが、取り込んだブランチと違います")
    exp = export_repo()
    saved = sandbox._read_meta(box.path).get("last_check")
    check = {"passed": bool(isinstance(saved, dict) and saved.get("passed")), "at": (saved or {}).get("at") if isinstance(saved, dict) else None}
    if not isinstance(saved, dict) or not saved.get("at"):
        problems.append("関門をまだ流していません")
    elif not saved.get("passed"):
        problems.append("最後の関門が不合格です")
    tier, guarded, file_count = None, [], 0
    export_head = ""
    try:
        export_head = _host_git("rev-parse", "--verify", ws_ref(box.name), cwd=exp)
    except SandboxError:
        problems.append("取り込んだ内容の記録（export）がありません。取り込み直してください")
    if export_head and (not _OID.fullmatch(pr_head) or pr_head != export_head):
        problems.append("PR の先頭の commit が、取り込んだ内容と違います（取り込み後に作業場が変わったか、PR に別の commit が足されました）。取り込み直してください")
    elif export_head:
        try:
            tree = _host_git("rev-parse", f"{pr_head}^{{tree}}", cwd=exp)
        except SandboxError:
            tree = ""
        if not tree or tree != (saved or {}).get("tree"):
            problems.append("PR の内容が、関門で確かめた内容と違います。関門を流し直してください")
        try:
            # 区分の判定は、PR が実際に入れる差分（今の main との共通の祖先から）で見る。元の revision から見ると、
            # 作業場が取り込んだ main の分（他の PR の変更）まで数え、画面だけの PR が「L2・40 ファイル」と出た
            # （2026-11-22 の通しの U20。結果のカードの U16 と同じ change_base を使う）
            base = change_base(box, pr_head)
            _host_git("cat-file", "-e", f"{base}^{{commit}}", cwd=exp)  # 箱の外の export.git での確認（ここも箱の中では動かさない）
            report = change_policy.classify(exp, base, pr_head, "controldeck")
            tier, guarded, file_count = report["tier"], report["guarded"], len(report["files"])
        except (change_policy.PolicyError, SandboxError):
            problems.append("変更の区分（L0/L1/L2）を決められませんでした")
    return {"pr": {"number": number, "url": adopted["pr_url"], "state": state, "mergeable": mergeable, "head": pr_head[:10], "slug": slug},
            "check": check, "tier": tier, "file_count": file_count,
            "guarded": [{"path": row["path"], "reasons": row["reasons"]} for row in guarded],
            "phrase": MERGE_L2_PHRASE if tier == "L2" else "", "problems": problems, "ready": not problems,
            "merged": merged, "_head": pr_head}


def merge_preview(name: str, username: str) -> dict:
    """マージの前に画面へ出す、前提の確認の結果（関門・区分・マージできるか）。何も書かない。"""
    facts = _merge_facts(get(name, username))
    facts.pop("_head")
    return facts


def merge(name: str, username: str, *, acknowledge_l2: bool = False, confirm_phrase: str = "") -> dict:
    """取り込んだ PR を squash でマージする（前提を全部満たすときだけ）。Release はしない。"""
    box = get(name, username)
    facts = _merge_facts(box)
    if facts["problems"]:
        raise AdoptRefused("マージできません: " + " / ".join(facts["problems"]))
    # L2 は画面の「L2 を確かめてマージ」のボタン（acknowledge_l2）で承認する（2026-11-23 利用者「文言を入力させずに
    # ボタンタップで承認できるように」）。印の無い L2 のマージは断る。古い確認の文言も受ける（互換）。
    if facts["tier"] == "L2" and not (acknowledge_l2 or confirm_phrase.strip() == MERGE_L2_PHRASE):
        raise AdoptRefused("この変更は L2 です。内容を確かめて「L2 を確かめてマージ」を押してください")
    pr = facts["pr"]
    # 確認した head のままのときだけマージする（確認からマージの間に PR へ commit が足されても入れない）
    _gh("pr", "merge", str(pr["number"]), "-R", pr["slug"], "--squash", "--delete-branch", "--match-head-commit", facts["_head"])
    result = {"at": time.time(), "by": username, "pr_url": pr["url"], "tier": facts["tier"], "head": pr["head"],
              "guarded": [row["path"] for row in facts["guarded"]][:50]}
    _update_meta(name, merged=result)
    return result


# ---- 保存と中断・再開（2026-11-22 利用者「作業場のコードが保存されるタイミング・停電や再起動・中断して再開できるか」）----
#
# 区切りは 2 つ: 作業場の会話の 1 ターン（OpenCode の応答）が終わるたびの自動保存と、ControlDeck の起動時の
# 立て直し（index.lock・生成中のまま止まったメッセージ・未保存の変更）。取り込み（adopt）は今までどおり 1 つの
# PR にまとめる（squash）。途中の保存はコミットの並びに残り、PR の本文にその数だけ出る。

MAIN_TTL = 10 * 60.0        # main の進み（外部への fetch）を覚えておく長さ
GIT_PROBE_TIMEOUT = 60


def disk_free_gib() -> float:
    """data のある所（箱・export.git・本番の DB）の空き（GiB）。

    ディレクトリはここでは作らない（symlink のパスを mkdir が解決してマウントの外へ作る恐れがあるため）。
    本番では起動時に data は必ず在る。調べられなければ呼び出し元が安全側へ扱えるよう例外を返す。"""
    usage = os.statvfs(sandbox._data_dir())
    return usage.f_bavail * usage.f_frsize / (1024 ** 3)


def min_free_gib() -> float:
    """ディスクの空きの下限（GiB）。これを下回ると新しい作業場を作らない・自動保存を飛ばす（設定で変えられる）。"""
    from app.integrations.opencode import provider

    try:
        value = float(provider.get_settings().get("workspace_min_free_gib") or 15.0)
    except (TypeError, ValueError):
        value = 15.0
    return max(0.0, value)


_AUTOSAVE_LOCKS: dict[str, threading.Lock] = {}
_AUTOSAVE_GUARD = threading.Lock()


def _autosave_lock(name: str) -> threading.Lock:
    with _AUTOSAVE_GUARD:
        return _AUTOSAVE_LOCKS.setdefault(name, threading.Lock())


def autosave(box: Sandbox, conversation_id: str = "") -> dict:
    """箱の中の未コミットの変更を区切り（1 ターンの終わり・起動時の立て直し）でコミットする。

    - 変更が無ければしない。ディスクの空きが下限を下回れば飛ばす（{"skipped": "disk"}）。
    - コミットは箱の中の箱の git で（--no-verify でフックを動かさない。作者は箱の設定済みの作者）。
    - 成功したら export.git へ取り込む（結果のカードの差分は export.git を見るので、途中の保存が見える）。
    返り値: {"committed": bool, "count": int, "skipped": ""|"disk"|"clean"|"unknown"|"error", "free_gib": float, "error": str,
             "sha": コミットした HEAD（保存しなかったら ""）}
    """
    # 同じ作業場の保存が重なる（2 つの会話のターンの終わり・起動時の立て直しと利用者の送信）と、番号が
    # 重なり index.lock を取り合うので、1 つずつにする
    with _autosave_lock(box.name):
        return _autosave(box, conversation_id)


def _autosave(box: Sandbox, conversation_id: str) -> dict:
    result = _autosave_commit(box, conversation_id)
    result.setdefault("sha", "")
    return result


def _autosave_commit(box: Sandbox, conversation_id: str) -> dict:
    free = disk_free_gib()
    if free < min_free_gib():
        return {"committed": False, "count": 0, "skipped": "disk", "free_gib": round(free, 1), "error": ""}
    state = sandbox.probe(box, fresh=True)
    if state.get("unknown"):
        return {"committed": False, "count": 0, "skipped": "unknown", "free_gib": round(free, 1),
                "error": "箱の中の状態を調べられません"}
    if not state["dirty"]:
        return {"committed": False, "count": 0, "skipped": "clean", "free_gib": round(free, 1), "error": ""}
    count = int(sandbox._read_meta(box.path).get("autosave_count") or 0) + 1
    note = f"（会話 {conversation_id} のターン）" if conversation_id else "（起動時の立て直し）"
    message = f"作業途中の保存 #{count}{note}"
    try:
        # マージのぶつかりの解決の途中（unmerged が残っている）はコミットしない — conflict marker 入りの
        # まま「解決済み」として履歴に残してしまう（レビュー指摘 2026-11-22）。42 はその印
        code, _, err = sandbox.in_box_result(
            box, ["sh", "-c",
                  'test -z "$(git ls-files -u)" || exit 42\n'
                  'git add -A && git commit --no-verify -q -m "$1"', "sh", message],
            timeout=180)
    except SandboxError as exc:
        return {"committed": False, "count": count, "skipped": "error", "free_gib": round(free, 1),
                "error": str(exc)[:200]}
    if code == 42:
        return {"committed": False, "count": count, "skipped": "merge", "free_gib": round(free, 1),
                "error": "マージのぶつかりの解決の途中なので保存しませんでした"}
    if code != 0:
        return {"committed": False, "count": count, "skipped": "error", "free_gib": round(free, 1),
                "error": (err.strip() or f"終了コード {code}")[:200]}
    # 保存の前に覚えた probe（dirty）は古い。捨てて、次の一覧が箱から調べ直すようにする
    _update_meta(box.name, autosave_count=count, probe=None)
    try:
        sha = _box_head(box)
    except SandboxError:
        sha = ""
    try:
        import_head(box)
    except SandboxError:
        pass          # カードは次に開いたときの取り込みで追いつく（保存自体は済んでいる）
    return {"committed": True, "count": count, "skipped": "", "free_gib": round(free, 1), "error": "", "sha": sha}


# ---- 保存の時点へ戻す（ROADMAP U15。Claude Code の /rewind のコード側） ----
SHA_RE = re.compile(r"[0-9a-f]{40}")
CHECKPOINT_LIMIT = 50
RESTORE_HISTORY = 20


def _box_head(box: Sandbox) -> str:
    return sandbox.in_box(box, ["git", "rev-parse", "HEAD"], timeout=30).strip()


def checkpoints(box: Sandbox) -> list[dict]:
    """戻せる時点。起点の revision から HEAD までのコミット（新しい順・最大 50）と、過去の「戻す前」。

    {sha, at, subject, count（作業途中の保存の番号・無ければ 0）, conversation_id, kind: "save"|"before_restore"}。"""
    meta = sandbox._read_meta(box.path)
    revision = str(meta.get("revision") or "")
    span = f"{revision}..HEAD" if SHA_RE.fullmatch(revision) else "HEAD"
    out = sandbox.in_box(box, ["git", "log", f"-n{CHECKPOINT_LIMIT}", "--format=%H%x1f%cI%x1f%s", span], timeout=60)
    items: list[dict] = []
    for line in out.splitlines():
        sha, _, rest = line.partition("\x1f")
        at, _, subject = rest.partition("\x1f")
        number = re.search(r"作業途中の保存 #(\d+)", subject)
        conv = re.search(r"会話 (\S+) のターン", subject)
        items.append({"sha": sha, "at": at, "subject": subject, "count": int(number.group(1)) if number else 0,
                      "conversation_id": conv.group(1) if conv else "", "kind": "save"})
    for entry in reversed(meta.get("restores") or []):
        items.append({"sha": entry["from"], "at": entry["at"], "subject": "戻す前の状態", "count": 0,
                      "conversation_id": "", "kind": "before_restore"})
    return items


def restore(box: Sandbox, sha: str, username: str) -> dict:
    """作業場のコードを、保存の時点へ戻す。今の状態は先に保存し、refs/controldeck/before-restore/<n> にも残す（やり直せる）。

    戻せるのは、HEAD の祖先で起点の revision の子孫（か同じ）の保存と、過去に戻した「戻す前」だけ。"""
    if not SHA_RE.fullmatch(sha or ""):
        raise SandboxError("戻す時点の指定が正しくありません")
    with _autosave_lock(box.name):
        saved = _autosave(box, "")
        if saved["skipped"] in ("unknown", "error", "merge"):
            raise SandboxError(saved["error"] or "今の変更を保存できないので戻しません")
        if saved["skipped"] == "disk" and sandbox.probe(box, fresh=True)["dirty"]:
            raise SandboxError("ディスクの空きが少なく今の変更を保存できないので、戻しません")
        meta = sandbox._read_meta(box.path)
        past = {str(r.get("from")) for r in meta.get("restores") or []}
        revision = str(meta.get("revision") or "")

        def ancestor(older: str, newer: str) -> bool:
            return sandbox.in_box_result(box, ["git", "merge-base", "--is-ancestor", older, newer], timeout=30)[0] == 0

        code = sandbox.in_box_result(box, ["git", "cat-file", "-e", f"{sha}^{{commit}}"], timeout=30)[0]
        allowed = code == 0 and (sha in past or (ancestor(sha, "HEAD") and (
            not SHA_RE.fullmatch(revision) or ancestor(revision, sha))))
        if not allowed:
            raise SandboxError("その時点へは戻せません（この作業場の保存ではありません）")
        head = _box_head(box)
        if head == sha:
            raise SandboxError("すでにその時点です")
        number = int(meta.get("restore_count") or 0) + 1
        # 戻した先より後のコミットは、参照を残さないと git の掃除で消える
        code, _, err = sandbox.in_box_result(
            box, ["sh", "-c", 'git update-ref "refs/controldeck/before-restore/$2" HEAD && git reset --hard -q "$1"',
                  "sh", sha, str(number)], timeout=180)
        if code != 0:
            raise SandboxError(f"戻せませんでした: {err.strip()[:200] or f'終了コード {code}'}")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        note = (f"利用者がコードを保存 {sha[:7]} の時点へ戻しました。それより後の変更は今のファイルにありません。"
                "必要ならファイルを読み直してから続けてください。")
        restores = [*(meta.get("restores") or []), {"at": now, "by": username, "from": head, "to": sha}]
        _update_meta(box.name, restores=restores[-RESTORE_HISTORY:], restore_count=number, restore_note=note)
        try:
            import_head(box)
        except SandboxError:
            pass
    return {"sha": sha, "before": head, "ref": f"refs/controldeck/before-restore/{number}"}


def take_restore_note(box: Sandbox) -> str:
    """戻したことを AI へ伝える 1 文を取り出す（一度きり）。"""
    note = str(sandbox._read_meta(box.path).get("restore_note") or "")
    if note:
        _update_meta(box.name, restore_note="")
    return note


def busy(box: Sandbox) -> bool:
    """OpenCode（会話サーバー）がこの作業場で今も動いているか（消すときの確認の文言を強くする）。"""
    from app.integrations.opencode import chat_server

    for server in chat_server._all():
        if server.workspace == box.name:
            return chat_server._unit_active(server.unit)
    return False


def _start_ref(meta: dict, exp: Path) -> str:
    """main の進みを数える起点。取り込んだ記録（main_merged_to）が export.git にあればそこから、
    無ければ元の revision から（取り込んだ分は「進み」として数えない）。"""
    base = str(meta.get("revision") or "")
    start = str(meta.get("main_merged_to") or "")
    if _OID.fullmatch(start):
        try:
            if _host_git("rev-parse", "--verify", f"{start}^{{commit}}", cwd=exp):
                return start
        except SandboxError:
            pass
    return base


def _safe_fetch_source(origin: str) -> bool:
    """fetch してよい取得元か。実行を伴うリモート補助（ext::・fd:: など `::` を含むもの）と、オプションに
    読める先頭の `-` は拒む（メタはホストだけが書くが、書き込み経路が増えたときのための二重の守り）。"""
    return bool(origin) and not origin.startswith("-") and "::" not in origin and "\n" not in origin


def fetch_main(box: Sandbox) -> int | None:
    """export.git へ元の origin の main を取り込み、まだ取り込んでいない main の進みを数える（取れなければ None）。

    ホストの git は export.git（bare・ホストが管理）で動かす。箱の作業ツリーでは動かさない。
    revision・origin は都度ディスクの meta から読む（_update_meta で書いた値が in-memory の box.meta に
    届かないため。メモリ内の meta は呼び出し元が古いまま持ちがち）。
    merge_main が取り込んだ main の sha を meta の main_merged_to へ記録するので、そこから先の分だけ数える。"""
    meta = sandbox._read_meta(box.path)
    origin = str(meta.get("origin") or "")
    if not _OID.fullmatch(str(meta.get("revision") or "")) or not origin:
        return None
    if not _safe_fetch_source(origin):
        return None
    exp = export_repo()
    try:
        _host_git("-c", "protocol.ext.allow=never", "fetch", "--no-tags", "--quiet", origin, "+refs/heads/main:refs/remotes/origin/main",
                  cwd=exp, timeout=60)
        tip = _host_git("rev-parse", "refs/remotes/origin/main", cwd=exp)
        return int(_host_git("rev-list", "--count", f"{_start_ref(meta, exp)}..{tip}", cwd=exp) or 0)
    except SandboxError:
        return None


def main_progress(box: Sandbox, *, fresh: bool = False) -> int | None:
    """元の main が何件進んでいるか。10 分は覚えた値を使う（外部への fetch は毎回しない）。"""
    saved = sandbox._read_meta(box.path).get("main_progress")
    if not fresh and isinstance(saved, dict) and time.time() - float(saved.get("at") or 0) < MAIN_TTL:
        return saved.get("behind")
    behind = fetch_main(box)
    if behind is None and isinstance(saved, dict):
        behind = saved.get("behind")       # 取れなければ前回の値（最初から取れなければ None）
    _update_meta(box.name, main_progress={"at": time.time(), "behind": behind})
    return behind


def merge_conflict_state(box: Sandbox) -> dict | None:
    saved = sandbox._read_meta(box.path).get("merge_state")
    return saved if isinstance(saved, dict) and saved.get("conflicts") else None


MAIN_REF = "refs/controldeck/main"       # 箱の中で取り込んだ元の main（ぶつかりを OpenCode が直すときの merge 元）


def merge_main(box: Sandbox) -> dict:
    """取り込みの前に、箱の中で元の main を取り込む（export.git から bundle を作り、箱へ読み取り専用で
    見せて箱の中の git で fetch + merge。ホストの git は export.git で動かすだけ）。

    bundle は箱の AI が書ける場所へは置かない（symlink を仕込まれてホストの git がそれを辿る脅威。
    レビュー指摘 2026-11-22）。ホストだけが書く export.git の隣へ置き、この呼び出しの間だけ readonly
    マウントで箱へ見せ、終わったら必ず消す（常駐の OpenCode の箱には見えない。ぶつかりを直すときは、
    取り込み済みの MAIN_REF を merge させる）。
    返り値: {"behind": int | None, "merged": bool, "conflicts": [ファイル], "error": str}
    ぶつかったら merge --abort で作業ツリーを箱へ帰し、ぶつかったファイルを meta の merge_state へ残す
    （結果のカードに「OpenCode に直させる」ボタンが出る）。merge できたら main の進みの記録を 0 に戻す。
    """
    behind = main_progress(box, fresh=True)       # 取り込みの判断は、覚えた値ではなく今の main で
    result: dict = {"behind": behind, "merged": False, "conflicts": [], "error": ""}
    if not behind:
        return result
    exp = export_repo()
    bundle = exp.parent / f"{box.name}-main.bundle"     # ホストだけが書く場所（箱からは readonly で見える）
    bundle.unlink(missing_ok=True)
    try:
        _host_git("bundle", "create", "--quiet", str(bundle), "refs/remotes/origin/main", cwd=exp)
        code, out, err = sandbox.in_box_result(
            box, ["git", "fetch", "-q", str(bundle), f"+refs/remotes/origin/main:{MAIN_REF}"],
            timeout=GIT_PROBE_TIMEOUT, readonly=[bundle])
        if code != 0:
            result["error"] = (err.strip() or out.strip())[:300]
            return result
        code, out, err = sandbox.in_box_result(
            box, ["git", "merge", "--no-edit", "--no-verify", MAIN_REF], timeout=180)
        if code == 0:
            tip = _host_git("rev-parse", "refs/remotes/origin/main", cwd=exp)
            _update_meta(box.name, main_progress={"at": time.time(), "behind": 0},
                         main_merged_to=tip, merge_state=None)
            result["merged"] = True
            return result
        # ぶつかったか失敗したか。ぶつかったファイルの一覧を取ってから、必ず abort（作業ツリーを帰す）
        c2, out2, _ = sandbox.in_box_result(box, ["git", "diff", "--name-only", "--diff-filter=U"],
                                            timeout=GIT_PROBE_TIMEOUT)
        conflicts = [line for line in out2.splitlines() if line.strip()][:200] if c2 == 0 else []
        if conflicts:
            c3, _, err3 = sandbox.in_box_result(box, ["git", "merge", "--abort"], timeout=GIT_PROBE_TIMEOUT)
            result["conflicts"] = conflicts
            if c3 != 0:
                # 作業ツリーにぶつかりが残る（自動保存はぶつかりの最中は保存しない）。利用者へ伝える
                result["error"] = f"merge --abort に失敗しました: {err3.strip()[:200]}"
            _update_meta(box.name, merge_state={"conflicts": conflicts, "at": time.time()})
        else:
            result["error"] = (err.strip() or out.strip())[:300]
    except SandboxError as exc:
        result["error"] = str(exc)
    finally:
        bundle.unlink(missing_ok=True)
    return result


def last_conversation(box: Sandbox) -> str:
    """この作業場で最後に OpenCode が動いた会話の ID（「ぶつかりを OpenCode に直させる」の送り先。無ければ空）。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import ChatMessage

    target = str(box.repo.resolve())
    escaped = target.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with SessionLocal() as db:
        rows = db.execute(select(ChatMessage).where(
            ChatMessage.meta_json.like(pattern, escape="\\")).order_by(
            ChatMessage.created_at.desc()).limit(50)).scalars().all()
        for row in rows:
            try:
                meta = json.loads(row.meta_json or "{}")
            except ValueError:
                continue
            project = str(meta.get("project_path") or "")
            try:
                if project and str(Path(project).resolve()) == target:
                    return row.conversation_id
            except OSError:
                continue
    return ""


def _box_git_running(box: Sandbox) -> bool:
    """箱（pod）の中で git の process が動いているか。pod が止まっていれば動いていない。
    分からなければ「動いている」扱い（消さない・安全側）。"""
    if not sandbox._pod_running_containers(sandbox.pod_name(box)):
        return False
    podman = shutil.which("podman") or "podman"
    result = subprocess.run([podman, "pod", "top", sandbox.pod_name(box)],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        return True
    return any(re.search(r"\bgit\b", line) for line in result.stdout.splitlines())


def _interrupt_generating(repo_paths: list[str], cutoff: object) -> set[str]:
    """生成中（status=generating）のまま止まった会話のメッセージで、作業場のパスを持ち、cutoff（この処理の
    開始時刻・aware な UTC）より前に始まったものを「中断」にする（ControlDeck の再起動で止まった分。
    既存の interrupt_unfinished と同じ形）。中断にした作業場のパス（resolve 済み）を返す。cutoff 以後に始まった回は、サーバーが受け付けを始めてから
    利用者が送り直した新しい回の可能性があるので触らない（レビュー指摘 2026-11-22）。"""
    from datetime import timezone

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import ChatMessage

    if not repo_paths:
        return set()
    paths = set()
    for raw in repo_paths:
        try:
            paths.add(str(Path(raw).resolve()))
        except OSError:
            continue
    if not paths:
        return set()
    hit: set[str] = set()
    with SessionLocal() as db:
        rows = db.execute(select(ChatMessage).where(
            ChatMessage.role == "assistant", ChatMessage.status == "generating",
            ChatMessage.meta_json.like('%"project_path"%'))).scalars().all()
        for row in rows:
            if row.created_at is None:
                continue
            created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
            if created >= cutoff:
                continue          # 起動後に始まった新しい回 — 触らない
            try:
                meta = json.loads(row.meta_json or "{}")
            except ValueError:
                continue
            project = str(meta.get("project_path") or "")
            try:
                resolved = str(Path(project).resolve())
            except OSError:
                continue
            if resolved not in paths:
                continue
            row.status = "interrupted"
            row.error = "ControlDeck の再起動で中断しました"
            hit.add(resolved)
        db.commit()
    return hit


def restore_interrupted() -> None:
    """ControlDeck の起動時に 1 回（裏で）: 全ての作業場の立て直し。

    - 箱の中に git の process が無ければ、残った .git/index.lock を消す（ログ 1 行）。
    - 生成中のまま止まった作業場の会話のメッセージを「中断（ControlDeck の再起動）」にする。
    - 中断にした会話の作業場と、index.lock が残っていた作業場は、未保存の変更を自動保存する（ターンの終わりの
      autosave と同じ）。止まっていた作業場まで全部起こす（podman の起動）ことはしない。
    どれかが失敗しても他の箱の立て直しは続く（起動は止めない）。
    """
    boxes = _all_boxes()
    # この処理より後に始まった回は、サーバーが受け付けを始めてから利用者が送った新しい回の可能性がある
    cutoff = datetime.now(timezone.utc)
    try:
        interrupted = _interrupt_generating([str(box.repo) for box in boxes], cutoff)
    except Exception:  # noqa: BLE001 - DB の失敗で起動の立て直しを止めない
        logger.exception("再起動で止まった作業場の会話の記録に失敗しました")
        interrupted = set()
    if interrupted:
        logger.info("再起動で止まった作業場の会話を「中断」にしました（作業場 %d 個）", len(interrupted))
    for box in boxes:
        lock = box.repo / ".git" / "index.lock"
        had_lock = lock.is_file()
        if had_lock:
            try:
                running = _box_git_running(box)
            except Exception:  # noqa: BLE001 - podman の失敗で起動の立て直しを止めない
                logger.exception("作業場 %s の箱の中の確認に失敗しました", box.name)
                running = True
            if not running:
                try:
                    sandbox.in_box(box, ["rm", "-f", ".git/index.lock"])
                except SandboxError as exc:
                    logger.warning("作業場 %s の .git/index.lock を消せませんでした: %s", box.name, exc)
                else:
                    logger.info("作業場 %s に残った .git/index.lock を消しました", box.name)
        try:
            resolved = str(box.repo.resolve())
        except OSError:
            resolved = ""
        if not had_lock and resolved not in interrupted:
            continue
        if lock.is_file():
            continue          # git が動いている（lock を消さなかった）間は保存しない
        try:
            saved = autosave(box)
        except Exception:  # noqa: BLE001
            logger.exception("作業場 %s の起動時の自動保存に失敗しました", box.name)
            continue
        if saved["committed"]:
            logger.info("作業場 %s の未保存の変更を自動保存しました（#%d）", box.name, saved["count"])
        elif saved["skipped"] == "error":
            logger.warning("作業場 %s の自動保存に失敗しました: %s", box.name, saved["error"])
        elif saved["skipped"] == "disk":
            logger.warning("ディスクの空きが %.1f GiB なので、作業場 %s を保存できません",
                           saved["free_gib"], box.name)
