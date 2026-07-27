"""Reason-for-visit routing — deterministic, and deliberately not clinical.

"Which doctor should I see?" is the question a booking chatbot exists to answer,
and it is one step away from a question this system must never answer. The line
this module draws is that a patient chooses from a fixed list of *administrative*
reasons ("bringing a child in", "following up on a previous visit"), and Python
maps that choice to a specialty. No symptom text is interpreted, by a model or
otherwise, so the clinical refusal in `runtime.SHARED_RULES` stays true rather
than becoming a prompt that a determined user can talk their way around.

The routing table lives here rather than in the agent's instructions for the
same reason the proposal validators live in Python: a prompt is a request, and
which doctor a patient is sent to is not something to request.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.core.database import get_db, execute_with_retry

# Every specialty here must exist in `staff.specialty`. When it doesn't — a
# clinic with no paediatrician — `specialty_for` falls back rather than
# offering a doctor who isn't there.
FALLBACK_SPECIALTY = "General Practice"

REASON_ROUTING: Dict[str, str] = {
    "general_checkup": "General Practice",
    "child_health": "Pediatrics",
    "follow_up": "Internal Medicine",
    "ongoing_condition": "Internal Medicine",
    "document_or_referral": "General Practice",
    "not_sure": FALLBACK_SPECIALTY,
}

# What the patient actually reads. Administrative descriptions only — none of
# these ask the patient to describe a symptom.
REASON_LABELS: Dict[str, str] = {
    "general_checkup": "General check-up or routine visit",
    "child_health": "Appointment for a child",
    "follow_up": "Follow-up on a previous visit",
    "ongoing_condition": "Regular review of an ongoing condition",
    "document_or_referral": "Paperwork, a referral or a certificate",
    "not_sure": "I'm not sure — help me choose",
}


def list_reasons() -> List[Dict[str, str]]:
    """The fixed reason list, in the order it should be offered."""
    return [
        {"reason": key, "label": REASON_LABELS[key], "specialty": REASON_ROUTING[key]}
        for key in REASON_ROUTING
    ]


# ------------------------------------------------------------------ emergency
#
# Checked before anything is sent to a model. An emergency is the one case where
# a slower, cleverer answer is a worse answer, and where the right response is
# fixed text rather than whatever the model composes this time.

_EMERGENCY_TERMS = (
    "emergency", "urgent", "ambulance", "999", "112", "911",
    "chest pain", "can't breathe", "cant breathe", "not breathing",
    "difficulty breathing", "trouble breathing", "shortness of breath",
    "heart attack", "stroke", "seizure", "unconscious", "passed out",
    "bleeding", "blood loss", "overdose", "poison", "poisoning",
    "suicidal", "suicide", "kill myself", "harm myself",
    "severe pain", "broken bone", "head injury", "anaphylaxis",
    "allergic reaction", "choking",
)

EMERGENCY_MESSAGE = (
    "This sounds like it may need urgent attention, and online booking is not the "
    "right route for it. Please call the emergency services (112) now, or go to your "
    "nearest emergency department. If it is not an emergency but you need to be seen "
    "today, please phone the clinic directly rather than booking here."
)


def is_emergency(text: str) -> bool:
    """Does this message describe something that must not wait for a booking?

    Substring matching on a fixed list, matched on word boundaries so "urgent"
    fires but "detergent" does not. It over-triggers by design: sending someone
    to the emergency number when they only wanted a check-up costs them one
    extra sentence, and the opposite mistake costs considerably more.
    """
    lowered = (text or "").lower()
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
        for term in _EMERGENCY_TERMS
    )


# ------------------------------------------------------------------ routing

def active_specialties() -> List[str]:
    """Specialties that currently have at least one active doctor."""
    rows = execute_with_retry(
        get_db().table("staff").select("specialty").eq("active", True)
    ).data or []
    seen = []
    for row in rows:
        specialty = (row.get("specialty") or "").strip()
        if specialty and specialty not in seen:
            seen.append(specialty)
    return seen


def specialty_for(reason: Optional[str]) -> str:
    """The specialty a reason routes to, guaranteed to be bookable.

    Two ways the mapped specialty can be wrong: the reason is one the model
    invented, or the clinic has no active doctor with that specialty any more.
    Both fall back to general practice — a patient sent to the wrong-but-real
    doctor gets seen and redirected; a patient sent to a specialty with nobody
    in it gets an empty slot list and no explanation.
    """
    mapped = REASON_ROUTING.get((reason or "").strip().lower())
    available = active_specialties()

    if mapped and mapped in available:
        return mapped
    if FALLBACK_SPECIALTY in available:
        return FALLBACK_SPECIALTY
    # A clinic with no general practice at all: any real specialty beats
    # returning one that will produce zero doctors.
    return available[0] if available else FALLBACK_SPECIALTY
