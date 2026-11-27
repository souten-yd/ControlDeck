"""Nexarch の AI チーム（T1）: タスク板と、担当のコンソールへの送り込み。

チーム = Nexarch のコンソール（役割つき）の集まり + タスク板。状態の正本はこの板（モデルの記憶に
頼らない）。担当のコンソールへは Terminal の inject_input（自動再開と同じ送り方）で短い文を渡し、
終わった報告は画面の「完了報告:」の 5 行（古い 4 行も可）を正規表現で読む（LLM なし）。

- 保存: `data/ai-commander/teams.json`（0600・tmp → os.replace・プロセス内は RLock）。DB は使わない。
- 壊れた JSON は空として扱う（落とさない）。
- 自動の取り込み・止まった／上限の印・通知は team_watch.py（T2a）。ここは一覧のときの担当の状態・報告の有無・未検証の印。
- T3a: メンバーに headless（CLI と接続だけ・画面なし）を足せる。実行は team_runner.py、上限の方針・並列・配り手は
  team_dispatch.py。チームごとに作業場所（CodeDEV の team-日時）を持つ（workdir）。
根拠: docs/PLAN-nexarch-team.md T1・T3。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from app.commander import consoles, status, workdir
from app.config import data_dir
from app.terminals.manager import manager

logger = logging.getLogger(__name__)

ROLES = ("leader", "developer", "reviewer", "researcher", "member")
STATUSES = ("todo", "doing", "review", "done", "rejected", "blocked")
NAME_MAX = 40
TITLE_MAX = 120
TEXT_MAX = 4000
FILES_MAX = 50
FILE_MAX = 500
HISTORY_MAX = 50
CAPTURE_LINES = 200
REPORT_FIELD_MAX = 2000
BRIEF_MAX = 8000
CHECKS_MAX = 10
CHECK_MAX = 500
MAX_RETRIES = 2
RESET_WAIT = 3.0  # リセットのコマンドを送ってから本文を送るまでの秒数
RESET_COMMANDS = {"claude": "/clear", "codex": "/new", "opencode": "/new", "gemini": "/clear"}
RETRY_TAIL = 1500
HEADLESS_CLIS = ("claude", "codex", "opencode")
ON_LIMIT = ("wait", "handoff")
PARALLEL_MAX = 8
RUNS_MAX = 20
HEADLESS_ID_RE = re.compile(r"^h[0-9a-f]{8}$")
LABEL_MAX = 40

_lock = threading.RLock()


class TeamError(Exception):
    """router が HTTP の状態へ直す。status は 400/404/409。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _path() -> Path:
    return data_dir() / "ai-commander" / "teams.json"


def _load() -> list[dict]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    teams = raw.get("teams") if isinstance(raw, dict) else None
    return [t for t in teams if isinstance(t, dict) and isinstance(t.get("id"), str)] if isinstance(teams, list) else []


def _save(teams: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"teams": teams}, ensure_ascii=False, indent=2))
    os.chmod(temp, 0o600)
    os.replace(temp, path)


# ---------------------------------------------------------------- 検証

def _has_control(value: str, *, multiline: bool) -> bool:
    allowed = {"\n", "\t"} if multiline else set()
    return any((ord(c) < 0x20 and c not in allowed) or ord(c) == 0x7F for c in value)


def clean_line(value: object, label: str, maximum: int, *, required: bool = True) -> str:
    """1 行の文字列（制御文字は断る）。"""
    if not isinstance(value, str):
        raise TeamError(f"{label}は文字列で指定してください")
    text = value.strip()
    if required and not text:
        raise TeamError(f"{label}を入力してください")
    if len(text) > maximum:
        raise TeamError(f"{label}は {maximum} 文字以内にしてください")
    if _has_control(text, multiline=False):
        raise TeamError(f"{label}に制御文字（改行・tab など）は使えません")
    return text


def clean_text(value: object, label: str, maximum: int = TEXT_MAX) -> str:
    """複数行の文字列（改行・tab 以外の制御文字は断る）。"""
    if not isinstance(value, str):
        raise TeamError(f"{label}は文字列で指定してください")
    text = value.strip()
    if len(text) > maximum:
        raise TeamError(f"{label}は {maximum} 文字以内にしてください")
    if _has_control(text, multiline=True):
        raise TeamError(f"{label}に制御文字は使えません（改行・tab は使えます）")
    return text


