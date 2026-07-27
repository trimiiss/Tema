"""Per-IP rate limiting for unauthenticated routes.

Every other router in this app gates access with `require_roles(...)` — a
JWT that costs an account to obtain. `/public/*` has no such gate by design,
which means the only thing standing between the OpenAI bill and a script that
hammers `POST /public/booking/chat` in a loop is this module.

In-memory, single-process, keyed by client IP — the same limitation
`core/events.py` documents for its pub/sub and accepts for the same reason:
this app runs one uvicorn worker. Restarting the process clears every
counter, and a deployment behind multiple workers or replicas would need a
shared store (Redis, etc.) instead. Good enough for a thesis prototype; not
good enough to depend on in front of a real clinic's public internet traffic.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict

from fastapi import HTTPException, Request

# Generous enough for a real back-and-forth conversation, tight enough that a
# tight loop against the endpoint burns through it in seconds rather than
# hours.
MAX_REQUESTS = 20
WINDOW_SECONDS = 60.0

_hits: Dict[str, Deque[float]] = {}


def _client_ip(request: Request) -> str:
    # Trust X-Forwarded-For only if this ever sits behind a reverse proxy that
    # sets it; falls back to the direct peer otherwise.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency: raises 429 once an IP exceeds the window's budget."""
    now = time.monotonic()
    ip = _client_ip(request)
    hits = _hits.setdefault(ip, deque())

    while hits and now - hits[0] > WINDOW_SECONDS:
        hits.popleft()

    if len(hits) >= MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please wait a moment before trying again.",
        )
    hits.append(now)
