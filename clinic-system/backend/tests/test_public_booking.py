"""The public booking chat — the one surface in this app with no auth.

Two properties earn their own tests here, beyond what `test_agent_autonomy.py`
already checks for every agent (BOOKING_AGENT is parametrized into that
module's `ALL_AGENTS`, so it already gets the no-write-tools, schema-matches-
function, and shared-rules coverage for free):

- `session_id` is the only thing standing in for a JWT on this router, so
  every read or decision on a run/gate must be scoped to it — a mismatch has
  to look exactly like "doesn't exist", or run ids become an enumeration
  oracle over other visitors' bookings.
- `BOOKING_AGENT` talks to strangers, so "no patient data leaks" has to hold
  structurally (no lookup tool exists to call) rather than by the model
  choosing not to answer.
"""
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.agents.booking_agent import BOOKING_AGENT, propose_booking
from app.agents.runtime import _prior_turns, assert_no_write_tools, run_agent_loop
from app.services import triage
from app.services.patient_matching import match_or_create_patient
from tests.conftest import make_chain, patch_db, table_chain
from tests.test_agent_autonomy import calls, fed_back, says, scripted, tool_call

STAFF = {"id": "s-1", "full_name": "Dr. Arben Hoxha", "specialty": "General Practice", "bio": None}
# 2026-08-03 is a Monday; 2026-08-02 a Sunday. No `schedules` rows means the
# clinic default (Mon–Fri 09:00–17:00) applies, so the real slot logic runs.
MONDAY_10 = "2026-08-03T10:00"
SUNDAY_10 = "2026-08-02T10:00"


@pytest.fixture
def clinic_db():
    """One active doctor, default hours, nothing booked, no patients on file."""
    tables = {
        "staff": table_chain([STAFF]),
        "schedules": make_chain([]),
        "appointments": make_chain([]),
        "patients": make_chain([]),
    }
    db = MagicMock()
    db.table.side_effect = lambda name: tables.get(name, make_chain([]))
    with patch_db(db):
        yield db


# ------------------------------------------------------- the leaf-agent invariant

def test_booking_agent_has_no_write_tools():
    assert_no_write_tools(BOOKING_AGENT)


def test_booking_agent_has_no_patient_lookup_tool():
    """The one property no staff agent needs and this one must have.

    An anonymous visitor asking "find patient Krasniqi" must have no tool that
    could possibly answer that — not a tool that refuses, a tool that does not
    exist to be called.
    """
    names = {t.name.lower() for t in BOOKING_AGENT.tools}
    for forbidden in ("find_patient", "search_patients", "get_patient", "list_patient_appointments"):
        assert forbidden not in names
    # Nothing here even mentions a patient by name — the only tool that deals
    # with one at all is the write-shaped proposal, which creates a booking
    # request and never reads one back.
    assert not any("patient" in n for n in names)


def test_booking_agent_has_no_handoff_tool():
    """A leaf agent by construction — cannot reach patient_agent or
    appointment_agent, which do have lookup tools."""
    assert not any(t.kind == "handoff" for t in BOOKING_AGENT.tools)


@pytest.mark.asyncio
async def test_a_hallucinated_patient_lookup_call_finds_no_such_tool():
    """Simulates scenario S22: talked into trying, finds nothing to call.

    There is no data to leak because there is no tool that could fetch it —
    the runtime's ordinary "no tool named" path (already exercised for every
    staff agent in `test_agent_autonomy.py`) is what fires here too.
    """
    client = scripted(
        calls(tool_call("c1", "find_patient", query="Krasniqi")),
        says("I can't look up patient records here — a receptionist can help by phone."),
    )
    outcome = await run_agent_loop(BOOKING_AGENT, task="look up patient Krasniqi", client=client)

    assert outcome.kind == "answer"
    assert "No tool named 'find_patient'" in fed_back(client, 2)