def clean_files(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TeamError("担当ファイルは文字列のリストで指定してください")
    if len(value) > FILES_MAX:
        raise TeamError(f"担当ファイルは {FILES_MAX} 件までです")
    out: list[str] = []
    for item in value:
        text = clean_line(item, "担当ファイル", FILE_MAX, required=False)
        if text and text not in out:
            out.append(text)
    return out


def clean_checks(value: object) -> list[str]:
    """確認のコマンド（1 行ずつ・制御文字不可）。空行は捨てる。"""
    if not isinstance(value, list):
        raise TeamError("確認のコマンドは文字列のリストで指定してください")
    out = [clean_line(item, "確認のコマンド", CHECK_MAX, required=False) for item in value]
    out = [c for c in out if c]
    if len(out) > CHECKS_MAX:
        raise TeamError(f"確認のコマンドは {CHECKS_MAX} 個までです")
    return out


def _now() -> float:
    return round(time.time(), 3)


def _event(task: dict, event: str, by: str) -> None:
    task.setdefault("history", []).append({"at": _now(), "event": event, "by": by})
    del task["history"][:-HISTORY_MAX]
    task["updated_at"] = _now()


def _find_team(teams: list[dict], team_id: str) -> dict:
    for team in teams:
        if team["id"] == team_id:
            return team
    raise TeamError("チームが見つかりません", 404)


def _find_task(team: dict, task_id: int) -> dict:
    for task in team.get("tasks", []):
        if task.get("id") == task_id:
            return task
    raise TeamError("タスクが見つかりません", 404)


# ---------------------------------------------------------------- メンバー（console と headless）

def is_headless(member: dict) -> bool:
    return member.get("kind") == "headless"


def member_key(member: dict) -> str:
    """担当（assignee）に入れる識別子。console は Terminal のセッション ID、headless は h + 16 進 8 桁。"""
    return str(member["id"] if is_headless(member) else member["session_id"])


def find_member(team: dict, key: str) -> dict | None:
    return next((m for m in team.get("members", []) if member_key(m) == key), None)


def member_is_local(member: dict) -> bool:
    """ローカル LLM（GPU を共有する担い手）。OpenCode の既定の接続＝ゲートウェイのローカルのモデル。"""
    return member.get("kind") == "headless" and member.get("cli") == "opencode" and member.get("account") == "default"


# ---------------------------------------------------------------- 担当ファイルの重なり

def _overlaps(a: str, b: str) -> bool:
    a, b = a.strip().rstrip("/"), b.strip().rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _warnings(team: dict, task: dict) -> list[str]:
    """同じチームの未完了（done 以外）の他のタスクと担当ファイルが重なるか。断らず知らせるだけ。"""
    if task.get("status") == "done":
        return []
    out: list[str] = []
    for other in team.get("tasks", []):
        if other is task or other.get("id") == task.get("id") or other.get("status") == "done":
            continue
        for mine in task.get("files", []):
            hit = next((theirs for theirs in other.get("files", []) if _overlaps(mine, theirs)), None)
            if hit is not None:
                out.append(f"#{other['id']} と {mine} が重なっています")
    return out


# ---------------------------------------------------------------- チーム

def _console_ids() -> dict[str, dict]:
    return {str(row["id"]): row for row in consoles.list_consoles()}


def _make_workdir(name: str) -> dict:
    """チームの作業場所。保存先（settings の team_root）があればその下の `<名前>-日時`、無ければ CodeDEV の team-日時
    （どちらも git init・manifest つき。CodeDEV の中なら Project Lab にも出る）。作れなければ空（実行のときに作り直す）。"""
    try:
        from app.commander import settings as commander_settings
        from app.integrations.opencode import projects

        made = None
        root = commander_settings.team_root()
        if root:
            try:
                made = projects.create_project_in(workdir.resolve_dir(root), name)
            except (OSError, ValueError) as exc:  # 保存先が消えた・外れた → 今の動き（CodeDEV）へ
                logger.warning("チームの保存先を使えません（CodeDEV に作ります）: %s", exc)
        if made is None:
            made = projects.create_auto_project("team", name)
        return {"workdir": made["path"], "workdir_folder": made["folder"], "workdir_name": made["name"]}
    except (OSError, ValueError, ImportError) as exc:
        logger.warning("チームの作業場所を作れませんでした: %s", exc)
        return {"workdir": "", "workdir_folder": "", "workdir_name": ""}


def ensure_workdir_for(team: dict) -> str:
    """team の dict を直接書き換えて作業場所のパスを返す（保存は呼び出し側）。無い・消えていれば作る。"""
    current = str(team.get("workdir") or "")
    if current and Path(current).is_dir():
        return current
    team.update(_make_workdir(str(team.get("name") or "team")))
    return str(team.get("workdir") or "")


def ensure_workdir(team_id: str) -> str:
    """チームの作業場所のパス。無い・消えていれば作る（T3a より前に作ったチームのためにも）。"""
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        before = team.get("workdir")
        path = ensure_workdir_for(team)
        if team.get("workdir") != before:
            _save(teams)
        return path


CREATED_BY = ("", "nexarch", "assistant")


def create_team(name: str, *, workdir_path: str = "", created_by: str = "") -> dict:
    """チームを作る。役割とルーターは Nexarch の設定の既定を写す（T3b）。

    保存先: AI アシスタントから作る（created_by="assistant"）ときは、その会話の OpenCode の作業場所（workdir_path）をそのまま
    使う（新しいフォルダを作らない・必須）。それ以外は workdir_path があればそれ、無ければ Nexarch の設定の保存先
    （team_root）の下に自動で作る（未設定なら CodeDEV に自動の作業場所）。workdir_path は作業フォルダの検証を通す。
    """
    from app.commander import team_roles

    name = clean_line(name, "チーム名", NAME_MAX)
    if created_by not in CREATED_BY:
        raise TeamError("作成元が正しくありません")
    if created_by == "assistant" and not workdir_path:
        raise TeamError("AI アシスタントから作るときは、会話の作業場所（workdir）が必要です")
    if workdir_path:
        try:
            resolved = workdir.resolve_dir(workdir_path)
        except (OSError, ValueError) as exc:
            raise TeamError(f"作業場所を使えません: {exc}") from exc
        made = {"workdir": str(resolved), "workdir_folder": resolved.name, "workdir_name": resolved.name}
    else:
        made = _make_workdir(name)
    roles = team_roles.new_team_roles()
    router = team_roles.new_team_router()
    with _lock:
        teams = _load()
        team = {
            "id": "t" + secrets.token_hex(4), "name": name, "created_at": _now(),
            "members": [], "tasks": [], "next_task_id": 1, "notify": True,
            "brief": "", "reset_before_dispatch": True,
            "on_limit": "handoff", "fallback": [], "codex_without_sandbox": True, "max_parallel": 3, "local_parallel": 1, "auto_run": True,
            "roles": roles, "router": router, "limits": {}, "created_by": created_by,
            **made,
        }
        teams.append(team)
        _save(teams)
    return team


def rename_team(team_id: str, name: str) -> dict:
    name = clean_line(name, "チーム名", NAME_MAX)
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        team["name"] = name
        _save(teams)
    return team


def update_team(team_id: str, fields: dict) -> dict:
    """チームの名前・前提（brief）・送る前のリセットを、送った欄だけ変える。"""
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        if fields.get("name") is not None:
            team["name"] = clean_line(fields["name"], "チーム名", NAME_MAX)
        if fields.get("brief") is not None:
            team["brief"] = clean_text(fields["brief"], "チームの前提", BRIEF_MAX)
        if fields.get("reset_before_dispatch") is not None:
            if not isinstance(fields["reset_before_dispatch"], bool):
                raise TeamError("リセットの入り切りは真偽で指定してください")
            team["reset_before_dispatch"] = fields["reset_before_dispatch"]
        if fields.get("on_limit") is not None:
            if fields["on_limit"] not in ON_LIMIT:
                raise TeamError("上限で止まったときの方針は wait か handoff にしてください")
            team["on_limit"] = fields["on_limit"]
        if fields.get("fallback") is not None:
            team["fallback"] = _clean_fallback(team, fields["fallback"])
        for key, label, top in (("max_parallel", "同時に走らせる数", PARALLEL_MAX),
                                ("local_parallel", "ローカルの同時の数", PARALLEL_MAX)):
            if fields.get(key) is not None:
                value = fields[key]
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= top:
                    raise TeamError(f"{label}は 1〜{top} の整数にしてください")
                team[key] = value
        if fields.get("codex_without_sandbox") is not None:
            if not isinstance(fields["codex_without_sandbox"], bool):
                raise TeamError("サンドボックスなしの入り切りは真偽で指定してください")
            team["codex_without_sandbox"] = fields["codex_without_sandbox"]
        if fields.get("auto_run") is not None:
            if not isinstance(fields["auto_run"], bool):
                raise TeamError("自動の割り当ての入り切りは真偽で指定してください")
            team["auto_run"] = fields["auto_run"]
        _save(teams)
    return team


def _clean_fallback(team: dict, value: object) -> list[str]:
    """担い手の候補の順番。チームのメンバーの識別子だけ・重複なし。"""
    if not isinstance(value, list):
        raise TeamError("候補の順番はリストで指定してください")
    keys = {member_key(m) for m in team.get("members", [])}
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in keys:
            raise TeamError("候補の順番はこのチームのメンバーだけで指定してください")
        if item not in out:
            out.append(item)
    return out


def delete_team(team_id: str) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        teams.remove(team)
        _save(teams)
    # 役割の印は外す（他のチームのメンバーでなければ）
    still = {m["session_id"] for t in teams for m in t.get("members", []) if not is_headless(m)}
    for member in team.get("members", []):
        if not is_headless(member) and member["session_id"] not in still:
            _clear_role(member["session_id"])
    try:  # 走っている実行があれば止める
        from app.commander import team_runner
        team_runner.stop_team(team_id)
    except Exception:  # noqa: BLE001 - チームの削除は止めない
        logger.exception("チームの実行を止められませんでした")
    return team


def _clear_role(session_id: str) -> None:
    try:
        manager.clear_marks(session_id, ("role",))
    except (KeyError, OSError, RuntimeError, ValueError):
        pass  # 閉じたコンソールの印は外せなくてよい


def _clean_headless(item: dict) -> dict:
    """headless のメンバー（CLI と接続だけ。画面なし）。id は引き継ぐ（なければ作る）。"""
    cli = item.get("cli")
    role = item.get("role")
    if cli not in HEADLESS_CLIS:
        raise TeamError(f"ヘッドレスの CLI は {' / '.join(HEADLESS_CLIS)} のどれかにしてください")
    if role not in ROLES:
        raise TeamError(f"役割は {' / '.join(ROLES)} のどれかにしてください")
    from app.commander import accounts

    account = item.get("account") or accounts.DEFAULT_ACCOUNT
    if not isinstance(account, str) or not accounts.ACCOUNT_NAME_RE.fullmatch(account):
        raise TeamError("アカウントの名前が正しくありません")
    try:
        exists = accounts.account_exists(accounts.CLIS[cli], account)
    except Exception:  # noqa: BLE001 - OpenCode の接続の一覧を取れないとき
        exists = account == accounts.DEFAULT_ACCOUNT
    if not exists:
        raise TeamError("アカウントが見つかりません")
    label = clean_line(item.get("label") or "", "名前", LABEL_MAX, required=False)
    raw_id = item.get("id")
    ident = raw_id if isinstance(raw_id, str) and HEADLESS_ID_RE.fullmatch(raw_id) else "h" + secrets.token_hex(4)
    from app.commander import models as model_list
    from app.commander import team_roles

    try:
        model, effort = model_list.clean_model(item.get("model")), model_list.clean_effort(item.get("effort"))
    except ValueError as exc:
        raise TeamError(str(exc)) from exc
    member = {"id": ident, "kind": "headless", "cli": cli, "account": account, "role": role, "label": label,
              "model": model, "effort": effort}
    if item.get("auto") is True and isinstance(item.get("role_id"), str):  # 役割から自動で作った枠（画面では役割の下にまとめる）
        member.update({"auto": True, "role_id": item["role_id"][:16],
                       "ep": team_roles.endpoint_key({"kind": "headless", "cli": cli, "account": account, "model": model, "effort": effort})})
    return member


def set_members(team_id: str, members: object) -> dict:
    """メンバーを丸ごと置き換える。console は Nexarch のコンソールだけ、headless は CLI と接続。リーダーは 0〜1 人。"""
    if not isinstance(members, list):
        raise TeamError("メンバーはリストで指定してください")
    known = _console_ids()
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in members:
        if not isinstance(item, dict):
            raise TeamError("メンバーの形が正しくありません")
        if item.get("kind") == "headless":
            member = _clean_headless(item)
            if member["id"] in seen:
                member["id"] = "h" + secrets.token_hex(4)
            seen.add(member["id"])
            cleaned.append(member)
            continue
        sid = item.get("session_id")
        role = item.get("role")
        if not isinstance(sid, str) or sid not in known:
            raise TeamError("Nexarch のコンソールだけメンバーにできます", 400)
        if role not in ROLES:
            raise TeamError(f"役割は {' / '.join(ROLES)} のどれかにしてください")
        if sid in seen:
            raise TeamError("同じコンソールを 2 回入れられません")
        seen.add(sid)
        console_member = {"session_id": sid, "role": role}
        if item.get("auto") is True and isinstance(item.get("role_id"), str) and isinstance(item.get("ep_def"), dict):
            from app.commander import team_roles

            ep_def = team_roles.clean_endpoint(item["ep_def"])
            console_member.update({"auto": True, "role_id": item["role_id"][:16], "ep_def": ep_def,
                                   "ep": team_roles.endpoint_key(ep_def)})
        cleaned.append(console_member)
    if sum(1 for m in cleaned if m["role"] == "leader") > 1:
        raise TeamError("リーダーは 1 人までです")
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        removed = {m["session_id"] for m in team.get("members", []) if not is_headless(m)} - seen
        team["members"] = cleaned
        if team.get("fallback"):  # 外れたメンバーは候補の順番からも外す
            team["fallback"] = [k for k in team["fallback"] if k in seen]
        _save(teams)
        others = {m["session_id"] for t in teams if t is not team for m in t.get("members", []) if not is_headless(m)}
    for sid in removed - others:
        _clear_role(sid)
    for member in cleaned:
        if is_headless(member):
            continue
        try:
            manager.tag_session(member["session_id"], {"role": member["role"]})
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("役割の印を付けられませんでした（%s）: %s", member["session_id"], exc)
    return team


# ---------------------------------------------------------------- タスク

def _check_role(team: dict, role: object) -> str:
    if role in (None, ""):
        return ""
    if not isinstance(role, str) or not any(r.get("id") == role for r in team.get("roles", [])):
        raise TeamError("役割はこのチームの役割から選んでください")
    return role


def _check_assignee(team: dict, assignee: object) -> str:
    if assignee in (None, ""):
        return ""
    if not isinstance(assignee, str) or assignee not in {member_key(m) for m in team.get("members", [])}:
        raise TeamError("担当はこのチームのメンバーから選んでください")
    return assignee


def is_unverified(task: dict) -> bool:
    """報告があり、確認（verify）が空、または試験が空・「なし」「未実施」「-」なら未検証。"""
    report = task.get("report")
    if not isinstance(report, dict):
        return False
    verify = str(report.get("verify") or "").strip()
    tests = str(report.get("tests") or "").strip().lower()
    return not verify or tests in _NONE_WORDS


def _task_view(team: dict, task: dict) -> dict:
    view = {**task, "checks": task.get("checks") or [], "warnings": _warnings(team, task), "unverified": is_unverified(task)}
    run = task.get("run")
    if isinstance(run, dict) and run.get("status") == "running":
        from app.commander import team_runner
        view["run"] = team_runner.live_overlay(team["id"], task["id"], run)  # 実行中の最後の行・経過・使用量（メモリ）
    return view


# ---------------------------------------------------------------- 実際にやった担い手の記録（runs）

def runs_close(task: dict, result: str, **extra: object) -> None:
    """開いている最後の記録（result が running / sent）を閉じる。無ければ何もしない。"""
    runs = task.get("runs") or []
    if runs and runs[-1].get("result") in ("running", "sent"):
        last = runs[-1]
        last["result"] = result
        last["ended_at"] = _now()
        last["seconds"] = round(max(0.0, last["ended_at"] - float(last.get("started_at") or last["ended_at"])), 1)
        last.update({k: v for k, v in extra.items() if v is not None})


def runs_open(task: dict, member: dict, *, kind: str, cli: str = "", account: str = "", label: str = "",
              model: str = "") -> dict:
    """担い手を 1 回分記録する（何回目・誰・結果・時間・使用量）。前の開いたままの記録は「入れ替え」で閉じる。"""
    runs_close(task, "replaced")
    runs = task.setdefault("runs", [])
    entry = {
        "n": int(task.get("runs_total") or 0) + 1, "kind": kind, "member": member_key(member),
        "cli": cli or str(member.get("cli") or ""), "account": account or str(member.get("account") or ""),
        "label": label or str(member.get("label") or ""), "model": model,
        "started_at": _now(), "ended_at": None, "seconds": None, "result": "running" if kind == "headless" else "sent",
        "usage": None,
    }
    task["runs_total"] = entry["n"]
    runs.append(entry)
    del runs[:-RUNS_MAX]
    return entry


def set_notify(team_id: str, enabled: object) -> dict:
    if not isinstance(enabled, bool):
        raise TeamError("通知の入り切りは真偽で指定してください")
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        team["notify"] = enabled
        _save(teams)
    return team


def set_roles(team_id: str, roles: object) -> dict:
    """役割を丸ごと置き換える（名前・数・プライマリ・セカンダリ・説明）。外した役割のタスクは役割なしへ。"""
    from app.commander import team_roles

    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        cleaned = team_roles.clean_roles(roles, previous=team.get("roles", []))
        ids = {r["id"] for r in cleaned}
        for task in team.get("tasks", []):
            if task.get("role") and task["role"] not in ids:
                task["role"] = ""
        team["roles"] = cleaned
        _save(teams)
    return team


def set_router(team_id: str, router: object) -> dict:
    """ルーターの任命。null なら既定のルーターに戻す。"""
    from app.commander import team_roles

    ep = team_roles.clean_endpoint_or_none(router, "ルーター")
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        team["router"] = None if ep is None else {**ep, "kind": "headless"}
        _save(teams)
    return team


def create_task(team_id: str, fields: dict, by: str) -> dict:
    title = clean_line(fields.get("title", ""), "タイトル", TITLE_MAX)
    goal = clean_text(fields.get("goal", ""), "目的")
    acceptance = clean_text(fields.get("acceptance", ""), "受け入れ条件")
    files = clean_files(fields.get("files", []))
    checks = clean_checks(fields.get("checks", []))
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        assignee = _check_assignee(team, fields.get("assignee", ""))
        task = {
            "id": int(team.get("next_task_id") or 1), "title": title, "goal": goal,
            "acceptance": acceptance, "files": files, "checks": checks, "assignee": assignee, "status": "todo",
            "role": _check_role(team, fields.get("role")),
            "report": None, "created_at": _now(), "updated_at": _now(), "history": [],
        }
        team["next_task_id"] = task["id"] + 1
        team.setdefault("tasks", []).append(task)
        _event(task, "作成", by)
        _save(teams)
        return _task_view(team, task)


def update_task(team_id: str, task_id: int, fields: dict, by: str) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        changed: list[str] = []
        if "title" in fields:
            task["title"] = clean_line(fields["title"], "タイトル", TITLE_MAX)
            changed.append("タイトル")
        if "goal" in fields:
            task["goal"] = clean_text(fields["goal"], "目的")
            changed.append("目的")
        if "acceptance" in fields:
            task["acceptance"] = clean_text(fields["acceptance"], "受け入れ条件")
            changed.append("受け入れ条件")
        if "files" in fields:
            task["files"] = clean_files(fields["files"])
            changed.append("担当ファイル")
        if "checks" in fields:
            task["checks"] = clean_checks(fields["checks"])
            changed.append("確認のコマンド")
        if "role" in fields and fields["role"] != task.get("role"):
            task["role"] = _check_role(team, fields["role"])
            changed.append("役割")
        if "assignee" in fields and fields["assignee"] != task.get("assignee"):
            task["assignee"] = _check_assignee(team, fields["assignee"])
            changed.append("担当")
        if "status" in fields and fields["status"] != task.get("status"):
            if fields["status"] not in STATUSES:
                raise TeamError(f"状態は {' / '.join(STATUSES)} のどれかにしてください")
            _event(task, f"状態 {task['status']} → {fields['status']}", by)
            task["status"] = fields["status"]
        if changed:
            _event(task, "変更: " + "・".join(changed), by)
        _save(teams)
        return _task_view(team, task)


def delete_task(team_id: str, task_id: int) -> dict:
    try:
        from app.commander import team_runner
        team_runner.stop_run(team_id, task_id)
    except Exception:  # noqa: BLE001 - 削除は止めない
        logger.exception("実行を止められませんでした")
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        team["tasks"].remove(task)
        _save(teams)
        return task


# ---------------------------------------------------------------- 送り込み・取り込み

def dispatch_text(task: dict, brief: str = "") -> str:
    files = ", ".join(task.get("files") or []) or "指定なし"
    head = f"【チームの前提】\n{brief}\n\n" if brief else ""
    return head + (
        f"【Nexarch タスク #{task['id']}】{task['title']}\n"
        f"目的: {task.get('goal') or '（なし）'}\n"
        f"受け入れ条件: {task.get('acceptance') or '（なし）'}\n"
        f"担当ファイル: {files}\n"
        "終わったら最後に次の 5 行で報告してください:\n"
        "完了報告: <何をしたか 1 行>\n"
        "変更ファイル: <パスを , で>\n"
        "試験: <実行した試験と結果>\n"
        "確認: <実行したコマンドと、その結果の要点>\n"
        "残り: <残っていること。無ければ なし>"
    )


def _assignee_console(task: dict) -> dict:
    sid = task.get("assignee") or ""
    if not sid:
        raise TeamError("担当が決まっていません")
    row = _console_ids().get(sid)
    if row is None:
        raise TeamError("担当のコンソールが閉じています", 409)
    return row


def _reset_command(row: dict) -> str:
    """担当の CLI に応じた会話のリセットのコマンド。CLI が分からなければ空（飛ばす）。"""
    return RESET_COMMANDS.get(str(row.get("commander_cli") or ""), "")


def dispatch_task(team_id: str, task_id: int, by: str, handoff: str = "") -> dict:
    """担当へ送る。担当が headless なら実行（team_runner）、console なら画面へ送り込む。

    handoff は上限で止まった前の担当からの引き継ぎの文（本文の後ろに付ける）。
    """
    with _lock:
        team = _find_team(_load(), team_id)
        task = _find_task(team, task_id)
        if task["status"] == "done":
            raise TeamError("完了したタスクは送れません", 409)
        assignee_member = find_member(team, str(task.get("assignee") or ""))
    if assignee_member is not None and is_headless(assignee_member):
        from app.commander import team_runner
        return team_runner.start_run(team_id, task_id, by, extra=handoff)
    with _lock:
        team = _find_team(_load(), team_id)
        task = _find_task(team, task_id)
        row = _assignee_console(task)
        state = row.get("commander_state") or (status.WAITING if row.get("alive", True) else status.EXITED)
        text = dispatch_text(task, str(team.get("brief") or ""))
        handoff = handoff or str(task.get("resume_note") or "")
        if handoff:
            text += "\n\n" + handoff
        reset = _reset_command(row) if team.get("reset_before_dispatch", True) is not False else ""
    if state == status.EXITED:
        raise TeamError("担当のコンソールは終了しています", 409)
    if state == status.WORKING:
        raise TeamError("作業中です。終わってから送ります", 409)
    sid = str(row["id"])
    try:
        if reset:
            manager.inject_input(sid, reset, submit=True)
            time.sleep(RESET_WAIT)  # 呼び出しは backend のスレッド（同期 endpoint）。event loop は止めない
        manager.inject_input(sid, text, submit=True)
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise TeamError(f"担当のコンソールへ送れませんでした: {exc}", 409) from exc
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        task["status"] = "doing"
        task["attempts"] = 0
        task.pop("checks_result", None)
        task.pop("run", None)
        task.pop("wait_until", None)
        task.pop("resume_note", None)
        task.pop("blocked_reason", None)
        if reset:
            _event(task, f"会話をリセット（{reset}）", by)
        for key in ("stalled_at", "limited_at", "closed_noted", "notified"):
            task.pop(key, None)  # 新しい作業として見張りを数え直す
        _event(task, "担当へ送信" + ("（引き継ぎつき）" if handoff else ""), by)
        runs_open(task, assignee_member or {"session_id": sid}, kind="console",
                  cli=str(row.get("commander_cli") or ""), account=str(row.get("commander_account") or ""),
                  label=str(row.get("commander_label") or ""))
        _save(teams)
        return _task_view(team, task)


_PREFIX = r"^[\s>│|●•·\-\*]*"
_REPORT_RE = {
    "summary": re.compile(_PREFIX + r"完了報告\s*[:：]\s*(.*?)\s*$"),
    "changed_files": re.compile(_PREFIX + r"変更ファイル\s*[:：]\s*(.*?)\s*$"),
    "tests": re.compile(_PREFIX + r"試験\s*[:：]\s*(.*?)\s*$"),
    "verify": re.compile(_PREFIX + r"確認\s*[:：]\s*(.*?)\s*$"),
    "remaining": re.compile(_PREFIX + r"残り\s*[:：]\s*(.*?)\s*$"),
}
_PLACEHOLDER = re.compile(r"^<[^<>]*>$")
_NONE_WORDS = {"", "なし", "無し", "ない", "未実施", "-", "none", "n/a"}


def parse_report(text: str) -> dict | None:
    """画面の文字から、最後の「完了報告:」で始まる 5 行（確認の行が無い古い 4 行も可）の塊を読む。無ければ None。

    送った文面の見本（`完了報告: <何をしたか 1 行>`）が画面に残っているので、`<…>` のままの塊は
    報告とみなさない。
    """
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        first = _REPORT_RE["summary"].match(lines[start])
        if not first:
            continue
        values = {"summary": first.group(1)}
        cursor = start + 1
        for key in ("changed_files", "tests", "verify", "remaining"):
            while cursor < len(lines) and not lines[cursor].strip():
                cursor += 1  # 空行は読み飛ばす
            matched = _REPORT_RE[key].match(lines[cursor]) if cursor < len(lines) else None
            if not matched:
                if key == "verify":
                    values["verify"] = ""  # 古い 4 行の形（確認の行なし）
                    continue
                break
            values[key] = matched.group(1)
            cursor += 1
        else:
            if _PLACEHOLDER.match(values["summary"]):
                continue  # 見本のまま
            return _normalize_report(values)
    return None


def _split_files(value: str) -> list[str]:
    if value.strip().lower() in _NONE_WORDS:
        return []
    return [p for p in (s.strip() for s in re.split(r"[,、]", value)) if p][:FILES_MAX]


def _normalize_report(values: dict) -> dict:
    changed = values.get("changed_files", [])
    if isinstance(changed, str):
        changed = _split_files(changed)
    return {
        "summary": str(values.get("summary", ""))[:REPORT_FIELD_MAX],
        "changed_files": [str(p)[:FILE_MAX] for p in changed][:FILES_MAX],
        "tests": str(values.get("tests", ""))[:REPORT_FIELD_MAX],
        "verify": str(values.get("verify", ""))[:REPORT_FIELD_MAX],
        "remaining": str(values.get("remaining", ""))[:REPORT_FIELD_MAX],
    }


def collect_report(team_id: str, task_id: int, by: str) -> dict:
    with _lock:
        team = _find_team(_load(), team_id)
        task = _find_task(team, task_id)
        sid = task.get("assignee") or ""
        if not sid:
            raise TeamError("担当が決まっていません")
        member = find_member(team, sid)
        if member is not None and is_headless(member):
            raise TeamError("ヘッドレスの担当の報告は実行の終わりに自動で取り込みます", 409)
        if sid not in _console_ids():
            raise TeamError("担当のコンソールが閉じています", 409)
    report = parse_report(manager.capture_text(sid, CAPTURE_LINES))
    if report is None:
        raise TeamError("まだ報告がありません", 404)
    return _store_report(team_id, task_id, report, by, "報告を取り込み（画面から）")


def put_report(team_id: str, task_id: int, fields: dict, by: str) -> dict:
    """人が手で報告を入れる。"""
    summary = clean_line(fields.get("summary", ""), "完了報告", REPORT_FIELD_MAX)
    changed = clean_files(fields.get("changed_files", []))
    tests = clean_text(fields.get("tests", ""), "試験", REPORT_FIELD_MAX)
    verify = clean_text(fields.get("verify", ""), "確認", REPORT_FIELD_MAX)
    remaining = clean_text(fields.get("remaining", ""), "残り", REPORT_FIELD_MAX)
    report = {"summary": summary, "changed_files": changed, "tests": tests, "verify": verify, "remaining": remaining}
    return _store_report(team_id, task_id, report, by, "報告を手で入力")


def _store_report(team_id: str, task_id: int, report: dict, by: str, event: str) -> dict:
    with _lock:
        teams = _load()
        team = _find_team(teams, team_id)
        task = _find_task(team, task_id)
        task["report"] = report
        task["status"] = "review"
        _event(task, event, by)
        runs_close(task, "reported" if (task.get("runs") or [{}])[-1].get("kind") == "console" else "ok")
        mark_checks_running(task)
        _save(teams)
        view = _task_view(team, task)
    if task.get("checks"):
        from app.commander import team_checks
        team_checks.schedule(team_id, task_id)
    return view


def mark_checks_running(task: dict) -> None:
    """報告が入ったとき、確認のコマンドがあれば実行中の印を付け、無ければ前の結果を消す（排他の中で呼ぶ）。"""
    if task.get("checks"):
        task["checks_result"] = {"status": "running", "ran_at": _now(), "results": []}
    else:
        task.pop("checks_result", None)


# ---------------------------------------------------------------- 一覧と見張り

def _headless_state(task: dict) -> str:
    """ヘッドレスの担当の状態。working（実行中）・limited・waiting（それ以外）。"""
    run = task.get("run")
    if not isinstance(run, dict):
        return "waiting"
    return {"running": "working", "limited": "limited"}.get(str(run.get("status")), "waiting")


def parallel_summary(team: dict) -> dict:
    """板の上に出す「同時に動いている数／上限」と待ちの数。"""
    tasks = team.get("tasks", [])
    return {
        "running": sum(1 for t in tasks if t.get("status") == "doing"),
        "max": int(team.get("max_parallel") or 3),
        "waiting": sum(1 for t in tasks if t.get("status") == "todo"),
        "local_max": int(team.get("local_parallel") or 1),
    }


def list_teams() -> list[dict]:
    """全チーム。タスクに担当の状態（assignee_state）と、doing の報告の有無（report_ready）を添える。

    capture は 1 回の一覧につき担当ごとに 1 回。コンソールが閉じていれば assignee_state は "closed"。
    headless の担当は画面が無いので、実行（run）の状態から決める。
    """
    with _lock:
        teams = _load()
    rows = _console_ids() if any(t.get("tasks") or t.get("members") for t in teams) else {}
    captured: dict[str, bool] = {}
    out: list[dict] = []
    for team in teams:
        headless_keys = {member_key(m) for m in team.get("members", []) if is_headless(m)}
        tasks = []
        for task in team.get("tasks", []):
            sid = task.get("assignee") or ""
            view = _task_view(team, task)
            if sid in headless_keys:
                view["assignee_state"] = _headless_state(task)
                view["report_ready"] = False
                tasks.append(view)
                continue
            row = rows.get(sid) if sid else None
            view["assignee_state"] = (row.get("commander_state") or status.WAITING) if row else ("closed" if sid else "")
            ready = False
            if row is not None and task.get("status") == "doing":
                if sid not in captured:
                    try:
                        captured[sid] = parse_report(manager.capture_text(sid, CAPTURE_LINES)) is not None
                    except (KeyError, OSError, RuntimeError, ValueError):
                        captured[sid] = False
                ready = captured[sid]
            view["report_ready"] = ready
            tasks.append(view)
        members = []
        for m in team.get("members", []):
            row = {**m, "kind": "headless" if is_headless(m) else "console", "key": member_key(m)}
            if is_headless(m) and m.get("cli") == "codex":
                from app.commander import team_runner
                row["no_sandbox"] = team_runner.member_sandbox_free(m)  # この機械では codex のサンドボックスが使えない
            members.append(row)
        from app.commander import team_roles

        out.append({**team, "members": members, "tasks": tasks, "parallel": parallel_summary(team),
                    "roles": team_roles.role_views(team), "router": team.get("router"),
                    "router_effective": team_roles.router_endpoint(team), "limits": team.get("limits", {}),
                    "on_limit": team.get("on_limit", "handoff"), "fallback": team.get("fallback", []),
                    "max_parallel": team.get("max_parallel", 3), "local_parallel": team.get("local_parallel", 1),
                    "auto_run": team.get("auto_run", True),
                    "codex_without_sandbox": team.get("codex_without_sandbox", True), "workdir": team.get("workdir", ""),
                    "workdir_folder": team.get("workdir_folder", ""), "workdir_name": team.get("workdir_name", "")})
    return out
