"""ランタイム設定の「能力（capability）」一覧: 共通の並び・項目を 1 か所で定義する。

UI 担当の RuntimeSettings 部品は `GET /api/v1/models/settings/capabilities`（この module）を
読み、①モデル ②コンテキスト ③同時に処理する数 ④速さの調整 ⑤起動 ⑥エキスパートの同じ
並びで設定画面を出す。**ランタイムに無い欄は返さない**（AGENTS.md「ランタイムに無い項目は
出さない」・docs/PLAN-model-catalog.md「run3 共通バックエンド API」）。

欄の id は実際の保存 API の引数名そのもの。選択肢・上限・既定・`save_keys` は各ランタイムの
Pydantic モデル（検証の一元化）から読むので、ここに二重持ちしない（検証と画面が食い違わない）。
古い登録・編集の引数も capability に含め、共通 UI から消さずに扱えるようにする。

Sly の prefix cache は常にオン（利用者の決まり 2026-11-28）。欄としては出さず、
always_on に注記するだけにする。
"""
from __future__ import annotations

from typing import Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

SECTION_ORDER = ("model", "context", "parallel", "speed", "launch", "expert")
SECTION_LABELS = {
    "model": "モデル", "context": "コンテキストの長さ", "parallel": "同時に処理する数",
    "speed": "速さの調整", "launch": "起動", "expert": "エキスパート",
}


def _literal_choices(annotation) -> tuple:
    """`Literal[...]` と `Optional[Literal[...]]` のどちらからも選択肢を取り出す。"""
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    for item in get_args(annotation):
        found = _literal_choices(item)
        if found:
            return found
    return ()


def _metadata_value(info, name: str):
    return next((getattr(item, name) for item in info.metadata if hasattr(item, name)), None)


def _bounds(info) -> tuple:
    """Pydantic の検証定数から画面の下限・上限・刻みを読む。

    `ge/le` だけでなく `gt/lt`（排他境界）も扱う。排他境界をそのまま min/max にすると
    スライダーで境界値を選べて保存時に 422 になるので、1 刻み内側へ寄せる。
    """
    step = _metadata_value(info, "multiple_of")
    low = _metadata_value(info, "ge")
    high = _metadata_value(info, "le")
    if low is None:
        gt = _metadata_value(info, "gt")
        if gt is not None:
            inset = step if step else (0.01 if "float" in str(info.annotation) else 1)
            low = gt + inset
    if high is None:
        lt = _metadata_value(info, "lt")
        if lt is not None:
            inset = step if step else (0.01 if "float" in str(info.annotation) else 1)
            high = lt - inset
    return low, high, step


def _default_of(info):
    if info.default is not PydanticUndefined:
        return info.default
    if info.default_factory is not None:
        try:
            return info.default_factory()
        except TypeError:
            return None
    return None


def _field(field_id: str, label: str, field_type: str, *, default=None, choices=None,
           minimum=None, maximum=None, step=None, expert: bool = False, note: str = "") -> dict:
    row = {"id": field_id, "label": label, "type": field_type, "default": default,
           "expert": expert}
    if choices is not None:
        row["choices"] = list(choices)
    if minimum is not None:
        row["min"] = minimum
    if maximum is not None:
        row["max"] = maximum
    if step is not None:
        row["step"] = step
    if note:
        row["note"] = note
    return row


def _api_field(body: type[BaseModel], field_id: str, label: str, field_type: str, *,
               default=None, choices=None, expert: bool = False, note: str = "") -> dict:
    """保存 API の Pydantic 定義から下限・上限・刻み・選択肢を読み、欄を作る。

    画面側で数値を丸めても保存で 422 にならないよう、検証定数を二重に持たない
    （docs/PLAN-model-catalog.md「run4」の約束）。
    """
    info = body.model_fields[field_id]
    if choices is None:
        choices = _literal_choices(info.annotation) or None
    low, high, step = _bounds(info)
    kwargs = {
        "default": _default_of(info) if default is None else default,
        "choices": choices,
        "minimum": low,
        "maximum": high,
        "step": step,
        "expert": expert,
        "note": note,
    }
    # None を明示したい欄（既定が null の int など）は呼び出し側で default=None を渡せないので、
    # sentinel で区別する。
    if default is _NULL:
        kwargs["default"] = None
    return _field(field_id, label, field_type, **kwargs)


