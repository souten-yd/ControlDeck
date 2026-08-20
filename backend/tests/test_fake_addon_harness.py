from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def _fake_app():
    path = Path(__file__).parents[2] / "tools" / "fake-addon" / "app.py"
    spec = importlib.util.spec_from_file_location("control_deck_fake_addon", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.app


def test_fake_addon_manifest_is_valid():
    from app.addons.schema import AddonManifestV2, load_manifest_file

    path = Path(__file__).parents[2] / "tools" / "fake-addon" / "control-deck-addon.json"
    parsed = load_manifest_file(path)
    assert isinstance(parsed.manifest, AddonManifestV2)
    assert parsed.manifest.id == "fake-addon"
    assert parsed.warnings == ()


def test_fake_addon_health_states_and_partial_availability():
    with TestClient(_fake_app()) as client:
        initial = client.get("/health")
        assert initial.status_code == 200
        assert initial.json()["status"] == "healthy"

        degraded = client.post("/test/health", json={"status": "degraded", "video_available": False})
        assert degraded.status_code == 200
        payload = degraded.json()
        assert payload["contributions"]["navigation:workspace"] == "available"
        assert payload["contributions"]["workflow_executor:fake.video"]["reason_code"] == "worker_not_installed"

        setup = client.post("/test/health", json={"status": "setup_required"}).json()
        assert setup["setup"][1]["state"] == "missing"
        assert setup["setup"][1]["action"]["kind"] == "open_route"


def test_fake_gpu_job_completes_and_can_be_canceled():
    with TestClient(_fake_app()) as client:
        created = client.post("/fake-gpu/jobs", json={"duration_sec": 0.05, "vram_bytes": 123456})
        assert created.status_code == 202
        job_id = created.json()["id"]
        for _ in range(30):
            job = client.get(f"/fake-gpu/jobs/{job_id}").json()
            if job["status"] == "succeeded":
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded"
        assert job["vram_bytes"] == 123456

        created = client.post("/fake-gpu/jobs", json={"duration_sec": 2, "vram_bytes": 987654}).json()
        canceled = client.delete(f"/fake-gpu/jobs/{created['id']}")
        assert canceled.status_code == 200
        for _ in range(30):
            job = client.get(f"/fake-gpu/jobs/{created['id']}").json()
            if job["status"] == "canceled":
                break
            time.sleep(0.01)
        assert job["status"] == "canceled"