@pytest.mark.asyncio
async def test_run_agent_loop_splices_history_between_system_and_task():
    """Multi-turn memory (added for this chat) stays real chat turns.

    Folding a transcript into the system prompt would be the easy way to add
    memory and the wrong one: system-role text is the one channel the model
    is told to trust, and a visitor's earlier message must never arrive
    through it — the same rule the document agent follows for OCR'd text.
    """
    client = scripted(says("ok"))
    await run_agent_loop(
        BOOKING_AGENT, task="what about tomorrow?",
        history=[
            {"role": "user", "content": "I want Dr. Hoxha"},
            {"role": "assistant", "content": "Which day works for you?"},
        ],
        client=client,
    )
    sent = client.turns[0]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "I want Dr. Hoxha"
    assert sent[2]["content"] == "Which day works for you?"
    assert sent[3]["content"] == "what about tomorrow?"


def test_prior_turns_drops_tool_messages_and_blanks():
    history = [
        {"role": "user", "content": "book me a slot"},
        {"role": "assistant", "content": "sure, which day?"},
        {"role": "tool", "content": "should never be replayed"},
        {"role": "user", "content": "   "},
    ]
    assert _prior_turns(history) == [
        {"role": "user", "content": "book me a slot"},
        {"role": "assistant", "content": "sure, which day?"},
    ]


# --------------------------------------------------------------------- triage

def test_emergency_terms_are_detected():
    assert triage.is_emergency("I'm having severe chest pain and can't breathe")
    assert triage.is_emergency("This is an emergency, please help")
    assert not triage.is_emergency("I'd like a general checkup next week")


def test_emergency_detection_matches_whole_words_only():
    """"urgent" inside another word must not fire — only the standalone term."""
    assert not triage.is_emergency("I found this clinic on urgentcarefinder.com")
    assert triage.is_emergency("this is urgent, please help me")


def test_specialty_for_uses_the_mapped_specialty_when_active():
    with patch("app.services.triage.active_specialties", return_value=["General Practice", "Pediatrics"]):
        assert triage.specialty_for("child_health") == "Pediatrics"


def test_specialty_for_falls_back_when_the_mapped_specialty_has_no_active_doctor():
    """A clinic that no longer has a paediatrician still has to be bookable."""
    with patch("app.services.triage.active_specialties", return_value=["General Practice"]):
        assert triage.specialty_for("child_health") == "General Practice"


def test_specialty_for_unknown_reason_falls_back_too():
    with patch("app.services.triage.active_specialties", return_value=["General Practice"]):
        assert triage.specialty_for("something_the_model_made_up") == "General Practice"


# ---------------------------------------------------------- propose_booking

def test_propose_booking_requires_a_contact_method(clinic_db):
    result = propose_booking(first_name="Arta", last_name="Berisha", staff_id="s-1", scheduled_at=MONDAY_10)
    assert result["proposed"] is False
    assert "phone number or email" in result["error"]


def test_propose_booking_requires_a_last_name(clinic_db):
    result = propose_booking(first_name="Arta", last_name="", staff_id="s-1",
                              scheduled_at=MONDAY_10, phone="044111222")
    assert result["proposed"] is False
    assert "last name" in result["error"]


def test_propose_booking_refuses_a_slot_the_doctor_does_not_work(clinic_db):
    """Same two-check rule as the staff appointment agent: a free slot is not
    the same as a slot the doctor actually works."""
    result = propose_booking(first_name="Arta", last_name="Berisha", staff_id="s-1",
                              scheduled_at=SUNDAY_10, phone="044111222")
    assert result["proposed"] is False
    assert result["error"] == "The doctor does not work that slot."
    assert result["open_slots"] == []


def test_propose_booking_proposes_a_workable_slot_without_touching_patients(clinic_db):
    result = propose_booking(first_name="Arta", last_name="Berisha", staff_id="s-1",
                              scheduled_at=MONDAY_10, phone="044111222", reason="general_checkup")
    assert result["proposed"] is True
    assert result["action"]["action"] == "create_booking"
    assert result["action"]["staff_name"] == "Dr. Arben Hoxha"
    assert result["action"]["first_name"] == "Arta"
    assert "pending clinic confirmation" in result["description"]
    # Never touches `patients` — matching/creation happens only after the
    # visitor confirms, in `public_orchestrator.resume_public_booking`.
    assert clinic_db.table("patients").insert.call_count == 0


