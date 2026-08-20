-- Migration: Add author_name column to wa_messages
-- Run: psql $DATABASE_URL -f schema-migration-author-name.sql

ALTER TABLE wa_messages ADD COLUMN IF NOT EXISTS author_name VARCHAR(128);
