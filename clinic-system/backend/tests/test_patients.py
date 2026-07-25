"""Tests for patient CRUD and missing-fields detection."""
import pytest
from unittest.mock import patch, MagicMock
from app.agents.patient_agent import (
    tool_get_patient,
    tool_search_patients,
    tool_flag_missing_fields,
    REQUIRED_FIELDS,
)


def _make_db(data):
    db = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=data)
    for m in ("select","eq","or_","limit","maybe_single","insert","update"):
        getattr(chain, m).return_value = chain
    db.table.return_value = chain
    return db


def test_get_patient_found():
    patient = {"id": "p1", "code": "P001", "first_name": "Alban", "last_name": "Krasniqi"}
    db = _make_db([patient])
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_get_patient("P001")
    assert result["found"] is True
    assert result["patient"]["code"] == "P001"


def test_get_patient_not_found():
    db = _make_db([])
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
        "dob": "1985-03-12", "gender": "M",
        "phone": "+383-44-100001", "email": "alban@demo.test",
        "address": "Prishtina",
    }
    db = _make_db([full])
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_flag_missing_fields("p1")
    assert result["missing_fields"] == []
    assert result["complete"] is True


def test_flag_missing_fields_partial_patient():
    partial = {"id": "p5", "first_name": "Besnik", "last_name": "Ahmeti"}
    db = _make_db([partial])
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_flag_missing_fields("p5")
    assert "dob" in result["missing_fields"]
    assert "phone" in result["missing_fields"]
    assert result["complete"] is False


def test_flag_missing_fields_patient_not_found():
    db = _make_db([])
    with patch("app.agents.patient_agent.get_db", return_value=db):
        result = tool_flag_missing_fields("nonexistent")
    assert "error" in result


def test_required_fields_set():
    assert "first_name" in REQUIRED_FIELDS
    assert "dob" in REQUIRED_FIELDS
    assert "phone" in REQUIRED_FIELDS
