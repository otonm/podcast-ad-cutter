"""QueueLogHandler and SSE event generator for live pipeline log streaming.

Architecture:
    pipeline/runner.py
      └─ logs at INFO/DEBUG/WARNING/ERROR
           └─ QueueLogHandler.emit()
                └─ queue.put_nowait(msg)  (thread-safe via call_soon_threadsafe)
                     └─ /pipeline/events async generator
                          └─ yield {"event": "log", "data": msg}
                               └─ EventSource in browser
                                    └─ appends <p> to #log-output div

A None sentinel enqueued when the pipeline finishes causes the generator to
return, which closes the SSE stream. The browser then fires a 'done' event.
"""

import asyncio
import contextlib
import logging
import threading
from collections.abc import AsyncGenerator

# Module-level queue — set at run start, cleared at run end.
_active_queue: asyncio.Queue[str | None] | None = None
_active_handler: logging.Handler | None = None
_status_subscribers: list[asyncio.Queue[str | None]] = []


def get_active_queue() -> asyncio.Queue[str | None] | None:
    """Return the active log queue, or None if no pipeline is running."""
    return _active_queue


def subscribe_status() -> asyncio.Queue[str | None]:
    """Create and register a per-connection status notification queue."""
    q: asyncio.Queue[str | None] = asyncio.Queue()
    _status_subscribers.append(q)
    return q


def unsubscribe_status(q: asyncio.Queue[str | None]) -> None:
    """Remove a status subscriber queue (called on disconnect)."""
    with contextlib.suppress(ValueError):
        _status_subscribers.remove(q)


def notify_started() -> None:
    """Broadcast 'started' to all /status-events subscribers."""
    for q in _status_subscribers:
        q.put_nowait("started")


def notify_done() -> None:
    """Broadcast 'done' to all /status-events subscribers."""
    for q in _status_subscribers:
        q.put_nowait("done")


class QueueLogHandler(logging.Handler):
    """Logging handler that pushes formatted log records into an asyncio.Queue.

    Thread-safe: emit() uses call_soon_threadsafe so it can be called from
    asyncio.to_thread coroutines (e.g. audio_editor running in a thread pool).
    """

    def __init__(self, queue: asyncio.Queue[str | None], loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._queue = queue
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        """Format the record and push it to the asyncio queue in a thread-safe way."""
        try:
            msg = self.format(record)
            if threading.current_thread() is threading.main_thread():
                self._queue.put_nowait(msg)
            else:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, msg)
        except Exception:
            self.handleError(record)


def attach_handler(loop: asyncio.AbstractEventLoop) -> asyncio.Queue[str | None]:
    """Create a queue and attach a QueueLogHandler to the root logger at INFO level.

    Returns the queue so the SSE generator can drain it.
    """
    global _active_queue, _active_handler

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    _active_queue = queue

    handler = QueueLogHandler(queue, loop)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(levelname)-8s %(name)s — %(message)s")
    handler.setFormatter(formatter)

    logging.getLogger().addHandler(handler)
    _active_handler = handler

    return queue


def detach_handler() -> None:
    """Remove the QueueLogHandler from the root logger and clear module state."""
    global _active_queue, _active_handler
    if _active_handler is not None:
        logging.getLogger().removeHandler(_active_handler)
        _active_handler = None
    _active_queue = None


async def event_generator(
    queue: asyncio.Queue[str | None],
) -> AsyncGenerator[dict[str, str], None]:
    """Async generator that yields SSE dicts from the log queue.

    Yields {"event": "log", "data": msg} for each log line.
    Yields {"event": "done", "data": ""} and returns when a None sentinel arrives.
    """
    while True:
        msg = await queue.get()
        if msg is None:
            yield {"event": "done", "data": ""}
            return
        yield {"event": "log", "data": msg}
