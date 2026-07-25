"""Tests for staff administration (admin creates doctors / receptionists)
and for manual appointment booking by a receptionist."""
import pytest
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from fastapi.testclient import TestClient
from app.main import app
from tests.conftest import make_chain, patch_db

DOCTOR_ROW = {
    "id": "staff-new-001",
    "user_id": None,
    "full_name": "Dr. Test Doctor",
    "specialty": "Cardiology",
    "bio": None,
    "active": True,
}


@contextmanager
def make_client(token: str, role_name: str | None, tables: dict | None = None,
                new_user_id: str = "auth-user-001"):
    """Client whose mock DB dispatches per table name.

    `tables` maps table name -> data returned by .execute(); anything not listed
    falls back to []. The `user_roles` table doubles as the auth role lookup.
    """
    explicit = tables or {}
    tables = {
        "user_roles": [{"roles": {"name": role_name}}] if role_name else [],
        "roles": {"id": 3},
        "staff": [DOCTOR_ROW],
        **explicit,
    }
    # One cached chain per table so tests can inspect the calls made on it.
    # Chains for explicitly-passed tables exist up front, so a test can stub
    # them before issuing the request; the rest appear on first use, which lets
    # tests assert a table was never touched at all.
    chains: dict[str, MagicMock] = {n: make_chain(tables[n]) for n in explicit}

    def table(name):
        if name not in chains:
            chains[name] = make_chain(tables.get(name, []))
        return chains[name]

    db = MagicMock()
    db.table.side_effect = table
    db.auth.admin.create_user.return_value = SimpleNamespace(
        user=SimpleNamespace(id=new_user_id)
    )

    with patch_db(db):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        c.mock_db = db
        c.chains = chains
        yield c


# ---- Authorization ----

def test_receptionist_cannot_create_staff(receptionist_token):
    with make_client(receptionist_token, "receptionist") as c:
        r = c.post("/staff/", json={"full_name": "Dr. X", "role": "doctor"})
        assert r.status_code == 403


def test_doctor_cannot_create_staff(admin_token):
    with make_client(admin_token, "doctor") as c:
        r = c.post("/staff/", json={"full_name": "Dr. X", "role": "doctor"})
        assert r.status_code == 403


def test_no_role_cannot_list_staff(no_role_token):
    with make_client(no_role_token, None) as c:
        assert c.get("/staff/").status_code == 403


@pytest.mark.parametrize("role", ["admin", "receptionist", "doctor"])
def test_all_roles_can_list_staff(admin_token, role):
    """Booking needs the doctor list, so reads are open to every role."""
    with make_client(admin_token, role) as c:
        assert c.get("/staff/").status_code == 200


def test_receptionist_cannot_deactivate_staff(receptionist_token):
    with make_client(receptionist_token, "receptionist") as c:
        assert c.delete("/staff/staff-new-001").status_code == 403


# ---- Creating doctors ----

def test_admin_creates_doctor_without_login(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={
            "full_name": "Dr. Test Doctor", "role": "doctor", "specialty": "Cardiology",
        })
        assert r.status_code == 201
        assert r.json()["full_name"] == "Dr. Test Doctor"
        c.mock_db.auth.admin.create_user.assert_not_called()


def test_admin_creates_doctor_with_login_provisions_auth_user(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={
            "full_name": "Dr. Test Doctor", "role": "doctor",
            "email": "doc@clinic.demo", "password": "secret123",
        })
        assert r.status_code == 201
        c.mock_db.auth.admin.create_user.assert_called_once()
        payload = c.mock_db.auth.admin.create_user.call_args[0][0]
        assert payload["email"] == "doc@clinic.demo"
        assert payload["email_confirm"] is True
        # The new login is granted its role in user_roles.
        assert c.chains["user_roles"].insert.called


def test_creating_doctor_with_work_days_writes_schedules(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={
            "full_name": "Dr. Test Doctor", "role": "doctor",
            "work_days": [0, 2, 4], "start_time": "08:00", "end_time": "16:00",
        })
        assert r.status_code == 201
        assert "schedules" in c.chains, "expected a schedules insert"
        rows = c.chains["schedules"].insert.call_args[0][0]
        assert [row["weekday"] for row in rows] == [0, 2, 4]
        assert all(row["start_time"] == "08:00:00" for row in rows)


def test_creating_doctor_without_work_days_writes_no_schedules(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={"full_name": "Dr. Test Doctor", "role": "doctor"})
        assert r.status_code == 201
        assert "schedules" not in c.chains


# ---- Creating receptionists ----

