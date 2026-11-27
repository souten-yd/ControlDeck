from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.addon_runtime import grants as runtime_grants
from app.addons import execution, registry, tokens
from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.project_lab import service as project_lab
from app.security.deps import user_permissions
from app.security.localhost import is_loopback

router = APIRouter(prefix="/addons/agent-mcp", tags=["addons"])
MCP_TOKEN_TTL_SECONDS = 8 * 60 * 60
# OpenCode 側が 1 コールを待つ長さ（ミリ秒）。bridge の待ちに合わせる。
#
# 135 秒だった。当時の bridge が 130 秒で切っていたので、その少し外側という
# 意味だった。bridge を「host が返すまで待つ」へ変えた後もここが残り、client
# 側だけが先に切っていた。実測: 45 秒の曲の生成に 98〜127 秒かかり、
# エージェントは "MCP request timeout" として失敗を受け取りながら、
# SonicForge 側では曲が出来上がっていた。
#
# 終わりを決めるのは host である（wait_agent_tool_job は進捗が止まったときだけ
# 打ち切る）。client はその答えを待つ側なので、正常な処理時間で切らない。
MCP_CLIENT_TIMEOUT_MS = 3_600_000
_SUBJECT = re.compile(r"^opencode:[A-Za-z0-9_-]{1,64}$")
PROJECT_OUTPUT_GRANT_TOOL = "control_deck.project_output_grant"
_OUTPUT_CAPABILITIES = {"projects.pick", "files.export"}
# 逆向きの入口。project の中の file を add-on に「読ませる」ための grant を作る。
#
# 生成された物を参照にするのは出来ていたが、利用者が project へ置いた絵——下描き、
# 既存のキャラ表、UI の当たり——を渡す道が無かった。add-on の取り込み口は Host の
# 画面からしか届かず、agent からは手が出せない。
PROJECT_INPUT_GRANT_TOOL = "control_deck.project_input_grant"
_INPUT_CAPABILITIES = {"projects.pick", "files.pick"}
# 一覧から外した契約を取りに来るための道具。
#
# 一覧に全部の契約を載せると、使わない道具のぶんまで毎ターン払うことになる。
# 名前と説明だけを載せ、schema は使うと決めてから取らせる。
CONTRACT_TOOL = "control_deck.tool_contract"
# 作業の結果（UI 検証・精密調査・レビュー）を ControlDeck へ渡す道具。画面はこれを
# 幅ごとの写真・指摘・根拠の一覧として出す。自由形式の文章だと画面が読み取れない。
REPORT_TOOL = "control_deck.report"
# ControlDeck の Deep Research を OpenCode から呼ぶ道具。既定はローカル（コード・RAG）だけ。
RESEARCH_TOOL = "control_deck.research"


def _research_tool() -> dict[str, Any] | None:
    from app.integrations.opencode import provider as opencode
    from app.integrations.opencode.research import RESEARCH_SCHEMA, allowed_sources

    settings = opencode.get_settings()
    if not settings.get("research_tool"):
        return None
    reach = "Web・GitHub・論文も使える" if "web" in allowed_sources(settings) else "このプロジェクトのコードと RAG だけを調べる（外部へ問い合わせない）"
    return {
        "name": RESEARCH_TOOL,
        "description": (
            "根拠付きの精密調査（ControlDeck の Deep Research）。計画→検索→再評価→反証→引用検証を繰り返し、"
            f"出典付きのレポートを返す。{reach}。数分かかる。結果は ControlDeck の画面にも残る。"
        ),
        "inputSchema": execution.model_facing_schema(RESEARCH_SCHEMA),
    }


# AI アシスタントの会話の OpenCode の作業場所で、Nexarch のチームリーダーに一文で頼む（T3c）
NEXARCH_REQUEST_TOOL = "control_deck.nexarch_team_request"
NEXARCH_STATUS_TOOL = "control_deck.nexarch_team_status"
NEXARCH_TOOLS = (NEXARCH_REQUEST_TOOL, NEXARCH_STATUS_TOOL)


