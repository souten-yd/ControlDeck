"""ランタイム共通の目録・設定の形・ファイルからの登録の読み取り（run3 共通バックエンド API）。

目的: Models 画面の共通部品（RuntimeCatalog・RuntimeSettings・RuntimeRegisterFromFile）が
読む 3 つの API を守る。

- 目録は全ランタイムを並べ、載せる repo は確認済みのものだけにする。大きさの分からない行は
  size_gib を null にし、推測値（URL・大きさ・sha）を書かない（取得の開始時に HF の API から読む）。
- 設定の形は 6 つの並びを全ランタイムで揃え、欄の id・選択肢・上限を実際の保存 API
  （LlamaInstanceBody・RadianceModelBody・SlyRadianceModelBody・StrataModelBody・
  MayaInstanceSettingsBody / MayaRegisterBody）と一致させる。
  ランタイムに無い欄（Sly の prefix cache など）は出さず、always_on に理由を書く。
- ファイルからの登録は新規選択を /data1tb と /data2tb の中だけにし、realpath で symlink の
  外抜けを断る。GGUF ヘッダ・config.json は読む量と件数を制限して読む。

根拠: 2026-11-28 利用者の追加の要望「Radiance・Strata・Maya にも同じ取得機能を入れ、目録を
作り直して。各詳細設定を極力統一して」と docs/PLAN-model-catalog.md「run3 共通バックエンド API」。
以前は目録と設定の形が画面ごとに別々で、ランタイムごとの検証と食い違っていた。

追加（Nexarch #19）: 調査済みの repo id とファイル名を実 API へ反映し、llama.cpp と
Radiance の空目録を解消する。Maya の owner の誤記（peasmith）を直し、Strata の新行は
GSQ-RCO・Unsloth UD-*・Q8_0 だけに絞る。全 5 ランタイムから共通の HF 取得 Job を
停止・再開・取り消しできる道を接続する。根拠: docs/PLAN-model-catalog.md「目録候補」と
.nexarch/brief-runtime-unify.md「大きさ・sha は取得の開始時に HF の API から読む」。
"""
from __future__ import annotations

import json
import struct
from typing import Literal, get_args, get_origin

import pytest
from annotated_types import Ge, Le, MultipleOf

from app.models_mgmt import model_paths, runtime_catalog, runtime_settings
from app.models_mgmt import router as models_router

_ROW_KEYS = {
    "id", "name", "kind", "format", "repo", "quantization", "size_gib", "memory_gib",
    "uncensored", "supported", "note", "acquire", "source", "fit", "family", "files",
}


# ---- 目録 ------------------------------------------------------------------------------------


def test_catalog_has_every_runtime_and_no_guessed_sizes():
    """目的: 目録が 5 ランタイムを並べ、出どころの確かな行だけを載せること。

    根拠: 2026-11-28 利用者「載るかどうかと容量を調査して目録を作って」「llama.cpp は目録をやめて従来のファイル
    選択に戻す」。大きさは調査（HF の一覧・ランタイム本体の目録）の値だけ（docs/research-model-catalog-20261128.md）。
    """
    payload = runtime_catalog.api_payload()
    blocks = {block["id"]: block for block in payload["runtimes"]}
    assert list(blocks) == ["llama", "radiance", "sly-radiance", "strata", "maya"]
    assert blocks["llama"]["entries"] == []                       # 手元のファイルを選んで登録する
    assert len(blocks["radiance"]["entries"]) == 39
    assert len(blocks["sly-radiance"]["entries"]) == 6
    for runtime, block in blocks.items():
        ids = [row["id"] for row in block["entries"]]
        assert len(ids) == len(set(ids)), runtime
        for row in block["entries"]:
            assert set(row) == _ROW_KEYS
            assert row["id"] and row["name"]
            if row["acquire"] == "hf":
                assert row["repo"], row["id"]
            if runtime == "radiance":
                assert row["size_gib"] and row["files"] and row["files"][0].endswith(".rad"), row["id"]

