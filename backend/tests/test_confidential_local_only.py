"""発明の内容を外部の LLM へ出さない守り（ゲートウェイの層・Patent の流れ・埋め込みの宛先）。

目的:
- 「機密」の印（ヘッダ x-control-deck-confidential）がある要求は、呼び出し元の許可（egress）があっても
  cloud: へ出ない（403）。ローカルは通り、印が無ければ従来どおり。
- Patent の流れ（案件の AI 支援・クレームチャート・調査の検索語・文献の分析・図の説明）の LLM 呼び出しは
  すべて local_only=True（= 機密扱い）で、cloud: を選べない。呼び出しを列挙して固定する。
- 埋め込みの宛先はループバックだけ（資料の本文を外部 URL へ送らない）。

根拠: 2026-11-21 利用者「知財の、私の方から出したアイデアは、ローカル LLM 以外の LLM へのアクセスを拒否する形に。
Patent の特許フローに従ってやる場合は基本的にローカルでしか使えないように」。docs/PLAN-knowledge-platform.md §10.2。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest

from app.models_mgmt import cloud, gateway
from app.models_mgmt.runtime_provider import RuntimeChatRequest, RuntimeProviderError, _request_headers

APP = Path(__file__).resolve().parents[1] / "app"
BODY = {"model": "cloud:openrouter/anthropic/claude-x", "messages": [{"role": "user", "content": "x"}]}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud, "data_dir", lambda: tmp_path)
    cloud.save_provider("openrouter", {"api_key": "sk-or-v1-secretvalue1234"})
    cloud.save_provider("openrouter", {"enabled": True, "models": ["anthropic/claude-x"]})
    cloud.save_egress({"assistant": True, "opencode": True, "api": True})
    return tmp_path


def _headers(confidential: bool):
    headers = {"Authorization": f"Bearer {gateway.get_api_key(create=True)}", gateway.CLIENT_HEADER: "assistant"}
    if confidential:
        headers[gateway.CONFIDENTIAL_HEADER] = "1"
    return headers


def _mock_upstream(monkeypatch) -> list:
    sent: list = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            sent.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **dict(k, transport=Transport())))
    return sent


def test_confidential_mark_blocks_cloud_even_when_the_caller_is_allowed(client, store, monkeypatch, caplog):
    sent = _mock_upstream(monkeypatch)
    with caplog.at_level("WARNING", logger="control_deck.audit"):
        response = client.post("/api/v1/llm/v1/chat/completions", headers=_headers(True),
                               json=dict(BODY, messages=[{"role": "user", "content": "秘密の請求項の本文"}]))
    assert response.status_code == 403 and "発明の内容を含むため" in response.json()["detail"]
    assert sent == []
    assert "confidential_cloud_blocked" in caplog.text and "秘密の請求項" not in caplog.text


def test_without_the_mark_cloud_works_as_before(client, store, monkeypatch):
    sent = _mock_upstream(monkeypatch)
    response = client.post("/api/v1/llm/v1/chat/completions", headers=_headers(False), json=BODY)
    assert response.status_code == 200 and len(sent) == 1


def test_confidential_mark_does_not_block_local_models(client, store):
    """ローカルのモデルは印があっても止めない（止まるのは cloud: だけ）。モデルが無ければ 404 系で、403 にならない。"""
    response = client.post("/api/v1/llm/v1/chat/completions", headers=_headers(True),
                           json={"model": "no-such-local-model", "messages": []})
    assert response.status_code != 403 or "発明の内容" not in response.text


def test_confidential_request_adds_the_mark_only_for_the_gateway():
    base = "http://127.0.0.1:8000" + gateway.API_PATH
    for kwargs in ({"confidential": True}, {"local_only": True}):
        req = RuntimeChatRequest(base_url=base, model="m", messages=[], **kwargs)
        assert _request_headers(req)[gateway.CONFIDENTIAL_HEADER] == "1"
    plain = RuntimeChatRequest(base_url=base, model="m", messages=[])
    assert gateway.CONFIDENTIAL_HEADER not in _request_headers(plain)
    outside = RuntimeChatRequest(base_url="https://example.com/v1", model="m", messages=[], confidential=True)
    assert gateway.CONFIDENTIAL_HEADER not in _request_headers(outside)


@pytest.mark.parametrize("kwargs", [{"confidential": True}, {"local_only": True}])
def test_confidential_request_refuses_a_cloud_model_before_sending(kwargs):
    from app.models_mgmt.runtime_provider import provider_for_base_url

    base = "http://127.0.0.1:8000" + gateway.API_PATH
    req = RuntimeChatRequest(base_url=base, model="cloud:openrouter/x", messages=[], **kwargs)
    with pytest.raises(RuntimeProviderError):
        asyncio.run(provider_for_base_url(base)._prepare(req))


# Patent の流れで LLM を呼ぶ場所（画面から動くもの）。ここに無い場所で RuntimeChatRequest を作ったら、
# 下の網羅の検査が落ちる。足したら必ず local_only=True を付けてここへ入れる。
PATENT_FLOW_LLM_CALLS = {
    "patents/service.py": 1,      # 案件の AI 支援（hearing・interview・filing_check など）
    "patents/judge.py": 1,        # クレームチャート
    "patents/survey.py": 1,       # 調査の検索語の案
    "knowledge/analysis.py": 1,   # 文献の分析（案件から呼ぶ）
    "knowledge/pdf_ingest.py": 1,  # PDF の図の説明
}


def _requests(path: str) -> list[ast.Call]:
    tree = ast.parse((APP / path).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "RuntimeChatRequest"]


@pytest.mark.parametrize("path,count", sorted(PATENT_FLOW_LLM_CALLS.items()))
def test_every_patent_flow_llm_call_is_local_only(path, count):
    calls = _requests(path)
    assert len(calls) == count, f"{path}: RuntimeChatRequest の数が変わった。local_only=True を確かめて目録を直す"
    for call in calls:
        flag = next((k.value for k in call.keywords if k.arg == "local_only"), None)
        assert isinstance(flag, ast.Constant) and flag.value is True, f"{path}: local_only=True が無い"


def test_patent_package_has_no_other_llm_entrypoints():
    """patents/ の中の LLM 呼び出しは上の表にあるものだけ（新しい呼び出し口が黙って増えない）。"""
    found = {str(p.relative_to(APP)) for p in (APP / "patents").glob("*.py") if _requests(str(p.relative_to(APP)))}
    assert found == {k for k in PATENT_FLOW_LLM_CALLS if k.startswith("patents/")}


def test_embedding_destination_must_be_loopback(monkeypatch):
    from app.workflows import rag

    for url in ("http://127.0.0.1:8081/v1", "http://localhost:11434/v1", "http://[::1]:8081/v1"):
        rag.require_loopback_embed(url)
    for url in ("https://api.openai.com/v1", "http://192.168.1.5:8081/v1", "http://evil.example/v1", ""):
        with pytest.raises(rag.RagError):
            rag.require_loopback_embed(url)
    with pytest.raises(rag.RagError):
        rag.set_config("conf-test", {"embed_base_url": "https://api.openai.com/v1"})
    with pytest.raises(rag.RagError):
        rag.create_collection("conf-test2", {"embed_base_url": "https://api.openai.com/v1"})
    with pytest.raises(rag.RagError):   # 保存済みの値が外部でも送らない
        asyncio.run(rag.embed(["x"], "https://api.openai.com/v1", "m", ""))
