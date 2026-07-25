-- ============================================================
-- CLINIC MULTI-AGENT SYSTEM — Initial Schema
-- All data is synthetic / demo only
-- ============================================================

-- ---- Roles ----
CREATE TABLE IF NOT EXISTS roles (
  id   SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE  -- admin | receptionist | doctor
);
INSERT INTO roles (name) VALUES ('admin'), ('receptionist'), ('doctor')
  ON CONFLICT DO NOTHING;

-- ---- User roles (links Supabase Auth users to roles) ----
CREATE TABLE IF NOT EXISTS user_roles (
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role_id INTEGER NOT NULL REFERENCES roles(id),
  PRIMARY KEY (user_id, role_id)
);

-- ---- Patients (administrative data only, synthetic) ----
CREATE TABLE IF NOT EXISTS patients (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code       TEXT NOT NULL UNIQUE,   -- e.g. P001
  first_name TEXT NOT NULL,
  last_name  TEXT NOT NULL,
  dob        DATE,
  gender     TEXT CHECK (gender IN ('M', 'F', 'Other')),
  phone      TEXT,
  email      TEXT,
  address    TEXT,
  notes      TEXT,
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---- Staff ----
CREATE TABLE IF NOT EXISTS staff (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id   UUID REFERENCES auth.users(id),
  full_name TEXT NOT NULL,
  specialty TEXT,
  bio       TEXT,
  active    BOOLEAN DEFAULT TRUE
);

-- ---- Services ----
CREATE TABLE IF NOT EXISTS services (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name             TEXT NOT NULL,
  duration_minutes INTEGER NOT NULL DEFAULT 30,
  description      TEXT
);

-- ---- Staff schedules (weekly recurring) ----
CREATE TABLE IF NOT EXISTS schedules (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id   UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  weekday    INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0=Mon, 6=Sun
  start_time TIME NOT NULL,
  end_time   TIME NOT NULL
);

-- ---- Appointments ----
CREATE TYPE appointment_status AS ENUM (
  'proposed', 'confirmed', 'completed', 'cancelled', 'no_show'
);

CREATE TABLE IF NOT EXISTS appointments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id   UUID NOT NULL REFERENCES patients(id),
  staff_id     UUID NOT NULL REFERENCES staff(id),
  service_id   UUID REFERENCES services(id),
  scheduled_at TIMESTAMPTZ NOT NULL,
  duration_min INTEGER NOT NULL DEFAULT 30,
  status       appointment_status NOT NULL DEFAULT 'proposed',
  notes        TEXT,
  created_by   UUID REFERENCES auth.users(id),
  updated_by   UUID REFERENCES auth.users(id),
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- ---- Documents ----
CREATE TYPE document_status AS ENUM ('pending', 'processing', 'verified', 'rejected');
CREATE TYPE document_type   AS ENUM ('referral', 'insurance', 'id', 'lab_result', 'other');

CREATE TABLE IF NOT EXISTS documents (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id   UUID REFERENCES patients(id),
  filename     TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  doc_type     document_type DEFAULT 'other',
  status       document_status DEFAULT 'pending',
  uploaded_by  UUID REFERENCES auth.users(id),
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_fields (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  field_name  TEXT NOT NULL,
  field_value TEXT,
  confidence  REAL DEFAULT 0,
  verified_by UUID REFERENCES auth.users(id),
  verified_at TIMESTAMPTZ
);

-- ---- Agent runs + steps ----
CREATE TYPE run_status AS ENUM ('running', 'awaiting_approval', 'completed', 'failed', 'cancelled');

CREATE TABLE IF NOT EXISTS agent_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID REFERENCES auth.users(id),
  input_text   TEXT NOT NULL,
  intent       TEXT,
  status       run_status DEFAULT 'running',
  result       JSONB,
  created_at   TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_steps (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id     UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  agent_name TEXT NOT NULL,
  action     TEXT NOT NULL,
  input      JSONB,
  output     JSONB,
  timestamp  TIMESTAMPTZ DEFAULT now()
);

-- ---- Approval gates ----
CREATE TYPE gate_status AS ENUM ('pending', 'approved', 'rejected');

CREATE TABLE IF NOT EXISTS approval_gates (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id             UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_id            UUID REFERENCES agent_steps(id),
  action_description TEXT NOT NULL,
  payload            JSONB NOT NULL,
  status             gate_status DEFAULT 'pending',
  decided_by         UUID REFERENCES auth.users(id),
  decided_at         TIMESTAMPTZ
);

-- ---- Audit log ----
CREATE TABLE IF NOT EXISTS audit_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES auth.users(id),
  action      TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id   UUID,
  details     JSONB,
  timestamp   TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- SEED DATA (synthetic)
-- ============================================================

-- Staff (no linked auth users for seed — standalone records)
INSERT INTO staff (id, full_name, specialty, active) VALUES
  ('11111111-0000-0000-0000-000000000001', 'Dr. Arben Hoxha',   'General Practice', TRUE),
  ('11111111-0000-0000-0000-000000000002', 'Dr. Blerta Kelmendi','Pediatrics',        TRUE),
  ('11111111-0000-0000-0000-000000000003', 'Dr. Gentian Rexha',  'Internal Medicine', TRUE)
ON CONFLICT DO NOTHING;

-- Schedules (Mon–Fri 08:00–16:00 for each doctor)
INSERT INTO schedules (staff_id, weekday, start_time, end_time) VALUES
  ('11111111-0000-0000-0000-000000000001', 0, '08:00', '16:00'),
  ('11111111-0000-0000-0000-000000000001', 1, '08:00', '16:00'),
  ('11111111-0000-0000-0000-000000000001', 2, '08:00', '16:00'),
  ('11111111-0000-0000-0000-000000000001', 3, '08:00', '16:00'),
  ('11111111-0000-0000-0000-000000000001', 4, '08:00', '16:00'),
  ('11111111-0000-0000-0000-000000000002', 0, '09:00', '17:00'),
  ('11111111-0000-0000-0000-000000000002', 2, '09:00', '17:00'),
  ('11111111-0000-0000-0000-000000000002', 4, '09:00', '17:00'),
  ('11111111-0000-0000-0000-000000000003', 1, '10:00', '18:00'),
  ('11111111-0000-0000-0000-000000000003', 3, '10:00', '18:00')
ON CONFLICT DO NOTHING;

-- Services
INSERT INTO services (id, name, duration_minutes, description) VALUES
  ('22222222-0000-0000-0000-000000000001', 'General Checkup',       30, 'Routine administrative registration and checkup scheduling'),
  ('22222222-0000-0000-0000-000000000002', 'Follow-up Visit',        20, 'Follow-up appointment scheduling'),
  ('22222222-0000-0000-0000-000000000003', 'Document Verification',  15, 'Administrative document review and filing')
ON CONFLICT DO NOTHING;

-- Patients (synthetic names)
INSERT INTO patients (id, code, first_name, last_name, dob, gender, phone, email, address) VALUES
  ('33333333-0000-0000-0000-000000000001', 'P001', 'Alban',    'Krasniqi', '1985-03-12', 'M', '+383-44-100001', 'alban.k@demo.test',    'Prishtina, Kosovo'),
  ('33333333-0000-0000-0000-000000000002', 'P002', 'Fjolla',   'Berisha',  '1992-07-22', 'F', '+383-44-100002', 'fjolla.b@demo.test',   'Prizren, Kosovo'),
  ('33333333-0000-0000-0000-000000000003', 'P003', 'Visar',    'Gashi',    '1978-11-05', 'M', '+383-44-100003', 'visar.g@demo.test',    'Peja, Kosovo'),
  ('33333333-0000-0000-0000-000000000004', 'P004', 'Drita',    'Morina',   '2001-01-30', 'F', '+383-44-100004', 'drita.m@demo.test',    'Gjilan, Kosovo'),
  ('33333333-0000-0000-0000-000000000005', 'P005', 'Besnik',   'Ahmeti',   '1969-09-18', 'M', NULL,            NULL,                   'Mitrovica, Kosovo')
ON CONFLICT DO NOTHING;