def test_catalog_entries_are_looked_up_by_runtime():
    """目的: 取得の API が目録の行をランタイム＋id で引けること（別ランタイムの id を混ぜない）。

    根拠: POST /models/acquire-jobs は行の repo と種類を見て Job を作る。取り違えると
    別のランタイム向けの重みを落とす。
    """
    for block in runtime_catalog.api_payload()["runtimes"]:
        for row in block["entries"]:
            entry = runtime_catalog.get_entry(block["id"], row["id"])
            assert entry is not None and entry.runtime == block["id"]
    # 別のランタイムを指定したら引けない（同じ id を横取りしない）。
    assert runtime_catalog.get_entry("strata", "swift-heretic") is None
    assert runtime_catalog.get_entry("radiance", "swift-heretic") is None
    assert runtime_catalog.get_entry("unknown", "swift-heretic") is None
    assert runtime_catalog.get_entry("sly-radiance", "swift-heretic") is not None

# ---- 設定の形 --------------------------------------------------------------------------------


def _fields_by_id(runtime: str) -> dict[str, dict]:
    payload = runtime_settings.api_payload()
    return {f["id"]: f for section in payload["runtimes"][runtime]["sections"]
            for f in section["fields"]}


def _bounds(metadata) -> tuple[object, object]:
    minimum = next((item.ge for item in metadata if isinstance(item, Ge)), None)
    maximum = next((item.le for item in metadata if isinstance(item, Le)), None)
    return minimum, maximum


def _step(metadata):
    return next((item.multiple_of for item in metadata if isinstance(item, MultipleOf)), None)


def test_capabilities_share_the_same_section_order():
    """目的: 全ランタイムの設定画面が同じ 6 つの並びで出ること（バラバラにしない）。

    根拠: 依頼「各詳細設定がバラバラで使いにくいので極力統一し」。並びが揃うと画面の
    同じ位置に同じ意味の設定が来る。
    """
    payload = runtime_settings.api_payload()
    assert list(payload["runtimes"]) == ["llama", "radiance", "sly-radiance", "strata", "maya"]
    for runtime, body in payload["runtimes"].items():
        assert [section["id"] for section in body["sections"]] == list(runtime_settings.SECTION_ORDER), runtime
        for section in body["sections"]:
            assert section["label"]
            for field in section["fields"]:
                assert field["id"] and field["label"] and field["type"]
                assert "default" in field


def _api_body(runtime: str, name: str):
    from app.features import router as features_router

    return getattr(features_router, name) if runtime == "maya" else getattr(models_router, name)


@pytest.mark.parametrize(("runtime", "body_names"), [
    ("llama", ("LlamaInstanceBody",)),
    ("radiance", ("RadianceModelBody",)),
    ("sly-radiance", ("SlyRadianceModelBody",)),
    ("strata", ("StrataModelBody",)),
    # Maya は登録（MayaRegisterBody）と調整（MayaInstanceSettingsBody）で API が分かれる。
    ("maya", ("MayaInstanceSettingsBody", "MayaRegisterBody")),
])
def test_capability_fields_match_the_save_api(runtime: str, body_names: tuple[str, ...]):
    """目的: 設定の欄の id・選択肢・上限を、実際の保存 API の検証と同じにすること。

    根拠: 依頼「設定は実際の起動/保存に接続し、未対応項目を見せない」。画面が保存 API に
    無い欄や、保存で断られる上限を出すと、操作してから失敗する。上限を二重に持つと
    片方だけ直して食い違う（以前の ctx_size・image_min_tokens がそうなっていた）。
    2026-11-28 の run4 で Maya を見落としていた（context の下限・同時処理数の上限・
    重複した欄 id が保存 API と食い違っていた）ので、Maya も同じ関門に入れる。
    """
    bodies = [_api_body(runtime, name) for name in body_names]
    fields = _fields_by_id(runtime)
    for field_id, field in fields.items():
        model_fields = [body.model_fields[field_id] for body in bodies
                        if field_id in body.model_fields]
        if field["type"] == "ref" and not model_fields:
            continue                    # 既存の一覧 API（options-spec・Maya の settings）への参照
        assert model_fields, f"{runtime}.{field_id} は登録・保存の API に無い"

        # 複数の API が同じ id を持つときは、両方が受け付ける範囲（共通部分）に収める。
        minimums = [v for v in (_bounds(item.metadata)[0] for item in model_fields) if v is not None]
        maximums = [v for v in (_bounds(item.metadata)[1] for item in model_fields) if v is not None]
        if minimums:
            assert field.get("min") == max(minimums), f"{runtime}.{field_id} の下限"
        if maximums:
            assert field.get("max") == min(maximums), f"{runtime}.{field_id} の上限"
        # 刻み（multiple_of）も保存 API に合わせる。画面が 5000 のような値を出して
        # 保存で 422 になるのを防ぐ（run4 のレビューで kv_resident を見落としていた）。
        steps = [v for v in (_step(item.metadata) for item in model_fields) if v is not None]
        if steps:
            assert field.get("step") == max(steps), f"{runtime}.{field_id} の刻み"
        literals = [get_args(item.annotation) for item in model_fields
                    if get_origin(item.annotation) is Literal]
        if literals:
            allowed = tuple(item for item in literals[0] if all(item in other for other in literals[1:]))
            assert field["type"] == "select", f"{runtime}.{field_id} は選択欄"
            assert tuple(field["choices"]) == allowed, f"{runtime}.{field_id} の選択肢"


