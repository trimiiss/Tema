"""
Appointment Agent — autonomous scheduling agent.

GPT-4o drives this agent's own reasoning loop (see `runtime.run_agent_loop`) and
chooses which of the tools below to call; the orchestrator does not sequence
them. What the model cannot do is write: the three `propose_*` tools are the
only route to a change, they re-derive every fact from the database rather than
trusting the model's arguments, and a proposal that survives validation becomes
an approval gate a human must accept. The functions that actually touch
`appointments` are marked `@mutating` and are unreachable from the tool set.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional
from app.core.database import get_db, execute_with_retry
from app.agents.runtime import (
    AgentSpec, ToolSpec, handoff_tool, integer, mutating, obj, string,
)
from app.services.schedule_service import (
    check_conflict, get_available_slots, parse_when, to_clinic,
)


def _clinic_instant(value: str) -> str:
    """Normalize an agent-supplied time to an unambiguous instant.

    The orchestrator builds these from LLM-extracted date/time text, so they
    arrive naive; `scheduled_at` is TIMESTAMPTZ and would otherwise be read
    as UTC rather than clinic wall-clock.
    """
    return to_clinic(datetime.fromisoformat(value)).isoformat()


_PATIENT_COLUMNS = "id,code,first_name,last_name"


def tool_check_patient(query: str) -> Dict[str, Any]:
    """Resolve a patient from whatever the agent was told.

    Agents pass `query` as people actually speak — "Alban Krasniqi" far more
    often than "P001" — so a code-only lookup misses the common case and the
    booking dies as "patient not found". Try the code first, then fall back to
    matching the name.

    A name is only accepted when it identifies exactly one patient: booking
    the wrong Krasniqi is worse than asking the user to disambiguate, so
    ambiguous matches come back as not-found with `candidates` listed.

    The parameter name is part of the contract: it is what the model fills in
    against this tool's JSON schema, so it must match `find_patient`'s.
    """
    query = (query or "").strip()
    if not query:
        return {"found": False, "patient": None}

    db = get_db()
    resp = execute_with_retry(
        db.table("patients").select(_PATIENT_COLUMNS).eq("code", query).maybe_single()
    )
    if resp.data:
        return {"found": True, "patient": resp.data}

    matches = _search_patients_by_name(db, query)
    if len(matches) == 1:
        return {"found": True, "patient": matches[0]}
    if len(matches) > 1:
        return {"found": False, "patient": None, "candidates": matches}
    return {"found": False, "patient": None}


def _search_patients_by_name(db, query: str) -> list[dict]:
    """Patients matching a free-text name, most specific interpretation first."""
    parts = query.split()
    if len(parts) >= 2:
        # "Alban Krasniqi" — neither name column contains the whole string, so
        # match the first token against first_name and the last against last_name.
        rows = execute_with_retry(
            db.table("patients").select(_PATIENT_COLUMNS)
            .ilike("first_name", f"%{parts[0]}%")
            .ilike("last_name", f"%{parts[-1]}%")
        ).data
        if rows:
            return rows
    return execute_with_retry(
        db.table("patients").select(_PATIENT_COLUMNS)
        .or_(f"first_name.ilike.%{query}%,last_name.ilike.%{query}%")
    ).data


_TITLES = {"dr", "dr.", "doctor", "prof", "prof.", "mr", "mr.", "ms", "ms.", "mrs", "mrs."}


def tool_check_staff(name: str) -> Dict[str, Any]:
    """Find active staff by however the user named them.

    People say "Dr. Hoxha" for "Dr. Arben Hoxha", and a single `ilike` on the
    whole fragment needs the substring to be contiguous — it isn't, so the
    booking fails on a doctor who plainly exists. Fall back to requiring every
    non-title token to appear somewhere in the name.
    """
    fragment = (name or "").strip()
    if not fragment:
        return {"staff": []}

    db = get_db()
    base = db.table("staff").select("id,full_name,specialty").eq("active", True)
    rows = execute_with_retry(base.ilike("full_name", f"%{fragment}%")).data
    if rows:
        return {"staff": rows}

    tokens = [t for t in fragment.split() if t.lower().strip(".,") not in _TITLES]
    if not tokens:
        return {"staff": []}

    # Chained ilike filters AND together: every token must appear in the name.
    q = db.table("staff").select("id,full_name,specialty").eq("active", True)
    for token in tokens:
        q = q.ilike("full_name", f"%{token}%")
    return {"staff": execute_with_retry(q).data}


def tool_available_slots(staff_id: str, date: str) -> Dict[str, Any]:
    dt = parse_when(date)
    slots = get_available_slots(staff_id, dt)
    return {"staff_id": staff_id, "date": date, "slots": slots[:10]}


def tool_check_conflict(staff_id: str, scheduled_at: str) -> Dict[str, Any]:
    dt = datetime.fromisoformat(scheduled_at)
    conflict = check_conflict(staff_id, dt)
    return {"has_conflict": bool(conflict), "conflict": conflict}


def tool_check_availability(staff_id: str, scheduled_at: str, duration_min: int = 30) -> Dict[str, Any]:
    """Is the requested start actually a slot this doctor works?

    `check_conflict` only proves nothing else is booked then — an empty
    Sunday passes it cleanly, which is how the agent path booked a doctor
    outside their working days while the manual form (which picks from
    `get_available_slots`) could not. Compare instants, never ISO strings:
    the same moment has several valid spellings.
    """
    requested = to_clinic(datetime.fromisoformat(scheduled_at))
    slots = get_available_slots(staff_id, requested, duration_min)
    open_instants = {datetime.fromisoformat(s) for s in slots}
    return {
        "available": requested in open_instants,
        "requested": requested.isoformat(),
        "open_slots": slots[:6],
    }


def tool_list_appointments(patient_query: str) -> Dict[str, Any]:
    """Upcoming and recent appointments for whoever `patient_query` identifies."""
    found = tool_check_patient(patient_query)
    if not found["found"]:
        return {
            "found": False,
            "candidates": found.get("candidates", []),
            "error": f"No single patient matches '{patient_query}'.",
        }
    patient = found["patient"]
    rows = execute_with_retry(
        get_db().table("appointments")
        .select("id,scheduled_at,duration_min,status,staff_id,notes")
        .eq("patient_id", patient["id"])
        .order("scheduled_at", desc=True)
        .limit(20)
    ).data
    return {"found": True, "patient": patient, "appointments": rows}


# ---------------------------------------------------------------- proposals
#
# Everything below refuses to trust the model. An id it passes must resolve to a
# real row, a time it picks must clear both the conflict check and the doctor's
# schedule, and the human-readable description shown on the approval card is
# built from the rows we just read — never from the model's own words. A model
# that hallucinates a patient id or a Sunday slot gets an error back and has to
# think again; it cannot open a gate on a fact that isn't true.


def _load_patient(patient_id: str) -> Optional[dict]:
    return execute_with_retry(
        get_db().table("patients").select(_PATIENT_COLUMNS).eq("id", patient_id).maybe_single()
    ).data


def _load_staff(staff_id: str) -> Optional[dict]:
    return execute_with_retry(
        get_db().table("staff").select("id,full_name,specialty").eq("id", staff_id).maybe_single()
    ).data


def _load_appointment(appointment_id: str) -> Optional[dict]:
    return execute_with_retry(
        get_db().table("appointments")
        .select("id,patient_id,staff_id,scheduled_at,duration_min,status")
        .eq("id", appointment_id)
        .maybe_single()
    ).data


def _slot_is_workable(staff_id: str, when: datetime, duration_min: int,
                      exclude_appointment_id: Optional[str] = None) -> Dict[str, Any]:
    """Both scheduling checks, in the order that gives the most useful error.

    A free slot is not the same as a slot the doctor works: `check_conflict`
    passes cleanly for an empty Sunday, so the availability check has to run too
    or the agent proposes times the manual booking form would never offer.
    """
    conflict = check_conflict(staff_id, when, duration_min, exclude_appointment_id)
    if conflict:
        return {"ok": False, "error": "That time is already booked.", "conflict": conflict}

    slots = get_available_slots(staff_id, when, duration_min, exclude_appointment_id)
    open_instants = {datetime.fromisoformat(s) for s in slots}
    if to_clinic(when) not in open_instants:
        return {
            "ok": False,
            "error": "The doctor does not work that slot.",
            "open_slots": slots[:8],
        }
    return {"ok": True}


def propose_create_appointment(patient_id: str, staff_id: str, scheduled_at: str,
                               duration_min: int = 30, notes: str = "",
                               service_id: Optional[str] = None) -> Dict[str, Any]:
    patient = _load_patient(patient_id)
    if not patient:
        return {"proposed": False, "error": f"No patient with id '{patient_id}'. Look the patient up first."}
    staff = _load_staff(staff_id)
    if not staff:
        return {"proposed": False, "error": f"No staff member with id '{staff_id}'. Look the doctor up first."}

    try:
        when = parse_when(scheduled_at)
    except ValueError as exc:
        return {"proposed": False, "error": f"Could not read '{scheduled_at}' as a date and time: {exc}"}

    duration = int(duration_min or 30)
    check = _slot_is_workable(staff["id"], when, duration)
    if not check["ok"]:
        return {"proposed": False, **{k: v for k, v in check.items() if k != "ok"}}

    patient_name = f"{patient['first_name']} {patient['last_name']}"
    return {
        "proposed": True,
        "action": {
            "action": "create",
            "patient_id": patient["id"],
            "patient_name": patient_name,
            "staff_id": staff["id"],
            "staff_name": staff["full_name"],
            "scheduled_at": when.isoformat(),
            "duration_min": duration,
            "service_id": service_id,
            "notes": notes or "",
        },
        "description": (
            f"Create appointment for {patient_name} ({patient['code']}) with "
            f"{staff['full_name']} on {when.strftime('%Y-%m-%d %H:%M')} "
            f"for {duration} minutes"
        ),
    }


def propose_cancel_appointment(appointment_id: str, reason: str = "") -> Dict[str, Any]:
    appt = _load_appointment(appointment_id)
    if not appt:
        return {"proposed": False, "error": f"No appointment with id '{appointment_id}'."}
    if appt["status"] == "cancelled":
        return {"proposed": False, "error": "That appointment is already cancelled."}

    patient = _load_patient(appt["patient_id"])
    label = f"{patient['first_name']} {patient['last_name']}" if patient else "the patient"
    when = to_clinic(datetime.fromisoformat(appt["scheduled_at"]))
    return {
        "proposed": True,
        "action": {"action": "cancel", "appointment_id": appointment_id, "reason": reason},
        "description": (
            f"Cancel {label}'s appointment on {when.strftime('%Y-%m-%d %H:%M')}"
            + (f" — {reason}" if reason else "")
        ),
    }


def propose_reschedule_appointment(appointment_id: str, new_scheduled_at: str) -> Dict[str, Any]:
    appt = _load_appointment(appointment_id)
    if not appt:
        return {"proposed": False, "error": f"No appointment with id '{appointment_id}'."}
    if appt["status"] == "cancelled":
        return {"proposed": False, "error": "That appointment is cancelled; book a new one instead."}

    try:
        when = parse_when(new_scheduled_at)
    except ValueError as exc:
        return {"proposed": False, "error": f"Could not read '{new_scheduled_at}' as a date and time: {exc}"}

    duration = appt.get("duration_min") or 30
    # Exclude the appointment being moved, or it conflicts with itself.
    check = _slot_is_workable(appt["staff_id"], when, duration, exclude_appointment_id=appointment_id)
    if not check["ok"]:
        return {"proposed": False, **{k: v for k, v in check.items() if k != "ok"}}

    patient = _load_patient(appt["patient_id"])
    label = f"{patient['first_name']} {patient['last_name']}" if patient else "the patient"
    old = to_clinic(datetime.fromisoformat(appt["scheduled_at"]))
    return {
        "proposed": True,
        "action": {
            "action": "reschedule",
            "appointment_id": appointment_id,
            "new_scheduled_at": when.isoformat(),
        },
        "description": (
            f"Move {label}'s appointment from {old.strftime('%Y-%m-%d %H:%M')} "
            f"to {when.strftime('%Y-%m-%d %H:%M')}"
        ),
    }


# ------------------------------------------------------------------- writes
#
# Reachable only from `resume_orchestrator`, after a human approved the gate.


@mutating
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


@mutating
def tool_cancel_appointment(appointment_id: str, updated_by: str) -> Dict[str, Any]:
    db = get_db()
    db.table("appointments").update({"status": "cancelled", "updated_by": updated_by}).eq("id", appointment_id).execute()
    return {"cancelled": True, "appointment_id": appointment_id}


@mutating
def tool_reschedule_appointment(appointment_id: str, new_scheduled_at: str, updated_by: str) -> Dict[str, Any]:
    db = get_db()
    new_time = _clinic_instant(new_scheduled_at)
    db.table("appointments").update({
        "scheduled_at": new_time,
        "status": "confirmed",
        "updated_by": updated_by,
    }).eq("id", appointment_id).execute()
    return {"rescheduled": True, "appointment_id": appointment_id, "new_time": new_time}


APPOINTMENT_INSTRUCTIONS = """
You are the Appointment Agent for a clinic. You own scheduling: booking,
cancelling, rescheduling, and answering questions about who is booked when.

