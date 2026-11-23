"""機能と拡張: Playwright のブラウザ（3 つの版・別々の置き場）と、OS のパッケージ（apt）の案内の項目。

目的:
- E2E（frontend）・本体の Python・Playwright MCP が別々の置き場を使い、箱から見えること（同じ置き場だと、版の違う
  `playwright install` が互いのブラウザを消す。2026-11-23 に MCP で `install ffmpeg` したら E2E の chromium-1228 などが消えた）。
- 状態は置き場の中身と、その版が求める revision（browsers.json）の比較で決まり、足りないものが分かること。
- 導入は argv（shell なし）で、版ごとに別の PLAYWRIGHT_BROWSERS_PATH で走り、MCP が未導入なら飛ばすこと。実際には走らせない（run はモック）。
- apt のパッケージは sudo が要るのでボタンでは入れず、足りないものだけのコマンドをコピーさせること。エラーの文が項目を案内すること。
根拠: 2026-11-23 利用者「設定の機能の追加のところに導入ボタンを」「追加で入れないといけないものについては機能と拡張に導入ボタンを付けること」。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.features import playwright_browsers as pb
from app.features import registry, system_packages


def _core(root: Path, revisions: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    (root / "cli.js").write_text("", encoding="utf-8")
    (root / "browsers.json").write_text(json.dumps({"browsers": [{"name": n, "revision": r} for n, r in revisions.items()]}))
    return root


def _installed(store: Path, name: str, revision: str) -> None:
    folder = store / f"{name.replace('-', '_')}-{revision}"
    folder.mkdir(parents=True)
    (folder / "INSTALLATION_COMPLETE").write_text("")


@pytest.fixture()
def world(tmp_path):
    repo, data = tmp_path / "repo", tmp_path / "data"
    _core(repo / "frontend/node_modules/playwright-core",
          {"chromium": "1228", "chromium-headless-shell": "1228", "webkit": "2311", "ffmpeg": "1011", "firefox": "1"})
    driver = tmp_path / "py-driver"
    driver.mkdir()
    (driver / "browsers.json").write_text(json.dumps({"browsers": [{"name": "chromium", "revision": "1234"}]}))
    return {"repo_root": repo, "python_driver": driver}, data


def test_three_versions_use_separate_stores(tmp_path):
    data = tmp_path / "data"
    stores = {c.id: c.store for c in pb.components(data, python_driver=None)}
    assert stores["e2e"].name == "ms-playwright" and stores["python"].name == "ms-playwright-py" and stores["mcp"].name == "ms-playwright-mcp"
    assert len(set(stores.values())) == 3 and all(p.parent == data.resolve() / "cache" for p in stores.values())


def test_sandbox_sees_e2e_and_mcp_stores_read_only(tmp_path, monkeypatch):
    from app.maintenance import sandbox

    monkeypatch.setattr(sandbox, "_data_dir", lambda: tmp_path)
    for store in ("ms-playwright", "ms-playwright-mcp", "ms-playwright-py"):
        (tmp_path / "cache" / store).mkdir(parents=True)
    names = {p.name for p in sandbox._tool_dirs()}
    assert {"ms-playwright", "ms-playwright-mcp"} <= names and "ms-playwright-py" not in names


def test_mcp_environment_points_to_its_own_store(monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    monkeypatch.setattr(pb, "_data", lambda: tmp_path)
    assert pb.mcp_browsers_path() == tmp_path.resolve() / "cache" / "ms-playwright-mcp"
    assert pb.python_browsers_path() == tmp_path.resolve() / "cache" / "ms-playwright-py"
    monkeypatch.setattr(provider, "get_settings", lambda: {"ui_verify": True})
    monkeypatch.setattr(provider.registry, "mcp_entry", lambda feature_id: tmp_path / "index.js")
    monkeypatch.setattr(provider, "_browser_executable", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(provider, "ui_verify_dir", lambda job_id: tmp_path / "shots")
    server = provider.ui_verify_server("job1")
    assert server["environment"] == {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path.resolve() / "cache" / "ms-playwright-mcp")}


def test_status_lists_what_is_missing_then_complete(world):
    kwargs, data = world
    first = pb.overview(data, **kwargs)
    assert first["available"] and not first["complete"]
    assert pb.e2e_missing(data, **kwargs) == ["chromium-1228", "chromium-headless-shell-1228", "webkit-2311", "ffmpeg-1011"]
    mcp = next(c for c in first["components"] if c["id"] == "mcp")
    assert not mcp["applicable"]   # MCP が未導入なら比べない
    for name, rev in (("chromium", "1228"), ("chromium_headless_shell", "1228"), ("webkit", "2311"), ("ffmpeg", "1011")):
        _installed(data / "cache" / "ms-playwright", name, rev)
    _installed(data / "cache" / "ms-playwright-py", "chromium", "1234")
    done = pb.overview(data, **kwargs)
    assert done["complete"] and done["missing"] == [] and pb.e2e_missing(data, **kwargs) == []


def test_old_revision_counts_as_missing_after_version_bump(world):
    kwargs, data = world
    _installed(data / "cache" / "ms-playwright-py", "chromium", "1200")   # 古い revision だけ
    states = {c["id"]: c for c in pb.overview(data, **kwargs)["components"]}
    assert states["python"]["missing"] == ["chromium-1234"]


def test_install_runs_each_version_into_its_own_store_without_shell(world, monkeypatch):
    kwargs, data = world
    _core(data / "features/playwright-mcp/node_modules/playwright-core", {"ffmpeg": "1013"})
    calls = []

    def fake_run(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    assert pb.install_missing(data, run=fake_run, **kwargs) == ["e2e", "python", "mcp"]
    by_store = {Path(o["env"]["PLAYWRIGHT_BROWSERS_PATH"]).name: a for a, o in calls}
    assert by_store["ms-playwright"][2:] == ["install", *pb.E2E_BROWSERS] and by_store["ms-playwright"][1].endswith("frontend/node_modules/playwright-core/cli.js")
    assert by_store["ms-playwright-py"][1:] == ["-m", "playwright", "install", "chromium"]
    assert by_store["ms-playwright-mcp"][2:] == ["install", "ffmpeg"] and "playwright-mcp" in by_store["ms-playwright-mcp"][1]
    assert all(isinstance(a, list) and not o.get("shell") and "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" not in o["env"] for a, o in calls)


def test_install_skips_mcp_when_not_installed_and_complete_parts(world):
    kwargs, data = world
    calls = []
    pb.install_missing(data, run=lambda argv, **o: calls.append(o["env"]["PLAYWRIGHT_BROWSERS_PATH"]) or subprocess.CompletedProcess(argv, 0, "", ""), **kwargs)
    assert [Path(c).name for c in calls] == ["ms-playwright", "ms-playwright-py"]


def test_install_failure_is_reported_in_japanese(world):
    kwargs, data = world
    with pytest.raises(pb.PlaywrightBrowsersError, match="導入に失敗"):
        pb.install_missing(data, run=lambda argv, **o: subprocess.CompletedProcess(argv, 1, "", "boom"), **kwargs)


def test_registry_feature_never_auto_updates_and_installs_through_module(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    spec = registry.FEATURES["playwright-browsers"]
    assert spec["kind"] == "playwright-browsers" and registry.category("playwright-browsers") == "search"
    assert "Playwright のブラウザ" in spec["name"]
    state = {"done": False}
    overview = lambda: {"available": True, "complete": state["done"], "missing": [] if state["done"] else ["x: y"],
                        "components": [], "any_present": False}
    monkeypatch.setattr(pb, "overview", overview)
    monkeypatch.setattr(pb, "install_missing", lambda: state.update(done=True) or ["e2e"])
    assert not registry.status("playwright-browsers")["installed"]
    assert registry.install("playwright-browsers")["installed"]
    assert registry.update("playwright-browsers")["installed"]
    with pytest.raises(registry.FeatureError):
        registry.uninstall("playwright-browsers")
    from app.features import auto_update

    assert "playwright-browsers" not in [i["id"] for i in auto_update.targets()]


# ---- OS のパッケージ ----

def test_system_packages_status_and_copyable_command(monkeypatch):
    present = {"pdfinfo", "pdftotext", "pdftoppm", "tesseract"}
    monkeypatch.setattr(system_packages.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    monkeypatch.setattr(system_packages.glob, "glob", lambda pattern: [])
    state = registry.status("system-ocr")
    assert not state["installed"] and state["missing_packages"] == ["tesseract-ocr-jpn"]
    assert state["install_command"] == "sudo apt install -y tesseract-ocr-jpn"
    monkeypatch.setattr(system_packages.glob, "glob", lambda pattern: ["/usr/share/tessdata/jpn.traineddata"])
    assert registry.status("system-ocr")["installed"] and registry.status("system-ocr")["install_command"] == ""
    monkeypatch.setattr(system_packages.shutil, "which", lambda name: None)
    assert registry.status("system-zstd")["install_command"] == "sudo apt install -y zstd"
    assert registry.status("system-gh")["missing_packages"] == ["gh"]


def test_system_packages_are_never_installed_by_a_button(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    with pytest.raises(registry.FeatureError, match="sudo"):
        registry.install("system-zstd")
    with pytest.raises(registry.FeatureError):
        registry.update("system-zstd")
    assert called == []


def test_errors_point_to_the_feature_item(monkeypatch):
    monkeypatch.setattr(system_packages.shutil, "which", lambda name: None)
    monkeypatch.setattr(system_packages.glob, "glob", lambda pattern: [])
    from app.knowledge import pdf_ingest
    from app.models_mgmt import gpu_release

    with pytest.raises(pdf_ingest.PdfError, match="設定 → 機能と拡張 → PDF・画像の文字の読み取り.*sudo apt install -y poppler-utils tesseract-ocr tesseract-ocr-jpn"):
        pdf_ingest._require("pdftotext")
    with pytest.raises(gpu_release.ReleaseError, match="設定 → 機能と拡張 → zstd.*sudo apt install -y zstd"):
        gpu_release._decompress_zst(Path("a.zst"), Path("a"))


def test_maya_tokenizers_guidance_and_ensure(monkeypatch, tmp_path):
    from app.models_mgmt import maya_probe, maya_s

    monkeypatch.setattr(maya_s, "source", lambda: tmp_path)
    assert maya_s.ensure_tokenizers() is False   # venv が無ければ何もしない
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    state = {"ok": False}
    calls = []

    def fake_run(argv, **options):
        calls.append(argv)
        if "pip" in argv:
            state["ok"] = True
        return subprocess.CompletedProcess(argv, 0 if state["ok"] or "pip" in argv else 1, "", "")

    monkeypatch.setattr(maya_s.subprocess, "run", fake_run)
    assert maya_s.ensure_tokenizers() is True
    assert any(a[1:4] == ["-m", "pip", "install"] and maya_s.TOKENIZERS_PACKAGE in a for a in calls)
    monkeypatch.setitem(__import__("sys").modules, "tokenizers", None)
    assert "機能と拡張 → Project Maya" in maya_probe.blockers("deepseek")[0]
