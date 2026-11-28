"""ランタイム設定の「能力（capability）」一覧: 共通の並び・項目を 1 か所で定義する。

UI 担当の RuntimeSettings 部品は `GET /api/v1/models/settings/capabilities`（この module）を
読み、①モデル ②コンテキスト ③同時に処理する数 ④速さの調整 ⑤起動 ⑥エキスパートの同じ
並びで設定画面を出す。**ランタイムに無い欄は返さない**（AGENTS.md「ランタイムに無い項目は
出さない」・docs/PLAN-model-catalog.md「run3 共通バックエンド API」）。

欄の id は実際の保存 API の引数名と同じ（SlyRadianceModelBody・StrataModelBody・
save_instance の引数）。選択肢・上限・既定は各ランタイムの検証定数から読むので、
ここに二重持ちしない（検証と画面が食い違わない）。

Sly の prefix cache は常にオン（利用者の決まり 2026-11-28）。欄としては出さず、
always_on に注記するだけにする。
"""
from __future__ import annotations

from typing import Literal, get_args, get_origin

SECTION_ORDER = ("model", "context", "parallel", "speed", "launch", "expert")
SECTION_LABELS = {
    "model": "モデル", "context": "コンテキストの長さ", "parallel": "同時に処理する数",
    "speed": "速さの調整", "launch": "起動", "expert": "エキスパート",
}


