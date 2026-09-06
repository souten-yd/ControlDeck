"""プロジェクトの持ち出しで秘密情報が混ざらないことを守る。

ここは画面で1つずつ開く経路と違い、ソース一式が対象になる。allowlistが
効かない代わりに「危ないものを落とす」で選ぶので、落とし漏れは即そのまま
手元のZIPへ入る。落ちること自体をテストで固定する。
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.project_lab import export


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "src").mkdir(parents=True)
    (root / "dist").mkdir()
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / ".git").mkdir()

    (root / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
    (root / "src" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "dist" / "bundle.js").write_text("console.log(1)\n", encoding="utf-8")
    (root / "node_modules" / "left-pad" / "index.js").write_text("//\n", encoding="utf-8")
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")

    # 名前で落ちるもの
    (root / ".env").write_text("API_KEY=abc\n", encoding="utf-8")
    (root / ".env.local").write_text("X=1\n", encoding="utf-8")
    (root / "server.key").write_text("k\n", encoding="utf-8")
    (root / "my-secrets.yaml").write_text("a: b\n", encoding="utf-8")
    # 名前では分からず、本文で落ちるもの
    (root / "config.py").write_text(
        'TOKEN = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8",
    )
    # 紛らわしいが落としてはいけないもの
    (root / "tokenizer.json").write_text('{"vocab": {}}\n', encoding="utf-8")
    (root / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
    return root


def _paths(plan: export.ExportPlan, root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in plan.files}


def test_plan_keeps_source_and_drops_secrets(project: Path):
    plan = export.plan(project)
    included = _paths(plan, project)

    assert "index.html" in included
    assert "src/app.ts" in included
    # 作った物は持ち出せる。artifact探索の SKIP_DIRS をそのまま使うと落ちてしまう。
    assert "dist/bundle.js" in included
    # 変数名が API_KEY なだけの file は落とさない。落とすとソースの大半に当たる。
    assert ".env.example" in included
    # `token` を含むが区切りで挟まれていないので秘密ではない。
    assert "tokenizer.json" in included

    for secret in (".env", ".env.local", "server.key", "my-secrets.yaml", "config.py"):
        assert secret not in included, f"{secret} が ZIP に入ってしまう"

    # 実行環境の産物は丸ごと落とす
    assert not any(p.startswith(("node_modules/", ".git/")) for p in included)


def test_plan_reports_why_each_file_was_dropped(project: Path):
    plan = export.plan(project)
    reasons = {item["path"]: item["reason"] for item in plan.excluded}

    assert "鍵" in reasons[".env"]
    assert "拡張子" in reasons["server.key"]
    # 本文で見つけたものは、どの書式に当たったかまで示す。名前が普通なので
    # 理由が分からないと、利用者は落とされた意味を追えない。
    assert "本文" in reasons["config.py"]
    assert "ghp_" in reasons["config.py"]
    assert reasons["node_modules"] == "実行環境の産物です"


def test_symlink_is_not_followed(tmp_path: Path):
    """project の外を指す symlink を辿ると、その先ごと持ち出してしまう。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "id_rsa").write_text("PRIVATE\n", encoding="utf-8")
    root = tmp_path / "demo"
    root.mkdir()
    (root / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    (root / "escape").symlink_to(outside)

    plan = export.plan(root)
    assert _paths(plan, root) == {"index.html"}
    assert any(item["reason"] == "symlinkは辿りません" for item in plan.excluded)


def test_archive_has_single_root_and_records_exclusions(project: Path, tmp_path: Path):
    plan = export.plan(project)
    target = tmp_path / "out.zip"
    export.write_archive(project, target, plan)

    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        note = archive.read("demo/EXCLUDED.txt").decode("utf-8")

    # 展開しても散らからないよう root は1つ
    assert {name.split("/")[0] for name in names} == {"demo"}
    assert "demo/src/app.ts" in names
    # 雛形の .env.example は入るが、実体の .env は入らない
    assert "demo/.env.example" in names
    assert "demo/.env" not in names and "demo/.env.local" not in names
    # 手元に落ちた後で除外に気づけるのはこの file だけ
    assert ".env" in note and "server.key" in note


def _make_project(monkeypatch, tmp_path: Path, name: str = "apidemo") -> Path:
    from app.project_lab import service

    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "index.html").write_text("<h1>api</h1>", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.setattr(service, "project_root", lambda: tmp_path.resolve())
    return root


def test_export_plan_endpoint_lists_what_will_be_dropped(admin_client, monkeypatch, tmp_path):
    _make_project(monkeypatch, tmp_path)
    response = admin_client.get("/api/v1/project-lab/projects/apidemo/export-plan")
    assert response.status_code == 200
    body = response.json()
    assert body["fileCount"] == 1
    assert [item["path"] for item in body["excluded"]] == [".env"]


def test_archive_endpoint_returns_a_zip_without_secrets(admin_client, monkeypatch, tmp_path):
    _make_project(monkeypatch, tmp_path)
    response = admin_client.get("/api/v1/project-lab/projects/apidemo/archive")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "apidemo.zip" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
    assert "apidemo/index.html" in names
    assert "apidemo/.env" not in names
