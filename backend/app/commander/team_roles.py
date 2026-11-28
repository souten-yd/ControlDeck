"""Nexarch の AI チームの役割・担い手の指定・プライマリ／セカンダリ（T3b・コード・LLM なし）。

- 担い手の指定（endpoint）: `{kind: headless|console, cli, account, model, effort}`。コンソール側（kind・cli・account）と
  モデル側（model・effort）を別々に持つ。model・effort が空なら CLI の既定のまま（引数を付けない）。
- 役割（role）: `{id, name, count（同時に呼ぶ最大数 1〜4）, primary, secondary（0〜3）, note}`。タスクは role を持ち、
  配り手が役割の数まで担い手を呼び出す（headless は自動のメンバー、console は Nexarch のコンソールを自動で開く）。
- 移る・戻る: 担い手が上限で止まったら、その役割の chain（primary → secondary の順）の次の使える endpoint へ引き継ぐ。
  止まった endpoint は回復の時刻まで使わない（team["limits"]）。**回復の時刻が過ぎたら、次のタスクからはプライマリへ戻す**
  （走っているタスクは止めない）。戻ったら履歴に「プライマリ（〇〇）に戻りました」。
- 引き継ぎ先が無い（chain が尽きた・未設定）: タスクは blocked（blocked_reason=no_handoff）で通知。画面の「引き継ぎ先を選ぶ」から
  endpoint を選ぶとその場で引き継いで再開し、チェックを付ければその役割の secondary へ足す（choose_handoff）。
- 鍵のファイルは読まない・外へは何も取りに行かない。
根拠: 利用者（2026-11-27）「ロールの設定をできるようにして、それに基づいて必要数を呼び出すイメージ。プライマリとセカンダリーも。
期限切れでセカンダリに移行しても回復後にはプライマリに戻れると良い」「トークンが尽きた場合、プライマリ（引き継ぎ先）の設定が無い場合は
止まるが、そのコンソールの設定を押すと引き継ぎ先を選べる」と docs/PLAN-nexarch-team.md T3。
"""
from __future__ import annotations

import logging
import secrets
import time

from app.commander import accounts, models, teams

logger = logging.getLogger(__name__)

KINDS = ("headless", "console")
COUNT_MAX = 4
SECONDARY_MAX = 3
ROLES_MAX = 8
ROLE_NAME_MAX = 20
ROLE_NOTE_MAX = 200
ROLE_ID_MAX = 16
RECOVER_UNKNOWN = 30 * 60   # 回復の時刻が読めないときの待ち（秒）
# 段（tier。T3c）: 役割の名前がそのまま段の名前。チームリーダーは delegate に tier を渡すだけで、担い手は Nexarch が決める。
TIERS = ("explore", "implement", "review", "hard")
BUILTIN_ROLES = (
    {"name": "explore", "count": 2, "note": "探す・読む・調べる（安い担い手。読むだけ）"},
    {"name": "implement", "count": 3, "note": "ふつうの実装・試験を書く（worktree で書く）"},
    {"name": "review", "count": 1, "note": "評価・レビュー（強い・別の系列が望ましい。読むだけ）"},
    {"name": "hard", "count": 1, "note": "難しい設計・判断の要る不具合（チームリーダー自身が既定）"},
)
# 既定の割り当ての例（導入済みの CLI だけ・hard は未設定＝チームリーダー自身）
SUGGESTED = {"explore": {"cli": "opencode"}, "implement": {"cli": "claude", "model": "sonnet"}, "review": {"cli": "codex"}}
MEMBER_ROLE_OF_NAME = {"review": "reviewer", "explore": "researcher"}


# ---------------------------------------------------------------- 担い手の指定

