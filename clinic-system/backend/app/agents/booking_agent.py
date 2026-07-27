"""Booking Agent — the ONLY agent an unauthenticated visitor ever talks to.

Every other agent in this system is reached from `POST /agent/run`, which
requires a staff JWT. This one is reached from `POST /public/booking/chat`,
which requires nothing — anyone with the URL can start a run. That changes what
"safe tool set" means, and this module is written to that stricter bar:

- **No patient-lookup tool exists here**, in any form — no `find_patient`,
  no `search_patients`, no "list this person's appointments". The staff agents
  have those because a receptionist asking about a patient is legitimate; an
  anonymous visitor asking "look up patient Krasniqi" is exactly the attack
  this system must not enable. If a caller wants to find out whether someone
  is a patient here, this agent has no way to tell them.
- **No `handoff_tool`.** This is a leaf agent by construction — it cannot reach
  `patient_agent` or `appointment_agent`, which do have lookup tools. A handoff
  path in would be a lookup tool by another name.
- **Nothing here writes, same invariant as every other agent.** `propose_booking`
  is a `kind="proposal"` tool: it validates deterministically and hands back a
  gate, and never touches `patients` or `appointments`. Unlike the staff
  appointment agent, it cannot even look up a patient to attach the booking to
  — there is no patient yet. Matching or creating one happens in
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

from app.agents.runtime import AgentSpec, ToolSpec, obj, string
from app.core.database import get_db, execute_with_retry
from app.services import triage
from app.services.schedule_service import (
    check_conflict, clinic_today, get_available_slots, parse_when, to_clinic,
)

_STAFF_COLUMNS = "id,full_name,specialty,bio"
MAX_LOOKAHEAD_DAYS = 14
DEFAULT_DURATION_MIN = 30


def tool_list_reasons() -> Dict[str, Any]:
    return {"reasons": triage.list_reasons()}


def tool_list_doctors(specialty: str) -> Dict[str, Any]:
    """Active doctors, optionally filtered to one specialty.

    `ilike` with no wildcard is a case-insensitive equality check, which is
    all that is needed here since `specialty` is expected to be one of the
    values `find_specialty_for_reason` returns — but if a model passes
    something that matches nothing, fall back to the full active list rather
    than a dead end.
    """
    db = get_db()
    base = db.table("staff").select(_STAFF_COLUMNS).eq("active", True)
    specialty = (specialty or "").strip()
    if specialty:
        rows = execute_with_retry(base.ilike("specialty", specialty)).data
        if rows:
            return {"doctors": rows, "specialty": specialty}
    rows = execute_with_retry(db.table("staff").select(_STAFF_COLUMNS).eq("active", True)).data
    return {"doctors": rows, "specialty": specialty or None}


def tool_specialty_for_reason(reason: str) -> Dict[str, Any]:
    """Map a reason code from `list_reasons` to a specialty that has an active doctor."""
    return {"reason": reason, "specialty": triage.specialty_for(reason)}


def tool_list_available_slots(staff_id: str, date: str) -> Dict[str, Any]:
    try:
        dt = parse_when(date)
    except ValueError as exc:
        return {"error": f"Could not read '{date}' as a date: {exc}"}
    slots = get_available_slots(staff_id, dt, DEFAULT_DURATION_MIN)
    return {"staff_id": staff_id, "date": date, "slots": slots[:10]}


def tool_find_earliest_slot(specialty: str, from_date: str = "") -> Dict[str, Any]:
    """The soonest open slot with any active doctor in `specialty`, scanning ahead.

    Powers "what's the earliest you have" without the model having to call
    `list_available_slots` once per doctor per day itself.
    """
    doctors = tool_list_doctors(specialty)["doctors"]
    if not doctors:
        return {"found": False, "error": f"No active doctors for '{specialty}'."}

    try:
        start_day = parse_when(from_date).date() if from_date else clinic_today()
    except ValueError as exc:
        return {"found": False, "error": f"Could not read '{from_date}' as a date: {exc}"}

    for offset in range(MAX_LOOKAHEAD_DAYS):
        day = start_day + timedelta(days=offset)
        day_dt = datetime.combine(day, datetime.min.time())
        for doc in doctors:
            slots = get_available_slots(doc["id"], day_dt, DEFAULT_DURATION_MIN)
            if slots:
                return {
                    "found": True,
                    "staff_id": doc["id"],
                    "staff_name": doc["full_name"],
                    "date": day.isoformat(),
                    "slot": slots[0],
                }
    return {"found": False, "error": f"Nothing open in the next {MAX_LOOKAHEAD_DAYS} days."}


def tool_list_services() -> Dict[str, Any]:
    rows = execute_with_retry(
        get_db().table("services").select("id,name,duration_minutes,description")
    ).data
    return {"services": rows}


# ---------------------------------------------------------------- proposal
#
# The only write-shaped tool this agent has, and it does not write. It re-checks
# the doctor and the slot from the database — never trusts the model's claim
# that a slot is free — and it deliberately does NOT look up or create a
# patient: that only happens after the human (the visitor) confirms, via
# `public_orchestrator.resume_public_booking`.


def _load_active_staff(staff_id: str) -> Optional[dict]:
    return execute_with_retry(
        get_db().table("staff").select(_STAFF_COLUMNS).eq("id", staff_id).eq("active", True).maybe_single()
    ).data


def _slot_is_workable(staff_id: str, when: datetime, duration_min: int) -> Dict[str, Any]:
    """Same two checks `appointment_agent._slot_is_workable` runs, kept local:

    a slot with no conflict is not the same as a slot the doctor actually
    works — `check_conflict` alone passes cleanly for a Sunday nobody staffs.
    """
    conflict = check_conflict(staff_id, when, duration_min)
    if conflict:
        return {"ok": False, "error": "That time is already booked."}
    slots = get_available_slots(staff_id, when, duration_min)
    open_instants = {datetime.fromisoformat(s) for s in slots}
    if to_clinic(when) not in open_instants:
        return {"ok": False, "error": "The doctor does not work that slot.", "open_slots": slots[:8]}
    return {"ok": True}


def propose_booking(
    first_name: str, last_name: str, staff_id: str, scheduled_at: str,
    phone: str = "", email: str = "", reason: str = "", notes: str = "",
) -> Dict[str, Any]:
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip()

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

    check = _slot_is_workable(staff["id"], when, DEFAULT_DURATION_MIN)
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
            "scheduled_at": when.isoformat(),
            "duration_min": DEFAULT_DURATION_MIN,
            "reason": reason or "",
            "notes": notes or "",
        },
        "description": (
            f"Booking request for {patient_name} with {staff['full_name']} "
            f"on {when.strftime('%Y-%m-%d %H:%M')} — pending clinic confirmation"
        ),
    }


BOOKING_INSTRUCTIONS = """
You are the appointment booking assistant on the clinic's public website. The
person you are talking to is a visitor, not clinic staff — you have never seen
them before and you cannot look up whether they already have a record here.

