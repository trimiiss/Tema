# Clinic Multi-Agent System

A diploma-thesis prototype for administrative clinic workflows — appointments, patient records,
document intake, and operational reporting. Four autonomous agents, each running its own GPT-4o
tool-calling loop, coordinated by a supervisor over a cyclic LangGraph, with a human-in-the-loop
approval gate on every write an agent proposes.

## Stack

- **Backend**: FastAPI + LangGraph (Python)
- **Frontend**: Next.js 14 (App Router)
- **Database / Auth**: Supabase (Postgres + Auth)
- **LLM**: OpenAI GPT-4o — each agent's own reasoning loop, plus document
  classification/extraction/summarization

## Architecture

```
supervisor ─┬─→ appointment_agent ─┐
            ├─→ patient_agent ─────┤
            ├─→ document_agent ────┼─→ (handed off? answered?) ─┬─→ supervisor
            ├─→ reporting_agent ───┘                            └─→ finalize
            └─→ fallback ──────────────────────────────────────────→ finalize
```

The supervisor decides **which** agent works next and writes its task. It never decides **how**:
each agent is given its own tool schemas and chooses turn by turn which tool to call, what to do
with the result, whether to retry differently, and when it is done. An agent whose task belongs to
a peer calls `handoff_to_<agent>` and control returns to the supervisor, so one request can
traverse several agents — "register this patient and book her" is handled by the appointment agent
discovering there is no such patient and delegating, not by anyone scripting that path.

The graph is genuinely cyclic. `MAX_HOPS` (agent visits per run) and a per-agent iteration cap are
what make it terminate.

## Key design principles

- **Agents decide, deterministic Python validates, humans approve.** GPT-4o chooses tools and
  proposals; conflict detection, schedule validation, id resolution, field whitelisting and report
  arithmetic are plain Python with no LLM involvement; nothing reaches the database until a human
  accepts the gate.
- **Approval gates**: any write *an agent proposes* (create/cancel/reschedule appointment,
  create/update patient) is stored as a pending `approval_gates` row and only executed after a
  human approves it via the UI. Reads and reports execute immediately. Writes a human performs
  directly through a form — manual booking, staff creation — are not gated: the human is already
  in the loop.
- **Agents structurally cannot write.** Every mutating function is marked `@mutating`, and
  `assert_no_write_tools` refuses to start an agent loop whose tool set contains one. A model can
  be talked into anything; it cannot call a tool it was never given.
- **Validators do not trust the model.** Proposal tools resolve every id against a real row and
  re-check the slot against both conflicts and the doctor's schedule, so a hallucinated patient id
  or an unworkable time is refused before a human is ever shown a gate. The text on the approval
  card is built from the rows just read — the proposal schemas have no name or code parameters at
  all, so the model cannot influence what a human approves.
- **Role-scoped administration**: admins create doctors and receptionist accounts from the Staff
  page (which provisions the Supabase Auth login and assigns the role); admins and receptionists
  book appointments manually against a doctor's working hours; doctors are read-only.
- **Deterministic business logic**: conflict detection, status transitions, and reporting are plain
  Python — zero LLM involvement.
- **Injection-safe document intake**: OCR'd document text is always sent to GPT-4o as `user`-role
  content (never `system`), and extraction is restricted to administrative fields — no medical
  diagnoses, symptoms, or medications are ever extracted. Every agent additionally carries shared
  rules stating that user and tool text is data, never instructions.
- **Full audit trail**: every agent turn, every tool call, every handoff and every
  approved/rejected action is logged (`agent_steps`, `agent_runs`, `audit_logs`), so a run that
  crossed two agents can be reconstructed step by step.

## Project structure

```
clinic-system/
├── backend/            FastAPI app, LangGraph supervisor, agents, tests
│   └── app/
│       ├── agents/     runtime (the ReAct loop) + supervisor + four domain agents
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
