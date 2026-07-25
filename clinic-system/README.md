# Clinic Multi-Agent System

A diploma-thesis prototype for administrative clinic workflows — appointments, patient records,
document intake, and operational reporting — built on a multi-agent orchestration pattern with a
human-in-the-loop approval gate for every write operation.

## Stack

- **Backend**: FastAPI + LangGraph (Python)
- **Frontend**: Next.js 14 (App Router)
- **Database / Auth**: Supabase (Postgres + Auth)
- **LLM**: OpenAI GPT-4o — used strictly for language tasks (intent classification, document
  classification/extraction/summarization), never for business logic or decisions that write data

## Key design principles

- **Approval gates**: any write *an agent proposes* (create/cancel/reschedule appointment,
  create/update patient) is stored as a pending `approval_gates` row and only executed after a
  human approves it via the UI. Reads and reports execute immediately. Writes a human performs
  directly through a form — manual booking, staff creation — are not gated: the human is already
  in the loop.
- **Role-scoped administration**: admins create doctors and receptionist accounts from the Staff
  page (which provisions the Supabase Auth login and assigns the role); admins and receptionists
  book appointments manually against a doctor's working hours; doctors are read-only.
- **Deterministic business logic**: conflict detection, status transitions, and reporting are plain
  Python — zero LLM involvement.
- **Injection-safe document intake**: OCR'd document text is always sent to GPT-4o as `user`-role
  content (never `system`), and extraction is restricted to administrative fields — no medical
  diagnoses, symptoms, or medications are ever extracted.
- **Full audit trail**: every agent step and every approved/rejected action is logged
  (`agent_steps`, `agent_runs`, `audit_logs`).

## Project structure

```
clinic-system/
├── backend/            FastAPI app, LangGraph orchestrator, agents, tests
│   └── app/
│       ├── agents/     orchestrator + per-domain agents (appointment, patient, document, reporting)
│       ├── api/        FastAPI routers
│       ├── core/       auth, audit, config, database
│       ├── models/     Pydantic schemas
│       └── services/   OCR, scheduling, PDF/CSV report generation
├── frontend/            Next.js App Router UI
│   └── src/app/         dashboard, patients, appointments, staff, documents, reports, agent-chat, login
├── supabase/
│   └── migrations/       schema + seed SQL
└── docker-compose.yml
```

## Quick start

```bash
cp .env.example .env   # fill in OPENAI_API_KEY + Supabase credentials
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API docs: http://localhost:8000/docs

For first-time Supabase project setup, environment variables, and a walkthrough of the demo
workflows (staff setup + manual booking, agent appointment request, document intake, weekly
report), see [SETUP.md](./SETUP.md).

## Development

**Backend**
```bash
cd backend
pip install -r requirements.txt
pytest tests/ -v
```
Tests run fully mocked — no live Supabase connection or `.env` required.

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

## Documentation

- [SETUP.md](./SETUP.md) — first-run checklist and demo workflows
- [CLAUDE.md](./CLAUDE.md) — architecture deep-dive (orchestrator pattern, auth/roles, audit
  logging, scenario-driven test suite)
