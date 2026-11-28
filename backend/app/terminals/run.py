"""AI アシスタントの「▶ 実行」を、画面の中で実行して出力を見せる（2026-11-28 利用者の依頼）。

tmux のセッションを 1 つ作ってコマンドを貼り、出力の読み取り・Ctrl-C・sudo のパスワード入力を
作った本人だけに許す。入力（パスワード）は標準入力で tmux へ渡す（manager.inject_input）。
**パスワードはログ・監査・応答・例外の文言のどこにも残さない**:
- 本文は pydantic を通さず自前で検査する（検証エラーが入力を返さないように）。
- 監査は「入力を送った」ことと長さだけ。
- prompt が出ていないときは送らない（409）。取り違えて平文を画面に出さないため。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import signal
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import require_permissions
from app.terminals.manager import SessionStateError, manager
from app.terminals.router import resolve_session_cwd

logger = logging.getLogger("control_deck.terminals.run")

router = APIRouter(prefix="/terminals/run", tags=["terminals"])
# 管理者だけ（今の確認のシートと同じ）
run_user = require_permissions("terminal.use", "settings.manage")

RUN_LABEL = "AI アシスタントの実行"
COMMAND_MAX = 20_000
SECRET_MAX = 1024
SECRET_BODY_MAX = 8 * 1024
OUTPUT_LINES = 200

DONE_MARK = "__CD_RUN_DONE__"
# 終わりの印を出す文。コマンドの文字列には連結せず、別の行として貼る（終了コードは直前の $?）
DONE_LINE = "printf '\\n" + DONE_MARK + ":%s\\n' \"$?\""
# 止めたあとの印。$? は止め方で変わる（Ctrl-C が捨てた行の続きなので 0 になることもある）ため 130 で固定する
STOPPED_LINE = "printf '\\n" + DONE_MARK + ":130\\n'"
_DONE_RE = re.compile(r"^" + DONE_MARK + r":(\d{1,3})\s*$")

# パスワードを求める行（sudo の英語・日本語・su・doas など）。判定はこの 1 か所だけ。
_PROMPT_RE = re.compile(
    r"^(?:\[sudo\].*|[^\n]{0,60}?(?:password|パスワード)[^\n]{0,40}?)[:：]\s*$", re.IGNORECASE)


def password_prompt(lines: list[str]) -> bool:
    """最後の数行（空行を除く最後の行）がパスワードの要求で終わっているか。"""
    tail = [line.rstrip() for line in lines if line.strip()][-1:]
    return bool(tail) and len(tail[0]) <= 120 and bool(_PROMPT_RE.match(tail[0].strip()))


def parse_capture(text: str) -> dict:
    """capture-pane の文字から {output, done, exit_code, password_prompt} を作る。"""
    lines = text.splitlines()
    exit_code: int | None = None
    for line in lines:
        match = _DONE_RE.match(line)
        if match:
            exit_code = int(match.group(1))
    shown = [line for line in lines if DONE_MARK not in line]
    while shown and not shown[-1].strip():
        shown.pop()
    done = exit_code is not None
    return {
        "output": "\n".join(shown[-OUTPUT_LINES:]),
        "done": done,
        "exit_code": exit_code,
        "password_prompt": (not done) and password_prompt(shown),
    }


def _owned_session(sid: str, user: User) -> str:
    """作った本人のセッションだけを通す。違う・無い・印が無いは全て 404（存在を教えない）。"""
    try:
        owner = manager.read_mark(sid, "run_owner")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="セッションが見つかりません") from exc
    except SessionStateError as exc:
        raise HTTPException(status_code=503, detail="セッションの状態を確認できませんでした") from exc
    if owner != str(user.id):
        raise HTTPException(status_code=404, detail="セッションが見つかりません")
    return sid


class RunBody(BaseModel):
    command: str = Field(min_length=1, max_length=COMMAND_MAX)
    cwd: str = Field(default="", max_length=4096)


@router.post("", status_code=201)
def start_run(
    request: Request,
    body: RunBody,
    user: User = Depends(run_user),
    db: Session = Depends(get_db),
):
    command = body.command.rstrip("\n")
    if not command.strip() or "\x00" in command:
        raise HTTPException(status_code=422, detail="コマンドが不正です")
    cwd = resolve_session_cwd(body.cwd)
    try:
        session = manager.create_session(cwd=cwd)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    sid = session["id"]
    try:
        manager.tag_session(sid, {"label": RUN_LABEL, "created_by": user.username[:64] or "user",
                                  "run_owner": str(user.id)})
        manager.inject_input(sid, command + "\n" + DONE_LINE, submit=True)
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        logger.warning("run: セッション %s へコマンドを貼れませんでした (%s)", sid, type(exc).__name__)
        _discard(sid)
        raise HTTPException(status_code=503, detail="コマンドを実行できませんでした") from exc
    audit.record(
        db, "terminal.run", user=user, resource_type="terminal", resource_id=sid, request=request,
        # コマンドの本文は残さない（秘密が混じりうる）。長さと短い指紋・cwd だけ
        metadata={"length": len(command), "sha256_12": hashlib.sha256(command.encode()).hexdigest()[:12],
                  **({"cwd": cwd} if cwd else {})},
    )
    return {"session_id": sid}


def _discard(sid: str) -> None:
    try:
        manager.kill_session(sid)
    except Exception:  # noqa: BLE001 - 片付けの失敗で元のエラーを隠さない
        logger.warning("run: セッション %s を片付けられませんでした", sid)


@router.get("/{sid}")
def read_run(sid: str, user: User = Depends(run_user)):
    _owned_session(sid, user)
    return parse_capture(manager.capture_text(sid, OUTPUT_LINES))


async def _secret_body(request: Request) -> str:
    """{"secret": "..."} を自前で検査する。失敗の応答にも本文を入れない（pydantic の検証エラーは入力を返す）。"""
    raw = await request.body()
    bad = HTTPException(status_code=422, detail="入力が不正です")
    if len(raw) > SECRET_BODY_MAX:
        raise bad
    try:
        data = json.loads(raw)
    except ValueError:
        raise bad from None
    secret = data.get("secret") if isinstance(data, dict) else None
    if (not isinstance(secret, str) or not 1 <= len(secret) <= SECRET_MAX
            or any(c in secret for c in "\x00\r\n")):
        raise bad
    return secret


@router.post("/{sid}/input")
def send_run_input(
    sid: str,
    request: Request,
    user: User = Depends(run_user),
    secret: str = Depends(_secret_body),
    db: Session = Depends(get_db),
):
    _owned_session(sid, user)
    if not parse_capture(manager.capture_text(sid, 20))["password_prompt"]:
        # パスワードを求めていない所へ送ると、平文が画面に出る
        raise HTTPException(status_code=409, detail="いまパスワードは求められていません")
    length = len(secret)
    try:
        manager.inject_input(sid, secret, submit=True)
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        logger.warning("run: セッション %s へ入力を送れませんでした (%s)", sid, type(exc).__name__)
        raise HTTPException(status_code=503, detail="入力を送れませんでした") from exc
    finally:
        del secret
    audit.record(
        db, "terminal.run.input", user=user, resource_type="terminal", resource_id=sid, request=request,
        metadata={"length": length},
    )
    return {"ok": True}


def _mark_done_after_interrupt(sid: str) -> None:
    """Ctrl-C は貼った複数行の残り（終わりの印）も捨てる。シェルが入力待ちに戻ったら印だけ出し直す（終了値は 130 で固定）。戻らなければ（Ctrl-C を無視する処理）何も貼らない。"""
    for attempt in range(12):
        time.sleep(0.2)
        # 文字の Ctrl-C で止まらない（貼った複数行の実行では SIGINT・SIGTERM が効かないことを実測 2026-11-28）。
        # 前面のプロセスグループへ INT → TERM → KILL と強めていく（止めるのは利用者が押した「止める」）
        escalate = {4: signal.SIGINT, 7: signal.SIGTERM, 10: signal.SIGKILL}.get(attempt)
        if escalate is not None and not manager.pane_idle(sid):
            manager.interrupt_foreground(sid, escalate)
        if manager.pane_idle(sid):
            try:
                manager.inject_input(sid, STOPPED_LINE, submit=True)
            except (ValueError, KeyError, OSError, RuntimeError):
                logger.warning("run: セッション %s へ終わりの印を出せませんでした", sid)
            return


@router.post("/{sid}/stop")
def stop_run(sid: str, request: Request, user: User = Depends(run_user), db: Session = Depends(get_db)):
    _owned_session(sid, user)
    try:
        manager.send_interrupt(sid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="セッションが見つかりません") from exc
    except (OSError, SessionStateError) as exc:
        raise HTTPException(status_code=503, detail="Ctrl-C を送れませんでした") from exc
    _mark_done_after_interrupt(sid)
    audit.record(db, "terminal.run.stop", user=user, resource_type="terminal", resource_id=sid, request=request)
    return {"ok": True}
