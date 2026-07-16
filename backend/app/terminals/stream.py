"""WebSocketから独立した端末出力streamとbounded sequence journal。"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import Callable

from app.terminals.manager import TerminalConnection, TerminalManager

JOURNAL_MAX_BYTES = 4 * 1024 * 1024
JOURNAL_MAX_CHUNKS = 4096
STREAM_GRACE_SECONDS = 30.0


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    data: bytes


class OutputJournal:
    """連続sequenceを持つbyte/chunk上限付きring buffer。"""

    def __init__(self, max_bytes: int = JOURNAL_MAX_BYTES, max_chunks: int = JOURNAL_MAX_CHUNKS) -> None:
        self.max_bytes = max_bytes
        self.max_chunks = max_chunks
        self._entries: deque[JournalEntry] = deque()
        self._bytes = 0
        self._next_sequence = 1

    @property
    def latest_sequence(self) -> int:
        return self._next_sequence - 1

    @property
    def oldest_sequence(self) -> int:
        return self._entries[0].sequence if self._entries else self._next_sequence

    @property
    def byte_count(self) -> int:
        return self._bytes

    @property
    def chunk_count(self) -> int:
        return len(self._entries)

    def append(self, data: bytes) -> JournalEntry:
        entry = JournalEntry(self._next_sequence, data)
        self._next_sequence += 1
        self._entries.append(entry)
        self._bytes += len(data)
        while self._entries and (self._bytes > self.max_bytes or len(self._entries) > self.max_chunks):
            removed = self._entries.popleft()
            self._bytes -= len(removed.data)
        return entry

    def after(self, sequence: int, through: int | None = None) -> list[JournalEntry] | None:
        """範囲外ならNone。sequence位置は連続値から算出し、先頭から探索しない。"""
        latest = self.latest_sequence if through is None else min(through, self.latest_sequence)
        if sequence > self.latest_sequence or sequence < self.oldest_sequence - 1:
            return None
        start = max(0, sequence + 1 - self.oldest_sequence)
        count = max(0, latest - max(sequence, self.oldest_sequence - 1))
        return list(islice(self._entries, start, start + count))

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0


class TerminalClientStream:
    """1 browser instanceのtmux attachを再接続間も保持する。"""

    def __init__(self, session_id: str, client_instance_id: str, connection: TerminalConnection) -> None:
        self.session_id = session_id
        self.client_instance_id = client_instance_id
        self.connection = connection
        self.journal = OutputJournal()
        self.connection_generation = 0
        self.subscriber: asyncio.Queue[JournalEntry | None] | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.cleanup_handle: asyncio.TimerHandle | None = None
        self.closed = False

    def start(self, on_eof: Callable[[], None]) -> None:
        if self.reader_task is not None:
            return

        async def read() -> None:
            await self.connection.read_loop(self._on_data)
            self.closed = True
            if self.subscriber is not None:
                self.subscriber.put_nowait(None)
            on_eof()

        self.reader_task = asyncio.create_task(read())

    async def _on_data(self, data: bytes) -> None:
        entry = self.journal.append(data)
        queue = self.subscriber
        if queue is not None:
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                # WebSocketが詰まってもPTY readerを止めず、resume journalへ退避する。
                self.subscriber = None
                try:
                    queue.get_nowait()
                    queue.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def attach(self, connection_generation: int) -> asyncio.Queue[JournalEntry | None]:
        if self.closed or connection_generation <= self.connection_generation:
            raise ValueError("stale connection generation")
        if self.cleanup_handle is not None:
            self.cleanup_handle.cancel()
            self.cleanup_handle = None
        self.connection_generation = connection_generation
        previous = self.subscriber
        if previous is not None:
            try:
                previous.put_nowait(None)
            except asyncio.QueueFull:
                pass
        queue: asyncio.Queue[JournalEntry | None] = asyncio.Queue(maxsize=512)
        self.subscriber = queue
        return queue

    def detach(self, queue: asyncio.Queue[JournalEntry | None]) -> None:
        if self.subscriber is queue:
            self.subscriber = None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.cleanup_handle is not None:
            self.cleanup_handle.cancel()
            self.cleanup_handle = None
        if self.reader_task is not None:
            self.reader_task.cancel()
        self.connection.close()
        self.journal.clear()


class TerminalStreamRegistry:
    def __init__(self, terminal_manager: TerminalManager) -> None:
        self.manager = terminal_manager
        self._streams: dict[tuple[str, str], TerminalClientStream] = {}

    def acquire(
        self,
        session_id: str,
        client_instance_id: str,
        connection_generation: int,
        rows: int,
        cols: int,
    ) -> tuple[TerminalClientStream, bool, asyncio.Queue[JournalEntry | None]]:
        key = (session_id, client_instance_id)
        stream = self._streams.get(key)
        created = stream is None or stream.closed
        if created:
            connection = self.manager.open_connection(session_id, rows, cols)
            stream = TerminalClientStream(session_id, client_instance_id, connection)
            self._streams[key] = stream
            stream.start(lambda: self._remove_if_same(key, stream))
        queue = stream.attach(connection_generation)
        return stream, created, queue

    def release(self, stream: TerminalClientStream, queue: asyncio.Queue[JournalEntry | None]) -> None:
        stream.detach(queue)
        # 新世代が既に購読中なら、旧WebSocketのfinallyでcleanupを予約しない。
        if stream.subscriber is not None:
            return
        if stream.closed:
            self._remove_if_same((stream.session_id, stream.client_instance_id), stream)
            return
        loop = asyncio.get_running_loop()
        stream.cleanup_handle = loop.call_later(STREAM_GRACE_SECONDS, self._expire, stream)

    def close_session(self, session_id: str) -> None:
        for key, stream in list(self._streams.items()):
            if key[0] == session_id:
                self._streams.pop(key, None)
                stream.close()

    def _expire(self, stream: TerminalClientStream) -> None:
        key = (stream.session_id, stream.client_instance_id)
        self._remove_if_same(key, stream)
        stream.close()

    def _remove_if_same(self, key: tuple[str, str], stream: TerminalClientStream) -> None:
        if self._streams.get(key) is stream:
            self._streams.pop(key, None)

    def stream_count(self) -> int:
        return len(self._streams)