How to work:
- Resolve people before you act. `find_patient` and `find_staff` take whatever
  the user said — a code like P001, or a name like "Dr. Hoxha" — and return real
  ids. Use those ids; never guess one.
- If `find_patient` comes back with several candidates, do not pick one. Ask the
  user which they meant.
- Before proposing a booking, check the doctor actually has the slot. If the
  requested time does not work, call `list_available_slots` for that day and
  offer the user the real alternatives instead of proposing anyway.
- The `propose_*` tools re-check everything themselves and will refuse a slot
  that is booked or outside the doctor's hours. If one refuses, read the
  `open_slots` it gives you and offer those.
- If the patient does not exist yet and the user wants them registered, hand off
  to the patient agent — registering patients is not your job. Include the
  doctor, date and time you already worked out in the handoff task.
""".strip()

APPOINTMENT_AGENT = AgentSpec(
    name="appointment_agent",
    purpose="Books, cancels, reschedules and looks up appointments; knows doctors' schedules and free slots.",
    instructions=APPOINTMENT_INSTRUCTIONS,
    tools=(
        ToolSpec(
            name="find_patient",
            description="Find a patient by code (P001) or by name. Returns the patient id needed for booking.",
            parameters=obj({"query": string("A patient code or full name.")}, ["query"]),
            fn=tool_check_patient,
        ),
        ToolSpec(
            name="find_staff",
            description="Find active clinical staff by name or name fragment, e.g. 'Hoxha' or 'Dr. Arben Hoxha'.",
            parameters=obj({"name": string("Doctor's name or part of it.")}, ["name"]),
            fn=tool_check_staff,
        ),
        ToolSpec(
            name="list_available_slots",
            description="Open start times for a doctor on a given day. Use this whenever a requested time does not work.",
            parameters=obj(
                {
                    "staff_id": string("Staff id from find_staff."),
                    "date": string("The day, as YYYY-MM-DD."),
                },
                ["staff_id", "date"],
            ),
            fn=tool_available_slots,
        ),
        ToolSpec(
            name="check_slot",
            description="Check one specific start time for a doctor: is it free, and does the doctor work then?",
            parameters=obj(
                {
                    "staff_id": string("Staff id from find_staff."),
                    "scheduled_at": string("Start time as YYYY-MM-DDTHH:MM."),
                    "duration_min": integer("Appointment length in minutes; 30 if unsure."),
                },
                ["staff_id", "scheduled_at"],
            ),
            fn=tool_check_availability,
        ),
        ToolSpec(
            name="list_patient_appointments",
            description="List a patient's appointments, with their ids — needed before cancelling or rescheduling.",
            parameters=obj({"patient_query": string("Patient code or name.")}, ["patient_query"]),
            fn=tool_list_appointments,
        ),
        ToolSpec(
            name="propose_create_appointment",
            description=(
                "Propose booking an appointment. This does NOT book it: it opens an approval "
                "gate for a human. Refuses times that are taken or outside the doctor's hours."
            ),
            parameters=obj(
                {
                    "patient_id": string("Patient id from find_patient."),
                    "staff_id": string("Staff id from find_staff."),
                    "scheduled_at": string("Start time as YYYY-MM-DDTHH:MM."),
                    "duration_min": integer("Length in minutes; 30 if the user did not say."),
                    "notes": string("Anything the user asked to be recorded. May be empty."),
                },
                ["patient_id", "staff_id", "scheduled_at"],
            ),
            fn=propose_create_appointment,
            kind="proposal",
        ),
        ToolSpec(
            name="propose_cancel_appointment",
            description="Propose cancelling an existing appointment. Opens an approval gate; does not cancel it.",
            parameters=obj(
                {
                    "appointment_id": string("Id from list_patient_appointments."),
                    "reason": string("Why it is being cancelled, if the user said."),
                },
                ["appointment_id"],
            ),
            fn=propose_cancel_appointment,
            kind="proposal",
        ),
        ToolSpec(
            name="propose_reschedule_appointment",
            description="Propose moving an appointment to a new time. Opens an approval gate; does not move it.",
            parameters=obj(
                {
                    "appointment_id": string("Id from list_patient_appointments."),
                    "new_scheduled_at": string("New start time as YYYY-MM-DDTHH:MM."),
                },
                ["appointment_id", "new_scheduled_at"],
            ),
            fn=propose_reschedule_appointment,
            kind="proposal",
        ),
        handoff_tool(
            "patient_agent",
            "Hand off to the patient agent — registering a new patient, or correcting "
            "patient details. Use this when the patient you need does not exist yet.",
        ),
    ),
)
