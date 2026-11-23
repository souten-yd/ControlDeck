"""ニュースが使う LLM（まとめ・一言要約）の選択と呼び出し。

- 選べるのは、ローカル（"auto"・登録済みのモデル）と、クラウド（"cloud:<プロバイダ>/<モデル>"。Models で選んだもの）。
  クラウドは **無料のモデル（:free で終わる・価格 0）を上に出して「無料」と印を付け**、有料は選べても「有料」と警告する。
- 外部への送信は Models の「外部への送信」で「ニュース（公開情報だけ）」を許可したときだけ（呼び出し元は "news"）。
  クラウドを選んでいて許可が無いときは、**ローカルへ黙って切り替えず**、LlmNotAllowed で呼び出し側が「作らない」と記録する。
- 送るのは記事の題・抜粋・出どころ（公開情報）と、まとめのときだけ利用者が設定に書いた「まとめの重点」の文（設定の説明にも書く）。英語の抜粋の訳も同じ公開情報。チャットの題・好み・出来事（ControlDeck の中の出来事）は渡さない（呼び出し側の約束。digest.py）。
- 呼び出しは既存の経路: ローカルは実インスタンスへ直結（呼ばれたらロードする方式・GPU の取り合いは既存の決まりのまま）、
  クラウドはゲートウェイ（送ってよいか・月の上限・データの扱いはそこで判定）。
"""
from __future__ import annotations

import asyncio
import logging

from app.models_mgmt import cloud

logger = logging.getLogger("control_deck.news.llm")

CALLER = "news"
LOCAL_DEFAULT = "auto"
TIMEOUT_SECONDS = 300


class LlmNotAllowed(RuntimeError):
    """クラウドのモデルを選んでいるが、外部への送信が許可されていない（または使うモデルに選ばれていない）。"""


class LlmUnavailable(RuntimeError):
    """LLM が使えない・失敗した（呼び出し側は見出しだけのまとめに落とす）。"""


def is_free(model_id: str, free_ids: set[str] | frozenset[str] = frozenset()) -> bool:
    """無料のモデルか。OpenRouter は無料のモデルの id が :free で終わる。価格 0 と分かっているもの（free_ids）も無料。"""
    return model_id.endswith(":free") or model_id in free_ids


def options(local_models: list[dict], free_ids: set[str] | frozenset[str] = frozenset()) -> dict:
    """設定の選択肢。local_models は [{"id", "name"}]。cloud は無料が先。
    egress=False のときも一覧は出す（許可が無い案内を画面に出すため）。"""
    rows = []
    for item in cloud.enabled_models():
        model = str(item["id"])
        provider_model = model[len(cloud.PREFIX):].partition("/")[2]
        free = is_free(provider_model, free_ids)
        rows.append({"id": model, "name": str(item["name"]), "provider": item["provider"], "free": free, "paid": not free})
    rows.sort(key=lambda r: (not r["free"], r["name"].lower()))
    return {"local": local_models, "cloud": rows, "egress": cloud.egress_allowed(CALLER), "caller_label": cloud.CALLER_LABELS[CALLER]}


def check_allowed(model: str) -> None:
    """クラウドのモデルなら、いま送ってよいか。だめなら LlmNotAllowed（理由つき）。ローカルは常に通す。"""
    if not cloud.is_cloud_model(model):
        return
    if not cloud.egress_allowed(CALLER):
        raise LlmNotAllowed("外部への送信が許可されていません（Models → フロンティアモデル → 外部への送信で「ニュース（公開情報だけ）」を許可してください）")
    if not cloud.usable(model, caller=CALLER):
        raise LlmNotAllowed("このクラウドのモデルは、いまは使えません（Models でプロバイダを有効にし、使うモデルに選んでください）")


async def complete(model: str, messages: list[dict], *, max_tokens: int = 2048, temperature: float = 0.3) -> str:
    """1 回の生成。許可が無ければ LlmNotAllowed、それ以外の失敗は LlmUnavailable。"""
    from app.models_mgmt import gateway
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, RuntimeProviderError, provider_for_request

    await asyncio.to_thread(check_allowed, model)
    request = RuntimeChatRequest(base_url=gateway.base_url(), model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
                                 disable_thinking=True, timeout_seconds=TIMEOUT_SECONDS, client=CALLER)
    try:
        provider = provider_for_request(request)
        if not cloud.is_cloud_model(model):
            from app.models_mgmt import llama
            from app.models_mgmt.runtime_policy import ensure_gpu_profile

            await llama.ensure_ready_by_base_url(request.base_url)
            await asyncio.to_thread(ensure_gpu_profile, base_url=request.base_url)
        text = await asyncio.wait_for(provider.complete(request), TIMEOUT_SECONDS + 30)
    except (RuntimeProviderError, RuntimeError, OSError, asyncio.TimeoutError) as exc:
        raise LlmUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}".rstrip(": ")) from exc
    except Exception as exc:  # noqa: BLE001 - ゲートウェイの HTTPException（モデルが解けない等）も含め、まとめは見出しだけに落とす
        raise LlmUnavailable(f"{type(exc).__name__}: {str(getattr(exc, 'detail', exc))[:200]}") from exc
    if not (text or "").strip():
        raise LlmUnavailable("LLM の答えが空でした")
    return text
