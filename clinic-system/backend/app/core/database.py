import httpcore
import httpx
from supabase import create_client, Client
from app.core.config import settings


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
    fresh connection. Reads are safe to repeat; writes reach here only through
    endpoints whose inserts are idempotent for this purpose.

    A `.maybe_single()` miss comes back as `_NoRow` rather than `None` — see
    that class for why.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            result = query.execute()
            return _NoRow() if result is None else result
        except _TRANSIENT as e:
            last = e
    raise last
