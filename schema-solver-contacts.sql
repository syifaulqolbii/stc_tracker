-- Moban FU Case Tracker — Solver Contacts CRUD
-- Run: psql $DATABASE_URL -f schema-solver-contacts.sql

CREATE TABLE IF NOT EXISTS solver_contacts (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(128) NOT NULL,
    phone_number  VARCHAR(20) NOT NULL,        -- format internasional: 6281234567890
    role          VARCHAR(100),                 -- posisi: Solusi 1, Solver IT, Supervisor, dll
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_solver_contacts_active ON solver_contacts(is_active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_solver_contacts_phone ON solver_contacts(phone_number) WHERE is_active = true;