def _nexarch_tools(project_id: object, permissions: set[str]) -> list[dict[str, Any]]:
    if not isinstance(project_id, str) or "terminal.use" not in permissions:
        return []
    return [
        {"name": NEXARCH_REQUEST_TOOL,
         "description": ("Nexarch の AI チームのチームリーダーへ一文で頼む。チームリーダーが調べ・計画し・段ごとに担い手へ振り・評価・統合・試験する。"
                         "この会話の作業場所で動く。返り値の team_id で nexarch_team_status を引く（終わるまで数分〜数十分）。"),
         "inputSchema": {"type": "object", "additionalProperties": False, "required": ["text"],
                         "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 4000, "description": "頼むこと（一文〜数行）"}}}},
        {"name": NEXARCH_STATUS_TOOL,
         "description": "頼んだチームの状態の要約（進み具合・結果・質問・使用量）。",
         "inputSchema": {"type": "object", "additionalProperties": False, "required": ["team_id"],
                         "properties": {"team_id": {"type": "string", "pattern": "^t[0-9a-f]{8}$"}}}},
    ]


def _run_nexarch(name: str, arguments: dict[str, Any], claims: dict[str, Any], permissions: set[str]) -> dict:
    """同期の処理（チームを作る・状態を読む）。呼び出し側が to_thread へ出す。"""
    from app.commander import leader_mcp, team_leader, teams

    project_id = claims.get("project_id")
    if not _nexarch_tools(project_id, permissions):
        raise HTTPException(status_code=404, detail="nexarch toolが見つかりません")
    if name == NEXARCH_REQUEST_TOOL:
        if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
            raise HTTPException(status_code=422, detail="text（頼むこと）だけを渡してください")
        try:
            folder = project_lab.resolve_project(str(project_id))
            team = team_leader.create_for_assistant(arguments["text"], str(folder))
        except (project_lab.ProjectLabError, teams.TeamError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"team_id": team["id"], "status": "running", "note": "頼みました。nexarch_team_status で進み具合を見られます"}
    team_id = arguments.get("team_id")
    if set(arguments) != {"team_id"} or not isinstance(team_id, str) or not re.fullmatch(r"t[0-9a-f]{8}", team_id):
        raise HTTPException(status_code=422, detail="team_id だけを渡してください")
    try:
        team = leader_mcp._read(team_id)  # noqa: SLF001
    except leader_mcp.ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    run = team.get("leader_run") or {}
    result = team.get("result") or {}
    return {"team_id": team_id, "name": team["name"], "state": run.get("status") or "", "goal": str(run.get("goal") or "")[:300],
            "progress": [r["text"] for r in team.get("timeline", [])[-8:]],
            "pending_questions": [q["question"] for q in team.get("questions", []) if q.get("answer") is None],
            "result": {k: result.get(k) for k in ("summary", "outcome", "remaining", "changes", "checks", "usage")} if result else None,
            "usage": leader_mcp.usage_total(team)}


KNOWLEDGE_TOOL = "control_deck.knowledge_search"
KNOWLEDGE_MAX_CHARS = 30_000


def _knowledge_tool() -> dict[str, Any]:
    return {
        "name": KNOWLEDGE_TOOL,
        "description": (
            "ControlDeck のナレッジ（登録した資料・特許の公報・規格）を横断して探し、関係する文書を [D番号] 付きで返す。"
            "入るときは全文、入らないときは骨格（要約・請求項・適用範囲・引用規格）と該当箇所。公報番号・規格番号でも引ける。"
            "公開済み・AI に使ってよい文書だけを返す（機密の資料は返らない）。"
        ),
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500,
                                     "description": "探す内容。番号はそのまま。例: JIS C 61000-4-2 試験レベル"}},
        },
    }


async def _run_knowledge(arguments: dict[str, Any]) -> dict:
    """MCP の相手のモデルは分からないので cloud とみなす（統合の決まり 3: 機密・AI 利用不可を返さない）。"""
    from app.knowledge import context

    query = arguments.get("query")
    if set(arguments) != {"query"} or not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise HTTPException(status_code=422, detail="query（1〜500 文字）だけを渡してください")
    built, _ = await context.search_and_build(query.strip(), consumer="cloud", budget_chars=KNOWLEDGE_MAX_CHARS)
    return {"mode": built.mode, "text": built.text or "関係する公開の文書は見つかりませんでした。",
            "sources": [{"label": s["label"], "title": s["title"]} for s in built.sources]}


NEWS_KNOWLEDGE_TOOL = "control_deck.news_knowledge"


def _news_knowledge_tool() -> dict[str, Any]:
    return {
        "name": NEWS_KNOWLEDGE_TOOL,
        "description": (
            "ニュースハブ（QNAP）の Things（技術・製品・組織・人）のカードを引く: 概要・別名・関係（上位・下位・仲間・使う・置き換え）・主張と根拠の記事・変化と矛盾。"
            "ニュースや技術動向の言葉の言い換え・関連語を知りたいときに使う。公開の区分だけを返す。ハブとつながっていなければ空。"
        ),
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500, "description": "探す内容。例: llama.cpp の代わりになるもの"}},
        },
    }


async def _run_news_knowledge(arguments: dict[str, Any]) -> dict:
    """ハブへは読む API だけ（hub_link.call）。区分は公開のみ。件数・大きさの上限は hub_expand.news_cards。"""
    import asyncio as _asyncio

    from app.knowledge import hub_expand

    query = arguments.get("query")
    if set(arguments) != {"query"} or not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise HTTPException(status_code=422, detail="query（1〜500 文字）だけを渡してください")
    out = await _asyncio.to_thread(hub_expand.news_cards, query.strip())
    return {"text": out["text"] or "関係する Things は見つかりませんでした（ハブにつながっていないか、公開の記録に無い言葉です）。", "things": out["things"], "sources": out["sources"],
            "truncated": out.get("truncated", False)}


# ---- Project Lab の実行の道具（P2a）。中身は project_lab の既存の関数を呼ぶだけ ----
LAB_PROJECTS_TOOL = "control_deck.lab_projects"
LAB_RUN_START_TOOL = "control_deck.lab_run_start"
LAB_RUN_STATUS_TOOL = "control_deck.lab_run_status"
LAB_RUN_LOGS_TOOL = "control_deck.lab_run_logs"
LAB_RUN_STOP_TOOL = "control_deck.lab_run_stop"
LAB_TOOLS = (LAB_PROJECTS_TOOL, LAB_RUN_START_TOOL, LAB_RUN_STATUS_TOOL, LAB_RUN_LOGS_TOOL, LAB_RUN_STOP_TOOL)
LAB_LOG_TAIL_DEFAULT_KB = 32
LAB_LOG_TAIL_MAX_KB = 256


