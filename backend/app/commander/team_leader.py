"""Nexarch の AI チームのチームリーダーの実行（T3c。T3b の「1 回だけ計画を返す」を、MCP で Nexarch を動かす長い実行へ置き換えた）。

「頼む」（一文＋任意で作業場所）→ チームを作り → チームリーダーの endpoint（T3b で任命）をヘッドレスで長く動かす。
チームリーダーは MCP の道具 `nexarch_team`（leader_mcp.py）で、調べる・計画を書く・段（explore・implement・review・hard）で仕事を振る・
待つ・差分と結果を見る・確認を流す・統合する・利用者に聞く・終える。計画・仕事・実行・結果はすべて Nexarch が持つ。

- 起動の引数（`--help` で確かめた 2026-11-27 の版）: claude は `--mcp-config <0600 の設定ファイル> --strict-mcp-config --permission-mode dontAsk
  --allowedTools mcp__nexarch_team__*,Read,Glob,Grep`、codex は `exec -s read-only -c mcp_servers.nexarch_team.command=… args=… env_vars=…`
  （token は環境変数で渡し env_vars で橋渡しへ転送する）、opencode は provider の payload["mcp"]（設定の mcp に nexarch_team を足す）。
  どれも stdio の橋渡し（integrations/opencode/addon_mcp_bridge.py）が token つきで Nexarch の HTTP の口を呼ぶ。
- 上限: チームリーダーの endpoint が上限なら、チームリーダーのセカンダリ（チームのチームリーダー → hard 役割の primary・secondary の順）で
  `team_status` から再開（本文に「再開です。team_status を最初に読む」）。止めた endpoint は回復の時刻まで使わない。
  プロセスが落ちたら 1 回だけ同じ endpoint で再開。finish せずに終わったら「まだ終わっていない」として再開（最大 3 回。超えたら止めて通知）。
- 終わったら結果（何をしたか・確認の結果・変更ファイルと行数・残り・使用量の合計）を team["result"] へ。元から git の作業場所なら
  「取り込む」（land）でチームのブランチを元のブランチへ。Nexarch が git init した作業場所は終わりに元のブランチへ fast-forward。
- 実行の記録（team["leader_runs"]）・監査ログ commander.team.leader（本文・出力・鍵・token は入れない）。
根拠: 利用者（2026-11-27）「一文リーダーに依頼したら、調査・計画・振り分け・個別の実装と評価・統合して評価・試験してソフトを開発する
イメージ。従来 Claude がやっている Haiku や Sonnet を使って Opus が作業している流れを模倣。ただし LLM は横断的に。司令塔も Claude だけでなく
Codex・OpenRouter・OpenCode・ローカル LLM が使えるように」。
"""
from __future__ import annotations

import logging
import shlex
import threading
import time

from app.commander import leader_mcp, team_git, team_inputs, team_roles, team_runner, teams, workdir
from app.config import get_config

logger = logging.getLogger(__name__)

GOAL_MAX = 20000   # 依頼の上限（API の入口と同じ。2026-11-28 本番で 5000 字の仕様が「4000 文字以内」で断られた）
RUNS_KEPT = 30
MAX_RESUMES = 3
MAX_CRASH_RETRIES = 1
REQUESTS_KEPT = 20
SHELL_OPERATORS = {"|", "||", "&&", "&", ";", ">", ">>", "<", "<<", "2>", "2>&1"}
# 再起動からの自動再開の上限（brief D・2026-11-28）: 30 分に 2 回まで（teams.json へ永続）。
AUTO_RESUME_WINDOW = 30 * 60
AUTO_RESUME_MAX = 2


def mcp_url() -> str:
    return f"http://127.0.0.1:{get_config().server.port}/api/v1/commander/leader-mcp"


# ---------------------------------------------------------------- 検証（確認のコマンド）

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


# ---------------------------------------------------------------- 本文

