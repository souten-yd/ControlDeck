"""Models 画面の共通 API（目録・取得・登録）のリクエストの型。

docs/PLAN-model-catalog.md「run3 共通バックエンド API」の契約に対応する。
ランタイム別の登録・設定の本体（SlyRadianceModelBody など）は従来どおり
models_mgmt/router.py にある（既存 API は動かさない）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 共通取得に対応する全 5 ランタイム（HF の取得 Job を停止・再開・取り消しできる道）。
# Strata はランタイム自身が取る既存の経路も残す（互換）。HF から取った分はここから登録する。
AcquireRuntime = Literal["llama", "radiance", "sly-radiance", "strata", "maya"]


class AcquireRegister(BaseModel):
    """取得が終わった後の登録（省略可）。使う欄は runtime ごとに違う。

    - llama: alias・role・endpoint_id・port・ctx_size・n_parallel（llama.save_instance）
    - sly-radiance: alias・port・context・kv_cache・max_num_seqs・quantization・
      gpu_memory_utilization・prefill_chunk・reasoning_effort（sly_radiance.save_instance。
      prefix cache は常にオンで、切る欄は無い）
    - radiance: alias・port・context（.rad をそのまま既存の radiance.save_instance へ）。
    - strata: alias・port・context・family・model（取得した分割 GGUF のフォルダを既存の
      strata_gateway.save_instance へ。ランタイム自身が取る既存の経路は残す）。
    - maya: 登録は Maya 本体の目録（probe）から行うので、ここでは alias だけ予約できない
      （使わない。省略してダウンロードのみ）。
    """

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    role: Literal["llm", "embedding", "reranker"] | None = None
    endpoint_id: str | None = Field(default=None, max_length=128)
    port: int | None = Field(default=None, ge=1024, le=65535)
    # 以下は Sly-Radiance の save_instance が受け付ける範囲に合わせる（受け付けない値を
    # ここで 422 にする。数 GB の取得を終えた後に登録で初めて失敗するのを防ぐ）。
    context: int | None = Field(default=None, ge=4096, le=262_144)
    ctx_size: int | None = Field(default=None, ge=0, le=1_048_576)
    n_parallel: int | None = Field(default=None, ge=1, le=64)
    max_num_seqs: int | None = Field(default=None, ge=1, le=8)
    kv_cache: str | None = Field(default=None, max_length=16)
    quantization: str | None = Field(default=None, max_length=32)
    gpu_memory_utilization: float | None = Field(default=None, ge=0.50, le=0.98)
    prefill_chunk: int | None = None
    reasoning_effort: str | None = Field(default=None, max_length=16)
    # Strata の既存の保存 API と同じ引数名（目録の family と量子化 id を渡す）。
    family: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9._-]+$")
    model: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9._-]+$")

    @field_validator("kv_cache")
    @classmethod
    def _sly_kv_cache(cls, value: str | None) -> str | None:
        from app.models_mgmt import sly_radiance

        if value is not None and value not in sly_radiance.KV_TYPES:
            raise ValueError(f"KV 形式は {', '.join(sly_radiance.KV_TYPES)} のどれかです")
        return value

    @field_validator("prefill_chunk")
    @classmethod
    def _sly_prefill_chunk(cls, value: int | None) -> int | None:
        from app.models_mgmt import sly_radiance

        if value is not None and value not in sly_radiance.CHUNKS:
            raise ValueError(f"prefill の chunk は {', '.join(map(str, sly_radiance.CHUNKS))} のどれかです")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def _sly_reasoning_effort(cls, value: str | None) -> str | None:
        from app.models_mgmt import sly_radiance

        if value is not None and value not in sly_radiance.REASONING_EFFORTS:
            raise ValueError("思考の既定が不正です")
        return value

    @field_validator("quantization")
    @classmethod
    def _sly_quantization(cls, value: str | None) -> str | None:
        from app.models_mgmt import sly_radiance

        if value is not None and value not in sly_radiance.QUANT_SWITCHES:
            raise ValueError("量子化の切り替えが不正です")
        return value


# 登録の adapter が実際に読む欄（hf._register_llama・hf._register_sly_radiance）。
# ここに無い欄は黙って捨てずに 422 にする（UI が欄を出したのに効かない、を防ぐ）。
_REGISTER_FIELDS: dict[str, frozenset[str]] = {
    "llama": frozenset({"alias", "role", "endpoint_id", "port", "ctx_size", "n_parallel"}),
    "radiance": frozenset({"alias", "port", "context"}),
    "sly-radiance": frozenset({
        "alias", "port", "context", "kv_cache", "max_num_seqs", "quantization",
        "gpu_memory_utilization", "prefill_chunk", "reasoning_effort",
    }),
    "strata": frozenset({"alias", "port", "context", "family", "model"}),
    "maya": frozenset(),  # 登録は Maya 本体の目録から（登録の欄は受け付けない）
}


class AcquireBody(BaseModel):
    """POST /api/v1/models/acquire-jobs の本体。

    大きさ（expected_bytes）は含めない: Job の開始時に HF の API から読む
    （呼び出し側・目録のどちらも推測値を渡さない）。
    """

    # JSON の鍵は "register"（hf.download の引数名と同じ）。pydantic v2 が
    # BaseModel.register（メタクラスの側）と重なるため、Python 側の名前だけ変える。
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    runtime: AcquireRuntime
    entry_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._/-]+$")
    library_id: str = Field(default="", max_length=64)
    # 省略時は repo の最上位ファイル全部（Sly・Maya のフォルダ形式のモデル）。
    files: list[str] = Field(default_factory=list, max_length=64)
    register_model: AcquireRegister | None = Field(default=None, alias="register")

    @model_validator(mode="after")
    def _register_fields_fit_runtime(self) -> "AcquireBody":
        if self.register_model is None:
            return self
        if self.runtime == "maya":
            raise ValueError("Maya の登録は Maya 本体の目録から行ってください（register を省略）")
        allowed = _REGISTER_FIELDS[self.runtime]
        used = {key for key, value in self.register_model.model_dump().items()
                if value is not None}
        extra = sorted(used - allowed)
        if extra:
            raise ValueError(
                f"{self.runtime} の登録では使えない欄です: {', '.join(extra)}"
                f"（使える欄: {', '.join(sorted(allowed))}）"
            )
        return self
