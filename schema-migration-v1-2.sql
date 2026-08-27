-- Moban FU Case Tracker v1.2 — Migration from v1.1
-- Run: psql $DATABASE_URL -f schema-migration-v1-2.sql
-- This script is safe to run multiple times (IF NOT EXISTS)

-- ============================================================
-- 1. Create new lookup tables (if not exist from schema-v1-2.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS areas (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS regionals (
    id            SERIAL PRIMARY KEY,
    area_id       INT NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(area_id, name)
);
CREATE INDEX IF NOT EXISTS idx_regionals_area ON regionals(area_id);

CREATE TABLE IF NOT EXISTS sumber_tickets (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(50) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jenis_cases (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(50) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 2. Alter cases table — add new columns
-- ============================================================

ALTER TABLE cases ADD COLUMN IF NOT EXISTS area_id INT REFERENCES areas(id) ON DELETE SET NULL;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS regional_id INT REFERENCES regionals(id) ON DELETE SET NULL;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS sumber_ticket_id INT REFERENCES sumber_tickets(id) ON DELETE SET NULL;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS jenis_case_id INT REFERENCES jenis_cases(id) ON DELETE SET NULL;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS asal_grapari TEXT;

-- Add indexes for new columns
CREATE INDEX IF NOT EXISTS idx_cases_area ON cases(area_id);
CREATE INDEX IF NOT EXISTS idx_cases_regional ON cases(regional_id);
CREATE INDEX IF NOT EXISTS idx_cases_sumber ON cases(sumber_ticket_id);
CREATE INDEX IF NOT EXISTS idx_cases_jenis ON cases(jenis_case_id);

-- ============================================================
-- 3. Seed data — Sumber Ticket & Jenis Case
-- ============================================================

INSERT INTO sumber_tickets (name) VALUES ('STC'), ('Grapari'), ('Web IT')
    ON CONFLICT (name) DO NOTHING;

INSERT INTO jenis_cases (name) VALUES ('Non Order'), ('Non AO'), ('Mobile')
    ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- 4. Migrate old case_type → jenis_case_id (best-effort)
--    Old values: stc, smooa, mobile, ufo, other
--    New values: Non Order, Non AO, Mobile
--    Mapping:
--      mobile → Mobile
--      stc, smooa, ufo, other → Non Order (default)
-- ============================================================

UPDATE cases SET jenis_case_id = (
    SELECT id FROM jenis_cases WHERE name = 'Mobile'
) WHERE case_type = 'mobile' AND jenis_case_id IS NULL;

UPDATE cases SET jenis_case_id = (
    SELECT id FROM jenis_cases WHERE name = 'Non Order'
) WHERE case_type IN ('stc', 'smooa', 'ufo', 'other') AND jenis_case_id IS NULL;