_NULL = object()


def _section(section_id: str, fields: list[dict]) -> dict:
    return {"id": section_id, "label": SECTION_LABELS[section_id], "fields": fields}


def _lifecycle_fields(body: type[BaseModel], *, on_demand: bool) -> list[dict]:
    fields = []
    if on_demand and "on_demand_start" in body.model_fields:
        fields.append(_api_field(body, "on_demand_start", "呼ばれたら起動", "bool", default=True))
    if "idle_exclude" in body.model_fields:
        fields.append(_api_field(body, "idle_exclude", "アイドルで止めない", "bool", default=False,
                                 note="空いた GPU をほかのモデルに明け渡さない"))
    if "auto_start" in body.model_fields:
        fields.append(_api_field(body, "auto_start", "自動起動", "bool", default=False,
                                 note="ControlDeck の起動時に立ち上げる"))
    return fields


def _save_keys(bodies: list[type[BaseModel]]) -> list[str]:
    """既存の保存 API が受け付ける引数名。capability に無い既存値を消さないために使う。"""
    keys: list[str] = []
    for body in bodies:
        for name in body.model_fields:
            if name not in keys:
                keys.append(name)
    return keys


def _llama() -> dict:
    from app.models_mgmt.router import LlamaInstanceBody

    body = LlamaInstanceBody
    cache_types = _literal_choices(body.model_fields["cache_type_k"].annotation)
    draft_cache_types = _literal_choices(body.model_fields["spec_draft_cache_type_k"].annotation)
    think_choices = _literal_choices(body.model_fields["think"].annotation)
    load_modes = _literal_choices(body.model_fields["load_mode"].annotation)
    role_choices = _literal_choices(body.model_fields["role"].annotation)
    return {
        "sections": [
            _section("model", [
                _api_field(body, "alias", "モデル名", "text", default=""),
                _api_field(body, "model_path", "GGUF ファイル", "path", default="",
                           note="登録はファイルから（GET /api/v1/models/register/inspect）"),
                _api_field(body, "role", "役割", "select", default="llm", choices=role_choices),
            ]),
            _section("context", [
                # 上限は既存の保存 API（LlamaInstanceBody.ctx_size）の検証と同じ値。
                _api_field(body, "ctx_size", "コンテキストの長さ", "int", default=4096,
                           note="モデルの上限は GGUF のメタデータから（inspect の context_length）"),
                _api_field(body, "deep_research_ctx_size", "Deep Research 用の長さ", "int",
                           default=0, expert=True),
            ]),
            _section("parallel", [
                _api_field(body, "n_parallel", "同時に処理する数", "int", default=1),
            ]),
            _section("speed", [
                _api_field(body, "n_gpu_layers", "GPU に載せる層", "int", default=999,
                           note="999 で全層。VRAM 不足時は下げる"),
                _api_field(body, "spec_type", "先読みの種類", "text", default="", expert=True,
                           note="使える値は起動中のバイナリの /models/llama/options から"),
                _api_field(body, "draft_max", "先読みの最大トークン", "int", default=3, expert=True),
                _api_field(body, "draft_min", "先読みの最小確率", "int", default=0, expert=True),
                _api_field(body, "draft_p_min", "先読みの最低確率", "float", default=0.0, expert=True),
                _api_field(body, "think", "思考の既定", "select", default="auto",
                           choices=think_choices),
                _api_field(body, "think_budget_tokens", "思考の上限", "int", default=0),
                _api_field(body, "temperature", "温度", "float", default=0.8, expert=True),
                _api_field(body, "top_k", "top_k", "int", default=40, expert=True),
                _api_field(body, "top_p", "top_p", "float", default=0.95, expert=True),
                _api_field(body, "min_p", "min_p", "float", default=0.05, expert=True),
                _api_field(body, "repeat_penalty", "繰り返し抑制", "float", default=1.0, expert=True),
                _api_field(body, "seed", "シード", "int", default=-1, expert=True),
                _api_field(body, "kv_unified", "KV を統合", "bool", default=False, expert=True),
                _api_field(body, "flash_attn", "Flash Attention", "bool", default=True, expert=True),
                _api_field(body, "n_predict", "最大出力トークン", "int", default=2048, expert=True),
                _api_field(body, "cache_type_k", "K キャッシュの形式", "select", default="f16",
                           choices=cache_types, expert=True),
                _api_field(body, "cache_type_v", "V キャッシュの形式", "select", default="f16",
                           choices=cache_types, expert=True),
                _api_field(body, "batch_size", "バッチサイズ", "int", default=2048, expert=True),
                _api_field(body, "ubatch_size", "マイクロバッチ", "int", default=512, expert=True),
                _api_field(body, "threads", "生成スレッド", "int", default=-1, expert=True),
                _api_field(body, "threads_batch", "バッチスレッド", "int", default=-1, expert=True),
                _api_field(body, "mmap", "mmap", "bool", default=True, expert=True),
                _api_field(body, "mlock", "mlock", "bool", default=False, expert=True),
                _api_field(body, "load_mode", "読み込み方", "select", default="",
                           choices=load_modes, expert=True),
                _api_field(body, "cpu_moe", "MoE を CPU へ", "bool", default=False, expert=True),
                _api_field(body, "n_cpu_moe", "CPU へ置く MoE 層", "int", default=0, expert=True),
                _api_field(body, "spec_draft_model_path", "先読みモデルの GGUF", "path",
                           default="", expert=True),
                _api_field(body, "spec_draft_ngl", "先読みモデルを載せる層", "int",
                           default=-1, expert=True),
                _api_field(body, "spec_draft_cache_type_k", "先読み K キャッシュ", "select",
                           default="", choices=draft_cache_types, expert=True),
                _api_field(body, "spec_draft_cache_type_v", "先読み V キャッシュ", "select",
                           default="", choices=draft_cache_types, expert=True),
                _api_field(body, "image_min_tokens", "画像 1 枚の最小トークン", "int",
                           default=1024, expert=True),
            ]),
            _section("launch", _lifecycle_fields(body, on_demand=False)),
            _section("expert", [
                _api_field(body, "port", "ポート", "int", default=8080, expert=True),
                _api_field(body, "mmproj_path", "画像の部品（mmproj）", "path", default="", expert=True),
                _api_field(body, "endpoint_id", "エンドポイント", "text", default="", expert=True),
                _api_field(body, "order", "並び順", "int", default=1, expert=True),
                _field("options", "詳しい引数", "ref", default="", expert=True,
                       note="GET /api/v1/models/options-spec（既存の引数一覧）"),
            ]),
        ],
        "always_on": [],
        "save_keys": _save_keys([body]),
        "endpoints": {"instances": "/api/v1/models/llama/instances"},
    }


