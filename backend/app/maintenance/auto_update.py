"""ControlDeck 本体の自動適用（docs/POLICY-autonomous-development.md の L1）。

正式 Release が出ていて、ControlDeck が使われていない時間が続いたら、管理者が押すのと同じ
更新（self_update.start）を始める。更新そのもの（バックアップ・build・再起動・確認・失敗時の
巻き戻し）は self_update が行う。ここが決めるのは「いつ始めるか」と「結果を知らせる」だけ。

使われているとみなすもの（どれか 1 つでもあれば待つ）:
- ゲートウェイの推論中（OpenCode・チャット・Add-on の AI）、Add-on へ GPU を貸している最中
- 実行中・待機中の Job（Workflow・Add-on の生成・OpenCode の headless 実行）
- 開いている OpenCode の対話セッション（再起動で LLM への接続が切れる）
- Add-on が持っている GPU の lease

検証用インスタンス（CONTROL_DECK_ISOLATED）では動かない。

強制（force・既定は入。利用者 2026-11-28「本体の更新は 5 分ごとにチェックして、強制的に更新する機能を入れて。
使われている時でも更新する。既定 On」）: 上の「使われている」を待たず、新しい Release を見つけたらすぐ適用する。
走っている AI チーム・Job は再起動で中断される（AI チームは起動のときに再開する）。切れば従来どおり待つ。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.auto_update")

DEFAULTS = {"enabled": True, "idle_minutes": 10, "check_interval_minutes": 5, "force": True}
OPENCODE_COMMANDS = {"opencode", "opencode2", "podman"}


def _settings_path() -> Path:
    return data_dir() / "updates" / "auto.json"


def get_settings() -> dict:
    values = dict(DEFAULTS)
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            values.update({key: raw[key] for key in DEFAULTS if key in raw})
            if "force" not in raw:
                # 強制の項目が無い古い保存（確認は 30 分ごとが既定だった）は、新しい既定（5 分ごと）へ移す
                values["check_interval_minutes"] = min(int(values["check_interval_minutes"]), DEFAULTS["check_interval_minutes"])
    except (OSError, ValueError):
        pass
    values["enabled"] = bool(values["enabled"])
    values["force"] = bool(values["force"])
    values["idle_minutes"] = max(1, min(240, int(values["idle_minutes"])))
    values["check_interval_minutes"] = max(5, min(1440, int(values["check_interval_minutes"])))
    return values


def save_settings(patch: dict) -> dict:
    values = get_settings()
    for key in DEFAULTS:
        if key in patch:
            values[key] = patch[key]
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values), encoding="utf-8")
    os.replace(tmp, path)
    return get_settings()


def _prompt_path() -> Path:
    return data_dir() / "updates" / "apply-now.json"


def request_prompt_apply(release_tag: str) -> None:
    """利用者が承認した Release は、使われていなければ待たずに適用する（10 分の待ちを飛ばす）。"""
    path = _prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"release_tag": release_tag, "at": time.time()}), encoding="utf-8")


def _prompt_requested(release_tag: str) -> bool:
    try:
        value = json.loads(_prompt_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return value.get("release_tag") == release_tag and time.time() - float(value.get("at") or 0) < 86400


def busy_reasons() -> list[str]:
    """いま本番が使われている理由。空なら使われていない。"""
    reasons: list[str] = []
    try:
        from app.models_mgmt.resource_provider import provider

        llm = provider()
        if llm._active_requests > 0:
            reasons.append("LLM の推論中")
        if llm.lending():
            reasons.append("Add-on へ GPU を貸出中")
    except Exception:  # noqa: BLE001 - 判定できないものは使用中とみなす
        reasons.append("LLM の状態を確認できない")
    try:
        from app.jobs import service as jobs

        if any(job.status in ("queued", "running") for job in list(jobs._jobs.values())):
            reasons.append("実行中の Job")
    except Exception:  # noqa: BLE001
        reasons.append("Job の状態を確認できない")
    try:
        from app.terminals.manager import manager as terminals

        for session in terminals.list_sessions():
            if str(session.get("program") or "") in OPENCODE_COMMANDS:
                reasons.append("OpenCode のセッションが開いている")
                break
    except Exception:  # noqa: BLE001
        reasons.append("ターミナルの状態を確認できない")
    try:
        from app.resources.broker import broker

        if any(lease.owner.startswith("addon:") for lease in broker.leases.current()):
            reasons.append("Add-on が GPU を使用中")
    except Exception:  # noqa: BLE001
        reasons.append("GPU の状態を確認できない")
    return reasons


async def notify(title: str, message: str, *, kind: str = "update") -> None:
    """有効な通知先すべてへ送る。届かなくても更新は止めない。"""
    try:
        from app.alerts.notify import send_notification
        from app.database import SessionLocal
        from app.models import NotificationChannel
        from app.security.crypto import decrypt_text

        def channels() -> list[tuple[str, str]]:
            with SessionLocal() as db:
                rows = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all()
                result = []
                for row in rows:
                    try:
                        result.append((row.channel_type, decrypt_text(row.url_encrypted)))
                    except Exception:  # noqa: BLE001
                        continue
                return result

        for channel_type, url in await asyncio.to_thread(channels):
            await send_notification(channel_type, url, title, message, kind=kind)
    except Exception:  # noqa: BLE001 - 通知の失敗で更新を止めない
        logger.exception("更新の通知を送れませんでした")


async def report_result_once() -> None:
    """再起動の後、直前の更新の結果を一度だけ知らせる。"""
    from app.maintenance import self_update

    state = await asyncio.to_thread(self_update._saved_state)
    if not state.get("automatic") or state.get("reported") or state.get("phase") not in ("succeeded", "failed"):
        return
    if state["phase"] == "succeeded":
        await notify("ControlDeck を更新しました",
                     f"{state.get('release_tag') or ''} を自動で適用しました（{str(state.get('new_sha', ''))[:12]}）。")
    else:
        await notify("ControlDeck の自動更新に失敗しました",
                     f"{state.get('error') or '原因不明'}。前の版で動いています。")
    state["reported"] = True
    await asyncio.to_thread(self_update._write_state, state)


async def tick(idle_since: float | None, now: float) -> float | None:
    """1 回分の判断。戻り値は「使われていない状態がいつから続いているか」。"""
    from app.maintenance import self_update

    settings = get_settings()
    if not settings["enabled"]:
        return None
    current = await asyncio.to_thread(self_update.status)
    if current.get("error") or not current.get("update_available"):
        return None
    if str((current.get("job") or {}).get("phase")) in ("scheduled", "running"):
        return None
    if settings["force"]:
        target = str(current["remote_sha"])
        _prompt_path().unlink(missing_ok=True)
        await asyncio.to_thread(self_update.start, target, automatic=True, release_tag=str(current.get("release_tag") or ""))
        await notify("ControlDeck を自動更新します",
                     f"{current.get('release_tag')} を適用します（使用中でも更新する設定）。数分で再起動し、異常があれば自動で前の版へ戻します。")
        return None
    reasons = await asyncio.to_thread(busy_reasons)
    if reasons:
        logger.info("更新 %s を待っています: %s", current.get("release_tag"), "、".join(reasons))
        return None
    idle_since = idle_since or now
    prompt = _prompt_requested(str(current.get("release_tag") or ""))
    if not prompt and now - idle_since < settings["idle_minutes"] * 60:
        return idle_since
    if prompt:
        _prompt_path().unlink(missing_ok=True)
    target = str(current["remote_sha"])
    await asyncio.to_thread(self_update.start, target, automatic=True, release_tag=str(current.get("release_tag") or ""))
    await notify("ControlDeck を自動更新します",
                 f"{current.get('release_tag')} を適用します。数分で再起動し、異常があれば自動で前の版へ戻します。")
    return None


async def auto_update_loop() -> None:
    from app.config import isolated

    if isolated():
        return
    try:
        await report_result_once()
    except Exception:  # noqa: BLE001
        logger.exception("更新結果の通知に失敗しました")
    idle_since: float | None = None
    while True:
        try:
            # 使われていない時間を数えるため、確認は短い間隔で行う。Release の取得は
            # self_update.status が行うので、GitHub への問い合わせは check_interval に合わせる。
            idle_since = await tick(idle_since, time.time())
        except Exception:  # noqa: BLE001 - 判断の失敗で service を止めない
            logger.exception("自動更新の判断に失敗しました")
            idle_since = None
        # 承認された Release が待っていれば 1 分ごとに見る（使われていなくなったらすぐ適用）。
        # 長く眠っている間に承認されても気づけるよう、眠りは 1 分ずつに分ける。
        waiting = idle_since or _prompt_path().exists()
        interval = 60 if waiting else get_settings()["check_interval_minutes"] * 60
        for _ in range(max(1, interval // 60)):
            await asyncio.sleep(60)
            if not waiting and _prompt_path().exists():
                break
