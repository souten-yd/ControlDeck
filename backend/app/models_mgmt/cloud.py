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
from dataclasses import dataclass, field
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


def models_for(caller: str) -> list[dict]:
    """その呼び出し元が選べる外部モデル。外部への送信を許可していなければ空。

    画面の選択肢に使う。実際に送ってよいかは resolve() が呼び出しのたびに確かめる。
    """
    if caller not in CALLERS or not _read()["egress"].get(caller):
        return []
    return enabled_models()


def usable(model: str, *, caller: str) -> bool:
    """`cloud:…` が、いまその呼び出し元から使えるか（許可あり・有効・選ばれている）。"""
    return any(item["id"] == model for item in models_for(caller))


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


@dataclass
class CallTimer:
    """実行中の 1 回の呼び出し（Home の表示用。メモリだけ、本文は持たない）。"""
    provider: str
    model: str
    caller: str
    started: float = field(default_factory=time.monotonic)
    started_at: float = field(default_factory=time.time)
    first_at: float | None = None
    events: int = 0  # 流れてきた差分の数（おおよそのトークン数）


_inflight: list[CallTimer] = []


def begin_call(target: CloudTarget, caller: str) -> CallTimer:
    timer = CallTimer(target.provider, target.model, caller)
    with _lock:
        _inflight.append(timer)
    return timer


def note_stream(timer: CallTimer, chunk: bytes) -> None:
    """中継した SSE の塊から、最初の応答の時刻と差分の数だけを数える（本文は見ない）。"""
    count = chunk.count(b"data:")
    if count and timer.first_at is None:
        timer.first_at = time.monotonic()
    timer.events += count


def _finish(timer: CallTimer | None) -> tuple[float | None, float | None]:
    if timer is None:
        return None, None
    with _lock:
        if timer in _inflight:
            _inflight.remove(timer)
    duration = time.monotonic() - timer.started
    ttft = (timer.first_at - timer.started) if timer.first_at is not None else None
    return duration, ttft


def _speed(completion: int, duration: float | None, ttft: float | None) -> float | None:
    """生成の速さ（tok/s）。stream なら最初のトークンより後の時間で割る。"""
    if not duration or completion <= 1:
        return None
    if ttft is not None and duration - ttft > 0.05:
        return (completion - 1) / (duration - ttft)
    return completion / duration


def record_usage(target: CloudTarget, caller: str, usage: dict | None, *, status_code: int,
                 timer: CallTimer | None = None) -> None:
    """1 回の呼び出しの記録。本文は残さない。"""
    usage = usage or {}
    cost = usage.get("cost")
    duration, ttft = _finish(timer)
    completion = int(usage.get("completion_tokens") or 0)
    speed = _speed(completion, duration, ttft) if status_code < 400 else None
    row = {
        "at": time.time(), "caller": caller, "provider": target.provider, "model": target.model,
        "status": status_code, "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": completion,
        "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
    }
    if duration is not None:
        row["duration_s"] = round(duration, 3)
    if ttft is not None:
        row["ttft_s"] = round(ttft, 3)
    if speed is not None:
        row["tokens_per_second"] = round(speed, 1)
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


def _day_start(now: float | None = None) -> float:
    local = time.localtime(now or time.time())
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


def summary(*, now: float | None = None) -> dict:
    """Home 用のまとめ: 実行中の呼び出し、今日・今月の合計、最近使ったモデルの速さ。"""
    now = now or time.time()
    mono = time.monotonic()
    with _lock:
        # 中継が始まらずに捨てられた呼び出しは記録されないまま残るので、上流の時間切れ（600 秒）を過ぎたら外す。
        _inflight[:] = [t for t in _inflight if mono - t.started < 900]
        active = list(_inflight)
    running = []
    for t in active:
        elapsed = mono - t.started
        live = None
        if t.first_at is not None and t.events > 1 and mono - t.first_at > 0.5:
            live = round((t.events - 1) / (mono - t.first_at), 1)
        running.append({
            "provider": t.provider, "model": t.model, "caller": t.caller,
            "caller_label": CALLER_LABELS.get(t.caller, t.caller),
            "elapsed_s": round(elapsed, 1), "phase": "generate" if t.first_at is not None else "waiting",
            "tokens": t.events, "tokens_per_second": live,
        })

    month_start, day_start = _month_start(now), _day_start(now)
    rows = usage_rows(since=min(month_start, now - 7 * 86400), limit=1_000_000)

    def totals(items: list[dict]) -> dict:
        return {
            "calls": len(items),
            "errors": sum(1 for r in items if int(r.get("status") or 0) >= 400),
            "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in items),
            "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in items),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in items), 6),
        }

    by_model: dict[tuple[str, str], dict] = {}
    for r in rows:  # 新しい順
        if r.get("at", 0) < now - 7 * 86400:
            continue
        key = (str(r.get("provider")), str(r.get("model")))
        entry = by_model.setdefault(key, {"provider": key[0], "model": key[1], "calls": 0, "cost_usd": 0.0,
                                          "last_at": r.get("at"), "_tps": [], "_ttft": []})
        entry["calls"] += 1
        entry["cost_usd"] += float(r.get("cost_usd") or 0)
        if r.get("tokens_per_second") and len(entry["_tps"]) < 20:
            entry["_tps"].append(float(r["tokens_per_second"]))
        if r.get("ttft_s") is not None and len(entry["_ttft"]) < 20:
            entry["_ttft"].append(float(r["ttft_s"]))
    models = []
    for entry in sorted(by_model.values(), key=lambda e: e["last_at"] or 0, reverse=True)[:5]:
        tps, ttft = entry.pop("_tps"), entry.pop("_ttft")
        entry["cost_usd"] = round(entry["cost_usd"], 6)
        entry["tokens_per_second"] = round(sum(tps) / len(tps), 1) if tps else None
        entry["ttft_s"] = round(sum(ttft) / len(ttft), 2) if ttft else None
        models.append(entry)

    last = rows[0] if rows else None
    return {
        "running": running,
        "today": totals([r for r in rows if r.get("at", 0) >= day_start]),
        "month": totals([r for r in rows if r.get("at", 0) >= month_start]),
        "models": models,
        "last": ({k: last.get(k) for k in ("at", "caller", "provider", "model", "status", "prompt_tokens",
                                           "completion_tokens", "cost_usd", "duration_s", "ttft_s",
                                           "tokens_per_second")} if last else None),
    }


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
        row = {"id": model_id, "name": str(item.get("name") or model_id)[:200],
               "context_length": item.get("context_length"), "free": free,
               "created": item.get("created") if isinstance(item.get("created"), int) else None,
               # USD / 100 万トークン。分からなければ None。
               "price_input": prompt, "price_output": completion,
               "privacy": _privacy(provider_id, model_id, free, zdr_models)}
        if provider_id == "openrouter":
            # 絞り込み用（賢さの目安・対応機能・提供元）。他のプロバイダは /models に情報が無い。
            row.update(_capabilities(item, model_id))
        rows.append(row)
    return rows


