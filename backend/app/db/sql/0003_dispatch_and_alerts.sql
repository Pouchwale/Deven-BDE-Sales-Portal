-- =====================================================================
-- 0003_dispatch_and_alerts.sql
--
-- Two independent additions:
--
--   1. A CONVERTED lead can be marked DISPATCHED once the work is actually
--      delivered - a separate, explicit step from the stage itself, because
--      "won" and "fulfilled" are different facts. Dispatch is what makes a
--      lead eligible for a feedback ask.
--
--   2. A feedback alert can be handed to a person, so a flagged department
--      has an owner rather than just a number on a dashboard.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

ALTER TABLE leads ADD COLUMN dispatched_at {{TS}};

ALTER TABLE feedback_alerts ADD COLUMN assigned_to_user_id {{UUID}} REFERENCES users(id) ON DELETE SET NULL;
