"""
Appointment Agent — autonomous scheduling agent.

GPT-4o drives this agent's own reasoning loop (see `runtime.run_agent_loop`) and
chooses which of the tools below to call; the orchestrator does not sequence
them. What the model cannot do is write: the three `propose_*` tools are the
only route to a change, they re-derive every fact from the database rather than
trusting the model's arguments, and a proposal that survives validation becomes
an approval gate a human must accept. The functions that actually touch
`appointments` are marked `@mutating` and are unreachable from the tool set.

This module also defines `PUBLIC_APPOINTMENT_AGENT` further down — the one
agent an unauthenticated visitor ever reaches, from `POST /public/booking/chat`.
It shares this file and its scheduling helpers with the staff-facing
`APPOINTMENT_AGENT` above, but is a deliberately smaller, separately-defined
`AgentSpec`, not a restricted mode of the same one:

- **No patient-lookup tool.** No `find_patient`, no `list_patient_appointments`
  — nothing that lets an anonymous caller ask "does patient Krasniqi exist"
  and get an answer. `APPOINTMENT_AGENT`'s `find_patient` is fine for a
  receptionist; it is exactly the attack surface a public, unauthenticated
  endpoint must not expose.
- **No handoff tool.** It cannot reach `patient_agent` (which does have a
  patient-lookup-shaped tool) or anything else — a handoff path in would be a
  lookup tool by another name.
- **Nothing here writes**, same invariant as the staff agent. `propose_booking`
  is a `kind="proposal"` tool: it validates deterministically and hands back a
  gate, and never touches `patients` or `appointments`. Unlike
  `propose_create_appointment`, it cannot even look up a patient to attach the
  booking to — there is no patient yet. Matching or creating one happens in
  `app.services.patient_matching`, run only from
  `public_orchestrator.resume_public_booking` after the *visitor* confirms,
  never as something this agent's model can call.
- **Which doctor to suggest is decided in `app.services.triage`, not by this
  agent guessing from symptoms.** The model is given a fixed list of
  administrative reasons-for-visit; it never reads or reasons about symptom
  text. That keeps `SHARED_RULES`' clinical refusal true rather than something
  a persistent visitor can talk around.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from postgrest.exceptions import APIError
from app.core.database import get_db, execute_with_retry
from app.agents.runtime import (
    AgentSpec, ToolSpec, handoff_tool, integer, mutating, obj, string,
)
from app.services import triage
from app.services.schedule_service import (
    check_conflict, clinic_now, clinic_today, drop_past_slots,
    earliest_bookable_instant, get_available_slots, parse_when, to_clinic,
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

    Shared by the staff proposal tools below and by `PUBLIC_APPOINTMENT_AGENT`'s
    `propose_booking` — the check is identical either way, only who ends up
    attached to the appointment differs.
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


# ===================================================== public booking agent
#
# Everything below is the public, unauthenticated surface described in the
# module docstring. It reuses `_slot_is_workable`, `get_available_slots`,
# `parse_when` and `to_clinic` from the staff agent above rather than
# duplicating the scheduling math — the check is the same regardless of who
# is asking. What it deliberately does NOT reuse is any staff tool that looks
# up or lists patient data, or the handoff tool.

_PUBLIC_STAFF_COLUMNS = "id,full_name,specialty,bio"
MAX_LOOKAHEAD_DAYS = 14
DEFAULT_DURATION_MIN = 30
# How many times to put in front of a visitor at once: enough to be a choice,
# few enough to read as a row of buttons rather than a timetable.
MAX_SLOT_OPTIONS = 6
MAX_SLOTS_PER_DOCTOR_PER_DAY = 2


def tool_list_reasons() -> Dict[str, Any]:
    return {"reasons": triage.list_reasons()}


def tool_list_doctors(specialty: str) -> Dict[str, Any]:
    """Active doctors, optionally filtered to one specialty.

    `ilike` with no wildcard is a case-insensitive equality check, which is
    all that is needed here since `specialty` is expected to be one of the
    values `tool_specialty_for_reason` returns — but if a model passes
    something that matches nothing, fall back to the full active list rather
    than a dead end.
    """
    db = get_db()
    base = db.table("staff").select(_PUBLIC_STAFF_COLUMNS).eq("active", True)
    specialty = (specialty or "").strip()
    if specialty:
        rows = execute_with_retry(base.ilike("specialty", specialty)).data
        if rows:
            return {"doctors": rows, "specialty": specialty}
    rows = execute_with_retry(db.table("staff").select(_PUBLIC_STAFF_COLUMNS).eq("active", True)).data
    return {"doctors": rows, "specialty": specialty or None}


def tool_specialty_for_reason(reason: str) -> Dict[str, Any]:
    """Map a reason code from `list_reasons` to a specialty that has an active doctor.

    The doctors come back with it. Left to look them up in a second call, a
    model often skips it and asks "do you have a preferred doctor?" instead —
    a question the visitor cannot answer, because the names have never been in
    front of them. Returning the list makes the answer to that question part
    of the same result, and the booking page renders it as pickable cards.
    """
    specialty = triage.specialty_for(reason)
    # `specialty` last: `tool_list_doctors` reports back whatever it filtered
    # on, and the routed specialty is the answer to the question asked here.
    return {"reason": reason, **tool_list_doctors(specialty), "specialty": specialty}


def tool_list_available_slots(staff_id: str, date: str) -> Dict[str, Any]:
    """Open start times for one doctor on one day — future ones only.

    A visitor cannot walk into this morning, so a day that has already been
    and gone comes back as a refusal, and today comes back with the hours that
    have already passed removed (`drop_past_slots`). Without this the model
    reads 09:00 off a schedule at four in the afternoon and offers it, which
    is exactly the slot the validator would then refuse.
    """
    try:
        dt = parse_when(date)
    except ValueError as exc:
        return {"error": f"Could not read '{date}' as a date: {exc}"}

    if dt.date() < clinic_today():
        return {
            "staff_id": staff_id, "date": date, "slots": [],
            "error": (
                f"{dt.date().isoformat()} has already passed — today is "
                f"{clinic_today().isoformat()}. Ask for a day from today onwards."
            ),
        }

    slots = drop_past_slots(get_available_slots(staff_id, dt, DEFAULT_DURATION_MIN))
    out: Dict[str, Any] = {"staff_id": staff_id, "date": date, "slots": slots[:10]}
    if not slots:
        out["note"] = (
            "Nothing left open on that day. Call `find_earliest_slot` and offer "
            "the next openings instead of asking the visitor to guess a day."
        )
    return out


def _upcoming_options(doctors: list, start_day, limit: int = MAX_SLOT_OPTIONS,
                      per_doctor_per_day: int = MAX_SLOTS_PER_DOCTOR_PER_DAY,
                      duration_min: int = DEFAULT_DURATION_MIN) -> list:
    """The next `limit` bookable slots across `doctors`, earliest first.

    Scans forward day by day from `start_day` and stops as soon as it has
    enough — a doctor with a wide-open fortnight would otherwise fill the list
    from one morning, and the visitor would be choosing between six times with
    the same doctor rather than between the clinic's soonest openings.
    """
    options: list = []
    for offset in range(MAX_LOOKAHEAD_DAYS):
        day = start_day + timedelta(days=offset)
        day_dt = datetime.combine(day, datetime.min.time())
        for doc in doctors:
            slots = drop_past_slots(get_available_slots(doc["id"], day_dt, duration_min))
            for slot in slots[:per_doctor_per_day]:
                options.append({
                    "staff_id": doc["id"],
                    "staff_name": doc["full_name"],
                    "date": day.isoformat(),
                    "slot": slot,
                })
        if len(options) >= limit:
            break
    # Sorted as instants, not as strings: a fortnight can straddle the DST
    # change, and "…T09:00+01:00" sorts after "…T09:00+02:00" while being an
    # hour later in real time.
    options.sort(key=lambda o: datetime.fromisoformat(o["slot"]))
    return options[:limit]


def tool_find_earliest_slot(specialty: str, from_date: str = "") -> Dict[str, Any]:
    """The soonest open slots with the active doctors in `specialty`.

    Powers "what's the earliest you have" without the model having to call
    `list_available_slots` once per doctor per day itself.

    Returns a handful of `options`, not just the single earliest one: a visitor
    handed one take-it-or-leave-it time has to type a counter-offer, while a
    short list of real openings is something the booking page renders as
    buttons they can tap. `slot`/`staff_id`/`staff_name` still describe the
    earliest of them, so anything reading the first option keeps working.

    Every option is in the future — a slot earlier today is gone
    (`drop_past_slots`), and a `from_date` already behind us is clamped to
    today rather than scanned.
    """
    doctors = tool_list_doctors(specialty)["doctors"]
    if not doctors:
        return {"found": False, "error": f"No active doctors for '{specialty}'."}

    try:
        start_day = parse_when(from_date).date() if from_date else clinic_today()
    except ValueError as exc:
        return {"found": False, "error": f"Could not read '{from_date}' as a date: {exc}"}
    start_day = max(start_day, clinic_today())

    options = _upcoming_options(doctors, start_day)
    if not options:
        return {"found": False, "error": f"Nothing open in the next {MAX_LOOKAHEAD_DAYS} days."}

    first = options[0]
    return {
        "found": True,
        "staff_id": first["staff_id"],
        "staff_name": first["staff_name"],
        "date": first["date"],
        "slot": first["slot"],
        "options": options,
    }


_SERVICE_COLUMNS = "id,name,duration_minutes,description"


def tool_list_services() -> Dict[str, Any]:
    rows = execute_with_retry(
        get_db().table("services").select(_SERVICE_COLUMNS).order("name")
    ).data
    return {"services": rows}


def _load_service(service_id: str) -> Optional[dict]:
    """The service with this id, or None. Same UUID-safety as `_load_active_staff`."""
    if not (service_id or "").strip():
        return None
    try:
        return execute_with_retry(
            get_db().table("services").select(_SERVICE_COLUMNS).eq("id", service_id).maybe_single()
        ).data
    except APIError:
        return None


# ---------------------------------------------------------------- proposal
#
# The only write-shaped tool this agent has, and it does not write. It re-checks
# the doctor and the slot from the database — never trusts the model's claim
# that a slot is free — and it deliberately does NOT look up or create a
# patient: that only happens after the human (the visitor) confirms, via
# `public_orchestrator.resume_public_booking`.


def _load_active_staff(staff_id: str) -> Optional[dict]:
    """The doctor with this id, or None.

    `staff.id` is a UUID column, so an id the model invented rather than read
    from a tool result ('doc123') is not merely absent — Postgres rejects the
    comparison and postgrest surfaces it as an `APIError`, which would escape
    `propose_booking` as an unhandled exception and end the run with a generic
    "technical issue". A malformed id means the same thing to the caller as an
    unknown one, so it comes back as None and the model gets told to look the
    doctor up first.
    """
    try:
        return execute_with_retry(
            get_db().table("staff").select(_PUBLIC_STAFF_COLUMNS).eq("id", staff_id).eq("active", True).maybe_single()
        ).data
    except APIError:
        # `execute_with_retry` has already exhausted the transient transport
        # failures, so an APIError surviving to here is the query itself being
        # unanswerable — for a lookup whose only variable is `staff_id`, that
        # means the id is unusable. Same answer as "no such doctor".
        return None


def propose_booking(
    first_name: str, last_name: str, staff_id: str, scheduled_at: str,
    phone: str = "", email: str = "", reason: str = "", notes: str = "",
    service_id: str = "",
) -> Dict[str, Any]:
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip()

    # Which service, first — it is step 1 of the conversation and it is what
    # sets how long to block out: a 15-minute document check must not reserve
    # the 30 minutes a checkup needs, and the length comes from the service's
    # own row, never from the model. An absent or unrecognised service is a
    # refusal rather than a silent fallback to the default, because guessing
    # books the visitor for a length nobody chose. The refusal is fed back as
    # an ordinary tool result, which is what sends the model to go and ask.
    service = _load_service(service_id)
    if not service:
        # The refusal carries the catalogue with it. Left to "ask them again",
        # a model tends to re-ask in prose — and the visitor is then choosing
        # from a list only the model can see. Handing back the real rows means
        # the reminder names the actual services, and the booking page renders
        # them as the same pickable cards `list_services` produces.
        return {
            "proposed": False,
            "error": (
                "No service chosen yet — remind the visitor to pick one before this can "
                "be booked. Show them the services below, ask which they want, and call "
                "`propose_booking` again with that service's id as `service_id`."
            ),
            **tool_list_services(),
        }
    duration = int(service["duration_minutes"])

    if not last_name:
        return {"proposed": False, "error": "A last name is required to submit a booking request."}
    if not phone and not email:
        return {"proposed": False, "error": "A phone number or email is required so the clinic can confirm this."}

    staff = _load_active_staff(staff_id)
    if not staff:
        return {"proposed": False, "error": f"No active doctor with id '{staff_id}'. Look the doctor up first."}

    try:
        when = parse_when(scheduled_at)
    except ValueError as exc:
        return {"proposed": False, "error": f"Could not read '{scheduled_at}' as a date and time: {exc}"}

    # A time that has already been and gone. The schedule still says the doctor
    # works 09:00 on a Monday morning that is now over, so `_slot_is_workable`
    # would wave it through and the visitor would be handed a confirmation card
    # for an appointment they cannot attend. The refusal carries this doctor's
    # real next openings, so the model offers those instead of asking the
    # visitor to guess another time — and the booking page renders them as
    # buttons, the same way it renders `find_earliest_slot`'s options.
    now = clinic_now()
    if to_clinic(when) < earliest_bookable_instant():
        return {
            "proposed": False,
            "error": (
                f"{to_clinic(when).strftime('%d %b at %H:%M')} is in the past — it is now "
                f"{now.strftime('%d %b, %H:%M')}. Offer the visitor one of the times below instead."
            ),
            "staff_id": staff["id"],
            "staff_name": staff["full_name"],
            "options": _upcoming_options([staff], clinic_today(), duration_min=duration),
        }

    # Reuses the staff agent's `_slot_is_workable` — same conflict + working-hours
    # check either way, just with no `exclude_appointment_id` (this always books
    # a new appointment, never moves an existing one).
    check = _slot_is_workable(staff["id"], when, duration)
    if not check["ok"]:
        return {"proposed": False, **{k: v for k, v in check.items() if k != "ok"}}

    patient_name = f"{first_name} {last_name}".strip()
    return {
        "proposed": True,
        "action": {
            "action": "create_booking",
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "staff_id": staff["id"],
            "staff_name": staff["full_name"],
            "service_id": service["id"],
            "service_name": service["name"],
            "scheduled_at": when.isoformat(),
            "duration_min": duration,
            "reason": reason or "",
            "notes": notes or "",
        },
        "description": (
            f"Book {patient_name} for a {service['name']} with {staff['full_name']} "
            f"on {when.strftime('%Y-%m-%d %H:%M')}"
        ),
    }


PUBLIC_BOOKING_INSTRUCTIONS = """
You are the appointment booking assistant on the clinic's public website. The
person you are talking to is a visitor, not clinic staff — you have never seen
them before and you cannot look up whether they already have a record here.

