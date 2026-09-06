"""Real loopback WebSocket smoke of the production relay task lifetime helper.

Uses an owned transient systemd unit and an unauthenticated *fixture* app, not
the installed Host API. Authentication and authorization are covered separately.
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

from starlette.websockets import WebSocket


def serve(port: int) -> None:
    import asyncio
    from fastapi import FastAPI
    import uvicorn
    from app.websocket_tasks import run_websocket_tasks

    app = FastAPI()
    state = {"active": 0, "closed": 0, "errors": 0, "directions": 0}

    @app.get("/state")
    async def status() -> dict[str, int]:
        return dict(state)

    async def relay(websocket: WebSocket) -> None:
        await websocket.accept()
        state["active"] += 1

        async def reader() -> None:
            state["directions"] += 1
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") == "fail":
                        raise OSError("fixture upstream failure")
                    if message.get("text") is not None:
                        await websocket.send_text(message["text"])
                    elif message.get("bytes") is not None:
                        await websocket.send_bytes(message["bytes"])
            finally:
                state["directions"] -= 1

        async def pending_direction() -> None:
            state["directions"] += 1
            try:
                await asyncio.Event().wait()
            finally:
                state["directions"] -= 1

        try:
            await run_websocket_tasks(reader, pending_direction)
        except OSError:
            state["errors"] += 1
            await websocket.close(code=4502)
        finally:
            state["active"] -= 1
            state["closed"] += 1

    app.websocket("/relay")(relay)
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=int)
    args = parser.parse_args()
    if args.serve is not None:
        serve(args.serve)
        return

    import httpx
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosedError

    repo = Path(__file__).resolve().parents[1]
    root = Path(tempfile.mkdtemp(prefix="cd-relay-lifetime-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    unit = f"cd-relay-lifetime-{uuid.uuid4().hex[:10]}"
    subprocess.run([
        "systemd-run", "--user", "--collect", f"--unit={unit}",
        "--property=RuntimeMaxSec=60", f"--working-directory={repo}/backend",
        f"--setenv=PYTHONPATH={repo}/backend", sys.executable,
        str(Path(__file__).resolve()), "--serve", str(port),
    ], check=True)
    started = time.monotonic()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    response = client.get("/state")
                    response.raise_for_status()
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            observations = []
            for index in range(21):
                with connect(f"ws://127.0.0.1:{port}/relay", proxy=None) as connection:
                    if index == 20:
                        connection.send("fail")
                        try:
                            connection.recv(timeout=2)
                            raise AssertionError("failure must close the socket")
                        except ConnectionClosedError as exc:
                            assert exc.rcvd is not None and exc.rcvd.code == 4502
                    else:
                        connection.send("text")
                        assert connection.recv(timeout=2) == "text"
                        connection.send(b"binary")
                        assert connection.recv(timeout=2) == b"binary"
                deadline = time.monotonic() + 2
                while True:
                    state = client.get("/state").json()
                    if state["closed"] == index + 1:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError(state)
                    time.sleep(0.01)
                assert state["active"] == state["directions"] == 0, state
                assert state["errors"] == int(index == 20), state
                observations.append(state)
            result = {"mode": "isolated real websocket / production helper / fixture app",
                      "unit": unit, "elapsed_sec": time.monotonic() - started,
                      "observations": observations}
            (root / "observations.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps({"evidence": str(root), "elapsed_sec": result["elapsed_sec"],
                              "final": observations[-1]}))
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], check=True)


if __name__ == "__main__":
    main()
