"""Shared test fixtures.

The suite is fully mocked — no live Supabase connection or `.env` is needed.
Mocking happens at the `create_client` boundary rather than on `get_db`:
routers do `from app.core.database import get_db`, which binds the name at
import time, so patching `database.get_db` would leave those bindings pointing
at the real function.
"""
import os
import pytest
from contextlib import contextmanager
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

# Point to test env
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-32chars-minimum!!")
os.environ.setdefault("SECRET_KEY", "test-secret")


@contextmanager
def patch_db(mock_db):
    """Make every `get_db()` call in the app return `mock_db`.

    Must stay open for the duration of the request under test.
    """
    from app.core import database
    with patch.object(database, "create_client", return_value=mock_db):
        yield mock_db


def make_chain(data):
    """A chainable postgrest query mock whose `.execute()` returns `data`."""
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=data)
    for method in ("select", "insert", "update", "delete", "upsert", "eq", "neq",
                   "gte", "lte", "lt", "gt", "ilike", "like", "or_", "in_",
                   "is_", "order", "limit", "range", "single", "maybe_single"):
        getattr(chain, method).return_value = chain
    # `.not_` is a property, not a call: postgrest spells negation
    # `.not_.in_(...)`, so it has to resolve back to the chain itself.
    chain.not_ = chain
    return chain


@pytest.fixture
def mock_db():
    """Returns a mock Supabase client with chainable query methods."""
    db = MagicMock()
    db._chain_factory = make_chain
    db.table.side_effect = lambda name: make_chain([])
    return db


@pytest.fixture
def admin_token():
    """JWT token for an admin user (signed with test secret)."""
    from jose import jwt
    return jwt.encode(
        {"sub": "user-admin-001", "email": "admin@clinic.demo", "role": "authenticated"},
        "test-jwt-secret-32chars-minimum!!",
        algorithm="HS256",
    )


@pytest.fixture
def receptionist_token():
    from jose import jwt
    return jwt.encode(
        {"sub": "user-recept-001", "email": "recept@clinic.demo", "role": "authenticated"},
        "test-jwt-secret-32chars-minimum!!",
        algorithm="HS256",
    )


@pytest.fixture
def no_role_token():
    from jose import jwt
    return jwt.encode(
        {"sub": "user-norole-001", "email": "norole@clinic.demo"},
        "test-jwt-secret-32chars-minimum!!",
        algorithm="HS256",
    )


@pytest.fixture
def client(mock_db, admin_token):
    from app.main import app
    with patch_db(mock_db):
        # Make mock_db.table return chain with admin role
        def table_side_effect(name):
            chain = mock_db._chain_factory([{"roles": {"name": "admin"}}])
            return chain
        mock_db.table.side_effect = table_side_effect
        with TestClient(app) as c:
            c.headers.update({"Authorization": f"Bearer {admin_token}"})
            yield c
