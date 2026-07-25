import logging

import httpcore
import httpx
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_db() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# A dropped keep-alive connection surfaces as any of these depending on where
# in the request it died. None of them mean the query itself was bad.
_TRANSIENT = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpcore.RemoteProtocolError,
)


class _NoRow:
    """Stand-in for a `.maybe_single()` query that matched nothing.

    postgrest returns a bare `None` in that case rather than a response with
    `data=None`, so every `resp.data` on the far side raises
    `AttributeError: 'NoneType' object has no attribute 'data'` — a 500 where
    the caller expected an empty result. Normalizing here keeps `.data` valid
    at every call site, so `if not resp.data:` means "no such row" as intended.
    """

    data = None


def execute_with_retry(query, attempts: int = 3):
    """Run a postgrest query, retrying a dropped HTTP/2 connection.

    postgrest-py's httpx client hardcodes http2=True; Supabase closes idle
    HTTP/2 connections with a GOAWAY, and a pooled connection reused just
    after that lands mid-request as `ConnectionTerminated`. The retry opens a
    fresh connection.

    Retries are **at-least-once**, not exactly-once: the connection can die
    after the query committed but before its response arrived, and the retry
    then runs it again. Reads and updates are safe to repeat. An **insert is
    not**, so a caller inserting under this guard must make the write
    idempotent itself (see `ocr_service.process_document_async`).

    Every retry is logged, and that is not decoration. This guard used to
    swallow the transport error silently, which made a duplicated-row incident
    impossible to attribute: "no retry appears in the log" looked like evidence
    that no retry happened, when in fact a retry could never have appeared
    there. A silent recovery is still an incident report — log it.

    A `.maybe_single()` miss comes back as `_NoRow` rather than `None` — see
    that class for why.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = query.execute()
            if attempt > 1:
                logger.warning(
                    "postgrest query recovered on attempt %d/%d — if it was a write, "
                    "the earlier attempt may also have committed",
                    attempt, attempts,
                )
            return _NoRow() if result is None else result
        except _TRANSIENT as e:
            last = e
            logger.warning(
                "postgrest transport error on attempt %d/%d: %s: %s",
                attempt, attempts, type(e).__name__, e,
            )
    logger.error("postgrest query failed after %d attempts", attempts, exc_info=last)
    raise last
