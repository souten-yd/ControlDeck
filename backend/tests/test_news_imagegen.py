"""目的: ニュースの「画像の無い記事に AI でイメージ画像を作る」（CPU の専用実行）の組み立て・上限・置き場・配る API・削除・未導入の振る舞いの試験。
子プロセス（モデル）は全部モック。外へは通信せず、GPU も使わない。

根拠: 2026-11-23 利用者「ニュースから画像が取れない場合は本文の意図を汲み取って、AI で生成画像をする…CPU と RAM の利用のみ…画像を生成したらすぐに
アンロード…連続で生成…メディアフォージとかを使わずに、ニュースフィード独自の生成 AI を保有してほしい」。
守ること:
- プロンプトは LLM を使わない規則（英語の見出しはそのまま・日本語は対応表か種類の決まり文句）で、文字・ロゴ・顔を避ける言い回しを含む。
- 設定が切・機能が未導入なら何もしない。件数・スレッド数の上限を子へ渡す。失敗した記事は再起動まで試さない。
- 画像は id（整数）だけでパスを組み、置き場の外・リンクは返さない。API はログインした人だけ、その人に見える記事だけ。
- 古い記事を消すと画像も消える。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.news import imagegen, settings, store
from app.news import db as news_db
from app.news.db import NewsItem
from tests.test_news import NOW, add, login  # noqa: F401  (clean_news は autouse の fixture を共有する)
from tests.test_news import clean_news  # noqa: F401


@pytest.fixture(autouse=True)
def places(tmp_path, monkeypatch):
    monkeypatch.setattr(imagegen, "images_dir", lambda: tmp_path / "images")
    monkeypatch.setattr(imagegen, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(imagegen, "feature_python", lambda: tmp_path / "venv" / "bin" / "python")
    imagegen._failed.clear()
    yield


def install(tmp_path):
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    for rel in imagegen.imagegen_worker.REQUIRED_FILES:
        f = tmp_path / "models" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")


CFG = {"imagegen": {"enabled": True, "threads": 4, "max_per_run": 2}}


def test_prompt_english_title_kept_japanese_mapped_and_style_guards():
    en = imagegen.build_prompt("Qwen releases a new open model for coding", "", "model")
    assert en.startswith("Qwen releases a new open model") and "no text" in en and "no logo" in en and "no human face" in en
    ja = imagegen.build_prompt("日銀が金利を引き上げ", "株式市場は円安が進んだ", "news")
    assert "stock market" in ja and "no text" in ja
    # 対応表に無い日本語は種類の決まり文句。日本語がそのまま入らない
    other = imagegen.build_prompt("今日のできごとについて", "", "release")
    assert "software release" in other and "今日" not in other


def test_disabled_or_not_installed_does_nothing(tmp_path):
    add("https://e/1")
    called = []
    run = lambda job: called.append(job) or []  # noqa: E731
    assert imagegen.generate_pending({"imagegen": {"enabled": False}}, runner=run)["skipped"] == "disabled"
    assert imagegen.generate_pending(CFG, runner=run)["skipped"] == "not_installed"
    assert called == []


def test_generates_with_limits_sets_url_and_remembers_failures(tmp_path):
    install(tmp_path)
    ids = [add(f"https://e/{i}", title=f"Open model number {i} released today", published=NOW.replace(minute=i)) for i in range(4)]
    add("https://e/img", image="https://img/x.png")
    add("/x", kind="event")
    seen = []

    def run(job):
        seen.append(job)
        out = imagegen.images_dir()
        out.mkdir(parents=True, exist_ok=True)
        rows = []
        for n, item in enumerate(job["items"]):
            if n == 0:
                (out / f"{item['id']}.webp").write_bytes(b"RIFFxxxxWEBP")
            rows.append({"id": item["id"], "ok": n == 0})
        return rows

    result = imagegen.generate_pending(CFG, runner=run)
    assert result == {"done": 1, "failed": 1, "skipped": ""}
    job = seen[0]
    assert job["threads"] == 4 and len(job["items"]) == 2          # 件数・スレッド数の上限
    assert [i["id"] for i in job["items"]] == [ids[3], ids[2]]       # 新しい記事から
    with news_db.session() as db:
        assert db.get(NewsItem, ids[3]).image_url == f"/api/v1/news/images/{ids[3]}.webp"
        assert db.get(NewsItem, ids[2]).image_url is None
    imagegen.generate_pending(CFG, runner=run)
    assert ids[2] not in [i["id"] for i in seen[1]["items"]]         # 失敗した記事は試さない


def test_worker_timeout_is_passed_and_child_killed(tmp_path, monkeypatch):
    install(tmp_path)
    add("https://e/1")
    captured = {}
    monkeypatch.setattr(imagegen, "_run_worker", lambda job, timeout=0: captured.update(job=job) or [])
    imagegen.generate_pending(CFG)
    assert 0 < captured["job"]["deadline_seconds"] < imagegen.TIMEOUT_SECONDS


def test_image_path_only_inside_dir_and_no_symlink(tmp_path):
    root = imagegen.images_dir()
    root.mkdir(parents=True)
    (root / "5.webp").write_bytes(b"x")
    outside = tmp_path / "secret.webp"
    outside.write_bytes(b"s")
    (root / "6.webp").symlink_to(outside)
    assert imagegen.image_path(5) == (root / "5.webp").resolve()
    assert imagegen.image_path(6) is None and imagegen.image_path(7) is None
    assert imagegen.image_path("../secret") is None and imagegen.image_path(True) is None


def test_images_api_needs_login_and_visibility(client):
    root = imagegen.images_dir()
    root.mkdir(parents=True)
    mine = add("https://e/m", image="/api/v1/news/images/1.webp")
    other = add("https://e/o", image="/api/v1/news/images/2.webp", uid=999999)
    plain = add("https://e/p", image="https://img/x.png")
    for i in (mine, other, plain):
        (root / f"{i}.webp").write_bytes(b"RIFFxxxxWEBP")
    # 記事の image_url は id で作る形に合わせる
    with news_db.session() as db:
        for i in (mine, other):
            db.get(NewsItem, i).image_url = imagegen.url_for(i)
        db.commit()
    client.cookies.clear()
    assert client.get(f"/api/v1/news/images/{mine}.webp").status_code == 401
    login(client, "imguser")
    ok = client.get(f"/api/v1/news/images/{mine}.webp")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/webp"
    assert client.get(f"/api/v1/news/images/{other}.webp").status_code == 404      # 他人宛て
    assert client.get(f"/api/v1/news/images/{plain}.webp").status_code == 404      # 生成した画像ではない
    assert client.get("/api/v1/news/images/99999.webp").status_code == 404
    assert client.get("/api/v1/news/images/..%2Fx.webp").status_code in (404, 422)
    listing = client.get("/api/v1/news/items?view=latest").json()["items"]
    flags = {r["id"]: r["image_ai"] for r in listing}
    assert flags[mine] is True and flags[plain] is False


def test_prune_deletes_generated_images(tmp_path):
    root = imagegen.images_dir()
    root.mkdir(parents=True)
    old = add("https://e/old", published=NOW.replace(year=2020))
    new = add("https://e/new")
    for i in (old, new):
        (root / f"{i}.webp").write_bytes(b"x")
    with news_db.session() as db:
        assert store.prune(db, 1) == 1
    assert not (root / f"{old}.webp").exists() and (root / f"{new}.webp").exists()


def test_settings_defaults_and_validation(client):
    assert settings.normalize({})["imagegen"] == {"enabled": False, "threads": 8, "max_per_run": 30}
    assert settings.normalize({"imagegen": {"enabled": True, "threads": 2, "max_per_run": 5}})["imagegen"]["threads"] == 2
    for bad in ({"threads": 0}, {"threads": 99}, {"max_per_run": 0}, {"max_per_run": 101}, {"threads": "x"}):
        with pytest.raises(Exception):
            settings.normalize({"imagegen": bad})


def test_feature_is_python_lib_with_pinned_cpu_only_packages_and_models():
    from app.features import registry

    spec = registry.FEATURES["news-imagegen"]
    assert spec["kind"] == "python-lib" and spec["models"] == "news-imagegen"
    assert all("==" in p for p in spec["packages"])                      # 版を固定
    assert not any(p.split("==")[0] in ("torch", "torchvision", "onnxruntime-gpu") for p in spec["packages"])   # GPU・CUDA・ROCm を入れない
    assert all(v["revision"] and len(v["revision"]) == 40 for v in imagegen.imagegen_worker.MODEL_REPOS.values())   # モデルも版固定
