"""Real HTTP acceptance of queue receipts using an isolated production broker.

Only the broker is production code. The unauthenticated loopback fixture uses
fake capacity and a gated resident; it never touches installed Jobs or GPUs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from app.resources.schema import ResourceRequest


def serve(port: int) -> None:
    import asyncio
    from fastapi import FastAPI
    import uvicorn
    from app.resources.broker import ResourceBroker
    from app.resources.devices import fake_devices
    from app.resources.probes import ProviderRegistry
    from app.resources.providers import ProviderReservation, ResourceProvider

    class Resident(ResourceProvider):
        id = "fixture-resident"
        can_step_aside = True

        def __init__(self) -> None:
            self.reserved = 40
            self.entered = False
            self.finish = asyncio.Event()

        def reservations(self) -> list[ProviderReservation]:
            return ([ProviderReservation(self.id, "gpu0", "fixture:resident", self.reserved)]
                    if self.reserved else [])

        async def step_aside(self, device_id: str) -> tuple[bool, str, int]:
            self.entered = True
            await self.finish.wait()
            freed, self.reserved = self.reserved, 0
            return True, "released", freed

    resident = Resident()
    broker = ResourceBroker(fake_devices(100), ProviderRegistry([resident]))
    app = FastAPI()

    @app.get("/state")
    async def state() -> dict:
        return {"entered": resident.entered, "finish": resident.finish.is_set(),
                "reserved": resident.reserved, "leases": len(broker.leases.current()),
                "requests": [v.model_dump(mode="json") for v in await broker.request_statuses()]}

    @app.post("/requests", status_code=202)
    async def submit(body: ResourceRequest) -> dict:
        return (await broker.submit(body)).model_dump(mode="json")

    @app.get("/requests/{request_id}")
    async def poll(request_id: str) -> dict:
        return (await broker.keep_waiting(request_id)).model_dump(mode="json")

    @app.delete("/requests/{request_id}")
    async def cancel(request_id: str) -> dict:
        return (await broker.cancel_request(request_id)).model_dump(mode="json")

    @app.post("/finish")
    async def finish() -> dict:
        resident.finish.set()
        if broker._room_task is not None:
            await broker._room_task
        return await state()

    @app.post("/release/{lease_id}")
    async def release(lease_id: str) -> dict:
        return (await broker.release(lease_id)).model_dump(mode="json")

    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=int)
    args = parser.parse_args()
    if args.serve is not None:
        serve(args.serve)
        return

    import httpx

    repo = Path(__file__).resolve().parents[1]
    root = Path(tempfile.mkdtemp(prefix="cd-resource-receipt-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    unit = f"cd-resource-receipt-{uuid.uuid4().hex[:10]}"
    subprocess.run([
        "systemd-run", "--user", "--collect", f"--unit={unit}",
        "--property=RuntimeMaxSec=60", f"--working-directory={repo}/backend",
        f"--setenv=PYTHONPATH={repo}/backend", sys.executable,
        str(Path(__file__).resolve()), "--serve", str(port),
    ], check=True)
    started = time.monotonic()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    client.get("/state").raise_for_status()
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)

            body = {"owner": "addon:fixture", "job_id": "cancel-me", "device": "auto",
                    "vram": {"resident_bytes": 80, "execution_peak_bytes": 80,
                             "cold_load_peak_bytes": 80, "headroom_bytes": 0, "confidence": "measured"},
                    "compute_mode": "exclusive-required", "priority": 0, "class": "interactive",
                    "max_wait_sec": 300, "on_insufficient": "queue"}
            receipt_started = time.monotonic()
            response = client.post("/requests", json=body)
            receipt_sec = time.monotonic() - receipt_started
            assert response.status_code == 202, response.text
            receipt = response.json()
            assert receipt["state"] == "waiting" and receipt["lease_id"] is None
            state = client.get("/state").json()
            assert state["entered"] and not state["finish"] and state["leases"] == 0, state
            path = f"/requests/{receipt['request_id']}"
            assert client.get(path).json()["state"] == "waiting"
            assert client.delete(path).json()["state"] == "canceled"
            body["job_id"] = "grant-me"
            second = client.post("/requests", json=body)
            assert second.status_code == 202 and second.json()["state"] == "waiting"
            second_path = f"/requests/{second.json()['request_id']}"
            finished = client.post("/finish")
            finished.raise_for_status()
            assert client.get(path).json()["state"] == "canceled"
            granted = client.get(second_path).json()
            assert granted["state"] == "granted" and granted["lease_id"]
            released = client.post(f"/release/{granted['lease_id']}")
            released.raise_for_status()
            final = client.get("/state").json()
            assert final["leases"] == 0 and final["reserved"] == 0, final
            result = {"mode": "real HTTP / production broker / gated provider fixture",
                      "unit": unit, "elapsed_sec": time.monotonic() - started,
                      "receipt_sec": receipt_sec, "waiting_before_drain": state,
                      "final": final, "passed": True}
            (root / "observations.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps({"evidence": str(root), "receipt_sec": receipt_sec,
                              "elapsed_sec": result["elapsed_sec"], "passed": True}))
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], check=True)


if __name__ == "__main__":
    main()
