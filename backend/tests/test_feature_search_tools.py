"""機能と拡張: 「検索と収集」の分類、Python ライブラリの機能（専用 venv）、更新ありの確認、まとめて導入。

目的:
- Web 本文抽出（Trafilatura）は分類「検索と収集」に出て、専用 venv に**版を固定して**入れ、更新で最新版へ上げること（本体の venv を汚さない）。
- 導入済みかは「専用 venv の Python でライブラリを import できるか」で決め、版を出すこと（壊れていれば再導入を促す）。
- pip・npm の機能でも PyPI / npm の最新版と比べて「更新あり」を出せること。
- 分類ごとの「まとめて導入」が、未導入の pip・npm の機能だけを 1 つの Job で入れ、失敗を結果に残すこと。

根拠: 2026-11-21 利用者「必要なコードやプラグインは、ControlDeck の設定の機能と拡張に導入ボタンと更新ボタンを付けて、
個別管理・一括導入・更新ができるようにすること」。docs/PLAN-web-collector.md §3。外部（PyPI）へは出ない（差し替え）。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys

from app.features import package_updates, registry
from tests.conftest import CSRF_HEADERS


def test_web_extract_is_a_pinned_python_library_in_the_search_category(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    spec = registry.FEATURES["web-extract"]
    assert registry.category("web-extract") == "search" and spec["kind"] == "python-lib"
    assert spec["packages"] == ["trafilatura==2.3.1"] and spec["latest_packages"] == ["trafilatura"]
    calls = []

    def fake_pip(feature_id, packages):
        calls.append(packages)
        venv = registry._feature_root(feature_id) / "venv" / "bin"
        venv.mkdir(parents=True, exist_ok=True)
        if not (venv / "python").exists():
            (venv / "python").symlink_to(sys.executable)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(registry, "_pip_install", fake_pip)
    # 専用 venv の python で import できた版を出す（ここでは本体の python を差し替えて、json を trafilatura に見立てる）
    monkeypatch.setitem(registry.FEATURES["web-extract"], "module", "json")
    monkeypatch.setattr(registry, "_python_lib_version", lambda spec, python: "2.3.1" if python.is_file() else "")
    before = registry.status("web-extract")
    assert not before["installed"] and before["category"] == "search"
    state = registry.install("web-extract")
    assert calls == [["trafilatura==2.3.1"]] and state["installed"] and state["version"] == "2.3.1" and state["enabled"]
    registry.update("web-extract")
    assert calls[-1] == ["trafilatura"]


def test_broken_library_asks_for_reinstall(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    python = registry._feature_root("web-extract") / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    # 本物の判定: 専用 venv の python で import する（本体の python には trafilatura が無い → 壊れている扱い）
    state = registry.status("web-extract")
    assert not state["installed"] and not state["enabled"]


def test_update_check_compares_with_pypi(monkeypatch):
    async def fake_json(url):
        assert url == "https://pypi.org/pypi/trafilatura/json"
        return {"info": {"version": "2.4.0"}, "urls": [{"upload_time_iso_8601": "2026-11-01T00:00:00Z"}]}

    monkeypatch.setattr(package_updates, "_get_json", fake_json)
    monkeypatch.setattr(registry, "status", lambda fid: {"version": "2.3.1", "managed": True})
    package_updates.forget("web-extract")
    out = asyncio.run(package_updates.release_status("web-extract"))
    assert out["update_available"] and out["latest_tag"] == "2.4.0" and out["installed_tag"] == "2.3.1"
    assert package_updates._npm_name({"package": "@ast-grep/cli@0.51.0"}) == "@ast-grep/cli"
    assert package_updates._npm_name({"latest_package": "@playwright/mcp@latest"}) == "@playwright/mcp"
    assert package_updates._npm_name({"latest_package": "superpowers@git+https://github.com/obra/superpowers.git"}) == ""
    assert not package_updates._newer("2.3.1", "2.3.1") and package_updates._newer("2.10.0", "2.9.9")


def test_install_all_in_a_category_runs_one_job(admin_client, monkeypatch):
    installed = []

    def fake_install(fid):
        installed.append(fid)
        if fid == "context7-mcp":
            raise registry.FeatureError("ネットワークに届きません")
        return {"id": fid}

    real_status = registry.status
    monkeypatch.setattr(registry, "install", fake_install)
    monkeypatch.setattr(registry, "status", lambda fid: {**real_status(fid), "installed": fid in ("opencode-v2",)})
    r = admin_client.post("/api/v1/features/install-all-jobs", json={"category": "opencode"}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    features = r.json()["features"]
    assert "opencode-v2" not in features and "context7-mcp" in features
    job = admin_client.get(f"/api/v1/jobs/{r.json()['job_id']}")
    for _ in range(50):
        job = admin_client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
        if job["status"] not in ("queued", "running"):
            break
        asyncio.run(asyncio.sleep(0.05))
    assert job["status"] == "succeeded", job
    assert job["result"]["failed"][0]["id"] == "context7-mcp" and len(job["result"]["installed"]) == len(features) - 1
    assert admin_client.post("/api/v1/features/install-all-jobs", json={"category": "nope"},
                             headers=CSRF_HEADERS).status_code == 404


def test_search_category_lists_the_search_components(monkeypatch, tmp_path):
    """検索と収集に、検索エンジン（SearXNG）・本文抽出・robots.txt・JS ページ用のブラウザが並ぶ。
    根拠: 2026-11-21 利用者「まとめて導入するのはこの検索機能に関する部分のこと。チェックを付けて一括導入でもいい」。"""
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    ids = {fid for fid in registry.KNOWN_FEATURES if registry.category(fid) == "search"}
    assert ids == {"searxng", "web-extract", "web-robots", "web-browser"}
    assert registry.FEATURES["web-robots"]["packages"] == ["protego==0.7.0"]


def test_searxng_is_managed_by_its_setup_script_and_not_uninstalled_here(monkeypatch, tmp_path):
    from app.features import search_kinds

    home = tmp_path / "searxng"
    monkeypatch.setitem(registry.FEATURES["searxng"], "install_dir", str(home))
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    assert not registry.status("searxng")["installed"]
    calls = []

    def fake_script(spec, *, latest):
        calls.append(latest)
        home.mkdir(exist_ok=True)
        (home / "run.sh").write_text("#!/bin/sh\n")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(search_kinds, "script_install", fake_script)
    assert registry.install("searxng")["installed"]
    registry.update("searxng")
    assert calls == [False, True]
    import pytest

    with pytest.raises(registry.FeatureError, match="Apps"):
        registry.uninstall("searxng")
    assert (home / "run.sh").exists()


def test_browser_is_this_pcs_chrome_and_nothing_is_downloaded(monkeypatch, tmp_path):
    """Web 収集のブラウザは、この PC の Chrome を使う（Playwright 用の Chromium を別に入れない）。
    根拠: 2026-11-21 利用者「Chrome を Playwright などで使っているので二重になっていないか確認して」。"""
    import pytest

    from app.web_collector import browser

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    chrome = tmp_path / "google-chrome"
    chrome.write_text("")
    monkeypatch.setattr(browser, "system_chrome", lambda: str(chrome))
    monkeypatch.setattr(browser, "chrome_version", lambda: "Google Chrome 155.0")
    state = registry.status("web-browser")
    assert state["installed"] and state["enabled"] and state["version"] == "Google Chrome 155.0"
    assert not state["managed"]                                       # 更新・削除のボタンを出さない
    with pytest.raises(registry.FeatureError, match="OS のパッケージ"):
        registry.install("web-browser")
    with pytest.raises(registry.FeatureError):
        registry.uninstall("web-browser")
    assert not (tmp_path / "features" / "web-browser").exists()       # 何もダウンロードしない
    monkeypatch.setattr(browser, "system_chrome", lambda: "")
    assert not registry.status("web-browser")["installed"]


def test_install_only_the_checked_features(admin_client, monkeypatch):
    installed = []
    real_status = registry.status
    monkeypatch.setattr(registry, "install", lambda fid: installed.append(fid) or {"id": fid})
    monkeypatch.setattr(registry, "status", lambda fid: {**real_status(fid), "installed": fid == "searxng"})
    r = admin_client.post("/api/v1/features/install-all-jobs",
                          json={"category": "search", "feature_ids": ["web-extract", "web-robots"]}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    assert r.json()["features"] == ["web-extract", "web-robots"]
    for _ in range(50):
        job = admin_client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
        if job["status"] not in ("queued", "running"):
            break
        asyncio.run(asyncio.sleep(0.05))
    assert installed == ["web-extract", "web-robots"]
    bad = admin_client.post("/api/v1/features/install-all-jobs", json={"category": "search", "feature_ids": ["opencode-v2"]},
                            headers=CSRF_HEADERS)
    assert bad.status_code == 422
