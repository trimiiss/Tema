"""Approval gate enforcement — no write without a human decision.

The invariant survives the agents becoming autonomous: an agent decides for
itself what to propose, but a proposal is all it can ever produce.
`resume_orchestrator` is the only code path that calls a `@mutating` function,
and it runs only after `POST /agent/approve/{gate_id}` accepted a gate.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.orchestrator import initial_state, node_appointment, resume_orchestrator
from tests.conftest import make_chain, patch_db, table_chain
from tests.test_agent_autonomy import calls, says, scripted, tool_call

PATIENT = {"id": "p1", "code": "P001", "first_name": "Arta", "last_name": "Berisha"}
STAFF = {"id": "s1", "full_name": "Dr. Arben Hoxha", "specialty": "General"}
MONDAY_10 = "2026-08-03T10:00"    # a Monday; clinic default hours are Mon–Fri


@pytest.fixture
def clinic_db():
    tables = {
        "patients": table_chain([PATIENT]),
        "staff": table_chain([STAFF]),
        "schedules": make_chain([]),
        "appointments": make_chain([]),
    }
    db = MagicMock()
    db.table.side_effect = lambda name: tables.get(name, make_chain([]))
    with patch_db(db):
        yield db


@pytest.mark.asyncio
async def test_agent_proposal_opens_a_gate_and_writes_nothing(clinic_db):
    """The whole path: the agent decides to book, and a gate is what comes out."""
    client = scripted(calls(tool_call("c1", "propose_create_appointment",
                                      patient_id="p1", staff_id="s1", scheduled_at=MONDAY_10)))

    with patch("app.agents.runtime.get_client", return_value=client), \
         patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._create_gate", return_value="gate-99") as gate, \
         patch("app.agents.orchestrator._update_run"), \
         patch("app.agents.appointment_agent.tool_create_appointment") as write:
        state = initial_state("r1", "book Arta with Dr. Hoxha Monday at 10", "u1")
        state["task"] = state["input_text"]
        result = await node_appointment(state)

    write.assert_not_called()
    assert result["status"] == "awaiting_approval"
    assert result["gate_id"] == "gate-99"
    assert result["pending_action"]["action"] == "create"

    _, _, description, payload = gate.call_args.args
    assert "Arta Berisha" in description        # what the human will actually approve
    assert payload["scheduled_at"].startswith("2026-08-03T10:00")


@pytest.mark.asyncio
async def test_an_unworkable_slot_never_reaches_a_human(clinic_db):
    """A gate is a request for a human's attention; don't spend it on a Sunday.

    Nothing is booked that Sunday, so a conflict check alone would pass it. The
    proposal validator checks the doctor's hours too and refuses, and the agent
    reports back instead.
    """
    client = scripted(
        calls(tool_call("c1", "propose_create_appointment",
                        patient_id="p1", staff_id="s1", scheduled_at="2026-08-02T10:00")),
        says("Dr. Hoxha doesn't work Sundays — Monday 09:00 is free."),
    )

    with patch("app.agents.runtime.get_client", return_value=client), \
         patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._create_gate") as gate, \
         patch("app.agents.orchestrator._update_run"):
        state = initial_state("r2", "book Arta on Sunday", "u1")
        result = await node_appointment(state)

    gate.assert_not_called()
    assert result["status"] == "running"
    assert "Sundays" in result["answers"][-1]["text"]


@pytest.mark.asyncio
async def test_approval_rejected_does_not_execute():
    with patch("app.agents.orchestrator._update_run") as mock_update, \
         patch("app.agents.orchestrator._get_gate", return_value=None):
        await resume_orchestrator("run-1", "gate-1", "rejected", "user-1")
    call_args = mock_update.call_args
    assert "completed" in str(call_args)


@pytest.mark.asyncio
async def test_approval_approved_executes_action():
    gate = {
        "id": "gate-1",
        "run_id": "run-1",
        "action_description": "Create appointment for Arta Berisha",
        "payload": {
            "action": "create",
            "patient_id": "p1", "patient_name": "Arta Berisha",
            "staff_id": "s1", "staff_name": "Dr. Arben Hoxha",
            "scheduled_at": MONDAY_10,
            "duration_min": 30, "notes": "",
        },
    }
    with patch("app.agents.orchestrator._get_gate", return_value=gate), \
         patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._update_run") as mock_update, \
         patch("app.agents.orchestrator.log_action"), \
         patch("app.agents.appointment_agent.tool_create_appointment",
               return_value={"appointment": {"id": "appt-new"}}) as mock_create:
        await resume_orchestrator("run-1", "gate-1", "approved", "user-1")
    mock_create.assert_called_once()
    mock_update.assert_called()


@pytest.mark.asyncio
async def test_approved_patient_creation_is_audited():
    """Every executed action leaves an `audit_logs` row naming who approved it."""
    gate = {
        "id": "gate-2", "run_id": "run-2",
        "action_description": "Register new patient Blerim Gashi as P042",
        "payload": {"action": "create_patient",
                    "params": {"code": "P042", "first_name": "Blerim", "last_name": "Gashi"}},
    }
    with patch("app.agents.orchestrator._get_gate", return_value=gate), \
         patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._update_run"), \
         patch("app.agents.orchestrator.log_action") as audit, \
         patch("app.agents.patient_agent.tool_create_patient",
               return_value={"patient": {"id": "p-new"}}):
        await resume_orchestrator("run-2", "gate-2", "approved", "user-7")

    audit.assert_called_once_with("user-7", "create", "patient", "p-new")


@pytest.mark.asyncio
async def test_gate_cannot_be_decided_twice():
    """A pending gate set to 'approved' cannot be decided again."""
    gate = MagicMock()
    gate.data = {"id": "g1", "status": "approved", "run_id": "r1", "payload": {}}

    mock_db = MagicMock()
    chain = make_chain(None)
    chain.execute.return_value = gate

    from fastapi.testclient import TestClient
    from app.main import app
    from jose import jwt
    token = jwt.encode({"sub": "u1", "email": "a@b.com"}, "test-jwt-secret-32chars-minimum!!", "HS256")

    role_chain = make_chain([{"roles": {"name": "admin"}}])
    mock_db.table.side_effect = lambda t: role_chain if t == "user_roles" else chain

    with patch_db(mock_db):
        c = TestClient(app)
        r = c.post("/agent/approve/g1",
                   json={"decision": "approved"},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409


# ------------------------------------------------------- multi-agent, end to end

@pytest.mark.asyncio
async def test_a_request_that_needs_two_agents_traverses_both(clinic_db):
    """The flagship path, run through the real graph.

    "Register Arta and book her" is not one agent's job. The supervisor sends it
    to the appointment agent, which finds no such patient and hands off; the
    supervisor honours the handoff without re-deciding; the patient agent
    proposes the registration; the run stops at a gate. Four LLM turns, two
    agents, one human decision — and no write.
    """
    clinic_db.table.side_effect = lambda name: {
        "staff": table_chain([STAFF]),
    }.get(name, table_chain([]))      # no patients on file

    supervisor = AsyncMock()
    supervisor.chat.completions.create.return_value = MagicMock(choices=[MagicMock(
        message=MagicMock(content=json.dumps({
            "agent": "appointment_agent",
            "task": "Book Arta Berisha with Dr. Hoxha on 2026-08-03 at 10:00.",
            "reason": "scheduling request",
        }), tool_calls=None))])

    agents = scripted(
        # appointment agent: look for her, don't find her, delegate
        calls(tool_call("c1", "find_patient", query="Arta Berisha")),
        calls(tool_call("c2", "handoff_to_patient_agent",
                        task="Register Arta Berisha, then book her with Dr. Hoxha (s1) "
                             "on 2026-08-03 at 10:00.",
                        reason="She has no record yet.")),
        # patient agent: register her
        calls(tool_call("c3", "propose_create_patient", first_name="Arta", last_name="Berisha")),
    )

    steps, gates = [], []
    with patch("app.agents.orchestrator._client", return_value=supervisor), \
         patch("app.agents.runtime.get_client", return_value=agents), \
         patch("app.agents.orchestrator._log_step",
               side_effect=lambda run, agent, action, i, o: steps.append((agent, action)) or "s"), \
         patch("app.agents.orchestrator._create_gate",
               side_effect=lambda run, step, desc, payload: gates.append((desc, payload)) or "gate-1"), \
         patch("app.agents.orchestrator._update_run"), \
         patch("app.agents.patient_agent.tool_create_patient") as write_patient, \
         patch("app.agents.appointment_agent.tool_create_appointment") as write_appt:
        from app.agents.orchestrator import _graph
        final = await _graph.ainvoke(initial_state("r-e2e", "register Arta Berisha and book her "
                                                            "with Dr. Hoxha Monday at 10", "u1"))

    # Both agents worked, in that order, and the supervisor took the handoff as given.
    visited = [agent for agent, _ in steps]
    assert visited.index("appointment_agent") < visited.index("patient_agent")
    assert ("supervisor", "accept_handoff") in steps
    assert supervisor.chat.completions.create.call_count == 1   # not re-decided on handoff

    # It ended at a gate, and nothing was written.
    assert final["status"] == "awaiting_approval"
    assert len(gates) == 1
    description, payload = gates[0]
    assert "Arta Berisha" in description
    assert payload["action"] == "create_patient"
    write_patient.assert_not_called()
    write_appt.assert_not_called()