How to work, in order:
1. Establish which service they are booking, before anything else. Call
   `list_services` when the service is still unsettled and the catalogue is not
   already in the context you were given — anything already shown to this
   visitor is listed there with its id, and that id is all you need. If they
   already named a service, match it to a real one and keep its id. If they did
   not name one — or named something that isn't in the list — ask them to
   choose from the list, showing the service names back to them, and wait for
   their answer before moving on. Do not pick a service for them, and do not
   move on to doctors or times with the service still unsettled. If they are
   unsure what they need, offer `list_reasons` to narrow it down and then come
   back and confirm which service that means — every booking needs one. Never
   ask them to describe symptoms or what is wrong with them.
   Once the service is settled it stays settled, including when they named it
   in the very message you are answering: carry its id forward, go straight on
   to step 2 in the same turn, and never list the services again. Do not stop
   to announce their own choice back to them and wait.
2. Work out who should see them, and show them the actual doctors. Call
   `find_specialty_for_reason` for the specialty, then `list_doctors` — with an
   empty specialty it lists everyone. Name the doctors that came back, with
   their specialty, as a short list to pick from. Never ask "do you have a
   preferred doctor?" or "any specialty in mind?" without the list attached:
   the visitor cannot see your tools, so an open question like that asks them
   to guess at names they have never been shown. If exactly one doctor comes
   back, take them and say who it is rather than asking someone to pick from a
   list of one. Once a doctor is settled, do not list the doctors again.
