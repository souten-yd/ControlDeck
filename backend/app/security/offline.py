"""秘密の会話（一方通行）の外部への調査を、通信の直前で止める 1 か所。

利用者の決定（2026-11-21）: 会話ごとに「この会話は秘密」を押せる。途中から押してもよいが、押した会話の文脈には秘密が入って
いるので **解除はできない**。秘密の会話の中では、外部への調査（Web・ページ・学術・特許・市場・GitHub）と外部の LLM を使わない。
「AI に利用を制限させるのではなく、根本的につながらないようにする」ので、守りの本体は **通信する直前の関数がここを呼ぶこと**:

- web_collector/egress.resolve_public（HTTP 取得・ブラウザの中継・ページ取得の全部の出口）
- workflows/nodes.node_web_search（SearXNG・DuckDuckGo）
- workflows/external_search._client（学術・特許・市場の外部 API の全部）
- workflows/github_research.inspect_repository

止めるのは `confidential_scope()` の中だけ（contextvar）。スコープの入口は会話の実行（chat_persist._run_chat_job）と、
会話を指定した検索（chat_router.chat_search）。特許の公報の取得・Workflow など、スコープの外は止めない。
AI に道具を見せない・外部のモデルをローカルへ替えるのは補助。会話ごとに印を 1 つ見るだけで、文の中身の判定はしない。
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

_confidential: contextvars.ContextVar[bool] = contextvars.ContextVar("confidential_conversation", default=False)

NOTICE = "外部への調査は止めています（秘密の会話）"
NO_LOCAL_MODEL = "ローカルのモデルが使えないため答えられません（秘密の会話は外部のモデルへ送りません）"
OPENCODE_REFUSAL = "秘密の会話の OpenCode は次の版で対応します"


class ExternalResearchBlocked(Exception):
    """秘密の会話なので、外部へ接続しなかった。"""


def in_confidential_scope() -> bool:
    return _confidential.get()


# 呼び出し側の読みやすさのための別名（「外部の調査を止めるか」）
external_research_blocked = in_confidential_scope


@contextmanager
def confidential_scope(active: bool = True) -> Iterator[None]:
    """この中で動く外部への通信を、通信の直前で止める。active=False なら何もしない。"""
    if not active:
        yield
        return
    token = _confidential.set(True)
    try:
        yield
    finally:
        _confidential.reset(token)


def ensure_external_allowed(what: str) -> None:
    """通信する直前に呼ぶ。秘密の会話のスコープの中なら ExternalResearchBlocked。"""
    if _confidential.get():
        raise ExternalResearchBlocked(f"{NOTICE}: {what}")


class LocalModelUnavailable(Exception):
    pass


def local_target(base_url: str, model: str) -> tuple[str, str]:
    """秘密の会話で使うモデルの接続先。ローカル（この機械のループバック）ならそのまま、外部（cloud: ・外部の endpoint）なら
    ローカルの既定のモデルへ替える。ローカルのモデルが無ければ LocalModelUnavailable（外部へは流さない）。"""
    from urllib.parse import urlsplit

    from fastapi import HTTPException

    from app.models_mgmt import gateway
    from app.models_mgmt.runtime_provider import resolve_target

    def is_local(url: str, name: str) -> bool:
        if str(name or "").startswith("cloud:"):
            return False
        host = (urlsplit(url).hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "::1") and not gateway.is_gateway_url(url)

    try:
        resolved = resolve_target(base_url, model)
        if is_local(*resolved):
            return resolved
    except HTTPException:
        pass   # 登録の無い名前など。既定のローカルへ
    try:
        resolved = resolve_target(gateway.base_url(), gateway.AUTO_MODEL)
    except HTTPException as exc:
        raise LocalModelUnavailable(NO_LOCAL_MODEL) from exc
    if not is_local(*resolved):
        raise LocalModelUnavailable(NO_LOCAL_MODEL)
    return resolved


def confidential_request_kwargs() -> dict:
    """RuntimeChatRequest へ渡す、機密の印（ゲートウェイの `x-control-deck-confidential`）。
    request 側に confidential の欄があるときだけ付ける（欄を足すのは gateway の変更）。"""
    from app.models_mgmt.runtime_provider import RuntimeChatRequest

    if in_confidential_scope() and "confidential" in RuntimeChatRequest.__dataclass_fields__:
        return {"confidential": True}
    return {}
