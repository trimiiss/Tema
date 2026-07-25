"""
Patient Administration Agent — read-only tools execute freely;
write tools must pass through an approval gate in the orchestrator.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from app.core.database import get_db

REQUIRED_FIELDS = ["first_name", "last_name", "dob", "gender", "phone", "email", "address"]


def tool_get_patient(patient_code: str) -> Dict[str, Any]:
    db = get_db()
    resp = db.table("patients").select("*").eq("code", patient_code).maybe_single().execute()
    if resp.data:
        return {"found": True, "patient": resp.data}
    return {"found": False, "patient": None}


def tool_search_patients(query: str) -> Dict[str, Any]:
    db = get_db()
    resp = (
        db.table("patients")
        .select("id,code,first_name,last_name,phone,email")
        .or_(f"first_name.ilike.%{query}%,last_name.ilike.%{query}%,code.ilike.%{query}%")
        .limit(10)
        .execute()
    )
    return {"results": resp.data}


def tool_flag_missing_fields(patient_id: str) -> Dict[str, Any]:
    db = get_db()
    resp = db.table("patients").select("*").eq("id", patient_id).maybe_single().execute()
    if not resp.data:
        return {"error": "Patient not found"}
    p = resp.data
    missing = [f for f in REQUIRED_FIELDS if not p.get(f)]
    return {"patient_id": patient_id, "missing_fields": missing, "complete": len(missing) == 0}


def tool_create_patient(data: Dict[str, Any], created_by: str) -> Dict[str, Any]:
    db = get_db()
    data["created_by"] = created_by
    resp = db.table("patients").insert(data).execute()
    return {"patient": resp.data[0]}


def tool_update_patient(patient_id: str, fields: Dict[str, Any], updated_by: str) -> Dict[str, Any]:
    db = get_db()
    resp = db.table("patients").update(fields).eq("id", patient_id).execute()
    return {"patient": resp.data[0] if resp.data else None}


PATIENT_TOOLS = {
    "get_patient": tool_get_patient,
    "search_patients": tool_search_patients,
    "flag_missing_fields": tool_flag_missing_fields,
    "create_patient": tool_create_patient,
    "update_patient": tool_update_patient,
}