3. Find them a time. If they name a day *and* a time ("tomorrow at 10am"),
   that is their choice — say it back to them with the doctor's name and move
   straight to step 4. Do not answer a time they have chosen with a list of
   other times, and do not call `list_available_slots` to double-check it:
   step 5 re-checks the slot against the schedule and hands you that doctor's
   real alternatives if it turns out to be taken. Only offer a list when they
   have not settled on a time — `find_earliest_slot` for "the soonest you
   have", `list_available_slots` for a day they name without a time — and then
   offer what came back as a few concrete choices and let them pick one.
4. Once the doctor and the time are settled, the only thing left to ask for is
   who they are: their first name, last name, and a phone number or email (at
   least one contact method is required — say so if they give neither). Ask for
   those and nothing else — do not re-open the service, the doctor or the time.
5. Call `propose_booking`, passing the `service_id` from step 1 — it is
   required and it sets how long the appointment runs, so if you reach this
   point without one the call is refused and you must go back and ask. This
   does not book anything by itself: it puts the details in front of them to
   check. Tell them to confirm, and that the appointment is booked once they
   do. Never claim it is booked before that.
6. If `propose_booking` refuses a slot, read why and offer the alternatives it
   gives you, or ask them to pick a different time.

Rules specific to this surface:
- You have no tool to look up a patient, a record, or another visitor's
  booking, and none should ever be implied to exist. If asked to find, check,
  or list any patient information, say you cannot do that here — a
  receptionist can help by phone.
