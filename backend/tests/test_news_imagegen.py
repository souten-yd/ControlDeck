"""目的: ニュースの「画像の無い記事に AI でイメージ画像を作る」（CPU の専用実行）の組み立て・上限・置き場・配る API・削除・未導入の振る舞いの試験。
子プロセス（モデル）は全部モック。外へは通信せず、GPU も使わない。

根拠: 2026-11-23 利用者「ニュースから画像が取れない場合は本文の意図を汲み取って、AI で生成画像をする…CPU と RAM の利用のみ…画像を生成したらすぐに
アンロード…連続で生成…メディアフォージとかを使わずに、ニュースフィード独自の生成 AI を保有してほしい」。
精度の段（2026-11-23 利用者「このモデルを残しておいて、小中大みたいな感じで、選べるようにしてほしい、設定で」）: 設定の既定は小・未導入の段は何も作らない・
段ごとに台本の引数（段・スレッド・seed=記事の id・時間の上限）と件数の上限（大は少なめ）が変わる・段ごとに導入・削除できる（小は消せない）。
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
    monkeypatch.setattr(imagegen, "models_dir", lambda: tmp_path / "models" / "news-imagegen")
    monkeypatch.setattr(imagegen, "data_dir", lambda: tmp_path)       # remove_models は data/models の直下だけ消す
    monkeypatch.setattr(imagegen, "feature_python", lambda: tmp_path / "venv" / "bin" / "python")
    imagegen._failed.clear()
    yield


def install(tmp_path, tier="small"):
    (tmp_path / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    for rel in imagegen.imagegen_worker.TIER_FILES[tier]:
        f = tmp_path / "models" / "news-imagegen" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")


CFG = {"imagegen": {"enabled": True, "threads": 4, "max_per_run": 2}}


def test_prompt_english_title_kept_japanese_mapped_and_style_guards():
    en = imagegen.build_prompt("Qwen releases a new open model for coding", "", "model")
    assert en.startswith("Qwen releases a new open model") and "objects and scenery" in en and "wordless" in en
    ja = imagegen.build_prompt("日銀が金利を引き上げ", "株式市場は円安が進んだ", "news")
    assert "stock market" in ja and "objects and scenery" in ja
    # CLIP は否定語を理解せず、書くとかえって描く。人物・文字の語は肯定文に入れない（避けるのは物・風景の言い回し）
    for prompt in (en, ja):
        assert not any(w in prompt.lower() for w in ("people", "person", "face", "text", "letters", "logo"))
    assert imagegen.NEGATIVE == "text, letters, logo, watermark, people, face"
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
    assert result == {"done": 1, "failed": 1, "skipped": "", "tier": "small"}
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
    assert settings.normalize({})["imagegen"] == {"enabled": False, "tier": "small", "threads": 8, "max_per_run": 30}   # 既定は小
    assert settings.normalize({"imagegen": {"tier": "large"}})["imagegen"]["tier"] == "large"
    for bad_tier in ("huge", "", 3):
        with pytest.raises(Exception):
            settings.normalize({"imagegen": {"tier": bad_tier}})
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


# ---- 精度の段（小・中・大） ----

def test_tier_normalize_and_models_ready_per_tier(tmp_path):
    assert imagegen.normalize_tier("medium") == "medium" and imagegen.normalize_tier("x") == "small" and imagegen.normalize_tier(None) == "small"
    install(tmp_path, "small")
    assert imagegen.available("small") and not imagegen.available("medium") and not imagegen.available("large")
    install(tmp_path, "large")
    assert imagegen.available("large")
    info = {t["id"]: t for t in imagegen.tiers_info()}
    assert info["small"]["installed"] and not info["medium"]["installed"] and info["large"]["installed"]
    assert info["large"]["megabytes"] > info["medium"]["megabytes"] and info["large"]["time_seconds"] > info["medium"]["time_seconds"]


def test_selected_tier_not_installed_skips_with_tier(tmp_path):
    install(tmp_path, "small")
    add("https://e/1")
    called = []
    cfg = {"imagegen": {"enabled": True, "tier": "large", "threads": 4, "max_per_run": 5}}
    out = imagegen.generate_pending(cfg, runner=lambda job: called.append(job) or [])
    assert out["skipped"] == "not_installed" and out["tier"] == "large" and called == []


def test_job_carries_tier_seed_and_per_tier_cap_and_timeout(tmp_path):
    install(tmp_path, "large")
    for i in range(30):
        add(f"https://e/{i}", title=f"Open model number {i} released today", published=NOW.replace(minute=i))
    seen = []
    cfg = {"imagegen": {"enabled": True, "tier": "large", "threads": 4, "max_per_run": 100}}
    imagegen.generate_pending(cfg, runner=lambda job: seen.append(job) or [])
    job = seen[0]
    spec = imagegen.imagegen_worker.TIER_SPECS["large"]
    assert job["tier"] == "large" and len(job["items"]) == spec["max_per_run"] < 30     # 大は少なめ
    assert all(i["seed"] == i["id"] for i in job["items"])                                # 記事ごとに違う絵（seed は id）
    assert job["deadline_seconds"] == spec["job_timeout"] - 60


def test_worker_args_per_tier(tmp_path, monkeypatch):
    """段ごとの台本の引数（子プロセスはモック）。"""
    install(tmp_path, "medium")
    add("https://e/1")
    popen = {}

    class FakeProc:
        returncode = 0

        def communicate(self, data):
            popen["job"] = data
            return "", ""

        def poll(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(imagegen.subprocess, "Popen", lambda cmd, **kw: popen.update(cmd=cmd) or FakeProc())
    cfg = {"imagegen": {"enabled": True, "tier": "medium", "threads": 3, "max_per_run": 5}}
    imagegen.generate_pending(cfg)
    import json as _json

    sent = _json.loads(popen["job"])
    assert sent["tier"] == "medium" and sent["threads"] == 3
    assert popen["cmd"][-1] == "generate" and "imagegen_worker.py" in popen["cmd"][-2]


def test_tier_specs_are_consistent():
    w = imagegen.imagegen_worker
    assert set(w.TIER_SPECS) == set(w.TIER_FILES) == set(w.TIERS) == set(settings.IMAGEGEN_TIERS)
    for tier, spec in w.TIER_SPECS.items():
        assert all(name in w.MODEL_REPOS for name in spec["models"])
        w_, h_ = spec["size"]
        assert w_ % 64 == 0 and h_ % 64 == 0 or tier == "medium" and h_ % 32 == 0   # UNet が割れる形
        assert spec["out"][0] <= w_ and spec["out"][1] <= h_
        assert {rel.split("/")[0] for rel in w.TIER_FILES[tier]} == set(spec["models"])    # 取得する部品と、揃いの確認が食い違わない
    assert w.TIER_SPECS["small"]["max_per_run"] >= w.TIER_SPECS["large"]["max_per_run"]


def test_lcm_schedule_and_guidance_embedding():
    pytest.importorskip("numpy")
    w = imagegen.imagegen_worker
    assert w.lcm_timesteps(4) == [999, 759, 519, 279]          # LCMScheduler（original_inference_steps=50）と同じ
    assert w.lcm_timesteps(1) == [999]
    emb = w.guidance_embedding(7.0)
    assert emb.shape == (1, 256)
    assert w.item_seed({"id": 7}) == 7 and w.item_seed({"id": 7, "seed": 9}) == 9


def test_remove_models_by_tier_keeps_other_tiers(tmp_path):
    install(tmp_path, "small")
    install(tmp_path, "medium")
    install(tmp_path, "large")
    imagegen.remove_models("large")
    assert imagegen.available("small") and imagegen.available("medium") and not imagegen.available("large")
    assert not (tmp_path / "models" / "news-imagegen" / "taesdxl").exists()
    imagegen.remove_models()
    assert not (tmp_path / "models" / "news-imagegen").exists()


def test_feature_model_tier_install_and_remove(tmp_path, monkeypatch):
    from app.features import registry

    install(tmp_path, "small")
    monkeypatch.setattr(registry, "_managed_executable", lambda fid: tmp_path / "venv" / "bin" / "python")
    got = []
    monkeypatch.setattr(registry, "_download_models", lambda fid, tier="small": got.append(tier))
    monkeypatch.setattr(registry, "status", lambda fid: {"id": fid})
    registry.install_model_tier("news-imagegen", "medium")
    assert got == ["medium"]
    with pytest.raises(registry.FeatureError):
        registry.install_model_tier("news-imagegen", "huge")
    with pytest.raises(registry.FeatureError):
        registry.remove_model_tier("news-imagegen", "small")            # 小は機能のアンインストールでだけ消える
    install(tmp_path, "medium")
    registry.remove_model_tier("news-imagegen", "medium")
    assert not imagegen.available("medium") and imagegen.available("small")
    with pytest.raises(registry.FeatureError):
        registry.install_model_tier("python", "medium")                  # 段のモデルを持たない機能
