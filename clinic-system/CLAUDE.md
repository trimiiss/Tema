# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Clinic Multi-Agent System — a diploma-thesis prototype for administrative clinic workflows (appointments, patient records, document intake, operational reporting) built on a multi-agent orchestration pattern. Backend: FastAPI + LangGraph. Frontend: Next.js 14 (App Router). DB/Auth: Supabase (Postgres + Auth). LLM: OpenAI GPT-4o, used strictly for language tasks (intent classification, document classification/extraction/summarization) — never for business logic or decisions that write data.

## Commands

All commands below are run from `clinic-system/` unless noted.

### Backend (FastAPI)
```bash
cd backend
pip install -r requirements.txt
pytest tests/ -v                        # full suite (30+ tests)
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

### Orchestrator / sub-agent pattern (`backend/app/agents/`)
`orchestrator.py` builds a LangGraph `StateGraph` (`OrchestratorState` TypedDict) that is the single entry point for all natural-language requests (`POST /agent/run`):

1. `node_classify_intent` — the only place a raw user request is classified: GPT-4o returns `{intent, sub_intent, params}` (intent ∈ appointment/patient/document/report).
2. Conditional routing (`_route_intent`) dispatches to one node per intent: `node_appointment`, `node_patient`, `node_document`, `node_report`, or `node_fallback`.
3. Each node calls plain-Python "tool" functions defined in the matching sibling module (`appointment_agent.py`, `patient_agent.py`, `reporting_agent.py`, `document_agent.py`) — these do DB reads/writes and deterministic business logic (conflict detection, missing-field checks). GPT-4o is **not** called again here except inside `document_agent.py`.
4. Every step (LLM call or tool call) is recorded via `_log_step` into `agent_steps` for full traceability of a run (`agent_runs` row per invocation).
5. **Any write operation (create/cancel/reschedule appointment, create/update patient) does not execute directly.** The node builds a `pending_action` payload and inserts an `approval_gates` row (`status: pending`) instead, then returns `status: awaiting_approval`. Read/list operations return `status: completed` immediately with no gate.
6. A human decides via `POST /agent/approve/{gate_id}` (`app/api/agents.py`) — this flips the gate's status, writes an `audit_logs` entry, and calls `resume_orchestrator`, which is the *only* code path allowed to actually call the mutating tool functions (`tool_create_appointment`, `tool_cancel_appointment`, etc.) and then writes another audit log entry.

When adding a new write-capable agent action, follow this same two-phase shape (propose → gate → `resume_orchestrator` executes on approval) rather than performing the write inline in the classify/route node — the approval-gate invariant is enforced by tests (`tests/test_approval_gates.py`, `tests/test_scenarios.py::test_write_operations_all_require_approval`) and is a core thesis requirement, not incidental.

### Document intake security pattern (`document_agent.py`)
Uploaded document text is always passed to GPT-4o as **user**-role content, never system-role, and is explicitly framed as "data only" in the prompt — this is the injection-prevention pattern for untrusted OCR'd text. Field extraction and summarization prompts explicitly forbid returning medical diagnoses/symptoms/medications — only administrative fields (names, dates, policy numbers, etc.) are extracted. Preserve both properties (user-role placement, medical-content exclusion) when touching this file.

### Scenario-driven test suite
`backend/tests/scenarios.json` holds 20 scenario fixtures (id, input, expected_intent/sub_intent, `requires_approval`, expected_outcome, optional `security_note`). `test_scenarios.py` asserts structural invariants across all of them (every write scenario requires approval, every read/report scenario doesn't, at least one scenario each for prompt-injection, medical-advice-blocking, unauthorized-access, and conflict-detection). When adding a new agent behavior, add a corresponding scenario entry rather than only unit-testing the code path.

### Staff administration & manual booking (`backend/app/api/staff.py`)
Two paths write appointments, and they are deliberately different:
- **Agent path** (`POST /agent/run`) — proposes, opens an approval gate, executes only on approval. See above.
- **Manual path** (`POST /appointments/` and `PATCH /appointments/{id}`, used by the "New Appointment" / "Edit" form) — admins and receptionists write directly, no gate. The gate guards writes an *agent* proposed on a user's behalf; a human filling in a form is already the human in the loop. `tests/test_staff.py::test_receptionist_can_book_appointment_manually` asserts no `approval_gates` row is touched here.

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

### Supabase connection flakiness (`app/core/database.py`)
postgrest-py hardcodes `http2=True` and Supabase sends GOAWAY on idle connections, so a pooled connection reused just after that dies mid-request as `httpx.RemoteProtocolError: <ConnectionTerminated>`. It looks like an application bug but is pure transport noise, and it hits whichever query happens to run third or fourth — reports were failing this way because `report_service` issued four queries per generation without a guard.

**Wrap every postgrest query in `execute_with_retry(...)`** rather than calling `.execute()` directly. Pass the builder, not the result: `execute_with_retry(db.table("x").select("*").eq(...))`. It retries the transient transport errors only and re-raises anything else on the first attempt, so real query bugs still surface immediately.

### Audit logging
`app/core/audit.py::log_action(user_id, action, entity_type, entity_id, details)` writes to `audit_logs`. Called directly from API routes for document verify/reject and gate decisions, and from `orchestrator.resume_orchestrator` after executing an approved action. Any new mutating endpoint or approved-action branch should call this too.

### Reporting (`reporting_agent.py` / `report_service.py`)
Fully deterministic — no LLM involved. Date ranges are inclusive of **both** endpoints: `_day_bounds` turns `date_to` into the *following* clinic-local midnight and queries `gte(start) / lt(end)`. Comparing against `date_to` directly resolves to 00:00 on that day and silently drops everything scheduled during the final day of the range — the bug `test_range_includes_the_whole_final_day` pins. Produces appointment summaries, no-show reports, and missing-document reports, rendered to PDF (`reportlab`) or CSV. Reachable both via chat (`report` intent → `node_report`) and directly via `POST /reports/generate`.

### Frontend structure
Next.js App Router pages under `frontend/src/app/` map roughly 1:1 to backend routers: `dashboard`, `patients`, `appointments`, `documents`, `reports`, `agent-chat`, `staff` (admin-only), plus `login`. All backend calls go through typed wrapper objects in `frontend/src/lib/api.ts` (`patientsApi`, `appointmentsApi`, `staffApi`, `servicesApi`, `documentsApi`, `reportsApi`, `agentApi`) which attach the Supabase session's access token as a Bearer header via `src/lib/supabase.ts` (`createBrowserClient`). Add new backend calls there rather than calling `fetch` ad hoc from components.
