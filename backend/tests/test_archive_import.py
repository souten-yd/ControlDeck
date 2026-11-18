"""チャットに添付した圧縮ファイルを OpenCode のプロジェクトへ展開する（archive_import）と、添付・送信の受け口。

目的: 2026-11-18 利用者の依頼「OpenCode の開発中に ZIP や RAR を添付し、中身がソースコードならプログラムの
フォルダに置いて参照できるように」。外から来たファイルなので、展開する前と後の両方で危ないものを断る。
  - ZIP・7z・tar を展開できる（RAR は 7z が読む。作る道具が無いので 7z で代表する）
  - 空のプロジェクトなら直下へ、既存のファイルがあれば imports/<名前>/ へ。上書きしない
  - 中身が 1 つのフォルダだけなら、その中を展開先の直下にする
  - `..`・絶対パス・リンク・宣言を偽った圧縮爆弾・パスワード付きを断る
  - OpenCode 以外の機能で圧縮ファイルを送ると断る（展開先が無い）
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.workflows import archive_import as ai


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


SOURCE = {"app/main.py": b"print('hi')\n", "app/util/helpers.py": b"x = 1\n", "app/README.md": b"# app\n"}


def test_zip_into_an_empty_project_lands_at_the_root(tmp_path):
    archive = _zip(tmp_path / "app.zip", SOURCE)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    result = ai.extract_into_project(archive, "app.zip", project)
    assert result["relative"] == "." and result["files"] == 3
    assert (project / "main.py").read_bytes() == b"print('hi')\n"   # app/ は 1 つだけなので外す
    assert (project / "util" / "helpers.py").is_file()
    assert not any(p.name.startswith(".import-") for p in project.iterdir())
    note = ai.prompt_note("app.zip", result)
    assert "プロジェクトの直下" in note and "- util/helpers.py" in note


def test_existing_project_gets_imports_folder_and_nothing_is_overwritten(tmp_path):
    archive = _zip(tmp_path / "app.zip", SOURCE)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("mine")
    first = ai.extract_into_project(archive, "app.zip", project)
    second = ai.extract_into_project(archive, "app.zip", project)
    assert first["relative"] == "imports/app" and second["relative"] == "imports/app-2"
    assert (project / "main.py").read_text() == "mine"
    assert (project / "imports" / "app" / "main.py").is_file()


def test_tar_and_7z_are_extracted(tmp_path):
    src = tmp_path / "src"
    for name, data in SOURCE.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    with tarfile.open(tmp_path / "app.tar.gz", "w:gz") as t:
        t.add(src / "app", arcname="app")
    project = tmp_path / "p1"
    project.mkdir()
    assert ai.extract_into_project(tmp_path / "app.tar.gz", "app.tar.gz", project)["files"] == 3
    if shutil.which("7z") is None:
        pytest.skip("7z が無い")
    subprocess.run(["7z", "a", "-t7z", str(tmp_path / "app.7z"), str(src / "app")], check=True, capture_output=True)
    project2 = tmp_path / "p2"
    project2.mkdir()
    assert ai.extract_into_project(tmp_path / "app.7z", "app.7z", project2)["files"] == 3
    assert (project2 / "util" / "helpers.py").is_file()


def test_dangerous_entries_are_rejected_before_extracting(tmp_path):
    with pytest.raises(ai.ArchiveError, match="親フォルダ"):
        ai.inspect(_zip(tmp_path / "a.zip", {"../evil.py": b"x"}), "a.zip")
    with pytest.raises(ai.ArchiveError, match="安全でない"):
        ai.inspect(_zip(tmp_path / "b.zip", {"/etc/evil": b"x"}), "b.zip")
    link = tmp_path / "c.tar"
    with tarfile.open(link, "w") as t:
        info = tarfile.TarInfo("app/passwd")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(info)
    with pytest.raises(ai.ArchiveError, match="リンク"):
        ai.inspect(link, "c.tar")
    with pytest.raises(ai.ArchiveError, match="ZIP・7z・RAR"):
        ai.inspect(tmp_path / "c.tar", "notes.txt")


def test_a_bomb_that_lies_about_its_size_stops_while_writing(tmp_path, monkeypatch):
    archive = _zip(tmp_path / "bomb.zip", {"big.bin": b"0" * (3 * 1024 * 1024)})
    monkeypatch.setattr(ai, "MAX_TOTAL_BYTES", 2 * 1024 * 1024)
    with pytest.raises(ai.ArchiveError, match="大きすぎ"):
        ai.inspect(archive, "bomb.zip")
    monkeypatch.setattr(ai, "inspect", lambda path, name: {"files": 1, "bytes": 1})   # 宣言を信じた場合
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(ai.ArchiveError, match="大きすぎ"):
        ai.extract_into_project(archive, "bomb.zip", project)
    assert list(project.iterdir()) == []   # 途中まで書いたものも残さない


def test_upload_accepts_archives_and_send_requires_opencode(admin_client, tmp_path):
    from tests.conftest import CSRF_HEADERS

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("tool/run.py", "print(1)")
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/attachments", headers=CSRF_HEADERS,
                          files={"file": ("tool.zip", data.getvalue(), "application/zip")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "archive" and body["files"] == 1 and body["id"].endswith(".zip")
    bad = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/attachments", headers=CSRF_HEADERS,
                            files={"file": ("evil.zip", _zip(tmp_path / "e.zip", {"../x": b"1"}).read_bytes(),
                                            "application/zip")})
    assert bad.status_code == 422 and "親フォルダ" in bad.json()["detail"]
    sent = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                             json={"content": "見て", "model": "test", "mode": "chat", "attachments": [body["id"]]})
    assert sent.status_code == 422 and "OpenCode" in sent.json()["detail"]


def test_file_list_counts_only_what_was_extracted_not_git(tmp_path):
    """新しいプロジェクトの git init の中身（.git/hooks など）を、展開したファイルとして数えない（レビュー指摘）。"""
    project = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    result = ai.extract_into_project(_zip(tmp_path / "app.zip", SOURCE), "app.zip", project)
    assert result["relative"] == "." and result["files"] == 3
    assert not any(line.startswith(".git") for line in result["tree"])


@pytest.mark.skipif(shutil.which("7z") is None, reason="7z が無い")
def test_7z_with_a_link_is_rejected_and_a_lying_7z_is_stopped_while_extracting(tmp_path, monkeypatch):
    src = tmp_path / "d"
    src.mkdir()
    (src / "evil").symlink_to("/etc")
    (src / "ok.txt").write_text("hi")
    subprocess.run(["7z", "a", "-snl", str(tmp_path / "l.7z"), str(src)], check=True, capture_output=True)
    with pytest.raises(ai.ArchiveError, match="リンク"):
        ai.inspect(tmp_path / "l.7z", "l.7z")
    big = tmp_path / "big"
    big.mkdir()
    (big / "big.bin").write_bytes(b"0" * (3 * 1024 * 1024))
    subprocess.run(["7z", "a", str(tmp_path / "b.7z"), str(big)], check=True, capture_output=True)
    monkeypatch.setattr(ai, "MAX_TOTAL_BYTES", 1024 * 1024)
    monkeypatch.setattr(ai, "inspect", lambda path, name: {"files": 1, "bytes": 1})   # 宣言を偽った場合
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(ai.ArchiveError, match="大きすぎ"):
        ai.extract_into_project(tmp_path / "b.7z", "b.7z", project)
    assert list(project.iterdir()) == []


def test_imports_link_and_broken_layout_are_reported_as_archive_errors(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "imports").symlink_to(outside)
    with pytest.raises(ai.ArchiveError, match="外"):
        ai.extract_into_project(_zip(tmp_path / "a.zip", SOURCE), "a.zip", project)
    assert list(outside.iterdir()) == []
    # 同じ名前のファイルとフォルダ → 分かる言葉で（NotADirectoryError のまま出さない）
    clash = _zip(tmp_path / "c.zip", {"src": b"file", "src/a.py": b"x"})
    project2 = tmp_path / "p2"
    project2.mkdir()
    with pytest.raises(ai.ArchiveError, match="展開できません"):
        ai.extract_into_project(clash, "c.zip", project2)


def test_canceled_archive_is_deleted(admin_client):
    from tests.conftest import CSRF_HEADERS

    from app.workflows.chat_persist import _archive_path

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("run.py", "print(1)")
    archive_id = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/attachments", headers=CSRF_HEADERS,
                                   files={"file": ("t.zip", data.getvalue(), "application/zip")}).json()["id"]
    assert _archive_path(conv_id, archive_id).is_file()
    r = admin_client.delete(f"/api/v1/chat/conversations/{conv_id}/attachments/{archive_id}", headers=CSRF_HEADERS)
    assert r.status_code == 204 and not _archive_path(conv_id, archive_id).exists()
