# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Clinic Multi-Agent System — a diploma-thesis prototype for administrative clinic workflows (appointments, patient records, document intake, operational reporting). Four autonomous GPT-4o agents, each running its own tool-calling loop, coordinated by a supervisor over a cyclic LangGraph, with a human approval gate on every write an agent proposes. Backend: FastAPI + LangGraph. Frontend: Next.js 14 (App Router). DB/Auth: Supabase (Postgres + Auth).

The division of labour is the design's core claim: **agents decide, deterministic Python validates, humans approve.** GPT-4o chooses which tools to call and what to propose; conflict detection, schedule validation, id resolution, field whitelisting and report arithmetic are plain Python with no LLM involvement; nothing an agent proposes touches the database until a human accepts the gate.

## Commands

All commands below are run from `clinic-system/` unless noted.

### Backend (FastAPI)
```bash
cd backend
pip install -r requirements.txt
pytest tests/ -v                        # full suite (177 tests)
pytest tests/test_appointments.py -v    # single file
pytest tests/test_approval_gates.py -k test_gate_cannot_be_decided_twice -v  # single test
```
Tests run fully mocked — no live Supabase connection or `.env` needed to run the suite; dummy env vars are set via `os.environ.setdefault` at import time. `tests/conftest.py` exposes two shared helpers that all test modules should use:
- `patch_db(mock_db)` — a context manager that patches `app.core.database.create_client`, **not** `get_db`. Routers do `from app.core.database import get_db`, which binds the name at import time, so patching `database.get_db` leaves those bindings pointing at the real function. The patch must stay open while the request runs, so helpers that build a `TestClient` are context managers (`with make_client(...) as c:`), never plain functions that return a client from inside a `with` block.
- `make_chain(data)` — a chainable postgrest query mock whose `.execute()` returns `data`. Pass a **dict** when the query under test ends in `.maybe_single()` and a **list** otherwise; mock rows must include every column the response model declares. Note `.not_` is a property (`.not_.in_(...)`), so it resolves back to the chain rather than being a call.

### Frontend (Next.js)
```bash
cd frontend
npm install
npm run dev      # http://localhost:3000
npm run build
npm run lint
```

### Full stack (Docker)
```bash
docker compose up --build
```
Backend on :8000 (`/docs` for OpenAPI UI), frontend on :3000. Requires `.env` at repo root (copy `.env.example`) with `OPENAI_API_KEY` and Supabase credentials; `SUPABASE_JWT_SECRET` comes from Supabase Dashboard → Settings → API.

### Database
Schema and seed data live in `supabase/migrations/` — apply by pasting into the Supabase SQL Editor in filename order (no migration CLI/runner wired up). `002_uniform_working_hours.sql` resets every doctor to Mon–Fri 09:00–17:00; it exists because the original seed gave each doctor different days, so booking on an unscheduled weekday returned no slots. See `SETUP.md` for the full first-run checklist (create project, run migration, invite a user, assign a role via `user_roles`).

## Architecture

### Supervisor / autonomous-agent pattern (`backend/app/agents/`)
`orchestrator.py` builds a **cyclic** LangGraph `StateGraph` (`OrchestratorState` TypedDict), the single entry point for all natural-language requests (`POST /agent/run`):

```
supervisor ─┬─→ appointment_agent ─┐
            ├─→ patient_agent ─────┤
            ├─→ document_agent ────┼─→ (handed off? answered?) ─┬─→ supervisor
            ├─→ reporting_agent ───┘                            └─→ finalize → END
            └─→ fallback ──────────────────────────────────────────→ finalize
```

1. `node_supervisor` picks **which** agent works next and writes its task; it never decides *how* the work is done. It returns `{agent, task, reason}` — an unrecognized agent name is coerced to `fallback` rather than becoming a graph node name.
2. Each agent node runs `runtime.run_agent_loop(spec, ...)`: a ReAct loop where GPT-4o is given that agent's own tool schemas and chooses, turn by turn, which tool to call and when it is done. **Nothing scripts the tool sequence.** Each result is fed back as a `tool` message before the next decision.
3. An agent whose task belongs to a peer calls `handoff_to_<agent>`, which ends its loop and returns control to the supervisor. On a handoff the supervisor **skips the model entirely** and routes to the named target — re-deciding is how two agents that each think the task is the other's ping-pong until the hop limit fires.
4. Every LLM turn and tool call is recorded via `_log_step` into `agent_steps` (`agent_runs` row per invocation), so a multi-agent run is fully traceable.
5. `MAX_HOPS` (4) caps agent visits per run; `runtime.DEFAULT_MAX_ITERATIONS` (6) caps turns within one agent's loop. Both are what make a cyclic graph terminate.

