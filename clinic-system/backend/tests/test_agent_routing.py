"""Tests for orchestrator intent classification and routing."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import json


def _mock_gpt(content: str):
    mock = AsyncMock()
    mock.choices = [MagicMock(message=MagicMock(content=content))]
    return mock


@pytest.mark.asyncio
async def test_classify_appointment_intent():
    from app.agents.orchestrator import node_classify_intent

    with patch("app.agents.orchestrator._client") as mock_client_fn:
        client = AsyncMock()
        client.chat.completions.create.return_value = _mock_gpt(
            '{"intent": "appointment", "sub_intent": "create", "params": {"patient_code": "P001"}}'
        )
        mock_client_fn.return_value = client
        with patch("app.agents.orchestrator._log_step", return_value="step-1"), \
             patch("app.agents.orchestrator._update_run"):
            state = await node_classify_intent({
                "run_id": "run-1", "user_id": "u1",
                "input_text": "Schedule an appointment for P001",
                "intent": None, "sub_intent": None, "extracted_params": {},
                "pending_action": None, "gate_id": None, "gate_decision": None,
                "result": None, "error": None, "status": "running",
            })
    assert state["intent"] == "appointment"
    assert state["sub_intent"] == "create"


@pytest.mark.asyncio
async def test_classify_patient_intent():
    from app.agents.orchestrator import node_classify_intent

    with patch("app.agents.orchestrator._client") as mock_client_fn:
        client = AsyncMock()
        client.chat.completions.create.return_value = _mock_gpt(
            '{"intent": "patient", "sub_intent": "read", "params": {"patient_code": "P002"}}'
        )
        mock_client_fn.return_value = client
        with patch("app.agents.orchestrator._log_step", return_value="step-1"), \
             patch("app.agents.orchestrator._update_run"):
            state = await node_classify_intent({
                "run_id": "run-2", "user_id": "u1",
                "input_text": "Show me patient P002",
                "intent": None, "sub_intent": None, "extracted_params": {},
                "pending_action": None, "gate_id": None, "gate_decision": None,
                "result": None, "error": None, "status": "running",
            })
    assert state["intent"] == "patient"


@pytest.mark.asyncio
async def test_classify_report_intent():
    from app.agents.orchestrator import node_classify_intent

    with patch("app.agents.orchestrator._client") as mock_client_fn:
        client = AsyncMock()
        client.chat.completions.create.return_value = _mock_gpt(
            '{"intent": "report", "sub_intent": "generate", "params": {"date_from": "2026-07-14", "date_to": "2026-07-20"}}'
        )
        mock_client_fn.return_value = client
        with patch("app.agents.orchestrator._log_step", return_value="step-1"), \
             patch("app.agents.orchestrator._update_run"):
            state = await node_classify_intent({
                "run_id": "run-3", "user_id": "u1",
                "input_text": "Generate weekly report",
                "intent": None, "sub_intent": None, "extracted_params": {},
                "pending_action": None, "gate_id": None, "gate_decision": None,
                "result": None, "error": None, "status": "running",
            })
    assert state["intent"] == "report"


def test_route_intent_appointment():
    from app.agents.orchestrator import _route_intent
    state = {"intent": "appointment"}
    assert _route_intent(state) == "appointment"


def test_route_intent_patient():
    from app.agents.orchestrator import _route_intent
    assert _route_intent({"intent": "patient"}) == "patient"


def test_route_intent_report():
    from app.agents.orchestrator import _route_intent
    assert _route_intent({"intent": "report"}) == "report"


def test_route_intent_unknown_falls_back():
    from app.agents.orchestrator import _route_intent
    assert _route_intent({"intent": "unknown"}) == "fallback"
    assert _route_intent({"intent": "gibberish"}) == "fallback"


@pytest.mark.asyncio
async def test_appointment_node_creates_approval_gate_on_conflict_free():
    from app.agents.orchestrator import node_appointment

    with patch("app.agents.orchestrator._log_step", return_value="step-x"), \
         patch("app.agents.orchestrator._create_gate", return_value="gate-1"), \
         patch("app.agents.orchestrator._update_run"), \
         patch("app.agents.appointment_agent.tool_check_patient",
               return_value={"found": True, "patient": {"id": "p1", "first_name": "Alban", "last_name": "Krasniqi"}}), \
         patch("app.agents.appointment_agent.tool_check_staff",
               return_value={"staff": [{"id": "s1", "full_name": "Dr. Hoxha"}]}), \
         patch("app.agents.appointment_agent.tool_check_conflict",
               return_value={"has_conflict": False}):
        state = await node_appointment({
            "run_id": "run-4", "user_id": "u1",
            "input_text": "Book appointment",
            "intent": "appointment", "sub_intent": "create",
            "extracted_params": {"patient_code": "P001", "staff_name": "Hoxha", "date": "2026-07-24", "time": "10:00"},
            "pending_action": None, "gate_id": None, "gate_decision": None,
            "result": None, "error": None, "status": "running",
        })
    assert state["status"] == "awaiting_approval"
    assert state["gate_id"] == "gate-1"
