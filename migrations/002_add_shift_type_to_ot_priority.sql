-- Run in Supabase SQL Editor after 001_create_tables.sql
-- Adds shift_type to ot_priority and updates the unique index to include it.
-- Same day can have priority_order=1 for 7-3 AND for 10-6, so shift_type must
-- be part of the unique constraint.

ALTER TABLE ot_priority ADD COLUMN IF NOT EXISTS shift_type VARCHAR(20);

DROP INDEX IF EXISTS ot_priority_date_order_version;
CREATE UNIQUE INDEX IF NOT EXISTS ot_priority_date_shift_order_version
    ON ot_priority(priority_date, shift_type, priority_order, source_version);
