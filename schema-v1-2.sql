-- Moban FU Case Tracker v1.2 — Area, Regional, Sumber Ticket, Jenis Case
-- Jalankan di Supabase SQL Editor atau: psql $DATABASE_URL -f schema-v1-2.sql

-- ============================================================
-- Tabel Lookup Baru
-- ============================================================

-- Area (parent hierarchy)
CREATE TABLE IF NOT EXISTS areas (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Regional (child of Area)
CREATE TABLE IF NOT EXISTS regionals (
    id            SERIAL PRIMARY KEY,
    area_id       INT NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(area_id, name)
);
CREATE INDEX IF NOT EXISTS idx_regionals_area ON regionals(area_id);

-- Sumber Ticket: STC, Grapari, Web IT
CREATE TABLE IF NOT EXISTS sumber_tickets (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(50) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- Jenis Case: Non Order, Non AO, Mobile
CREATE TABLE IF NOT EXISTS jenis_cases (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(50) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Tabel Cases (dengan kolom baru)
-- ============================================================

CREATE TABLE IF NOT EXISTS cases (
    id               SERIAL PRIMARY KEY,
    case_code        VARCHAR(50) UNIQUE,        -- INC000023470570 / Case ID
    case_type        VARCHAR(30) NOT NULL,       -- deprecated: stc|smooa|mobile|ufo|other (kept for data compat)
    title            TEXT,
    fields           JSONB NOT NULL DEFAULT '{}',
    message_text     TEXT NOT NULL,
    wa_message_id    VARCHAR(128) UNIQUE,        -- ROOT pesan case di grup
    status           VARCHAR(20) NOT NULL DEFAULT 'open',  -- open | in_progress | done | issue
    ack              VARCHAR(20),                -- PENDING | SERVER | DEVICE | READ

    -- Kolom baru v1.2
    area_id          INT REFERENCES areas(id) ON DELETE SET NULL,
    regional_id      INT REFERENCES regionals(id) ON DELETE SET NULL,
    sumber_ticket_id INT REFERENCES sumber_tickets(id) ON DELETE SET NULL,
    jenis_case_id    INT REFERENCES jenis_cases(id) ON DELETE SET NULL,
    asal_grapari     TEXT,

    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_area ON cases(area_id);
CREATE INDEX IF NOT EXISTS idx_cases_regional ON cases(regional_id);
CREATE INDEX IF NOT EXISTS idx_cases_sumber ON cases(sumber_ticket_id);
CREATE INDEX IF NOT EXISTS idx_cases_jenis ON cases(jenis_case_id);

-- ============================================================
-- Tabel WA Messages (sama seperti v1.1)
-- ============================================================

CREATE TABLE IF NOT EXISTS wa_messages (
    wa_message_id VARCHAR(128) PRIMARY KEY,
    quoted_id     VARCHAR(128),
    case_id       INT REFERENCES cases(id) ON DELETE SET NULL,
    author        VARCHAR(64),
    author_name   VARCHAR(128),
    body          TEXT,
    from_me       BOOLEAN DEFAULT false,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wamsg_quoted ON wa_messages(quoted_id);
CREATE INDEX IF NOT EXISTS idx_wamsg_case   ON wa_messages(case_id);

-- ============================================================
-- Tabel Progress Updates (sama seperti v1.1)
-- ============================================================

CREATE TABLE IF NOT EXISTS progress_updates (
    id            SERIAL PRIMARY KEY,
    case_id       INT REFERENCES cases(id) ON DELETE SET NULL,
    wa_message_id VARCHAR(128) UNIQUE REFERENCES wa_messages(wa_message_id),
    author        VARCHAR(64),
    body          TEXT,
    parsed_status VARCHAR(20),
    parsed_note   TEXT,
    source        VARCHAR(10),
    confidence    REAL,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_updates_case ON progress_updates(case_id);
