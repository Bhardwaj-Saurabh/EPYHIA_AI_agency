-- 002_brand_approval.sql
-- Flow 1 step 6 (DESIGN.md section 5): administrator approval is bound to the
-- brand-document id and content hash. This records that approval on the
-- document version itself; runs/tasks transition when it lands.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE brand_document
  ADD COLUMN content_hash TEXT,
  ADD COLUMN approved_by TEXT,
  ADD COLUMN approved_at TIMESTAMPTZ;

-- Backfill hashes for versions created before this migration.
UPDATE brand_document
   SET content_hash = encode(digest(full_text, 'sha256'), 'hex')
 WHERE content_hash IS NULL;
