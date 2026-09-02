-- Migration: Soft delete support for cases
-- Adds deleted_at column to cases table

ALTER TABLE cases ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Index for filtering active cases (most queries)
CREATE INDEX IF NOT EXISTS idx_cases_deleted ON cases(deleted_at) WHERE deleted_at IS NULL;
