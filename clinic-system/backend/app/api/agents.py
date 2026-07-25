import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.core.auth import require_roles
from app.core.database import get_db
from app.core.audit import log_action
from app.core.events import subscribe, unsubscribe, publish
from app.models.schemas import AgentRunRequest, AgentRunOut, ApprovalDecision

router = APIRouter(prefix="/agent", tags=["agents"])

# How long to wait for a new event before sending a keep-alive comment,
# so intermediate proxies don't time out the connection while a run is
# sitting at "awaiting_approval" for an arbitrary amount of human time.
_SSE_KEEPALIVE_SECONDS = 15


@router.post("/run", response_model=AgentRunOut, status_code=201)
async def start_agent_run(
    body: AgentRunRequest,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    run = db.table("agent_runs").insert({
        "user_id": user["id"],
        "input_text": body.input_text,
        "status": "running",
    }).execute().data[0]

    run_id = run["id"]

    import asyncio
    from app.agents.orchestrator import run_orchestrator
    asyncio.create_task(run_orchestrator(run_id, body.input_text, user["id"]))

    return {**run, "steps": [], "gates": []}


@router.get("/runs", response_model=list[AgentRunOut])
async def list_runs(
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    runs = db.table("agent_runs").select("*").eq("user_id", user["id"]).order("created_at", desc=True).limit(20).execute().data
    result = []
    for run in runs:
        steps = db.table("agent_steps").select("*").eq("run_id", run["id"]).order("timestamp").execute().data
        gates = db.table("approval_gates").select("*").eq("run_id", run["id"]).execute().data
        result.append({**run, "steps": steps, "gates": gates})
    return result


@router.get("/runs/{run_id}", response_model=AgentRunOut)
async def get_run(
    run_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    run = db.table("agent_runs").select("*").eq("id", run_id).maybe_single().execute()
    if not run.data:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = db.table("agent_steps").select("*").eq("run_id", run_id).order("timestamp").execute().data
    gates = db.table("approval_gates").select("*").eq("run_id", run_id).execute().data
    return {**run.data, "steps": steps, "gates": gates}


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    run = db.table("agent_runs").select("*").eq("id", run_id).maybe_single().execute()
    if not run.data:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_gen():
        q = subscribe(run_id)
        try:
            steps = db.table("agent_steps").select("*").eq("run_id", run_id).order("timestamp").execute().data
            gates = db.table("approval_gates").select("*").eq("run_id", run_id).execute().data
            snapshot = {"type": "snapshot", "run": run.data, "steps": steps, "gates": gates}
            yield f"data: {json.dumps(snapshot)}\n\n"

            if run.data["status"] in ("completed", "failed"):
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


@router.post("/approve/{gate_id}")
async def decide_gate(
    gate_id: str,
    body: ApprovalDecision,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be 'approved' or 'rejected'")

    db = get_db()
    gate = db.table("approval_gates").select("*").eq("id", gate_id).maybe_single().execute()
    if not gate.data:
        raise HTTPException(status_code=404, detail="Approval gate not found")
    if gate.data["status"] != "pending":
        raise HTTPException(status_code=409, detail="Gate already decided")

    db.table("approval_gates").update({
        "status": body.decision,
        "decided_by": user["id"],
        "decided_at": "now()",
    }).eq("id", gate_id).execute()

    log_action(user["id"], body.decision, "approval_gate", gate_id)
    publish(gate.data["run_id"], {"type": "gate", "gate": {**gate.data, "status": body.decision, "decided_by": user["id"]}})

    # Resume the LangGraph run
    from app.agents.orchestrator import resume_orchestrator
    asyncio.create_task(resume_orchestrator(gate.data["run_id"], gate_id, body.decision, user["id"]))

    return {"gate_id": gate_id, "decision": body.decision}
