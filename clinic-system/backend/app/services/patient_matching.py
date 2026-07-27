"""Guest-booking patient matching — deterministic, and never a model tool.

The public booking chat has no login, so there is no `patient_id` to hand it
and no way to prove the person typing is who they say they are. That is an
accepted trade-off of guest booking (see CLAUDE.md's "human approval gate on
every write" invariant, extended here with a second gate: `appointments.status`
lands as `proposed`, not `confirmed`, so staff have the final say regardless of
who claims the booking).

What this module is responsible for is narrower and non-negotiable: it keeps
one real person from silently accumulating duplicate `patients` rows every time
they book again. It runs *after* a booking is approved, from
`public_orchestrator.resume_public_booking` only — never as a tool the model can
call. A model with a "find or create a patient by contact details" tool is a
model an anonymous caller can use to fish for whether a given phone number or
email already has a file, one guess at a time. Keeping this deterministic and
gate-side closes that off entirely.
"""
from __future__ import annotations

import re
from typing import Optional

from app.agents.patient_agent import _next_patient_code
from app.core.database import get_db, execute_with_retry

_PATIENT_COLUMNS = "id,code,first_name,last_name,phone,email"


def _normalize_phone(phone: str) -> str:
    """Digits only, so '+383 44 100001' and '+383-44-100001' compare equal.

    This is punctuation-insensitive, not international-prefix-aware: a local
    '038 44 100001' and its international form '+383 44 100001' differ by the
    leading trunk digit vs. the country code and will NOT match here. Folding
    that in would mean guessing which prefix convention a raw string uses,
    and guessing wrong merges two different people's history under one
    record — worse than the duplicate this function exists to avoid. An
    unmatched guest simply gets a new patient row, which staff can merge by
    hand if needed.
    """
    return re.sub(r"\D", "", phone or "")


def _find_by_email(email: str) -> Optional[dict]:
    email = (email or "").strip()
    if not email:
        return None
    rows = execute_with_retry(
        get_db().table("patients").select(_PATIENT_COLUMNS).ilike("email", email)
    ).data
    return rows[0] if rows else None


def _find_by_phone(phone: str) -> Optional[dict]:
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    # Phones are stored as typed, so match by digits-only comparison rather
    # than trusting the stored formatting to be consistent.
    rows = execute_with_retry(get_db().table("patients").select(_PATIENT_COLUMNS)).data or []
    for row in rows:
        if _normalize_phone(row.get("phone") or "") == normalized:
            return row
    return None


def match_or_create_patient(
    first_name: str, last_name: str, phone: str = "", email: str = "",
) -> dict:
    """Reuse an existing patient by email or phone, or create a new record.

    Order matters: email is the more deliberately-typed, less error-prone
    identifier, so it is tried first. A match on either is treated as "this is
    the same person" without comparing names — a patient who books under a
    nickname or a married name should still land on their one existing record
    rather than a second one keyed by whichever name they typed that day.
    """
    existing = _find_by_email(email) or _find_by_phone(phone)
    if existing:
        return {"patient": existing, "created": False}

    db = get_db()
    data = {
        "code": _next_patient_code(),
        "first_name": (first_name or "").strip(),
        "last_name": (last_name or "").strip(),
        "phone": (phone or "").strip() or None,
        "email": (email or "").strip() or None,
        "source": "patient_portal",
        "created_by": None,
    }
    resp = db.table("patients").insert(data).execute()
    return {"patient": resp.data[0], "created": True}
