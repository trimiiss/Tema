"""Background work must be tracked, logged, and drainable on shutdown.

`asyncio.create_task` alone gave a task nobody held: garbage-collectable
mid-flight, silent on failure, and impossible to wait for at shutdown. The last
one hung `uvicorn --reload` — the old worker never exited, so the new one never
started and stale code kept serving while the log claimed it had reloaded.
"""
import asyncio
import pytest

from app.core import tasks


@pytest.fixture(autouse=True)
def _no_leaked_tasks():
    tasks._tasks.clear()
    yield
    tasks._tasks.clear()


@pytest.mark.asyncio
async def test_a_spawned_task_is_held_until_it_finishes():
    started = asyncio.Event()

    async def work():
        started.set()
        await asyncio.sleep(0.05)
        return "done"

    task = tasks.spawn(work(), name="unit")
    await started.wait()
    assert tasks.pending() == 1        # a strong reference exists while it runs

    assert await task == "done"
    await asyncio.sleep(0)             # let the done-callback run
    assert tasks.pending() == 0        # and is released afterwards


@pytest.mark.asyncio
async def test_a_failing_task_is_logged_rather_than_swallowed(caplog):
    async def boom():
        raise RuntimeError("agent run exploded")

    tasks.spawn(boom(), name="doomed")
    await asyncio.sleep(0.05)

    assert "agent run exploded" in caplog.text
    assert tasks.pending() == 0


@pytest.mark.asyncio
async def test_drain_waits_for_work_in_flight():
    """This is what lets the process exit — and therefore reload — cleanly."""
    finished = []

    async def work():
        await asyncio.sleep(0.05)
        finished.append(True)

    tasks.spawn(work())
    await tasks.drain(timeout=2.0)

    assert finished == [True]
    assert tasks.pending() == 0


@pytest.mark.asyncio
async def test_drain_cancels_work_that_overruns_the_timeout():
    """A hung run must not hold the process open indefinitely."""
    async def forever():
        await asyncio.sleep(30)

    task = tasks.spawn(forever())
    await tasks.drain(timeout=0.05)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_drain_with_nothing_pending_is_a_no_op():
    await tasks.drain(timeout=0.01)
    assert tasks.pending() == 0
