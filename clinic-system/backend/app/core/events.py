"""
In-memory pub/sub for streaming agent run progress over SSE.

Single-process only (fine for this app: one uvicorn worker, no external
event bus needed) — subscribers are asyncio.Queues keyed by run_id.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, List

_subscribers: Dict[str, List[asyncio.Queue]] = {}


def subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    subs = _subscribers.get(run_id)
    if not subs:
        return
    if q in subs:
        subs.remove(q)
    if not subs:
        _subscribers.pop(run_id, None)


def publish(run_id: str, event: Dict[str, Any]) -> None:
    for q in _subscribers.get(run_id, []):
        q.put_nowait(event)
