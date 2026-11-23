"""通知の入口（種類ごとの有効・無効と、お休みモード）。

2026-11-23 利用者の依頼。通知は「Project の実行の終わり」「監視の警報」「更新」の 3 か所だけで、AI アシスタントの
コード開発・調査の完了は通知していなかった。また夜は通知を出さないようにしたい。

どの場所から出す通知も、必ず `notify_event` を通す。この入口が次の順に判断し、送ってよい人だけを `webpush` へ渡す。
1. 利用者ごとの「その種類を受け取る」の設定（既定は KINDS の default）。
2. お休みモード（既定は有効・22:00〜07:00・サーバーの地域の時刻）。お休み中は送らず捨てる。
   監視の警報のうち緊急のもの（urgent=True）は、利用者が「お休み中も送る」を選んだときだけ送る（既定は送らない。2026-11-23 利用者「お休み中の通知は届かないようにしてほしい」）。
3. 送ってよければ `webpush.send_in_background`（待つ呼び出しは send_to）へ渡す。

設定の置き場は利用者ごとの JSON（data_dir/push/notify-prefs.json）。DB の表は足さない（移行を要さず、
更新の設定 updates/auto.json と同じ形）。上書きした値だけを持ち、既定は KINDS から読む。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from app.config import data_dir

logger = logging.getLogger("control_deck.alerts.events")


@dataclass(frozen=True)
class Kind:
    id: str
    group: str
    label: str
    note: str
    default: bool = True


GROUPS = (
    ("assistant", "AI アシスタント"),
    ("workspace", "作業場・Project"),
    ("media", "生成"),
    ("system", "システム"),
)

# 実際に検出できる場所がある種類だけを置く。検出の場所は docs/design-notification-kinds.md の表にもある。
KINDS: tuple[Kind, ...] = (
    Kind("code_done", "assistant", "コード開発の完了", "AI アシスタント（OpenCode）の回が終わったとき"),
    Kind("code_question", "assistant", "質問待ち", "OpenCode が質問の答えを待っているとき、またはプランに決めてもらいたいことがあるとき"),
    Kind("code_permission", "assistant", "権限の確認待ち", "OpenCode が許可を求めて止まったとき"),
    Kind("code_failed", "assistant", "コード開発の失敗", "OpenCode の回が失敗して止まったとき"),
    Kind("code_interrupted", "assistant", "中断", "停電・再起動で実行が中断されたとき"),
    Kind("research_done", "assistant", "調査の完了", "Web・学術・Deep・複合の調査の回答ができたとき（失敗も）"),
    Kind("chat_done", "assistant", "ふつうの会話の答え", "60 秒以上かかった長い生成が終わったとき（数が多いので既定は無効）", False),
    Kind("workspace_gate", "workspace", "関門の結果", "作業場の関門（dev-check）が合格・不合格で終わったとき"),
    Kind("workspace_adopted", "workspace", "取り込み・マージ", "作業場を取り込んだ・マージしたとき"),
    Kind("workspace_disk", "workspace", "ディスクの空きが少ない", "空きが少なくて作業場を保存できなかったとき"),
    Kind("project_run", "workspace", "Project の実行の終わり", "Project Lab の実行が終わったとき（起動した人へ）"),
    Kind("media_done", "media", "画像・音楽・3D の生成", "メディアの生成・編集が完了・失敗したとき"),
    Kind("model_error", "system", "モデルの読み込みの失敗", "モデルを起動できなかったとき（管理者）"),
    Kind("update", "system", "更新・承認待ち", "本番の更新の完了・失敗、承認待ち（通知チャンネル「この ControlDeck の通知」を選んだとき）"),
    Kind("alert", "system", "監視の警報", "アラートルールが発火したとき（通知チャンネル「この ControlDeck の通知」を選んだとき）"),
    Kind("patent_watch", "system", "特許のウォッチ・期限", "特許の期限が近いとき（件数だけ。案件名は載せない）"),
)
KIND_BY_ID = {kind.id: kind for kind in KINDS}

QUIET_DEFAULT = {"enabled": True, "start": "22:00", "end": "07:00", "urgent_alerts": False}

_lock = threading.Lock()


def local_now() -> datetime:
    """お休みの判断に使う、サーバーの地域の時刻（試験で差し替える）。"""
    return datetime.now()


def _path():
    return data_dir() / "push" / "notify-prefs.json"


def _read_all() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def parse_clock(value: object) -> int | None:
    """「HH:MM」を 0 時からの分にする。形が違えば None。"""
    text = str(value or "")
    if len(text) != 5 or text[2] != ":" or not (text[:2] + text[3:]).isdigit():
        return None
    hour, minute = int(text[:2]), int(text[3:])
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def get_prefs(user_id: int) -> dict:
    """{"kinds": {種類: 有効か}, "quiet": {...}}。上書きが無い分は既定。"""
    saved = _read_all().get(str(user_id))
    saved = saved if isinstance(saved, dict) else {}
    overrides = saved.get("kinds") if isinstance(saved.get("kinds"), dict) else {}
    kinds = {kind.id: bool(overrides.get(kind.id, kind.default)) for kind in KINDS}
    quiet = dict(QUIET_DEFAULT)
    stored = saved.get("quiet") if isinstance(saved.get("quiet"), dict) else {}
    for key, default in QUIET_DEFAULT.items():
        if key in stored and type(stored[key]) is type(default):
            quiet[key] = stored[key]
    if parse_clock(quiet["start"]) is None or parse_clock(quiet["end"]) is None:
        quiet["start"], quiet["end"] = QUIET_DEFAULT["start"], QUIET_DEFAULT["end"]
    return {"kinds": kinds, "quiet": quiet}


def save_prefs(user_id: int, *, kinds: dict[str, bool] | None = None, quiet: dict | None = None) -> dict:
    """その人の設定だけを書く（他の人の行には触れない）。知らない種類・形の違う値は呼び出し側（API）で断る。"""
    with _lock:
        everything = _read_all()
        mine = everything.get(str(user_id)) if isinstance(everything.get(str(user_id)), dict) else {}
        if kinds:
            mine["kinds"] = {**(mine.get("kinds") or {}), **{key: bool(value) for key, value in kinds.items() if key in KIND_BY_ID}}
        if quiet:
            mine["quiet"] = {**(mine.get("quiet") or {}), **{key: quiet[key] for key in QUIET_DEFAULT if key in quiet}}
        everything[str(user_id)] = mine
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(everything, handle)
        os.replace(tmp, path)
    return get_prefs(user_id)


def quiet_state(quiet: dict, now: datetime | None = None) -> dict:
    """お休み中か。{"active": bool, "until": "07:00" | None}。開始の時刻は含み、終わりの時刻は含まない。
    開始 > 終了は日をまたぐ範囲（22:00〜07:00）。"""
    if not quiet.get("enabled"):
        return {"active": False, "until": None}
    start, end = parse_clock(quiet["start"]), parse_clock(quiet["end"])
    current = now or local_now()
    minute = current.hour * 60 + current.minute
    if start is None or end is None or start == end:
        return {"active": False, "until": None}
    active = start <= minute < end if start < end else (minute >= start or minute < end)
    return {"active": active, "until": quiet["end"] if active else None}


def settings_view(user_id: int) -> dict:
    """設定の画面へ返す形（種類の一覧・既定・今の状態）。"""
    prefs = get_prefs(user_id)
    return {
        "groups": [{"id": gid, "label": label} for gid, label in GROUPS],
        "kinds": [{"id": k.id, "group": k.group, "label": k.label, "note": k.note, "default": k.default,
                   "enabled": prefs["kinds"][k.id]} for k in KINDS],
        "quiet": prefs["quiet"],
        "quiet_now": quiet_state(prefs["quiet"]),
    }


def _users_with_permission(permission: str) -> list[int]:
    from app.database import SessionLocal
    from app.models import PushSubscription, User
    from app.security.deps import user_permissions

    with SessionLocal() as db:
        ids = {row[0] for row in db.query(PushSubscription.user_id).distinct().all()}
        users = db.query(User).filter(User.id.in_(ids), User.is_active.is_(True)).all() if ids else []
        return [user.id for user in users if permission in user_permissions(user)]


def _record_news(kind: str, *, title: str, body: str, url: str, user_ids: list[int] | None, permission: str | None) -> None:
    """ニュースの専門ページ（app/news）の「出来事」へ残す。種類の有効・お休みに関係なく、宛先の人ごとに記録する。
    title・body は呼び出し側が伏せた後の文なので、そのまま入れてよい。失敗しても通知を止めない。"""
    try:
        from app.news.store import record_event

        record_event(kind, title=title, body=body, url=url, user_ids=user_ids, permission=permission)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ニュースの出来事を記録できません (%s): %s", kind, exc)


_last_sent: dict[tuple[str, str], float] = {}


def notify_event(kind: str, *, title: str, body: str = "", url: str = "/", tag: str | None = None,
                 user_ids: list[int] | None = None, permission: str | None = None,
                 urgent: bool = False, cooldown: float = 0, wait: bool = False) -> dict:
    """通知の入口。送った人数（users）を返す。wait=True なら送り終わるまで待ち、webpush.send_to の結果も混ぜる
    （通知チャンネルが「届いたか」を知るため）。通知の失敗で本来の処理を止めない。

    urgent は監視の警報のうち緊急のもの。お休み中でも、利用者が許していれば送る。
    cooldown は同じ（種類・tag）をこの秒数のあいだ 1 回だけにする（同じ失敗の連発を抑える）。"""
    if kind not in KIND_BY_ID:
        raise ValueError(f"知らない通知の種類です: {kind}")
    none = {"users": 0}
    try:
        if cooldown and tag:
            now = time.monotonic()
            if now - _last_sent.get((kind, tag), -1e12) < cooldown:
                return none
            _last_sent[(kind, tag)] = now
        _record_news(kind, title=title, body=body, url=url, user_ids=user_ids, permission=permission)
        candidates = user_ids if user_ids is not None else _users_with_permission(permission or "")
        now_local = local_now()
        allowed = []
        for uid in candidates:
            prefs = get_prefs(uid)
            if not prefs["kinds"][kind]:
                continue
            if quiet_state(prefs["quiet"], now_local)["active"] and not (urgent and prefs["quiet"]["urgent_alerts"]):
                continue
            allowed.append(uid)
        if not allowed:
            return none
        from app.alerts import webpush

        send = dict(title=title, body=body, url=url, tag=tag, user_ids=allowed, permission=permission)
        if wait:
            return {"users": len(allowed), **webpush.send_to(**send)}
        webpush.send_in_background(**send)
        return {"users": len(allowed)}
    except Exception:  # noqa: BLE001 - 通知の失敗で本来の処理を止めない
        logger.exception("通知の入口で失敗しました (%s)", kind)
        return none