PROMPT = """あなたは Nexarch のチームリーダーです。利用者の依頼を、チームを動かして完成させます。
**自分で大きな実装はしません。** 費用を抑えるため、次の流れ（Opus が Haiku・Sonnet を使う流れ）で進めてください。

【段（tier）】delegate の tier に渡すだけで、担い手は Nexarch が決めます。
- explore: 探す・読む・調べる（安い担い手。読むだけ）
- implement: ふつうの実装・試験を書く（worktree とブランチで書く）
- review: 評価・レビュー（強い担い手。読むだけ）
- hard: 難しい設計・判断の要る不具合だけ（自分でやるより hard へ）

【進め方】
1. team_status を最初に呼ぶ（状態・計画・質問が分かる。再開のときも同じ）。
2. 調べる: 作業場所のファイルは読める（書かない）。広い調べものは explore へ振る。Web・資料・コードの検索は research。
3. write_plan で計画を書く（利用者の画面に出る）。項目ごとに tier・depends_on・files・checks（確認のコマンド。1 行・パイプや && なし）。
4. delegate で仕事を振る。独立した implement は同時に振ってよい（worktree で隔離される）。wait で結果を待つ。
5. 結果を確かめる: wait の 5 行の報告と確認の合否・変更ファイル、必要なら read_result(diff)。足りなければ note つきで delegate して直させる。
6. integrate で終わった implement をチームのブランチへ統合する。競合は implement へ戻す。
7. run_checks(where="team") で統合後の全体を確かめ、review の担い手に全体を評価させる。
8. 作業場所が ControlDeck（./deck.sh がある）なら、7 の確認に `./deck.sh dev-check --base origin/main --skip-visual` を入れ、最後の行が「合格」になるまで直させる。
9. 最後に finish（何をしたか・確認の結果・残りを書く。完成できなければ outcome=blocked）。
進み具合は report で 1 行ずつ残す。利用者への質問は ask_user（聞かずに進める設定のときは、最善の判断で進めて判断を report に残す）。
外部からの取得・本番データの変更・秘密に関わることは、設定にかかわらず ask_user(required=true) で聞く。"""


def build_prompt(goal: str, *, resume: bool, follow_up: bool) -> str:
    head = PROMPT + f"\n\n【依頼】\n{goal}"
    if resume:
        head += "\n\n【再開です】前の実行が途中で止まりました。team_status を最初に読み、ここまでの計画と状態の続きから進めてください。やり直さないこと。"
    elif follow_up:
        head += "\n\n【続きの依頼です】このチームには前の依頼の計画と結果があります。team_status で状態を読んでから、この依頼を足してください。"
    return head


# ---------------------------------------------------------------- 状態

def _chain(team: dict) -> list[dict]:
    """チームリーダーの chain: チームのチームリーダー（無ければ既定）→ hard 役割の primary・secondary（同じものは省く）。"""
    first = team_roles.leader_endpoint(team)
    out = [first]
    hard = team_roles.tier_role(team, "hard")
    keys = {team_roles.endpoint_key(first)}
    for ep in team_roles.chain(hard) if hard else []:
        ep = {**ep, "kind": "headless"}
        if team_roles.endpoint_key(ep) not in keys:
            keys.add(team_roles.endpoint_key(ep))
            out.append(ep)
    return out


def _begin(team_id: str, goal: str, *, resume: bool, ep: dict, carry: dict | None = None) -> int:
    """チームリーダーの 1 回の起動を記録して、その番号 n を返す。"""
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        run = team.get("leader_run") if isinstance(team.get("leader_run"), dict) else {}
        n = int(run.get("n") or 0) + 1
        team["leader_run"] = {**run, "status": "running", "n": n, "goal": goal[:GOAL_MAX], "endpoint": team_roles.endpoint_label(ep),
                              "started_at": run.get("started_at") if resume and run.get("started_at") else teams._now(),  # noqa: SLF001
                              "attempt_started_at": teams._now(), "resumes": int((carry or run).get("resumes") or 0),  # noqa: SLF001
                              "crashes": int((carry or run).get("crashes") or 0), "finish": None if not resume else run.get("finish")}
        teams._save(all_teams)  # noqa: SLF001
    return n