def _lab_tools(permissions: set[str]) -> list[dict[str, Any]]:
    """権限ごとに出す。見る道具は project_lab.view、始める・止めるは project_lab.run（router.py と同じ）。"""
    tools: list[dict[str, Any]] = []
    run_id = {"run_id": {"type": "integer", "minimum": 1, "description": "実行の id。例: 12"}}
    if "project_lab.view" in permissions:
        tools.append({
            "name": LAB_PROJECTS_TOOL,
            "description": (
                "Project Lab のプロジェクトの一覧と、実行できる設定（profiles の id・label・type）を返す。"
                "実行を始める前に呼んで project_id と profile_id を確かめる。引数は無い。"
            ),
            "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        })
    if "project_lab.run" in permissions:
        tools.append({
            "name": LAB_RUN_START_TOOL,
            "description": (
                "プロジェクトの実行（cli・test・web）を始める。同時 3 本まで、同じプロジェクトは 1 本まで。"
                "返り値の id で状態とログを読む。web なら preview の URL も返る（準備ができるまで数秒かかる）。"
                "例: {\"project_id\": \"my-app\", \"profile_id\": \"dev\", \"timeout_seconds\": 600}"
            ),
            "inputSchema": {
                "type": "object", "additionalProperties": False, "required": ["project_id", "profile_id"],
                "properties": {
                    "project_id": {"type": "string", "minLength": 1, "maxLength": 128,
                                   "description": "プロジェクトの id（lab_projects で調べる）。例: my-app"},
                    "profile_id": {"type": "string", "minLength": 1, "maxLength": 64,
                                   "description": "実行の設定の id。例: dev"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600,
                                        "description": "最長の実行時間（秒）。既定 600"},
                },
            },
        })
    if "project_lab.view" in permissions:
        tools.append({
            "name": LAB_RUN_STATUS_TOOL,
            "description": (
                "実行の状態（status・result・終了コード）、増えた・変わった成果物、web なら preview の URL を返す。"
                "例: {\"run_id\": 12}"
            ),
            "inputSchema": {"type": "object", "additionalProperties": False, "required": ["run_id"],
                            "properties": run_id},
        })
        tools.append({
            "name": LAB_RUN_LOGS_TOOL,
            "description": (
                "実行のログの末尾を返す（秘密は伏せ済み）。"
                f"tail_kb は 1〜{LAB_LOG_TAIL_MAX_KB}（既定 {LAB_LOG_TAIL_DEFAULT_KB}）。"
                "例: {\"run_id\": 12, \"tail_kb\": 32}"
            ),
            "inputSchema": {"type": "object", "additionalProperties": False, "required": ["run_id"],
                            "properties": {**run_id,
                                           "tail_kb": {"type": "integer", "minimum": 1,
                                                       "maximum": LAB_LOG_TAIL_MAX_KB,
                                                       "description": "末尾から読む量（KB）。例: 32"}}},
        })
    if "project_lab.run" in permissions:
        tools.append({
            "name": LAB_RUN_STOP_TOOL,
            "description": "動いている実行を止める。終わった実行は止められない。例: {\"run_id\": 12}",
            "inputSchema": {"type": "object", "additionalProperties": False, "required": ["run_id"],
                            "properties": run_id},
        })
    return tools


def _lab_int(arguments: dict[str, Any], key: str, *, low: int, high: int, default: int | None = None) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HTTPException(status_code=422, detail=f"{key} は {low}〜{high} の整数です")
    return value


def _lab_str(arguments: dict[str, Any], key: str, max_length: int) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"{key} は 1〜{max_length} 文字の文字列です")
    return value


def _lab_run_for(db: Session, run_id: int, user: User, permissions: set[str]):
    """他人の run は見えない（管理者 = settings.manage は全員分）。無い run と区別しない。"""
    from app.models import ProjectRun

    row = db.get(ProjectRun, run_id)
    if row is None or ("settings.manage" not in permissions and row.created_by != user.id):
        raise HTTPException(status_code=404, detail="Project runが見つかりません")
    return row


def _lab_out(db: Session, row) -> dict[str, Any]:
    """run_out に、箱や外から開ける絶対の preview URL（ControlDeck の 127.0.0.1）を足す。"""
    from app.config import get_config
    from app.project_lab import runs

    out = runs.run_out(db, row)
    if row.profile_type == "web":
        out["previewPath"] = runs.preview_base(row)
        out["previewUrlAbsolute"] = f"http://127.0.0.1:{int(get_config().server.port)}{runs.preview_base(row)}"
    return out


def _run_lab_tool(name: str, arguments: dict[str, Any], user: User, permissions: set[str],
                  db: Session, request: Request) -> Any:
    """同期の処理（subprocess・DB）。呼び出し側が asyncio.to_thread へ出す。"""
    from app.project_lab import runs

    needed = "project_lab.run" if name in (LAB_RUN_START_TOOL, LAB_RUN_STOP_TOOL) else "project_lab.view"
    if needed not in permissions:
        raise HTTPException(status_code=403, detail=f"権限がありません: {needed}")
    allowed_keys = {
        LAB_PROJECTS_TOOL: set(), LAB_RUN_START_TOOL: {"project_id", "profile_id", "timeout_seconds"},
        LAB_RUN_STATUS_TOOL: {"run_id"}, LAB_RUN_LOGS_TOOL: {"run_id", "tail_kb"}, LAB_RUN_STOP_TOOL: {"run_id"},
    }[name]
    if not set(arguments) <= allowed_keys:
        raise HTTPException(status_code=422, detail=f"引数は {sorted(allowed_keys)} だけです")
    if name == LAB_PROJECTS_TOOL:
        rows = []
        for item in project_lab.list_projects():
            profiles: list[dict[str, str]] = []
            try:
                manifest, _ = project_lab._read_manifest(project_lab.resolve_project(item["id"]))
            except project_lab.ProjectLabError:
                manifest = None
            if manifest is not None:
                profiles = [{"id": p.id, "label": p.label, "type": p.type} for p in manifest.profiles]
            rows.append({"id": item["id"], "name": item["name"], "profiles": profiles})
        return {"projects": rows}
    if name == LAB_RUN_START_TOOL:
        project_id = _lab_str(arguments, "project_id", 128)
        profile_id = _lab_str(arguments, "profile_id", 64)
        timeout = _lab_int(arguments, "timeout_seconds", low=1, high=3600, default=600)
        try:
            row = runs.start_run(db, project_id=project_id, profile_id=profile_id,
                                 timeout_seconds=timeout, created_by=user.id)
        except project_lab.ProjectLabError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except runs.ProjectRunError as exc:
            audit.record(db, "project_lab.run.start", user=user, resource_type="project",
                         resource_id=project_id, request=request, result="failure",
                         metadata={"profile_id": profile_id, "via": "agent_mcp"})
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit.record(db, "project_lab.run.start", user=user, resource_type="project_run",
                     resource_id=str(row.id), request=request,
                     metadata={"project_id": project_id, "profile_id": profile_id, "via": "agent_mcp"})
        return _lab_out(db, row)
    row = _lab_run_for(db, _lab_int(arguments, "run_id", low=1, high=2**31 - 1), user, permissions)
    if name == LAB_RUN_STATUS_TOOL:
        return _lab_out(db, row)
    if name == LAB_RUN_LOGS_TOOL:
        tail_kb = _lab_int(arguments, "tail_kb", low=1, high=LAB_LOG_TAIL_MAX_KB, default=LAB_LOG_TAIL_DEFAULT_KB)
        runs.refresh_run(db, row)
        text = runs.run_logs(row)  # 秘密の伏せは run_logs（service.redact_text）が済ませている
        limit = tail_kb * 1024
        return {"run_id": row.id, "status": row.status, "truncated": len(text) > limit, "logs": text[-limit:]}
    try:
        runs.cancel_run(db, row)
    except runs.ProjectRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "project_lab.run.cancel", user=user, resource_type="project_run",
                 resource_id=str(row.id), request=request, metadata={"via": "agent_mcp"})
    return _lab_out(db, row)