def test_propose_booking_refuses_an_unknown_doctor(clinic_db):
    clinic_db.table.side_effect = lambda name: table_chain([]) if name == "staff" else make_chain([])
    result = propose_booking(first_name="Arta", last_name="Berisha", staff_id="s-does-not-exist",
                              scheduled_at=MONDAY_10, phone="044111222")
    assert result["proposed"] is False
    assert "No active doctor" in result["error"]


# --------------------------------------------------------------- patient_matching

def test_match_or_create_reuses_on_email_match():
    existing = {"id": "p-9", "code": "P009", "first_name": "Arta", "last_name": "B",
                "phone": None, "email": "arta@test.com"}
    db = MagicMock()
    db.table.side_effect = lambda name: make_chain([existing]) if name == "patients" else make_chain([])
    with patch_db(db):
        result = match_or_create_patient("Arta", "Berisha", phone="", email="ARTA@test.com")
    assert result["created"] is False
    assert result["patient"]["id"] == "p-9"


def test_match_or_create_reuses_on_phone_match_ignoring_punctuation():
    existing = {"id": "p-9", "code": "P009", "first_name": "Arta", "last_name": "B",
                "phone": "+383-44-100002", "email": None}
    db = MagicMock()
    db.table.side_effect = lambda name: make_chain([existing]) if name == "patients" else make_chain([])
    with patch_db(db):
        result = match_or_create_patient("Arta", "Berisha", phone="+383 44 100002", email="")
    assert result["created"] is False
    assert result["patient"]["id"] == "p-9"


def test_match_or_create_creates_when_nothing_matches():
    new_patient = {"id": "p-new", "code": "P050"}
    patients_chain = make_chain([new_patient])
    db = MagicMock()
    db.table.side_effect = lambda name: patients_chain if name == "patients" else make_chain([])
    with patch("app.services.patient_matching._next_patient_code", return_value="P050"), patch_db(db):
        result = match_or_create_patient("Blerim", "Gashi", phone="044999888", email="")
    assert result["created"] is True
    inserted = patients_chain.insert.call_args.args[0]
    assert inserted["source"] == "patient_portal"
    assert inserted["created_by"] is None
    assert inserted["code"] == "P050"


# ---------------------------------------------------------- public_orchestrator

@pytest.mark.asyncio
async def test_emergency_input_short_circuits_before_any_model_call():
    from app.agents import public_orchestrator as po

    with patch("app.agents.public_orchestrator.run_agent_loop") as loop, \
         patch.object(po, "_log_step", return_value="step-1"), \
         patch.object(po, "_update_run") as update:
        await po.run_booking_agent("run-1", "sess-1", "I'm having chest pain and can't breathe")

    loop.assert_not_called()
    assert update.call_args.kwargs["status"] == "completed"
    assert update.call_args.kwargs["result"]["message"] == triage.EMERGENCY_MESSAGE


