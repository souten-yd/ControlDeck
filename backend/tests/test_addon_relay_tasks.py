from __future__ import annotations

import asyncio

import anyio
import pytest

from app.websocket_tasks import run_websocket_tasks


@pytest.mark.parametrize("outcome", ["complete", "error", "asyncio_cancel", "scope_cancel"])
def test_relay_joins_every_sibling_before_returning(outcome: str) -> None:
    async def scenario() -> None:
        started = [asyncio.Event(), asyncio.Event()]
        stopped: list[int] = []
        release = asyncio.Event()

        async def direction(index: int) -> None:
            started[index].set()
            try:
                if index == 0 and outcome in {"complete", "error"}:
                    await release.wait()
                    if outcome == "error":
                        raise OSError("upstream failed")
                    return
                await asyncio.Event().wait()
            finally:
                # Real cleanup may require await; the relay must join it.
                with anyio.CancelScope(shield=True):
                    await asyncio.sleep(0.01)
                stopped.append(index)

        async def relay() -> None:
            await run_websocket_tasks(lambda: direction(0), lambda: direction(1))

        if outcome == "scope_cancel":
            async with anyio.create_task_group() as group:
                group.start_soon(relay)
                await asyncio.gather(*(event.wait() for event in started))
                group.cancel_scope.cancel()
        else:
            task = asyncio.create_task(relay())
            await asyncio.gather(*(event.wait() for event in started))
            if outcome == "asyncio_cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "error":
                release.set()
                with pytest.raises(OSError, match="upstream failed"):
                    await task
            else:
                release.set()
                await task
        assert sorted(stopped) == [0, 1]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())