def _radiance() -> dict:
    from app.models_mgmt import radiance
    from app.models_mgmt.router import RadianceModelBody

    body = RadianceModelBody
    return {
        "sections": [
            _section("model", [
                _api_field(body, "alias", "モデル名", "text", default=""),
                _api_field(body, "model_path", ".rad ファイル", "path", default="",
                           note="Radiance のモデルは .rad 1 ファイル（Sly の safetensors フォルダとは別形式）"),
            ]),
            _section("context", [
                _field("context", "コンテキストの長さ", "int", default=131072,
                       minimum=radiance._CONTEXT_MIN, maximum=radiance._CONTEXT_MAX,  # noqa: SLF001 - 検証の定数をそのまま使う
                       note=f"学習時の上限 {radiance.TRAINING_CONTEXT:,}"),
            ]),
            _section("parallel", [
                _api_field(body, "max_num_seqs", "同時に処理する数", "int", default=4),
            ]),
            _section("speed", [
                _api_field(body, "kv_cache", "KV キャッシュの形式", "select", default="bf16",
                           choices=radiance.KV_TYPES),
                _api_field(body, "speculative_tokens", "先読みトークン数", "int", default=_NULL,
                           note="null（省略）で自動"),
                _api_field(body, "reasoning_effort", "思考の既定", "select", default="",
                           choices=radiance.REASONING_EFFORTS),
                _api_field(body, "thinking_budget", "思考の上限", "int", default=0),
                _api_field(body, "memory_saver", "メモリセーバー", "bool", default=False),
                _api_field(body, "ram_offload", "RAM への退避", "bool", default=False),
            ]),
            _section("launch", _lifecycle_fields(body, on_demand=True)),
            _section("expert", [
                _api_field(body, "port", "ポート", "int", default=8100, expert=True),
                _api_field(body, "gpu_headroom_mib", "GPU の空き確保量", "int", default=1024,
                           expert=True),
            ]),
        ],
        "always_on": [],
        "save_keys": _save_keys([body]),
        "endpoints": {"instances": "/api/v1/models/radiance/instances"},
    }