DIAGNOSTICS_TOOL = "control_deck.diagnostics"


def _diagnostics_tool() -> dict[str, Any]:
    return {
        "name": DIAGNOSTICS_TOOL,
        "description": (
            "ControlDeck 自身の診断レポート（Markdown、秘密は伏せ済み）を返す。自己診断の結果、本体の再起動と"
            "その理由、OOM で強制終了されたプロセス、管理下の常駐の異常終了、event loop の詰まり（塞いだ関数の"
            "stack つき）、遅い API、警告・エラーのまとめ（traceback つき）、メモリ、失敗したジョブ。"
            "ControlDeck の不具合や遅さ・落ちる原因を調べるときに最初に呼ぶ。"
        ),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"hours": {"type": "number", "minimum": 1, "maximum": 168,
                                     "description": "遡る時間（既定 24）"}},
        },
    }


def _report_tool() -> dict[str, Any]:
    from app.integrations.opencode.reports import REPORT_SCHEMA

    return {
        "name": REPORT_TOOL,
        "description": (
            "UI 検証・精密調査・コードレビューの結果を ControlDeck の画面へ渡す。作業の最後に 1 回呼ぶ。"
            "画面写真は撮ったときのファイル名で指す（ControlDeck が取り込む）。返り値の id を利用者へ伝える。"
        ),
        "inputSchema": execution.model_facing_schema(REPORT_SCHEMA),
    }


def _contract_threshold() -> int:
    from app.config import get_config

    try:
        return int(get_config().addons.agent_tool_contract_threshold)
    except Exception:  # noqa: BLE001 - 設定の都合で MCP を止めない
        return 0


def _contract_tool() -> dict[str, Any]:
    return {
        "name": CONTRACT_TOOL,
        "description": (
            "道具の引数の契約（入力 schema）を取り出す。一覧の説明に"
            "「契約はこの一覧に載せていない」と書かれている道具は、"
            "これで契約を読んでから呼ぶこと。name には一覧に出ている道具の名前を渡す。"
        ),
        "inputSchema": execution.model_facing_schema({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }),
    }


