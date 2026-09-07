-- Moban FU Case Tracker — Default Group (grup test development)
-- Adds wa_groups.is_default: grup fallback saat POST /api/cases tanpa group_id.
-- Grup default TIDAK muncul di GET /api/groups (switcher) kecuali ?include_default=true,
-- tapi tetap aktif di-track oleh webhook/crawl/reminder karena barisnya ada di wa_groups.
-- Run: psql $DATABASE_URL -f schema-migration-group-default.sql
-- Safe to run multiple times (IF NOT EXISTS)

ALTER TABLE wa_groups ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT false;

-- Maksimum satu grup default di level DB
CREATE UNIQUE INDEX IF NOT EXISTS idx_wa_groups_default
    ON wa_groups(is_default) WHERE is_default = true;