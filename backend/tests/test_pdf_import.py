"""添付 PDF: 文字の復元・原本とページ画像の保存・OpenCode のプロジェクトへ安全に置くこと。

根拠: 2026-11-18 本番「PDF を添付したが全く読まれていない」。文字が /uni30E6… のまま登録され、原本も無かった。
"""
from __future__ import annotations

import io
import shutil

import pytest
from pypdf import PdfWriter

from app.workflows import pdf_import


def _pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=100)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_glyph_names_become_text():
    assert pdf_import.repair_text("/uni30E6/uni30CB/uni30C8/uni30E9/uni30C3/uni30AF/uni0050\n/uni0031") == "ユニトラックP\n1"
    assert pdf_import.repair_text("/uniD800 /path") == "/uniD800 /path"       # 文字にならないものは触らない
    assert pdf_import.repair_text("/uni30E630E7") == "ユョ"                    # 1 つの字形が複数の文字
    assert pdf_import.repair_text("/uniface /uni") == "/uniface /uni"         # ふつうの文字列は変えない


def test_same_pdf_is_kept_once_and_summarised_for_research(tmp_path):
    data = _pdf()
    first = pdf_import.save(tmp_path, "Plan15.pdf", data, "ユニトラック レイアウトプラン", 2)
    assert pdf_import.save(tmp_path, "Plan15.pdf", data, "ユニトラック レイアウトプラン", 2) == first
    assert [item["name"] for item in pdf_import.saved(tmp_path)] == ["Plan15.pdf"]
    assert "レイアウトプラン" in pdf_import.brief(tmp_path)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm が無い")
def test_pages_are_placed_in_the_project_once(tmp_path):
    docs, project = tmp_path / "docs", tmp_path / "project"
    project.mkdir()
    pdf_import.save(docs, "図面 A.pdf", _pdf(), "本文", 2)
    item = pdf_import.saved(docs)[0]
    placed = pdf_import.install(docs, item, project)
    target = project / placed["relative"]
    assert placed["new"] and placed["images"] == 2
    assert sorted(p.name for p in target.iterdir()) == ["page-001.png", "page-002.png", "source.pdf", "text.txt"]
    assert (project / ".controldeck/attachments/.gitignore").read_text() == "*\n"   # 他の .controldeck は隠さない
    again = pdf_import.install(docs, item, project)
    assert not again["new"] and again["images"] == 2
    assert pdf_import.remove(docs, item["id"]) == "図面 A.pdf" and pdf_import.saved(docs) == []
    assert "page-001.png" in pdf_import.prompt_note([placed]) and "read" in pdf_import.prompt_note([placed])


def test_links_in_the_project_are_not_followed(tmp_path):
    """プロジェクトはモデルが書ける。リンクを置かれても箱の外へ書かない。"""
    docs, project, outside = tmp_path / "docs", tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".controldeck").symlink_to(outside)
    pdf_import.save(docs, "a.pdf", _pdf(1), "本文", 1)
    with pytest.raises(OSError):
        pdf_import.install(docs, pdf_import.saved(docs)[0], project)
    assert list(outside.iterdir()) == []