- Do not ask about or discuss symptoms, diagnoses, medication, or how someone
  is feeling beyond the fixed reason categories. If they describe symptoms
  unprompted, do not interpret them — just continue with the reason categories
  and, if relevant, remind them this is not the place for medical advice.
- Never invent a doctor, a specialty, a time slot, or an id. Everything you
  reference must have come back from a tool in this conversation.
- Offer choices, do not ask the visitor to compose an answer. Whenever a tool
  has just returned services, doctors or slots, list them by name in your
  reply — the booking page turns each one into a button they can tap, so a
  reply that names them is a reply they can answer with one tap.
- Look something up only when you are about to offer it. The booking page draws
  every list you fetch as buttons under your reply, so fetching the services or
  the doctors again after the visitor has chosen puts that choice back on their
  screen and reads as though you had forgotten their answer.
- Never offer, repeat or confirm a time that has already passed. Today's date
  and the current clinic time are given to you at the start of the task; the
  earliest time you can offer is later today, and a slot from this morning is
  gone. If a day has no openings left, say so and offer the next ones instead.
""".strip()

PUBLIC_APPOINTMENT_AGENT = AgentSpec(
    name="booking_agent",
    purpose="Public-facing: helps a visitor pick a reason for visit, a doctor and a time, and books the appointment once the visitor confirms.",
    instructions=PUBLIC_BOOKING_INSTRUCTIONS,
    tools=(
        ToolSpec(
            name="list_reasons",
            description="The fixed list of administrative reasons a visitor can choose from, each with its specialty.",
            parameters=obj({}),
            fn=tool_list_reasons,
        ),
        ToolSpec(
            name="find_specialty_for_reason",
            description=(
                "Map a reason code from list_reasons to the specialty that handles it, "
                "and the active doctors who work it. Show those doctors to the visitor "
                "by name — the booking page renders them as cards they can pick."
            ),
            parameters=obj({"reason": string("A reason code from list_reasons, e.g. 'general_checkup'.")}, ["reason"]),
            fn=tool_specialty_for_reason,
        ),
        ToolSpec(
            name="list_doctors",
            description="Active doctors, optionally filtered by specialty (e.g. 'Pediatrics').",
            parameters=obj({"specialty": string("Specialty to filter by. Empty string for all active doctors.")}, ["specialty"]),
            fn=tool_list_doctors,
        ),
        ToolSpec(
            name="list_available_slots",
            description=(
                "Open start times for one doctor on one day, with any hour that has "
                "already passed removed. The booking page renders them as buttons."
            ),
            parameters=obj(
                {
                    "staff_id": string("Doctor id from list_doctors."),
                    "date": string("The day, as YYYY-MM-DD, or 'tomorrow'."),
                },
                ["staff_id", "date"],
            ),
            fn=tool_list_available_slots,
        ),
        ToolSpec(
            name="find_earliest_slot",
            description=(
                "The soonest open slots with the active doctors in a specialty, as a short "
                "list of options the visitor can pick from. Use when the visitor wants 'the "
                "soonest available'. Never returns a time that has already passed."
            ),
            parameters=obj(
                {
                    "specialty": string("Specialty to search, e.g. 'General Practice'."),
                    "from_date": string("Start searching from this day (YYYY-MM-DD). Empty string for today."),
                },
                ["specialty"],
            ),
            fn=tool_find_earliest_slot,
        ),
        ToolSpec(
            name="list_services",
            description=(
                "The clinic's service catalogue, for settling which service the visitor "
                "is booking — the booking page renders the result as buttons they can "
                "pick from. Call it while that is still open; once they have chosen, "
                "their service's id is carried forward for you and calling this again "
                "just re-asks a question they have answered."
            ),
            parameters=obj({}),
            fn=tool_list_services,
        ),
        ToolSpec(
            name="propose_booking",
            description=(
                "Put the booking to the visitor for confirmation. This does NOT create an "
                "appointment — it opens a gate the visitor must confirm, and the appointment "
                "is booked when they do. Refuses a doctor id that doesn't exist or a slot "
                "that is unavailable."
            ),
            parameters=obj(
                {
                    "first_name": string("Visitor's first name."),
                    "last_name": string("Visitor's last name. Required."),
                    "phone": string("Visitor's phone number. Required if no email is given."),
                    "email": string("Visitor's email. Required if no phone is given."),
                    "staff_id": string("Doctor id from list_doctors."),
                    "scheduled_at": string("Chosen start time as YYYY-MM-DDTHH:MM."),
                    "service_id": string(
                        "Service id from list_services. Required — it sets how long the "
                        "appointment is booked for. Ask the visitor to pick one first."
                    ),
                    "reason": string("The reason code from list_reasons, if one was chosen."),
                    "notes": string("Anything else the visitor wants the clinic to know. May be empty."),
                },
                ["first_name", "last_name", "staff_id", "scheduled_at", "service_id"],
            ),
            fn=propose_booking,
            kind="proposal",
        ),
    ),
)