def _record(team_id: str, status: str, ep: dict, seconds: float, usage: dict | None, reason: str = "") -> None:
    def save(team: dict) -> None:
        runs = team.setdefault("leader_runs", [])
        runs.append({"status": status, "started_at": (team.get("leader_run") or {}).get("attempt_started_at"), "ended_at": teams._now(),  # noqa: SLF001
                     "endpoint": team_roles.endpoint_label(ep), "seconds": seconds, "usage": usage, "reason": reason[:200]})
        del runs[:-RUNS_KEPT]

    try:
        leader_mcp._mutate(team_id, save)  # noqa: SLF001
    except leader_mcp.ToolError:
        pass


def _set_status(team_id: str, status: str, reason: str = "", notify: str = "") -> None:
    notify_text = ""

    def save(team: dict) -> None:
        nonlocal notify_text
        run = team.setdefault("leader_run", {})
        run.update({"status": status, "ended_at": teams._now(), "reason": reason[:200]})  # noqa: SLF001
        run.pop("stop_requested", None)  # 止めた印は状態が確定したので外す
        if notify and team.get("notify", True) is not False:
            notify_text = f"Nexarch: チーム『{team['name'][:40]}』{notify}"
        if status in ("failed", "stopped"):
            leader_mcp.add_timeline(team, "stop", {"failed": "チームリーダーが止まりました: ", "stopped": "止めました: "}[status] + reason[:100])

    try:
        leader_mcp._mutate(team_id, save)  # noqa: SLF001
    except leader_mcp.ToolError:
        return
    if notify_text:
        team_runner._notify([{"title": "Nexarch", "message": notify_text}])  # noqa: SLF001


def _attempt(team_id: str, ep: dict, text: str, cwd, token: str, allow_no_sandbox: bool) -> dict:
    key = (team_id, 0)
    with team_runner._active_lock:  # noqa: SLF001
        team_runner._active[key] = {"proc": None, "stop": False, "live": {}, "started": time.monotonic()}  # noqa: SLF001
    try:
        result = team_runner._execute(key, ep, text, cwd, allow_no_sandbox, mcp={"url": mcp_url(), "token": token},  # noqa: SLF001
                                      timeout=team_runner.LEADER_TIMEOUT)
    except team_runner.LaunchError as exc:
        result = {"launch_error": str(exc)[:200]}
    with team_runner._active_lock:  # noqa: SLF001
        entry = team_runner._active.pop(key, None)  # noqa: SLF001
    result["stopped"] = bool(entry and entry.get("stop"))
    return result


def _finalize(team_id: str) -> None:
    """finish を受けた後の仕上げ: 結果をまとめる（確認の結果・変更ファイル・残り・使用量）・git の反映・通知。"""
    notify = ""

    def save(team: dict) -> None:
        nonlocal notify
        finish = (team.get("leader_run") or {}).get("finish") or {}
        info = team.get("git") or {}
        changes = None
        if info.get("enabled"):
            try:
                changes = team_git.team_numstat(info)
            except team_git.GitError:
                changes = None
        plan_items = (team.get("plan") or {}).get("items", [])
        remaining = [i["title"] for i in plan_items if i.get("status") not in ("integrated", "done")]
        landed = False
        if info.get("enabled") and not info.get("origin_was_git") and finish.get("outcome") == "done":
            landed = team_git.fast_forward_base(str(team.get("workdir") or ""), info)
        team["result"] = {
            "summary": str(finish.get("summary") or ""), "outcome": finish.get("outcome") or "done", "finished_at": teams._now(),  # noqa: SLF001
            "checks": team.get("last_team_checks"), "changes": changes, "remaining": remaining,
            "usage": leader_mcp.usage_total(team), "can_land": bool(info.get("enabled") and info.get("origin_was_git")
                                                                   and finish.get("outcome") == "done" and changes and changes["count"]),
            "landed": landed, "team_branch": info.get("team_branch") or "", "base_branch": info.get("base_branch") or "",
        }
        if team.get("notify", True) is not False:
            ok = finish.get("outcome") == "done"
            notify = f"Nexarch: チーム『{team['name'][:40]}』" + ("が完了しました" if ok else "が止まりました（結果を見てください）")

    leader_mcp._mutate(team_id, save)  # noqa: SLF001
    if notify:
        team_runner._notify([{"title": "Nexarch", "message": notify}])  # noqa: SLF001