def _sly_radiance() -> dict:
    from app.models_mgmt import sly_radiance as sly
    from app.models_mgmt.router import SlyRadianceModelBody

    body = SlyRadianceModelBody
    return {
        "sections": [
            _section("model", [
                _api_field(body, "alias", "モデル名", "text", default=""),
                _api_field(body, "model_path", "モデルのフォルダ", "path", default="",
                           note="config.json のある safetensors のフォルダ（取得で揃う）"),
                _api_field(body, "drafter_path", "先読みモデルのフォルダ", "path",
                           default="", expert=True),
            ]),
            _section("context", [
                _field("context", "コンテキストの長さ", "int", default=131072,
                       minimum=sly._CONTEXT_MIN, maximum=sly._CONTEXT_MAX,  # noqa: SLF001 - 検証の定数をそのまま使う
                       note="先読みを使うときは 8 の余白が要る（起動時に引かれる）"),
            ]),
            _section("parallel", [
                _api_field(body, "max_num_seqs", "同時に処理する数", "int", default=4),
            ]),
            _section("speed", [
                _api_field(body, "kv_cache", "KV キャッシュの形式", "select", default="fp8",
                           choices=sly.KV_TYPES),
                _api_field(body, "speculative_tokens", "先読みトークン数", "int", default=7,
                           note="0 で先読みなし"),
                _api_field(body, "prefill_chunk", "prefill の chunk", "select", default=2048,
                           choices=sly.CHUNKS),
                _api_field(body, "gpu_memory_utilization", "GPU のメモリの使用率", "float",
                           default=0.98,
                           note="共通の VRAM の余白を超えないよう起動時に調整される"),
                _api_field(body, "reasoning_effort", "思考の既定", "select", default="",
                           choices=sly.REASONING_EFFORTS),
                _api_field(body, "quantization", "量子化の切り替え", "select", default="",
                           choices=sly.QUANT_SWITCHES, expert=True),
            ]),
            _section("launch", _lifecycle_fields(body, on_demand=True)),
            _section("expert", [
                _api_field(body, "port", "ポート", "int", default=8100, expert=True),
                _api_field(body, "engine_settings", "環境変数（RADIANCE_*・R4D_*）", "dict",
                           default={}, expert=True,
                           note=f"{sly._MAX_ENV} 個まで"),  # noqa: SLF001
            ]),
        ],
        # 利用者の決まり（2026-11-28）: 常時オン。切る欄は出さない。
        "always_on": [{"id": "prefix_cache",
                       "note": "prefix cache は常にオンです（起動 argv に必ず付く。切る設定はありません）"}],
        "save_keys": _save_keys([body]),
        "endpoints": {"instances": "/api/v1/models/sly-radiance/instances"},
    }


