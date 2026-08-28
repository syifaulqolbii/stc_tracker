-- Migration: Reminder feature for Moban FU Case Tracker
-- Adds mentions storage, reminder tracking, and reminder log

-- Simpan mentions saat case dibuat (JSONB array of {number, name})
ALTER TABLE cases ADD COLUMN IF NOT EXISTS mentions JSONB DEFAULT '[]';

-- Hitung berapa kali case sudah di-reminder
ALTER TABLE cases ADD COLUMN IF NOT EXISTS reminder_count INT DEFAULT 0;

-- Kapan terakhir kali di-reminder
ALTER TABLE cases ADD COLUMN IF NOT EXISTS last_reminder_at TIMESTAMPTZ;

-- Log setiap reminder
CREATE TABLE IF NOT EXISTS reminder_log (
    id            SERIAL PRIMARY KEY,
    case_id       INT REFERENCES cases(id) ON DELETE CASCADE,
    wa_message_id VARCHAR(128),
    message       TEXT,
    triggered_by  VARCHAR(20),          -- 'manual' | 'cron'
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reminder_log_case ON reminder_log(case_id);
