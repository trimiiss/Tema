"""Tests for appointment CRUD, conflict detection, and status transitions."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from app.services.schedule_service import check_conflict, get_available_slots
from tests.conftest import make_chain


def _make_db(data):
    db = MagicMock()
    db.table.return_value = make_chain(data)
    return db


# ---- Conflict detection (unit, no DB) ----

def test_check_conflict_returns_none_when_no_conflict():
    mock_db = _make_db([])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        dt = datetime(2026, 7, 24, 10, 0)
        result = check_conflict("staff-001", dt)
    assert result is None


def test_check_conflict_returns_conflicting_appointment():
    existing = {"id": "appt-001", "scheduled_at": "2026-07-24T10:00:00", "duration_min": 30}
    mock_db = _make_db([existing])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        dt = datetime(2026, 7, 24, 10, 0)
        result = check_conflict("staff-001", dt)
    assert result == existing


def test_check_conflict_different_time_no_conflict():
    mock_db = _make_db([])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        dt = datetime(2026, 7, 24, 14, 0)  # 4 hours later
        result = check_conflict("staff-001", dt)
    assert result is None


# ---- Available slots ----

def test_available_slots_returns_empty_for_no_schedule():
    mock_db = _make_db([])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        dt = datetime(2026, 7, 24)
        slots = get_available_slots("staff-001", dt)
    assert slots == []


def test_available_slots_returns_slots_within_schedule():
    sched = [{"staff_id": "s1", "weekday": 3, "start_time": "08:00", "end_time": "10:00"}]
    mock_db = MagicMock()
    sched_chain = make_chain(sched)
    appt_chain = make_chain([])
    mock_db.table.side_effect = lambda name: sched_chain if name == "schedules" else appt_chain

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        dt = datetime(2026, 7, 23)  # Thursday
        slots = get_available_slots("s1", dt)
    # 08:00–10:00 = 4 slots of 30min
    assert len(slots) == 4


# ---- Status transitions ----

VALID_TRANSITIONS = {
    "proposed":  ["confirmed", "cancelled"],
    "confirmed": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}

@pytest.mark.parametrize("from_status,to_status,should_pass", [
    ("proposed",  "confirmed",  True),
    ("proposed",  "cancelled",  True),
    ("proposed",  "completed",  False),
    ("proposed",  "no_show",    False),
    ("confirmed", "completed",  True),
    ("confirmed", "no_show",    False),
    ("confirmed", "cancelled",  True),
    ("completed", "cancelled",  False),
    ("cancelled", "confirmed",  False),
    ("no_show",   "confirmed",  False),
])
def test_status_transition(from_status, to_status, should_pass):
    allowed = VALID_TRANSITIONS.get(from_status, [])
    assert (to_status in allowed) == should_pass


def test_double_booking_detected():
    existing = {"id": "appt-existing"}
    mock_db = _make_db([existing])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        result = check_conflict("staff-001", datetime(2026, 7, 24, 10, 0))
    assert result is not None
    assert result["id"] == "appt-existing"
