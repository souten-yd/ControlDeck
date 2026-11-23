"""Home の通知の 1 行（/home-notices）の順位・解決で消える・閉じる。

目的:
- 未解決の元（監視の警報・更新の失敗）を、重要度（エラー > 警告 > 注意 > 情報）→ 新しさの順に返す。
- 元が解決したら（警報の解除・次の更新の成功）一覧から消え、次に重要なものが先頭に来る。
- 「閉じる」は本人の画面だけ。閉じた件は出ず、元が解決した後は印も掃除される。
- 何も無ければ空（画面は行ごと出さない）。外へは出ない（承認・アプリ・更新の状態はモック）。
根拠: 2026-11-23 利用者「ホーム画面に最終的な通知の内容…重要度でインフォメーション、アテンション、ワーニング、エラー…解決されたら消して次に優先度の高い内容を表示」。
"""
from __future__ import annotations

import time

import pytest

from app.alerts import home_notice
from app.database import SessionLocal
from app.maintenance import approvals, self_update
from app.models import AlertEvent, AlertRule, utcnow
from tests.conftest import CSRF_HEADERS

URL = "/api/v1/home-notices"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(home_notice, "_path", lambda: tmp_path / "dismissed.json")
    monkeypatch.setattr(approvals, "list_all", lambda **kw: [])
    monkeypatch.setattr(self_update, "_saved_state", lambda: {})
    with SessionLocal() as db:
        db.query(AlertEvent).delete()
        db.query(AlertRule).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(AlertEvent).delete()
        db.query(AlertRule).delete()
        db.commit()


def _alert(metric: str, name: str) -> int:
    with SessionLocal() as db:
        rule = AlertRule(name=name, metric=metric)
        db.add(rule)
        db.commit()
        event = AlertEvent(rule_id=rule.id, rule_name=name, message=f"{name} の値が高い", status="active", triggered_at=utcnow())
        db.add(event)
        db.commit()
        return event.id


def _resolve(event_id: int) -> None:
    with SessionLocal() as db:
        db.get(AlertEvent, event_id).status = "resolved"
        db.commit()


def test_empty_when_nothing(admin_client):
    assert admin_client.get(URL).json() == {"items": [], "total": 0}


def test_ranked_by_severity_then_resolves_to_next(admin_client, monkeypatch):
    warn = _alert("cpu_percent", "CPU 高負荷")
    err = _alert("app_down", "アプリ停止")
    monkeypatch.setattr(approvals, "list_all", lambda **kw: [
        {"id": "a1", "status": "pending", "title": "PR を承認", "created_at": time.time()}])
    body = admin_client.get(URL).json()
    assert [i["level"] for i in body["items"]] == ["error", "warning", "attention"]
    assert body["items"][0]["title"] == "アプリ停止" and body["total"] == 3
    _resolve(err)
    assert admin_client.get(URL).json()["items"][0]["title"] == "CPU 高負荷"
    _resolve(warn)
    assert admin_client.get(URL).json()["items"][0]["level"] == "attention"


def test_same_level_newer_first(admin_client):
    _alert("cpu_percent", "古い")
    time.sleep(0.01)
    _alert("gpu_percent", "新しい")
    assert [i["title"] for i in admin_client.get(URL).json()["items"]] == ["新しい", "古い"]


def test_update_failure_shown_and_cleared_by_next_success(admin_client, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(self_update, "_saved_state", lambda: {"phase": "failed", "error": "起動しませんでした", "finished_at": now})
    first = admin_client.get(URL).json()["items"][0]
    assert first["level"] == "error" and "更新" in first["title"]
    monkeypatch.setattr(self_update, "_saved_state", lambda: {"phase": "succeeded", "finished_at": now + 1})
    assert admin_client.get(URL).json()["total"] == 0


def test_dismiss_hides_and_cleans_after_resolution(admin_client):
    first = _alert("cpu_percent", "CPU")
    second = _alert("app_down", "停止")
    items = admin_client.get(URL).json()["items"]
    assert admin_client.post(URL + "/dismiss", json={"key": items[0]["key"]}, headers=CSRF_HEADERS).status_code == 200
    after = admin_client.get(URL).json()
    assert [i["title"] for i in after["items"]] == ["CPU"]
    _resolve(second)
    admin_client.get(URL)
    import json

    saved = [k for v in json.loads(home_notice._path().read_text()).values() for k in v]
    assert items[0]["key"] not in saved  # 解決した元の「閉じた」印は掃除される
    _resolve(first)
    assert admin_client.get(URL).json()["total"] == 0
