"""会話のモデルの選び方を、ふつうのチャットと OpenCode で 1 つにそろえる（2026-11-27 利用者の依頼）。

- `groups()`: 画面の「モデルの札」に並べる一覧。種類ごとのまとまり（ローカル・OpenRouter・OpenCode（Zen）・Claude ほか）。
  使えない経路のものは並べない（Claude・Codex の CLI は会話の経路が無いので出さない）。秘密の会話はローカルだけ。
- `resolve_code_model()`: 会話で選んでいるモデルを OpenCode のモデル（`provider/モデル`）へ。使えなければ
  Models の既定の OpenCode のモデル（settings の default_model）へ置き換えて、理由を 1 行返す。
  会話のモデルを見ずに作業場の順番（GLM-5.3 ほか）で動いていたのが、Qwen で始めた会話が OpenCode で GLM になった原因。
"""
from __future__ import annotations

from dataclasses import dataclass, field

ZEN_PREFIXES = ("opencode-go/", "opencode/")
LOCAL_PREFIX = "controldeck/"

# OpenCode（Zen）のモデル。作業場の順番の既定（sandbox.DEFAULT_MODEL_CHAIN）と同じ顔ぶれ。
ZEN_MODELS = {
    "opencode-go/glm-5.3": ("GLM-5.3", "高性能・OpenCode Go の定額内・外へ出ます"),
    "opencode/hy4-flash-free": ("HY4 Flash（無料）", "速い・無料・外へ出ます"),
    "opencode/deepseek-v4.1-flash-free": ("DeepSeek V4.1 Flash（無料）", "無料・外へ出ます"),
}

GROUPS = (
    ("local", "ローカル"),
    ("openrouter", "OpenRouter"),
    ("opencode", "OpenCode（Zen）"),
    ("claude", "Claude"),
    ("codex", "Codex"),
    ("api", "OpenAI・Gemini など"),
)
_CLOUD_GROUP = {"openrouter": "openrouter", "anthropic": "claude"}


def is_zen(model: str) -> bool:
    return str(model or "").startswith(ZEN_PREFIXES)


def default_model() -> str:
    """Models で選んだ OpenCode の既定のモデル（`provider/モデル`）。無ければ空（作業場の順番どおり）。"""
    from app.integrations.opencode import provider

    return str(provider.get_settings().get("default_model") or "").strip()


def ref_of(model: str) -> str:
    """会話のモデル id を OpenCode の参照へ（Zen はそのまま、他はゲートウェイの controldeck/）。"""
    model = str(model or "").strip()
    return model if is_zen(model) else f"{LOCAL_PREFIX}{model}"


def model_of(ref: str) -> str:
    """OpenCode の参照を会話のモデル id へ（controldeck/ を外す）。"""
    ref = str(ref or "")
    return ref[len(LOCAL_PREFIX):] if ref.startswith(LOCAL_PREFIX) else ref


def label_of(model: str, labels: dict[str, str] | None = None) -> str:
    model = model_of(model)
    if model in ZEN_MODELS:
        return ZEN_MODELS[model][0]
    return (labels or {}).get(model) or model.split("/", 1)[-1]


def _cloud_usable(model: str, caller: str) -> bool:
    try:
        from app.models_mgmt import cloud

        return cloud.usable(model, caller=caller)
    except Exception:  # noqa: BLE001 - 外部モデルの都合で一覧を止めない
        return False


def code_usable(model: str, *, secret: bool, zen_ok: bool, llm_ok: bool) -> str:
    """そのモデルを OpenCode で使えるか。使えれば空文字、使えなければ理由。"""
    model = model_of(model)
    if secret:
        return "秘密の会話では OpenCode を使いません"
    if is_zen(model):
        return "" if zen_ok else "OpenCode（Zen）のキーが使えません"
    from app.models_mgmt import cloud

    if cloud.is_cloud_model(model):
        return "" if _cloud_usable(model, "opencode") else "OpenCode への外部送信が許可されていません"
    return "" if llm_ok else "この作業場はローカル LLM を借りない設定です"


