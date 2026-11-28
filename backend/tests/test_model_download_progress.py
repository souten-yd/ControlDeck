"""目的: モデルの取得が、再起動の後も続き（Maya）、進み具合（今の量・全体・何個目のファイルか）を画面に渡せること。
Strata の量子化の選択肢が系列ごとであること。

根拠: 2026-11-28 利用者「インクリングの SQ20 って Q2 なの？」「ダウンロードが進んでいるのか分からない。今の量・最大・
速度・何分の何かを分かるように」「自動再開機能は有効なのに効いていない」（本体の自動更新の再起動で Maya の取得の
hf download が止められ、続きが取られなかった）。
"""
from __future__ import annotations

from app.jobs import service as jobs


def test_maya_download_is_resumable_after_a_restart():
    """Maya の「ダウンロードして登録」は再開の種類に登録されている（再起動の後、同じ引数で作り直す）。"""
    from app.models_mgmt import resume  # noqa: F401 - 読み込みで登録する

    assert "model.maya-s.download" in jobs._resumable_kinds  # noqa: SLF001
    runner = resume.maya_download_runner({"setup_id": "hy4-flash-maya-l", "folder": "/data2tb/LLM/x"})
    assert callable(runner)
    assert resume.maya_download_title({"setup_id": "glm-maya-s"}) == "Mayaダウンロード glm-maya-s"


def test_maya_folder_bytes_counts_partial_files_and_skips_symlinks(tmp_path):
    """取得中の量は途中のファイル（.incomplete）も数え、symlink は辿らない。"""
    from app.models_mgmt import maya_s

    (tmp_path / "a.gguf").write_bytes(b"x" * 10)
    cache = tmp_path / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    (cache / "b.incomplete").write_bytes(b"y" * 5)
    (tmp_path / "link").symlink_to(tmp_path / "a.gguf")
    assert maya_s._folder_bytes(tmp_path) == 15  # noqa: SLF001


def test_strata_quantization_choices_follow_the_family():
    """量子化の選択肢は系列ごと（Inkling の SQ-20 を選ぶと、画面が qwen の一覧の先頭「q2_0」を出していた）。"""
    from app.models_mgmt import runtime_settings

    cap = runtime_settings._strata()  # noqa: SLF001
    fields = {f["id"]: f for section in cap["sections"] for f in section["fields"]}
    model = fields["model"]
    assert model["choices_by"]["field"] == "family"
    assert "sq-20" in model["choices_by"]["map"]["inkling-2-mini"]
    assert "q2_0" not in model["choices_by"]["map"]["inkling-2-mini"]
    assert model["choice_labels"]["sq-20"] == "SQ-20"
    assert fields["family"]["choice_labels"]["inkling-2-mini"] == "Inkling-2-Mini"
    for field in fields.values():
        assert "/api/v1" not in str(field.get("note") or "") and "strata_catalog" not in str(field.get("note") or "")
