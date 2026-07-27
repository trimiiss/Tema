"""Tests for appointment CRUD, conflict detection, and status transitions."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from app.services.schedule_service import (
    check_conflict,
    complete_elapsed_appointments,
    get_available_slots,
    to_clinic,
)
from tests.conftest import make_chain, patch_db


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


# ---- Timezone correctness ----
#
# `appointments.scheduled_at` is TIMESTAMPTZ. A naive datetime sent to Postgres
# is read as UTC, which is how a 10:00 booking used to come back as 12:00.

def test_slots_carry_the_clinic_utc_offset():
    """Naive slot strings are the bug — every slot must pin an instant."""
    sched = [{"staff_id": "s1", "weekday": 3, "start_time": "09:00", "end_time": "11:00"}]
    mock_db = MagicMock()
    mock_db.table.side_effect = lambda n: make_chain(sched if n == "schedules" else [])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        slots = get_available_slots("s1", datetime(2026, 7, 23))

    assert slots, "expected slots"
    for s in slots:
        parsed = datetime.fromisoformat(s)
        assert parsed.tzinfo is not None, f"{s} is naive"
    # Summer in Europe/Tirane is CEST (UTC+2); the wall clock stays 09:00.
    first = datetime.fromisoformat(slots[0])
    assert first.hour == 9
    assert first.utcoffset() == timedelta(hours=2)


def test_naive_datetime_is_read_as_clinic_wall_clock():
    localized = to_clinic(datetime(2026, 7, 23, 10, 0))
    assert localized.hour == 10
    assert localized.utcoffset() == timedelta(hours=2)
    # Same wall clock, correct instant: 10:00 CEST is 08:00 UTC.
    assert localized.astimezone(timezone.utc).hour == 8


def test_booked_slot_is_excluded_despite_utc_storage():
    """A stored UTC row must knock out the matching clinic-local slot."""
    sched = [{"staff_id": "s1", "weekday": 3, "start_time": "09:00", "end_time": "11:00"}]
    # 09:00 CEST is persisted as 07:00Z — a string compare would never match.
    booked = [{"id": "a1", "scheduled_at": "2026-07-23T07:00:00+00:00", "duration_min": 30}]
    mock_db = MagicMock()
    mock_db.table.side_effect = lambda n: make_chain(sched if n == "schedules" else booked)

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        slots = get_available_slots("s1", datetime(2026, 7, 23))

    hours = [datetime.fromisoformat(s).strftime("%H:%M") for s in slots]
    assert "09:00" not in hours
    assert "09:30" in hours


# ---- Default working hours ----

def test_doctor_with_no_schedule_falls_back_to_clinic_defaults():
    """A doctor with no rows is bookable Mon–Fri 09:00–17:00, not unbookable."""
    mock_db = _make_db([])  # no schedules and no appointments

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        slots = get_available_slots("s1", datetime(2026, 7, 23))  # Thursday

    hours = [datetime.fromisoformat(s).strftime("%H:%M") for s in slots]
    assert hours[0] == "09:00"
    assert hours[-1] == "16:30"
    assert len(hours) == 16  # 09:00–17:00 in 30-min steps


def test_default_hours_do_not_apply_at_the_weekend():
    mock_db = _make_db([])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        slots = get_available_slots("s1", datetime(2026, 7, 25))  # Saturday
    assert slots == []


def test_an_explicit_schedule_suppresses_the_fallback():
    """A doctor set to Tuesdays only must not sprout default Monday hours."""
    sched = [{"staff_id": "s1", "weekday": 1, "start_time": "10:00", "end_time": "12:00"}]
    mock_db = MagicMock()
    mock_db.table.side_effect = lambda n: make_chain(sched if n == "schedules" else [])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        monday = get_available_slots("s1", datetime(2026, 7, 20))
        tuesday = get_available_slots("s1", datetime(2026, 7, 21))

    assert monday == []
    assert [datetime.fromisoformat(s).strftime("%H:%M") for s in tuesday][0] == "10:00"


# ---- Duration handling ----

def test_long_appointment_blocks_every_slot_it_covers():
    sched = [{"staff_id": "s1", "weekday": 3, "start_time": "09:00", "end_time": "12:00"}]
    booked = [{"id": "a1", "scheduled_at": "2026-07-23T07:00:00+00:00", "duration_min": 90}]
    mock_db = MagicMock()
    mock_db.table.side_effect = lambda n: make_chain(sched if n == "schedules" else booked)

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        slots = get_available_slots("s1", datetime(2026, 7, 23))

    hours = [datetime.fromisoformat(s).strftime("%H:%M") for s in slots]
    # 09:00–10:30 is taken; the shift resumes at 10:30.
    assert hours == ["10:30", "11:00", "11:30"]


def test_slot_is_not_offered_when_service_overruns_the_shift():
    sched = [{"staff_id": "s1", "weekday": 3, "start_time": "09:00", "end_time": "10:00"}]
    mock_db = MagicMock()
    mock_db.table.side_effect = lambda n: make_chain(sched if n == "schedules" else [])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        slots = get_available_slots("s1", datetime(2026, 7, 23), duration_min=60)

    # Only 09:00 leaves room for a full hour before the 10:00 finish.
    assert [datetime.fromisoformat(s).strftime("%H:%M") for s in slots] == ["09:00"]


def test_overlap_with_earlier_long_appointment_is_detected():
    """The old range query missed this: existing starts *before* the new one."""
    existing = {"id": "a1", "scheduled_at": "2026-07-23T07:00:00+00:00", "duration_min": 60}
    mock_db = _make_db([existing])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        # 09:30 CEST lands in the middle of the 09:00–10:00 appointment.
        result = check_conflict("s1", datetime(2026, 7, 23, 9, 30), 30)
    assert result is not None
    assert result["id"] == "a1"


def test_adjacent_appointments_do_not_conflict():
    existing = {"id": "a1", "scheduled_at": "2026-07-23T07:00:00+00:00", "duration_min": 30}
    mock_db = _make_db([existing])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        # 09:30 starts exactly when the 09:00 appointment ends.
        result = check_conflict("s1", datetime(2026, 7, 23, 9, 30), 30)
    assert result is None


def test_conflict_check_ignores_the_appointment_being_edited():
    existing = {"id": "a1", "scheduled_at": "2026-07-23T07:00:00+00:00", "duration_min": 30}
    mock_db = _make_db([existing])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        result = check_conflict("s1", datetime(2026, 7, 23, 9, 0), 30,
                                exclude_appointment_id="a1")
    assert result is None


def test_double_booking_detected():
    existing = {
        "id": "appt-existing",
        "scheduled_at": "2026-07-24T08:00:00+00:00",  # 10:00 clinic time
        "duration_min": 30,
    }
    mock_db = _make_db([existing])

    with patch("app.services.schedule_service.get_db", return_value=mock_db):
        result = check_conflict("staff-001", datetime(2026, 7, 24, 10, 0))
    assert result is not None
    assert result["id"] == "appt-existing"


# ---- Auto-completing elapsed appointments ----

def _sweep_db(rows):
    """A db whose appointments table returns `rows` and records the update."""
    from tests.conftest import make_chain
    chain = make_chain(rows)
    db = MagicMock()
    db.table.side_effect = lambda name: chain
    return db, chain


def _clinic_now():
    from app.services.schedule_service import clinic_tz
    return datetime.now(clinic_tz())


def test_confirmed_appointment_whose_end_passed_is_completed():
    now = _clinic_now()
    row = {"id": "a-1", "scheduled_at": (now - timedelta(hours=2)).isoformat(),
           "duration_min": 30}
    db, chain = _sweep_db([row])
    with patch_db(db):
        assert complete_elapsed_appointments() == 1
    chain.update.assert_called_once_with({"status": "completed"})
    chain.in_.assert_called_once_with("id", ["a-1"])


def test_appointment_still_running_is_left_alone():
    """The end of the appointment, not its start.

    A 30-minute slot that began 10 minutes ago is still in progress; marking it
    completed would close it while the patient is in the room.
    """
    now = _clinic_now()
    row = {"id": "a-1", "scheduled_at": (now - timedelta(minutes=10)).isoformat(),
           "duration_min": 30}
    db, chain = _sweep_db([row])
    with patch_db(db):
        assert complete_elapsed_appointments() == 0
    chain.update.assert_not_called()


def test_sweep_only_considers_confirmed_appointments():
    """A proposed appointment nobody accepted must not become "completed".

    Its time passing is not evidence the visit happened — the sweep may only
    make transitions `VALID_TRANSITIONS` already allows, and `proposed` goes to
    confirmed or cancelled, never straight to completed.
    """
    now = _clinic_now()
    db, chain = _sweep_db([{"id": "a-1", "scheduled_at": (now - timedelta(days=1)).isoformat(),
                            "duration_min": 30}])
    with patch_db(db):
        complete_elapsed_appointments()
    chain.eq.assert_any_call("status", "confirmed")


def test_sweep_with_nothing_to_do_writes_nothing():
    db, chain = _sweep_db([])
    with patch_db(db):
        assert complete_elapsed_appointments() == 0
    chain.update.assert_not_called()


def test_sweep_defaults_a_missing_duration():
    """`duration_min` absent must not crash the sweep mid-list."""
    now = _clinic_now()
    row = {"id": "a-1", "scheduled_at": (now - timedelta(hours=5)).isoformat(),
           "duration_min": None}
    db, chain = _sweep_db([row])
    with patch_db(db):
        assert complete_elapsed_appointments() == 1
