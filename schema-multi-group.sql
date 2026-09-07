-- Moban FU Case Tracker — Multi-Group Support
-- Adds wa_groups lookup table + cases.group_id column
-- Run: psql $DATABASE_URL -f schema-multi-group.sql
-- Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS)

-- ============================================================
-- 1. Tabel grup WhatsApp
-- ============================================================

CREATE TABLE IF NOT EXISTS wa_groups (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,      -- label "Grup A" / "Grup B"
    chat_id       VARCHAR(128) NOT NULL UNIQUE,      -- 120363xxx@g.us
    is_active     BOOLEAN NOT NULL DEFAULT true,
    is_default    BOOLEAN NOT NULL DEFAULT false,     -- grup fallback saat create case tanpa group_id
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wa_groups_active ON wa_groups(is_active);

-- Maksimum satu grup default di level DB
CREATE UNIQUE INDEX IF NOT EXISTS idx_wa_groups_default
    ON wa_groups(is_default) WHERE is_default = true;

-- ============================================================
-- 2. Kolom grup pada cases
-- ============================================================

ALTER TABLE cases ADD COLUMN IF NOT EXISTS group_id INT
    REFERENCES wa_groups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_cases_group ON cases(group_id);