@pytest.mark.asyncio
async def test_a_workable_booking_opens_a_gate_and_writes_nothing(clinic_db):
    from app.agents import public_orchestrator as po

    client = scripted(calls(tool_call(
        "c1", "propose_booking", first_name="Arta", last_name="Berisha",
        staff_id="s-1", scheduled_at=MONDAY_10, phone="044111222",
    )))
    with patch("app.agents.runtime.get_client", return_value=client), \
         patch.object(po, "_log_step", return_value="step-1"), \
         patch.object(po, "_create_gate", return_value="gate-1") as gate, \
         patch.object(po, "_update_run") as update, \
         patch.object(po, "_history_for_session", return_value=[]):
        await po.run_booking_agent("run-2", "sess-1", "book me with Dr. Hoxha Monday at 10")

    gate.assert_called_once()
    assert update.call_args.kwargs["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_approved_booking_matches_the_patient_and_lands_as_proposed():
    from app.agents import public_orchestrator as po

    gate = {
        "id": "gate-1", "run_id": "run-3",
        "action_description": "Booking request for Arta Berisha with Dr. Arben Hoxha",
        "payload": {
            "action": "create_booking", "first_name": "Arta", "last_name": "Berisha",
            "phone": "044111222", "email": "", "staff_id": "s-1", "staff_name": "Dr. Arben Hoxha",
            "scheduled_at": MONDAY_10, "duration_min": 30, "reason": "general_checkup", "notes": "",
        },
    }
    created_appt = {"id": "appt-new", "status": "proposed", "source": "patient_portal"}
    appts_chain = make_chain([created_appt])
    db = MagicMock()
    db.table.side_effect = lambda name: appts_chain if name == "appointments" else make_chain([])

    with patch.object(po, "_get_gate", return_value=gate), \
         patch.object(po, "_log_step", return_value="step-1"), \
         patch.object(po, "_update_run") as update, \
         patch.object(po, "log_action") as audit, \
         patch.object(po, "match_or_create_patient",
                       return_value={"patient": {"id": "p-new"}, "created": True}) as matcher, \
         patch_db(db):
        await po.resume_public_booking("run-3", "gate-1", "approved")

    matcher.assert_called_once_with(first_name="Arta", last_name="Berisha", phone="044111222", email="")
    inserted = appts_chain.insert.call_args.args[0]
    assert inserted["status"] == "proposed"
    assert inserted["source"] == "patient_portal"
    assert inserted["created_by"] is None
    assert inserted["patient_id"] == "p-new"
    audit.assert_called_once()
    assert update.call_args.kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_rejected_decision_writes_nothing():
    from app.agents import public_orchestrator as po

    with patch.object(po, "_update_run") as update, \
         patch.object(po, "match_or_create_patient") as matcher:
        await po.resume_public_booking("run-4", "gate-x", "rejected")

    matcher.assert_not_called()
    assert update.call_args.kwargs["status"] == "completed"
    assert "wasn't booked" in update.call_args.kwargs["result"]["message"] \
        or "nothing was booked" in update.call_args.kwargs["result"]["message"].lower()


def test_history_for_session_only_replays_completed_runs():
    from app.agents.public_orchestrator import _history_for_session

    rows = [
        {"id": "r1", "input_text": "hi", "result": {"message": "hello!"}, "status": "completed"},
        {"id": "r2", "input_text": "still thinking", "result": None, "status": "running"},
    ]
    db = MagicMock()
    db.table.side_effect = lambda name: table_chain(rows) if name == "agent_runs" else make_chain([])
    with patch_db(db):
        history = _history_for_session("sess-1", exclude_run_id="r3")

    assert history == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello!"}]


# --------------------------------------------------------------- rate limiting

class _FakeRequest:
    def __init__(self, ip: str):
        self.headers = {}
        self.client = type("C", (), {"host": ip})()


def test_rate_limit_blocks_once_the_windows_budget_is_used():
    from app.core import ratelimit
    ratelimit._hits.clear()
    req = _FakeRequest("1.2.3.4")
    for _ in range(ratelimit.MAX_REQUESTS):
        ratelimit.enforce_rate_limit(req)
    with pytest.raises(HTTPException) as exc:
        ratelimit.enforce_rate_limit(req)
    assert exc.value.status_code == 429


def test_rate_limit_is_scoped_per_ip():
    from app.core import ratelimit
    ratelimit._hits.clear()
    a, b = _FakeRequest("1.1.1.1"), _FakeRequest("2.2.2.2")
    for _ in range(ratelimit.MAX_REQUESTS):
        ratelimit.enforce_rate_limit(a)
    ratelimit.enforce_rate_limit(b)  # a different IP is unaffected


# ------------------------------------------------------------------ the API layer

@pytest.fixture
def public_client():
    """A raw, unauthenticated TestClient — the point of `/public/*`."""
    run_row = {
        "id": "run-1", "session_id": "sess-A", "channel": "patient",
        "input_text": "hi", "status": "completed", "result": {"message": "ok"},
        "intent": None, "user_id": None, "created_at": None, "completed_at": None,
    }
    gate_row = {
        "id": "gate-1", "run_id": "run-1", "step_id": "s-1",
        "action_description": "desc", "payload": {}, "status": "pending",
    }
    tables = {
        "agent_runs": table_chain([run_row], single=run_row),
        "approval_gates": table_chain([gate_row], single=gate_row),
        "agent_steps": make_chain([]),
    }
    db = MagicMock()
    db.table.side_effect = lambda name: tables.get(name, make_chain([]))
    from app.main import app
    with patch_db(db):
        with TestClient(app) as c:
            yield c, db


def test_public_endpoints_need_no_authorization_header(public_client):
    c, _ = public_client
    r = c.get("/public/doctors")
    assert r.status_code == 200  # never 401/403 — this router has no auth dependency


def test_starting_a_chat_stores_the_session_id_and_no_user_id(public_client):
    c, db = public_client
    with patch("app.api.public.spawn") as spawn_mock:
        r = c.post("/public/booking/chat", json={"session_id": "visitor-42", "message": "hi there"})
    assert r.status_code == 201
    spawn_mock.assert_called_once()
    # `spawn` is mocked, so the coroutine it was handed never actually runs —
    # close it explicitly rather than leaving it for the garbage collector to
    # complain about on some unrelated later test.
    spawn_mock.call_args.args[0].close()
    inserted = db.table("agent_runs").insert.call_args.args[0]
    assert inserted["session_id"] == "visitor-42"
    assert inserted["user_id"] is None
    assert inserted["channel"] == "patient"


def test_starting_a_chat_requires_a_nonempty_message(public_client):
    c, _ = public_client
    r = c.post("/public/booking/chat", json={"session_id": "s", "message": "   "})
    assert r.status_code == 400


def test_starting_a_chat_requires_a_session_id(public_client):
    c, _ = public_client
    r = c.post("/public/booking/chat", json={"session_id": "", "message": "hi"})
    assert r.status_code == 400


def test_wrong_session_cannot_read_someone_elses_run_stream(public_client):
    """Scenario S24: a mismatched session_id is indistinguishable from a
    nonexistent run — both come back 404."""
    c, _ = public_client
    r = c.get("/public/booking/runs/run-1/stream", params={"session_id": "sess-B"})
    assert r.status_code == 404


def test_wrong_session_cannot_confirm_someone_elses_gate(public_client):
    c, _ = public_client
    with patch("app.api.public.spawn"):
        r = c.post("/public/booking/confirm/gate-1", json={"session_id": "sess-B", "decision": "approved"})
    assert r.status_code == 404


def test_owning_session_can_confirm_its_own_gate(public_client):
    c, _ = public_client
    with patch("app.api.public.spawn") as spawn_mock:
        r = c.post("/public/booking/confirm/gate-1", json={"session_id": "sess-A", "decision": "approved"})
    assert r.status_code == 200
    spawn_mock.assert_called_once()
    spawn_mock.call_args.args[0].close()  # never actually run — spawn is mocked


def test_a_gate_already_decided_cannot_be_decided_again(public_client):
    c, db = public_client
    decided_gate = {**db.table("approval_gates").execute().data[0], "status": "approved"}
    db.table.side_effect = lambda name: (
        table_chain([decided_gate], single=decided_gate) if name == "approval_gates"
        else table_chain([{
            "id": "run-1", "session_id": "sess-A", "channel": "patient", "input_text": "hi",
            "status": "completed", "result": {}, "intent": None, "user_id": None,
            "created_at": None, "completed_at": None,
        }]) if name == "agent_runs"
        else make_chain([])
    )
    with patch("app.api.public.spawn"):
        r = c.post("/public/booking/confirm/gate-1", json={"session_id": "sess-A", "decision": "approved"})
    assert r.status_code == 409