# 「70b」「8x22b」「a3b」「135m」。前後が英数字のもの（4bit・v2b・32k など）は拾わない。
_PARAMS = re.compile(r"(?<![a-z0-9.])(?:(\d{1,3})x)?(\d{1,4}(?:\.\d{1,2})?)([bm])(?![a-z0-9])", re.IGNORECASE)
_ACTIVE_PARAMS = re.compile(r"(?<![a-z0-9.])a(\d{1,4}(?:\.\d{1,2})?)b(?![a-z0-9])", re.IGNORECASE)
# gemma-3n の「e4b」（実効 4B）のように、数字の前に 1 文字付く表記。
_EFFECTIVE_PARAMS = re.compile(r"(?<![a-z0-9.])e(\d{1,3}(?:\.\d{1,2})?)b(?![a-z0-9])", re.IGNORECASE)


def parameter_count(*names: object) -> tuple[float | None, float | None]:
    """名前からパラメータ数（10 億単位）を読み取る。(全体, 実際に動く分)。

    OpenRouter の /models にパラメータ数の項目は無い。オープンなモデルは名前に書く習慣が
    あるので、hugging_face_id・id・表示名の順に見る。Claude や GPT のように公開していない
    モデルは None（画面では「不明」）。賢さの目安であって、正確な値ではない。
    """
    total: float | None = None
    active: float | None = None
    for name in names:
        text = str(name or "")
        if not text:
            continue
        if total is None:
            sizes = []
            for count, number, unit in _PARAMS.findall(text):
                value = float(number) * (int(count) if count else 1)
                value = value / 1000 if unit.lower() == "m" else value
                if 0.01 <= value <= 2000:
                    sizes.append(value)
            if not sizes:
                sizes = [float(number) for number in _EFFECTIVE_PARAMS.findall(text) if 0.01 <= float(number) <= 2000]
            if sizes:
                # 「30b-a3b」「17b-128e」のように複数あれば、いちばん大きいものが全体。
                total = round(max(sizes), 3)
        if active is None:
            found = _ACTIVE_PARAMS.search(text)
            if found:
                active = round(float(found.group(1)), 3)
        if total is not None and active is not None:
            break
    if active is not None and (total is None or active >= total):
        active = None
    return total, active


def _capabilities(item: dict, model_id: str) -> dict:
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    modalities = architecture.get("input_modalities")
    if not isinstance(modalities, list):
        # 古い形式: "text+image->text"
        modalities = str(architecture.get("modality") or "").split("->", 1)[0].split("+")
    supported = item.get("supported_parameters") if isinstance(item.get("supported_parameters"), list) else []
    params, active = parameter_count(item.get("hugging_face_id"), model_id, item.get("name"))
    return {
        "vendor": model_id.split("/", 1)[0] if "/" in model_id else "",
        "params_b": params, "active_params_b": active,
        "modalities": sorted({str(m).strip().lower() for m in modalities if str(m).strip()})[:8],
        "tools": "tools" in supported,
        "reasoning": "reasoning" in supported or "include_reasoning" in supported,
    }


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
    headers = {"Authorization": f"Bearer {decrypt_text(entry['key'])}"}
    credits: dict = {}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
            # アカウント全体の入金額と使用額（残高）。/key はこのキーの上限と使用額だけ。
            try:
                paid = await client.get("https://openrouter.ai/api/v1/credits", headers=headers)
                if paid.status_code == 200:
                    credits = (paid.json() or {}).get("data") or {}
            except (httpx.HTTPError, ValueError):
                credits = {}
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

    def credit(key: str) -> float | None:
        value = credits.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    total, spent = credit("total_credits"), credit("total_usage")
    balance = round(total - spent, 4) if total is not None and spent is not None else None
    # 管理用キー（Management / Provisioning key）はキーの管理と残高の確認だけができ、
    # モデルへの問い合わせは "User not found"（401）で断られる。画面で知らせる。
    management = bool(data.get("is_management_key") or data.get("is_provisioning_key"))
    return {"supported": True, "management_key": management, "balance": balance, "credits_total": total, "credits_used": spent, "limit": number("limit"), "limit_remaining": number("limit_remaining"),
            "usage": number("usage"), "usage_monthly": number("usage_monthly"), "usage_daily": number("usage_daily"),
            "is_free_tier": bool(data.get("is_free_tier")),
            "free_requests_remaining": free.get("remaining") if isinstance(free.get("remaining"), int) else None}