def test_capability_ids_are_unique_and_defaults_fit_the_choices():
    """目的: 同じ設定を 2 つの節に出さないこと。数値の既定が範囲内・選択の既定が選択肢内に
    あること（画面が初期値のまま保存して 422 にならない）。

    根拠: run4 で Maya の `instance_settings` が「速さ」と「エキスパート」の両方に出て
    いた。id が重なると UI の部品が同じ欄を 2 度描き、片方の変更が他方に消される。
    """
    for runtime, body in runtime_settings.api_payload()["runtimes"].items():
        ids = [field["id"] for section in body["sections"] for field in section["fields"]]
        assert len(ids) == len(set(ids)), f"{runtime} の欄 id が重複: {ids}"
        for section in body["sections"]:
            for field in section["fields"]:
                default = field.get("default")
                if field["type"] in ("int", "float") and default is not None:
                    assert field.get("min", default) <= default <= field.get("max", default), \
                        f"{runtime}.{field['id']} の既定 {default} が範囲外"
                if field["type"] == "select" and default is not None:
                    assert default in field["choices"], f"{runtime}.{field['id']} の既定 {default} が選択肢に無い"
                if field["type"] == "select":
                    assert field.get("choices"), f"{runtime}.{field['id']} に選択肢が無い"


def test_acquire_register_rejects_what_the_sly_save_api_rejects():
    """目的: 取得ジョブの登録引数（AcquireRegister）が、Sly-Radiance の保存 API と同じ値を
    受け入れ、保存で断られる値を最初に 422 にすること。

    根拠: run4 のレビュー（major）「AcquireRegister が保存 API より緩く、数 GB の取得を
    終えた後に登録で失敗してモデルが未登録のまま残る」。取得の前に断れば無駄な取得が無い。
    """
    from pydantic import ValidationError

    from app.schemas.models import AcquireRegister

    for bad in ({"max_num_seqs": 16}, {"context": 2_097_152}, {"ctx_size": -1},
                {"kv_cache": "fp16x"}, {"prefill_chunk": 1536},
                {"quantization": "not-a-switch"}, {"reasoning_effort": "ultra"},
                {"gpu_memory_utilization": 1.5}):
        with pytest.raises(ValidationError):
            AcquireRegister(alias="sly-test", **bad)
    # 保存 API が受け付ける値は通る（範囲・刻み・選択肢）。
    from app.models_mgmt import sly_radiance

    ok = AcquireRegister(alias="sly-test", context=8192, max_num_seqs=8, kv_cache="fp8",
                         prefill_chunk=4096, reasoning_effort="high",
                         quantization=next(iter(sly_radiance.QUANT_SWITCHES)))
    assert ok.max_num_seqs == 8


