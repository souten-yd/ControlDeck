"""Flow App（Workflow → 単一実行ファイル）の書き出しと実行。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.flow_app import packager, portable

PORTABLE_DEFINITION = {
    "nodes": [
        {"id": "trigger", "type": "trigger", "name": "開始", "config": {
            "inputs": [{"key": "text", "label": "入力", "type": "text", "required": True}],
        }},
        {"id": "up", "type": "string.op", "name": "大文字化", "config": {"op": "upper", "text": "{{trigger.text}}"}},
        {"id": "out", "type": "output.render", "name": "結果", "config": {
            "name": "result", "renderer": "text", "value": "{{up.result}}",
        }},
    ],
    "edges": [{"source": "trigger", "target": "up"}, {"source": "up", "target": "out"}],
}
BLOCKED_DEFINITION = {
    "nodes": [
        {"id": "trigger", "type": "trigger", "name": "開始", "config": {}},
        {"id": "rag", "type": "rag.query", "name": "検索", "config": {}},
    ],
    "edges": [{"source": "trigger", "target": "rag"}],
}


def _workflow(name: str, definition: dict) -> int:
    from app.database import SessionLocal
    from app.models import Workflow

    with SessionLocal() as db:
        row = Workflow(name=name, description="flow app test", definition_json=json.dumps(definition))
        db.add(row)
        db.commit()
        return row.id


def test_portable_analysis_blocks_host_only_nodes():
    assert portable.analyze(PORTABLE_DEFINITION)["portable"] is True
    blocked = portable.analyze(BLOCKED_DEFINITION)
    assert blocked["portable"] is False
    assert blocked["blockedNodeTypes"] == ["rag.query"]
    assert "Knowledge" in blocked["diagnostics"][0]["message"]
    # 無効化済みノードは書き出しを止めない。
    disabled = {**BLOCKED_DEFINITION, "nodes": [
        {**node, "disabled": True} if node["type"] == "rag.query" else node
        for node in BLOCKED_DEFINITION["nodes"]
    ]}
    assert portable.analyze(disabled)["portable"] is True
    # トリガーなしは書き出せない。
    assert portable.analyze({"nodes": [], "edges": []})["portable"] is False


def test_exported_app_runs_standalone(tmp_path):
    """生成した.pyzが、リポジトリ非依存の別プロセスとして実行できる。"""
    target = tmp_path / "demo.pyz"
    meta = packager.build_flow_app(
        name="Upper Demo", description="", definition=PORTABLE_DEFINITION,
        workflow_id=1, output_path=target,
    )
    assert target.is_file() and meta["size"] > 10_000
    assert meta["inputs"][0]["name"] == "text" and meta["outputs"][0]["name"] == "result"

    completed = subprocess.run(
        [sys.executable, str(target), "--input", json.dumps({"text": "hello"}), "--json"],
        capture_output=True, text=True, timeout=120, check=False, cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "SUCCEEDED"
    assert result["outputs"]["result"]["value"] == "HELLO"
    assert [node["status"] for node in result["nodes"]] == ["SUCCEEDED"] * 3

    listing = subprocess.run(
        [sys.executable, str(target), "--info"], capture_output=True, text=True, timeout=60, check=False,
    )
    assert "入力  text" in listing.stdout and "出力  result" in listing.stdout


def _installed_pyinstaller() -> Path | None:
    """testはdata_dirが隔離されるため、実機に導入済みのアドオンを既定の場所から探す。"""
    import shutil

    found = shutil.which("pyinstaller")
    if found:
        return Path(found)
    default = Path.home() / ".local/share/control-deck/features/pyinstaller/venv/bin/pyinstaller"
    return default if default.is_file() else None


def test_binary_export_runs_without_python(tmp_path, monkeypatch):
    """単一バイナリは配布先にPythonが無くても動く（ビルド環境アドオン導入時のみ検証）。"""
    from app.features import registry

    builder = _installed_pyinstaller()
    if builder is None:
        pytest.skip("アプリビルド環境アドオンが未導入です")
    monkeypatch.setattr(registry, "executable", lambda feature_id: builder if feature_id == "pyinstaller" else None)
    target = tmp_path / "demo-bin"
    meta = packager.build_binary(
        name="Upper Demo", description="", definition=PORTABLE_DEFINITION,
        workflow_id=1, output_path=target,
    )
    assert meta["format"] == "binary" and target.is_file() and meta["size"] > 1_000_000
    completed = subprocess.run(
        [str(target), "--input", json.dumps({"text": "hello"}), "--json"],
        capture_output=True, text=True, timeout=300, check=False, cwd=str(tmp_path),
        # PATHにpythonを置かず、配布先へPythonが無い状況を再現する。
        env={"PATH": "/nonexistent", "HOME": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["outputs"]["result"]["value"] == "HELLO"


def test_binary_export_requires_addon(tmp_path, monkeypatch):
    from app.features import registry

    monkeypatch.setattr(registry, "executable", lambda feature_id: None)
    with pytest.raises(packager.FlowAppError, match="アドオン"):
        packager.build_binary(
            name="Upper Demo", description="", definition=PORTABLE_DEFINITION,
            workflow_id=1, output_path=tmp_path / "demo-bin",
        )


def test_export_rejects_non_portable_workflow(tmp_path):
    with pytest.raises(packager.FlowAppError):
        packager.build_flow_app(
            name="blocked", description="", definition=BLOCKED_DEFINITION,
            workflow_id=1, output_path=tmp_path / "blocked.pyz",
        )
    assert not (tmp_path / "blocked.pyz").exists()


def test_flow_app_api_exports_downloads_and_deletes(admin_client, tmp_path, monkeypatch):
    from app.flow_app import router as flow_router

    monkeypatch.setattr(flow_router, "data_dir", lambda: tmp_path)
    workflow_id = _workflow("Export Me", PORTABLE_DEFINITION)
    headers = {"X-Requested-With": "ControlDeck"}

    preview = admin_client.get(f"/api/v1/flow-apps/{workflow_id}/preview")
    assert preview.status_code == 200
    assert preview.json()["portable"] is True and preview.json()["inputs"][0]["name"] == "text"

    created = admin_client.post(f"/api/v1/flow-apps/{workflow_id}/exports", json={}, headers=headers)
    assert created.status_code == 201, created.text
    filename = created.json()["filename"]
    assert filename.endswith(".pyz") and created.json()["checksum"]
    assert (tmp_path / "flow-apps" / str(workflow_id) / filename).is_file()

    listed = admin_client.get(f"/api/v1/flow-apps/{workflow_id}/exports")
    assert [item["filename"] for item in listed.json()] == [filename]

    download = admin_client.get(f"/api/v1/flow-apps/{workflow_id}/exports/{filename}/download")
    assert download.status_code == 200 and download.content[:2] == b"#!"

    # path traversalと未知のfileは404。
    assert admin_client.get(f"/api/v1/flow-apps/{workflow_id}/exports/..%2Fescape.pyz/download").status_code == 404
    assert admin_client.get(f"/api/v1/flow-apps/{workflow_id}/exports/other.pyz/download").status_code == 404

    removed = admin_client.delete(f"/api/v1/flow-apps/{workflow_id}/exports/{filename}", headers=headers)
    assert removed.status_code == 204
    assert admin_client.get(f"/api/v1/flow-apps/{workflow_id}/exports").json() == []

    blocked_id = _workflow("Blocked", BLOCKED_DEFINITION)
    rejected = admin_client.post(f"/api/v1/flow-apps/{blocked_id}/exports", json={}, headers=headers)
    assert rejected.status_code == 422 and "rag" in rejected.json()["detail"].lower()
    assert admin_client.post("/api/v1/flow-apps/999999/exports", json={}, headers=headers).status_code == 404


def test_flow_app_capability_reports_supported_nodes(admin_client):
    capability = admin_client.get("/api/v1/flow-apps/capability")
    assert capability.status_code == 200
    payload = capability.json()
    formats = {item["id"]: item for item in payload["formats"]}
    assert formats["pyz"]["available"] is True and payload["available"] is True
    # 単一バイナリはビルド環境アドオンの導入状況に従う（未導入なら選べない）。
    assert formats["binary"]["available"] in {True, False}
    assert "llm.chat" in payload["supportedNodes"] and "rag.query" not in payload["supportedNodes"]


def test_bundle_sources_stay_in_sync_with_host_nodes():
    """同梱するのは本体のnodes.pyそのもの。コピー元が消えたら書き出しは壊れる。"""
    repo_root = Path(packager.__file__).resolve().parents[2]
    for source, _ in packager.COPIED_MODULES:
        assert (repo_root / source).is_file(), source
    assert (packager.BUNDLE_DIR / "__main__.py").is_file()
    assert (packager.BUNDLE_DIR / "flowapp" / "runner.py").is_file()
    assert (packager.BUNDLE_DIR / "flowapp" / "ui.html").is_file()