@dataclass
class Choice:
    ref: str = ""           # OpenCode へ切り替えるモデル（空なら切り替えない＝従来どおり）
    note: str = ""          # 置き換えたときの 1 行
    source: str = ""        # conversation / default / ""
    detail: dict = field(default_factory=dict)


def resolve_code_model(chat_model: str, *, explicit: bool, secret: bool, zen_ok: bool, llm_ok: bool,
                       labels: dict[str, str] | None = None, default: str | None = None) -> Choice:
    """OpenCode で使うモデルを決める。

    explicit（会話でモデルを選んでいる・または始めている）なら、そのモデルをそのまま使う。使えないものなら
    既定の OpenCode のモデルへ置き換え、「OpenCode では〇〇が使えないので △△ にします」の 1 行を返す。
    explicit でなければ既定のモデル（無ければ従来どおり）。
    """
    fallback = default_model() if default is None else default
    fallback_ok = bool(fallback) and not code_usable(fallback, secret=secret, zen_ok=zen_ok, llm_ok=llm_ok)
    chosen = str(chat_model or "").strip()
    if explicit and chosen:
        reason = code_usable(chosen, secret=secret, zen_ok=zen_ok, llm_ok=llm_ok)
        if not reason:
            return Choice(ref=ref_of(chosen), source="conversation")
        if fallback_ok:
            note = f"OpenCode では{label_of(chosen, labels)}が使えないので{label_of(fallback, labels)}にします"
            return Choice(ref=ref_of(fallback), note=note, source="default", detail={"reason": reason})
        return Choice(note=f"OpenCode では{label_of(chosen, labels)}が使えないので、OpenCode の既定のモデルにします",
                      detail={"reason": reason})
    if fallback_ok:
        return Choice(ref=ref_of(fallback), source="default")
    return Choice()


def groups(*, local_models: list[str], labels: dict[str, str], auto_name: str, gateway_base: str,
           secret: bool, zen_ok: bool, llm_ok: bool = True) -> dict:
    """画面向けの一覧。使えないものは並べない（外部は許可したものだけ・秘密の会話はローカルだけ）。

    各モデル: id（会話のモデル）・name・hint（一言）・external・chat（ふつうのチャットで使える）・
    code（OpenCode で使える）・detail（エキスパートだけに出す細かい値）。
    """
    out: dict[str, list[dict]] = {key: [] for key, _ in GROUPS}
    for model in local_models:
        if model == "auto":
            name, hint = "自動", f"Models で選んだ実行環境に任せる{f'（今は {auto_name}）' if auto_name else ''}"
        else:
            name, hint = labels.get(model, model), "ローカル・外へ出ません・無料"
        out["local"].append({"id": model, "name": name, "hint": hint, "external": False, "chat": True,
                             "code": not code_usable(model, secret=secret, zen_ok=zen_ok, llm_ok=llm_ok),
                             "detail": model if name != model else ""})
    if not secret:
        try:
            from app.models_mgmt import cloud

            enabled = cloud.enabled_models()
        except Exception:  # noqa: BLE001
            enabled = []
        for item in enabled:
            cid, provider = item["id"], str(item["provider"])
            chat, code = _cloud_usable(cid, "assistant"), _cloud_usable(cid, "opencode")
            if not (chat or code):
                continue
            free = cid.endswith(":free")
            hint = ("外へ出ます・無料枠" if free else "外へ出ます・従量課金") + ("" if code else "・チャット専用")
            ctx = None
            try:
                ctx = cloud.context_length(provider, cid.split("/", 1)[-1])
            except Exception:  # noqa: BLE001
                pass
            out[_CLOUD_GROUP.get(provider, "api")].append(
                {"id": cid, "name": item["name"].split(" / ", 1)[-1], "hint": hint, "external": True,
                 "chat": chat, "code": code,
                 "detail": f"{cid}" + (f" ・ コンテキスト {ctx:,}" if ctx else "")})
        if zen_ok:
            for ref, (name, hint) in ZEN_MODELS.items():
                out["opencode"].append({"id": ref, "name": name, "hint": f"{hint}・OpenCode 専用", "external": True,
                                        "chat": False, "code": True, "detail": ref})
    return {"base_url": gateway_base,
            "groups": [{"id": key, "label": label, "models": out[key]} for key, label in GROUPS if out[key]]}
