-- One row per (document, field name).
--
-- `document_fields` had no constraint saying so, so three insurance documents
-- each stored all four of their fields twice and the database was happy to
-- keep them. The cause was `execute_with_retry` re-sending an insert that had
-- already committed; the application now upserts on this key instead (see
-- `ocr_service.process_document_async`), and that upsert *requires* the index
-- below — Postgres rejects an ON CONFLICT whose columns have no unique index.
--
-- So this migration is a prerequisite for document processing, not a
-- tidy-up. It also states the invariant where it belongs, so a path nobody
-- anticipated fails loudly rather than silently duplicating.
--
-- Safe to run on an existing database. Run the diagnostic in SETUP.md *first*
-- if you still want to know which mechanism caused an existing duplicate: this
-- migration deletes the evidence.

-- Keep one row per (document_id, field_name): a verified row wins over an
-- unverified one, then the highest confidence, then the oldest.
DELETE FROM document_fields d
WHERE d.ctid NOT IN (
  SELECT DISTINCT ON (document_id, field_name) ctid
  FROM document_fields
  ORDER BY document_id,
           field_name,
           (verified_at IS NOT NULL) DESC,
           confidence DESC NULLS LAST,
           ctid
);

CREATE UNIQUE INDEX IF NOT EXISTS document_fields_document_id_field_name_key
  ON document_fields (document_id, field_name);
