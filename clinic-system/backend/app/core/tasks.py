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

`spawn` must work from a worker thread, not just from the loop. FastAPI runs a
**sync** `def` endpoint via `run_in_threadpool`, and there is no running loop in
that thread — so `asyncio.create_task` raises `RuntimeError: no running event
loop` and the endpoint 500s. Every caller of `spawn` is such an endpoint
(`agents.start_agent_run`, `agents.decide_gate`, `public.start_chat`), which is
why starting an agent chat, approving a gate, and booking from the website all
failed. In the browser it looks like a CORS error rather than a 500, because
the exception escapes before `CORSMiddleware` adds its headers to the response.

So `capture_loop()` records the loop at startup and `spawn` schedules onto it
with `run_coroutine_threadsafe` when called off-loop. Keeping the endpoints
sync is the point: their Supabase calls are blocking, and making them `async
def` would move that blocking onto the event loop instead.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

_tasks: Set[asyncio.Task] = set()

# The app's event loop, recorded at startup so `spawn` can reach it from a
# threadpool worker. Set by `capture_loop()` in the lifespan.
_loop: Optional[asyncio.AbstractEventLoop] = None

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


def capture_loop() -> None:
    """Record the running loop so `spawn` can reach it from a worker thread."""
    global _loop
    _loop = asyncio.get_running_loop()


def _track(task: asyncio.Task, name: Optional[str]) -> asyncio.Task:
    if name:
        task.set_name(name)
    _tasks.add(task)
    task.add_done_callback(_finished)
    return task


def spawn(coro: Coroutine[Any, Any, Any], *, name: Optional[str] = None) -> asyncio.Task:
    """Run `coro` in the background, keeping a strong reference until it ends.

    Safe to call from a sync endpoint running in FastAPI's threadpool, where
    there is no running loop — see the module docstring.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        if _loop is None:
            raise RuntimeError(
                "tasks.spawn() called with no event loop available. The app's "
                "lifespan must call tasks.capture_loop() at startup."
            )
        # Hop back onto the app's loop. `run_coroutine_threadsafe` returns a
        # concurrent.futures.Future, not a Task, so the task is created *on*
        # the loop and tracked there — otherwise `drain()` would have nothing
        # to wait for and shutdown would not know the run was in flight.
        done: "concurrent.futures.Future[asyncio.Task]" = concurrent.futures.Future()

        def _create() -> None:
            done.set_result(_track(_loop.create_task(coro), name))  # type: ignore[union-attr]

        _loop.call_soon_threadsafe(_create)
        return done.result()

    return _track(asyncio.create_task(coro), name)


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
