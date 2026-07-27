"""Public booking API — the ONLY router in this app with no `require_roles`.

Every other router assumes a Supabase JWT already separated staff from
strangers. This one can't: a visitor books before they have any account at
all. Two things stand in for that missing auth layer, and both matter more
here than anywhere else in the codebase:

- `session_id` (a UUID the browser mints and keeps in localStorage) is the
  only thing distinguishing one visitor's runs and gates from another's.
  Every read or decide on a `run_id`/`gate_id` below re-checks it against
  `agent_runs.session_id` and returns a plain 404 on mismatch — the same
  status as "doesn't exist" — so this can't be used to enumerate other
  people's booking attempts by guessing ids.
- `enforce_rate_limit` guards the two endpoints that spend an OpenAI call or
  write to the database, since nothing else here costs an attacker anything
  to hit repeatedly.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.database import get_db, execute_with_retry
from app.core.events import publish, subscribe, unsubscribe
from app.core.audit import log_action
from app.core.ratelimit import enforce_rate_limit
from app.core.tasks import spawn
from app.models.schemas import (
    AgentRunOut, PublicChatRequest, PublicConfirmRequest,
    PublicDoctorOut, PublicReasonOut, PublicSlotsOut,
)
from app.services import triage
from app.services.schedule_service import get_available_slots, parse_when

router = APIRouter(prefix="/public", tags=["public"])

_SSE_KEEPALIVE_SECONDS = 15


def _require_session_id(session_id: str) -> str:
    session_id = (session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    return session_id


# ---- Read-only reference data — no session needed ----

@router.get("/doctors", response_model=list[PublicDoctorOut])
def list_doctors():
    db = get_db()
    return execute_with_retry(
        db.table("staff").select("id,full_name,specialty,bio").eq("active", True)
    ).data


@router.get("/reasons", response_model=list[PublicReasonOut])
def list_reasons():
    return triage.list_reasons()


@router.get("/slots", response_model=PublicSlotsOut)
def slots(staff_id: str, date: str, duration_min: int = 30):
    """Deterministic fallback slot picker for the UI's own doctor/day pickers,
    independent of anything the chat has said this turn."""
    try:
        dt = parse_when(date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"staff_id": staff_id, "date": date, "slots": get_available_slots(staff_id, dt, duration_min)}


# ---- Booking chat — session-scoped ----

@router.post("/booking/chat", response_model=AgentRunOut, status_code=201,
             dependencies=[Depends(enforce_rate_limit)])
def start_chat(body: PublicChatRequest):
    session_id = _require_session_id(body.session_id)
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    db = get_db()
    run = execute_with_retry(db.table("agent_runs").insert({
        "user_id": None,
        "session_id": session_id,
        "channel": "patient",
        "input_text": message,
        "status": "running",
    })).data[0]
    run_id = run["id"]

    from app.agents.public_orchestrator import run_booking_agent
    spawn(run_booking_agent(run_id, session_id, message), name=f"booking-run:{run_id}")

    return {**run, "steps": [], "gates": []}


@router.get("/booking/runs", response_model=list[AgentRunOut])
def list_runs(session_id: str = Query(...)):
    session_id = _require_session_id(session_id)
    db = get_db()
    runs = execute_with_retry(
        db.table("agent_runs").select("*").eq("session_id", session_id)
        .order("created_at", desc=True).limit(20)
    ).data
    if not runs:
        return []

    # Three queries rather than 1 + 2N — same reason as `agents.list_runs`, and
    # it matters more here: this is the first request a visitor's browser makes
    # on the public booking page.
    run_ids = [run["id"] for run in runs]
    steps = execute_with_retry(
        db.table("agent_steps").select("*").in_("run_id", run_ids).order("timestamp")
    ).data or []
    gates = execute_with_retry(
        db.table("approval_gates").select("*").in_("run_id", run_ids)
    ).data or []

    steps_by_run: dict[str, list] = {}
    for step in steps:
        steps_by_run.setdefault(step["run_id"], []).append(step)
    gates_by_run: dict[str, list] = {}
    for gate in gates:
        gates_by_run.setdefault(gate["run_id"], []).append(gate)

    return [
        {**run, "steps": steps_by_run.get(run["id"], []), "gates": gates_by_run.get(run["id"], [])}
        for run in runs
    ]


def _load_own_run(db, run_id: str, session_id: str) -> dict:
    """A run, only if it belongs to `session_id`. Same 404 either way, so a
    guessed id and someone else's real id are indistinguishable from outside."""
    run = execute_with_retry(db.table("agent_runs").select("*").eq("id", run_id).maybe_single())
    if not run.data or run.data.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.data


@router.get("/booking/runs/{run_id}/stream")
async def stream_run(run_id: str, session_id: str = Query(...)):
    session_id = _require_session_id(session_id)
    db = get_db()
    run = _load_own_run(db, run_id, session_id)

    async def event_gen():
        q = subscribe(run_id)
        try:
            steps = execute_with_retry(db.table("agent_steps").select("*").eq("run_id", run_id).order("timestamp")).data
            gates = execute_with_retry(db.table("approval_gates").select("*").eq("run_id", run_id)).data
            snapshot = {"type": "snapshot", "run": run, "steps": steps, "gates": gates}
            yield f"data: {json.dumps(snapshot)}\n\n"

            if run["status"] in ("completed", "failed"):
                return

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
        finally:
            unsubscribe(run_id, q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/booking/confirm/{gate_id}", dependencies=[Depends(enforce_rate_limit)])
def confirm_booking(gate_id: str, body: PublicConfirmRequest):
    session_id = _require_session_id(body.session_id)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be 'approved' or 'rejected'")

    db = get_db()
    gate = execute_with_retry(db.table("approval_gates").select("*").eq("id", gate_id).maybe_single())
    if not gate.data:
        raise HTTPException(status_code=404, detail="Approval gate not found")

    # Ownership check: the gate's run must belong to this session. Same 404 as
    # "gate doesn't exist" — deciding someone else's booking must not even be
    # distinguishable as a permissions error from outside.
    _load_own_run(db, gate.data["run_id"], session_id)

    if gate.data["status"] != "pending":
        raise HTTPException(status_code=409, detail="This request was already decided")

    execute_with_retry(db.table("approval_gates").update({
        "status": body.decision,
        "decided_by": None,
        "decided_at": "now()",
    }).eq("id", gate_id))

    log_action(None, body.decision, "approval_gate", gate_id, {"channel": "patient"})
    publish(gate.data["run_id"], {"type": "gate", "gate": {**gate.data, "status": body.decision}})

    from app.agents.public_orchestrator import resume_public_booking
    spawn(resume_public_booking(gate.data["run_id"], gate_id, body.decision),
          name=f"booking-confirm:{gate_id}")

    return {"gate_id": gate_id, "decision": body.decision}