def clean_endpoint(item: object, label: str = "担い手") -> dict:
    """endpoint の検証。kind・cli・account・model・effort だけを残す（余計な欄は捨てる）。"""
    if not isinstance(item, dict):
        raise teams.TeamError(f"{label}の形が正しくありません")
    kind = item.get("kind") or "headless"
    if kind not in KINDS:
        raise teams.TeamError(f"{label}の種類は headless か console にしてください")
    cli = item.get("cli")
    if cli not in teams.HEADLESS_CLIS:
        raise teams.TeamError(f"{label}の CLI は {' / '.join(teams.HEADLESS_CLIS)} のどれかにしてください")
    account = item.get("account") or accounts.DEFAULT_ACCOUNT
    if not isinstance(account, str) or not accounts.ACCOUNT_NAME_RE.fullmatch(account):
        raise teams.TeamError(f"{label}のアカウントの名前が正しくありません")
    try:
        exists = accounts.account_exists(accounts.CLIS[cli], account)
    except Exception:  # noqa: BLE001 - OpenCode の接続の一覧を取れないとき
        exists = account == accounts.DEFAULT_ACCOUNT
    if not exists:
        raise teams.TeamError(f"{label}のアカウントが見つかりません")
    try:
        model = models.clean_model(item.get("model"))
        effort = models.clean_effort(item.get("effort"))
    except ValueError as exc:
        raise teams.TeamError(str(exc)) from exc
    return {"kind": kind, "cli": cli, "account": account, "model": model, "effort": effort}


def clean_endpoint_or_none(item: object, label: str = "担い手") -> dict | None:
    return None if item in (None, "", {}) else clean_endpoint(item, label)


def endpoint_key(ep: dict) -> str:
    return ":".join(str(ep.get(k) or "") for k in ("kind", "cli", "account", "model", "effort"))


def endpoint_label(ep: dict) -> str:
    cli = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}.get(str(ep.get("cli")), str(ep.get("cli")))
    parts = [cli + (f"（{ep['account']}）" if ep.get("account") not in (None, "", accounts.DEFAULT_ACCOUNT) else "")]
    if ep.get("model"):
        parts.append(str(ep["model"]))
    if ep.get("effort"):
        parts.append(str(ep["effort"]))
    return "・".join(parts) + ("・画面あり" if ep.get("kind") == "console" else "")


# ---------------------------------------------------------------- 役割

def clean_roles(value: object, *, previous: list[dict] | None = None) -> list[dict]:
    """役割の一覧の検証（丸ごと置き換え）。id は引き継ぐ（無ければ作る）。名前は重ねない。"""
    if not isinstance(value, list):
        raise teams.TeamError("役割はリストで指定してください")
    if len(value) > ROLES_MAX:
        raise teams.TeamError(f"役割は {ROLES_MAX} 個までです")
    known = {r.get("id") for r in previous or []}
    out: list[dict] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise teams.TeamError("役割の形が正しくありません")
        name = teams.clean_line(item.get("name"), "役割の名前", ROLE_NAME_MAX)
        if name in names:
            raise teams.TeamError("役割の名前が重なっています")
        names.add(name)
        count = item.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= COUNT_MAX:
            raise teams.TeamError(f"役割の数は 1〜{COUNT_MAX} の整数にしてください")
        secondary = item.get("secondary") or []
        if not isinstance(secondary, list) or len(secondary) > SECONDARY_MAX:
            raise teams.TeamError(f"セカンダリは {SECONDARY_MAX} 個までのリストにしてください")
        rid = item.get("id")
        if not (isinstance(rid, str) and rid and len(rid) <= ROLE_ID_MAX and rid in known) \
                or any(r["id"] == rid for r in out):
            rid = "r" + secrets.token_hex(3)
        last = item.get("last_index")
        out.append({
            "id": rid, "name": name, "count": count, "note": teams.clean_text(item.get("note") or "", "役割の説明", ROLE_NOTE_MAX),
            "primary": clean_endpoint_or_none(item.get("primary"), "プライマリ"),
            "secondary": [clean_endpoint(s, "セカンダリ") for s in secondary],
            "last_index": last if isinstance(last, int) and not isinstance(last, bool) and 0 <= last <= SECONDARY_MAX else 0,
        })
    return out


def builtin_roles() -> list[dict]:
    return clean_roles([{**r, "primary": None, "secondary": []} for r in BUILTIN_ROLES])


def suggested_roles() -> list[dict]:
    """組み込みの 4 段に、導入済みの CLI の既定の割り当て（例）を入れたもの。入れられない段は未設定のまま。"""
    out = []
    for r in BUILTIN_ROLES:
        primary = None
        want = SUGGESTED.get(r["name"])
        if want:
            try:
                if accounts.is_installed(accounts.CLIS[want["cli"]]):
                    primary = clean_endpoint({"kind": "headless", "cli": want["cli"], "model": want.get("model", "")})
            except Exception:  # noqa: BLE001 - 接続の一覧を取れない等。未設定のまま
                primary = None
        out.append({**r, "primary": primary, "secondary": []})
    return clean_roles(out)


