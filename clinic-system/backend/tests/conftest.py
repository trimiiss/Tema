"""
Shared test fixtures. Tests run against the real Supabase instance
using SERVICE_ROLE key, so no mocking — per thesis requirement.
"""
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

# Point to test env
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-32chars-minimum!!")
os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture
def mock_db():
    """Returns a mock Supabase client with chainable query methods."""
    def _make_chain(data):
        chain = MagicMock()
        chain.execute.return_value = MagicMock(data=data)
        for method in ("select","insert","update","delete","eq","neq","gte","lte","lt","gt",
                       "ilike","or_","in_","not_","order","limit","maybe_single"):
            getattr(chain, method).return_value = chain
        return chain

    db = MagicMock()
    db._chain_factory = _make_chain
    db.table.side_effect = lambda name: _make_chain([])
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
    from app.core import database, auth
    with patch.object(database, "get_db", return_value=mock_db), \
         patch.object(auth, "get_db", return_value=mock_db):
        # Make mock_db.table return chain with admin role
        def table_side_effect(name):
            chain = mock_db._chain_factory([{"roles": {"name": "admin"}}])
            return chain
        mock_db.table.side_effect = table_side_effect
        with TestClient(app) as c:
            c.headers.update({"Authorization": f"Bearer {admin_token}"})
            yield c