How to work, in order:
1. Ask why they want to come in, and offer `list_reasons` as choices rather
   than asking them to describe symptoms. Never ask what is wrong with them.
2. Use `find_specialty_for_reason` to map their answer to a specialty, then
   `list_doctors` to show who is available in it. If they already know which
   doctor they want, that is fine too.
3. Find them a time: `list_available_slots` for a specific day they name, or
   `find_earliest_slot` if they just want the soonest opening.
4. Once they have picked a doctor and a slot, collect their first name, last
   name, and a phone number or email (at least one contact method is
   required — say so if they give neither).
5. Call `propose_booking`. This does NOT book anything: it submits a request
   the clinic still has to confirm. Say exactly that — never tell them the
   appointment is booked or confirmed.
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
""".strip()

BOOKING_AGENT = AgentSpec(
    name="booking_agent",
    purpose="Public-facing: helps a visitor pick a reason for visit, a doctor and a time, and submits a booking request for clinic confirmation.",
    instructions=BOOKING_INSTRUCTIONS,
    tools=(
        ToolSpec(
            name="list_reasons",
            description="The fixed list of administrative reasons a visitor can choose from, each with its specialty.",
            parameters=obj({}),
            fn=tool_list_reasons,
        ),
        ToolSpec(
            name="find_specialty_for_reason",
            description="Map a reason code from list_reasons to the specialty that handles it.",
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
            description="Open start times for one doctor on one day.",
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
            description="The soonest open slot with any active doctor in a specialty. Use when the visitor just wants 'the soonest available'.",
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
            description="The clinic's service catalogue, if the visitor asks what's offered.",
            parameters=obj({}),
            fn=tool_list_services,
        ),
        ToolSpec(
            name="propose_booking",
            description=(
                "Submit a booking request for clinic confirmation. This does NOT create an "
                "appointment — it opens a request the visitor must confirm and the clinic must "
                "still accept. Refuses a doctor id that doesn't exist or a slot that is unavailable."
            ),
            parameters=obj(
                {
                    "first_name": string("Visitor's first name."),
                    "last_name": string("Visitor's last name. Required."),
                    "phone": string("Visitor's phone number. Required if no email is given."),
                    "email": string("Visitor's email. Required if no phone is given."),
                    "staff_id": string("Doctor id from list_doctors."),
                    "scheduled_at": string("Chosen start time as YYYY-MM-DDTHH:MM."),
                    "reason": string("The reason code from list_reasons, if one was chosen."),
                    "notes": string("Anything else the visitor wants the clinic to know. May be empty."),
                },
                ["first_name", "last_name", "staff_id", "scheduled_at"],
            ),
            fn=propose_booking,
            kind="proposal",
        ),
    ),
)
