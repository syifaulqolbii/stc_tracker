-- Migration: Add media columns to wa_messages
-- Handles image+caption and image-only replies from WAHA webhooks
-- Safe to run multiple times (IF NOT EXISTS)

ALTER TABLE wa_messages ADD COLUMN IF NOT EXISTS media_url TEXT;
ALTER TABLE wa_messages ADD COLUMN IF NOT EXISTS media_type VARCHAR(50);
