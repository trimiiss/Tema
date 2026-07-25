-- ============================================================
-- Uniform working hours: every doctor Mon–Fri 09:00–17:00
--
-- The initial seed gave each doctor different days (Blerta Mon/Wed/Fri,
-- Gentian Tue/Thu), so booking on any other weekday returned no slots at all.
-- Run this once in the Supabase SQL Editor after 001_initial.sql.
-- ============================================================

DELETE FROM schedules;

INSERT INTO schedules (staff_id, weekday, start_time, end_time)
SELECT s.id, d.weekday, '09:00'::time, '17:00'::time
FROM staff s
CROSS JOIN (VALUES (0), (1), (2), (3), (4)) AS d(weekday)
WHERE s.active = TRUE;