def _worker(team_id: str, goal: str, follow_up: bool) -> None:
    try:
        _loop(team_id, goal, follow_up)
    except Exception:  # noqa: BLE001 - スレッドの失敗で落とさない
        logger.exception("Nexarch のチームリーダーの実行に失敗しました")
        _set_status(team_id, "failed", "内部エラー", "のチームリーダーが内部エラーで止まりました")
    try:
        from app.commander import team_dispatch

        team_dispatch.kick(team_id)
    except Exception:  # noqa: BLE001
        logger.exception("配り手を動かせませんでした")


def _loop(team_id: str, goal: str, follow_up: bool) -> None:
    resume = False
    crashes = 0
    resumes = 0
    while True:
        team = leader_mcp._read(team_id)  # noqa: SLF001
        allow_no_sandbox = team.get("codex_without_sandbox", True) is not False
        now = time.time()
        chain = _chain(team)
        if team.get("local_only"):  # 閉じたチーム: ローカル LLM の endpoint だけ（brief C・2026-11-28）
            chain = [e for e in chain if not teams.endpoint_local_problem(e)]
            if not chain:
                _set_status(team_id, "failed", "閉じたチームですが、ローカル LLM のチームリーダーがいません",
                            "のチームリーダーを動かせるローカルの担い手がありません（閉じたチーム）")
                return
        ep = next((e for e in chain if team_roles.cooling_until(team, e, now) is None), None)
        if ep is None:
            _set_status(team_id, "failed", "チームリーダーの担い手が全部上限です", "のチームリーダーを動かせる担い手がありません（上限）")
            return
        try:
            cwd = workdir.resolve_dir(teams.ensure_workdir(team_id))
        except (OSError, ValueError) as exc:
            _set_status(team_id, "failed", f"作業場所を使えません: {exc}", "の作業場所を使えません")
            return
        n = _begin(team_id, goal, resume=resume, ep=ep, carry={"resumes": resumes, "crashes": crashes})
        text = build_prompt(goal, resume=resume, follow_up=follow_up and n == 1)
        started = time.monotonic()
        result = _attempt(team_id, ep, text, cwd, leader_mcp.issue_token(team_id, n), allow_no_sandbox)
        seconds = round(time.monotonic() - started, 1)
        state = result.get("state")
        usage = dict(state.usage) if state else None
        if "launch_error" in result:
            _record(team_id, "failed", ep, seconds, usage, result["launch_error"])
            _audit(team_id, ep, seconds, "launch_error")
            _set_status(team_id, "failed", result["launch_error"], "のチームリーダーを起動できませんでした")
            return
        finish = (leader_mcp._read(team_id).get("leader_run") or {}).get("finish")  # noqa: SLF001
        if result.get("stopped"):
            _record(team_id, "stopped", ep, seconds, usage)
            _audit(team_id, ep, seconds, "stopped")
            _set_status(team_id, "stopped", "利用者が止めました")
            return
        failing = result.get("exit") != 0 or bool(state and state.is_error) or result.get("timed_out") or result.get("too_large")
        if finish:
            _record(team_id, "done", ep, seconds, usage)
            _audit(team_id, ep, seconds, "finished")
            _finalize(team_id)
            _set_status(team_id, "done" if finish.get("outcome") == "done" else "blocked")
            return
        if failing:
            limited, reset_at = team_runner.detect_limit(((state.errors if state else []) + [result.get("stderr", ""), result.get("final", "")]))
            if limited:
                def mark(t: dict, ep=ep, reset_at=reset_at) -> None:
                    team_roles.mark_limited(t, ep, reset_at, time.time())
                    leader_mcp.add_timeline(t, "handoff", f"チームリーダー（{team_roles.endpoint_label(ep)}）が上限で止まりました。次の担い手で再開します")

                leader_mcp._mutate(team_id, mark)  # noqa: SLF001
                _record(team_id, "limited", ep, seconds, usage, "上限")
                _audit(team_id, ep, seconds, "limited")
                resume = True
                continue  # 次のループで chain の次の endpoint（セカンダリ）が選ばれる。無ければ上で失敗
            _record(team_id, "failed", ep, seconds, usage, team_runner.failure_reason(result))
            _audit(team_id, ep, seconds, "crashed")
            crashes += 1
            if crashes > MAX_CRASH_RETRIES:
                _set_status(team_id, "failed", f"実行が落ちました（{team_runner.failure_reason(result)}）", "のチームリーダーが落ちました")
                return
            resume = True
            continue
        _record(team_id, "no_finish", ep, seconds, usage, "finish されずに終わった")
        _audit(team_id, ep, seconds, "no_finish")
        resumes += 1
        if resumes > MAX_RESUMES:
            _set_status(team_id, "failed", "finish されないまま 3 回再開しました", "のチームリーダーが finish しないまま止まりました")
            return
        leader_mcp.timeline(team_id, "resume", f"チームリーダーがまだ終わっていないので再開します（{resumes}/{MAX_RESUMES}）")
        resume = True


