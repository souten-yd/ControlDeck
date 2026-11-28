"""目的: 目録の「取得して登録」（Maya）が、まだ無い取得先のフォルダを安全に作れること。モデルの置き場（ライブラリ）は
/data1tb・/data2tb の中だけにすること。

根拠: 2026-11-28 利用者（スマホの写真）「ダウンロードしようとしたら存在しないと出る」（存在しません:
/data1tb/LLM/GLM-5.3-Flash-Maya-GGUF）。Maya の取得は既にあるフォルダが前提だった。同日「LLM のダウンロードフォルダの
ルートは data1tb と data2tb のフォルダにしてください」。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.features.router import _make_model_folder


@pytest.fixture
def roots(monkeypatch, tmp_path):
    from app.models_mgmt import model_paths

    one = tmp_path / "data1tb"
    two = tmp_path / "data2tb"
    (one / "LLM").mkdir(parents=True)
    two.mkdir()
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(one), str(two)))
    monkeypatch.setattr(model_paths, "_blocked", lambda path: False)
    return one, two, tmp_path


def test_makes_the_folder_under_an_existing_parent_inside_the_roots(roots):
    one, _two, _tmp = roots
    _make_model_folder(str(one / "LLM" / "GLM-5.3-Flash-Maya-GGUF"))
    assert (one / "LLM" / "GLM-5.3-Flash-Maya-GGUF").is_dir()
    _make_model_folder(str(one / "LLM" / "GLM-5.3-Flash-Maya-GGUF"))     # 既にあっても良い


@pytest.mark.parametrize("bad", ["outside", "bad name", "..", "a/../../x"])
def test_refuses_outside_the_roots_and_bad_names(roots, bad):
    one, _two, tmp = roots
    target = {"outside": str(tmp / "elsewhere" / "M"), "bad name": str(one / "LLM" / "a b"),
              "..": str(one / "LLM" / ".."), "a/../../x": str(one / "LLM" / "a" / ".." / ".." / "x")}[bad]
    (tmp / "elsewhere").mkdir(exist_ok=True)
    with pytest.raises(HTTPException):
        _make_model_folder(target)


def test_refuses_a_symlink_parent_leading_outside(roots):
    one, _two, tmp = roots
    (tmp / "out").mkdir()
    (one / "link").symlink_to(tmp / "out")
    with pytest.raises(HTTPException):
        _make_model_folder(str(one / "link" / "M"))
    assert not (tmp / "out" / "M").exists()


def test_library_paths_must_be_inside_the_two_roots(monkeypatch, tmp_path):
    from app.models_mgmt import libraries, model_paths

    inside = tmp_path / "data2tb" / "LLM"
    inside.mkdir(parents=True)
    monkeypatch.setattr(model_paths, "SELECTION_ROOTS", (str(tmp_path / "data1tb"), str(tmp_path / "data2tb")))
    monkeypatch.setattr(libraries, "_resolve_path", lambda entry: (entry["path"], True))
    monkeypatch.setattr("app.files.service.resolve", lambda path, must_exist=True: path)
    ok = libraries.validate_entries([{"id": "main2", "label": "2TB", "path": str(inside), "subpath": "", "default": True}])
    assert ok[0]["default"]
    with pytest.raises(libraries.LibraryError):
        libraries.validate_entries([{"id": "x", "label": "x", "path": str(tmp_path / "other"), "subpath": "", "default": True}])
    # 内蔵（Embed・Reranker の従来の置き場）は除く
    libraries.validate_entries([{"id": libraries.BUILTIN_LIBRARY_ID, "label": "内蔵", "path": str(tmp_path / "d"), "subpath": ""}])


def test_download_job_makes_a_missing_folder_without_the_flag(roots, monkeypatch, admin_client):
    """更新前に開いた古い画面は create_folder を送らない。それでも取得先が無ければ作る（「存在しません」で止めない）。
    2026-11-28 利用者「まだ存在しないと出る。存在しないからダウンロードしてほしいのに」。"""
    from app.models_mgmt import maya_s
    from tests.conftest import CSRF_HEADERS

    one, _two, _tmp = roots
    target = one / "LLM" / "Hy4-Flash-Maya-GGUF"
    monkeypatch.setattr(maya_s, "installed", lambda: True)
    seen = []

    def fake_catalog(folder=""):
        seen.append(folder)
        raise maya_s.MayaError("stop here")   # 作られたかだけを見る

    monkeypatch.setattr(maya_s, "catalog", fake_catalog)
    response = admin_client.post("/api/v1/features/maya-s/models/download-jobs", headers=CSRF_HEADERS,
                                 json={"setup_id": "hy4-flash-maya-l", "folder": str(target)})
    assert response.status_code == 422 and "stop here" in response.text
    assert target.is_dir() and seen == [str(target)]