def tier_role(team: dict, tier: str) -> dict | None:
    """段（tier）の役割。名前が段の名前の役割。"""
    return find_role_by_name(team, tier)


def new_team_roles() -> list[dict]:
    """新しいチームの役割 = Nexarch の設定の既定の役割（無ければ組み込みの 3 つ）。id は新しく振る。"""
    from app.commander import settings as commander_settings

    source = commander_settings.default_roles()
    try:
        return clean_roles([{**r, "id": None, "last_index": 0} for r in source]) if source else suggested_roles()
    except teams.TeamError:
        logger.warning("既定の役割が正しくないので組み込みの役割を使います")
        return builtin_roles()


def new_team_leader() -> dict | None:
    from app.commander import settings as commander_settings

    return commander_settings.default_leader()


def find_role(team: dict, role_id: object) -> dict | None:
    return next((r for r in team.get("roles", []) if r.get("id") == role_id), None) if role_id else None


def find_role_by_name(team: dict, name: object) -> dict | None:
    return next((r for r in team.get("roles", []) if r.get("name") == name), None)


def chain(role: dict) -> list[dict]:
    """primary → secondary の順の endpoint（未設定の primary は飛ばす）。"""
    return ([role["primary"]] if role.get("primary") else []) + list(role.get("secondary") or [])


def leader_endpoint(team: dict) -> dict:
    """チームリーダーの担い手: チームのチームリーダー → 既定のチームリーダー → 導入済みの最初の CLI（claude・codex・opencode の順）。"""
    from app.commander import settings as commander_settings

    for ep in (team.get("leader"), commander_settings.default_leader()):
        if ep:
            return {**ep, "kind": "headless"}
    for cli in teams.HEADLESS_CLIS:
        try:
            if accounts.is_installed(accounts.CLIS[cli]):
                return {"kind": "headless", "cli": cli, "account": accounts.DEFAULT_ACCOUNT, "model": "", "effort": ""}
        except Exception:  # noqa: BLE001
            continue
    return {"kind": "headless", "cli": "claude", "account": accounts.DEFAULT_ACCOUNT, "model": "", "effort": ""}


# ---------------------------------------------------------------- 回復の時刻（止まった endpoint は使わない）

def mark_limited(team: dict, ep: dict, reset_at: float | None, now: float) -> float:
    until = float(reset_at) if reset_at and reset_at > now else now + RECOVER_UNKNOWN
    team.setdefault("limits", {})[endpoint_key(ep)] = {"until": until, "at": now}
    return until


def cooling_until(team: dict, ep: dict, now: float) -> float | None:
    """その endpoint が回復を待っている間なら、回復の時刻。過ぎていれば（もう使えるので）None。"""
    entry = (team.get("limits") or {}).get(endpoint_key(ep))
    if isinstance(entry, dict) and float(entry.get("until") or 0) > now:
        return float(entry["until"])
    return None


def member_endpoint(member: dict) -> dict | None:
    """メンバーを endpoint へ。自動のメンバーは保存した ep の欄から、手で足した headless は cli・account から。"""
    if teams.is_headless(member):
        return {"kind": "headless", "cli": member.get("cli"), "account": member.get("account") or accounts.DEFAULT_ACCOUNT,
                "model": member.get("model") or "", "effort": member.get("effort") or ""}
    if member.get("ep_def"):
        return dict(member["ep_def"])
    return None


# ---------------------------------------------------------------- メンバーの自動作成

def _busy(team: dict, member: dict) -> bool:
    key = teams.member_key(member)
    return any(t.get("assignee") == key and t.get("status") == "doing" for t in team.get("tasks", []))