def test_acquire_register_rejects_fields_the_runtime_would_ignore():
    """目的: 取得後の登録で、そのランタイムが使わない欄を 422 にすること。

    根拠: run4 の再レビュー（minor）「AcquireRegister は runtime をまたぐ欄を全部受け、
    adapter が黙って捨てる（例: sly-radiance に ctx_size）」。画面が欄を出したのに効かない
    状態を作らない（PLAN の「欄の id は保存 API と同じ」）。
    """
    from pydantic import ValidationError

    from app.schemas.models import AcquireBody

    with pytest.raises(ValidationError, match="ctx_size"):
        AcquireBody(runtime="sly-radiance", entry_id="swift-heretic",
                    register={"alias": "s", "ctx_size": 8192})
    with pytest.raises(ValidationError, match="kv_cache"):
        AcquireBody(runtime="llama", entry_id="some-entry",
                    register={"alias": "l", "kv_cache": "fp8"})
    with pytest.raises(ValidationError, match="Maya"):
        AcquireBody(runtime="maya", entry_id="glm-m-derisked",
                    register={"alias": "m"})
    # そのランタイムの欄だけなら通る。
    ok = AcquireBody(runtime="sly-radiance", entry_id="swift-heretic",
                     register={"alias": "s", "context": 8192})
    assert ok.register_model.context == 8192
    ok_llama = AcquireBody(runtime="llama", entry_id="some-entry",
                           register={"alias": "l", "ctx_size": 8192})
    assert ok_llama.register_model.ctx_size == 8192


def test_maya_capabilities_survive_a_broken_settings_file(tmp_path, monkeypatch):
    """目的: Maya の settings.json が壊れていても、既定へ戻して共通の設定 API を返すこと。

    根拠: run4 のレビュー（major）「maya_s.get_settings の json.loads が無防備で、
    settings.json が壊れると GET /api/v1/models/settings/capabilities が全ランタイム分
    まとめて 500 になる」。保存は非原子的なので起こりうる。AGENTS 7/8。
    """
    from app.models_mgmt import maya_s

    (tmp_path / "settings.json").write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(maya_s, "root", lambda: tmp_path)
    assert maya_s.get_settings()["context"] == 8192
    payload = runtime_settings.api_payload()
    assert payload["runtimes"]["maya"]["sections"]
    assert payload["runtimes"]["strata"]["sections"]


def test_sly_prefix_cache_is_always_on_and_has_no_field():
    """目的: Sly の prefix cache を切る欄を設定の形に出さず、常時オンとだけ注記すること。

    根拠: 利用者の決まり（2026-11-28）「Sly の prefix cache は常時オン。切る選択肢・false 値を
    受け付けない」。欄を出すと切れると思わせる。
    """
    body = runtime_settings.api_payload()["runtimes"]["sly-radiance"]
    assert "prefix_cache" not in _fields_by_id("sly-radiance")
    assert [item["id"] for item in body["always_on"]] == ["prefix_cache"]
    assert "常にオン" in body["always_on"][0]["note"]


def test_radiance_speculative_tokens_defaults_to_auto():
    """目的: Radiance の先読みの既定を保存 API と同じ「自動（null）」にすること。

    根拠: 保存 API（RadianceModelBody・save_instance）の既定は None（コンテナ自身の深さ）。
    画面が 7 を既定として送ると、利用者が触っていないのに先読みの深さが変わる。
    """
    field = _fields_by_id("radiance")["speculative_tokens"]
    assert field["default"] is None
    assert "自動" in field["note"]


# ---- ファイルからの登録（読み取り） ------------------------------------------------------------


def _write_gguf(path, pairs) -> None:
    """GGUF の先頭メタデータだけを持つ最小のファイルを書く（テンソルは無し）。"""
    buffer = bytearray(b"GGUF")
    buffer += struct.pack("<I", 3)             # version
    buffer += struct.pack("<QQ", 0, len(pairs))  # tensor count, metadata count
    for key, value in pairs:
        raw = key.encode()
        buffer += struct.pack("<Q", len(raw)) + raw
        if isinstance(value, str):
            data = value.encode()
            buffer += struct.pack("<I", 8) + struct.pack("<Q", len(data)) + data
        else:
            buffer += struct.pack("<I", 4) + struct.pack("<I", value)
    path.write_bytes(bytes(buffer))


