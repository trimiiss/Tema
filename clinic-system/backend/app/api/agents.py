import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.core.auth import require_roles
from app.core.database import get_db, execute_with_retry
from app.core.audit import log_action
from app.core.events import subscribe, unsubscribe, publish
from app.core.tasks import spawn
from app.models.schemas import AgentRunRequest, AgentRunOut, ApprovalDecision

router = APIRouter(prefix="/agent", tags=["agents"])

# How long to wait for a new event before sending a keep-alive comment,
# so intermediate proxies don't time out the connection while a run is
# sitting at "awaiting_approval" for an arbitrary amount of human time.
_SSE_KEEPALIVE_SECONDS = 15


@router.post("/run", response_model=AgentRunOut, status_code=201)
def start_agent_run(
    body: AgentRunRequest,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    run = execute_with_retry(db.table("agent_runs").insert({
        "user_id": user["id"],
        "input_text": body.input_text,
        "session_id": body.session_id or None,
        "status": "running",
    })).data[0]

    run_id = run["id"]

    from app.agents.orchestrator import run_orchestrator
    spawn(run_orchestrator(run_id, body.input_text, user["id"], body.session_id),
          name=f"agent-run:{run_id}")

    return {**run, "steps": [], "gates": []}


@router.get("/runs", response_model=list[AgentRunOut])
def list_runs(
    since: str = "",
    session_id: str = "",
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    """This user's agent runs, newest first.

    `since` (an ISO instant) narrows them to one sitting. The dashboard's
    activity widget passes the moment the current login began, because a run
    from three days ago reads as "just happened" otherwise.

    `session_id` narrows them to one chat conversation — the chat page passes
    it so switching to a new conversation ("New Chat") doesn't resurrect the
    previous one's transcript. Omitted, the full recent list (bounded by
    `since` if that's given) comes back.
    """
    db = get_db()
    q = db.table("agent_runs").select("*").eq("user_id", user["id"])
    if since:
        q = q.gte("created_at", since)
    if session_id:
        q = q.eq("session_id", session_id)
    runs = execute_with_retry(q.order("created_at", desc=True).limit(20)).data
    if not runs:
        return []

    # Three queries regardless of how many runs came back, rather than 1 + 2N.
    # At the `limit(20)` above that was 41 sequential round-trips to a remote
    # Postgres, which dominated the page load and was also the shape most
    # likely to meet a GOAWAY partway through.
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


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_run(
    run_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    run = execute_with_retry(db.table("agent_runs").select("*").eq("id", run_id).maybe_single())
    if not run.data:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = execute_with_retry(db.table("agent_steps").select("*").eq("run_id", run_id).order("timestamp")).data
    gates = execute_with_retry(db.table("approval_gates").select("*").eq("run_id", run_id)).data
    return {**run.data, "steps": steps, "gates": gates}


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    db = get_db()
    known = execute_with_retry(db.table("agent_runs").select("*").eq("id", run_id).maybe_single())
    if not known.data:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_gen():
        q = subscribe(run_id)
        try:
        # Subscribe *before* reading the run row, not after. Between a read and
        # a subscription the run can finish, and the `status`/`done` events it
        # published in that window reach nobody: the client is left holding a
        # snapshot that says "running" with no stream left to correct it, and a
        # finished answer sits on screen as "Processing…" forever. Reading the
        # row afterwards means the snapshot can never be older than the
        # subscription. A step caught by both arrives twice, and the client
        # drops the duplicate by id.
            fresh = execute_with_retry(db.table("agent_runs").select("*").eq("id", run_id).maybe_single())
            run = fresh.data or known.data
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


@router.post("/approve/{gate_id}")
def decide_gate(
    gate_id: str,
    body: ApprovalDecision,
    user: dict = Depends(require_roles("admin", "receptionist")),
):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be 'approved' or 'rejected'")

    db = get_db()
    gate = execute_with_retry(db.table("approval_gates").select("*").eq("id", gate_id).maybe_single())
    if not gate.data:
        raise HTTPException(status_code=404, detail="Approval gate not found")
    if gate.data["status"] != "pending":
        raise HTTPException(status_code=409, detail="Gate already decided")

    execute_with_retry(db.table("approval_gates").update({
        "status": body.decision,
        "decided_by": user["id"],
        "decided_at": "now()",
    }).eq("id", gate_id))

    log_action(user["id"], body.decision, "approval_gate", gate_id)
    publish(gate.data["run_id"], {"type": "gate", "gate": {**gate.data, "status": body.decision, "decided_by": user["id"]}})

    # Resume the LangGraph run
    from app.agents.orchestrator import resume_orchestrator
    spawn(resume_orchestrator(gate.data["run_id"], gate_id, body.decision, user["id"]),
          name=f"gate-resume:{gate_id}")

    return {"gate_id": gate_id, "decision": body.decision}