class AgentMcpCall(BaseModel):
    name: str = Field(min_length=1, max_length=192)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=64)


def _save_report(arguments: dict[str, Any], user: User, claims: dict[str, Any], db: Session, request: Request) -> dict:
    from pydantic import ValidationError

    from app.integrations.opencode import provider as opencode
    from app.integrations.opencode import reports

    try:
        body = reports.ReportBody.model_validate(arguments)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()))
        raise HTTPException(status_code=422, detail=f"報告の形が不正です: {where} {first.get('msg', '')}".strip()) from exc
    session = str(claims.get("sub") or "").removeprefix("opencode:")
    record = reports.save(body, owner_user_id=user.id, session=session,
                          images_from=opencode.ui_verify_dir(session) if session else None)
    audit.record(db, "opencode.report", user=user, resource_type="opencode_report",
                 resource_id=record["id"], request=request,
                 metadata={"kind": body.kind, "verdict": body.verdict, "findings": len(body.findings)})
    return {"id": record["id"], "missing_images": record["missing_images"],
            "message": "ControlDeck の OpenCode 画面の「結果」に表示しました"}


async def _run_research(arguments: dict[str, Any], user: User, claims: dict[str, Any], db: Session, request: Request) -> dict:
    from app.integrations.opencode import provider as opencode
    from app.integrations.opencode import reports, research
    from app.workflows.chat_router import SearchBody, _deep_search

    settings = opencode.get_settings()
    if not settings.get("research_tool"):
        raise HTTPException(status_code=404, detail="精密調査の道具は無効です")
    project_path: Path | None = None
    project_id = claims.get("project_id")
    if isinstance(project_id, str) and project_id:
        try:
            project_path = _resolve_project_directory(project_id, ".")
        except HTTPException:
            project_path = None
    if project_path is None:
        project_path = research.resolve_project(arguments.get("project_dir"), codedev=opencode.codedev_root())
    try:
        planned = research.plan(arguments, settings, project_path=project_path)
    except research.ResearchRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    body = SearchBody(
        query=planned["question"], mode="deep", depth=planned["depth"],
        base_url=str(settings.get("base_url") or ""), model=str(settings.get("model") or ""),
        source_types=planned["sources"], local_project_path=planned["project_path"],
    )
    try:
        result = await _deep_search(body)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 調査の失敗を道具の失敗として返す
        raise HTTPException(status_code=502, detail=f"調査に失敗しました: {str(exc)[:300]}") from exc
    report_body = reports.ReportBody.model_validate(
        research.to_report(planned["question"], result, refused=planned["refused"]))
    session = str(claims.get("sub") or "").removeprefix("opencode:")
    record = reports.save(report_body, owner_user_id=user.id, session=session, images_from=None)
    audit.record(db, "opencode.research", user=user, resource_type="opencode_report",
                 resource_id=record["id"], request=request,
                 metadata={"sources": planned["sources"], "depth": planned["depth"],
                           "external": any(item in research.EXTERNAL_SOURCES for item in planned["sources"])})
    report_text = str(result.get("report") or "")
    return {
        "report_id": record["id"],
        "report": report_text[:research.REPORT_INLINE_CHARS],
        "truncated": len(report_text) > research.REPORT_INLINE_CHARS,
        "sources": [{"title": item["title"], "url": item["url"]} for item in report_body.model_dump()["sources"][:40]],
        "unverified": report_body.unverified,
        "used_sources": planned["sources"],
    }


def issue_opencode_token(user_id: int, correlation_id: str, *, project_id: str | None = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", correlation_id)[:64]
    if not safe:
        raise ValueError("OpenCode correlation IDが不正です")
    return tokens.issue(
        "control-deck",
        subject=f"opencode:{safe}",
        kind="agent-mcp",
        actor_user_id=user_id,
        project_id=project_id,
        ttl_seconds=MCP_TOKEN_TTL_SECONDS,
    )


RENEW_HEADER = "X-Control-Deck-MCP-Token"


def _renewed_token(claims: dict[str, Any]) -> str | None:
    """残りが半分を切っていたら、同じ範囲で新しいトークンを返す。

    OpenCode の session は何日も開いたままになる。TTL を切ると 8 時間で MCP が
    使えなくなり、利用者からは「トークンが無い」としか見えない。かといって TTL を
    伸ばすと、設定ファイルが漏れたときの露出が延びる。使われている限り更新する
    形にして、放置された session は従来どおり期限で切れるようにする。
    """
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int):
        return None
    if expires_at - int(time.time()) > MCP_TOKEN_TTL_SECONDS // 2:
        return None
    subject = str(claims.get("sub") or "")
    correlation = subject.removeprefix("opencode:")
    actor_user_id = claims.get("actor_user_id")
    if not correlation or not isinstance(actor_user_id, int):
        return None
    project_id = claims.get("project_id")
    try:
        return issue_opencode_token(
            actor_user_id, correlation,
            project_id=project_id if isinstance(project_id, str) else None,
        )
    except ValueError:
        return None


def _offer_renewal(response: Response, claims: dict[str, Any]) -> None:
    token = _renewed_token(claims)
    if token is not None:
        response.headers[RENEW_HEADER] = token


