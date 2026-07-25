# Setup Guide

## 1. Supabase Project
1. Go to supabase.com → New Project
2. Go to SQL Editor → paste contents of `supabase/migrations/001_initial.sql` → Run
3. Create a test user: Authentication → Users → Invite (e.g. admin@clinic.demo)
4. Assign role: SQL Editor → `INSERT INTO user_roles (user_id, role_id) VALUES ('your-user-uuid', 1);`

## 2. Environment Variables
```bash
cp .env.example .env
# Fill in: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET
```
Get the JWT secret from: Supabase Dashboard → Settings → API → JWT Secret

## 3. Run with Docker
```bash
docker compose up --build
```
- Frontend: http://localhost:3000
- Backend API docs: http://localhost:8000/docs

## 4. Run Tests
```bash
cd backend
pip install -r requirements.txt
pytest tests/ -v
```
Expected: 30+ tests passing.

## 5. Three Demo Workflows

### Workflow 1 — Appointment Request
1. Go to Agent Chat
2. Type: `Schedule Alban Krasniqi with Dr. Hoxha for a general checkup tomorrow at 10am`
3. See agent trace unfold (classify → check patient → check staff → check conflict)
4. See Approval Gate appear → click **Approve**
5. Appointment is created and audit log is written

### Workflow 2 — Document Intake
1. Go to Documents → Upload Document (any PDF)
2. Agent auto-processes: OCR → classify → extract fields
3. Review extracted fields in the detail panel
4. Click **Verify** → document status changes to verified

### Workflow 3 — Weekly Operational Report
1. Go to Reports
2. Select date range → Generate & Download PDF
3. PDF includes: appointment summary, no-shows, missing documents

## Architecture Notes
- Business rules (conflict detection, status transitions) = deterministic Python, zero LLM
- LLM (GPT-4o) is used only for: intent classification, document classification, field extraction, summarization
- Document text is always passed as `user` role to GPT-4o — never as `system` (injection prevention)
- Every write action goes through an approval gate before execution
- All actions are recorded in `audit_logs` table
