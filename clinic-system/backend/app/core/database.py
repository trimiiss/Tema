import httpcore
import httpx
from supabase import create_client, Client
from app.core.config import settings


def get_db() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def execute_with_retry(query):
    """Run a postgrest query, retrying once on a dropped HTTP/2 connection.

    postgrest-py's httpx client hardcodes http2=True; under Docker Desktop's
    network layer that connection occasionally gets GOAWAY'd mid-request.
    """
    try:
        return query.execute()
    except (httpx.RemoteProtocolError, httpcore.RemoteProtocolError):
        return query.execute()
