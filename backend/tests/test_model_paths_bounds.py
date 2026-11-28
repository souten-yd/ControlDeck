"""登録前の読み取り（model_paths）が読む量・件数・型・子の symlink を厳密に守ることの試験。

目的: 新規選択の検証と登録前の読み取り（app/models_mgmt/model_paths.py）が、細工した・
壊れたファイル・フォルダで長く回ったり、選んだフォルダの外を実体として読んだりしないこと。
量・件数・型の上限と、子（フォルダの中のファイル）の symlink の拒否を確かめる。
inspect は同期 endpoint の threadpool で動くので、1 件が長く止まると他の要求まで遅れる。

根拠: Nexarch #20（2026-11-28）のレビュー残件（P1）。
- config.json が子 symlink でもそのまま開いていた（resolve したフォルダの外を読める）。
- フォルダの大きさの走査（glob・stat）に件数の上限が無く、symlink も追いかけていた。
- GGUF の文字列配列は件数（count）だけのループで、総読取の上限が各要素の間に効いて
  いなかった（大きなファイルで長時間止められた）。
- .rad と config.json は名前だけで形式を認定していた。
同じ観点の既存の試験は test-catalog で探して無いことを確認した（test_runtime_catalog.py は
正しい入力の読み取りと、選択のルートの外・上位の symlink の拒否だけを確かめている）。
"""
import os
import struct
import threading
import time

from app.models_mgmt import model_paths


# ---- GGUF の最小ヘッダを作る部品 ---------------------------------------------------------------


def _gguf_string(data: bytes) -> bytes:
    return struct.pack("<Q", len(data)) + data


def _gguf_scalar(key: str, kind: int, raw: bytes) -> bytes:
    return _gguf_string(key.encode()) + struct.pack("<I", kind) + raw


def _gguf_string_array_header(key: str, count: int) -> bytes:
    """文字列配列（kind 9・要素型 8）の鍵と件数まで。要素は呼び出し側で足す。"""
    return _gguf_string(key.encode()) + struct.pack("<IIQ", 9, 8, count)


def test_gguf_string_array_with_a_lying_count_stops_quickly(tmp_path, monkeypatch):
    """目的: 文字列配列の件数（count）を大きく偽った GGUF ヘッダを短い時間で打ち切ること。

    根拠: 修正前は総読取の上限（8MB）が配列の要素の間に効かず、ファイルの終わりまで
    要素ごとのループを回し続けた。GGUF は数十 GB のファイルなので、細工したヘッダで
    threadpool の枠を長時間奪える（512MB の疎なファイルで修正前は約 11 秒・返りは空。
    修正後は配列 1 つ 200000 要素の上限で打ち切り、0.1 秒未満・読めた値は返す）。
    上限は語彙（15 万程度）の本物の配列が収まる大きさにしている。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    header = (
        b"GGUF" + struct.pack("<I", 3)
        + struct.pack("<QQ", 0, 2)                                   # tensors, metadata count
        + _gguf_scalar("general.file_type", 4, struct.pack("<I", 7))  # Q8_0
        + _gguf_string_array_header("tokenizer.ggml.tokens", 2 ** 40)  # 偽の件数
    )
    path = tmp_path / "lying.gguf"
    path.write_bytes(header)
    with path.open("rb+") as handle:
        # 疎なファイル: 中身はゼロで、要素（長さ 0 の文字列）が延々続くように見える。
        # 512MB はディスクを消さない（穴のまま）。GGUF は実際に数十 GB あるので、
        # この大きさのファイルで止められないと実害になる。
        handle.truncate(len(header) + 512 * 1024 * 1024)

    start = time.perf_counter()
    info = model_paths.inspect(str(path))
    elapsed = time.perf_counter() - start

    assert info["format"] == "gguf"
    # 打ち切る前に読めた鍵は返す（壊れたファイルで登録画面を止めないのと同じ扱い）。
    assert info["metadata"].get("general.file_type") == 7
    assert elapsed < 5.0, f"悪性ヘッダの読み取りが {elapsed:.2f} 秒かかった"


def test_gguf_keeps_reading_keys_after_a_legitimate_array(tmp_path, monkeypatch):
    """目的: 上限内の本物の文字列配列（語彙など）の後も、続く鍵を読み続けること。

    根拠: 配列で打ち切るときに全体を止める作りにすると、配列より後にある鍵
    （<arch>.context_length・general.file_type）を失う。実際の GGUF は語彙の
    文字列配列を持つので、上限内は要素を素通りして続きを読む。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    elements = b"".join(_gguf_string(f"token-{i}".encode()) for i in range(300))
    header = (
        b"GGUF" + struct.pack("<I", 3)
        + struct.pack("<QQ", 0, 3)
        + _gguf_string_array_header("tokenizer.ggml.tokens", 300) + elements
        + _gguf_scalar("qwen3.context_length", 4, struct.pack("<I", 4096))
        + _gguf_scalar("general.file_type", 4, struct.pack("<I", 7))
    )
    path = tmp_path / "normal.gguf"
    path.write_bytes(header)

    info = model_paths.inspect(str(path))
    assert info["metadata"]["context_length"] == 4096
    assert info["metadata"]["general.file_type"] == 7


