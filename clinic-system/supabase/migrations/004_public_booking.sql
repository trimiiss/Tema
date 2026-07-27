-- ============================================================
-- PUBLIC PATIENT BOOKING — schema support for the guest booking chatbot
--
-- The booking chat is unauthenticated: a member of the public opens /book with
-- no account. Two columns in the existing schema make that impossible as-is,
-- because both reference a user that a guest does not have:
--
--   agent_runs.user_id    UUID REFERENCES auth.users(id)
--   appointments.created_by UUID REFERENCES auth.users(id)
--
-- Both are nullable, so a guest run stores NULL there and is identified
-- instead by `agent_runs.session_id` — a UUID the browser generates and keeps
-- in localStorage. That column is the only thing tying a guest to their own
-- runs, so every public endpoint filters on it.
-- ============================================================

-- ---- Guest identity on agent runs ----
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS session_id TEXT;

-- Which surface the run came from. Defaulting to 'staff' leaves every existing
-- row correct, and lets `GET /agent/runs` stay staff-only without a data fix.
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'staff';

-- Every public read is "this session's runs", so the lookup is by session_id.
CREATE INDEX IF NOT EXISTS agent_runs_session_id_idx ON agent_runs (session_id);

-- ---- Provenance ----
-- Explicit, rather than inferring "a patient booked this" from
-- `created_by IS NULL` — which is also true of every seeded row, and of
-- anything created before this migration.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'staff';
ALTER TABLE patients     ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'staff';

-- Patient-submitted bookings land as 'proposed' and wait in the staff queue,
-- so that queue reads this index on every load.
CREATE INDEX IF NOT EXISTS appointments_status_source_idx ON appointments (status, source);