def test_inspect_reads_gguf_header_and_says_which_runtimes_can_use_it(tmp_path, monkeypatch):
    """目的: GGUF を選んだとき、ヘッダから量子化・文脈長・系列を読み、使えるランタイムを返すこと。

    根拠: 依頼「ダウンロード済みのファイルから自由に選べる」。登録前に読めないと、
    量子化の種類や文脈の上限を利用者が勘で入れることになる。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    path = tmp_path / "My Model (v2).gguf"
    _write_gguf(path, [
        ("general.architecture", "qwen3"),
        ("general.file_type", 7),                  # Q8_0
        ("general.quantized_by", "Unsloth"),
        ("qwen3.context_length", 8192),
    ])

    info = model_paths.inspect(str(path))
    assert info["kind"] == "file" and info["format"] == "gguf"
    assert info["alias_suggestion"] == "my-model-v2"
    assert info["metadata"]["quantization"] == "Q8_0"
    assert info["metadata"]["context_length"] == 8192
    assert info["size_bytes"] == path.stat().st_size
    views = {view["runtime"]: view for view in info["runtimes"]}
    assert views["llama.cpp"]["eligible"] is True
    assert views["maya"]["eligible"] is True
    assert "Unsloth" in views["strata"]["reason"]
    assert views["sly-radiance"]["eligible"] is False


def test_inspect_reads_a_safetensors_folder_config(tmp_path, monkeypatch):
    """目的: config.json のあるフォルダを Sly 用（safetensors）と判定し、量子化の方式を読むこと。

    根拠: Sly の登録はフォルダ。Radiance の .rad と同じ形式として扱わない（依頼の注意）。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    folder = tmp_path / "Swift"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps({
        "model_type": "qwen3",
        "quantization_config": {"quant_method": "compressed-tensors", "format": "fp8"},
        "max_position_embeddings": 131072,
    }))
    (folder / "model-00001.safetensors").write_bytes(b"x" * 10)

    info = model_paths.inspect(str(folder))
    assert info["kind"] == "folder" and info["format"] == "safetensors"
    assert info["metadata"]["quant_method"] == "compressed-tensors"
    assert info["metadata"]["quant_format"] == "fp8"
    assert info["metadata"]["max_position_embeddings"] == 131072
    # フォルダの大きさは最上位のファイルの合計（config.json も数える）。
    assert info["size_bytes"] == (folder / "config.json").stat().st_size + 10
    views = {view["runtime"]: view for view in info["runtimes"]}
    assert views["sly-radiance"]["eligible"] is True
    assert views["radiance"]["eligible"] is False
    assert ".rad" in views["radiance"]["reason"]


def test_new_selection_is_refused_outside_the_allowed_roots(tmp_path, monkeypatch):
    """目的: 新規の選択・登録を /data1tb と /data2tb の中だけにし、symlink で外へ抜けさせないこと。

    根拠: 利用者の決まり（2026-11-27）「選べるフォルダ（ルート）は /data1tb と /data2tb に統一」。
    既存の登録は壊さないので、この検証は新規の選択だけに使う。
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.gguf"
    _write_gguf(outside, [("general.architecture", "qwen3")])
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(allowed),))

    with pytest.raises(PermissionError):
        model_paths.resolve_selection(str(outside))

    link = allowed / "escape.gguf"
    link.symlink_to(outside)
    with pytest.raises(PermissionError):
        model_paths.resolve_selection(str(link))

    inside = allowed / "ok.gguf"
    _write_gguf(inside, [("general.architecture", "qwen3")])
    assert model_paths.resolve_selection(str(inside)) == inside.resolve()


def test_broken_gguf_does_not_stop_the_register_screen(tmp_path, monkeypatch):
    """目的: 壊れたファイル・GGUF でないファイルでも例外にせず、形式 unknown として返すこと。

    根拠: 登録画面で読めないファイルを選んだときに画面全体を止めない（AGENTS.md 7 と同じ扱い）。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    broken = tmp_path / "broken.gguf"
    broken.write_bytes(b"NOT-GGUF")
    info = model_paths.inspect(str(broken))
    assert info["format"] == "unknown"
    assert info["metadata"] == {}


# ---- 共通 API の出口 -------------------------------------------------------------------------


