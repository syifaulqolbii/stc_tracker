-- Moban FU Case Tracker v1.2 — Seed Data
-- Run: psql $DATABASE_URL -f seed-v1-2.sql
-- Only seeds lookup tables. Area & Regional data dikasih nanti oleh user.

-- Sumber Ticket
INSERT INTO sumber_tickets (name) VALUES ('STC'), ('Grapari'), ('Web IT')
    ON CONFLICT (name) DO NOTHING;

-- Jenis Case
INSERT INTO jenis_cases (name) VALUES ('Non Order'), ('Non AO'), ('Mobile')
    ON CONFLICT (name) DO NOTHING;

-- Area & Regional: dikasih nanti oleh user
-- Contoh (uncomment untuk testing):
-- INSERT INTO areas (name) VALUES ('Area 1'), ('Area 2'), ('Area 3')
--     ON CONFLICT (name) DO NOTHING;
-- INSERT INTO regionals (area_id, name) VALUES
--     ((SELECT id FROM areas WHERE name='Area 1'), 'Regional 1'),
--     ((SELECT id FROM areas WHERE name='Area 1'), 'Regional 2'),
--     ((SELECT id FROM areas WHERE name='Area 1'), 'Regional 3'),
--     ((SELECT id FROM areas WHERE name='Area 2'), 'Regional 4'),
--     ((SELECT id FROM areas WHERE name='Area 2'), 'Regional 5'),
--     ((SELECT id FROM areas WHERE name='Area 2'), 'Regional 6')
--     ON CONFLICT (area_id, name) DO NOTHING;
