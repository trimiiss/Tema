"""
Appointment Agent — all write operations require approval gate before execution.
Business logic is deterministic Python; GPT-4o is NOT used here.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional
from app.core.database import get_db
from app.services.schedule_service import check_conflict, get_available_slots, to_clinic


def _clinic_instant(value: str) -> str:
    """Normalize an agent-supplied time to an unambiguous instant.

    The orchestrator builds these from LLM-extracted date/time text, so they
    arrive naive; `scheduled_at` is TIMESTAMPTZ and would otherwise be read
    as UTC rather than clinic wall-clock.
    """
    return to_clinic(datetime.fromisoformat(value)).isoformat()


def tool_check_patient(patient_code: str) -> Dict[str, Any]:
    db = get_db()
    resp = db.table("patients").select("id,code,first_name,last_name").eq("code", patient_code).maybe_single().execute()
    if resp.data:
        return {"found": True, "patient": resp.data}
    return {"found": False, "patient": None}


def tool_check_staff(staff_name_fragment: str) -> Dict[str, Any]:
    db = get_db()
    resp = db.table("staff").select("id,full_name,specialty").ilike("full_name", f"%{staff_name_fragment}%").eq("active", True).execute()
    return {"staff": resp.data}


def tool_available_slots(staff_id: str, date_str: str) -> Dict[str, Any]:
    dt = datetime.fromisoformat(date_str)
    slots = get_available_slots(staff_id, dt)
    return {"staff_id": staff_id, "date": date_str, "slots": slots[:10]}


def tool_check_conflict(staff_id: str, scheduled_at: str) -> Dict[str, Any]:
    dt = datetime.fromisoformat(scheduled_at)
    conflict = check_conflict(staff_id, dt)
    return {"has_conflict": bool(conflict), "conflict": conflict}


def tool_create_appointment(
    patient_id: str,
    staff_id: str,
    service_id: Optional[str],
    scheduled_at: str,
    duration_min: int,
    notes: str,
    created_by: str,
) -> Dict[str, Any]:
    db = get_db()
    data = {
        "patient_id": patient_id,
        "staff_id": staff_id,
        "scheduled_at": _clinic_instant(scheduled_at),
        "duration_min": duration_min,
        "notes": notes,
        "status": "confirmed",
        "created_by": created_by,
        "updated_by": created_by,
    }
    if service_id:
        data["service_id"] = service_id
    resp = db.table("appointments").insert(data).execute()
    return {"appointment": resp.data[0]}


def tool_cancel_appointment(appointment_id: str, updated_by: str) -> Dict[str, Any]:
    db = get_db()
    db.table("appointments").update({"status": "cancelled", "updated_by": updated_by}).eq("id", appointment_id).execute()
    return {"cancelled": True, "appointment_id": appointment_id}


def tool_reschedule_appointment(appointment_id: str, new_scheduled_at: str, updated_by: str) -> Dict[str, Any]:
    db = get_db()
    new_time = _clinic_instant(new_scheduled_at)
    db.table("appointments").update({
        "scheduled_at": new_time,
        "status": "confirmed",
        "updated_by": updated_by,
    }).eq("id", appointment_id).execute()
    return {"rescheduled": True, "appointment_id": appointment_id, "new_time": new_time}


APPOINTMENT_TOOLS = {
    "check_patient": tool_check_patient,
    "check_staff": tool_check_staff,
    "available_slots": tool_available_slots,
    "check_conflict": tool_check_conflict,
    "create_appointment": tool_create_appointment,
    "cancel_appointment": tool_cancel_appointment,
    "reschedule_appointment": tool_reschedule_appointment,
}
