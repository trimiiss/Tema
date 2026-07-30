"""Tests for appointment CRUD, conflict detection, and status transitions."""
import pytest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.schedule_service import (
    check_conflict,
    complete_elapsed_appointments,
    get_available_slots,
    to_clinic,
)
from tests.conftest import make_chain, patch_db, table_chain


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


# ---- A doctor sees only their own diary ----
#
# `staff.user_id` is what ties a login to a bookable staff row. Everything below
# turns on that link: a doctor whose login resolves to a staff row is scoped to
# it, and a doctor whose login resolves to nothing sees nothing rather than
# everything — the failure has to be closed, not open.

FUTURE = "2030-01-07T10:00:00+01:00"   # a Monday, far enough out that the
                                       # elapsed-appointment sweep ignores it

def _appointment(appt_id: str, staff_id: str) -> dict:
    return {
        "id": appt_id, "patient_id": "p-1", "staff_id": staff_id, "service_id": None,
        "scheduled_at": FUTURE, "duration_min": 30, "status": "confirmed",
        "notes": None, "created_at": None, "source": "staff",
    }


@contextmanager
def _client_as(token: str, role: str, staff_row: dict | None, appointments: list[dict]):
    """Client authenticated as `role`, whose login maps to `staff_row` (or None).

    Yields `(client, appointments_chain)` so a test can assert on the filters
    that actually reached postgrest, not just on the rows the mock handed back.

    `table_chain` rather than `make_chain` because the appointments table is
    read both ways here: as a list by `list_appointments`, and via
    `.maybe_single()` by `get_appointment`, which must yield one row.
    """
    appointments_chain = table_chain(appointments)

    def table(name):
        if name == "user_roles":
            return make_chain([{"roles": {"name": role}}])
        if name == "staff":
            chain = make_chain([staff_row] if staff_row else [])
            chain.maybe_single.return_value = make_chain(staff_row)
            return chain
        if name == "appointments":
            return appointments_chain
        return make_chain([])

    db = MagicMock()
    db.table.side_effect = table
    with patch_db(db):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c, appointments_chain


def test_doctor_listing_is_filtered_to_their_own_staff_id(admin_token):
    """The filter is applied server-side, not merely defaulted client-side."""
    with _client_as(admin_token, "doctor", {"id": "s-mine"}, [_appointment("a-1", "s-mine")]) as (c, chain):
        resp = c.get("/appointments/")
    assert resp.status_code == 200
    chain.eq.assert_any_call("staff_id", "s-mine")


def test_a_doctor_cannot_widen_the_list_to_a_colleague(admin_token):
    """`?staff_id=` is overridden, not honoured — otherwise the scoping is a
    default a caller can simply opt out of by passing someone else's id."""
    with _client_as(admin_token, "doctor", {"id": "s-mine"}, []) as (c, chain):
        resp = c.get("/appointments/?staff_id=s-colleague")
    assert resp.status_code == 200
    chain.eq.assert_any_call("staff_id", "s-mine")
    assert ("staff_id", "s-colleague") not in [call.args for call in chain.eq.call_args_list]


def test_a_doctor_with_no_staff_row_sees_nothing_rather_than_everything(admin_token):
    """The seeded doctors carry no `user_id`, and a role granted by hand in SQL
    links nothing — an unresolvable doctor must fail closed."""
    with _client_as(admin_token, "doctor", None, [_appointment("a-1", "s-someone")]) as (c, _):
        resp = c.get("/appointments/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_a_doctor_cannot_fetch_a_colleagues_appointment_by_id(admin_token):
    """Hiding it from the list is not enough if the id still resolves."""
    with _client_as(admin_token, "doctor", {"id": "s-mine"}, [_appointment("a-1", "s-colleague")]) as (c, _):
        resp = c.get("/appointments/a-1")
    assert resp.status_code == 404


def test_a_doctor_can_fetch_their_own_appointment_by_id(admin_token):
    with _client_as(admin_token, "doctor", {"id": "s-mine"}, [_appointment("a-1", "s-mine")]) as (c, _):
        resp = c.get("/appointments/a-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "a-1"


def test_a_receptionist_still_sees_the_whole_clinic(receptionist_token):
    """Scoping applies to doctors only — the front desk books for everyone, so
    no `staff_id` filter should be applied on their behalf at all."""
    rows = [_appointment("a-1", "s-one"), _appointment("a-2", "s-two")]
    with _client_as(receptionist_token, "receptionist", None, rows) as (c, chain):
        resp = c.get("/appointments/")
    assert resp.status_code == 200
    assert {a["id"] for a in resp.json()} == {"a-1", "a-2"}
    assert not any(call.args[0] == "staff_id" for call in chain.eq.call_args_list)