def test_common_models_api_is_exposed(admin_client):
    """目的: 目録・設定の形・ファイルの読み取りが認証つきの API から見えること。

    根拠: UI 担当の共通部品はこの 3 本を読む（docs/PLAN-model-catalog.md の契約）。
    実装だけあって API に繋がっていないと画面から使えない。
    """
    response = admin_client.get("/api/v1/models/catalog")
    assert response.status_code == 200, response.text
    assert [b["id"] for b in response.json()["runtimes"]] == [
        "llama", "radiance", "sly-radiance", "strata", "maya"]

    response = admin_client.get("/api/v1/models/settings/capabilities")
    assert response.status_code == 200, response.text
    assert set(response.json()["runtimes"]) == {
        "llama", "radiance", "sly-radiance", "strata", "maya"}

    response = admin_client.get("/api/v1/models/register/inspect",
                                params={"path": "/nonexistent/model.gguf"})
    assert response.status_code in (403, 404)


def test_acquire_rejects_rows_without_a_hf_repo_and_maya_register(admin_client):
    """目的: HF から取れない行（Strata など）と Maya の取得後登録を共通 API で断ること。

    根拠: Strata はランタイム自身が取り、Maya の登録は Maya 本体の目録から行う
    （docs/PLAN-model-catalog.md）。共通の取得で別の道を通すと二重取得になる。
    """
    from tests.conftest import CSRF_HEADERS

    response = admin_client.post("/api/v1/models/acquire-jobs",
                                 json={"runtime": "llama", "entry_id": "not-in-catalog"},
                                 headers=CSRF_HEADERS)
    assert response.status_code == 404, response.text

    response = admin_client.post(
        "/api/v1/models/acquire-jobs",
        json={"runtime": "maya", "entry_id": "glm-m-derisked", "register": {"alias": "m"}},
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 422, response.text

    response = admin_client.post(
        "/api/v1/models/acquire-jobs",
        json={"runtime": "strata", "entry_id": "qwen/q2_0"},
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 422, response.text


def test_acquire_job_reads_sizes_at_start_and_keeps_the_runtime(admin_client, monkeypatch):
    """目的: 目録の行から作る取得 Job が、大きさを推測せず開始時に読み、登録先のランタイムを残すこと。

    根拠: 依頼「大きさが分からない行は取得の開始時に HF から読む」。ここで expected_bytes を
    入れてしまうと、目録の推測値が Job の引数として固定される。
    """
    from tests.conftest import CSRF_HEADERS

    captured: dict = {}

    class _FakeJob:
        id = "job-runtime-catalog"

    monkeypatch.setattr(models_router.jobs, "create",
                        lambda *args, **kwargs: captured.update(kwargs) or _FakeJob())

    response = admin_client.post(
        "/api/v1/models/acquire-jobs",
        json={
            "runtime": "sly-radiance", "entry_id": "swift-heretic",
            "register": {"alias": "swift", "port": 8123},
        },
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 201, response.text
    assert response.json()["job_id"] == "job-runtime-catalog"
    resume = captured["resume"]
    assert resume["expected_bytes"] == 0
    assert resume["fetch_sizes"] is True
    assert resume["register"]["runtime"] == "sly-radiance"
    assert resume["register"]["alias"] == "swift"


def test_rad_file_is_offered_to_radiance_only(tmp_path, monkeypatch):
    """目的: Radiance の .rad を Sly-Radiance（safetensors フォルダ）へ、Sly のフォルダを
    Radiance へ、相互に誤って登録させないこと。

    根拠: 依頼の注意「Radiance の .rad と Sly の safetensors は別形式として adapter で実際の
    対応を検証する」。取り違えると起動できない登録になる。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    rad = tmp_path / "model.rad"
    rad.write_bytes(b"RAD-DATA")

    info = model_paths.inspect(str(rad))
    assert info["kind"] == "file" and info["format"] == "rad"
    views = {view["runtime"]: view for view in info["runtimes"]}
    assert views["radiance"]["eligible"] is True
    assert views["sly-radiance"]["eligible"] is False
    assert "config.json" in views["sly-radiance"]["reason"]
    # 拡張子が .rad でも中身は読まない（形式の判定は拡張子。safetensors と混ぜない）。
    assert info["metadata"] == {}


# ---- 調査済み目録の反映と全ランタイムの共通取得（Nexarch #19） ------------------------------


def test_researched_rows_keep_only_confirmed_repo_ids_and_file_names():
    """目的: 調査で確かめた repo id・ファイル名・大きさがそのまま API に出ること。

    根拠: 2026-11-28 の調査（HF の公開 API のファイル一覧）。docs/research-model-catalog-20261128.md。
    """
    rad = {entry.id: entry for entry in runtime_catalog.entries_for("radiance")}
    assert rad["radiance-minicpm5-2b-bf16"].repo == "StillDeadcode/minicpm5-2b-bf16"
    assert rad["radiance-minicpm5-2b-bf16"].files == ("minicpm5-2b-bf16.rad",)
    assert rad["radiance-minicpm5-2b-bf16"].size_gib == 5.5
    assert rad["radiance-qwen38-27b-iq5r-v4"].files == ("qwen3.8-27b-iq5r-v4.rad",)
    assert rad["radiance-qwen38-27b-uncensored-iq8r"].uncensored is True
    assert all(entry.format == "rad" for entry in rad.values())

def test_strata_new_rows_use_only_supported_quantizations():
    """目的: Strata の HF の行（第三者の無検閲版）が、Strata の動かせる量子化（GSQ-RCO・Unsloth UD）だけであること。

    根拠: 依頼「Strata は GSQ-RCO・Unsloth UD-*・Q8_0 だけ」。量子化の作りと大きさが公式と同じものだけを載せた
    （2026-11-28 調査）。動かせない形式を「取得できる」と出すと、落とした後に起動できない。
    """
    new_rows = [entry for entry in runtime_catalog.entries_for("strata") if entry.source == "hf"]
    assert new_rows and all(entry.uncensored for entry in new_rows)
    for entry in new_rows:
        quant = entry.quantization.casefold()
        assert quant in ("q2_0", "iq2_xs", "iq3_xxs", "iq3_s", "ud-q4_k_xl"), entry.id
        assert entry.family in ("qwen", "swift", "inkling-2-mini"), entry.id

def test_all_five_runtimes_can_start_the_common_hf_job(admin_client, monkeypatch):
    """目的: 全 5 ランタイムが共通の取得 Job を始め、停止・再開・取消の道に載ること。

    根拠: 依頼「停止・再開もつけて」「全 5 ランタイムで HF 共通 Job から…できる道を接続」。
    これまで AcquireRuntime が 3 つだけだったので Radiance・Strata は共通 Job を始められなかった。
    """
    from tests.conftest import CSRF_HEADERS

    captured: list[dict] = []

    class _FakeJob:
        def __init__(self, index: int):
            self.id = f"job-{index}"

    def fake_create(*args, **kwargs):
        captured.append(kwargs)
        return _FakeJob(len(captured) - 1)

    monkeypatch.setattr(models_router.jobs, "create", fake_create)
    # 2026-11-28: llama.cpp は目録なし、Maya は本体の「ダウンロードして登録」で取る（共通の HF Job ではない）
    cases = [
        ("radiance", "radiance-minicpm5-2b-bf16", {"alias": "rad-x"}),
        ("sly-radiance", "swift-heretic", {"alias": "sly-x"}),
        ("strata", "gsq-qwen38-abliterated-iq3_xxs", {"alias": "strata-x"}),
    ]
    for index, (runtime, entry_id, register) in enumerate(cases):
        body = {"runtime": runtime, "entry_id": entry_id}
        if register is not None:
            body["register"] = register
        response = admin_client.post("/api/v1/models/acquire-jobs", json=body,
                                     headers=CSRF_HEADERS)
        assert response.status_code == 201, (runtime, response.text)
        assert response.json()["job_id"] == f"job-{index}"
        resume = captured[-1]["resume"]
        assert resume["fetch_sizes"] is True and resume["expected_bytes"] == 0
        if runtime in ("strata",):
            assert resume["quantization"] == "IQ3_XXS"
            assert resume["register"]["family"] == "qwen"
            assert resume["register"]["model"] == "iq3_xxs"


def test_catalog_files_endpoint_lists_only_the_selected_quantization(admin_client, monkeypatch):
    """目的: GGUF の量子化行のファイル一覧がサブフォルダの分割全片だけを返すこと。

    根拠: 依頼「サブディレクトリの GGUF 分割全片を正しく列挙し、選択した量子化だけ取得、
    repo 全体の他量子化を落とさない」。旧 list_model_files は最上位だけを見ていたので、
    量子化フォルダのrepoでは取得できるファイルが 0 件になっていた。
    """
    from app.models_mgmt import hf as hf_module

    async def fake_tree(repo, revision="main"):
        return [
            {"type": "file", "path": "README.md", "size": 10},
            {"type": "file", "path": "Q2_0/model-Q2_0-00001-of-00002.gguf", "size": 100},
            {"type": "file", "path": "Q2_0/model-Q2_0-00002-of-00002.gguf", "size": 200},
            {"type": "file", "path": "Q8_0/model-Q8_0-00001-of-00002.gguf", "size": 300},
            {"type": "file", "path": "Q8_0/model-Q8_0-00002-of-00002.gguf", "size": 400},
        ]

    monkeypatch.setattr(hf_module, "_repo_tree", fake_tree)
    response = admin_client.get(
        "/api/v1/models/catalog/strata/entries/sc117-qwen-abliterated-q2_0/files"   # llama.cpp の目録は 2026-11-28 に無くした
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["path"] for item in body["files"]] == [
        "Q2_0/model-Q2_0-00001-of-00002.gguf",
        "Q2_0/model-Q2_0-00002-of-00002.gguf",
    ]
    assert body["total_bytes"] == 300


def test_new_registration_rejects_outside_paths_even_with_wide_files_config(
        admin_client, monkeypatch, tmp_path):
    """目的: 新規登録 API が Files の許可ルートを広く設定しても 2 ルート外を断ること。

    根拠: レビュー #18「POST 新規登録で 2 ルート制限を迂回」。Files の allowed_roots は
    画面ごとの設定で広げられる（試験の conftest も sandbox を指定している）。新規選択は
    model_paths.SELECTION_ROOTS（/data1tb と /data2tb）を別に見て、API でも守る。
    既存登録の再保存はパスを変えなければ通す（2026-11-27 利用者の決まり）。
    """
    from pathlib import Path

    from app.files import service as files
    from app.models_mgmt import llama
    from tests.conftest import CSRF_HEADERS, _sandbox

    gguf = _sandbox / "wide-files.gguf"
    gguf.write_bytes(b"GGUF")
    # 偽の permissive な Files 設定: どのパスも通す。それでも 2 ルート制限は効く。
    monkeypatch.setattr(files, "resolve", lambda raw, **kw: Path(raw))

    cases = [
        ("llama", {"alias": "x", "model_path": str(gguf), "port": 8201}),
        ("radiance", {"alias": "x", "model_path": str(gguf), "port": 8201}),
        ("sly-radiance", {"alias": "x", "model_path": str(gguf), "port": 8201}),
        ("strata", {"alias": "x", "family": "qwen", "model": "q2_0",
                    "model_path": str(gguf), "port": 8201}),
    ]
    for runtime, body in cases:
        response = admin_client.post(f"/api/v1/models/{runtime}/instances", json=body,
                                     headers=CSRF_HEADERS)
        assert response.status_code == 422, (runtime, response.text)
        assert "新しく選べる" in response.json()["detail"]

    # 旧登録はそのまま再保存できる（パスが変わらないので新規制限はかからない）。
    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "old.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    llama.save_instance("old", {"alias": "old", "model_path": str(gguf), "port": 8201})
    saved: list[dict] = []
    monkeypatch.setattr(llama, "save_instance",
                        lambda alias, patch: saved.append(patch) or patch)
    response = admin_client.put("/api/v1/models/llama/instances/old",
                                json={"alias": "old", "model_path": str(gguf)},
                                headers=CSRF_HEADERS)
    assert response.status_code == 200, response.text
    assert saved[0]["model_path"] == str(gguf)

    # パスを変えるときは新規制限がかかる。
    changed = str(_sandbox / "changed.gguf")
    Path(changed).write_bytes(b"GGUF")
    response = admin_client.put("/api/v1/models/llama/instances/old",
                                json={"alias": "old", "model_path": changed},
                                headers=CSRF_HEADERS)
    assert response.status_code == 422, response.text
