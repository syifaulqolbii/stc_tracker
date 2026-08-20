-- Moban FU Case Tracker v1.1 — single group + reply-chain tracking
-- Jalankan di Supabase SQL Editor atau: psql $DATABASE_URL -f schema.sql

CREATE TABLE IF NOT EXISTS cases (
    id            SERIAL PRIMARY KEY,
    case_code     VARCHAR(50) UNIQUE,        -- INC000023470570 / Case ID; fallback CASE-0001
    case_type     VARCHAR(30) NOT NULL,      -- stc | smooa | mobile | ufo | other
    title         TEXT,
    fields        JSONB NOT NULL DEFAULT '{}',
    message_text  TEXT NOT NULL,
    wa_message_id VARCHAR(128) UNIQUE,       -- ROOT pesan case di grup (jangkar rantai)
    status        VARCHAR(20) NOT NULL DEFAULT 'open',  -- open | in_progress | done | issue
    ack           VARCHAR(20),               -- PENDING | SERVER | DEVICE | READ
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

-- Semua pesan grup + parent-nya. Bahan baku reply-chain traversal.
CREATE TABLE IF NOT EXISTS wa_messages (
    wa_message_id VARCHAR(128) PRIMARY KEY,
    quoted_id     VARCHAR(128),              -- parent (pesan yang di-reply); NULL jika bukan reply
    case_id       INT REFERENCES cases(id) ON DELETE SET NULL,
    author        VARCHAR(64),
    body          TEXT,
    from_me       BOOLEAN DEFAULT false,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wamsg_quoted ON wa_messages(quoted_id);
CREATE INDEX IF NOT EXISTS idx_wamsg_case   ON wa_messages(case_id);

CREATE TABLE IF NOT EXISTS progress_updates (
    id            SERIAL PRIMARY KEY,
    case_id       INT REFERENCES cases(id) ON DELETE SET NULL,
    wa_message_id VARCHAR(128) UNIQUE REFERENCES wa_messages(wa_message_id),
    author        VARCHAR(64),
    body          TEXT,
    parsed_status VARCHAR(20),               -- done | in_progress | issue | NULL
    parsed_note   TEXT,
    source        VARCHAR(10),               -- rule | reply | chain | llm | crawl | manual
    confidence    REAL,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_updates_case ON progress_updates(case_id);
