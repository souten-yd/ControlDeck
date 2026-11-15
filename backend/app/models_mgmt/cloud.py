"""外部のフロンティアモデル（API プロバイダ）。計画は docs/PLAN-frontier-models.md。

ControlDeck の LLM ゲートウェイに `cloud:<プロバイダ>/<モデル>` のモデルとして並べ、ローカルの
モデルと同じ入口（OpenAI 互換）で使えるようにする。各プロバイダの OpenAI 互換 API に揃えるので、
転送の実装は 1 つで済む。

外部へ送ることが既定ではない。守ること:
- 呼び出し元（OpenCode・AI アシスタント・Workflow・ゲートウェイを直接使う外部クライアント）ごとに、
  外部モデルを使ってよいかを利用者が選ぶ。既定はすべて不可。
- Add-on の local_only の経路（app.models_mgmt.ai_gateway）はここを通らない。
- API キーは ControlDeck の秘密鍵で暗号化して保存し、画面へは末尾 4 文字しか返さない。
- 呼び出しはすべて記録する（日時・呼び出し元・プロバイダ・モデル・トークン数・費用）。本文は記録しない。
- プロバイダごとの月の上限額を超えたら止める。
- プランの認証情報（ChatGPT・Claude のログイン）はここでは扱わない。公式 CLI の中だけで使う
  （エージェント CLI、計画の F3）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import data_dir

PREFIX = "cloud:"
CALLERS = ("opencode", "assistant", "workflows", "api")
CALLER_LABELS = {"opencode": "OpenCode", "assistant": "AI アシスタント", "workflows": "Workflow",
                 "api": "ゲートウェイを直接使う外部クライアント"}

# OpenAI 互換の API。base_url は固定（任意の送信先へキーを渡さない）。custom だけ利用者が指定する。
PROVIDERS: dict[str, dict[str, Any]] = {
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "keys_url": "https://openrouter.ai/settings/keys",
                   "note": "多数のモデル（Claude・GPT・Gemini・オープンモデル）を 1 つのキーで。無料のモデルもある"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1",
               "keys_url": "https://platform.openai.com/api-keys",
               "note": "API キー（従量課金）。ChatGPT のプランは Codex CLI で使う"},
    "anthropic": {"name": "Anthropic（Claude）", "base_url": "https://api.anthropic.com/v1",
                  "keys_url": "https://console.anthropic.com/settings/keys",
                  "note": "API キー（従量課金）。Claude のプランは Claude Code で使う"},
    "gemini": {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "keys_url": "https://aistudio.google.com/apikey", "note": "API キー"},
    "custom": {"name": "OpenAI 互換（任意）", "base_url": "", "keys_url": "",
               "note": "OpenAI 互換の API を持つ任意の送信先（HTTPS のみ）"},
}
# OpenRouter のデータの扱い（要求ごとに provider へ付ける）。既定は収集する提供元へ送らない。
#   zdr: 保存しない（Zero Data Retention）提供元だけ
#   deny: 入力を収集・学習に使う提供元へは送らない（provider.data_collection = "deny"）
#   allow: 制限しない
DATA_POLICIES = ("zdr", "deny", "allow")
DEFAULT_DATA_POLICY = "deny"
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")
_lock = threading.RLock()


class CloudError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _path() -> Path:
    return data_dir() / "cloud-providers.json"


def _usage_path() -> Path:
    return data_dir() / "cloud-usage.jsonl"


def _read() -> dict:
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("providers", {})
            value.setdefault("egress", {})
            return value
    except (OSError, ValueError):
        pass
    return {"providers": {}, "egress": {}}


def _write(value: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _provider(provider_id: str) -> dict:
    if provider_id not in PROVIDERS:
        raise CloudError("未対応のプロバイダです", 404)
    return PROVIDERS[provider_id]


def _validate_custom_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise CloudError("送信先は HTTPS の URL を指定してください（認証情報・クエリは入れない）")
    return url.strip().rstrip("/")


def _base_url(provider_id: str, entry: dict) -> str:
    return str(entry.get("base_url") or "") if provider_id == "custom" else str(PROVIDERS[provider_id]["base_url"])


def status() -> dict:
    """画面向け。キーそのものは返さない（末尾 4 文字だけ）。"""
    state = _read()
    providers = []
    for provider_id, spec in PROVIDERS.items():
        entry = state["providers"].get(provider_id, {})
        providers.append({
            "id": provider_id, "name": spec["name"], "note": spec["note"], "keys_url": spec["keys_url"],
            "base_url": _base_url(provider_id, entry), "configured": bool(entry.get("key")),
            "key_hint": str(entry.get("key_hint") or ""), "enabled": bool(entry.get("enabled")),
            "models": list(entry.get("models") or []),
            "monthly_budget_usd": float(entry.get("monthly_budget_usd") or 0),
            "spent_this_month_usd": round(spent_this_month(provider_id), 4),
            "data_policy": str(entry.get("data_policy") or DEFAULT_DATA_POLICY) if provider_id == "openrouter" else None,
        })
    egress = {caller: bool(state["egress"].get(caller)) for caller in CALLERS}
    return {"providers": providers, "egress": egress, "caller_labels": CALLER_LABELS}


def save_provider(provider_id: str, patch: dict) -> dict:
    """キー・有効/無効・使うモデル・月の上限を保存する。キーは暗号化する。"""
    from app.security.crypto import encrypt_text

    _provider(provider_id)
    allowed = {"api_key", "enabled", "models", "monthly_budget_usd", "base_url", "data_policy"}
    if set(patch) - allowed:
        raise CloudError("指定できない項目があります")
    with _lock:
        state = _read()
        entry = dict(state["providers"].get(provider_id, {}))
        if "api_key" in patch:
            key = str(patch["api_key"] or "").strip()
            if not key:
                entry.pop("key", None)
                entry.pop("key_hint", None)
                entry["enabled"] = False
            else:
                if len(key) > 512 or any(ch.isspace() for ch in key):
                    raise CloudError("API キーの形式が不正です")
                entry["key"] = encrypt_text(key)
                entry["key_hint"] = key[-4:]
        if "base_url" in patch:
            if provider_id != "custom":
                raise CloudError("送信先を変えられるのは OpenAI 互換（任意）だけです")
            entry["base_url"] = _validate_custom_url(str(patch["base_url"]))
        if "models" in patch:
            models = patch["models"]
            if not isinstance(models, list) or len(models) > 200 or not all(isinstance(m, str) and _MODEL.fullmatch(m) for m in models):
                raise CloudError("モデル名の形式が不正です")
            entry["models"] = list(dict.fromkeys(models))
        if "monthly_budget_usd" in patch:
            budget = patch["monthly_budget_usd"]
            if not isinstance(budget, (int, float)) or isinstance(budget, bool) or not 0 <= budget <= 100_000:
                raise CloudError("月の上限額は 0〜100000（USD）です。0 は上限なし")
            entry["monthly_budget_usd"] = float(budget)
        if "data_policy" in patch:
            if provider_id != "openrouter" or patch["data_policy"] not in DATA_POLICIES:
                raise CloudError(f"データの扱いは OpenRouter だけで、{' / '.join(DATA_POLICIES)} のどれかです")
            entry["data_policy"] = patch["data_policy"]
        if "enabled" in patch:
            if patch["enabled"] and not entry.get("key"):
                raise CloudError("先に API キーを設定してください")
            if provider_id == "custom" and patch["enabled"] and not entry.get("base_url"):
                raise CloudError("先に送信先の URL を設定してください")
            entry["enabled"] = bool(patch["enabled"])
        state["providers"][provider_id] = entry
        _write(state)
    return status()


def save_egress(patch: dict) -> dict:
    if not isinstance(patch, dict) or set(patch) - set(CALLERS) or not all(isinstance(v, bool) for v in patch.values()):
        raise CloudError(f"呼び出し元は {' / '.join(CALLERS)} を true / false で指定します")
    with _lock:
        state = _read()
        state["egress"].update(patch)
        _write(state)
    return status()


def is_cloud_model(model: str) -> bool:
    return str(model or "").startswith(PREFIX)


def enabled_models() -> list[dict]:
    """ゲートウェイの一覧に並べる外部モデル（有効なプロバイダで選ばれたものだけ）。"""
    state = _read()
    rows = []
    for provider_id, entry in state["providers"].items():
        if provider_id in PROVIDERS and entry.get("enabled") and entry.get("key"):
            for model in entry.get("models") or []:
                rows.append({"id": f"{PREFIX}{provider_id}/{model}", "provider": provider_id,
                             "name": f"{PROVIDERS[provider_id]['name']} / {model}", "external": True})
    return rows


@dataclass(frozen=True)
class CloudTarget:
    provider: str
    model: str
    base_url: str
    api_key: str
    data_policy: str = DEFAULT_DATA_POLICY

    def routing(self) -> dict | None:
        """OpenRouter へ付ける provider の指定（データの扱い）。"""
        if self.provider != "openrouter":
            return None
        if self.data_policy == "zdr":
            return {"data_collection": "deny", "zdr": True}
        if self.data_policy == "deny":
            return {"data_collection": "deny"}
        return None


def resolve(model: str, *, caller: str) -> CloudTarget:
    """`cloud:<プロバイダ>/<モデル>` を送信先へ解決する。送ってよいかもここで判定する。"""
    from app.security.crypto import decrypt_text

    if caller not in CALLERS:
        caller = "api"
    rest = str(model)[len(PREFIX):]
    provider_id, _, provider_model = rest.partition("/")
    if not provider_model:
        raise CloudError("外部モデルは cloud:<プロバイダ>/<モデル> で指定します")
    state = _read()
    if not state["egress"].get(caller):
        raise CloudError(f"{CALLER_LABELS[caller]} からの外部モデルの利用は許可されていません"
                         "（Models → フロンティアモデル → 外部への送信）", 403)
    _provider(provider_id)
    entry = state["providers"].get(provider_id, {})
    if not entry.get("enabled") or not entry.get("key"):
        raise CloudError(f"{PROVIDERS[provider_id]['name']} は有効になっていません", 403)
    if provider_model not in (entry.get("models") or []):
        raise CloudError("このモデルは使うモデルに選ばれていません", 403)
    budget = float(entry.get("monthly_budget_usd") or 0)
    if budget > 0 and spent_this_month(provider_id) >= budget:
        raise CloudError(f"{PROVIDERS[provider_id]['name']} の今月の上限額（{budget} USD）に達しました", 429)
    return CloudTarget(provider_id, provider_model, _base_url(provider_id, entry), decrypt_text(entry["key"]),
                       str(entry.get("data_policy") or DEFAULT_DATA_POLICY))


def record_usage(target: CloudTarget, caller: str, usage: dict | None, *, status_code: int) -> None:
    """1 回の呼び出しの記録。本文は残さない。"""
    usage = usage or {}
    cost = usage.get("cost")
    row = {
        "at": time.time(), "caller": caller, "provider": target.provider, "model": target.model,
        "status": status_code, "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
    }
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    os.chmod(path, 0o600)


def usage_rows(*, since: float = 0.0, limit: int = 500) -> list[dict]:
    try:
        lines = _usage_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("at", 0) < since:
            break
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _month_start(now: float | None = None) -> float:
    local = time.localtime(now or time.time())
    return time.mktime((local.tm_year, local.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def spent_this_month(provider_id: str) -> float:
    return sum(float(row.get("cost_usd") or 0) for row in usage_rows(since=_month_start(), limit=1_000_000)
               if row.get("provider") == provider_id)


async def list_remote_models(provider_id: str) -> list[dict]:
    """プロバイダが提供するモデルの一覧（使うモデルを選ぶため）。キーで問い合わせる。"""
    import httpx

    from app.security.crypto import decrypt_text

    _provider(provider_id)
    entry = _read()["providers"].get(provider_id, {})
    if not entry.get("key"):
        raise CloudError("先に API キーを設定してください")
    base = _base_url(provider_id, entry)
    if not base:
        raise CloudError("送信先の URL を設定してください")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(f"{base}/models",
                                        headers={"Authorization": f"Bearer {decrypt_text(entry['key'])}"})
    except httpx.HTTPError as exc:
        raise CloudError("プロバイダに接続できませんでした", 502) from exc
    if response.status_code in (401, 403):
        raise CloudError("API キーが受け付けられませんでした", 400)
    if response.status_code >= 400:
        raise CloudError(f"モデルの一覧を取得できませんでした（HTTP {response.status_code}）", 502)
    zdr_models = await _zdr_models() if provider_id == "openrouter" else set()
    rows = []
    for item in (response.json().get("data") or [])[:2000]:
        model_id = str(item.get("id") or "")
        if not _MODEL.fullmatch(model_id):
            continue
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        prompt, completion = _per_million(pricing.get("prompt")), _per_million(pricing.get("completion"))
        free = prompt == 0 and completion == 0
        rows.append({"id": model_id, "name": str(item.get("name") or model_id)[:200],
                     "context_length": item.get("context_length"), "free": free,
                     "created": item.get("created") if isinstance(item.get("created"), int) else None,
                     # USD / 100 万トークン。分からなければ None。
                     "price_input": prompt, "price_output": completion,
                     "privacy": _privacy(provider_id, model_id, free, zdr_models)})
    return rows


def _per_million(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number * 1_000_000, 6) if number >= 0 else None


def _privacy(provider_id: str, model_id: str, free: bool, zdr_models: set[str]) -> str:
    """入力の扱いの目安。

    zdr: 保存しないプロバイダの経路がある（OpenRouter の ZDR 一覧に載っている）
    may_train: 無料のモデル。提供元が入力を学習や記録に使うことがある（OpenRouter の利用規約）
    may_store: 提供元の方針に従って保存されることがある
    direct: プロバイダ自身の API（OpenAI・Anthropic・Gemini）。扱いはそのプロバイダの規約による
    """
    if provider_id != "openrouter":
        return "direct"
    if free:
        return "may_train"
    base = model_id.split(":", 1)[0]
    return "zdr" if model_id in zdr_models or base in zdr_models else "may_store"


_zdr_cache: tuple[float, set[str]] = (0.0, set())


async def _zdr_models() -> set[str]:
    """OpenRouter の、保存しない（Zero Data Retention）経路があるモデル。公開の一覧で、キーは送らない。1 時間持つ。"""
    import httpx

    global _zdr_cache
    if time.time() - _zdr_cache[0] < 3600 and _zdr_cache[1]:
        return _zdr_cache[1]
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get("https://openrouter.ai/api/v1/endpoints/zdr")
        models = {str(item.get("model_id") or "") for item in (response.json().get("data") or [])}
        models.discard("")
    except (httpx.HTTPError, ValueError, AttributeError):
        return _zdr_cache[1]
    _zdr_cache = (time.time(), models)
    return models


async def account(provider_id: str) -> dict:
    """残高と使用額（OpenRouter の /key）。他のプロバイダは API で残高を返さない。"""
    import httpx

    from app.security.crypto import decrypt_text

    _provider(provider_id)
    if provider_id != "openrouter":
        return {"supported": False}
    entry = _read()["providers"].get(provider_id, {})
    if not entry.get("key"):
        raise CloudError("先に API キーを設定してください")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get("https://openrouter.ai/api/v1/key",
                                        headers={"Authorization": f"Bearer {decrypt_text(entry['key'])}"})
    except httpx.HTTPError as exc:
        raise CloudError("OpenRouter に接続できませんでした", 502) from exc
    if response.status_code in (401, 403):
        raise CloudError("API キーが受け付けられませんでした", 400)
    if response.status_code >= 400:
        raise CloudError(f"残高を取得できませんでした（HTTP {response.status_code}）", 502)
    data = (response.json() or {}).get("data") or {}

    def number(key: str) -> float | None:
        value = data.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    free = data.get("free_model_daily_requests") if isinstance(data.get("free_model_daily_requests"), dict) else {}
    return {"supported": True, "limit": number("limit"), "limit_remaining": number("limit_remaining"),
            "usage": number("usage"), "usage_monthly": number("usage_monthly"), "usage_daily": number("usage_daily"),
            "is_free_tier": bool(data.get("is_free_tier")),
            "free_requests_remaining": free.get("remaining") if isinstance(free.get("remaining"), int) else None}
