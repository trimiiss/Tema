"""
Orchestrator Agent — LangGraph state machine that routes to sub-agents,
enforces approval gates before any write operation, and records all steps.
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.database import get_db
from app.core.audit import log_action
from app.core.events import publish

_oai: AsyncOpenAI | None = None


def _client() -> AsyncOpenAI:
    global _oai
    if _oai is None:
        _oai = AsyncOpenAI(api_key=settings.openai_api_key)
    return _oai


# ---- State ----
class OrchestratorState(TypedDict):
    run_id: str
    user_id: str
    input_text: str
    intent: Optional[str]
    sub_intent: Optional[str]
    extracted_params: Dict[str, Any]
    pending_action: Optional[Dict[str, Any]]  # action awaiting approval
    gate_id: Optional[str]
    gate_decision: Optional[str]             # "approved" | "rejected"
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    status: str  # "running" | "awaiting_approval" | "completed" | "failed"


# ---- Helpers ----
def _log_step(run_id: str, agent_name: str, action: str, inp: Any, out: Any) -> str:
    db = get_db()
    resp = db.table("agent_steps").insert({
        "run_id": run_id,
        "agent_name": agent_name,
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
    db = get_db()
    resp = db.table("approval_gates").select("*").eq("id", gate_id).maybe_single().execute()
    return resp.data


# ---- Nodes ----
async def node_classify_intent(state: OrchestratorState) -> OrchestratorState:
    run_id = state["run_id"]
    text = state["input_text"]

    response = await _client().chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an intent classifier for a clinic administrative system. "
                    "Classify the user request into one of: appointment, patient, document, report. "
                    "Also extract key parameters (patient_code, staff_name, date, time, action). "
                    "action can be: create, read, update, cancel, reschedule, list, generate. "
                    "Respond with JSON only: "
                    "{\"intent\": \"...\", \"sub_intent\": \"...\", \"params\": {...}}"
                ),
            },
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        max_tokens=300,
    )
    parsed = json.loads(response.choices[0].message.content)
    step_id = _log_step(run_id, "orchestrator", "classify_intent", {"text": text}, parsed)

    return {
        **state,
        "intent": parsed.get("intent", "unknown"),
        "sub_intent": parsed.get("sub_intent", ""),
        "extracted_params": parsed.get("params", {}),
    }


def _route_intent(state: OrchestratorState) -> str:
    intent = state.get("intent", "unknown")
    routes = {"appointment": "appointment", "patient": "patient", "document": "document", "report": "report"}
    return routes.get(intent, "fallback")


async def node_appointment(state: OrchestratorState) -> OrchestratorState:
    from app.agents.appointment_agent import (
        tool_check_patient, tool_check_staff, tool_available_slots,
        tool_check_conflict,
    )
    run_id = state["run_id"]
    params = state["extracted_params"]
    sub = state.get("sub_intent", "create")

    if sub in ("read", "list"):
        db = get_db()
        patient_code = params.get("patient_code", "")
        appts = []
        if patient_code:
            pat = tool_check_patient(patient_code)
            if pat["found"]:
                appts = db.table("appointments").select("*").eq("patient_id", pat["patient"]["id"]).execute().data
        step_id = _log_step(run_id, "appointment_agent", "list_appointments", params, {"count": len(appts)})
        return {**state, "result": {"appointments": appts}, "status": "completed"}

    # For create/reschedule/cancel — gather info first, then require approval
    patient_code = params.get("patient_code", "")
    staff_name = params.get("staff_name", "")
    date_str = params.get("date", "")
    time_str = params.get("time", "09:00")

    patient_result = tool_check_patient(patient_code) if patient_code else {"found": False}
    staff_result = tool_check_staff(staff_name) if staff_name else {"staff": []}

    step_id = _log_step(run_id, "appointment_agent", "gather_info",
                        {"patient_code": patient_code, "staff_name": staff_name},
                        {"patient": patient_result, "staff": staff_result})

    if not patient_result["found"]:
        return {**state, "result": {"error": f"Patient '{patient_code}' not found"}, "status": "completed"}
    if not staff_result["staff"]:
        return {**state, "result": {"error": f"Staff '{staff_name}' not found"}, "status": "completed"}

    patient = patient_result["patient"]
    staff = staff_result["staff"][0]

    # Parse datetime
    try:
        scheduled_at = datetime.fromisoformat(f"{date_str}T{time_str}:00")
    except Exception:
        return {**state, "result": {"error": "Could not parse date/time"}, "status": "completed"}

    # Check conflict
    conflict = tool_check_conflict(staff["id"], scheduled_at.isoformat())
    step_id2 = _log_step(run_id, "appointment_agent", "check_conflict",
                         {"staff_id": staff["id"], "scheduled_at": scheduled_at.isoformat()},
                         conflict)

    if conflict["has_conflict"]:
        return {**state, "result": {"error": "Time conflict detected", "conflict": conflict}, "status": "completed"}

    # Build pending action for approval gate
    pending = {
        "action": sub,
        "patient_id": patient["id"],
        "patient_name": f"{patient['first_name']} {patient['last_name']}",
        "staff_id": staff["id"],
        "staff_name": staff["full_name"],
        "scheduled_at": scheduled_at.isoformat(),
        "duration_min": 30,
        "notes": params.get("notes", ""),
    }

    gate_step_id = _log_step(run_id, "orchestrator", "create_approval_gate", pending, {"status": "pending"})
    gate_id = _create_gate(
        run_id, gate_step_id,
        f"Create appointment for {pending['patient_name']} with {pending['staff_name']} on {scheduled_at.strftime('%Y-%m-%d %H:%M')}",
        pending,
    )
    _update_run(run_id, status="awaiting_approval")

    return {**state, "pending_action": pending, "gate_id": gate_id, "status": "awaiting_approval"}


async def node_patient(state: OrchestratorState) -> OrchestratorState:
    from app.agents.patient_agent import tool_get_patient, tool_search_patients, tool_flag_missing_fields
    run_id = state["run_id"]
    params = state["extracted_params"]
    sub = state.get("sub_intent", "read")

    if sub == "read":
        code = params.get("patient_code", "")
        result = tool_get_patient(code) if code else tool_search_patients(params.get("query", ""))
        _log_step(run_id, "patient_agent", "read_patient", params, result)
        return {**state, "result": result, "status": "completed"}

    if sub in ("missing_fields", "check"):
        patient_id = params.get("patient_id", "")
        result = tool_flag_missing_fields(patient_id)
        _log_step(run_id, "patient_agent", "flag_missing", params, result)
        return {**state, "result": result, "status": "completed"}

    # Write operations require approval
    pending = {"action": sub, "params": params}
    step_id = _log_step(run_id, "orchestrator", "create_approval_gate", pending, {"status": "pending"})
    gate_id = _create_gate(run_id, step_id, f"Patient {sub}: {params}", pending)
    _update_run(run_id, status="awaiting_approval")
    return {**state, "pending_action": pending, "gate_id": gate_id, "status": "awaiting_approval"}


async def node_document(state: OrchestratorState) -> OrchestratorState:
    run_id = state["run_id"]
    params = state["extracted_params"]
    _log_step(run_id, "document_agent", "route", params, {"note": "document upload handled via /documents/upload endpoint"})
    return {**state, "result": {"message": "Upload documents via the Documents page. The agent will auto-process them."}, "status": "completed"}


async def node_report(state: OrchestratorState) -> OrchestratorState:
    from app.agents.reporting_agent import run_reporting_agent
    run_id = state["run_id"]
    params = state["extracted_params"]

    date_from = params.get("date_from", "")
    date_to = params.get("date_to", "")
    if not date_from or not date_to:
        # default to current week
        today = datetime.utcnow().date()
        from datetime import timedelta
        date_from = (today - timedelta(days=today.weekday())).isoformat()
        date_to = today.isoformat()

    _log_step(run_id, "reporting_agent", "generate_report", {"date_from": date_from, "date_to": date_to}, {})
    result = await run_reporting_agent(date_from, date_to)
    _log_step(run_id, "reporting_agent", "report_complete", {}, {"summary": result["appointment_summary"]})
    _update_run(run_id, status="completed", result=result, completed_at=datetime.utcnow().isoformat())
    return {**state, "result": result, "status": "completed"}


async def node_fallback(state: OrchestratorState) -> OrchestratorState:
    run_id = state["run_id"]
    _log_step(run_id, "orchestrator", "fallback", {"intent": state.get("intent")},
              {"message": "Could not route to a specific agent"})
    _update_run(run_id, status="completed")
    return {**state, "result": {"message": "I couldn't understand that request. Try: booking an appointment, looking up a patient, or generating a report."}, "status": "completed"}


async def node_finalize(state: OrchestratorState) -> OrchestratorState:
    if state["status"] == "completed":
        _update_run(state["run_id"], status="completed",
                    result=state.get("result"),
                    completed_at=datetime.utcnow().isoformat())
    return state


# ---- Graph ----
def _build_graph() -> StateGraph:
    g = StateGraph(OrchestratorState)
    g.add_node("classify", node_classify_intent)
    g.add_node("appointment", node_appointment)
    g.add_node("patient", node_patient)
    g.add_node("document", node_document)
    g.add_node("report", node_report)
    g.add_node("fallback", node_fallback)
    g.add_node("finalize", node_finalize)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _route_intent, {
        "appointment": "appointment",
        "patient":     "patient",
        "document":    "document",
        "report":      "report",
        "fallback":    "fallback",
    })
    for n in ("appointment", "patient", "document", "report", "fallback"):
        g.add_edge(n, "finalize")
    g.add_edge("finalize", END)
    return g


_graph = _build_graph().compile()


async def run_orchestrator(run_id: str, input_text: str, user_id: str) -> None:
    try:
        initial: OrchestratorState = {
            "run_id": run_id,
            "user_id": user_id,
            "input_text": input_text,
            "intent": None,
            "sub_intent": None,
            "extracted_params": {},
            "pending_action": None,
            "gate_id": None,
            "gate_decision": None,
            "result": None,
            "error": None,
            "status": "running",
        }
        await _graph.ainvoke(initial)
    except Exception as e:
        _update_run(run_id, status="failed", result={"error": str(e)})


async def resume_orchestrator(run_id: str, gate_id: str, decision: str, user_id: str) -> None:
    if decision != "approved":
        _update_run(run_id, status="completed",
                    result={"message": "Action rejected by user."},
                    completed_at=datetime.utcnow().isoformat())
        return

    # Execute the approved action
    gate = _get_gate(gate_id)
    if not gate:
        return
    payload = gate["payload"]
    action = payload.get("action", "create")

    step_id = _log_step(run_id, "orchestrator", "execute_approved_action", payload, {"decision": decision})

    try:
        if action in ("create",):
            from app.agents.appointment_agent import tool_create_appointment
            result = tool_create_appointment(
                patient_id=payload["patient_id"],
                staff_id=payload["staff_id"],
                service_id=payload.get("service_id"),
                scheduled_at=payload["scheduled_at"],
                duration_min=payload.get("duration_min", 30),
                notes=payload.get("notes", ""),
                created_by=user_id,
            )
            log_action(user_id, "create", "appointment", result["appointment"]["id"])

        elif action == "cancel":
            from app.agents.appointment_agent import tool_cancel_appointment
            result = tool_cancel_appointment(payload["appointment_id"], user_id)
            log_action(user_id, "cancel", "appointment", payload["appointment_id"])

        elif action == "reschedule":
            from app.agents.appointment_agent import tool_reschedule_appointment
            result = tool_reschedule_appointment(payload["appointment_id"], payload["new_scheduled_at"], user_id)
            log_action(user_id, "reschedule", "appointment", payload["appointment_id"])

        elif action in ("create_patient", "update_patient"):
            from app.agents.patient_agent import tool_create_patient, tool_update_patient
            if action == "create_patient":
                result = tool_create_patient(payload["params"], user_id)
            else:
                result = tool_update_patient(payload["params"]["patient_id"], payload["params"]["fields"], user_id)

        else:
            result = {"error": f"Unknown action: {action}"}

        _log_step(run_id, "orchestrator", "action_complete", payload, result)
        _update_run(run_id, status="completed", result=result, completed_at=datetime.utcnow().isoformat())

    except Exception as e:
        _update_run(run_id, status="failed", result={"error": str(e)})