def test_admin_creates_receptionist_account(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={
            "full_name": "Elira Bytyqi", "role": "receptionist",
            "email": "recept@clinic.demo", "password": "secret123",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["user_id"] == "auth-user-001"
        assert body["full_name"] == "Elira Bytyqi"
        c.mock_db.auth.admin.create_user.assert_called_once()
        # Receptionists are login-only — no bookable staff row.
        assert "staff" not in c.chains


# ---- Account listing ----

def test_accounts_listing_joins_roles(admin_token):
    role_rows = [
        {"user_id": "u-admin", "roles": {"name": "admin"}},
        {"user_id": "u-recept", "roles": {"name": "receptionist"}},
    ]
    with make_client(admin_token, "admin", {"user_roles": role_rows}) as c:
        c.mock_db.auth.admin.list_users.return_value = [
            SimpleNamespace(id="u-admin", email="admin@clinic.demo",
                            user_metadata={"full_name": "Admin"}, created_at=None),
            SimpleNamespace(id="u-recept", email="recept@clinic.demo",
                            user_metadata={"full_name": "Elira"}, created_at=None),
            SimpleNamespace(id="u-orphan", email="orphan@clinic.demo",
                            user_metadata={}, created_at=None),
        ]
        r = c.get("/staff/accounts")
        assert r.status_code == 200
        by_email = {a["email"]: a for a in r.json()}
        assert by_email["recept@clinic.demo"]["roles"] == ["receptionist"]
        # An account with no user_roles row is still listed, with no roles.
        assert by_email["orphan@clinic.demo"]["roles"] == []


def test_receptionist_cannot_list_accounts(receptionist_token):
    with make_client(receptionist_token, "receptionist") as c:
        assert c.get("/staff/accounts").status_code == 403


def test_accounts_path_is_not_read_as_a_staff_id(admin_token):
    """`/staff/accounts` must not be captured by a `/staff/{id}` route."""
    with make_client(admin_token, "admin") as c:
        c.mock_db.auth.admin.list_users.return_value = []
        assert c.get("/staff/accounts").json() == []


# ---- Validation ----

def test_unknown_role_rejected(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={"full_name": "Someone", "role": "surgeon-general"})
        assert r.status_code == 422


def test_email_without_password_rejected(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={
            "full_name": "Dr. X", "role": "doctor", "email": "x@clinic.demo",
        })
        assert r.status_code == 422
        c.mock_db.auth.admin.create_user.assert_not_called()


def test_inverted_working_hours_rejected(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.post("/staff/", json={
            "full_name": "Dr. X", "role": "doctor",
            "work_days": [0], "start_time": "17:00", "end_time": "09:00",
        })
        assert r.status_code == 422


def test_inverted_schedule_entry_rejected(admin_token):
    with make_client(admin_token, "admin") as c:
        r = c.put("/staff/staff-new-001/schedules", json=[
            {"weekday": 0, "start_time": "18:00", "end_time": "08:00"},
        ])
        assert r.status_code == 422


# ---- Manual booking by a receptionist ----

APPOINTMENT_ROW = {
    "id": "appt-new-001", "patient_id": "pat-1", "staff_id": "staff-1",
    "service_id": None, "scheduled_at": "2026-08-03T10:00:00+00:00",
    "duration_min": 30, "status": "proposed", "notes": None,
    "created_at": "2026-07-25T09:00:00+00:00",
}


def test_receptionist_can_book_appointment_manually(receptionist_token):
    """Manual booking is a direct write — no approval gate, unlike the agent path."""
    with make_client(receptionist_token, "receptionist",
                     {"appointments": [APPOINTMENT_ROW]}) as c:
        with patch("app.api.appointments.check_conflict", return_value=None):
            r = c.post("/appointments/", json={
                "patient_id": "pat-1", "staff_id": "staff-1",
                "scheduled_at": "2026-08-03T10:00:00", "duration_min": 30,
            })
        assert r.status_code == 201
        assert r.json()["id"] == "appt-new-001"
        # No approval gate is involved in the manual path.
        assert "approval_gates" not in c.chains


def test_admin_can_book_appointment_manually(admin_token):
    with make_client(admin_token, "admin", {"appointments": [APPOINTMENT_ROW]}) as c:
        with patch("app.api.appointments.check_conflict", return_value=None):
            r = c.post("/appointments/", json={
                "patient_id": "pat-1", "staff_id": "staff-1",
                "scheduled_at": "2026-08-03T10:00:00", "duration_min": 30,
            })
        assert r.status_code == 201


def test_manual_booking_rejects_conflicting_slot(receptionist_token):
    with make_client(receptionist_token, "receptionist") as c:
        with patch("app.api.appointments.check_conflict", return_value={"id": "appt-existing"}):
            r = c.post("/appointments/", json={
                "patient_id": "pat-1", "staff_id": "staff-1",
                "scheduled_at": "2026-08-03T10:00:00", "duration_min": 30,
            })
        assert r.status_code == 409


def test_manual_booking_stores_an_unambiguous_instant(receptionist_token):
    """A naive time must be persisted with the clinic offset, not as UTC."""
    with make_client(receptionist_token, "receptionist",
                     {"appointments": [APPOINTMENT_ROW]}) as c:
        with patch("app.api.appointments.check_conflict", return_value=None):
            c.post("/appointments/", json={
                "patient_id": "pat-1", "staff_id": "staff-1",
                "scheduled_at": "2026-08-03T10:00:00", "duration_min": 30,
            })
        stored = c.chains["appointments"].insert.call_args[0][0]["scheduled_at"]
        parsed = datetime.fromisoformat(stored)
        assert parsed.tzinfo is not None, f"{stored} was stored naive"
        assert parsed.hour == 10
        assert parsed.utcoffset() == timedelta(hours=2)  # CEST


# ---- Listing order ----

def test_appointments_default_to_latest_first(receptionist_token):
    with make_client(receptionist_token, "receptionist",
                     {"appointments": [APPOINTMENT_ROW]}) as c:
        assert c.get("/appointments/").status_code == 200
        assert c.chains["appointments"].order.call_args == call("scheduled_at", desc=True)


def test_appointments_can_be_sorted_earliest_first(receptionist_token):
    with make_client(receptionist_token, "receptionist",
                     {"appointments": [APPOINTMENT_ROW]}) as c:
        assert c.get("/appointments/?sort=earliest").status_code == 200
        assert c.chains["appointments"].order.call_args == call("scheduled_at", desc=False)


# ---- Editing an existing appointment ----

def test_receptionist_can_reassign_patient_doctor_and_time(receptionist_token):
    updated = {**APPOINTMENT_ROW, "patient_id": "pat-2", "staff_id": "staff-2"}
    with make_client(receptionist_token, "receptionist",
                     {"appointments": APPOINTMENT_ROW}) as c:
        # `.maybe_single()` fetches the existing row, the update returns a list.
        c.chains["appointments"].update.return_value = make_chain([updated])
        with patch("app.api.appointments.check_conflict", return_value=None) as cc:
            r = c.patch("/appointments/appt-new-001", json={
                "patient_id": "pat-2", "staff_id": "staff-2",
                "scheduled_at": "2026-08-04T11:00:00", "duration_min": 60,
            })
        assert r.status_code == 200
        # Conflict is re-checked against the NEW doctor and duration, and the
        # appointment's own row is excluded so it never conflicts with itself.
        kwargs = cc.call_args.kwargs
        assert cc.call_args.args[0] == "staff-2"
        assert cc.call_args.args[2] == 60
        assert kwargs["exclude_appointment_id"] == "appt-new-001"


def test_editing_into_a_taken_slot_is_rejected(receptionist_token):
    with make_client(receptionist_token, "receptionist",
                     {"appointments": APPOINTMENT_ROW}) as c:
        with patch("app.api.appointments.check_conflict", return_value={"id": "other"}):
            r = c.patch("/appointments/appt-new-001",
                        json={"scheduled_at": "2026-08-04T11:00:00"})
        assert r.status_code == 409


def test_editing_only_notes_skips_the_conflict_check(receptionist_token):
    """Nothing moved, so re-checking availability would be pointless work."""
    with make_client(receptionist_token, "receptionist",
                     {"appointments": APPOINTMENT_ROW}) as c:
        c.chains["appointments"].update.return_value = make_chain([APPOINTMENT_ROW])
        with patch("app.api.appointments.check_conflict") as cc:
            r = c.patch("/appointments/appt-new-001", json={"notes": "rescheduled by phone"})
        assert r.status_code == 200
        cc.assert_not_called()


def test_doctor_cannot_edit_appointment(admin_token):
    with make_client(admin_token, "doctor") as c:
        r = c.patch("/appointments/appt-new-001", json={"notes": "x"})
        assert r.status_code == 403


def test_doctor_cannot_book_appointment(admin_token):
    with make_client(admin_token, "doctor") as c:
        r = c.post("/appointments/", json={
            "patient_id": "pat-1", "staff_id": "staff-1",
            "scheduled_at": "2026-08-03T10:00:00",
        })
        assert r.status_code == 403