def _bearer_user(
    authorization: str | None,
    db: Session,
    request: Request | None = None,
) -> tuple[User, dict[str, Any]]:
    # OpenCode の session は何日も開いたままになる。同じ機械の中からの呼び出しなら
    # 期限は見ない。token は「どの利用者として動くか」を決めるために残す。誰として
    # 動くかが分からないと、見える Add-on tool も監査の記録も決められない。
    local = request is not None and is_loopback(request)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="OpenCode Add-on MCP tokenが必要です")
    try:
        claims = tokens.verify(
            authorization.removeprefix("Bearer ").strip(),
            addon_id="control-deck",
            kind="agent-mcp",
            max_ttl_seconds=MCP_TOKEN_TTL_SECONDS,
            allow_expired=local,
        )
    except tokens.AddonTokenError as exc:
        raise HTTPException(status_code=401, detail="OpenCode Add-on MCP tokenが無効です") from exc
    subject = claims.get("sub")
    actor_user_id = claims.get("actor_user_id")
    if not isinstance(subject, str) or _SUBJECT.fullmatch(subject) is None or not isinstance(actor_user_id, int):
        raise HTTPException(status_code=401, detail="OpenCode Add-on MCP tokenが無効です")
    user = db.get(User, actor_user_id)
    if user is None or not user.is_active or "workflows.run" not in user_permissions(user):
        raise HTTPException(status_code=403, detail="Add-on agent toolを利用できません")
    return user, claims


def _eligible_output_addons(permissions: set[str]) -> list[str]:
    if not {"project_lab.view", "files.edit"}.issubset(permissions):
        return []
    addon_ids = {str(item["addon_id"]) for item in execution.discover("agent_tools", permissions)}
    eligible: list[str] = []
    for addon_id in sorted(addon_ids):
        try:
            current = registry.status(addon_id)
        except registry.AddonRegistryError:
            continue
        if current.get("enabled") and _OUTPUT_CAPABILITIES.issubset(current.get("granted_capabilities", [])):
            eligible.append(addon_id)
    return eligible


def _project_output_tool(project_id: object, permissions: set[str]) -> dict[str, Any] | None:
    if not isinstance(project_id, str):
        return None
    addons = _eligible_output_addons(permissions)
    if not addons:
        return None
    return {
        "name": PROJECT_OUTPUT_GRANT_TOOL,
        "description": "現在のControl Deck project内に、選択したAdd-on用の短期output grantを作成します。",
        # Add-on由来のtoolと同じく、制約付きデコーダが展開できない長さ制約は落とす。
        # 実際の上限は _resolve_project_directory が引き続き強制する。
        "inputSchema": execution.model_facing_schema({
            "type": "object",
            "properties": {
                "addon_id": {"type": "string", "enum": addons},
                "relative_directory": {"type": "string", "minLength": 1, "maxLength": 512},
            },
            "required": ["addon_id", "relative_directory"],
            "additionalProperties": False,
        }),
    }


def _eligible_input_addons(permissions: set[str]) -> list[str]:
    # 読むだけなので、書く側（files.edit）は要らない。project を見られる人が
    # 既に見られる file を、その project の add-on に渡すだけである。
    if "project_lab.view" not in permissions:
        return []
    addon_ids = {str(item["addon_id"]) for item in execution.discover("agent_tools", permissions)}
    eligible: list[str] = []
    for addon_id in sorted(addon_ids):
        try:
            current = registry.status(addon_id)
        except registry.AddonRegistryError:
            continue
        if current.get("enabled") and _INPUT_CAPABILITIES.issubset(current.get("granted_capabilities", [])):
            eligible.append(addon_id)
    return eligible


def _project_input_tool(project_id: object, permissions: set[str]) -> dict[str, Any] | None:
    if not isinstance(project_id, str):
        return None
    addons = _eligible_input_addons(permissions)
    if not addons:
        return None
    return {
        "name": PROJECT_INPUT_GRANT_TOOL,
        "description": (
            "現在のControl Deck project内のfileを、選択したAdd-onに読ませるための短期grantを"
            "作成します。返ったgrant_idは、そのAdd-onのtoolで参照画像などとして渡せます。"
        ),
        "inputSchema": execution.model_facing_schema({
            "type": "object",
            "properties": {
                "addon_id": {"type": "string", "enum": addons},
                "relative_path": {"type": "string", "minLength": 1, "maxLength": 512},
            },
            "required": ["addon_id", "relative_path"],
            "additionalProperties": False,
        }),
    }


def _resolve_project_file(project_id: str, relative_path: object) -> Path:
    """project の中の file を指す。指せなければ理由を言って断る。

    directory 版と同じ道筋を通す——`..` も絶対 path も symlink での抜けも、
    解決した後に project の中かどうかで見る。
    """
    resolved = _resolve_project_member(project_id, relative_path)
    if not resolved.is_file():
        raise HTTPException(status_code=422, detail="project内のfileを指定してください")
    return resolved


