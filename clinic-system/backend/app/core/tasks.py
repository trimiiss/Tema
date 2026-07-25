"""Tracked background tasks.

`asyncio.create_task` hands back a task nobody holds. The event loop keeps only
a weak reference, which has three consequences this module fixes:

- A long agent run can be garbage-collected mid-flight.
- An exception inside it is swallowed — the run simply stops, with nothing in
  the log to say why.
- Shutdown cannot wait for it. That is what made `uvicorn --reload` hang on
  Windows: with an agent run in flight the old worker never finished, so the new
  one never started, and the server kept serving stale code while reporting
  "Reloading..." as though it had succeeded.

Use `spawn(...)` instead of `asyncio.create_task(...)` for anything fired from a
request handler, and let the app's lifespan `drain()` it on shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

_tasks: Set[asyncio.Task] = set()

# How long shutdown waits for in-flight work before cancelling it. An agent run
# is a handful of LLM round-trips; past this it is not going to finish and
# holding the process open helps nobody.
DRAIN_TIMEOUT_SECONDS = 10.0


def _finished(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background task %r failed", task.get_name(), exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], *, name: Optional[str] = None) -> asyncio.Task:
    """Run `coro` in the background, keeping a strong reference until it ends."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_finished)
    return task


def pending() -> int:
    return len(_tasks)


async def drain(timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
    """Let in-flight work finish, then cancel whatever is still going."""
    if not _tasks:
        return
    _, still_running = await asyncio.wait(set(_tasks), timeout=timeout)
    for task in still_running:
        task.cancel()
    if still_running:
        await asyncio.gather(*still_running, return_exceptions=True)