def _audit(team_id: str, ep: dict, seconds: float, result: str) -> None:
    try:
        from app.audit import service as audit
        from app.database import SessionLocal

        with SessionLocal() as db:
            audit.record(db, "commander.team.leader", username="nexarch", resource_type="commander_team",
                         resource_id=team_id, metadata={"cli": ep.get("cli"), "account": ep.get("account"), "model": ep.get("model"),
                                                       "effort": ep.get("effort"), "seconds": seconds, "result": result})
    except Exception:  # noqa: BLE001 - 監査の失敗で結果を失わない
        logger.exception("チームリーダーの監査ログを書けませんでした")


# ---------------------------------------------------------------- 入口

def goal_with_inputs(goal: str, rows: list[dict]) -> str:
    """チームリーダーへ渡す依頼に、資料のパスの一覧を足す（中身は展開しない・brief E）。"""
    if not rows:
        return goal
    lines = ["【資料】次の資料は作業場所の .nexarch/inputs に置いてあります（読むだけ・書き換えない）:"]
    lines += [f"- {row['path']}" for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)]
    return goal + "\n\n" + "\n".join(lines)


def start(team_id: str, goal: object, *, resume: bool = False, input_paths: object = None) -> dict:
    """チームリーダーを別スレッドで始める（画面は一覧を見て進み具合を知る）。走っている間は断る。

    既にこのチームに計画・結果があれば「続きの依頼」（頼み足し）として、team_status から読ませる。
    input_paths があれば資料を作業場所の .nexarch/inputs へ写し、リーダーにはパスの一覧だけを渡す。"""
    text = teams.clean_text(goal, "依頼", GOAL_MAX)
    if not text:
        raise teams.TeamError("依頼を入力してください")
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        run = team.get("leader_run")
        if isinstance(run, dict) and run.get("status") == "running":
            raise teams.TeamError("チームリーダーは実行中です", 409)
        existing_inputs = list(team.get("inputs") or [])
    # コピーは板のロックの外で（大きな資料で他のチームの操作を止めない）
    rows = existing_inputs
    if input_paths is not None or existing_inputs:
        workdir_path = teams.ensure_workdir(team_id)
        rows = team_inputs.publish(workdir_path, text, input_paths, existing=existing_inputs)
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        team = teams._find_team(all_teams, team_id)  # noqa: SLF001
        run = team.get("leader_run")
        if isinstance(run, dict) and run.get("status") == "running":
            raise teams.TeamError("チームリーダーは実行中です", 409)
        ep = team_roles.leader_endpoint(team)
        follow_up = bool(team.get("plan") or team.get("result"))
        requests = team.setdefault("requests", [])
        requests.append({"at": teams._now(), "text": text[:600]})  # noqa: SLF001
        del requests[:-REQUESTS_KEPT]
        goal_text = goal_with_inputs(text, rows)
        if rows:
            team["inputs"] = rows
        team["leader_run"] = {"status": "running", "n": int((run or {}).get("n") or 0), "goal": goal_text,  # noqa: SLF001
                              "started_at": teams._now(), "endpoint": team_roles.endpoint_label(ep),
                              "resumes": 0, "crashes": 0, "finish": None}
        team.pop("result", None)
        leader_mcp.add_timeline(team, "request", "依頼: " + text[:200])
        teams._save(all_teams)  # noqa: SLF001
        view = dict(team)
    team_runner._spawn(_worker, team_id, goal_text, follow_up)  # noqa: SLF001
    return view


