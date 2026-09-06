"""Structured lifetime for the concurrent directions of a WebSocket relay."""

from collections.abc import Awaitable, Callable

import anyio


async def run_websocket_tasks(*operations: Callable[[], Awaitable[None]]) -> None:
    """Stop siblings on first completion and join them before leaving the relay.

    A task group also owns cleanup when the ASGI caller is cancelled. Detached
    asyncio tasks followed by gather can lose that ownership during disconnect.
    Raise the first operational error after cleanup, instead of marking it as
    successful activity. Cancellation is not an operational error.
    """
    errors: list[Exception] = []
    async with anyio.create_task_group() as group:
        async def run(operation: Callable[[], Awaitable[None]]) -> None:
            try:
                await operation()
            except Exception as exc:
                errors.append(exc)
            finally:
                group.cancel_scope.cancel()

        for operation in operations:
            group.start_soon(run, operation)
    if errors:
        raise errors[0]