def _resolve_project_member(project_id: str, relative_directory: object) -> Path:
    """project の中の一点を指す。file か directory かはここでは見ない。"""
    if not isinstance(relative_directory, str) or len(relative_directory) > 512:
        raise HTTPException(status_code=422, detail="project内のpathが不正です")
    if "\\" in relative_directory:
        raise HTTPException(status_code=422, detail="project内のpathが不正です")
    normalized = relative_directory
    parts = normalized.split("/")
    if (
        not normalized or normalized.startswith(("/", "~")) or normalized in {".", "./"}
        or any(part in {"", ".", ".."} for part in parts) or "\x00" in normalized
    ):
        raise HTTPException(status_code=422, detail="project directoryが不正です")
    try:
        project = project_lab.resolve_project(project_id)
        resolved = (project / normalized).resolve(strict=True)
    except (project_lab.ProjectLabError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=422, detail="project内のpathが見つかりません") from exc
    if not resolved.is_relative_to(project):
        raise HTTPException(status_code=422, detail="project外は指定できません")
    return resolved


def _resolve_project_directory(project_id: str, relative_directory: object) -> Path:
    resolved = _resolve_project_member(project_id, relative_directory)
    if not resolved.is_dir():
        raise HTTPException(status_code=422, detail="project内のdirectoryを指定してください")
    return resolved


# 生成物を project へ置くツールは project_output_grant を必須にしている。agent は
# その grant を自分で作ってから呼ぶ必要があり、手順が1つ増えるぶん「生成はできた
# のに置けない」で止まりやすい。
#
# 自動で作っても境界は変わらない。作るのは呼ばれている add-on のぶんだけで、置き先は
# token が指している今の project の中に限られ、必要な権限も明示発行と同じものを見る。
# つまり agent が自分で control_deck.project_output_grant を呼べば得られるものと同じで、
# 省けるのは手順だけである。置き先を選びたいときは今までどおり明示的に呼べばよい。
AUTO_OUTPUT_DIRECTORY = "assets"


def _auto_output_grant(
    addon_id: str, project_id: object, permissions: set[str], user: User,
) -> str | None:
    """置き先の grant を自動で作る。作れないときは None を返す。

    ここで例外にしない。grant が無いまま進めば add-on 側の入力検証が
    「grant が要る」と言うので、そちらの方が理由が伝わる。
    """
    if not isinstance(project_id, str) or addon_id not in _eligible_output_addons(permissions):
        return None
    try:
        project = project_lab.resolve_project(project_id)
    except project_lab.ProjectLabError:
        return None
    directory = project / AUTO_OUTPUT_DIRECTORY
    try:
        # 置き先が無ければ作る。掘るのは project 直下の1段だけで、既にあれば触らない。
        directory.mkdir(exist_ok=True)
        if not directory.is_dir() or not directory.resolve().is_relative_to(project):
            return None
        grant = runtime_grants.create(addon_id, user.id, str(directory), "export")
    except (OSError, runtime_grants.GrantError):
        return None
    # 公開用の metadata は grant_id を返す（内部の id ではない）。
    value = grant.get("grant_id")
    return value if isinstance(value, str) else None


