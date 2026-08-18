"""思考（reasoning）設定の共通語彙。

think は「共通設定で全モデルを一律に縛るもの」ではなく **モデル個別の設定**として扱う。
モデルによって思考の深さの適正値が違い、共通設定が個別設定を上書きすると
モデルごとの調整ができなくなるため。

レベルは内部でトークンバジェットへ写像する。UI ではレベルを選ぶとバジェット入力欄に
対応値が入り、そのまま微調整できる（レベル＝プリセット）。
"""
from __future__ import annotations

from dataclasses import dataclass

# auto: モデル/チャットテンプレートの既定に任せる（何も送らない）
# custom: budget_tokens を直接指定する
THINK_MODES = ("auto", "off", "low", "medium", "high", "xhigh", "custom")
THINK_LEVELS = ("off", "low", "medium", "high", "xhigh")
THINK_LEVEL_BUDGETS: dict[str, int] = {
    "off": 0, "low": 1024, "medium": 4096, "high": 16384, "xhigh": 32768,
}
MAX_THINK_BUDGET = 262_144

# 旧語彙の読み替え。"on" は「レベル指定なしで有効」だったので、
# 現行語彙で最も近い high へ寄せる。
_LEGACY = {"": "auto", "true": "high", "on": "high", "max": "xhigh", "false": "off", "0": "off", "1": "high"}


@dataclass(frozen=True)
class ThinkSpec:
    """解決済みの思考設定。"""

    mode: str = "auto"
    budget_tokens: int = 0

    @property
    def enabled(self) -> bool | None:
        """思考を有効にするか。auto は「指定しない」を意味する None。"""
        if self.mode == "auto":
            return None
        return self.mode != "off"

    @property
    def reasoning_effort(self) -> str | None:
        """OpenAI 互換 API の reasoning_effort。custom は最も近いレベルへ丸める。"""
        if self.mode in THINK_LEVELS and self.mode != "off":
            return self.mode
        if self.mode == "custom" and self.budget_tokens > 0:
            return nearest_level(self.budget_tokens)
        return None

    @property
    def ollama_think(self) -> bool | str | None:
        """Ollama native /api/chat の think。バジェットは非対応なのでレベルへ落とす。"""
        if self.mode == "auto":
            return None
        if self.mode == "off":
            return False
        level = self.mode if self.mode in THINK_LEVELS else nearest_level(self.budget_tokens)
        # Ollama が解釈できるのは low/medium/high のみ。xhigh は high へ寄せる。
        return "high" if level == "xhigh" else level


def normalize_mode(value: object) -> str:
    """保存値・API入力を現行語彙へ正規化する（旧語彙も受ける）。"""
    if value is None:
        return "auto"
    if isinstance(value, bool):
        return "high" if value else "off"
    text = str(value).strip().lower()
    text = _LEGACY.get(text, text)
    return text if text in THINK_MODES else "auto"


def normalize_budget(mode: str, value: object) -> int:
    """custom のときだけ意味を持つトークンバジェット。0 は未指定（無制限扱い）。"""
    if mode != "custom":
        return 0
    try:
        tokens = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    if tokens <= 0:
        return 0
    return min(MAX_THINK_BUDGET, tokens)


def nearest_level(budget_tokens: int) -> str:
    """バジェットから最も近いレベル名を返す（レベルしか受けない相手向け）。"""
    if budget_tokens <= 0:
        return "off"
    levels = [(name, tokens) for name, tokens in THINK_LEVEL_BUDGETS.items() if tokens > 0]
    return min(levels, key=lambda item: abs(item[1] - budget_tokens))[0]


def spec(mode: object, budget_tokens: object = 0) -> ThinkSpec:
    resolved = normalize_mode(mode)
    return ThinkSpec(mode=resolved, budget_tokens=normalize_budget(resolved, budget_tokens))


def effective_budget(spec_value: ThinkSpec) -> int:
    """llama.cpp の --reasoning-budget に渡す値。-1 は無制限。"""
    if spec_value.mode == "auto":
        return -1
    if spec_value.mode == "custom":
        return spec_value.budget_tokens or -1
    return THINK_LEVEL_BUDGETS.get(spec_value.mode, -1)


def resolve(base_url: str, model: str) -> ThinkSpec:
    """base_url とモデル名から、そのモデルの思考設定を解決する。

    共通設定は参照しない。モデル個別設定だけが正。
    """
    from urllib.parse import urlsplit

    from app.models_mgmt import llama, ollama

    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3].rstrip("/")
    try:
        if normalized == ollama.base_url().rstrip("/"):
            config = ollama.get_model_config(model)
            return spec(config.get("think"), config.get("think_budget_tokens"))
        parsed = urlsplit(base_url)
        if parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port:
            # 同一ポートに複数モデルが載るため、モデル名（=alias）優先で引く。
            instance = next(
                (i for i in llama.list_instances() if str(i.get("alias")) == model), None,
            ) or llama.instance_for_port(parsed.port)
            if instance is not None:
                return spec(instance.get("think"), instance.get("think_budget_tokens"))
    except Exception:  # noqa: BLE001 - 思考設定の解決失敗で生成を止めない
        pass
    return ThinkSpec()


def migrate_shared_reasoning() -> dict:
    """旧・共通設定 chat.reasoning を各モデルの個別設定へ落とし込む（一度きり）。

    共通設定を消しただけだと、これまで「共通でオフ」にしていた環境が突然
    思考するようになり、体感が変わってしまう。個別 think が未設定のモデルにだけ
    旧共通値を書き、その後キーを取り除く。
    """
    import json as _json

    from app.models_mgmt import llama, ollama, runtime_policy

    path = runtime_policy._path()
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"migrated": False, "reason": "設定ファイルなし"}
    legacy = str((raw.get("chat") or {}).get("reasoning") or "")
    if not legacy:
        return {"migrated": False, "reason": "旧設定なし"}
    mode = normalize_mode(legacy)
    touched: list[str] = []
    if mode != "auto":
        try:
            for instance in llama.list_instances():
                if str(instance.get("role", "llm")) != "llm":
                    continue
                if normalize_mode(instance.get("think")) != "auto":
                    continue  # 個別設定が既にあるものは触らない
                llama.save_instance(str(instance["alias"]), {"think": mode})
                touched.append(f"llama.cpp:{instance['alias']}")
        except Exception:  # noqa: BLE001 - 移行失敗で起動を止めない
            pass
        try:
            for model, config in (ollama.get_settings().get("model_configs") or {}).items():
                if normalize_mode(config.get("think")) == "auto":
                    ollama.set_model_config(model, {"think": mode})
                    touched.append(f"ollama:{model}")
        except Exception:  # noqa: BLE001
            pass
    raw.get("chat", {}).pop("reasoning", None)
    try:
        path.write_text(_json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return {"migrated": False, "reason": "設定ファイルを書けません"}
    return {"migrated": True, "mode": mode, "models": touched}
