"""開発の作業場（箱）の中から、画面の見た目の検査が中継を通って流れること。

目的: 箱の中では本番のゲートウェイへ届かないので、箱の環境の OPENAI_BASE_URL（中継）を使う。ローカル LLM を借りない
作業場（中継が無い）では、不合格にせず未実施にする。明示の CONTROL_DECK_GATEWAY_URL があればそれを優先する。
根拠: 2026-11-22、利用者として作業場から＋メニューの作り直しを頼んだ通しで、関門が「画面の見た目: ゲートウェイへ
届きません（Connection refused）」で不合格になり、画面を変えた作業を取り込めなかった。
"""
from app.maintenance import visual_review as vr


def test_box_uses_the_relay(monkeypatch):
    monkeypatch.delenv(vr.GATEWAY_ENV, raising=False)
    monkeypatch.setenv("CONTROL_DECK_SANDBOX", "ws-1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:18903/api/v1/llm/v1/")
    assert vr._gateway() == "http://127.0.0.1:18903/api/v1/llm/v1"


def test_explicit_gateway_wins_and_host_ignores_openai_base_url(monkeypatch):
    monkeypatch.setenv(vr.GATEWAY_ENV, "http://127.0.0.1:9999/api/v1/llm/v1")
    monkeypatch.setenv("CONTROL_DECK_SANDBOX", "ws-1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:18903/api/v1/llm/v1")
    assert vr._gateway().endswith(":9999/api/v1/llm/v1")
    monkeypatch.delenv(vr.GATEWAY_ENV)
    monkeypatch.delenv("CONTROL_DECK_SANDBOX")
    assert vr._gateway() == vr.DEFAULT_GATEWAY   # ホストでは OPENAI_BASE_URL（他の用途）を使わない


def test_box_without_the_llm_relay_is_skipped_not_failed(monkeypatch, tmp_path):
    monkeypatch.delenv(vr.GATEWAY_ENV, raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("CONTROL_DECK_SANDBOX", "ws-1")
    result = vr.review([tmp_path / "a.png"])
    assert result.status == "skipped" and "借りない" in result.message
