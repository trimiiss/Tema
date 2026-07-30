"""
Patient Administration Agent — autonomous records agent.

Owns the patient record: lookup, search, completeness checks, and proposing new
or corrected records. Like the appointment agent it reasons for itself over its
own tool set, and like the appointment agent it cannot write — `propose_*`
validates deterministically and opens an approval gate, and the functions that
actually touch `patients` are `@mutating` and never reach the model.
"""
from __future__ import annotations
import re
from datetime import date
from typing import Any, Dict
from app.core.database import get_db, execute_with_retry
from app.agents.runtime import (
    AgentSpec, ToolSpec, handoff_tool, mutating, obj, string,
)

REQUIRED_FIELDS = ["first_name", "last_name", "dob", "phone", "email", "address"]

# Fields a proposal is allowed to set. Anything else the model invents — an id,
# a created_by, a column that does not exist — is dropped before it reaches the
# gate, so an approved gate can never write outside this set. `gender` is
# deliberately absent: the clinic does not record it, and leaving it out here
# means a model cannot write it even though the column still exists.
WRITABLE_FIELDS = {"first_name", "last_name", "dob", "phone", "email", "address", "notes"}


def tool_get_patient(patient_code: str) -> Dict[str, Any]:
    db = get_db()
    resp = execute_with_retry(db.table("patients").select("*").eq("code", patient_code).maybe_single())
    if resp.data:
        return {"found": True, "patient": resp.data}
    return {"found": False, "patient": None}


_SEARCH_COLUMNS = "id,code,first_name,last_name,phone,email"


def tool_search_patients(query: str) -> Dict[str, Any]:
    """Patients matching a free-text query, most specific reading first.

    "Leutrim Istrefi" is contained in neither `first_name` nor `last_name`, so
    an `or_` over the whole string finds nothing for a patient who plainly
    exists. Live, that made the agent report a registered patient as missing and
    move to register them again — only the duplicate check in
    `propose_create_patient` stopped a second record. Try the two-token reading
    first, as `appointment_agent.tool_check_patient` already does.
    """
    q = (query or "").strip()
    if not q:
        return {"results": []}

    db = get_db()
    parts = q.split()
    if len(parts) >= 2:
        # Chained ilike filters AND together: first token against the given
        # name, last against the family name.
        rows = execute_with_retry(
            db.table("patients").select(_SEARCH_COLUMNS)
            .ilike("first_name", f"%{parts[0]}%")
            .ilike("last_name", f"%{parts[-1]}%")
            .limit(10)
        ).data
        if rows:
            return {"results": rows}

    resp = execute_with_retry(
        db.table("patients").select(_SEARCH_COLUMNS)
        .or_(f"first_name.ilike.%{q}%,last_name.ilike.%{q}%,code.ilike.%{q}%")
        .limit(10)
    )
    return {"results": resp.data}


def tool_flag_missing_fields(patient_id: str) -> Dict[str, Any]:
    db = get_db()
    resp = execute_with_retry(db.table("patients").select("*").eq("id", patient_id).maybe_single())
    if not resp.data:
        return {"error": "Patient not found"}
    p = resp.data
    missing = [f for f in REQUIRED_FIELDS if not p.get(f)]
    return {"patient_id": patient_id, "missing_fields": missing, "complete": len(missing) == 0}


# ---------------------------------------------------------------- proposals

