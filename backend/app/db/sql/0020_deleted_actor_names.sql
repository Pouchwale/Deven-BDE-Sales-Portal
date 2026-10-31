-- =====================================================================
-- 0020_deleted_actor_names.sql
--
-- Deactivation is gone: removing a person now means deleting them, after
-- their customers, leads, references and reports are handed to someone else.
-- History rows keep pointing at whoever acted (actor_user_id, ON DELETE SET
-- NULL), so a deleted person's name would vanish from them. actor_name is
-- written at deletion time and shown when the account no longer exists.
-- =====================================================================

ALTER TABLE audit_events ADD COLUMN actor_name VARCHAR(120);
ALTER TABLE lead_activities ADD COLUMN actor_name VARCHAR(120);
ALTER TABLE customer_activities ADD COLUMN actor_name VARCHAR(120);
