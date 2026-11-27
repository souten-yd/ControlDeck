"""Nexarch の AI チームのルーター（T3b）: 目標からチームを自動で作る。

ルーター = 任命された担い手（headless の endpoint）。目標（文）と作業場所のファイルの一覧（上位 200 件・shell なしで列挙）・
役割の一覧・出力のスキーマを渡して **ヘッドレスで 1 回だけ**走らせ（T3a の team_runner。読むだけ・書かせない）、JSON の計画を受け取る。

- 出力: `{brief, roles: {役割の名前: 必要数}, tasks: [{title, goal, acceptance, files, role, checks}]}`（tasks は 1〜20）。
  claude は `--output-format json --json-schema`、codex は `--output-schema`、opencode は本文で JSON を求める。
- 検証はこのコード（スキーマに頼らない）。形が違えば 1 回だけ「形が違う」と言って再実行し、それでもだめなら止めて通知。
  役割の名前は既存のものだけ（外は捨てる）・数は 1〜4 に丸める・確認のコマンドは T2b の許す形（1 行・制御文字なし・shlex で割れる・
  シェルの演算子なし）だけ通し、通らないものは捨てて履歴に残す。
- 返ったら brief をチームの前提に、tasks を板（待ち）に、roles の数を役割の count に。auto_run なら配り手がそのまま動かす。
- ルーターの実行も team["router_runs"] に残す（時間・使用量・結果）。本文・出力・鍵はログにも通知にも出さない。
根拠: 利用者（2026-11-27）「チームはルーターが自動で作成して欲しい」「ルーターを任命する感じで」「既定のルーター設定ができると良い」。
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import threading
import time

from app.commander import team_roles, team_runner, teams, workdir

logger = logging.getLogger(__name__)

TASKS_MAX = 20
FILES_LISTED = 200
GOAL_MAX = 4000
RUNS_KEPT = 10
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache", "target"}
SHELL_OPERATORS = {"|", "||", "&&", "&", ";", ">", ">>", "<", "<<", "2>", "2>&1"}
_running_guard = threading.Lock()


class PlanError(Exception):
    """ルーターの出力の形が違う。メッセージは再実行の指示に使う（本文は入れない）。"""


# ---------------------------------------------------------------- スキーマ・本文

def schema_for(role_names: list[str]) -> dict:
    """出力のスキーマ。codex の厳密な形（全部必須・additionalProperties なし）に合わせ、役割の名前を固定で並べる。"""
    task = {
        "type": "object", "additionalProperties": False,
        "required": ["title", "goal", "acceptance", "files", "role", "checks"],
        "properties": {
            "title": {"type": "string"}, "goal": {"type": "string"}, "acceptance": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "role": {"type": "string", "enum": list(role_names)} if role_names else {"type": "string"},
            "checks": {"type": "array", "items": {"type": "string"}},
        },
    }
    return {
        "type": "object", "additionalProperties": False, "required": ["brief", "roles", "tasks"],
        "properties": {
            "brief": {"type": "string"},
            "roles": {"type": "object", "additionalProperties": False, "required": list(role_names),
                      "properties": {n: {"type": "integer", "minimum": 1, "maximum": team_roles.COUNT_MAX} for n in role_names}},
            "tasks": {"type": "array", "minItems": 1, "maxItems": TASKS_MAX, "items": task},
        },
    }


def list_files(cwd, limit: int = FILES_LISTED) -> list[str]:
    """作業場所のファイルの一覧（相対パス・上位 limit 件）。shell なし・.git などは辿らない・シンボリックリンクは辿らない。"""
    out: list[str] = []
    root = str(cwd)
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            out.append(os.path.relpath(os.path.join(current, name), root))
            if len(out) >= limit:
                return out
    return out


def build_prompt(team: dict, goal: str, files: list[str]) -> str:
    roles = "\n".join(f"- {r['name']}（最大 {r['count']} 人）: {r.get('note') or '（説明なし）'}" for r in team.get("roles", []))
    listing = "\n".join(files) if files else "（空の作業場所）"
    example = {"brief": "チームの前提（目的・守ること）", "roles": {r["name"]: r["count"] for r in team.get("roles", [])},
               "tasks": [{"title": "短い題", "goal": "目的", "acceptance": "受け入れ条件", "files": ["src/a.py"],
                          "role": (team.get("roles") or [{"name": ""}])[0]["name"], "checks": ["pytest -q"]}]}
    return (
        "あなたは開発チームのルーターです。次の目標を、役割ごとに分担できるタスクに分けてください。"
        "ファイルは変更せず、読むだけにして、**JSON だけ**を返してください（前後の説明・コードブロックは不要）。\n\n"
        f"【目標】\n{goal}\n\n【作業場所のファイル（上位 {FILES_LISTED} 件）】\n{listing}\n\n【役割】\n{roles}\n\n"
        f"【出力の形】brief（チームの前提）・roles（役割の名前→必要な人数 1〜{team_roles.COUNT_MAX}。上の役割の名前だけ）・"
        f"tasks（1〜{TASKS_MAX} 件。title・goal・acceptance・files・role（上の役割の名前）・checks）。\n"
        "確認のコマンド（checks）は、この作業場所でそのまま実行できるものだけ（1 行・パイプや && は使えない）。"
        "実行できるか分からないものは書かない。\n"
        f"例: {json.dumps(example, ensure_ascii=False)}")


# ---------------------------------------------------------------- 出力の検証

def extract_json(text: str) -> object | None:
    """出力から JSON の本体を取り出す（そのまま・コードブロック・最初の { から最後の }）。読めなければ None。"""
    text = (text or "").strip()
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def valid_check(cmd: object) -> str | None:
    """確認のコマンドが T2b の許す形か。通れば整えた文字列、通らなければ None。"""
    try:
        text = teams.clean_line(cmd, "確認のコマンド", teams.CHECK_MAX)
        argv = shlex.split(text)
    except (teams.TeamError, ValueError):
        return None
    if not argv or any(token in SHELL_OPERATORS for token in argv):
        return None
    return text


def _lines(value: object, limit: int, maximum: int) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        try:
            text = teams.clean_line(item, "項目", maximum, required=False)
        except teams.TeamError:
            continue
        if text and text not in out:
            out.append(text)
    return out[:limit]


def validate_plan(obj: object, team: dict) -> dict:
    """ルーターの出力を検証して整える。形が違えば PlanError。返す dict: brief・roles（id→数）・tasks・dropped（捨てたものの記録）。"""
    if not isinstance(obj, dict):
        raise PlanError("全体が JSON のオブジェクトではありません")
    brief = obj.get("brief")
    if not isinstance(brief, str):
        raise PlanError("brief が文字列ではありません")
    roles_in = obj.get("roles")
    if not isinstance(roles_in, dict):
        raise PlanError("roles が {役割の名前: 数} のオブジェクトではありません")
    tasks_in = obj.get("tasks")
    if not isinstance(tasks_in, list) or not 1 <= len(tasks_in) <= TASKS_MAX:
        raise PlanError(f"tasks が 1〜{TASKS_MAX} 件のリストではありません")
    by_name = {r["name"]: r for r in team.get("roles", [])}
    dropped: list[str] = []
    counts: dict[str, int] = {}
    for name, raw in roles_in.items():
        role = by_name.get(name)
        if role is None:
            dropped.append(f"役割「{str(name)[:20]}」は既存の役割にないので捨てました")
            continue
        try:
            number = int(raw) if not isinstance(raw, bool) else 1
        except (TypeError, ValueError):
            raise PlanError(f"roles の「{name}」の数が整数ではありません") from None
        clamped = max(1, min(team_roles.COUNT_MAX, number))
        if clamped != number:
            dropped.append(f"役割「{name}」の数 {number} を {clamped} に丸めました")
        counts[role["id"]] = clamped
    tasks: list[dict] = []
    for index, item in enumerate(tasks_in, 1):
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip() \
                or not isinstance(item.get("goal"), str):
            raise PlanError(f"tasks[{index}] に title と goal（文字列）がありません")
        role_name = item.get("role")
        role = by_name.get(role_name) if isinstance(role_name, str) else None
        if role_name not in (None, "") and role is None:
            dropped.append(f"タスク {index} の役割「{str(role_name)[:20]}」は既存の役割にないので役割なしにしました")
        checks: list[str] = []
        for cmd in item.get("checks") if isinstance(item.get("checks"), list) else []:
            ok = valid_check(cmd)
            if ok and ok not in checks:
                checks.append(ok)
            else:
                dropped.append(f"タスク {index} の確認のコマンドを捨てました（許す形ではありません）: {str(cmd)[:80]}")
        tasks.append({
            "title": " ".join(item["title"].split())[:teams.TITLE_MAX],
            "goal": _text(item.get("goal")), "acceptance": _text(item.get("acceptance")),
            "files": _lines(item.get("files"), teams.FILES_MAX, teams.FILE_MAX),
            "role": role["id"] if role else "", "checks": checks[:teams.CHECKS_MAX],
        })
    return {"brief": _text(brief, teams.BRIEF_MAX), "roles": counts, "tasks": tasks, "dropped": dropped}


def _text(value: object, maximum: int = teams.TEXT_MAX) -> str:
    text = value if isinstance(value, str) else ""
    text = "".join(c for c in text if c in "\n\t" or (ord(c) >= 0x20 and ord(c) != 0x7F)).strip()
    return text[:maximum]


# ---------------------------------------------------------------- 計画を板へ

def apply_plan(team_id: str, plan: dict) -> dict:
    """検証済みの計画を板へ（前提・役割の数・タスク）。作ったタスクの数を返す。"""
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        if plan["brief"]:
            team["brief"] = plan["brief"]
        for role in team.get("roles", []):
            if role["id"] in plan["roles"]:
                role["count"] = plan["roles"][role["id"]]
        made = 0
        for item in plan["tasks"]:
            task = {
                "id": int(team.get("next_task_id") or 1), **{k: item[k] for k in ("title", "goal", "acceptance", "files", "checks", "role")},
                "assignee": "", "status": "todo", "report": None, "created_at": teams._now(),  # noqa: SLF001
                "updated_at": teams._now(), "history": [],  # noqa: SLF001
            }
            team["next_task_id"] = task["id"] + 1
            team.setdefault("tasks", []).append(task)
            teams._event(task, "作成（ルーター）", "ルーター")  # noqa: SLF001
            for note in plan["dropped"]:
                if note.startswith(f"タスク {plan['tasks'].index(item) + 1} の"):
                    teams._event(task, note, "ルーター")  # noqa: SLF001
            made += 1
        _finish_run(team, "done", tasks=made, dropped=plan["dropped"][:10])
        teams._save(all_teams)  # noqa: SLF001
    return {"tasks": made}


# ---------------------------------------------------------------- 実行

def _finish_run(team: dict, result: str, **extra: object) -> None:
    run = team.get("router_run") if isinstance(team.get("router_run"), dict) else {}
    run.update({"status": result, "ended_at": teams._now(), **extra})  # noqa: SLF001
    team["router_run"] = run
    runs = team.setdefault("router_runs", [])
    runs.append({k: run.get(k) for k in ("status", "started_at", "ended_at", "endpoint", "seconds", "usage", "tasks", "reason")})
    del runs[:-RUNS_KEPT]


def _fail(team_id: str, reason: str, *, seconds: float = 0.0, usage: dict | None = None) -> None:
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        try:
            team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        except teams.TeamError:
            return
        _finish_run(team, "failed", reason=reason[:200], seconds=seconds, usage=usage)
        notify = team.get("notify", True) is not False
        teams._save(all_teams)  # noqa: SLF001
    if notify:
        team_runner._notify([{"title": "Nexarch", "message": f"Nexarch: チーム『{team['name'][:40]}』をルーターが作れませんでした（{reason[:80]}）"}])  # noqa: SLF001


def _attempt(team_id: str, member: dict, text: str, cwd, schema: dict, allow_no_sandbox: bool) -> dict:
    key = (team_id, 0)
    with team_runner._active_lock:  # noqa: SLF001
        team_runner._active[key] = {"proc": None, "stop": False, "live": {}, "started": time.monotonic()}  # noqa: SLF001
    try:
        return team_runner._execute(key, member, text, cwd, allow_no_sandbox, schema)  # noqa: SLF001
    except team_runner.LaunchError as exc:
        return {"launch_error": str(exc)[:200]}
    finally:
        with team_runner._active_lock:  # noqa: SLF001
            team_runner._active.pop(key, None)  # noqa: SLF001


def _worker(team_id: str, goal: str) -> None:
    try:
        _run(team_id, goal)
    except Exception:  # noqa: BLE001 - スレッドの失敗で落とさない
        logger.exception("Nexarch のルーターの実行に失敗しました")
        _fail(team_id, "内部エラー")
        return
    try:
        from app.commander import team_dispatch

        team_dispatch.kick(team_id)  # auto_run なら、そのまま動かし始める
    except Exception:  # noqa: BLE001
        logger.exception("配り手を動かせませんでした")


def _run(team_id: str, goal: str) -> None:
    with teams._lock:  # noqa: SLF001
        team = teams._find_team(teams._load(), team_id)  # noqa: SLF001
        ep = team_roles.router_endpoint(team)
        allow_no_sandbox = team.get("codex_without_sandbox", True) is not False
    path = teams.ensure_workdir(team_id)
    try:
        cwd = workdir.resolve_dir(path)
    except (OSError, ValueError) as exc:
        _fail(team_id, f"作業場所を使えません: {exc}")
        return
    with teams._lock:  # noqa: SLF001
        team = teams._find_team(teams._load(), team_id)  # noqa: SLF001
    schema = schema_for([r["name"] for r in team.get("roles", [])])
    base = build_prompt(team, goal, list_files(cwd))
    started = time.monotonic()
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    text = base
    reason = "形が違います"
    for attempt in (1, 2):
        result = _attempt(team_id, ep, text, cwd, schema, allow_no_sandbox)
        state = result.get("state")
        if state:
            for k in usage:
                usage[k] = round(usage[k] + state.usage.get(k, 0), 6)
        seconds = round(time.monotonic() - started, 1)
        if "launch_error" in result:
            _fail(team_id, result["launch_error"], seconds=seconds)
            return
        failing = result.get("exit") != 0 or bool(state and state.is_error) or result.get("timed_out")
        if failing:
            limited, _reset = team_runner.detect_limit(((state.errors if state else []) + [result.get("stderr", ""), result.get("final", "")]))
            _fail(team_id, "上限で止まりました" if limited else f"実行が失敗しました（終了コード {result.get('exit')}）",
                  seconds=seconds, usage=usage)
            return
        try:
            plan = validate_plan(extract_json(str(result.get("final") or "")), team)
        except PlanError as exc:
            reason = f"形が違います: {exc}"
            text = (base + f"\n\n【形が違います】前回の出力は受け取れませんでした（{exc}）。"
                    "指定の形の JSON だけをもう一度返してください。")
            continue
        with teams._lock:  # noqa: SLF001
            all_teams = teams._load()  # noqa: SLF001
            try:
                current = teams._find_team(all_teams, team_id)  # noqa: SLF001
            except teams.TeamError:
                return  # 実行の間にチームが消された
            current.setdefault("router_run", {}).update({"seconds": seconds, "usage": usage})
            teams._save(all_teams)  # noqa: SLF001
        apply_plan(team_id, plan)
        _audit(team_id, ep, seconds, "ok", len(plan["tasks"]))
        return
    _fail(team_id, reason, seconds=round(time.monotonic() - started, 1), usage=usage)
    _audit(team_id, ep, round(time.monotonic() - started, 1), "invalid", 0)


def _audit(team_id: str, ep: dict, seconds: float, result: str, tasks: int) -> None:
    try:
        from app.audit import service as audit
        from app.database import SessionLocal

        with SessionLocal() as db:
            audit.record(db, "commander.team.router", username="nexarch", resource_type="commander_team",
                         resource_id=team_id, metadata={"cli": ep.get("cli"), "account": ep.get("account"), "model": ep.get("model"),
                                                       "effort": ep.get("effort"), "seconds": seconds, "result": result, "tasks": tasks})
    except Exception:  # noqa: BLE001 - 監査の失敗で結果を失わない
        logger.exception("ルーターの監査ログを書けませんでした")


def start(team_id: str, goal: object) -> dict:
    """ルーターを 1 回、別スレッドで始める（画面は一覧を見て進み具合を知る）。走っている間は断る。"""
    goal = teams.clean_text(goal, "目標", GOAL_MAX)
    if not goal:
        raise teams.TeamError("目標を入力してください")
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        run = team.get("router_run")
        if isinstance(run, dict) and run.get("status") == "running":
            raise teams.TeamError("ルーターは実行中です", 409)
        ep = team_roles.router_endpoint(team)
        team["router_run"] = {"status": "running", "started_at": teams._now(), "endpoint": team_roles.endpoint_label(ep),  # noqa: SLF001
                              "goal": goal[:300]}
        teams._save(all_teams)  # noqa: SLF001
        view = dict(team)
    team_runner._spawn(_worker, team_id, goal)  # noqa: SLF001
    return view


def create_and_start(goal: object, name: object = "", work_path: object = "") -> dict:
    """「目標からチームを作る」: チームを作り（役割・ルーターは既定を写す）、ルーターを走らせる。"""
    goal_text = teams.clean_text(goal, "目標", GOAL_MAX)
    if not goal_text:
        raise teams.TeamError("目標を入力してください")
    title = name if isinstance(name, str) and name.strip() else " ".join(goal_text.split())[:teams.NAME_MAX]
    path = ""
    if work_path not in (None, ""):
        if not isinstance(work_path, str):
            raise teams.TeamError("作業場所は文字列で指定してください")
        try:
            path = str(workdir.resolve_dir(work_path))
        except (OSError, ValueError) as exc:
            raise teams.TeamError(f"作業場所を使えません: {exc}") from exc
    team = teams.create_team(title, workdir_path=path)
    return start(team["id"], goal_text)


def recover_interrupted() -> int:
    """ControlDeck の再起動で止まった（running のままの）ルーターの実行を失敗にする。"""
    count = 0
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        for team in all_teams:
            run = team.get("router_run")
            if isinstance(run, dict) and run.get("status") == "running" and not team_runner.is_active(team["id"], 0):
                _finish_run(team, "failed", reason="ControlDeck の再起動で中断されました")
                count += 1
        if count:
            teams._save(all_teams)  # noqa: SLF001
    return count

