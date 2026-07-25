"""Tests for audit log — every write must produce an entry."""
import pytest
from unittest.mock import patch, MagicMock, call
from app.core.audit import log_action


def test_log_action_inserts_record():
    mock_db = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=[{}])
    chain.insert.return_value = chain
    mock_db.table.return_value = chain

    with patch("app.core.audit.get_db", return_value=mock_db):
        log_action("user-1", "create", "appointment", "appt-1", {"key": "value"})

    mock_db.table.assert_called_with("audit_logs")
    insert_call = chain.insert.call_args[0][0]
    assert insert_call["user_id"] == "user-1"
    assert insert_call["action"] == "create"
    assert insert_call["entity_type"] == "appointment"
    assert insert_call["entity_id"] == "appt-1"
    assert insert_call["details"] == {"key": "value"}


def test_log_action_without_entity_id():
    mock_db = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=[{}])
    chain.insert.return_value = chain
    mock_db.table.return_value = chain

    with patch("app.core.audit.get_db", return_value=mock_db):
        log_action("user-1", "view", "patients")

    insert_call = chain.insert.call_args[0][0]
    assert insert_call["entity_id"] is None
    assert insert_call["details"] == {}


def test_log_action_without_user():
    mock_db = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=[{}])
    chain.insert.return_value = chain
    mock_db.table.return_value = chain

    with patch("app.core.audit.get_db", return_value=mock_db):
        log_action(None, "system", "document", "doc-1")

    insert_call = chain.insert.call_args[0][0]
    assert insert_call["user_id"] is None


def test_patient_create_audit_logged():
    """Patient creation via API must produce audit log entry."""
    from fastapi.testclient import TestClient
    from app.main import app
    from jose import jwt
    from tests.conftest import make_chain, patch_db

    token = jwt.encode({"sub": "u1", "email": "a@b.com"}, "test-jwt-secret-32chars-minimum!!", "HS256")

    mock_db = MagicMock()
    # A real row always carries every column, so the fixture must too.
    patient_data = {
        "id": "p-new", "code": "P099", "first_name": "Test", "last_name": "User",
        "dob": None, "gender": None, "phone": None, "email": None,
        "address": None, "notes": None, "created_at": None,
    }
    rows = {
        # Creating patients is receptionist-only.
        "user_roles": [{"roles": {"name": "receptionist"}}],
        "patients": [patient_data],
    }
    mock_db.table.side_effect = lambda name: make_chain(rows.get(name, [{}]))

    audit_calls = []
    original_log = log_action
    def capture_log(*args, **kwargs):
        audit_calls.append(args)
        return original_log.__wrapped__(*args, **kwargs) if hasattr(original_log, "__wrapped__") else None

    with patch_db(mock_db), \
         patch("app.api.patients.log_action", side_effect=capture_log):
        c = TestClient(app)
        r = c.post("/patients/",
                   json={"code": "P099", "first_name": "Test", "last_name": "User"},
                   headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 201
    assert len(audit_calls) >= 1
    assert audit_calls[0][1] == "create"
    assert audit_calls[0][2] == "patient"
