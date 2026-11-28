"""Nexarch のコンソールの鍵（ロック）: 保存・検知・自動復旧。

利用者の要望（2026-11-28）: 「開いているコンソールごとに、ロックをかけるようにしてほしい。
勝手に tmux とかを停止させられないようにしてほしい。ロックをかけたものは落とさないように」。

- 保護そのもの（閉じさせない）は manager.kill_session の中央ガードが持つ（@cd-commander-locked=1）。
  ここはその周りの 3 つ:
  1. 保存: ロックしたコンソールの情報（cli・account・cwd・label・model・effort・owner）を
     data/ai-commander/console-locks.json（0600・置き換えで書く）へ残す。tmux が消えた後も
     分かるように（印だけだと消えたら読めない）。
  2. 検知: 既存の resume の見回り（60 秒ごとの thread。resume.tick）から、ロックしたセッションが
     tmux から消えていないかを見る。消えていたら監査 commander.console.vanished と通知
     （タイトルだけ）。tmux の確認に失敗しても消失と扱わない。dead pane はセッションが残っている
     ので消失ではない。新しい見張りの loop は作らない（監視を増やしすぎない）。
  3. 自動復旧: 消えたロックのコンソールを同じ cli・account・作業フォルダ・label・model・effort
     で開き直し、続きから再開する（Claude --continue・Codex resume --last・OpenCode は既存の再開）。
     失敗も数えて 1 つの論理コンソールで 10 分 3 回まで（ID が変わっても記録は引き継ぐ。
     保存へ試みた時刻を残すので本体の再起動でも制限は維持される）。設定で切れる（既定は入）。

鍵の値は 1 つも保存しない（label・cli・アカウント名・作業フォルダのパスだけ）。
ロックの付け外しは画面（terminal.use）だけで、AI・MCP へは公開しない。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Callable
import time
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger(__name__)

# 復旧の歯止め（brief-console-lock.md「10 分に 3 回まで（ループしない）」）。
RESTORE_WINDOW = 10 * 60
RESTORE_MAX_ATTEMPTS = 3
# 上限で諦めたときの通知の繰り返しを抑える間隔。
GAVE_UP_NOTIFY_COOLDOWN = 10 * 60

# 印・保存・見回り・kill が同じ RLock を使う。途中で排他を解かない。
# 単一 backend process の API thread と既存 resume thread が対象。
from app.terminals.manager import _guard as _store_lock


class LockStoreError(RuntimeError):
    """ロックの保存が破損・読取不能。空として処理を続けてはいけない。"""


def _path() -> Path:
    return data_dir() / "ai-commander" / "console-locks.json"


def _read() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"locks": {}}
    except (OSError, ValueError) as exc:
        raise LockStoreError("ロックの保存を読めません。保護と復旧を安全側で停止しています") from exc
    try:
        if not isinstance(raw, dict) or not isinstance(raw.get("locks"), dict):
            raise ValueError("locks がありません")
        for origin, entry in raw["locks"].items():
            from app.terminals.manager import SESSION_ID_RE

            if not SESSION_ID_RE.fullmatch(origin) or not isinstance(entry, dict):
                raise ValueError("ロックの記録が不正です")
            if not SESSION_ID_RE.fullmatch(entry.get("session_id", "")):
                raise ValueError("セッション ID が不正です")
            if entry.get("pending_sid") and not SESSION_ID_RE.fullmatch(entry["pending_sid"]):
                raise ValueError("復旧先 ID が不正です")
            if entry.get("state", "locked") not in ("locked", "vanished", "restoring", "locking"):
                raise ValueError("ロックの状態が不正です")
            attempts = entry.get("attempts", [])
            if not isinstance(attempts, list) or any(not isinstance(t, (float, int)) for t in attempts):
                raise ValueError("復旧の時刻が不正です")
    except (TypeError, ValueError) as exc:
        raise LockStoreError("ロックの保存形式が不正です。保護と復旧を安全側で停止しています") from exc
    return raw


def _write(data: dict) -> None:
    """0600・置き換えで書く。作成時に 0600 を指定するので、chmod 前に読める窓が無い
    （alerts/events.py の save_prefs と同じ作法。失敗は呼び出し側へ出す）。"""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(temp, path)


def _entries() -> dict:
    return _read().get("locks", {})


def _display_name(entry: dict) -> str:
    return str(entry.get("label") or f"{entry.get('cli', '?')}/{entry.get('account', 'default')}")


def _find(session_id: str) -> tuple[str, dict] | None:
    """保存の中の 1 件を、今のセッション id か由来（最初に鍵をかけたとき）の id で引く。
    戻り値は (由来の id, 記録)。無ければ None。"""
    for origin, entry in _entries().items():
        if session_id in (origin, entry.get("session_id"), entry.get("pending_sid")):
            return origin, entry
    return None


def find_entry(session_id: str) -> dict | None:
    with _store_lock:
        found = _find(session_id)
        return found[1] if found else None


def lock_console(row: dict, *, user, now: float | None = None) -> None:
    """保存してから印を付ける（保存 → 印 → 状態の確定を 1 つの排他の中で）。

    既存のロックや復旧回数は保持する。書込不能なら tmux は変更しない。
    印の付与に失敗したら、印が無いと確かめられたときだけ新しい記録を消して失敗を返す。
    確かめられないときは記録（state=locking）を残して失敗を返す（保存の guard が閉じさせず、
    見回りがセッションの在る限り印を付け直す。保護を弱める方向へは倒さない）。
    """
    from app.terminals.manager import manager

    sid = str(row["id"])
    now = time.time() if now is None else now
    with _store_lock:
        found = _find(sid)
        data = _read()
        created = found is None
        if found is None:
            data["locks"][sid] = {
                "session_id": sid, "cli": str(row.get("commander_cli") or ""),
                "account": str(row.get("commander_account") or "default"),
                "cwd": manager.session_cwd(sid), "label": str(row.get("commander_label") or ""),
                "model": str(row.get("commander_model") or ""),
                "effort": str(row.get("commander_effort") or ""),
                "owner": str(row.get("commander_created_by") or getattr(user, "username", "")),
                "owner_id": getattr(user, "id", None), "locked_at": now,
                "state": "locking", "attempts": [], "restored_count": 0, "gave_up_at": 0.0,
            }
            _write(data)
            origin = sid
        else:
            origin = found[0]
        try:
            manager.set_locked(sid, True)
        except (OSError, RuntimeError, KeyError):
            marked = _marked(sid)
            if not marked:
                if created and marked is False:
                    _discard(origin)
                raise

        def locked(e: dict) -> None:
            # 冪等な再ロックでも失敗・ID 変更をまたぐ復旧回数をリセットしない。
            if e.get("pending_sid") == sid:
                e.pop("pending_sid", None)  # 暫定の復旧先を利用者が鍵で確定した
            e.update({"session_id": sid, "state": "locked"})

        _update(origin, locked)


def _marked(sid: str) -> bool | None:
    """印が付いているか。確かめられなければ None。"""
    from app.terminals.manager import manager

    try:
        return manager.is_locked(sid)
    except (OSError, RuntimeError, KeyError):
        return None


def _discard(origin: str) -> None:
    """付けられなかったロックの新しい記録を消す。消せなくても記録が保護を続けるだけ。"""
    try:
        data = _read()
        if data["locks"].pop(origin, None) is not None:
            _write(data)
    except (OSError, LockStoreError):
        logger.exception("付けられなかったロックの記録を消せませんでした（保護は継続）")


def unlock_console(session_id: str) -> bool:
    """現在の ID と暫定復旧先の印を外し、保存を消す。失敗は必ず API へ返す。

    保存失敗時は印を戻す。戻す通信にも失敗した場合でも、保存の guard は残る。
    自動処理にはこの解除を呼ぶ道を設けない。
    """
    from app.terminals.manager import manager

    with _store_lock:
        found = _find(session_id)
        data = _read()
        targets = {session_id} if found is None else {
            str(found[1]["session_id"]), str(found[1].get("pending_sid") or found[1]["session_id"]),
        }
        changed: list[str] = []
        try:
            for sid in sorted(targets):
                if manager.session_exists(sid):
                    changed.append(sid)  # timeout の場合も実際は印が消えたかもしれない
                    manager.set_locked(sid, False)
            if found is not None:
                data["locks"].pop(found[0])
                _write(data)
        except (OSError, RuntimeError, KeyError):
            for sid in changed:
                try:
                    manager.set_locked(sid, True)
                except (OSError, RuntimeError, KeyError):
                    logger.exception("解除の失敗後、ロックの印を戻せませんでした（保存で保護を継続）")
            raise
        return found is not None


# ---------------------------------------------------------------- 検知と自動復旧

def check(now: float | None = None) -> list[dict]:
    with _store_lock:
        return _check(now)


def _check(now: float | None = None) -> list[dict]:
    """見回り 1 回分（resume.tick と同じ thread で動く）。

    ロックしたセッションが tmux に無ければ「外から消えた」として監査・通知し、
    設定（auto_restore・既定は入）が許すなら同じ情報で開き直す。tmux の確認に
    失敗したときは何もしない（消失と誤認しない）。出来事を
    [{"event": "vanished"|"restored"|"failed"|"gave_up", ...}] で返す。
    """
    now = time.time() if now is None else now
    events: list[dict] = []
    for origin, snapshot in list(_entries().items()):
        try:
            _check_one(origin, dict(snapshot), now, events)
        except LockStoreError:
            raise  # 保存が壊れた。残りも安全側で止める
        except (OSError, RuntimeError, KeyError, ValueError) as exc:
            # 1 件の tmux・保存の失敗で他のコンソールの見回りを止めない。
            # 記録は失敗の手前の状態のまま残るので、次の見回りが同じ ID を確かめて続ける。
            logger.warning("ロックしたコンソール %s の見回りを完了できませんでした: %s", origin, exc)
    return [e for e in events if e]


def _check_one(origin: str, entry: dict, now: float, events: list) -> None:
    """1 件分。出来事は途中で失敗しても失わないよう、その場で events へ足す。"""
    from app.terminals.manager import manager

    sid = str(entry.get("pending_sid") or entry.get("session_id") or origin)
    state = str(entry.get("state") or "locked")
    try:
        exists = manager.session_exists(sid)
    except (OSError, RuntimeError, KeyError) as exc:
        # tmux を確認できなかっただけ。消失としてはいけない（誤認は復旧の空振り・複製になる）
        logger.warning("ロックしたコンソール %s の確認ができませんでした: %s", sid, exc)
        return
    if exists:
        if state != "locked":
            # 復旧途中（印・保存の前に止まった）か、付け損ねたロック。同じ ID を保護して確定する。
            # 新しいコンソールは作らない（複製しない）。
            if state == "restoring":
                manager.tag_session(sid, _marks(entry))
            manager.set_locked(sid, True)
            _adopt(origin, entry, sid)
        return
    if state in ("locked", "locking"):
        if not _update(origin, lambda e: e.update({"state": "vanished", "vanished_at": now})):
            return  # 見ている間に外された
        _audit("commander.console.vanished", entry, origin, session_id=sid)
        _notify(entry, f"Nexarch: 「{_display_name(entry)}」が外から閉じられました",
                tag=f"console-vanished:{origin}")
        events.append({"event": "vanished", "origin": origin, "session_id": sid})
        entry["state"] = "vanished"
    if state == "restoring":
        # 予約済 ID が確実に無い。失敗も消費した回数として残したまま次へ。
        _update(origin, lambda e: (e.pop("pending_sid", None), e.update({"state": "vanished"})))
        entry.pop("pending_sid", None)
        entry["state"] = "vanished"
    if entry.get("state") == "vanished":
        events.append(_try_restore(origin, entry, now))


def _try_restore(origin: str, entry: dict, now: float) -> dict | None:
    """消えたロックのコンソールを開き直す。10 分 3 回まで（失敗も数える）。"""
    from app.commander import settings as commander_settings
    from app.terminals.manager import manager

    if not commander_settings.auto_restore():
        return None  # 設定で切れているときは検知だけ
    attempts = [float(ts) for ts in entry.get("attempts", []) if now - float(ts) < RESTORE_WINDOW]
    if len(attempts) >= RESTORE_MAX_ATTEMPTS:
        gave_up_at = float(entry.get("gave_up_at") or 0)
        if not gave_up_at:
            if not _update(origin, lambda e: e.update({"attempts": attempts, "gave_up_at": now})):
                return None
            entry["attempts"], entry["gave_up_at"] = attempts, now
            _audit("commander.console.restore_gave_up", entry, origin,
                   session_id=str(entry.get("session_id") or origin), attempts=len(attempts))
            _notify(entry, f"Nexarch: 「{_display_name(entry)}」の自動復旧を止めました（10 分で 3 回）",
                    tag=f"console-gave-up:{origin}", cooldown=GAVE_UP_NOTIFY_COOLDOWN)
            return {"event": "gave_up", "origin": origin}
        return None  # 上限の通知・監査は 1 度だけ
    # 作成する ID と試行を先に保存。作成の timeout・印や adopt の失敗・再起動でも
    # 同じ ID を確認してから進むので、追跡不能の orphan / 重複を増やさない。
    new_sid = secrets.token_hex(4)
    if manager.session_exists(new_sid):
        raise RuntimeError("復旧先 ID が使用中です")
    attempts.append(now)
    _update(origin, lambda e: e.update({"attempts": attempts, "gave_up_at": 0.0,
                                      "pending_sid": new_sid, "state": "restoring"}))
    entry.update({"attempts": attempts, "pending_sid": new_sid, "state": "restoring"})
    try:
        result = _recreate(entry)
        if not result.get("opened"):
            raise RuntimeError(str(result.get("reason") or "開けませんでした"))
        if result["opened"][0]["id"] != new_sid:
            raise RuntimeError("復旧先 ID が一致しません")
        manager.set_locked(new_sid, True)
        _adopt(origin, entry, new_sid)
    except Exception as exc:  # noqa: BLE001 - 次回は予約 ID を調べる。自動解除・kill はしない
        logger.warning("ロックしたコンソール %s の復旧を完了できませんでした: %s", origin, exc)
        _audit("commander.console.restore_failed", entry, origin,
               session_id=new_sid, reason=f"{type(exc).__name__}: {exc}"[:160])
        return {"event": "failed", "origin": origin}
    return {"event": "restored", "origin": origin, "session_id": new_sid}


def _adopt(origin: str, entry: dict, sid: str) -> None:
    pending = bool(entry.get("pending_sid"))

    def adopt(e: dict) -> None:
        e.pop("pending_sid", None)
        e.update({"session_id": sid, "state": "locked",
                  "restored_count": int(e.get("restored_count") or 0) + int(pending)})

    _update(origin, adopt)
    if pending:
        _audit("commander.console.restored", entry, origin, session_id=sid,
               attempt=len(entry.get("attempts", [])))
        _notify(entry, f"Nexarch: 「{_display_name(entry)}」を開き直しました（続きから）",
                tag=f"console-restored:{origin}")


def _marks(entry: dict) -> dict[str, str]:
    """復旧先のセッションへ付け直す一覧用の印（空の値は付けない。tag_session の決まり）。"""
    marks = {"cli": entry.get("cli"), "account": entry.get("account"), "created_by": entry.get("owner"),
             "label": entry.get("label"), "model": entry.get("model"), "effort": entry.get("effort")}
    return {key: str(value) for key, value in marks.items() if value}


def _recreate(entry: dict) -> dict:
    """同じ cli・account・作業フォルダ・label・model・effort で開き直す（consoles へ任せる）。"""
    from app.commander import accounts, consoles

    cli = accounts.CLIS.get(str(entry.get("cli") or ""))
    if cli is None:
        raise ValueError("CLI の表に無い cli です")
    return consoles.create_consoles(
        cli=cli,
        account=str(entry.get("account") or accounts.DEFAULT_ACCOUNT),
        cwd=str(entry.get("cwd") or ""),
        count=1,
        label=str(entry.get("label") or ""),
        created_by=str(entry.get("owner") or "nexarch"),
        user_id=entry.get("owner_id") if isinstance(entry.get("owner_id"), int) else None,
        model=str(entry.get("model") or ""),
        effort=str(entry.get("effort") or ""),
        resume=True, session_id=str(entry["pending_sid"]),
    )


def _update(origin: str, mutate: Callable[[dict], object]) -> bool:
    """保存の 1 件を原子に変える。見ている間に外されていたら何もしない（False）。"""
    with _store_lock:
        data = _read()
        entry = data.get("locks", {}).get(origin)
        if entry is None:
            return False
        mutate(entry)
        _write(data)
        return True


def _audit(action: str, entry: dict, origin: str, **extra) -> None:
    """見回りの側の監査（user は無し。resume._audit と同じ作法）。失敗しても止めない。

    resource_id は最初に鍵をかけたときのセッション id（由来）で固定する。
    復旧で tmux の id が変わっても、1 つの論理コンソールとして履歴が追える。
    """
    from app.audit import service as audit
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            audit.record(db, action, user=None, resource_type="commander_console",
                         resource_id=origin,
                         metadata={
                             "cli": entry.get("cli", ""), "account": entry.get("account", ""),
                             "label": entry.get("label", ""), **extra,
                         })
    except Exception:  # noqa: BLE001 - 記録できなくても検知・復旧は止めない
        logger.exception("コンソールの鍵の監査ログを書けませんでした (%s)", action)


def _notify(entry: dict, title: str, *, tag: str, cooldown: float = 0) -> None:
    """通知はタイトルだけ（種類: console_vanish。本文・パス・鍵は載せない）。"""
    from app.alerts.events import notify_event

    try:
        notify_event("console_vanish", title=title, body="", url="/commander",
                     permission="terminal.use", tag=tag, cooldown=cooldown)
    except Exception:  # noqa: BLE001 - 通知の失敗で止めない
        logger.exception("コンソールの鍵の通知を送れませんでした")


# ---------------------------------------------------------------- 診断レポート用

def summary() -> dict:
    """診断レポートへ出す今の状態（保存を読むだけ・重い処理はしない）。"""
    now = time.time()
    locked = vanished = attempts_recent = gave_up_recent = 0
    rows: list[dict] = []
    try:
        with _store_lock:
            entries = _entries()
    except LockStoreError as exc:
        return {"locked": 0, "vanished": 0, "attempts_10min": 0, "gave_up_10min": 0,
                "consoles": [], "error": str(exc), "stopped": True}
    for origin, entry in entries.items():
        state = str(entry.get("state") or "locked")
        if state == "locked":
            locked += 1
        else:
            vanished += 1
        attempts = [float(ts) for ts in entry.get("attempts", []) if now - float(ts) < RESTORE_WINDOW]
        attempts_recent += len(attempts)
        if float(entry.get("gave_up_at") or 0) and now - float(entry["gave_up_at"]) < RESTORE_WINDOW:
            gave_up_recent += 1
        rows.append({"name": _display_name(entry), "state": state,
                     "session_id": str(entry.get("session_id") or origin),
                     "restored_count": int(entry.get("restored_count") or 0),
                     "attempts_10min": len(attempts)})
    return {"locked": locked, "vanished": vanished, "attempts_10min": attempts_recent,
            "gave_up_10min": gave_up_recent, "consoles": rows[:20]}
