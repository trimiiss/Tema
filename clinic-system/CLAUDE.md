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
Tests run fully mocked (`tests/conftest.py` patches `app.core.database.get_db` / `app.core.auth.get_db` with a chainable `MagicMock`) — no live Supabase connection or `.env` needed to run the suite; dummy env vars are set via `os.environ.setdefault` at import time.

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
Schema and seed data live in `supabase/migrations/001_initial.sql` — apply by pasting into the Supabase SQL Editor (no migration CLI/runner wired up). See `SETUP.md` for the full first-run checklist (create project, run migration, invite a user, assign a role via `user_roles`).

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

### Auth & roles (`backend/app/core/auth.py`)
JWTs are Supabase-issued and verified locally against `SUPABASE_JWT_SECRET` (HS256, `verify_aud` disabled). Roles are looked up per-request from the `user_roles`/`roles` tables (not embedded in the JWT). Endpoints declare required roles via `require_roles("admin", "receptionist")` as a FastAPI dependency — there are three roles: `admin`, `receptionist`, `doctor`. All Supabase access from the backend uses the **service-role** key (`app/core/database.py`), so authorization is enforced entirely in the FastAPI layer, not via Postgres RLS from these endpoints.

### Audit logging
`app/core/audit.py::log_action(user_id, action, entity_type, entity_id, details)` writes to `audit_logs`. Called directly from API routes for document verify/reject and gate decisions, and from `orchestrator.resume_orchestrator` after executing an approved action. Any new mutating endpoint or approved-action branch should call this too.

### Reporting (`reporting_agent.py` / `report_service.py`)
Fully deterministic — no LLM involved. Produces appointment summaries, no-show reports, and missing-document reports, rendered to PDF (`reportlab`) or CSV. Reachable both via chat (`report` intent → `node_report`) and directly via `POST /reports/generate`.

### Frontend structure
Next.js App Router pages under `frontend/src/app/` map roughly 1:1 to backend routers: `dashboard`, `patients`, `appointments`, `documents`, `reports`, `agent-chat`, plus `(auth)/login` and `(auth)/register` route group. All backend calls go through typed wrapper objects in `frontend/src/lib/api.ts` (`patientsApi`, `appointmentsApi`, `documentsApi`, `reportsApi`, `agentApi`) which attach the Supabase session's access token as a Bearer header via `src/lib/supabase.ts` (`createBrowserClient`). Add new backend calls there rather than calling `fetch` ad hoc from components.
