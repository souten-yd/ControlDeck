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
import re
import shutil
import subprocess
import time
from pathlib import Path

from app.maintenance import sandbox
from app.maintenance.sandbox import Sandbox, SandboxError

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


def _boxes_of(username: str) -> list[Sandbox]:
    boxes = []
    for entry in sorted(sandbox.root().iterdir()):
        if entry.is_dir() and sandbox.NAME.fullmatch(entry.name) and sandbox.has_meta(entry):
            try:
                box = sandbox.load(entry.name)
            except SandboxError:
                continue          # 改ざんの恐れがある箱は一覧に出さない（使わせない）
            if username and box.meta.get("owner") == username:
                boxes.append(box)
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


def _diff_summary(box: Sandbox, head: str) -> dict:
    """元（作った時の revision）から export.git の head までの差分。ファイルの中身は返さない。"""
    base = str(box.meta.get("revision") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise SandboxError("この作業場には元の revision の記録がありません")
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


def changes(box: Sandbox) -> dict:
    """元からの差分の要約（コミット済みの分は export.git で、未コミットの有無は箱の中で調べる）。"""
    state = sandbox.probe(box, fresh=True, with_tree=True)
    if state.get("unknown"):
        raise SandboxError("箱の中の状態を調べられませんでした。箱が動いているか確かめてください")
    summary_ = _diff_summary(box, import_head(box))
    summary_.update(dirty=bool(state["dirty"]), uncommitted=state["dirty"],
                    check=check_state(box, state["tree"]), adopted=adopted_state(box), merged=merged_state(box))
    return summary_


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


def run_check(name: str) -> dict:
    """関門（dev_check）を作業場へ流し、結果を meta に保存する。重いので Job から呼ぶ（同期の関数）。

    関門は箱の中で流す。理由: (1) ホストで流すと dev_check が box.repo の中でホストの git を動かす。(2) 箱の AI が書いた
    テスト・コードがホストの権限で動く。箱の中なら隔離が保たれ、未コミットの変更も関門が見る。箱の中は元の venv が見える。
    （箱の AI が dev_check 自体を書き換えて「合格」にしうるので、マージ前の関門は本番側で改めて通すこと。）
    """
    box = sandbox.load(name)
    base = str(box.meta.get("revision") or "")
    state = sandbox.probe(box, fresh=True, with_tree=True)
    if state.get("unknown"):
        raise SandboxError("箱の中の状態を調べられませんでした")
    python = Path(str(box.meta.get("source") or "")) / ".venv" / "bin" / "python"
    code, out, err = sandbox.in_box_result(
        box, ["sh", "-c", 'cd backend && exec "$0" -m app.maintenance.dev_check --base "$1"', str(python), base],
        timeout=CHECK_TIMEOUT)
    items = parse_report(out)
    if code in (0, 1) and items:
        passed = code == 0
    else:
        passed = False
        items = [{"status": "ng", "name": "関門", "message": (err.strip() or out.strip() or f"終了コード {code}")[-300:]}]
    saved = {"passed": passed, "items": items, "at": time.time(), "head": state["head"], "tree": state["tree"]}
    _update_meta(name, last_check=saved)
    return saved


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


def _body_of(summary_: dict, name: str, saved: dict, username: str) -> str:
    labels = {"ok": "合格", "ng": "不合格", "skip": "未実施"}
    lines = ["AI アシスタントの開発の作業場から作りました。", "",
             f"- 作業場: {name}（元: {summary_['base']} から {summary_['commit_count']} コミット）",
             f"- 変更: {summary_['file_count']} ファイル（+{summary_['added']} / -{summary_['removed']}）",
             f"- 利用者: {username}", "", "関門（./deck.sh dev-check）の結果:"]
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
            subject = commit_message.strip().splitlines()[0] if commit_message.strip() else (
                _host_git("log", "-1", "--format=%s", head, cwd=exp) or f"作業場 {name} からの変更")
            url = _gh("pr", "create", "-R", slug, "--base", "main", "--head", branch,
                      "--title", (title.strip() or subject)[:120], "--body", _body_of(summary_, name, saved, username)
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
    """元の revision から head までのファイル一覧（export.git で取る。リネームは新しい側の名前）。"""
    base = str(box.meta.get("revision") or "")
    if not _OID.fullmatch(base):
        raise SandboxError("この作業場には元の revision の記録がありません")
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
    files = _numstat_files(box, head)
    result: dict = {"base": str(box.meta.get("revision") or "")[:10], "head": head[:10], "files": files,
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
                             "--no-renames", str(box.meta.get("revision")), head, "--", path, cwd=export_repo())
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
            base = str(box.meta.get("revision") or "")
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


def merge(name: str, username: str, *, confirm_phrase: str = "") -> dict:
    """取り込んだ PR を squash でマージする（前提を全部満たすときだけ）。Release はしない。"""
    box = get(name, username)
    facts = _merge_facts(box)
    if facts["problems"]:
        raise AdoptRefused("マージできません: " + " / ".join(facts["problems"]))
    if facts["tier"] == "L2" and confirm_phrase.strip() != MERGE_L2_PHRASE:
        raise AdoptRefused(f"この変更は L2 です。確認の文言「{MERGE_L2_PHRASE}」を入力してください")
    pr = facts["pr"]
    # 確認した head のままのときだけマージする（確認からマージの間に PR へ commit が足されても入れない）
    _gh("pr", "merge", str(pr["number"]), "-R", pr["slug"], "--squash", "--delete-branch", "--match-head-commit", facts["_head"])
    result = {"at": time.time(), "by": username, "pr_url": pr["url"], "tier": facts["tier"], "head": pr["head"],
              "guarded": [row["path"] for row in facts["guarded"]][:50]}
    _update_meta(name, merged=result)
    return result