def _strata_families():
    from app.models_mgmt import strata_catalog

    nvidia = strata_catalog.has_nvidia_gpu()      # sysfs の走査は 1 回だけ
    return [f for f in strata_catalog.families() if strata_catalog.visible(f.id, nvidia)]


def _strata() -> dict:
    from app.models_mgmt import strata_catalog, strata_gateway
    from app.models_mgmt.router import StrataModelBody

    body = StrataModelBody
    families = _strata_families()
    # 量子化の選択肢は既定の系列（qwen）のもの。系列を変えたら目録から選び直す（既存の
    # GET /api/v1/models/strata/catalog が正）。ここに全系列の和を並べると、系列と合わない
    # 組を出して保存で断られる。
    family_ids = [f.id for f in families]
    default_family = family_ids[0] if family_ids else "qwen"
    quants = tuple(q.id for q in strata_catalog.quantizations(default_family)) if families else ()
    default_model = "iq2_xs" if "iq2_xs" in quants else (quants[0] if quants else "")
    contexts = [f"{value}k" for value in (4, 8, 16, 32, 64, 128, 256, 512, 1024)
                if strata_gateway.valid_context(f"{value}k")]
    # 量子化の選択肢は系列ごと（choices_by）。全部を qwen の一覧にしていたため、Inkling の SQ-20 を選ぶと
    # 画面が一覧の先頭の「q2_0」を出していた（2026-11-28 利用者「インクリングの SQ20 って Q2 なの？」）。
    family_field = _api_field(body, "family", "系列", "select", default=default_family, choices=family_ids)
    family_field["choice_labels"] = {f.id: f.short_name for f in families}
    model_field = _api_field(body, "model", "量子化", "select", default=default_model, choices=quants)
    model_field["choices_by"] = {"field": "family", "map": {
        f.id: [q.id for q in strata_catalog.quantizations(f.id)] for f in families}}
    model_field["choice_labels"] = {q.id: q.label for f in families for q in strata_catalog.quantizations(f.id)}
    return {
        "sections": [
            _section("model", [
                _api_field(body, "alias", "モデル名", "text", default=""),
                family_field,
                model_field,
                _api_field(body, "model_path", "手元のファイル（任意）", "path", default="",
                           note="指定すると、最初の起動での取得をしません"),
            ]),
            _section("context", [
                # 保存 API は "<N>k" の文字列。選択肢で表し、数値の上限は混ぜない。
                _field("context", "コンテキストの長さ", "select", default="32k",
                       choices=contexts,
                       note=f"{strata_gateway._CONTEXT_MIN_K}k〜{strata_gateway._CONTEXT_MAX_K}k"),  # noqa: SLF001
            ]),
            _section("parallel", [
                _api_field(body, "parallel", "同時に処理する数", "int", default=1),
            ]),
            _section("speed", [
                _api_field(body, "kv", "KV キャッシュの形式", "select", default="int8"),
                _api_field(body, "drafting", "先読み", "select", default="auto",
                           note="Inkling は auto か off だけ"),
                _api_field(body, "vision", "画像の読み取り", "select", default="on"),
                _api_field(body, "reasoning_effort", "思考の既定", "select", default="",
                           note="思考を持たない系列では指定できない"),
                _api_field(body, "thinking_budget", "思考の上限", "int", default=0),
                _api_field(body, "kv_resident", "KV の常駐トークン", "int", default=_NULL,
                           expert=True, note="null で既定。1024 の倍数。Qwen 系だけ"),
                _api_field(body, "ram_keep_gib", "RAM に残す量", "float", default=_NULL,
                           expert=True, note="Qwen 系だけ"),
            ]),
            _section("launch", _lifecycle_fields(body, on_demand=False)),
            _section("expert", [
                _api_field(body, "port", "ポート", "int", default=8095, expert=True),
            ]),
        ],
        "always_on": [],
        "save_keys": _save_keys([body]),
        "endpoints": {"instances": "/api/v1/models/strata/instances"},
    }


