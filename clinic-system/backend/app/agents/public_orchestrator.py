"""Runner for the public booking chat — one agent, no supervisor graph.

`orchestrator.py` runs a cyclic LangGraph because a staff request can traverse
several agents (register a patient, then book them). The public booking chat
never needs that: `booking_agent.BOOKING_AGENT` has no handoff tool and nothing
to hand off to, so a graph here would add machinery without adding capability
— and every extra node is an extra thing to prove safe for an endpoint that
takes anonymous input. A plain async function is the whole orchestrator.

The two entry points mirror `run_orchestrator` / `resume_orchestrator`
deliberately, so the SSE event vocabulary (`snapshot`/`step`/`gate`/`status`/
`done`) and `agent_runs`/`agent_steps`/`approval_gates` shape are identical and
the existing frontend SSE reader (`lib/api.ts`'s frame parser) works unchanged
against the public endpoints.

`resume_public_booking` is the only function in this module — and the only one
anywhere reachable from the public API — that writes to `patients` or
`appointments`. It runs only after the visitor themselves confirms the gate,
and the appointment it creates lands as `status="confirmed"`: a visitor who
picked a slot the validators already checked is booked, not queued. The slot
was re-derived from the database at proposal time, so nothing here rests on
what the model claimed. `source="patient_portal"` is still recorded, so staff
can always tell a self-service booking apart from one they entered.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.agents.booking_agent import BOOKING_AGENT
from app.agents.runtime import run_agent_loop
from app.core.audit import log_action
from app.core.database import get_db, execute_with_retry
from app.core.events import publish
from app.services import triage
from app.services.patient_matching import match_or_create_patient
from app.services.schedule_service import clinic_today, to_clinic

# How many earlier runs from this session to replay as conversation history.
# Bounded for the same reason `runtime.MAX_TOOL_RESULT_CHARS` is bounded: an
# unbounded transcript eventually pushes the system prompt out of the useful
# part of the context window, and a booking conversation resolves in a handful
# of turns or it should be restarted.
MAX_HISTORY_RUNS = 8


def _log_step(run_id: str, action: str, inp: Any, out: Any) -> str:
    db = get_db()
    resp = db.table("agent_steps").insert({
        "run_id": run_id,
        "agent_name": "booking_agent",
        "action": action,
        "input": inp if isinstance(inp, dict) else {"value": str(inp)},
        "output": out if isinstance(out, dict) else {"value": str(out)},
    }).execute()
    row = resp.data[0]
    publish(run_id, {"type": "step", "step": row})
    return row["id"]


def _update_run(run_id: str, **fields) -> None:
    db = get_db()
    db.table("agent_runs").update(fields).eq("id", run_id).execute()
    publish(run_id, {"type": "status", **fields})
    if fields.get("status") in ("completed", "failed"):
        publish(run_id, {"type": "done"})


def _create_gate(run_id: str, step_id: str, description: str, payload: dict) -> str:
    db = get_db()
    resp = db.table("approval_gates").insert({
        "run_id": run_id,
        "step_id": step_id,
        "action_description": description,
        "payload": payload,
        "status": "pending",
    }).execute()
    row = resp.data[0]
    publish(run_id, {"type": "gate", "gate": row})
    return row["id"]


def _get_gate(gate_id: str) -> Optional[dict]:
    resp = execute_with_retry(
        get_db().table("approval_gates").select("*").eq("id", gate_id).maybe_single()
    )
    return resp.data


def _history_for_session(session_id: str, exclude_run_id: str) -> List[Dict[str, str]]:
    """Earlier turns of this session's conversation, oldest first.

    Only completed runs with a stored answer contribute — a run still
    `awaiting_approval` or `failed` has nothing meaningful to replay, and this
    is exactly the same content `messagesFromRuns` on the staff chat page
    already reconstructs client-side, just read here for the model instead.
    """
    rows = execute_with_retry(
        get_db().table("agent_runs").select("id,input_text,result,status")
        .eq("session_id", session_id)
        .neq("id", exclude_run_id)
        .order("created_at", desc=True)
        .limit(MAX_HISTORY_RUNS)
    ).data or []

    turns: List[Dict[str, str]] = []
    for run in reversed(rows):  # oldest first
        if run.get("status") != "completed":
            continue
        message = (run.get("result") or {}).get("message")
        if not message:
            continue
        turns.append({"role": "user", "content": run["input_text"]})
        turns.append({"role": "assistant", "content": message})
    return turns


def _facts_for_session(session_id: str, exclude_run_id: str) -> str:
    """Ids this conversation has already established, as a context line.

    Each visitor message starts a *fresh* `run_agent_loop`, so the tool results
    of earlier turns are gone by the time the visitor sends their contact
    details — `_history_for_session` replays only the prose either side said.
    That prose names "Dr. Arben Hoxha" but never his UUID, so a model asked to
    call `propose_booking(staff_id=...)` has no id to pass and invents one,
    breaking the "never invent an id" rule it was given because following it
    was impossible.

    Re-reading the doctors and slots out of `agent_steps` puts those ids back
    in front of the model. Only the two step types that carry an id are read;
    everything else in the trace is noise for this purpose.
    """
    runs = execute_with_retry(
        get_db().table("agent_runs").select("id")
        .eq("session_id", session_id).neq("id", exclude_run_id)
        .order("created_at", desc=True).limit(MAX_HISTORY_RUNS)
    ).data or []
    if not runs:
        return ""

    steps = execute_with_retry(
        get_db().table("agent_steps").select("action,output")
        .in_("run_id", [r["id"] for r in runs]).order("timestamp")
    ).data or []

    doctors: Dict[str, str] = {}   # id -> name, deduped; later mentions win
    slots: List[str] = []
    for step in steps:
        output = step.get("output") or {}
        if step.get("action") == "list_doctors":
            for doc in output.get("doctors") or []:
                doctors[doc["id"]] = doc["full_name"]
        elif step.get("action") == "find_earliest_slot" and output.get("found"):
            doctors[output["staff_id"]] = output["staff_name"]
            slots.append(f"{output['staff_name']} at {output['slot']}")
        elif step.get("action") == "list_available_slots":
            for slot in (output.get("slots") or [])[:5]:
                name = doctors.get(output.get("staff_id"), output.get("staff_id"))
                slots.append(f"{name} at {slot}")

    parts: List[str] = []
    if doctors:
        listed = "; ".join(f"{name} (staff_id={sid})" for sid, name in doctors.items())
        parts.append(f"Doctors already looked up in this conversation: {listed}.")
    if slots:
        parts.append(f"Slots already offered: {'; '.join(slots[-8:])}.")
    if parts:
        parts.append(
            "Use these exact ids and times when the visitor refers back to a doctor "
            "or slot from earlier — do not invent an id, and do not re-run a lookup "
            "you already have the answer to."
        )
    return " ".join(parts)


async def run_booking_agent(run_id: str, session_id: str, input_text: str) -> None:
    """Drive one turn of the public booking chat."""
    if triage.is_emergency(input_text):
        # Fixed text, zero model calls — the one case where a slower, cleverer
        # answer is worse than a guaranteed-correct one.
        _log_step(run_id, "emergency_deflection", {"input": input_text}, {"deflected": True})
        _update_run(
            run_id, status="completed",
            result={"message": triage.EMERGENCY_MESSAGE},
            completed_at=datetime.utcnow().isoformat(),
        )
        return

    try:
        history = _history_for_session(session_id, run_id)
        context = f"Today is {clinic_today().isoformat()}."
        facts = _facts_for_session(session_id, run_id)
        if facts:
            context = f"{context} {facts}"

        def on_step(agent: str, action: str, inp: Any, out: Any) -> None:
            _log_step(run_id, action, inp, out)

        outcome = await run_agent_loop(
            BOOKING_AGENT, task=input_text, context=context, history=history, on_step=on_step,
        )

        if outcome.kind == "proposal":
            step_id = _log_step(run_id, "create_approval_gate", outcome.action or {}, {"status": "pending"})
            gate_id = _create_gate(run_id, step_id, outcome.description, outcome.action or {})
            _update_run(run_id, status="awaiting_approval")
            return

        # "answer" and "exhausted" both resolve to a message the visitor reads.
        # This agent has no handoff tool, so `outcome.kind == "handoff"` cannot
        # happen in practice; if it somehow did, `outcome.text` still reports
        # something reasonable rather than crashing the run.
        _update_run(
            run_id, status="completed",
            result={"message": outcome.text},
            completed_at=datetime.utcnow().isoformat(),
        )
    except Exception as e:  # noqa: BLE001 — surfaced on the run, not swallowed
        _update_run(run_id, status="failed", result={"error": str(e)})


async def resume_public_booking(run_id: str, gate_id: str, decision: str) -> None:
    """Execute a visitor-confirmed booking request. The only writer in this module.

    Reached only after `POST /public/booking/confirm/{gate_id}` has already
    verified the gate belongs to the caller's session — this function trusts
    that check happened and does not repeat it.
    """
    if decision != "approved":
        _update_run(
            run_id, status="completed",
            result={"message": "No problem — nothing was booked. Feel free to start over if you'd like a different time."},
            completed_at=datetime.utcnow().isoformat(),
        )
        return

    gate = _get_gate(gate_id)
    if not gate:
        return
    payload = gate["payload"]

    _log_step(run_id, "execute_confirmed_booking", payload, {"decision": decision})

    try:
        match = match_or_create_patient(
            first_name=payload.get("first_name", ""),
            last_name=payload.get("last_name", ""),
            phone=payload.get("phone", ""),
            email=payload.get("email", ""),
        )
        patient = match["patient"]

        db = get_db()
        appointment = db.table("appointments").insert({
            "patient_id": patient["id"],
            "staff_id": payload["staff_id"],
            "scheduled_at": to_clinic(datetime.fromisoformat(payload["scheduled_at"])).isoformat(),
            "duration_min": payload.get("duration_min", 30),
            "status": "confirmed",
            "source": "patient_portal",
            "notes": (
                f"[Patient portal] Reason: {payload.get('reason') or 'not specified'}. "
                f"{payload.get('notes', '')}"
            ).strip(),
            "created_by": None,
            "updated_by": None,
        }).execute().data[0]

        log_action(None, "create", "appointment", appointment["id"], {
            "source": "patient_portal", "patient_created": match["created"],
        })

        # Built from the row just written, not from `action_description` — that
        # string is phrased for a staff-side card and reads as nonsense once it
        # is spliced into a sentence addressed to the visitor themselves.
        when = to_clinic(datetime.fromisoformat(payload["scheduled_at"]))
        result = {
            "message": (
                f"You're booked with {payload.get('staff_name', 'the doctor')} on "
                f"{when.strftime('%A %d %B at %H:%M')}. "
                "Call the clinic if you need to change or cancel it."
            ),
            "appointment": appointment,
            "patient_created": match["created"],
        }
        _log_step(run_id, "booking_submitted", payload, result)
        _update_run(run_id, status="completed", result=result, completed_at=datetime.utcnow().isoformat())

    except Exception as e:
        _update_run(run_id, status="failed", result={"error": str(e)})
