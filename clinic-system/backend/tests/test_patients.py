"""Tests for patient CRUD and missing-fields detection."""
import json
import pytest
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.agents.patient_agent import (
    tool_get_patient,
    tool_search_patients,
    tool_flag_missing_fields,
    REQUIRED_FIELDS,
)


from tests.conftest import make_chain, patch_db


def _make_db(data):
    """`data` must match what the query returns: a dict for `.maybe_single()`
    lookups, a list for plain selects."""
    db = MagicMock()
    db.table.return_value = make_chain(data)
    return db


def test_get_patient_found():
    patient = {"id": "p1", "code": "P001", "first_name": "Alban", "last_name": "Krasniqi"}
    db = _make_db(patient)
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_get_patient("P001")
    assert result["found"] is True
    assert result["patient"]["code"] == "P001"


def test_get_patient_not_found():
    db = _make_db(None)
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_get_patient("P999")
    assert result["found"] is False
    assert result["patient"] is None


def test_search_patients_returns_results():
    patients = [{"id": "p1", "code": "P001", "first_name": "Alban", "last_name": "Krasniqi"}]
    db = _make_db(patients)
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_search_patients("Alban")
    assert len(result["results"]) == 1


def test_search_patients_empty():
    db = _make_db([])
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_search_patients("NonExistent")
    assert result["results"] == []


def test_flag_missing_fields_complete_patient():
    full = {
        "id": "p1",
        "first_name": "Alban", "last_name": "Krasniqi",
        "dob": "1985-03-12",
        "phone": "+383-44-100001", "email": "alban@demo.test",
        "address": "Prishtina",
    }
    db = _make_db(full)
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_flag_missing_fields("p1")
    assert result["missing_fields"] == []
    assert result["complete"] is True


def test_flag_missing_fields_partial_patient():
    partial = {"id": "p5", "first_name": "Besnik", "last_name": "Ahmeti"}
    db = _make_db(partial)
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_flag_missing_fields("p5")
    assert "dob" in result["missing_fields"]
    assert "phone" in result["missing_fields"]
    assert result["complete"] is False


def test_flag_missing_fields_patient_not_found():
    db = _make_db(None)
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_flag_missing_fields("nonexistent")
    assert "error" in result


def test_required_fields_set():
    assert "first_name" in REQUIRED_FIELDS
    assert "dob" in REQUIRED_FIELDS
    assert "phone" in REQUIRED_FIELDS


@contextmanager
def _client_as_receptionist(saved_row):
    """Receptionist client whose `patients` chain is inspectable."""
    chains = {"patients": make_chain([saved_row])}

    def table(name):
        if name == "user_roles":
            return make_chain([{"roles": {"name": "receptionist"}}])
        if name not in chains:
            chains[name] = make_chain([])
        return chains[name]

    db = MagicMock()
    db.table.side_effect = table
    with patch_db(db):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {_receptionist_token()}"})
        c.chains = chains
        yield c


def _receptionist_token():
    from jose import jwt
    return jwt.encode(
        {"sub": "user-recept-001", "email": "recept@clinic.demo", "role": "authenticated"},
        "test-jwt-secret-32chars-minimum!!",
        algorithm="HS256",
    )


PATIENT_ROW = {
    "id": "p-new-001", "code": "P901", "first_name": "Testim", "last_name": "Prova",
    "dob": "1990-04-17", "phone": "044111222",
    "email": "testim@demo.test", "address": None, "notes": None,
    "created_at": "2026-07-25T10:00:00Z",
}


def test_create_patient_with_dob_sends_json_serializable_payload():
    """`dob` must reach postgrest as an ISO string, not a `datetime.date`.

    postgrest JSON-encodes the payload, and a bare `date` raises
    `TypeError: Object of type date is not JSON serializable` — a 500 on every
    patient created with a date of birth. Mocks accept anything, so the payload
    is asserted to survive `json.dumps` rather than merely to have been passed.
    """
    with _client_as_receptionist(PATIENT_ROW) as c:
        resp = c.post("/patients/", json={
            "code": "P901", "first_name": "Testim", "last_name": "Prova",
            "dob": "1990-04-17",
        })
    assert resp.status_code == 201
    payload = c.chains["patients"].insert.call_args[0][0]
    assert payload["dob"] == "1990-04-17"
    json.dumps(payload)  # raises if any value is a bare date/datetime


def test_update_patient_with_dob_sends_json_serializable_payload():
    """Same contract as `create_patient` — see above."""
    with _client_as_receptionist(PATIENT_ROW) as c:
        resp = c.patch("/patients/p-new-001", json={"dob": "1991-05-18"})
    assert resp.status_code == 200
    payload = c.chains["patients"].update.call_args[0][0]
    assert payload["dob"] == "1991-05-18"
    json.dumps(payload)
