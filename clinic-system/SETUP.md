# Setup Guide

## 1. Supabase Project
1. Go to supabase.com → New Project
2. Go to SQL Editor → paste each file in `supabase/migrations/` in filename order → Run
   (`001_initial.sql`, then `002_uniform_working_hours.sql`, which puts every doctor on
   Mon–Fri 09:00–17:00 — run it on an existing database too)
3. Create a test user: Authentication → Users → Invite (e.g. admin@clinic.demo)
4. Assign role: SQL Editor → `INSERT INTO user_roles (user_id, role_id) VALUES ('your-user-uuid', 1);`

This SQL step bootstraps the **first admin only**. Once that admin can sign in, every
further doctor and receptionist is created from the **Staff** page in the UI — no more SQL.

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
Expected: 124 tests passing.

## 5. Demo Workflows

### Workflow 0 — Staff setup and manual booking (no LLM involved)
**As admin:**
1. Go to **Staff** → **+ Add Staff**
2. Pick **Doctor**, enter a name and specialty, tick the working days and hours, and
   optionally give an email + password to create a login for them
3. Repeat with **Receptionist** — receptionists always need an email + password, since
   the account *is* the record. They appear under *Login accounts*, not in the staff table.

**As admin or receptionist:**
4. Go to **Appointments** → **+ New Appointment**
5. Pick a patient and doctor — the slot picker fills with that doctor's open times,
   derived from the working hours set in step 2 (a doctor with no working hours shows none)
6. Booking someone not yet on file? Click **+ New patient** inside the form — they are
   added with the next free `Pnnn` code and selected for this appointment.
7. Pick a slot and book. This writes directly, with no approval gate: a human filling in
   a form is already the human in the loop. Double-booking is rejected with a 409.
8. To change anything later, hit **Edit** on the row — patient, doctor, service, date,
   time and notes are all editable, and availability is re-checked against the new
   doctor and duration. Completed and cancelled appointments are read-only history.

Times are wall-clock in `CLINIC_TIMEZONE` (default `Europe/Tirane`, matching Kosovo's
CET/CEST offsets and EU DST rules). Set it in `.env` if the clinic runs elsewhere.

### Workflow 1 — Appointment Request
1. Go to Agent Chat
2. Type: `Schedule Alban Krasniqi with Dr. Hoxha for a general checkup tomorrow at 10am`
   (patients and doctors can be named however you'd say them out loud — `P001` or
   `Alban Krasniqi`, `Dr. Hoxha` or `Dr. Arben Hoxha`. Pick a **weekday**: doctors
   are Mon–Fri, and the agent refuses a time outside their working hours, listing
   the open slots for that day instead.)
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
- Staff/account provisioning is admin-only; patient records are receptionist-only;
  manual booking is admin + receptionist; doctors are read-only throughout
- Doctors with no configured hours fall back to Mon–Fri 09:00–17:00 (`DEFAULT_WORK_*` in `.env`)
- LLM (GPT-4o) is used only for: intent classification, document classification, field extraction, summarization
- Document text is always passed as `user` role to GPT-4o — never as `system` (injection prevention)
- Every write action goes through an approval gate before execution
- All actions are recorded in `audit_logs` table

3. Two pending gates are already waiting in agent chat from my earlier runs (Fjolla 11:30, Leutrim 12:00 on Mon 27 July).

Am I done? Mostly — two honest caveats

I have to correct something I told you. I said the duplicated document fields were caused by execute_with_retry re-sending the insert. The evidence contradicts that: no retry or exception appears in the log, tand the agent returns 4 fieldsconsistently across repeated runs. So the delete-then-insert change I made is a reasonable idempotency guard, but it is not a verified fix for what I actually saw, and I stopped before
finding the real cause. The refws correctly; only the insurance one duplicated, twice.

The --reload hang is also not fking was worth doing on its own — it immediately surfaced a genuine silent failure — but I tested it and reload still hangs. uvicorn never logs Shutting down at all, so the cause is in its Windows reloader, not in the app. The backend is running witmanually after backend edits.

Everything else is verified: 18 path works live, thesearch_patients fix is confirmed, and uploads now land in backend/uploads/.

Want me to keep digging on the ?