def create_and_start(goal: object, name: object = "", work_path: object = "", *, owner_user_id: int = 0,
                     input_paths: object = None) -> dict:
    """「頼む」: チームを作り（役割・チームリーダーは既定を写す。名前は依頼から短く）、チームリーダーを走らせる。"""
    goal_text = teams.clean_text(goal, "依頼", GOAL_MAX)
    if not goal_text:
        raise teams.TeamError("依頼を入力してください")
    title = name if isinstance(name, str) and name.strip() else " ".join(goal_text.split())[:teams.NAME_MAX]
    path = ""
    if work_path not in (None, ""):
        if not isinstance(work_path, str):
            raise teams.TeamError("作業場所は文字列で指定してください")
        try:
            path = str(workdir.resolve_dir(work_path))
        except (OSError, ValueError) as exc:
            raise teams.TeamError(f"作業場所を使えません: {exc}") from exc
    team = teams.create_team(title, workdir_path=path, owner_user_id=owner_user_id)
    return start(team["id"], goal_text, input_paths=input_paths)


def create_for_assistant(goal: object, work_path: str, *, owner_user_id: int = 0) -> dict:
    """AI アシスタントの会話の OpenCode の作業場所でチームを作って頼む（保存先は会話の作業場所そのまま）。"""
    goal_text = teams.clean_text(goal, "依頼", GOAL_MAX)
    if not goal_text:
        raise teams.TeamError("依頼を入力してください")
    team = teams.create_team(" ".join(goal_text.split())[:teams.NAME_MAX], workdir_path=work_path, created_by="assistant",
                             owner_user_id=owner_user_id)
    return start(team["id"], goal_text)


def stop(team_id: str) -> bool:
    """チームリーダー（と走っている仕事）を止める。

    印（stop_requested）を先に板へ残す: 止めた直後に ControlDeck が再起動しても、自動の再開で
    利用者が止めたはずのものが動き出さないように（brief D・2026-11-28）。"""
    teams._find_team(teams._load(), team_id)  # noqa: SLF001

    def mark(team: dict) -> None:
        if isinstance(team.get("leader_run"), dict):
            team["leader_run"]["stop_requested"] = True

    try:
        leader_mcp._mutate(team_id, mark)  # noqa: SLF001
    except leader_mcp.ToolError:
        pass
    return team_runner.stop_run(team_id, 0)


def land(team_id: str) -> dict:
    """「取り込む」: チームのブランチを元のブランチへ（元から git の作業場所だけ）。"""
    with teams._lock:  # noqa: SLF001
        team = teams._find_team(teams._load(), team_id)  # noqa: SLF001
        result = team.get("result")
        info = team.get("git") or {}
        path = str(team.get("workdir") or "")
    if not isinstance(result, dict) or not result.get("can_land") or result.get("landed"):
        raise teams.TeamError("取り込めるチームのブランチがありません（元から git の作業場所で、完了した結果だけ）", 409)
    with leader_mcp._git_lock:  # noqa: SLF001
        done = team_git.land(path, info)
    if not done["ok"]:
        raise teams.TeamError(done["message"], 409)

    def save(t: dict) -> None:
        t["result"]["landed"] = True
        t["result"]["can_land"] = False
        leader_mcp.add_timeline(t, "land", "チームのブランチを元のブランチへ取り込みました")

    leader_mcp._mutate(team_id, save)  # noqa: SLF001
    return leader_mcp._read(team_id)  # noqa: SLF001


