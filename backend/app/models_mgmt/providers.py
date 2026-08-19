"""LLMランタイムを共通形式で検出するproviderカタログ。"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

_KNOWN_LOCAL = {
    11434: ("ollama", "Ollama"),
    8080: ("llama.cpp", "llama.cpp"),
    1234: ("lm-studio", "LM Studio"),
    8000: ("openai-compatible", "OpenAI互換"),
    5001: ("openai-compatible", "OpenAI互換"),
}


def capabilities(kind: str, *, managed: bool, available: bool = True) -> list[str]:
    generation = ["chat", "stream", "cancel"] if available else []
    if managed and kind == "ollama":
        return generation + ["list", "load", "unload", "delete", "pull", "configure"]
    if managed and kind == "llama.cpp":
        return generation + ["list", "load", "unload", "delete", "configure", "health", "start", "stop"]
    return generation + ["list"]


def _openai_base(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _provider_id(kind: str, base_url: str, *, managed: bool) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or "unknown"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return kind if managed and kind in ("ollama", "llama.cpp") else f"{kind}-{host}-{port}"


async def _candidates() -> list[dict]:
    from app.models_mgmt import llama, ollama

    candidates: dict[str, dict] = {}

    def add(base_url: str, kind: str, name: str, *, managed: bool = False,
            installed: bool | None = None, experimental: bool = False) -> None:
        base = _openai_base(base_url)
        current = candidates.get(base, {})
        effective_managed = managed or current.get("managed", False)
        effective_kind = current.get("provider", kind) if current.get("managed") else kind
        candidates[base] = {
            "id": _provider_id(effective_kind, base, managed=effective_managed),
            "provider": effective_kind, "name": current.get("name", name) if current.get("managed") else name,
            "base_url": base, "managed": effective_managed,
            "installed": installed if installed is not None else current.get("installed"),
            "experimental": experimental or current.get("experimental", False),
        }

    add(ollama.base_url(), "ollama", "Ollama", managed=True)
    llama_status = llama.runtime_status()
    if llama_status.get("base_url"):
        add(str(llama_status["base_url"]), "llama.cpp", "llama.cpp", managed=True,
            installed=bool(llama_status.get("installed")), experimental=True)
    else:
        port = int(llama_status.get("port") or 8080)
        add(f"http://127.0.0.1:{port}", "llama.cpp", "llama.cpp", managed=True,
            installed=False, experimental=True)
    # エンドポイント（=port）が独立したOpenAI endpoint。1エンドポイントに複数モデルを
    # 束ねられるため、base_urlをキーにすると同居モデルが1件へ潰れる。エンドポイント単位で
    # 1候補を作り、所属aliasは後段でmodelsとして列挙する。
    # embedding/rerankerはチャット先ではないため候補に出さない。
    endpoint_members: dict[str, list[str]] = {}
    for instance in llama_status.get("instances", []):
        if str(instance.get("role", "llm")) != "llm":
            continue
        base = _openai_base(
            str(instance.get("base_url") or f"http://127.0.0.1:{instance.get('port', 8080)}")
        )
        endpoint_members.setdefault(base, []).append(str(instance.get("alias")))
    for base, aliases in endpoint_members.items():
        label = aliases[0] if len(aliases) == 1 else f"{aliases[0]} ほか{len(aliases) - 1}件"
        add(base, "llama.cpp-instance", f"llama.cpp · {label}",
            installed=bool(llama_status.get("installed")), experimental=True)
        candidates[base]["aliases"] = aliases

    for port, (kind, name) in _KNOWN_LOCAL.items():
        add(f"http://127.0.0.1:{port}", kind, name)

    try:
        from app.applications import service as apps
        from app.database import SessionLocal
        from app.models import ManagedApplication

        def managed_ports() -> set[int]:
            db = SessionLocal()
            try:
                found: set[int] = set()
                for app in db.query(ManagedApplication).all():
                    found.update(apps.runtime_info(app).listening_ports or [])
                return found
            finally:
                db.close()

        for port in await asyncio.to_thread(managed_ports):
            add(f"http://127.0.0.1:{port}", "openai-compatible", "管理アプリ")
    except Exception:
        logger.exception("failed to collect managed application ports for LLM discovery")

    return list(candidates.values())


def _gateway_candidate() -> dict | None:
    """ControlDeck ゲートウェイ自身を接続先候補にする。

    llama.cpp の各ポートを直接指す代わりにこの1アドレスへ集約すると、モデル解決・
    オンデマンド起動・KVの受け入れ制御がゲートウェイ側の規則へ一元化され、OpenCode
    などの外部クライアントと内部のチャットが同じ経路を通る。
    自ポート宛のため疎通確認はせず、登録済みLLMをそのままモデル一覧として返す。
    """
    from app.models_mgmt import gateway, llama

    models = [str(i["alias"]) for i in llama.list_instances() if str(i.get("role", "llm")) == "llm"]
    if not models:
        return None
    # 先頭は仮想モデル。どれを使うかをControlDeckに任せると、停止中の別モデルを
    # 起こさずに済む（起動中があればそれを使う）。
    models = [gateway.AUTO_MODEL, *models]
    return {
        "id": "control-deck-gateway", "provider": "control-deck-gateway",
        "name": "ControlDeck ゲートウェイ", "base_url": gateway.base_url(),
        "managed": True, "installed": True, "experimental": False,
        "available": True, "models": models, "gateway": True,
        "capabilities": capabilities("control-deck-gateway", managed=True),
    }


def _selected_runtime() -> str:
    from app.models_mgmt.runtime_policy import get_policy

    try:
        return str(get_policy().selected_runtime)
    except Exception:
        return "ollama"


async def list_providers(*, include_unavailable: bool = True, exclude_port: int | None = None,
                         include_gateway: bool = False) -> list[dict]:
    """候補の `/v1/models` を並列確認し、共通provider形式で返す。

    ⚙️で選択中のruntimeには selected=true を付け、チャット等の既定接続先が
    ランタイム選択に追従できるようにする。選択中のmanaged providerは停止中でも
    一覧へ残す（llama.cppは生成時にオンデマンド起動されるため選択肢として有効）。
    接続先を選ぶ用途では include_gateway=True を渡す。llama.cpp運用時は個別ポートを
    ゲートウェイへ集約し、全クライアントの接続先を1つの管理アドレスへ揃える。
    """
    candidates = await _candidates()
    selected_runtime = _selected_runtime()
    # llama.cpp運用時の既定接続先はゲートウェイ。個別ポートも選べるよう一覧には残す。
    # モデル管理画面のようにランタイム自体を扱う用途では出さない（保有モデルはllama.cpp側）。
    gateway_item = (_gateway_candidate()
                    if include_gateway and selected_runtime == "llama.cpp" else None)
    for item in candidates:
        item["selected"] = (
            gateway_item is None
            and bool(item.get("managed"))
            and item.get("provider") == selected_runtime
        )

    async def probe(item: dict) -> dict | None:
        parsed = urlsplit(item["base_url"])
        if exclude_port and parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port == exclude_port:
            return None
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                response = await client.get(item["base_url"] + "/models")
            if response.status_code != 200:
                raise httpx.HTTPStatusError("unexpected status", request=response.request, response=response)
            payload = response.json()
            models = [m.get("id", "") for m in payload.get("data", []) if isinstance(m, dict)]
            return {**item, "available": True, "models": [m for m in models if m][:50],
                    "capabilities": capabilities(item["provider"], managed=item["managed"])}
        except (httpx.HTTPError, ValueError, TypeError):
            if item.get("managed") and (include_unavailable or item.get("selected")):
                models: list[str] = []
                if item.get("provider") == "llama.cpp":
                    # 停止中でも登録済みaliasを提示する（--alias がモデルIDになる）
                    from app.models_mgmt import llama

                    models = [str(inst["alias"]) for inst in llama.list_instances()]
                # エンドポイント候補は、そこに束ねた全モデルを選択肢として出す。
                models = item.get("aliases") or models
                return {**item, "available": False, "models": models,
                        "capabilities": capabilities(item["provider"], managed=item["managed"], available=False)}
            return None

    results = await asyncio.gather(*(probe(item) for item in candidates))
    found = [item for item in results if item is not None]
    if gateway_item is not None:
        # llama.cpp の個別ポートはゲートウェイへ集約する。同じモデルが接続先違いで
        # 二重に並ぶと、どちらを選んだかでモデル解決も受け入れ制御も変わってしまう。
        found = [item for item in found
                 if not str(item.get("provider") or "").startswith("llama.cpp")]
        gateway_item["selected"] = True
        found.append(gateway_item)
    return sorted(found,
                  key=lambda x: (not x.get("selected"), not x["managed"], x["name"], x["base_url"]))