When adding an agent: define an `AgentSpec` in its own module, add it to `AGENT_NAMES` and `_spec()` in `orchestrator.py`, add the graph node and its `_after_agent` conditional edges, and give peers a `handoff_tool` pointing at it. `_directory()` builds the supervisor's routing prompt from each spec's `purpose`, so that one line is what routing quality depends on.

### The approval-gate invariant (`runtime.py`)
Agent autonomy stops at the database, and this is enforced structurally rather than by prompt:

- Every function that writes is decorated `@mutating`, which sets `__mutates_db__`. `assert_no_write_tools(spec)` runs at the top of every `run_agent_loop` and raises `WriteToolExposed` if any tool set contains one. `tests/test_agent_autonomy.py::test_no_agent_exposes_a_write_tool` pins it for all four agents.
- Writes reach a model only as `propose_*` tools (`kind="proposal"`). A proposal returns `{"proposed": True, "action": {...}, "description": ...}` and **ends the agent's loop**; the node opens an `approval_gates` row and the run stops at `awaiting_approval`. A proposal returning `{"proposed": False, "error": ...}` is fed back as an ordinary tool result so the agent can replan — that is how the user gets offered alternative slots instead of an error.
- Proposal validators **re-derive every fact from the database** rather than trusting the model's arguments: ids must resolve to real rows, the slot is re-checked against conflicts *and* the doctor's schedule, and the human-readable gate description is built from the rows just read. The proposal schemas deliberately have no `patient_name`/`staff_name`/`code` parameters, so a model cannot influence what a human sees on the approval card.
- `resume_orchestrator` — reached only from `POST /agent/approve/{gate_id}` after a human decides — is the *only* code path that calls a `@mutating` function, and it writes an `audit_logs` entry for each.

Follow this shape (propose → validate → gate → `resume_orchestrator` executes) for any new write-capable agent action. It is a core thesis requirement, not incidental.

### Tool schemas are a contract (`runtime.ToolSpec`)
A tool's JSON-schema property names must match its function's parameter names. A mismatch does not crash: the runtime returns "Wrong arguments" to the model, which re-reads the same schema and makes the same call until the iteration cap — a tool that silently never works. `test_every_tool_schema_matches_the_function_it_calls` checks this for every tool by `inspect.signature`, and it is why `tool_check_patient` takes `query` and `tool_check_staff` takes `name`.

### Resolving names from agent input (`appointment_agent.py`)
Agents pass names the way people say them, so lookups must tolerate it: `tool_check_patient` tries the `Pnnn` code then falls back to a name search (accepting a name **only** when it matches exactly one patient — ambiguous matches come back with `candidates` for the user to disambiguate, rather than booking the wrong person). `tool_check_staff` falls back to requiring every non-title token to appear in `full_name`, because "Dr. Hoxha" is not a contiguous substring of "Dr. Arben Hoxha" and a single `ilike` misses it.

Dates and times get the same treatment: every agent's context supplies today's clinic date and the schemas ask for `YYYY-MM-DDTHH:MM`, but GPT-4o still returns "tomorrow" and "10am" often enough that `schedule_service.parse_when` / `parse_clinic_datetime` normalize both spellings instead of letting `fromisoformat` kill the booking. (These live in `schedule_service` rather than the orchestrator so agent modules can use them without an import cycle; `orchestrator` re-exports them.)

`_slot_is_workable` — used by both `propose_create_appointment` and `propose_reschedule_appointment` — runs `check_conflict` **and** `get_available_slots`, not just the conflict check: an empty Sunday has no conflicts, so conflict-freedom alone let an agent propose a doctor outside their working days when the manual form (which offers only `get_available_slots`) could not. The reschedule path passes `exclude_appointment_id` or the appointment conflicts with itself.

### Document intake security pattern (`document_agent.py`)
Uploaded document text is always passed to GPT-4o as **user**-role content, never system-role, and is explicitly framed as "data only" in the prompt — this is the injection-prevention pattern for untrusted OCR'd text. Field extraction and summarization prompts explicitly forbid returning medical diagnoses/symptoms/medications — only administrative fields (names, dates, policy numbers, etc.) are extracted. Preserve both properties (user-role placement, medical-content exclusion) when touching this file.

### Agent test strategy (`tests/test_agent_autonomy.py`)
Only the OpenAI calls are scripted; the tools run for real against a mocked Supabase, so the scheduling logic, id checks and proposal validators under test are the production ones. Two helpers make this work and are worth reusing:

- `scripted(*responses)` returns a client that also **deep-copies the messages it was sent each turn** into `client.turns`. The runtime appends to one list as the loop runs and `call_args_list` records it by reference, so asserting on `call_args_list` silently checks the *final* conversation — which turns "was this fed back before the next decision?" into an assertion that always passes.
- `conftest.table_chain(rows, single=)` — `.maybe_single()` yields one row while other terminals yield the list, for the tables agent proposals query both ways (by id, and by name search). `make_chain` fixes a single return value and breaks on those paths.
- `tool_call(call_id, name, /, **args)` is positional-only because `find_staff` takes an argument literally called `name`.

