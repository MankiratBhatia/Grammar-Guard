"""Server-Sent Events for generation.

The model is blocking CPU code, so a request runs in its OWN worker thread and pushes events into an
asyncio queue that the response drains. If the client disconnects the response generator is cancelled,
its `finally` sets the CancelToken, and the worker stops at the next token boundary (releasing the
engine lock through the generator's own cleanup). A timer cancels runs that exceed the time limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from sse_starlette.sse import EventSourceResponse

from grammarguard.api.errors import ApiError
from grammarguard.decoding.engine import CancelToken

log = logging.getLogger("grammarguard.api")

Emit = Callable[[str, dict[str, Any]], None]
Run = Callable[[CancelToken, Emit, threading.Event], None]


def stream(run: Run, timeout: float) -> EventSourceResponse:
    """Run `run(cancel, emit, timed_out)` in a worker thread and stream what it emits.

    Event names used by the endpoints: status, meta, token, done, error, end.
    """

    async def events():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()
        cancel, timed_out = CancelToken(), threading.Event()

        def put(item: dict[str, str] | None) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:  # the event loop is gone (server shutting down)
                pass

        def emit(event: str, data: dict[str, Any]) -> None:
            put({"event": event, "data": json.dumps(data)})

        def on_timeout() -> None:
            timed_out.set()
            cancel.cancel()

        def worker() -> None:
            timer = threading.Timer(timeout, on_timeout)
            timer.start()
            try:
                run(cancel, emit, timed_out)
            except ApiError as e:
                emit("error", {"code": e.code, "message": e.message, "position": e.position})
            except Exception as e:  # a server bug must not leave the stream hanging
                log.exception("generation failed")
                emit("error", {"code": "internal", "message": str(e) or e.__class__.__name__})
            finally:
                timer.cancel()
                put(None)

        threading.Thread(target=worker, daemon=True, name="generation").start()
        try:
            while (item := await queue.get()) is not None:
                yield item
            yield {"event": "end", "data": "{}"}
        finally:
            cancel.cancel()  # client gone (or finished): stop the worker at the next token

    return EventSourceResponse(events(), ping=15)