@router.get("/tools")
async def list_tools(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user, claims = _bearer_user(authorization, db, request)
    _offer_renewal(response, claims)
    permissions = user_permissions(user)
    threshold = _contract_threshold()
    tools = await execution.agent_mcp_tools(permissions, contract_threshold=threshold)
    for factory in (_project_output_tool, _project_input_tool):
        project_tool = factory(claims.get("project_id"), permissions)
        if project_tool is not None:
            tools.append(project_tool)
    if threshold > 0 and tools:
        tools.append(_contract_tool())
    tools.append(_report_tool())
    research = _research_tool()
    if research is not None:
        tools.append(research)
    if "settings.manage" in permissions:
        tools.append(_diagnostics_tool())
    if "workflows.run" in permissions:
        tools.append(_knowledge_tool())
        tools.append(_news_knowledge_tool())
    tools.extend(_lab_tools(permissions))
    tools.extend(_nexarch_tools(claims.get("project_id"), permissions))
    return {"tools": tools}


@router.post("/call")
async def call_tool(
    body: AgentMcpCall,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    user, claims = _bearer_user(authorization, db, request)
    _offer_renewal(response, claims)
    permissions = user_permissions(user)
    if body.name == CONTRACT_TOOL:
        if _contract_threshold() <= 0 or set(body.arguments) != {"name"}:
            raise HTTPException(status_code=404, detail="tool contract toolが見つかりません")
        requested = body.arguments.get("name")
        if not isinstance(requested, str):
            raise HTTPException(status_code=422, detail="道具の名前を渡してください")
        contract = await execution.agent_contract(requested, permissions)
        if contract is None:
            raise HTTPException(status_code=404, detail="Add-on agent toolが見つかりません")
        return {"name": requested, "inputSchema": contract}
    if body.name == REPORT_TOOL:
        return _save_report(body.arguments, user, claims, db, request)
    if body.name == DIAGNOSTICS_TOOL:
        if "settings.manage" not in permissions or not set(body.arguments) <= {"hours"}:
            raise HTTPException(status_code=404, detail="diagnostics toolが見つかりません")
        hours = body.arguments.get("hours", 24)
        if not isinstance(hours, (int, float)) or not 1 <= hours <= 168:
            raise HTTPException(status_code=422, detail="hours は 1〜168 です")
        import asyncio as _asyncio

        from app.diagnostics import report as diagnostics_report

        data = await _asyncio.to_thread(diagnostics_report.build, float(hours))
        audit.record(db, "diagnostics.report", user=user, resource_type="system", resource_id="diagnostics",
                     request=request, metadata={"hours": hours, "via": "agent_mcp"})
        return {"report_markdown": diagnostics_report.to_markdown(data)}
    if body.name in NEXARCH_TOOLS:
        import asyncio as _asyncio

        return await _asyncio.to_thread(_run_nexarch, body.name, body.arguments, claims, permissions)
    if body.name in LAB_TOOLS:
        import asyncio as _asyncio

        return await _asyncio.to_thread(_run_lab_tool, body.name, body.arguments, user, permissions, db, request)
    if body.name == RESEARCH_TOOL:
        return await _run_research(body.arguments, user, claims, db, request)
    if body.name == KNOWLEDGE_TOOL:
        if "workflows.run" not in permissions:
            raise HTTPException(status_code=404, detail="knowledge toolが見つかりません")
        return await _run_knowledge(body.arguments)
    if body.name == NEWS_KNOWLEDGE_TOOL:
        if "workflows.run" not in permissions:
            raise HTTPException(status_code=404, detail="news knowledge toolが見つかりません")
        return await _run_news_knowledge(body.arguments)
    if body.name == PROJECT_OUTPUT_GRANT_TOOL:
        project_id = claims.get("project_id")
        available = _project_output_tool(project_id, permissions)
        addon_id = body.arguments.get("addon_id")
        relative_directory = body.arguments.get("relative_directory")
        allowed = (
            available is not None
            and isinstance(addon_id, str)
            and addon_id in available["inputSchema"]["properties"]["addon_id"]["enum"]
            and set(body.arguments) == {"addon_id", "relative_directory"}
        )
        if not allowed or not isinstance(project_id, str):
            raise HTTPException(status_code=404, detail="project output grant toolが見つかりません")
        directory = _resolve_project_directory(project_id, relative_directory)
        try:
            result = runtime_grants.create(addon_id, user.id, str(directory), "export")
        except runtime_grants.GrantError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit.record(
            db,
            "addon.agent_mcp.project_output_grant",
            user=user,
            resource_type="addon",
            resource_id=addon_id,
            request=request,
            metadata={"project_id": project_id, "directory_depth": len(Path(str(relative_directory)).parts)},
        )
        return result
    if body.name == PROJECT_INPUT_GRANT_TOOL:
        project_id = claims.get("project_id")
        available = _project_input_tool(project_id, permissions)
        addon_id = body.arguments.get("addon_id")
        relative_path = body.arguments.get("relative_path")
        allowed = (
            available is not None
            and isinstance(addon_id, str)
            and addon_id in available["inputSchema"]["properties"]["addon_id"]["enum"]
            and set(body.arguments) == {"addon_id", "relative_path"}
        )
        if not allowed or not isinstance(project_id, str):
            raise HTTPException(status_code=404, detail="project input grant toolが見つかりません")
        target_file = _resolve_project_file(project_id, relative_path)
        try:
            result = runtime_grants.create(addon_id, user.id, str(target_file), "read")
        except runtime_grants.GrantError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit.record(
            db,
            "addon.agent_mcp.project_input_grant",
            user=user,
            resource_type="addon",
            resource_id=addon_id,
            request=request,
            metadata={"project_id": project_id, "path_depth": len(Path(str(relative_path)).parts)},
        )
        return result
    target = await execution.agent_mcp_target(body.name, permissions)
    if target is None:
        raise HTTPException(status_code=404, detail="Add-on agent toolが見つかりません")
    addon_id, contribution_id = target
    arguments = dict(body.arguments)
    if "project_output_grant" not in arguments:
        schema = await execution.agent_schema(addon_id, contribution_id, permissions=permissions)
        if "project_output_grant" in (schema.get("required") or []):
            issued = _auto_output_grant(addon_id, claims.get("project_id"), permissions, user)
            if issued is not None:
                arguments["project_output_grant"] = issued
                audit.record(
                    db,
                    "addon.agent_mcp.auto_output_grant",
                    user=user,
                    resource_type="addon",
                    resource_id=addon_id,
                    request=request,
                    metadata={
                        "project_id": str(claims.get("project_id")),
                        "directory": AUTO_OUTPUT_DIRECTORY,
                        "contribution_id": contribution_id,
                    },
                )
    try:
        job = await execution.create_agent_tool_job(
            addon_id,
            contribution_id,
            arguments,
            owner_user_id=user.id,
            permissions=permissions,
        )
        result = await execution.wait_agent_tool_job(job)
    except execution.AddonExecutionError as exc:
        audit.record(
            db,
            "addon.agent_mcp",
            user=user,
            resource_type="addon",
            resource_id=addon_id,
            result="failure",
            request=request,
            metadata={"contribution_id": contribution_id, "result_code": exc.code},
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail()) from exc
    audit.record(
        db,
        "addon.agent_mcp",
        user=user,
        resource_type="addon",
        resource_id=addon_id,
        request=request,
        metadata={"contribution_id": contribution_id, "job_id": job.id},
    )
    return result