# ---- フォルダ（safetensors）の子の検証 ---------------------------------------------------------


def test_config_json_symlink_is_rejected_and_the_folder_is_not_safetensors(tmp_path, monkeypatch):
    """目的: 選んだフォルダの子（config.json）が symlink なら読まず、safetensors と
    認定しないこと。フォルダの中へ向いた symlink でも同じく断ること。

    根拠: フォルダ自体は realpath で検証しても、子をそのまま開くと symlink で
    ルートの外（拒否場所を含む）を読める。中へ向いた symlink まで許すと、実体の
    所在が目録と画面で食い違う。名前だけ（config.json がある）で形式を決めるのも
    やめる（#20 のレビュー）。
    """
    import json

    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"model_type": "qwen3"}))
    folder = tmp_path / "weights"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(b"x" * 10)
    (folder / "config.json").symlink_to(outside)          # ルートの中・フォルダの外

    info = model_paths.inspect(str(folder))
    assert info["format"] == "unknown"
    assert info["metadata"] == {}
    views = {view["runtime"]: view for view in info["runtimes"]}
    assert views["sly-radiance"]["eligible"] is False

    (folder / "config.json").unlink()
    (folder / "real.json").write_text(json.dumps({"model_type": "qwen3"}))
    (folder / "config.json").symlink_to(folder / "real.json")   # フォルダの中でも断る

    info = model_paths.inspect(str(folder))
    assert info["format"] == "unknown"
    assert info["metadata"] == {}


def test_folder_with_junk_config_json_is_not_safetensors(tmp_path, monkeypatch):
    """目的: config.json の中身が読める JSON オブジェクトでないなら safetensors と
    認定しないこと。

    根拠: 名前だけの形式認定（#20 のレビュー）。壊れた JSON・JSON だがオブジェクトで
    ないものは sly-radiance で起動できない登録になる。深い入れ子は json.loads が
    RecursionError を出すので、例外を利用者へそのまま漏らさず unknown に落とす。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    cases = {
        "broken": "これは JSON ではありません",
        "array": "[1, 2, 3]",
        "deep": "[" * 200_000,
    }
    for name, content in cases.items():
        folder = tmp_path / name
        folder.mkdir()
        (folder / "config.json").write_text(content)
        info = model_paths.inspect(str(folder))
        assert info["format"] == "unknown", name
        assert info["metadata"] == {}, name


def test_config_json_fifo_does_not_hang_inspect(tmp_path, monkeypatch):
    """目的: config.json が名前付きパイプ（FIFO）でも inspect が止まらないこと。

    根拠: open("rb") は FIFO だと書き手が現れるまで戻らない。読む前に実体が通常
    ファイルか確かめないと、細工したフォルダで threadpool の枠を 1 つ永久に奪える。
    形式の判定（_detect の is_file）と読み取り（_config_metadata の実体の検証）の
    両方の関門で守る。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    folder = tmp_path / "weights"
    folder.mkdir()
    os.mkfifo(folder / "config.json")

    result: dict = {}

    def call():
        result["info"] = model_paths.inspect(str(folder))

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(5)
    assert worker.is_alive() is False, "FIFO の config.json で inspect が止まった"
    assert result["info"]["format"] == "unknown"


# ---- フォルダの大きさの走査 -------------------------------------------------------------------


def test_folder_size_is_bounded_and_ignores_symlinked_children(tmp_path, monkeypatch):
    """目的: フォルダの大きさの走査が子の symlink を数えず、件数の上限で止まること。

    根拠: 修正前は glob("*") に件数の上限が無く、stat も symlink を追いかけるので、
    大量の項目のあるフォルダで inspect が長く回り、フォルダの外の大きさまで数えた。
    上限（_FOLDER_MAX_ENTRIES）は模型フォルダ（高々数百のファイル）より大きい。
    """
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path),))
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (1 << 20))
    folder = tmp_path / "weights"
    folder.mkdir()
    (folder / "a.safetensors").write_bytes(b"x" * 7)
    (folder / "b.safetensors").write_bytes(b"x" * 3)
    (folder / "escaped.safetensors").symlink_to(big)

    assert model_paths._file_size(folder) == 10          # symlink は数に入れない

    monkeypatch.setattr(model_paths, "_FOLDER_MAX_ENTRIES", 2)
    for index in range(6):
        (folder / f"real-{index}.safetensors").write_bytes(b"x")
    # 全部数えると 16 バイト（7 + 3 + 6×1）。上限 2 件で打ち切るので、どの 2 件を
    # 数えても 16 未満になる（順序は問わない）。
    assert model_paths._file_size(folder) < 16
