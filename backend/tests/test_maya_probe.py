"""maya_probe の Hy4-Flash の行（hy4_entry）: tools/hy4/download.json を catalog の形へ正しく写す。

目的: Models の Maya 登録画面に Hy4 の 4 サイズ（XS・S・M・L）を出し、n-gram テーブル（全サイズで
同じ 1 ファイル）と mmproj（画像）の在り処を Maya 本体（maya.py の hy4_entry・write_config_hy4）と
同じに保つこと。対応する登録の流れは test_maya_register.py の Hy4 の行で確かめる。

根拠: 2026-11-22 の依頼「Maya の設定に hy4 の対応を追加して」。Hy4 は mm3 と違い、trunk と n-gram
テーブルが別々（00001 と 00002 のシャード）で、テーブルが XS のフォルダ 1 か所にだけ在る
（S・M・L の setup が --models-dir から hard-link する）。この写し方が違うと、ファイルが在っても
「足りない」と出たり、登録が通っても setup がテーブルを見つけられない。
Maya 本体（v1.1.73、GitHub の mw00/project-maya）の maya.py の hy4_entry・hy4_model_step と
tools/hy4/download.json で確かめた見方。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.models_mgmt import maya_probe


def _engine(tmp_path: Path) -> SimpleNamespace:
    """Maya 本体（maya.py）の代わり: manifest の在処と名前の写像（HY4_QUANTS）だけを持つ。"""
    manifest = tmp_path / "tools" / "hy4" / "download.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "repo": "peasantsmith/Hy4-Flash-Maya-GGUF", "revision": "9d73294d",
        "license": "Tencent Hy License (LICENSE.hy4-flash)",
        "vision": {"Maya-XS/mmproj-Hy4-Flash-Maya-XS.gguf": [784524096, "vsha"]},
        "ngram": {"Maya-XS/Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf": [63360616160, "nsha"]},
        "quants": {
            "Hy4-Flash-Maya-XS": {
                "memory_gb": 52, "for_gb": [56, 64],
                "files": {"Maya-XS/Hy4-Flash-Maya-XS-IQ2_XXS-00001-of-00002.gguf": [44786173984, "tsha"]}},
            "Hy4-Flash-Maya-S": {
                "memory_gb": 64, "for_gb": [68, 80],
                "files": {"Maya-S/Hy4-Flash-Maya-S-IQ2_S-00001-of-00002.gguf": [52970756096, "ssha"]}},
            "Hy4-Flash-Maya-M": {"memory_gb": 76, "files": {}},
        }}), encoding="utf-8")
    return SimpleNamespace(ROOT=tmp_path, HY4_MANIFEST=manifest,
                           HY4_QUANTS={"maya-xs": "Hy4-Flash-Maya-XS", "maya-s": "Hy4-Flash-Maya-S",
                                       "maya-m": "Hy4-Flash-Maya-M"})


def test_hy4_list_row_reads_the_manifest_sizes_and_memory(tmp_path):
    maya = _engine(tmp_path)
    row = maya_probe.hy4_entry(maya, "maya-xs", "Maya-XS", "hy4-flash-maya-xs", None)
    assert row["family"] == "hy4" and row["setup_id"] == "hy4-flash-maya-xs"
    assert row["tier"] == "Maya-XS" and row["quant"] == "maya-xs" and row["subfolder"] == "Maya-XS"
    # 大きさは trunk + n-gram 表（Maya-XS 44.8 GB + 63.4 GB）、メモリは manifest の memory_gb
    assert row["size_gb"] == 108.1 and row["memory_gb"] == 52
    assert row["ngram_gb"] == 63.4                    # 全サイズで同じ 1 ファイル（画面の説明にも出す）
    assert row["published"] is True and row["repo"] == "peasantsmith/Hy4-Flash-Maya-GGUF"
    assert row["revision"] == "9d73294d"
    # 自分で取得するときのコマンド: XS はフォルダ指定だけでテーブル・mmproj まで付いてくる
    assert '--include "Maya-XS/*"' in row["hf"] and "<フォルダ>" in row["hf"]
    # files の無いサイズ（未公開）は published=False: 選べる一覧に出ない
    assert maya_probe.hy4_entry(maya, "maya-m", "Maya-M", "hy4-flash-maya-m", None)["published"] is False


def test_hy4_xs_files_cover_trunk_ngram_and_mmproj_in_one_folder(tmp_path):
    maya = _engine(tmp_path)
    folder = tmp_path / "LLM"
    folder.mkdir()
    row = maya_probe.hy4_entry(maya, "maya-xs", "Maya-XS", "hy4-flash-maya-xs", folder)
    assert row["model_dir"] == str(folder / "Maya-XS") and row["models_dir"] == str(folder)
    files = {(f["name"], f["what"]): f for f in row["files"]}
    assert set(files) == {("Hy4-Flash-Maya-XS-IQ2_XXS-00001-of-00002.gguf", "model"),
                           ("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", "tables"),
                           ("mmproj-Hy4-Flash-Maya-XS.gguf", "vision")}
    assert all(f["path"].startswith(str(folder / "Maya-XS")) for f in row["files"])
    assert files[("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", "tables")]["required"] is True
    # 画像の部品は任意: 無くても登録できる（--no-vision で文字だけ）
    assert files[("mmproj-Hy4-Flash-Maya-XS.gguf", "vision")]["required"] is False
    assert not row["files"][0]["present"]            # 大きさで中身を確かめる（何も置いていない）


def test_hy4_bigger_sizes_keep_the_shared_ngram_and_mmproj_in_the_xs_folder(tmp_path):
    maya = _engine(tmp_path)
    folder = tmp_path / "LLM"
    folder.mkdir()
    row = maya_probe.hy4_entry(maya, "maya-s", "Maya-S", "hy4-flash-maya-s", folder)
    assert row["model_dir"] == str(folder / "Maya-S")
    # n-gram 表と mmproj は XS のフォルダに在る（S のフォルダではない）: そこから setup が hard-link する
    paths = {(f["name"], f["what"]): f["path"] for f in row["files"]}
    assert paths[("Hy4-Flash-Maya-S-IQ2_S-00001-of-00002.gguf", "model")] == str(folder / "Maya-S" / "Hy4-Flash-Maya-S-IQ2_S-00001-of-00002.gguf")
    assert paths[("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", "tables")] == str(folder / "Maya-XS" / "Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf")
    assert paths[("mmproj-Hy4-Flash-Maya-XS.gguf", "vision")] == str(folder / "Maya-XS" / "mmproj-Hy4-Flash-Maya-XS.gguf")
    # 自分で取得するときのコマンド: S のフォルダに加えて、テーブルを XS から取る
    assert '--include "Maya-S/*"' in row["hf"]
    assert '--include "Maya-XS/Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf"' in row["hf"]