def ensure_member(team: dict, role: dict, ep: dict) -> dict | None:
    """役割のこの endpoint の、空いている自動のメンバー（無ければ作る）。team を直接書き換える（保存は呼び出し側）。

    console は Nexarch のコンソールを自動で開く（作業場所はチームの作業場所）。開けなければ None。
    """
    key = endpoint_key(ep)
    live_sessions = None
    for member in team.get("members", []):
        if not member.get("auto") or member.get("role_id") != role["id"] or member.get("ep") != key or _busy(team, member):
            continue
        if teams.is_headless(member):
            return member
        if live_sessions is None:
            live_sessions = teams._console_ids()  # noqa: SLF001
        if member["session_id"] in live_sessions:
            return member
    member_role = MEMBER_ROLE_OF_NAME.get(role["name"], "developer")
    if ep["kind"] == "headless":
        member = {"id": "h" + secrets.token_hex(4), "kind": "headless", "cli": ep["cli"], "account": ep["account"],
                  "role": member_role, "label": role["name"], "model": ep["model"], "effort": ep["effort"],
                  "auto": True, "role_id": role["id"], "ep": key}
        team.setdefault("members", []).append(member)
        return member
    from app.commander import consoles

    cwd = teams.ensure_workdir_for(team)
    try:
        opened = consoles.create_consoles(cli=accounts.CLIS[ep["cli"]], account=ep["account"], cwd=cwd, count=1,
                                          label=role["name"], created_by="Nexarch", model=ep["model"], effort=ep["effort"])
    except (OSError, ValueError, RuntimeError, FileNotFoundError, PermissionError) as exc:
        logger.warning("役割のコンソールを開けませんでした: %s", exc)
        return None
    if not opened.get("opened"):
        logger.warning("役割のコンソールを開けませんでした: %s", opened.get("reason"))
        return None
    sid = str(opened["opened"][0]["id"])
    member = {"session_id": sid, "role": member_role, "auto": True, "role_id": role["id"], "ep": key, "ep_def": dict(ep)}
    team.setdefault("members", []).append(member)
    try:
        teams.manager.tag_session(sid, {"role": member_role})
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("役割の印を付けられませんでした（%s）: %s", sid, exc)
    return member


# ---------------------------------------------------------------- 配り手が使う選び方

def doing_in_role(team: dict, role: dict) -> int:
    return sum(1 for t in team.get("tasks", []) if t.get("status") == "doing" and t.get("role") == role["id"])


def pick_for_task(team: dict, task: dict, now: float | None = None) -> tuple[str, dict | None]:
    """役割のタスクの担い手を選ぶ。(mode, member)。team は直接書き換える（保存は呼び出し側）。

    - ("legacy", None): 役割が無い・役割に endpoint が 1 つも無い → 従来の選び方（担当・候補の順）へ。
    - ("wait", None): 役割の数が埋まっている・使える endpoint が無い（全部回復待ち）・コンソールが開けない → 今は配らない。
    - ("pick", member): この担い手へ。プライマリへ戻ったときは履歴に残す。
    """
    now = now or time.time()
    role = find_role(team, task.get("role"))
    eps = chain(role) if role else []
    if team.get("local_only"):  # 閉じたチーム: ローカル LLM の endpoint だけ（brief C・2026-11-28）
        eps = [e for e in eps if not teams.endpoint_local_problem(e)]
    if role is None or not eps:
        return "legacy", None
    if doing_in_role(team, role) >= int(role.get("count") or 1):
        return "wait", None
    for index, ep in enumerate(eps):
        if cooling_until(team, ep, now) is not None:
            continue
        member = ensure_member(team, role, ep)
        if member is None:
            return "wait", None
        if index == 0 and int(role.get("last_index") or 0) > 0:
            teams._event(task, f"プライマリ（{endpoint_label(ep)}）に戻りました", "Nexarch")  # noqa: SLF001
        role["last_index"] = index
        return "pick", member
    return "wait", None


def next_after_limit(team: dict, task: dict, role: dict, limited_keys: set[str], now: float) -> dict | None:
    """上限で止まった担い手の次の担い手（chain の使える次の endpoint）。無ければ None。"""
    for index, ep in enumerate(chain(role)):
        if team.get("local_only") and teams.endpoint_local_problem(ep):
            continue  # 閉じたチーム: ローカル LLM の endpoint だけ（brief C・2026-11-28）
        if cooling_until(team, ep, now) is not None or endpoint_key(ep) in limited_keys:
            continue
        member = ensure_member(team, role, ep)
        if member is None:
            continue
        role["last_index"] = index
        return member
    return None