def _field(field_id: str, label: str, field_type: str, *, default, choices=None,
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


def _section(section_id: str, fields: list[dict]) -> dict:
    return {"id": section_id, "label": SECTION_LABELS[section_id], "fields": fields}


def _literal_choices(annotation) -> tuple:
    """`Literal[...]` と `Optional[Literal[...]]` のどちらからも選択肢を取り出す。"""
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    for item in get_args(annotation):
        found = _literal_choices(item)
        if found:
            return found
    return ()


def _lifecycle_fields(*, on_demand: bool) -> list[dict]:
    fields = []
    if on_demand:
        fields.append(_field("on_demand_start", "呼ばれたら起動", "bool", default=True))
    fields.append(_field("idle_exclude", "アイドルで止めない", "bool", default=False,
                         note="空いた GPU をほかのモデルに明け渡さない"))
    fields.append(_field("auto_start", "自動起動", "bool", default=False,
                         note="ControlDeck の起動時に立ち上げる"))
    return fields


def _llama() -> dict:
    return {
        "sections": [
            _section("model", [
                _field("alias", "モデル名", "text", default=""),
                _field("model_path", "GGUF ファイル", "path", default="",
                       note="登録はファイルから（GET /api/v1/models/register/inspect）"),
                _field("role", "役割", "select", choices=("llm", "embedding", "reranker"),
                       default="llm"),
            ]),
            _section("context", [
                # 上限は既存の保存 API（LlamaInstanceBody.ctx_size）の検証と同じ値。
                _field("ctx_size", "コンテキストの長さ", "int", default=4096,
                       minimum=0, maximum=1_048_576,
                       note="モデルの上限は GGUF のメタデータから（inspect の context_length）"),
            ]),
            _section("parallel", [
                _field("n_parallel", "同時に処理する数", "int", default=1, minimum=1, maximum=64),
            ]),
            _section("speed", [
                _field("n_gpu_layers", "GPU に載せる層", "int", default=999, minimum=0, maximum=999,
                       note="999 で全層。VRAM 不足時は下げる"),
                _field("image_min_tokens", "画像 1 枚の最小トークン", "int", default=1024,
                       minimum=0, maximum=16_384, expert=True),
            ]),
            _section("launch", _lifecycle_fields(on_demand=False)),
            _section("expert", [
                _field("port", "ポート", "int", default=8080, minimum=1024, maximum=65535, expert=True),
                _field("mmproj_path", "画像の部品（mmproj）", "path", default="", expert=True),
                _field("options", "詳しい引数", "ref", default="",
                       note="GET /api/v1/models/options-spec（既存の引数一覧）", expert=True),
            ]),
        ],
        "endpoints": {"instances": "/api/v1/models/llama/instances"},
    }


def _radiance() -> dict:
    from app.models_mgmt import radiance

    return {
        "sections": [
            _section("model", [
                _field("alias", "モデル名", "text", default=""),
                _field("model_path", ".rad ファイル", "path", default="",
                       note="Radiance のモデルは .rad 1 ファイル（Sly の safetensors フォルダとは別形式）"),
            ]),
            _section("context", [
                _field("context", "コンテキストの長さ", "int", default=131072,
                       minimum=radiance._CONTEXT_MIN, maximum=radiance._CONTEXT_MAX,  # noqa: SLF001 - 検証の定数をそのまま使う
                       note=f"学習時の上限 {radiance.TRAINING_CONTEXT:,}"),
            ]),
            _section("parallel", [
                _field("max_num_seqs", "同時に処理する数", "int", default=4, minimum=1, maximum=32),
            ]),
            _section("speed", [
                _field("kv_cache", "KV キャッシュの形式", "select", choices=radiance.KV_TYPES,
                       default="bf16"),
                # 既定は「自動」（null）。保存 API の既定（speculative_tokens=None）と同じ。
                _field("speculative_tokens", "先読みトークン数", "int", default=None, minimum=0,
                       maximum=15, note="null（省略）で自動"),
                _field("gpu_headroom_mib", "GPU の空き確保量", "int", default=1024,
                       minimum=96, maximum=16384, expert=True),
                _field("reasoning_effort", "思考の既定", "select", choices=radiance.REASONING_EFFORTS,
                       default="", expert=True),
                _field("thinking_budget", "思考の上限", "int", default=0, minimum=0,
                       maximum=262144, expert=True),
                _field("memory_saver", "メモリセーバー", "bool", default=False, expert=True),
                _field("ram_offload", "RAM への退避", "bool", default=False, expert=True),
            ]),
            _section("launch", _lifecycle_fields(on_demand=True)),
            _section("expert", [
                _field("port", "ポート", "int", default=8100, minimum=8081, maximum=65535, expert=True),
            ]),
        ],
        "always_on": [],
        "endpoints": {"instances": "/api/v1/models/radiance/instances"},
    }


def _sly_radiance() -> dict:
    from app.models_mgmt import sly_radiance as sly

    return {
        "sections": [
            _section("model", [
                _field("alias", "モデル名", "text", default=""),
                _field("model_path", "モデルのフォルダ", "path", default="",
                       note="config.json のある safetensors のフォルダ（取得で揃う）"),
                _field("drafter_path", "先読みモデルのフォルダ", "path", default="", expert=True),
            ]),
            _section("context", [
                _field("context", "コンテキストの長さ", "int", default=131072,
                       minimum=sly._CONTEXT_MIN, maximum=sly._CONTEXT_MAX,  # noqa: SLF001 - 検証の定数をそのまま使う
                       note="先読みを使うときは 8 の余白が要る（起動時に引かれる）"),
            ]),
            _section("parallel", [
                _field("max_num_seqs", "同時に処理する数", "int", default=4, minimum=1,
                       maximum=sly.MAX_SEQS),
            ]),
            _section("speed", [
                _field("kv_cache", "KV キャッシュの形式", "select", choices=sly.KV_TYPES, default="fp8"),
                _field("speculative_tokens", "先読みトークン数", "int", default=7, minimum=0,
                       maximum=15, note="0 で先読みなし"),
                _field("prefill_chunk", "prefill の chunk", "select", choices=sly.CHUNKS, default=2048),
                _field("gpu_memory_utilization", "GPU のメモリの使用率", "float", default=0.98,
                       minimum=0.50, maximum=0.98,
                       note="共通の VRAM の余白を超えないよう起動時に調整される"),
                _field("quantization", "量子化の切り替え", "select", choices=sly.QUANT_SWITCHES,
                       default="", expert=True),
                _field("reasoning_effort", "思考の既定", "select", choices=sly.REASONING_EFFORTS,
                       default="", expert=True),
            ]),
            _section("launch", _lifecycle_fields(on_demand=True)),
            _section("expert", [
                _field("port", "ポート", "int", default=8100, minimum=8081, maximum=65535, expert=True),
                _field("engine_settings", "環境変数（RADIANCE_*・R4D_*）", "dict", default={},
                       note=f"{sly._MAX_ENV} 個まで", expert=True),  # noqa: SLF001
            ]),
        ],
        # 利用者の決まり（2026-11-28）: 常時オン。切る欄は出さない。
        "always_on": [{"id": "prefix_cache",
                       "note": "prefix cache は常にオンです（起動 argv に必ず付く。切る設定はありません）"}],
        "endpoints": {"instances": "/api/v1/models/sly-radiance/instances"},
    }


def _strata() -> dict:
    from app.models_mgmt import strata_catalog, strata_gateway

    families = _strata_families()
    # 量子化の選択肢は既定の系列（qwen）のもの。系列を変えたら目録から選び直す（既存の
    # GET /api/v1/models/strata/catalog が正）。ここに全系列の和を並べると、系列と合わない
    # 組を出して保存で断られる。
    family_ids = [f.id for f in families]
    # 既定は目録の先頭（今は qwen）。系列の並びが変わっても、選択肢と量子化の既定が
    # 食い違わないようにする。
    default_family = family_ids[0] if family_ids else "qwen"
    quants = tuple(q.id for q in strata_catalog.quantizations(default_family)) if families else ()
    # 既定はこれまでの iq2_xs（最も軽い推奨）。系列が変わって無い場合だけ先頭へ。
    default_model = "iq2_xs" if "iq2_xs" in quants else (quants[0] if quants else "")

    return {
        "sections": [
            _section("model", [
                _field("alias", "モデル名", "text", default=""),
                _field("family", "系列", "select", choices=family_ids,
                       default=default_family, note="GET /api/v1/models/strata/catalog（既存の目録）"),
                _field("model", "量子化", "select", choices=quants, default=default_model,
                       note="系列ごとの一覧は目録（strata_catalog）から"),
                _field("model_path", "既存の GGUF（任意）", "path", default="",
                       note="指定すれば初回起動の取得を飛ばす"),
            ]),
            _section("context", [
                # 保存 API は "<N>k" の文字列（4k〜1024k）。選択肢で表し、数値の上限は混ぜない。
                _field("context", "コンテキストの長さ", "select",
                       choices=[f"{value}k" for value in (4, 8, 16, 32, 64, 128, 256, 512, 1024)],
                       default="32k",
                       note=f"{strata_gateway._CONTEXT_MIN_K}k〜{strata_gateway._CONTEXT_MAX_K}k"),  # noqa: SLF001
            ]),
            _section("parallel", [
                _field("parallel", "同時に処理する数", "int", default=1, minimum=1, maximum=8),
            ]),
            _section("speed", [
                _field("kv", "KV キャッシュの形式", "select",
                       choices=("int8", "q4_0", "k8v4", "fp16"), default="int8"),
                _field("drafting", "先読み", "select", choices=("auto", "mtp", "dflash2", "off"),
                       default="auto", note="Inkling は auto か off だけ"),
                _field("vision", "画像の読み取り", "select", choices=("on", "off", "gpu", "cpu"),
                       default="on"),
                _field("kv_resident", "KV の常駐トークン", "int", default=None, minimum=4096,
                       maximum=1048576, step=1024, expert=True,
                       note="null で既定。1024 の倍数。Qwen 系だけ"),
                _field("ram_keep_gib", "RAM に残す量", "float", default=None, minimum=0,
                       maximum=256, expert=True, note="Qwen 系だけ"),
            ]),
            _section("launch", _lifecycle_fields(on_demand=False)),
            _section("expert", [
                _field("port", "ポート", "int", default=8095, minimum=8081, maximum=65535, expert=True),
                _field("reasoning_effort", "思考の既定", "select",
                       choices=("", "low", "medium", "xhigh"), default="", expert=True,
                       note="思考を持たない系列では指定できない"),
                _field("thinking_budget", "思考の上限", "int", default=0, minimum=0,
                       maximum=262144, expert=True),
            ]),
        ],
        "always_on": [],
        "endpoints": {"instances": "/api/v1/models/strata/instances"},
    }


def _strata_families():
    from app.models_mgmt import strata_catalog

    nvidia = strata_catalog.has_nvidia_gpu()      # sysfs の走査は 1 回だけ
    return [f for f in strata_catalog.families() if strata_catalog.visible(f.id, nvidia)]


def _maya() -> dict:
    from app.features.router import MayaInstanceSettingsBody
    from app.models_mgmt import maya_s

    # 選択肢・範囲は Maya の保存 API（MayaInstanceSettingsBody）から読む。Maya には登録用の
    # 別の API（MayaRegisterBody）があり、alias は目録の setup_id から Maya 自身が決めるので
    # 欄に出さない（欄の id は実際に渡せる引数名だけ、という決まりを守る）。
    effects = _literal_choices(MayaInstanceSettingsBody.model_fields["reasoning_effort"].annotation)
    defaults = maya_s.get_settings()
    stored_effort = defaults.get("reasoning_effort")
    effective_effort = stored_effort if stored_effort in effects else (
        "high" if "high" in effects else (effects[0] if effects else ""))
    try:
        thinking_budget = int(defaults.get("thinking_budget") or 0)
    except (TypeError, ValueError):
        thinking_budget = 0
    return {
        "sections": [
            _section("model", [
                _field("setup_id", "モデル（目録から）", "ref", default="",
                       note="GET /api/v1/features/maya-s/catalog の setup_id。別名は Maya が決める"),
                _field("folder", "モデルのフォルダ", "path", default="",
                       note="取得済みのファイルを置く場所"),
            ]),
            _section("context", [
                # 登録（MayaRegisterBody）と保存（MayaInstanceSettingsBody）の両方が受け付ける値。
                # 既定は Maya 本体の既定（maya_s.get_settings の 8192）に合わせる。
                _field("context", "コンテキストの長さ", "int", default=8192, minimum=8192,
                       maximum=1048576),
            ]),
            _section("parallel", [
                _field("parallel", "同時に処理する数", "int", default=1, minimum=1, maximum=4,
                       note="Maya の既定は 1（Hy4 は setup から固定）"),
            ]),
            _section("speed", [
                # 温度・サンプリングなど family ごとの値は Maya 本体が持つので、ここには
                # 書かない（推測しない）。既存の設定 API を参照する。
                _field("instance_settings", "速度・思考・サンプリング", "ref", default="",
                       note="GET /api/v1/features/maya-s/models/{alias}/settings"
                            "（family に応じた選択肢は既存の保存 API が検証する）"),
            ]),
            _section("launch", _lifecycle_fields(on_demand=True)),
            _section("expert", [
                _field("gpu_index", "GPU 番号", "int", default=0, minimum=0, maximum=15, expert=True),
                _field("reasoning_effort", "思考の既定", "select", choices=tuple(effects),
                       default=effective_effort, expert=True),
                _field("thinking_budget", "思考の上限", "int",
                       default=thinking_budget, minimum=0,
                       maximum=131072, expert=True),
            ]),
        ],
        "always_on": [],
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
