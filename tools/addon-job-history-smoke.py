"""Isolated real HTTP acceptance of persisted Add-on Job control.

Run with the Host venv and PYTHONPATH=backend. Never uses the installed DB or
restarts the installed service. Its own bounded systemd unit serves only the
production Job router/auth against a fresh fixture database.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    root = args.serve or Path(tempfile.mkdtemp(prefix="cd-job-history-"))
    config = root / "config.yaml"
    if args.serve is None:
        config.write_text(f"data_dir: {root}/data\n", encoding="utf-8")
    os.environ["CONTROL_DECK_CONFIG"] = str(config)
    os.environ["CONTROL_DECK_DB_URL"] = f"sqlite:///{root}/fixture.db"

    from app.jobs import service as jobs
    if args.serve is not None:
        from fastapi import FastAPI
        import uvicorn
        from app.addon_runtime.jobs import router
        assert not jobs._jobs
        assert jobs.recover_on_startup() == 1
        app = FastAPI()
        app.include_router(router, prefix="/api/v1/addon-runtime")
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
        return

    import httpx
    from app.addons import registry, tokens
    from app.addons.schema import parse_manifest
    from app.bootstrap import create_admin, init_db, seed_roles
    from app.database import SessionLocal

    init_db()
    with SessionLocal() as db:
        seed_roles(db)
        owner = create_admin(db, "history-smoke", uuid.uuid4().hex).id
    repo = Path(__file__).resolve().parents[1]
    registry.install(parse_manifest(json.loads((repo / "tools/fake-addon/control-deck-addon.json").read_text())))
    registry.set_enabled("fake-addon", True, grants=["jobs.write"])
    fixture = jobs.create_external("fake-addon", "history smoke", owner_user_id=owner)
    token = tokens.issue("fake-addon", subject=f"job:{fixture.id}", kind="service", actor_user_id=owner)
    wrong = tokens.issue("fake-addon", subject="job:not-the-fixture", kind="service", actor_user_id=owner)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    unit = f"cd-job-history-smoke-{uuid.uuid4().hex[:10]}"
    subprocess.run([
        "systemd-run", "--user", "--collect", f"--unit={unit}",
        "--property=RuntimeMaxSec=60", f"--working-directory={repo}/backend",
        f"--setenv=PYTHONPATH={repo}/backend", sys.executable, str(Path(__file__).resolve()),
        "--serve", str(root), "--port", str(port),
    ], check=True)

    def headers(bearer: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {bearer}", "X-Control-Deck-Addon-ID": "fake-addon"}

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            path = f"/api/v1/addon-runtime/fake-addon/jobs/{fixture.id}"
            deadline = time.monotonic() + 20
            while True:
                try:
                    response = client.get(f"{path}/control", headers=headers(token))
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            assert response.status_code == 200, response.status_code
            assert response.json()["status"] == "interrupted"
            assert response.json()["cancel_requested"] is False
            denied = client.get(f"{path}/control", headers=headers(wrong))
            refresh = client.post(f"{path}/credential/refresh", headers=headers(token))
            update = client.patch(path, json={"phase": "succeeded", "status": "succeeded"}, headers=headers(token))
            assert (denied.status_code, refresh.status_code, update.status_code) == (403, 404, 404)
            assert jobs._db_get(fixture.id)["status"] == "interrupted"
            evidence = {"control": response.json(), "wrong_job": denied.status_code,
                        "refresh": refresh.status_code, "update": update.status_code,
                        "db_status": "interrupted", "fixture_root": str(root), "unit": unit,
                        "scope": "fresh-process production Job router and auth; not full installed Host"}
            (root / "observations.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print(json.dumps(evidence))
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], check=True)


if __name__ == "__main__":
    main()