# ---------------------------------------------------------------- 画面に出す役割の状態

def role_views(team: dict, now: float | None = None) -> list[dict]:
    """役割ごとに、今どの endpoint で動いているか（0=プライマリ）と回復待ちの印を添える。"""
    now = now or time.time()
    out: list[dict] = []
    for role in team.get("roles", []):
        eps = chain(role)
        states = []
        for ep in eps:
            until = cooling_until(team, ep, now)
            states.append({"key": endpoint_key(ep), "label": endpoint_label(ep), "recovers_at": until})
        index = next((i for i, s in enumerate(states) if s["recovers_at"] is None), None)
        primary_recovers = states[0]["recovers_at"] if states and role.get("primary") else None
        out.append({**role, "chain": states, "active_index": index, "running": doing_in_role(team, role),
                    "on_secondary": bool(role.get("primary") and primary_recovers is not None),
                    "primary_recovers_at": primary_recovers})
    return out


# ---------------------------------------------------------------- 引き継ぎ先を選ぶ

def choose_handoff(team_id: str, task_id: int, endpoint: object, add_to_secondary: bool, by: str) -> dict:
    """引き継ぎ先が無くて止まったタスクへ、選んだ endpoint で引き継いで再開する。

    チェックを付ければ（add_to_secondary）その役割のセカンダリへ足す（上限 3・同じものは足さない）。
    """
    ep = clean_endpoint(endpoint, "引き継ぎ先")
    from app.commander import team_dispatch

    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        task = teams._find_task(team, task_id)  # noqa: SLF001
        run = task.get("run") if isinstance(task.get("run"), dict) else {}
        state = task.get("status")
        stopped = state in ("blocked", "todo") or (state == "doing" and (task.get("limited_at") or run.get("status") == "limited"))
        if not stopped:
            raise teams.TeamError("このタスクは引き継げる状態ではありません（止まったタスクだけ）", 409)
        role = find_role(team, task.get("role"))
        pseudo_role = role or {"id": "", "name": "引き継ぎ"}
        member = ensure_member(team, pseudo_role, ep)
        if member is None:
            raise teams.TeamError("引き継ぎ先を用意できませんでした（コンソールを開けません）", 409)
        prev = teams.find_member(team, str(task.get("assignee") or ""))
        prev_label = team_dispatch.member_label(prev) if prev else "前の担当"
        if prev is not None and (pe := member_endpoint(prev)) is not None:
            mark_limited(team, pe, (task.get("run") or {}).get("reset_at"), time.time())
        git_cwd = team_dispatch._workdir_of(team, prev)  # noqa: SLF001
        from app.commander import team_runner

        git = team_runner.git_changes(git_cwd) if git_cwd else ""
        text = team_dispatch.handoff_text(prev_label, team_dispatch._last_work(task, None), git)  # noqa: SLF001
        teams.runs_close(task, "limited")
        task["assignee"] = teams.member_key(member)
        task["status"] = "todo"
        task["resume_note"] = text
        for k in ("limited_at", "wait_noted", "handoff_waiting_noted", "wait_until", "run", "write_fail_retries",
                  "blocked_reason", "notified"):
            task.pop(k, None)
        added = False
        if add_to_secondary and role is not None:
            keys = {endpoint_key(e) for e in chain(role)}
            if endpoint_key(ep) not in keys and len(role.get("secondary") or []) < SECONDARY_MAX:
                role.setdefault("secondary", []).append(ep)
                added = True
        teams._event(task, f"引き継ぎ先を選択 → {endpoint_label(ep)}" + ("（セカンダリに追加）" if added else ""), by)  # noqa: SLF001
        teams._save(all_teams)  # noqa: SLF001
    try:
        return teams.dispatch_task(team_id, task_id, by)
    except teams.TeamError as exc:
        logger.info("選んだ引き継ぎ先へすぐには送れませんでした（待ちに戻します）: %s", exc)
        with teams._lock:  # noqa: SLF001
            return teams._task_view(teams._find_team(teams._load(), team_id),  # noqa: SLF001
                                    teams._find_task(teams._find_team(teams._load(), team_id), task_id))  # noqa: SLF001
