"""Tests for authentication and authorization."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.core import database, auth


def make_client(token: str, role_name: str | None) -> TestClient:
    mock_db = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(
        data=[{"roles": {"name": role_name}}] if role_name else []
    )
    for m in ("select","eq","maybe_single","order","limit","not_","in_","or_"):
        getattr(chain, m).return_value = chain
    mock_db.table.return_value = chain

    with patch.object(database, "get_db", return_value=mock_db), \
         patch.object(auth, "get_db", return_value=mock_db):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        return c


def test_missing_token_returns_403():
    c = TestClient(app)
    r = c.get("/patients/")
    assert r.status_code in (401, 403)


def test_invalid_token_returns_401():
    c = TestClient(app)
    r = c.get("/patients/", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert r.status_code == 401


def test_admin_can_access_patients(admin_token):
    c = make_client(admin_token, "admin")
    r = c.get("/patients/")
    assert r.status_code == 200


def test_receptionist_can_access_patients(receptionist_token):
    c = make_client(receptionist_token, "receptionist")
    r = c.get("/patients/")
    assert r.status_code == 200


def test_no_role_cannot_access_patients(no_role_token):
    c = make_client(no_role_token, None)
    r = c.get("/patients/")
    assert r.status_code == 403


def test_no_role_cannot_create_patient(no_role_token):
    c = make_client(no_role_token, None)
    r = c.post("/patients/", json={"code": "P099", "first_name": "Test", "last_name": "User"})
    assert r.status_code == 403


def test_doctor_cannot_create_patient(admin_token):
    """Doctors are read-only for patients."""
    c = make_client(admin_token, "doctor")
    r = c.post("/patients/", json={"code": "P099", "first_name": "Test", "last_name": "User"})
    assert r.status_code == 403


def test_health_endpoint_is_public():
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
