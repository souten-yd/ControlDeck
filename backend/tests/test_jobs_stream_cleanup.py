from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anyio
import pytest

from app.jobs import router


@pytest.mark.parametrize("cancel_kind", ["asyncio", "scope"])
def test_cancelled_stream_joins_update_and_disconnect_waiters(
    monkeypatch: pytest.MonkeyPatch, cancel_kind: str,
) -> None:
    async def scenario() -> None:
        started = [asyncio.Event(), asyncio.Event()]
        stopped: list[int] = []

        async def wait(index: int) -> None:
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(index)

        class Socket:
            async def accept(self) -> None:
                pass

            async def send_text(self, value: str) -> None:
                assert '"snapshot"' in value

            async def receive(self) -> dict[str, object]:
                await wait(1)
                return {}

        async def authenticate(*args: object) -> SimpleNamespace:
            return SimpleNamespace(id=1)

        async def listing(*args: object) -> list[dict[str, object]]:
            return []

        async def wait_global(*args: object) -> int:
            await wait(0)
            return 0

        monkeypatch.setattr(router, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr(router, "authenticate_websocket", authenticate)
        monkeypatch.setattr(router.jobs, "list_any", listing)
        monkeypatch.setattr(router.jobs, "stream_revision", lambda: 0)
        monkeypatch.setattr(router.jobs, "wait_global", wait_global)
        if cancel_kind == "scope":
            async with anyio.create_task_group() as group:
                group.start_soon(router.stream_jobs, Socket())
                await asyncio.gather(*(event.wait() for event in started))
                group.cancel_scope.cancel()
        else:
            task = asyncio.create_task(router.stream_jobs(Socket()))
            await asyncio.gather(*(event.wait() for event in started))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sorted(stopped) == [0, 1]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())