def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only real, non-empty patient columns."""
    return {k: v for k, v in (fields or {}).items() if k in WRITABLE_FIELDS and v not in (None, "")}


def _valid_dob(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return date(1900, 1, 1) <= parsed <= date.today()


def _next_patient_code() -> str:
    """The next free Pnnn code.

    Codes are assigned here, never by the model: `patients.code` is NOT NULL
    UNIQUE with no default, and a model asked to pick one will happily reuse an
    existing patient's.
    """
    rows = execute_with_retry(
        get_db().table("patients").select("code").order("code", desc=True).limit(1)
    ).data
    highest = 0
    if rows:
        match = re.fullmatch(r"P(\d+)", (rows[0].get("code") or "").strip())
        if match:
            highest = int(match.group(1))
    return f"P{highest + 1:03d}"


def propose_create_patient(first_name: str, last_name: str, dob: str = "",
                           phone: str = "", email: str = "", address: str = "") -> Dict[str, Any]:
    fields = _clean({
        "first_name": first_name, "last_name": last_name, "dob": dob,
        "phone": phone, "email": email, "address": address,
    })
    if not fields.get("first_name") or not fields.get("last_name"):
        return {"proposed": False, "error": "A first and last name are required to register a patient."}
    if "dob" in fields and not _valid_dob(fields["dob"]):
        return {"proposed": False, "error": f"'{fields['dob']}' is not a valid date of birth (use YYYY-MM-DD)."}

    # A duplicate record is worse than an extra question — surface the match and
    # let the human decide rather than silently creating a second file.
    existing = execute_with_retry(
        get_db().table("patients").select("id,code,first_name,last_name,dob")
        .ilike("first_name", fields["first_name"])
        .ilike("last_name", fields["last_name"])
    ).data
    if existing:
        return {
            "proposed": False,
            "error": "A patient with that name already exists. Confirm with the user before registering another.",
            "existing": existing,
        }

    fields["code"] = _next_patient_code()
    name = f"{fields['first_name']} {fields['last_name']}"
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    return {
        "proposed": True,
        "action": {"action": "create_patient", "params": fields},
        "description": (
            f"Register new patient {name} as {fields['code']}"
            + (f" (born {fields['dob']})" if "dob" in fields else "")
            + (f" — incomplete, still missing: {', '.join(missing)}" if missing else "")
        ),
    }


def propose_update_patient(patient_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    patient = execute_with_retry(
        get_db().table("patients").select("id,code,first_name,last_name").eq("id", patient_id).maybe_single()
    ).data
    if not patient:
        return {"proposed": False, "error": f"No patient with id '{patient_id}'. Look them up first."}

    clean = _clean(fields)
    if not clean:
        rejected = sorted(set(fields or {}) - WRITABLE_FIELDS)
        return {
            "proposed": False,
            "error": "Nothing updatable was given."
                     + (f" These are not patient fields: {rejected}." if rejected else ""),
            "updatable_fields": sorted(WRITABLE_FIELDS),
        }
    if "dob" in clean and not _valid_dob(clean["dob"]):
        return {"proposed": False, "error": f"'{clean['dob']}' is not a valid date of birth (use YYYY-MM-DD)."}

    name = f"{patient['first_name']} {patient['last_name']}"
    changes = ", ".join(f"{k} → {v}" for k, v in clean.items())
    return {
        "proposed": True,
        "action": {"action": "update_patient", "params": {"patient_id": patient_id, "fields": clean}},
        "description": f"Update {name} ({patient['code']}): {changes}",
    }


# ------------------------------------------------------------------- writes
#
# Reachable only from `resume_orchestrator`, after a human approved the gate.


@mutating
def tool_create_patient(data: Dict[str, Any], created_by: str) -> Dict[str, Any]:
    db = get_db()
    data["created_by"] = created_by
    resp = db.table("patients").insert(data).execute()
    return {"patient": resp.data[0]}


@mutating
def tool_update_patient(patient_id: str, fields: Dict[str, Any], updated_by: str) -> Dict[str, Any]:
    db = get_db()
    resp = db.table("patients").update(fields).eq("id", patient_id).execute()
    return {"patient": resp.data[0] if resp.data else None}


PATIENT_INSTRUCTIONS = """
You are the Patient Administration Agent for a clinic. You own the patient
record: finding patients, reporting what is missing from a file, registering new
patients, and correcting details.

How to work:
- Look a patient up before proposing any change; you need their real id, and
  `propose_update_patient` will reject one you made up.
- `search_patients` is for vague queries ("the Krasniqi woman"), `get_patient`
  for an exact code like P001.
- When registering, pass through only what the user actually told you. Do not
  fill in a plausible phone number, address or date of birth — an incomplete
  record is fine and `check_missing_fields` exists to find the gaps later.
- Registering is a proposal, not an action. Say it is awaiting approval.
- You do not schedule anything. Once a patient exists or is proposed, hand any
  booking back to the appointment agent with the details you were given.
- Record only administrative facts. If asked to store a diagnosis, symptoms or
  medication, refuse and say clinical information does not belong in these
  fields.
""".strip()

PATIENT_AGENT = AgentSpec(
    name="patient_agent",
    purpose="Finds patients, reports incomplete records, and proposes new or corrected patient details.",
    instructions=PATIENT_INSTRUCTIONS,
    tools=(
        ToolSpec(
            name="get_patient",
            description="Fetch one patient by their exact code, e.g. P001.",
            parameters=obj({"patient_code": string("Patient code such as P001.")}, ["patient_code"]),
            fn=tool_get_patient,
        ),
        ToolSpec(
            name="search_patients",
            description="Search patients by partial name or code. Use when you do not have an exact code.",
            parameters=obj({"query": string("Part of a name or code.")}, ["query"]),
            fn=tool_search_patients,
        ),
        ToolSpec(
            name="check_missing_fields",
            description="List which required fields are still empty on a patient's record.",
            parameters=obj({"patient_id": string("Patient id (not code).")}, ["patient_id"]),
            fn=tool_flag_missing_fields,
        ),
        ToolSpec(
            name="propose_create_patient",
            description=(
                "Propose registering a new patient. This does NOT create them: it opens an "
                "approval gate for a human. Refuses if a patient of that name already exists."
            ),
            parameters=obj(
                {
                    "first_name": string("Given name."),
                    "last_name": string("Family name."),
                    "dob": string("Date of birth as YYYY-MM-DD, if the user gave one."),
                    "phone": string("If the user gave one."),
                    "email": string("If the user gave one."),
                    "address": string("If the user gave one."),
                },
                ["first_name", "last_name"],
            ),
            fn=propose_create_patient,
            kind="proposal",
        ),
        ToolSpec(
            name="propose_update_patient",
            description="Propose correcting a patient's details. Opens an approval gate; does not change anything.",
            parameters=obj(
                {
                    "patient_id": string("Patient id from a lookup."),
                    "fields": {
                        "type": "object",
                        "description": "Only the fields to change. Allowed: "
                                       "first_name, last_name, dob, phone, email, address, notes.",
                    },
                },
                ["patient_id", "fields"],
            ),
            fn=propose_update_patient,
            kind="proposal",
        ),
        handoff_tool(
            "appointment_agent",
            "Hand off to the appointment agent — anything about booking, cancelling, "
            "rescheduling, doctors' hours or free slots.",
        ),
    ),
)
