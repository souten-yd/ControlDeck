"""オプション機能の自動更新（1 日 1 回、決めた時刻に）。

2026-11-20 利用者「設定の機能と拡張のオプション機能を、1 日 1 回ぐらい時間を決めて自動でバージョンアップできるように。
ボタンと時間設定を付けて」。

- 決めた時刻（サーバーの時計）を過ぎたら、その日のうちに 1 回だけまとめて更新する
- 使われている間（推論中・Job・OpenCode のセッション・Add-on の GPU）は待ち、空いたら始める
  （判定は本体の自動更新と同じ maintenance.auto_update.busy_reasons）
- 更新するのは ControlDeck が導入した（managed）もので、壊れていないもの。音声エンジンは開発終了なので除く。
  GPU ランタイムは新しい Release があるときだけ。npm・release bundle は各自の更新（最新を取得）に任せる
- 1 つが失敗しても残りは続ける。結果は保存して設定画面に出し、通知先へも送る
- 「今すぐ更新」は時刻を待たずに同じ処理を Job で走らせる

検証用インスタンス（CONTROL_DECK_ISOLATED）では自動では動かない。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.feature_auto_update")

DEFAULTS = {"enabled": False, "time": "04:00"}
JOB_KIND = "feature.auto_update"
_TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


def _settings_path() -> Path:
    return data_dir() / "updates" / "features-auto.json"


def _state_path() -> Path:
    return data_dir() / "updates" / "features-auto-state.json"


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def valid_time(value: object) -> bool:
    return isinstance(value, str) and bool(_TIME.fullmatch(value))


def get_settings() -> dict:
    raw = _read(_settings_path())
    return {
        "enabled": bool(raw.get("enabled", DEFAULTS["enabled"])),
        "time": raw["time"] if valid_time(raw.get("time")) else DEFAULTS["time"],
    }


def save_settings(patch: dict) -> dict:
    values = get_settings()
    if "enabled" in patch:
        values["enabled"] = bool(patch["enabled"])
    if "time" in patch:
        if not valid_time(patch["time"]):
            raise ValueError("時刻は HH:MM（00:00〜23:59）で指定してください")
        values["time"] = patch["time"]
    _write(_settings_path(), values)
    return get_settings()


def get_state() -> dict:
    """前回の実行（日付・始めた/終えた時刻・各機能の結果）。"""
    return _read(_state_path())


def due(settings: dict, state: dict, now: datetime) -> bool:
    """今日の時刻を過ぎていて、今日まだ自動で走らせていなければ True。"""
    if not settings["enabled"]:
        return False
    if now.strftime("%H:%M") < settings["time"]:
        return False
    return state.get("scheduled_date") != now.strftime("%Y-%m-%d")


def running() -> bool:
    from app.jobs import service as jobs

    return any(job.kind == JOB_KIND and job.status in ("queued", "running") for job in list(jobs._jobs.values()))


def targets() -> list[dict]:
    """更新の対象（ControlDeck が導入した・壊れていない・更新の仕組みがある）。"""
    from app.features.registry import invalidate_feature_cache, list_features

    invalidate_feature_cache()
    return [item for item in list_features()
            if item.get("installed") and item.get("managed") and not item.get("error")
            and item.get("kind") != "speech-engine"]


async def _update_one(item: dict, job=None) -> dict:
    from app.features import gpu_runtime, registry

    feature_id = str(item["id"])
    before = str(item.get("version") or "")
    entry = {"id": feature_id, "name": str(item.get("name") or feature_id), "before": before,
             "after": before, "status": "current", "message": ""}
    try:
        if item.get("kind") == "gpu-runtime":
            release = await gpu_runtime.release_status(feature_id)
            if release.get("error"):
                return {**entry, "status": "failed", "message": str(release["error"])}
            if not release.get("update_available"):
                return entry
            result = await gpu_runtime.update(feature_id, job)
        else:
            result = await asyncio.to_thread(registry.update, feature_id)
        after = str(result.get("version") or registry.status(feature_id).get("version") or "")
        return {**entry, "after": after, "status": "updated" if after and after != before else "current"}
    except Exception as exc:  # noqa: BLE001 - 1 つの失敗で残りを止めない
        logger.warning("オプション機能 %s の自動更新に失敗しました: %s", feature_id, exc)
        return {**entry, "status": "failed", "message": str(exc)[:300]}


async def run_all(job=None, *, trigger: str = "manual") -> dict:
    """対象を 1 つずつ更新し、結果を保存して返す。"""
    from app.features.registry import invalidate_feature_cache

    state = get_state()
    state.update({"started_at": time.time(), "trigger": trigger, "finished_at": None, "results": []})
    _write(_state_path(), state)
    items = await asyncio.to_thread(targets)
    results = []
    for index, item in enumerate(items):
        if job:
            job.set_progress(f"{item.get('name') or item['id']} を確認・更新中", index, len(items))
        results.append(await _update_one(item, job))
        state["results"] = results
        _write(_state_path(), state)
    invalidate_feature_cache()
    state["finished_at"] = time.time()
    _write(_state_path(), state)
    if job:
        job.set_progress("完了", len(items), len(items))
    await _report(results, trigger)
    return {"results": results}


async def _report(results: list[dict], trigger: str) -> None:
    updated = [r for r in results if r["status"] == "updated"]
    failed = [r for r in results if r["status"] == "failed"]
    if not updated and not failed:
        return
    from app.maintenance.auto_update import notify

    lines = [f"{r['name']}: {r['before'] or '?'} → {r['after']}" for r in updated]
    lines += [f"{r['name']}: 失敗（{r['message']}）" for r in failed]
    title = "オプション機能を更新しました" if not failed else "オプション機能の更新で失敗がありました"
    if trigger == "schedule":
        title = "（自動）" + title
    await notify(title, "\n".join(lines))


def start_job(owner_user_id: int | None = None, *, trigger: str = "manual"):
    from app.jobs import service as jobs

    async def run(job: jobs.Job) -> dict:
        return await run_all(job, trigger=trigger)

    return jobs.create(JOB_KIND, "オプション機能の一括更新", run, owner_user_id=owner_user_id)


async def tick(now: datetime) -> bool:
    """1 回分の判断。自動の更新を始めたら True。"""
    settings = get_settings()
    state = get_state()
    if not due(settings, state, now) or running():
        return False
    from app.maintenance import self_update
    from app.maintenance.auto_update import busy_reasons

    reasons = await asyncio.to_thread(busy_reasons)
    if reasons:
        logger.info("オプション機能の自動更新を待っています: %s", "、".join(reasons))
        return False
    # 本体の更新が予定・実行中なら重ねない（本体の再起動でこちらの Job が途中で切れる）
    try:
        phase = str(((await asyncio.to_thread(self_update._saved_state)) or {}).get("phase") or "")
    except Exception:  # noqa: BLE001
        phase = ""
    if phase in ("scheduled", "running"):
        return False
    state["scheduled_date"] = now.strftime("%Y-%m-%d")
    _write(_state_path(), state)
    start_job(trigger="schedule")
    return True


async def feature_auto_update_loop() -> None:
    from app.config import isolated

    if isolated():
        return
    while True:
        await asyncio.sleep(60)
        try:
            await tick(datetime.now())
        except Exception:  # noqa: BLE001 - 判断の失敗で service を止めない
            logger.exception("オプション機能の自動更新の判断に失敗しました")
