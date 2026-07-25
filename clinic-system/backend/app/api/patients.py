from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import get_current_user_roles, require_roles
from app.core.database import get_db, execute_with_retry
from app.core.audit import log_action
from app.models.schemas import PatientCreate, PatientUpdate, PatientOut

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("/", response_model=list[PatientOut])
async def list_patients(
    search: str = "",
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    q = db.table("patients").select("*")
    if search:
        q = q.or_(f"first_name.ilike.%{search}%,last_name.ilike.%{search}%,code.ilike.%{search}%")
    resp = q.order("last_name").execute()
    return resp.data


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: str,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    resp = execute_with_retry(db.table("patients").select("*").eq("id", patient_id).maybe_single())
    if not resp.data:
        raise HTTPException(status_code=404, detail="Patient not found")
    return resp.data


@router.post("/", response_model=PatientOut, status_code=201)
async def create_patient(
    body: PatientCreate,
    user: dict = Depends(require_roles("receptionist")),
):
    """Receptionist-only: front-desk staff own the patient register.

    Admins manage staff and accounts but do not edit patient records; doctors
    are read-only. Keep this in sync with `update_patient`.
    """
    db = get_db()
    # mode="json" renders `dob` as an ISO date string; postgrest JSON-encodes the
    # payload and cannot serialize a datetime.date.
    data = body.model_dump(mode="json", exclude_none=True)
    data["created_by"] = user["id"]
    resp = db.table("patients").insert(data).execute()
    log_action(user["id"], "create", "patient", resp.data[0]["id"], {"code": body.code})
    return resp.data[0]


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: str,
    body: PatientUpdate,
    user: dict = Depends(require_roles("receptionist")),
):
    """Receptionist-only — see `create_patient`."""
    db = get_db()
    data = body.model_dump(mode="json", exclude_none=True)  # see `create_patient`
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    data["updated_at"] = "now()"
    resp = db.table("patients").update(data).eq("id", patient_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Patient not found")
    log_action(user["id"], "update", "patient", patient_id, data)
    return resp.data[0]


@router.get("/{patient_id}/missing-fields")
async def missing_fields(
    patient_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    resp = execute_with_retry(db.table("patients").select("*").eq("id", patient_id).maybe_single())
    if not resp.data:
        raise HTTPException(status_code=404, detail="Patient not found")
    p = resp.data
    required = ["first_name", "last_name", "dob", "gender", "phone", "email", "address"]
    missing = [f for f in required if not p.get(f)]
    return {"patient_id": patient_id, "missing_fields": missing}