def _maya() -> dict:
    from app.features.router import MayaInstanceSettingsBody, MayaRegisterBody
    from app.models_mgmt import maya_s

    # 選択肢・範囲は Maya の保存 API（MayaInstanceSettingsBody・MayaRegisterBody）から読む。
    # alias は目録の setup_id から Maya 自身が決めるので欄に出さない。
    effects = _literal_choices(MayaInstanceSettingsBody.model_fields["reasoning_effort"].annotation)
    defaults = maya_s.get_settings()
    stored_effort = defaults.get("reasoning_effort")
    effective_effort = stored_effort if stored_effort in effects else (
        "high" if "high" in effects else (effects[0] if effects else ""))
    try:
        thinking_budget = int(defaults.get("thinking_budget") or 0)
    except (TypeError, ValueError):
        thinking_budget = 0
    settings = MayaInstanceSettingsBody
    register = MayaRegisterBody
    return {
        "sections": [
            _section("model", [
                _field("setup_id", "モデル（目録から）", "ref", default="",
                       note="GET /api/v1/features/maya-s/catalog の setup_id。別名は Maya が決める"),
                _field("folder", "モデルのフォルダ", "ref", default="",
                       note="ファイル選択・目録で決める（①モデルの slot）。保存 API に渡す値として残す"),
            ]),
            _section("context", [
                # 登録（MayaRegisterBody）と保存（MayaInstanceSettingsBody）の両方が受け付ける値。
                _field("context", "コンテキストの長さ", "int", default=8192, minimum=8192,
                       maximum=1048576),
            ]),
            _section("parallel", [
                _api_field(settings, "parallel", "同時に処理する数", "int", default=1,
                           note="Maya の既定は 1（Hy4 は setup から固定）"),
            ]),
            _section("speed", [
                # 速度・思考・サンプリングは Maya の保存 API が直接受け付ける。
                # 以前は参照欄だけ置いてあり、エキスパート切だと「速さの調整」が空だった。
                _api_field(settings, "reasoning_effort", "思考の既定", "select",
                           default=effective_effort, choices=effects),
                _api_field(settings, "thinking_budget", "思考の上限", "int",
                           default=thinking_budget),
                _api_field(settings, "temperature", "温度", "float", default=1.0),
                _api_field(settings, "top_p", "top_p", "float", default=1.0),
                _api_field(settings, "dspark", "下書き（DSpark）", "select", default="off"),
                _api_field(settings, "vram_experts", "VRAM に置く割合", "float", default=0.0),
                _api_field(settings, "mmap_experts", "ファイルの読み方", "select", default="auto"),
                _api_field(settings, "ssd_cache", "SSD キャッシュ", "text", default="off"),
                _api_field(settings, "prefill_chunk", "先読みの単位", "int", default=0),
                _api_field(settings, "vision", "画像の読み取り", "select", default="off"),
            ]),
            _section("launch", _lifecycle_fields(settings, on_demand=True)),
            _section("expert", [
                _api_field(settings, "gpu_index", "GPU 番号", "int", default=0, expert=True),
                _api_field(settings, "cpu_threads", "CPU スレッド数", "int", default=0, expert=True),
            ]),
        ],
        "always_on": [],
        "save_keys": _save_keys([settings, register]),
        "endpoints": {"instances": "/api/v1/features/maya-s/model"},
    }


_BUILDERS = {
    "llama": _llama,
    "radiance": _radiance,
    "sly-radiance": _sly_radiance,
    "strata": _strata,
    "maya": _maya,
}


def api_payload() -> dict:
    """GET /api/v1/models/settings/capabilities の本体（同期・軽量。検証定数の読み出しだけ）。"""
    return {"runtimes": {runtime: builder() for runtime, builder in _BUILDERS.items()}}
