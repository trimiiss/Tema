"""Tests for authentication and authorization."""
import pytest
from contextlib import contextmanager
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from app.main import app
from tests.conftest import make_chain, patch_db


@contextmanager
def make_client(token: str, role_name: str | None, rows: dict | None = None):
    """Client authenticated as `token`, resolving to a single role (or none).

    A context manager because the DB patch has to stay open while the request
    runs — returning the client from inside a `with` block would deactivate it.
    `rows` maps table name -> data for tests that need a write to succeed.
    """
    tables = {
        "user_roles": [{"roles": {"name": role_name}}] if role_name else [],
        **(rows or {}),
    }
    mock_db = MagicMock()
    mock_db.table.side_effect = lambda name: make_chain(tables.get(name, []))
    with patch_db(mock_db):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def test_missing_token_returns_403():
    c = TestClient(app)
    r = c.get("/patients/")
    assert r.status_code in (401, 403)


def test_invalid_token_returns_401():
    c = TestClient(app)
    r = c.get("/patients/", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert r.status_code == 401


def test_admin_can_access_patients(admin_token):
    with make_client(admin_token, "admin") as c:
        assert c.get("/patients/").status_code == 200


def test_receptionist_can_access_patients(receptionist_token):
    with make_client(receptionist_token, "receptionist") as c:
        assert c.get("/patients/").status_code == 200


def test_no_role_cannot_access_patients(no_role_token):
    with make_client(no_role_token, None) as c:
        assert c.get("/patients/").status_code == 403


def test_no_role_cannot_create_patient(no_role_token):
    with make_client(no_role_token, None) as c:
        r = c.post("/patients/", json={"code": "P099", "first_name": "Test", "last_name": "User"})
        assert r.status_code == 403


def test_doctor_cannot_create_patient(admin_token):
    """Doctors are read-only for patients."""
    with make_client(admin_token, "doctor") as c:
        r = c.post("/patients/", json={"code": "P099", "first_name": "Test", "last_name": "User"})
        assert r.status_code == 403


# ---- The patient register is receptionist-owned ----

def test_receptionist_can_create_patient(receptionist_token):
    created = {
        "id": "p-new", "code": "P099", "first_name": "Test", "last_name": "User",
        "dob": None, "gender": None, "phone": None, "email": None,
        "address": None, "notes": None, "created_at": None,
    }
    with make_client(receptionist_token, "receptionist", {"patients": [created]}) as c:
        r = c.post("/patients/", json={"code": "P099", "first_name": "Test", "last_name": "User"})
        assert r.status_code == 201


def test_admin_cannot_create_patient(admin_token):
    """Admins manage staff and accounts; the front desk owns patient records."""
    with make_client(admin_token, "admin") as c:
        r = c.post("/patients/", json={"code": "P099", "first_name": "Test", "last_name": "User"})
        assert r.status_code == 403


def test_admin_cannot_update_patient(admin_token):
    with make_client(admin_token, "admin") as c:
        assert c.patch("/patients/p1", json={"phone": "+383-44-000000"}).status_code == 403


def test_admin_can_still_read_patients(admin_token):
    """Restricting writes must not blind admins to the register."""
    with make_client(admin_token, "admin") as c:
        assert c.get("/patients/").status_code == 200


def test_health_endpoint_is_public():
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
