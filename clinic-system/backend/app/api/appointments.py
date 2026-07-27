from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import require_roles
from app.core.database import get_db, execute_with_retry
from app.core.audit import log_action
from app.models.schemas import AppointmentCreate, AppointmentUpdate, AppointmentOut
from app.services.schedule_service import (
    check_conflict, complete_elapsed_appointments, get_available_slots, to_clinic,
)

router = APIRouter(prefix="/appointments", tags=["appointments"])

APPOINTMENT_SELECT = "*, patients(first_name,last_name,phone,email), staff(full_name), services(name)"


def _flatten(row: dict) -> dict:
    patient = row.pop("patients", None) or {}
    staff = row.pop("staff", None) or {}
    service = row.pop("services", None) or {}
    row["patient_name"] = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip() or None
    row["staff_name"] = staff.get("full_name")
    row["service_name"] = service.get("name")
    # Only useful in practice for the Booking Requests queue (source ==
    # 'patient_portal'), where a receptionist needs a way to reach the visitor
    # to confirm — but harmless to include everywhere the row is already read.
    row["patient_phone"] = patient.get("phone")
    row["patient_email"] = patient.get("email")
    return row

VALID_TRANSITIONS = {
    "proposed":  {"confirmed", "cancelled"},
    "confirmed": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


@router.get("/", response_model=list[AppointmentOut])
def list_appointments(
    date_from: str = "",
    date_to: str = "",
    staff_id: str = "",
    patient_id: str = "",
    status: str = "",
    source: str = "",
    sort: str = "latest",
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    """`sort=latest` puts the most recent appointment first; `earliest` reverses it.

    `source` filters 'staff' vs 'patient_portal' — the Booking Requests queue
    passes `source=patient_portal` because `status=proposed` alone also
    matches an appointment a receptionist just created and hasn't confirmed
    yet; both start in the same status.
    """
    # Settle anything whose time has passed before reading, so the list never
    # shows a confirmed appointment from this morning as still upcoming. Runs
    # here rather than on a scheduler because the prototype has none; it is one
    # extra query when there is nothing to promote.
    complete_elapsed_appointments()

    db = get_db()
    q = db.table("appointments").select(APPOINTMENT_SELECT)
    if date_from:
        q = q.gte("scheduled_at", to_clinic(datetime.fromisoformat(date_from)).isoformat())
    if date_to:
        # The *following* clinic midnight, half-open — `lte(date_to)` resolves to
        # 00:00 on that day and silently drops everything scheduled during it, so
        # a same-day range (`date_from == date_to`, which is how the dashboard
        # asks for "today") matched only an appointment booked at exactly
        # midnight. Same rule as `report_service._day_bounds`.
        end = datetime.fromisoformat(date_to) + timedelta(days=1)
        q = q.lt("scheduled_at", to_clinic(end).isoformat())
    if staff_id:
        q = q.eq("staff_id", staff_id)
    if patient_id:
        q = q.eq("patient_id", patient_id)
    if status:
        q = q.eq("status", status)
    if source:
        q = q.eq("source", source)
    resp = q.order("scheduled_at", desc=(sort != "earliest")).execute()
    return [_flatten(row) for row in resp.data]


@router.get("/slots")
def available_slots(
    staff_id: str,
    date: str,
    duration_min: int = 30,
    exclude_appointment_id: str = "",
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    """Open start times, as ISO strings carrying the clinic's UTC offset.

    `exclude_appointment_id` lets the edit form offer the slot the appointment
    being edited currently occupies, instead of hiding it as self-conflicting.
    """
    dt = datetime.fromisoformat(date)
    slots = get_available_slots(staff_id, dt, duration_min, exclude_appointment_id or None)
    return {"staff_id": staff_id, "date": date, "slots": slots}


@router.get("/{appointment_id}", response_model=AppointmentOut)
def get_appointment(
    appointment_id: str,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    resp = execute_with_retry(db.table("appointments").select(APPOINTMENT_SELECT).eq("id", appointment_id).maybe_single())
    if not resp.data:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return _flatten(resp.data)


@router.post("/", response_model=AppointmentOut, status_code=201)
def create_appointment(
    body: AppointmentCreate,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    conflict = check_conflict(body.staff_id, body.scheduled_at, body.duration_min)
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Time conflict with appointment {conflict['id']}",
        )
    db = get_db()
    data = body.model_dump()
    # Naive input means clinic wall-clock; store an unambiguous instant.
    data["scheduled_at"] = to_clinic(data["scheduled_at"]).isoformat()
    data["created_by"] = user["id"]
    data["updated_by"] = user["id"]
    resp = db.table("appointments").insert(data).execute()
    log_action(user["id"], "create", "appointment", resp.data[0]["id"], data)
    return resp.data[0]


@router.patch("/{appointment_id}", response_model=AppointmentOut)
def update_appointment(
    appointment_id: str,
    body: AppointmentUpdate,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    existing = execute_with_retry(db.table("appointments").select("*").eq("id", appointment_id).maybe_single())
    if not existing.data:
        raise HTTPException(status_code=404, detail="Appointment not found")

    current_status = existing.data["status"]
    if body.status and body.status not in VALID_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition from '{current_status}' to '{body.status}'",
        )

    # Deciding a guest's own booking request (accept/reject) is receptionist-only —
    # narrower than the admin+receptionist write access appointments normally get,
    # because this is the step that puts a stranger's request onto the schedule.
    is_guest_decision = (
        existing.data.get("source") == "patient_portal"
        and current_status == "proposed"
        and body.status in ("confirmed", "cancelled")
    )
    if is_guest_decision and "receptionist" not in user["roles"]:
        raise HTTPException(
            status_code=403,
            detail="Only a receptionist can accept or reject a patient-submitted booking request",
        )

    # Rescheduling, reassigning the doctor, or changing the duration all move
    # the appointment's footprint, so any of them needs a fresh conflict check
    # against the *new* values — and must ignore the appointment's own row.
    staff_id = body.staff_id or existing.data["staff_id"]
    scheduled_at = body.scheduled_at or datetime.fromisoformat(existing.data["scheduled_at"])
    duration_min = body.duration_min or existing.data["duration_min"]

    if body.scheduled_at or body.staff_id or body.duration_min:
        conflict = check_conflict(
            staff_id, scheduled_at, duration_min, exclude_appointment_id=appointment_id
        )
        if conflict:
            raise HTTPException(status_code=409, detail=f"Time conflict with {conflict['id']}")

    data = body.model_dump(exclude_none=True)
    if "scheduled_at" in data:
        data["scheduled_at"] = to_clinic(data["scheduled_at"]).isoformat()
    data["updated_by"] = user["id"]
    resp = execute_with_retry(db.table("appointments").update(data).eq("id", appointment_id))
    log_action(user["id"], "update", "appointment", appointment_id, data)
    return resp.data[0]


@router.delete("/{appointment_id}", status_code=204)
def cancel_appointment(
    appointment_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    existing = execute_with_retry(db.table("appointments").select("status").eq("id", appointment_id).maybe_single())
    if not existing.data:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if existing.data["status"] in ("cancelled", "completed"):
        raise HTTPException(status_code=422, detail="Cannot cancel this appointment")
    db.table("appointments").update({"status": "cancelled", "updated_by": user["id"]}).eq("id", appointment_id).execute()
    log_action(user["id"], "cancel", "appointment", appointment_id)
