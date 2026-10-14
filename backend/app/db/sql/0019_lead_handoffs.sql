-- =====================================================================
-- 0019_lead_handoffs.sql
--
-- lead_handoffs - one row each time the person holding an open lead passes
-- it on to somebody in their team (POST /api/leads/{id}/pass-on). The lead
-- itself moves through leads.assigned_to_user_id as before; this table is
-- the record of every pass, so "how many leads were passed on, by whom, to
-- whom" is one query. No score reads it.
-- =====================================================================

CREATE TABLE lead_handoffs (
    id            {{UUID}}  PRIMARY KEY,
    lead_id       {{UUID}}  NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    from_user_id  {{UUID}}  REFERENCES users(id) ON DELETE SET NULL,
    to_user_id    {{UUID}}  REFERENCES users(id) ON DELETE SET NULL,
    reason        TEXT,
    created_at    {{TS}}    NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_lead_handoffs_created ON lead_handoffs (created_at);
CREATE INDEX ix_lead_handoffs_lead ON lead_handoffs (lead_id);
