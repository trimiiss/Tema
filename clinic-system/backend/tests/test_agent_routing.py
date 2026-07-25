"""Supervisor routing: which agent runs next, and when the run stops."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.orchestrator import (
    AGENT_NAMES, MAX_HOPS, _after_agent, _route_supervisor, initial_state, node_supervisor,
)


def _mock_completion(content: str):
    """A chat completion whose message is plain content and no tool calls."""
    message = MagicMock(content=content, tool_calls=None)
    return MagicMock(choices=[MagicMock(message=message)])


def _supervisor_says(**decision):
    client = AsyncMock()
    client.chat.completions.create.return_value = _mock_completion(json.dumps(decision))
    return client


async def _supervise(state, client):
    with patch("app.agents.orchestrator._client", return_value=client), \
         patch("app.agents.orchestrator._log_step", return_value="step-1"), \
         patch("app.agents.orchestrator._update_run"):
        return await node_supervisor(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", AGENT_NAMES)
async def test_supervisor_selects_each_agent(agent):
    state = initial_state("run-1", "do the thing", "u1")
    result = await _supervise(state, _supervisor_says(agent=agent, task="the task", reason="because"))
    assert result["active_agent"] == agent
    assert result["task"] == "the task"
    assert _route_supervisor(result) == agent


@pytest.mark.asyncio
async def test_supervisor_writes_a_self_contained_task_for_the_agent():
    """The agent never sees the conversation — only the task the supervisor wrote."""
    state = initial_state("run-2", "book Alban with Dr. Hoxha tomorrow at 10", "u1")
    result = await _supervise(state, _supervisor_says(
        agent="appointment_agent",
        task="Book patient Alban Krasniqi with Dr. Hoxha tomorrow at 10:00.",
        reason="scheduling",
    ))
    assert "Alban" in result["task"] and "10:00" in result["task"]


@pytest.mark.asyncio
async def test_unknown_agent_choice_falls_back():
    """A hallucinated agent name must not become a graph node name."""
    state = initial_state("run-3", "whatever", "u1")
    result = await _supervise(state, _supervisor_says(agent="billing_agent", task="x", reason="y"))
    assert result["active_agent"] == "fallback"
    assert _route_supervisor(result) == "fallback"


@pytest.mark.asyncio
async def test_unparseable_supervisor_reply_falls_back():
    client = AsyncMock()
    client.chat.completions.create.return_value = _mock_completion("not json at all")
    result = await _supervise(initial_state("run-4", "hello", "u1"), client)
    assert _route_supervisor(result) == "fallback"


@pytest.mark.asyncio
async def test_finish_routes_to_finalize():
    state = initial_state("run-5", "thanks", "u1")
    state["answers"] = [{"agent": "reporting_agent", "text": "42 appointments last week."}]
    result = await _supervise(state, _supervisor_says(agent="finish", task="", reason="answered"))
    assert _route_supervisor(result) == "finalize"


@pytest.mark.asyncio
async def test_handoff_bypasses_the_supervisor_model():
    """A delegating agent already chose its peer; re-deciding causes ping-pong.

    If the supervisor re-ran the model here, two agents that each believe the
    task belongs to the other would bounce the run between them until the hop
    limit killed it.
    """
    state = initial_state("run-6", "register Arta then book her", "u1")
    state.update({"active_agent": "appointment_agent", "handoff_from": "patient_agent",
                  "task": "Book Arta Berisha (id p-9) with Dr. Hoxha on 2026-08-03 at 10:00."})

    client = _supervisor_says(agent="patient_agent", task="ignored", reason="ignored")
    result = await _supervise(state, client)

    client.chat.completions.create.assert_not_called()
    assert result["active_agent"] == "appointment_agent"
    assert result["handoff_from"] is None
    assert "p-9" in result["task"]


@pytest.mark.asyncio
async def test_hop_limit_stops_the_cycle_without_asking_the_model():
    """The graph is cyclic, so something has to make it terminate."""
    state = initial_state("run-7", "loop forever", "u1")
    state["hops"] = MAX_HOPS

    client = _supervisor_says(agent="appointment_agent", task="keep going", reason="no")
    result = await _supervise(state, client)

    client.chat.completions.create.assert_not_called()
    assert _route_supervisor(result) == "finalize"


def test_agent_returns_to_supervisor_until_the_hop_limit():
    state = initial_state("run-8", "x", "u1")
    state["hops"] = 1
    assert _after_agent(state) == "supervisor"

    state["hops"] = MAX_HOPS
    assert _after_agent(state) == "finalize"


def test_awaiting_approval_ends_the_run_immediately():
    """A gate hands control to a human; the graph must not keep working."""
    state = initial_state("run-9", "book something", "u1")
    state.update({"status": "awaiting_approval", "hops": 1})
    assert _after_agent(state) == "finalize"


def test_parse_clinic_datetime_accepts_relative_days_and_12h_clock():
    """The model still emits "tomorrow"/"10am" despite the prompt.

    `datetime.fromisoformat("tomorrowT10am:00")` raised, which surfaced to the
    user as "Could not parse date/time" and killed the booking outright.
    """
    from datetime import timedelta
    from app.agents.orchestrator import parse_clinic_datetime, _clinic_today

    tomorrow = _clinic_today() + timedelta(days=1)
    assert parse_clinic_datetime("tomorrow", "10am").date() == tomorrow
    assert parse_clinic_datetime("tomorrow", "10am").hour == 10

    # 12-hour spellings, and the two meridiem edge cases.
    assert parse_clinic_datetime("2026-07-27", "2:30pm").hour == 14
    assert parse_clinic_datetime("2026-07-27", "2:30pm").minute == 30
    assert parse_clinic_datetime("2026-07-27", "12am").hour == 0
    assert parse_clinic_datetime("2026-07-27", "12pm").hour == 12
    assert parse_clinic_datetime("2026-07-27", "14:00").hour == 14

    with pytest.raises(ValueError):
        parse_clinic_datetime("next fortnight", "10:00")


def test_parse_when_handles_the_single_string_agents_send():
    from app.services.schedule_service import parse_when

    assert parse_when("2026-08-03T10:00").hour == 10
    assert parse_when("2026-08-03 10:00").hour == 10
    assert parse_when("2026-08-03 2pm").hour == 14
    with pytest.raises(ValueError):
        parse_when("")