Add agent behaviour tests here (autonomy, handoff, validators), gate tests to `test_approval_gates.py`.

### Scenario-driven test suite
`backend/tests/scenarios.json` holds 20 scenario fixtures (id, input, expected_intent/sub_intent, `requires_approval`, expected_outcome, optional `security_note`). `test_scenarios.py` asserts structural invariants across all of them (every write scenario requires approval, every read/report scenario doesn't, at least one scenario each for prompt-injection, medical-advice-blocking, unauthorized-access, and conflict-detection). When adding a new agent behavior, add a corresponding scenario entry rather than only unit-testing the code path.

### Staff administration & manual booking (`backend/app/api/staff.py`)
Two paths write appointments, and they are deliberately different:
- **Agent path** (`POST /agent/run`) — proposes, opens an approval gate, executes only on approval. See above.
- **Manual path** (`POST /appointments/` and `PATCH /appointments/{id}`, used by the "New Appointment" / "Edit" form) — admins and receptionists write directly, no gate. The gate guards writes an *agent* proposed on a user's behalf; a human filling in a form is already the human in the loop. `tests/test_staff.py::test_receptionist_can_book_appointment_manually` asserts no `approval_gates` row is touched here.

**Every path books at `status="confirmed"`, overriding the column's `'proposed'` default** — the manual form (`appointments.create_appointment`), the agent path after its gate (`appointment_agent.tool_create_appointment`), and the public booking chat after the visitor confirms (`public_orchestrator.resume_public_booking`). In each case a human has already agreed to the slot and deterministic Python has already re-checked it against conflicts and the doctor's schedule, so a second review step was asking the same person to accept their own booking twice. `'proposed'` now only appears on rows written before this and on the transition `proposed → confirmed|cancelled`, which stays in `VALID_TRANSITIONS` to settle them. Public bookings keep `source="patient_portal"`, which is what the "Online Bookings" tab on `/appointments` filters on — that tab is visibility, not an approval queue.

`PATCH /appointments/{id}` accepts `patient_id`, `staff_id`, `scheduled_at`, `duration_min`, `service_id`, `notes` and `status`. Changing the doctor, time, or duration all move the appointment's footprint, so any of them re-runs `check_conflict` against the **new** values with `exclude_appointment_id` set — otherwise the appointment conflicts with itself. Editing only notes/service skips the check entirely.

`POST /staff/` is admin-only and is the one place accounts are provisioned. Given `email` + `password` it creates a Supabase Auth user via `db.auth.admin.create_user` and grants the role by inserting into `user_roles` (role id looked up by name, never hardcoded). `role: "doctor"` additionally inserts a `staff` row — doctors are the bookable resource appointments reference — plus `schedules` rows from `work_days`/`start_time`/`end_time`. `role: "receptionist"` creates the login only; receptionists are not bookable so they get no `staff` row, which is why the Staff page lists them under "Login accounts" (`GET /staff/accounts`) rather than in the staff table.

Staff deletion is a soft delete (`active = false`) because `appointments.staff_id` references them. A doctor with no `schedules` rows produces zero available slots — the booking form surfaces this rather than failing silently.

### Time handling (`backend/app/services/schedule_service.py`)
`appointments.scheduled_at` is `TIMESTAMPTZ`, so a **naive** datetime handed to Postgres is silently read as UTC — that is how a 10:00 booking came back displaying as 12:00. Rules, all enforced by tests in `test_appointments.py`:
- `CLINIC_TIMEZONE` (default `Europe/Tirane` — IANA has no `Europe/Pristina`; Tirane carries the CET/CEST offsets and EU DST rules Kosovo observes) is the wall-clock zone. `schedules.start_time`/`end_time` and generated slots are interpreted in it.
- `to_clinic(dt)` reads a naive datetime as clinic wall-clock and converts an aware one. **Every datetime crossing the DB boundary must go through it** — that includes the agent path (`appointment_agent._clinic_instant`), not just the REST endpoints.
- Slot strings are returned with an offset (`2026-08-03T10:00:00+02:00`). Compare times as instants (`datetime` objects, or `Date.getTime()` in the frontend), never as ISO strings — the same moment has several valid spellings.
- Overlap is computed on **both** endpoints (`existing.start < new.end and existing.end > new.start`). A range query on `scheduled_at` alone misses an existing long appointment that starts before the new one.
- `get_available_slots` only offers a start time if the whole `duration_min` fits inside the working block and clears every booked interval, so a 60-minute service is not slotted into the last 30 minutes of a shift.
- A doctor with **no `schedules` rows at all** falls back to the clinic defaults (`DEFAULT_WORK_DAYS`/`DEFAULT_WORK_START`/`DEFAULT_WORK_END`, Mon–Fri 09:00–17:00). The fallback is all-or-nothing: once a doctor has any row, only their own rows apply, so a doctor deliberately set to Tue/Thu never sprouts hours they didn't agree to.

### Auth & roles (`backend/app/core/auth.py`)
JWTs are Supabase-issued and verified locally against `SUPABASE_JWT_SECRET` (HS256, `verify_aud` disabled). Roles are looked up per-request from the `user_roles`/`roles` tables (not embedded in the JWT). Endpoints declare required roles via `require_roles("admin", "receptionist")` as a FastAPI dependency — there are three roles: `admin`, `receptionist`, `doctor`. Ownership splits by role, and the split is asserted in `tests/test_auth.py`:
- **Patient register writes are `receptionist`-only** — not admin. The front desk owns patient records; admins manage staff and accounts. All three roles can *read* patients. The booking form hides its "+ New patient" control for non-receptionists to match.
- **Staff/account writes are `admin`-only.**
- **Appointment writes are `admin` + `receptionist`.** Doctors are read-only throughout. All Supabase access from the backend uses the **service-role** key (`app/core/database.py`), so authorization is enforced entirely in the FastAPI layer, not via Postgres RLS from these endpoints.
- **Reads are all three roles — including reports.** `/reports/summary` and `/reports/generate` are reads (the generate path writes only its own `audit_logs` entry), so `doctor` belongs in their `require_roles` alongside every other read endpoint. It was missing there, and because the Reports nav item carries no `roles` key in `Sidebar.tsx`, doctors saw the page, had the `summary` 403 swallowed by its `.catch(() => setSummary(null))`, and read every stat as `0`. **A role gate that is stricter than the nav that links to it does not restrict the page, it breaks it** — when tightening one, check the other. `/reports/audit-log` stays `admin`-only: it is oversight, not an operational report.

### Supabase connection flakiness (`app/core/database.py`)
postgrest-py hardcodes `http2=True` and Supabase sends GOAWAY on idle connections, so a pooled connection reused just after that dies mid-request as `httpx.RemoteProtocolError: <ConnectionTerminated>`. It looks like an application bug but is pure transport noise, and it hits whichever query happens to run third or fourth — reports were failing this way because `report_service` issued four queries per generation without a guard.

**Wrap every postgrest query in `execute_with_retry(...)`** rather than calling `.execute()` directly. Pass the builder, not the result: `execute_with_retry(db.table("x").select("*").eq(...))`. It retries the transient transport errors only and re-raises anything else on the first attempt, so real query bugs still surface immediately.

It also normalizes the other postgrest sharp edge: **`.maybe_single()` returns a bare `None`, not a response with `data=None`**, when nothing matches. `resp.data` on that raises `AttributeError: 'NoneType' object has no attribute 'data'` — a 500 where the caller meant to return 404. `execute_with_retry` substitutes a `_NoRow` sentinel so `if not resp.data:` behaves as written. Never call `.maybe_single().execute()` directly.

### Audit logging
`app/core/audit.py::log_action(user_id, action, entity_type, entity_id, details)` writes to `audit_logs`. Called directly from API routes for document verify/reject and gate decisions, and from `orchestrator.resume_orchestrator` after executing an approved action. Any new mutating endpoint or approved-action branch should call this too.

### Reporting (`reporting_agent.py` / `report_service.py`)
Fully deterministic — no LLM involved. Date ranges are inclusive of **both** endpoints: `_day_bounds` turns `date_to` into the *following* clinic-local midnight and queries `gte(start) / lt(end)`. Comparing against `date_to` directly resolves to 00:00 on that day and silently drops everything scheduled during the final day of the range — the bug `test_range_includes_the_whole_final_day` pins. Produces appointment summaries, no-show reports, and missing-document reports, rendered to PDF (`reportlab`) or CSV. Reachable both via chat (`report` intent → `node_report`) and directly via `POST /reports/generate`.

### Frontend structure
Next.js App Router pages under `frontend/src/app/` map roughly 1:1 to backend routers: `dashboard`, `patients`, `appointments`, `documents`, `reports`, `agent-chat`, `staff` (admin-only), plus `login`. All backend calls go through typed wrapper objects in `frontend/src/lib/api.ts` (`patientsApi`, `appointmentsApi`, `staffApi`, `servicesApi`, `documentsApi`, `reportsApi`, `agentApi`) which attach the Supabase session's access token as a Bearer header via `src/lib/supabase.ts` (`createBrowserClient`). Add new backend calls there rather than calling `fetch` ad hoc from components.