def recover_interrupted() -> dict:
    """ControlDeck の起動時: 再起動で中断されたチームリーダーを記録し、自動で続きから再開する（brief D・2026-11-28）。

    - running のままプロセスが無いものを interrupted にする（画面の「再開」でも続けられる）。
    - チームの設定 auto_resume（既定入）なら resume と同じ道で続きから。30 分に 2 回まで（teams.json へ永続）。
      超えたら failed にして通知（連続失敗で止める）。利用者が止めたもの（stop_requested）は再開しない。
    - 返り値: {"leaders", "resumed", "capped", "stopped"}（集計。見張りの loop のログ・試験で使う）。
    """
    out = {"leaders": 0, "resumed": 0, "capped": 0, "stopped": 0}
    notify_capped: list[dict] = []
    with teams._lock:  # noqa: SLF001
        all_teams = teams._load()  # noqa: SLF001
        for team in all_teams:
            run = team.get("leader_run")
            if not (isinstance(run, dict) and run.get("status") == "running") or team_runner.is_active(team["id"], 0):
                continue
            out["leaders"] += 1
            if run.pop("stop_requested", None):  # 利用者が止めた → 再開しない
                run.update({"status": "stopped", "ended_at": teams._now(), "reason": "利用者が止めました"})  # noqa: SLF001
                leader_mcp.add_timeline(team, "stop", "利用者が止めたので、再起動後も自動では再開しません")
                out["stopped"] += 1
                continue
            run.update({"status": "interrupted", "ended_at": teams._now(),  # noqa: SLF001
                        "reason": "ControlDeck の再起動で中断されました"})
            leader_mcp.add_timeline(team, "stop", "ControlDeck の再起動でチームリーダーが中断されました")
            if team.get("auto_resume", True) is False:
                continue
            log = [row for row in team.get("auto_resume_log", []) if isinstance(row, dict)]
            now = time.time()
            recent = sum(1 for row in log if now - float(row.get("at") or 0) < AUTO_RESUME_WINDOW)
            if recent >= AUTO_RESUME_MAX:
                run.update({"status": "failed", "reason": f"自動で再開できる回数の上限（30 分で {AUTO_RESUME_MAX} 回）に達しました"})  # noqa: SLF001
                leader_mcp.add_timeline(team, "stop", f"再起動からの自動再開が続いたので止めました（30 分で {AUTO_RESUME_MAX} 回）")
                out["capped"] += 1
                if team.get("notify", True) is not False:
                    notify_capped.append({"title": "Nexarch",
                                          "message": f"Nexarch: チーム『{team['name'][:40]}』のチームリーダーが再起動後に続けて止まるので、自動の再開をやめました（結果を見てください）"})
                continue
            log.append({"at": now})
            del log[:-20]
            team["auto_resume_log"] = log
            run["startup_pending"] = True  # 排他の外で resume する（下）
            leader_mcp.add_timeline(team, "resume", f"再起動があったので自動で再開します（30 分で {AUTO_RESUME_MAX} 回のうち {recent + 1} 回目）")
            out["resumed"] += 1
        if out["leaders"]:
            teams._save(all_teams)  # noqa: SLF001
    # 再開は排他の外で（resume が板を読み書きし、実行のスレッドも始まる）
    for team in teams._load():  # noqa: SLF001
        run = team.get("leader_run") or {}
        if not run.pop("startup_pending", None):
            continue
        try:
            resume(team["id"])
        except teams.TeamError as exc:
            logger.info("中断したチームリーダーを自動で再開できませんでした（画面の「再開」で続けられます）: %s", exc)
    if notify_capped:
        team_runner._notify(notify_capped)  # noqa: SLF001
    return out


def resume(team_id: str) -> dict:
    """止まったチームリーダーを、今の状態（team_status）から再開する。"""
    team = leader_mcp._read(team_id)  # noqa: SLF001
    run = team.get("leader_run") or {}
    if run.get("status") == "running":
        raise teams.TeamError("チームリーダーは実行中です", 409)
    goal = str(run.get("goal") or "")
    if not goal:
        raise teams.TeamError("再開する依頼がありません", 409)
    return start(team_id, goal + "\n（再開: team_status の状態の続きから）")
