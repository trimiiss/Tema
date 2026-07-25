"""Tests for approval gate enforcement — no write without approval."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.agents.orchestrator import node_appointment, node_patient, resume_orchestrator


@pytest.mark.asyncio
async def test_appointment_create_blocked_without_approval():
    """Appointment create node must return awaiting_approval, not commit directly."""
    with patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._create_gate", return_value="gate-99"), \
         patch("app.agents.orchestrator._update_run"), \
         patch("app.agents.appointment_agent.tool_check_patient",
               return_value={"found": True, "patient": {"id": "p1", "first_name": "A", "last_name": "B"}}), \
         patch("app.agents.appointment_agent.tool_check_staff",
               return_value={"staff": [{"id": "s1", "full_name": "Dr. X"}]}), \
         patch("app.agents.appointment_agent.tool_check_conflict",
               return_value={"has_conflict": False}), \
         patch("app.agents.appointment_agent.tool_create_appointment") as mock_create:
        state = await node_appointment({
            "run_id": "r1", "user_id": "u1", "input_text": "",
            "intent": "appointment", "sub_intent": "create",
            "extracted_params": {"patient_code": "P001", "staff_name": "X", "date": "2026-07-24", "time": "10:00"},
            "pending_action": None, "gate_id": None, "gate_decision": None,
            "result": None, "error": None, "status": "running",
        })
    # Appointment must NOT be created yet
    mock_create.assert_not_called()
    assert state["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_approval_rejected_does_not_execute():
    with patch("app.agents.orchestrator._update_run") as mock_update, \
         patch("app.agents.orchestrator._get_gate", return_value=None):
        await resume_orchestrator("run-1", "gate-1", "rejected", "user-1")
    # Should mark as completed with rejection message, not execute
    call_args = mock_update.call_args
    assert "completed" in str(call_args)


@pytest.mark.asyncio
async def test_approval_approved_executes_action():
    gate = {
        "id": "gate-1",
        "run_id": "run-1",
        "payload": {
            "action": "create",
            "patient_id": "p1", "patient_name": "A B",
            "staff_id": "s1", "staff_name": "Dr. X",
            "scheduled_at": "2026-07-24T10:00:00",
            "duration_min": 30, "notes": "",
        }
    }
    with patch("app.agents.orchestrator._get_gate", return_value=gate), \
         patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._update_run") as mock_update, \
         patch("app.agents.orchestrator.log_action"), \
         patch("app.agents.appointment_agent.tool_create_appointment",
               return_value={"appointment": {"id": "appt-new"}}) as mock_create:
        await resume_orchestrator("run-1", "gate-1", "approved", "user-1")
    mock_create.assert_called_once()
    # Should mark run as completed
    mock_update.assert_called()


@pytest.mark.asyncio
async def test_gate_cannot_be_decided_twice():
    """A pending gate set to 'approved' cannot be decided again."""
    from unittest.mock import MagicMock
    gate = MagicMock()
    gate.data = {"id": "g1", "status": "approved", "run_id": "r1", "payload": {}}

    mock_db = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = gate
    for m in ("select","eq","maybe_single"):
        getattr(chain, m).return_value = chain
    mock_db.table.return_value = chain

    from fastapi.testclient import TestClient
    from app.main import app
    from app.core import database, auth
    from jose import jwt
    token = jwt.encode({"sub": "u1", "email": "a@b.com"}, "test-jwt-secret-32chars-minimum!!", "HS256")

    role_chain = MagicMock()
    role_chain.execute.return_value = MagicMock(data=[{"roles": {"name": "admin"}}])
    for m in ("select","eq","maybe_single","update"):
        getattr(role_chain, m).return_value = role_chain
    mock_db.table.side_effect = lambda t: role_chain if t == "user_roles" else chain

    with patch.object(database, "get_db", return_value=mock_db), \
         patch.object(auth, "get_db", return_value=mock_db):
        c = TestClient(app)
        r = c.post(f"/agent/approve/g1",
                   json={"decision": "approved"},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409
