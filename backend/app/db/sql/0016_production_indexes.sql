-- =====================================================================
-- 0016_production_indexes.sql
--
-- Indexes for query patterns the application actually runs and that no
-- earlier migration covers. Additive only: no table or data changes.
-- IF NOT EXISTS so a hand-created index of the same name does not fail the
-- migration (supported by both SQLite and PostgreSQL).
--
--   ix_leads_updated_at
--       GET /leads orders every page by updated_at DESC
--       (services/leads.list_leads). Existing lead indexes all start with
--       assignee/origin/assigner, none with the sort column.
--
--   ix_lead_activities_actor
--       Dashboard team table: MAX(created_at) of lead_activities grouped by
--       actor_user_id for a set of users (services/dashboard). Only
--       (lead_id, ...) indexes existed.
--
--   ix_audit_events_created_at
--       GET /admin/audit with no filter orders the whole log by created_at
--       DESC and pages it; the existing audit indexes lead with actor or
--       entity.
--
--   ix_audit_events_action
--       GET /admin/audit?action=... filters by action, ordered by created_at.
--
--   ix_customers_name
--       GET /customers orders by name (services/customers.list_customers);
--       the feedback resolver's customer picker does the same.
--
--   ix_customer_references_asked_on
--       GET /references orders by asked_on DESC, created_at DESC for the
--       whole visible book (services/references.list_references).
--
--   ix_feedback_match_status_created
--       GET /feedback-sync/needs-review filters match_status IN (...) and
--       orders by created_at DESC. Supersedes nothing: ix_feedback_match_status
--       stays for the plain equality lookups.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

CREATE INDEX IF NOT EXISTS ix_leads_updated_at ON leads (updated_at);

CREATE INDEX IF NOT EXISTS ix_lead_activities_actor
    ON lead_activities (actor_user_id, created_at);

CREATE INDEX IF NOT EXISTS ix_audit_events_created_at ON audit_events (created_at);

CREATE INDEX IF NOT EXISTS ix_audit_events_action ON audit_events (action, created_at);

CREATE INDEX IF NOT EXISTS ix_customers_name ON customers (name);

CREATE INDEX IF NOT EXISTS ix_customer_references_asked_on
    ON customer_references (asked_on, created_at);

CREATE INDEX IF NOT EXISTS ix_feedback_match_status_created
    ON feedback (match_status, created_at);
