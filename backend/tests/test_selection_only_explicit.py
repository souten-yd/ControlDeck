"""利用者の既定の選択（selected_runtime）は、要求による載せ替えでは変わらない。

目的: ゲートウェイ・載せ替えの調停役・役割ごとのモデルの要求で別のランタイムが起きても、選択はそのまま。
利用者が画面でロード（各ランタイムの start）したときだけ、選択がそのランタイムへ移る。60 秒ごとの合わせ込みも回さない。
根拠: 2026-11-22 本番。役割で Maya-S を呼ぶたびに選択が Maya-S へ移り、既定（auto）の要求が Maya-S と Strata の間を
行き来した（90 分で 5 回書き換わった）。利用者「モデルを一旦 FlashNext に切り替えるがまた Maya-XS の DS に戻ってる」。
"""
import asyncio
import inspect

from app.models_mgmt import large_model_lifecycle, local_llm


def _recorder(monkeypatch):
    saved = []

    class P:
        coexistence = "exclusive"
        selected_runtime = "strata"

        def model_copy(self, update):
            saved.append(update)
            return self

    import app.models_mgmt.runtime_policy as rp
    monkeypatch.setattr(rp, "get_policy", lambda: P())
    monkeypatch.setattr(rp, "save_policy", lambda p: p)
    return saved


def test_on_demand_start_does_not_change_the_selection(monkeypatch):
    saved = _recorder(monkeypatch)

    class Module:
        @staticmethod
        async def ensure_ready(alias, timeout_seconds=240):
            # 起動の中で選択を合わせようとしても、要求による起動の区間では何もしない（to_thread でも文脈は引き継ぐ）
            await asyncio.to_thread(large_model_lifecycle.follow_selection, "maya-s")
            return True

    monkeypatch.setattr(local_llm, "find", lambda alias: {"alias": alias, "runtime": "maya-s", "loaded": True})
    monkeypatch.setattr(local_llm, "module_for", lambda runtime: Module)
    assert asyncio.run(local_llm.ensure_ready("maya-deepseek-xs")) is True
    assert saved == [], "要求による起動で選択が変わった"


def test_explicit_start_still_follows(monkeypatch):
    saved = _recorder(monkeypatch)
    large_model_lifecycle.follow_selection("maya-s")      # 画面のロード（要求による起動の区間の外）
    assert saved == [{"selected_runtime": "maya-s"}]


def test_idle_loop_no_longer_reconciles_the_selection():
    source = inspect.getsource(large_model_lifecycle.idle_unload_loop)
    assert "to_thread(reconcile_selection)" not in source
