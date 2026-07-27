"""Staff administration — doctors and receptionists.

Admin-only writes. Creating a staff member optionally provisions a Supabase
Auth login and assigns the matching role via `user_roles`; doctors also get a
`staff` row (they are the bookable resource appointments point at) plus their
weekly working hours in `schedules`.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import require_roles
from app.core.database import get_db, execute_with_retry
from app.core.audit import log_action
from app.models.schemas import (
    ScheduleEntry,
    ScheduleOut,
    ServiceOut,
    StaffCreate,
    StaffOut,
    StaffUpdate,
)

router = APIRouter(prefix="/staff", tags=["staff"])
services_router = APIRouter(prefix="/services", tags=["services"])

ASSIGNABLE_ROLES = ("doctor", "receptionist")


def _role_id(db, role_name: str) -> int:
    resp = execute_with_retry(db.table("roles").select("id").eq("name", role_name).maybe_single())
    if not resp or not resp.data:
        raise HTTPException(status_code=422, detail=f"Unknown role '{role_name}'")
    return resp.data["id"]


def _create_login(db, email: str, password: str, full_name: str, role_name: str) -> str:
    """Provision a Supabase Auth user and grant it `role_name`. Returns user id."""
    try:
        created = db.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name},
        })
    except Exception as e:  # supabase-py raises AuthApiError for duplicates
        raise HTTPException(status_code=409, detail=f"Could not create login: {e}")

    user = getattr(created, "user", None) or created
    user_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    if not user_id:
        raise HTTPException(status_code=502, detail="Auth user created without an id")

    db.table("user_roles").insert(
        {"user_id": user_id, "role_id": _role_id(db, role_name)}
    ).execute()
    return user_id


@router.get("/", response_model=list[StaffOut])
def list_staff(
    include_inactive: bool = False,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    q = db.table("staff").select("*")
    if not include_inactive:
        q = q.eq("active", True)
    resp = execute_with_retry(q.order("full_name"))
    return resp.data


@router.get("/accounts")
def list_accounts(user: dict = Depends(require_roles("admin"))):
    """Every login account with its assigned roles.

    Declared before `/{staff_id}/…` so "accounts" is never read as an id.
    """
    db = get_db()
    resp = execute_with_retry(db.table("user_roles").select("user_id, roles(name)"))
    roles_by_user: dict[str, list[str]] = {}
    for row in resp.data or []:
        role = (row.get("roles") or {}).get("name")
        uid = row.get("user_id")
        if role and uid:
            roles_by_user.setdefault(uid, []).append(role)

    try:
        users = db.auth.admin.list_users()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not list accounts: {e}")

    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": (u.user_metadata or {}).get("full_name"),
            "roles": roles_by_user.get(u.id, []),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/", response_model=StaffOut, status_code=201)
def create_staff(
    body: StaffCreate,
    user: dict = Depends(require_roles("admin")),
):
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=422, detail=f"role must be one of {list(ASSIGNABLE_ROLES)}"
        )
    if bool(body.email) != bool(body.password):
        raise HTTPException(
            status_code=422, detail="email and password must be provided together"
        )
    if body.start_time and body.end_time and body.start_time >= body.end_time:
        raise HTTPException(status_code=422, detail="start_time must be before end_time")

    db = get_db()
    user_id = None
    if body.email and body.password:
        user_id = _create_login(db, body.email, body.password, body.full_name, body.role)

    # Receptionists are accounts only — they are not bookable, so no staff row.
    if body.role != "doctor":
        log_action(user["id"], "create", "staff_account", user_id,
                   {"role": body.role, "email": body.email})
        return StaffOut(id=user_id or "", user_id=user_id, full_name=body.full_name, active=True)

    resp = db.table("staff").insert({
        "user_id": user_id,
        "full_name": body.full_name,
        "specialty": body.specialty,
        "bio": body.bio,
        "active": True,
    }).execute()
    if not resp.data:
        raise HTTPException(status_code=502, detail="Staff record was not created")
    staff = resp.data[0]

    if body.work_days and body.start_time and body.end_time:
        db.table("schedules").insert([
            {
                "staff_id": staff["id"],
                "weekday": d,
                "start_time": body.start_time.isoformat(),
                "end_time": body.end_time.isoformat(),
            }
            for d in sorted(set(body.work_days))
        ]).execute()

    log_action(user["id"], "create", "staff", staff["id"],
               {"role": body.role, "full_name": body.full_name, "has_login": user_id is not None})
    return staff


@router.patch("/{staff_id}", response_model=StaffOut)
def update_staff(
    staff_id: str,
    body: StaffUpdate,
    user: dict = Depends(require_roles("admin")),
):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    db = get_db()
    resp = execute_with_retry(db.table("staff").update(data).eq("id", staff_id))
    if not resp.data:
        raise HTTPException(status_code=404, detail="Staff member not found")
    log_action(user["id"], "update", "staff", staff_id, data)
    return resp.data[0]


@router.delete("/{staff_id}", status_code=204)
def deactivate_staff(
    staff_id: str,
    user: dict = Depends(require_roles("admin")),
):
    """Soft-delete: appointments reference staff, so rows are deactivated."""
    db = get_db()
    resp = execute_with_retry(db.table("staff").update({"active": False}).eq("id", staff_id))
    if not resp.data:
        raise HTTPException(status_code=404, detail="Staff member not found")
    log_action(user["id"], "deactivate", "staff", staff_id)


@router.get("/{staff_id}/schedules", response_model=list[ScheduleOut])
def list_schedules(
    staff_id: str,
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    resp = execute_with_retry(
        db.table("schedules").select("*").eq("staff_id", staff_id).order("weekday")
    )
    return resp.data


@router.put("/{staff_id}/schedules", response_model=list[ScheduleOut])
def replace_schedules(
    staff_id: str,
    entries: list[ScheduleEntry],
    user: dict = Depends(require_roles("admin")),
):
    for e in entries:
        if not 0 <= e.weekday <= 6:
            raise HTTPException(status_code=422, detail="weekday must be 0–6")
        if e.start_time >= e.end_time:
            raise HTTPException(status_code=422, detail="start_time must be before end_time")

    db = get_db()
    db.table("schedules").delete().eq("staff_id", staff_id).execute()
    if not entries:
        log_action(user["id"], "update", "staff_schedule", staff_id, {"entries": 0})
        return []
    resp = db.table("schedules").insert([
        {
            "staff_id": staff_id,
            "weekday": e.weekday,
            "start_time": e.start_time.isoformat(),
            "end_time": e.end_time.isoformat(),
        }
        for e in entries
    ]).execute()
    log_action(user["id"], "update", "staff_schedule", staff_id, {"entries": len(entries)})
    return resp.data


@services_router.get("/", response_model=list[ServiceOut])
def list_services(
    user: dict = Depends(require_roles("admin", "receptionist", "doctor")),
):
    db = get_db()
    resp = execute_with_retry(db.table("services").select("*").order("name"))
    return resp.data
