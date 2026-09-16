-- =====================================================================
-- 0002_timelines_and_undo.sql
--
-- Three changes, all driven by how the two kinds of record actually differ:
--
--   1. A customer that came from SAP is a COMPLETED lead. What is still open
--      about it is the feedback and the reference, so it gets a timeline of
--      its own (customer_activities) rather than being forced into the lead
--      pipeline it already finished.
--
--   2. Any timeline entry can be UNDONE. A mis-clicked stage change should be
--      reversible without editing history, so entries are marked undone and
--      stay visible rather than being deleted.
--
--   3. Feedback is linked to the customer it is about, so a response can be
--      shown next to the account instead of only in the feedback module.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================


-- --------------------------------------------- lead_activities: undo
-- Soft undo. The row stays so the trail reads "this happened, then it was
-- taken back" - which is the truth - rather than silently losing an entry.
ALTER TABLE lead_activities ADD COLUMN undone_at {{TS}};
ALTER TABLE lead_activities ADD COLUMN undone_by_user_id {{UUID}} REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX ix_lead_activities_undone ON lead_activities (lead_id, undone_at);


-- ------------------------------------------------ customer_activities
-- The timeline of a completed customer: feedback asked for and received,
-- Google review requested, calls, notes. Reference asks live in
-- customer_references and are merged into the same view at read time, so the
-- history is not duplicated in two places.
CREATE TABLE customer_activities (
    id                {{UUID}}     PRIMARY KEY,
    customer_id       {{UUID}}     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    actor_user_id     {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    activity_type     VARCHAR(30)  NOT NULL,
    remark            TEXT,
    -- Set when the entry points at something concrete: a feedback response,
    -- a reference row, the outbound review link.
    related_type      VARCHAR(30),
    related_id        {{UUID}},
    undone_at         {{TS}},
    undone_by_user_id {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    created_at        {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_customer_activities_type CHECK (
        activity_type IN (
            'NOTE',
            'CALL',
            'WHATSAPP',
            'EMAIL',
            'MEETING',
            'FEEDBACK_REQUESTED',
            'FEEDBACK_RECEIVED',
            'REVIEW_REQUESTED',
            'REFERENCE_ASKED'
        )
    )
);

CREATE INDEX ix_customer_activities_customer
    ON customer_activities (customer_id, created_at);


-- ------------------------------------------- feedback -> customer link
-- Resolved on import by matching mobile, email or company against the SAP
-- book. Nullable on purpose: a response from somebody we cannot match is
-- still a response, and guessing would be worse than leaving it unlinked.
ALTER TABLE feedback ADD COLUMN customer_id {{UUID}} REFERENCES customers(id) ON DELETE SET NULL;

CREATE INDEX ix_feedback_customer ON feedback (customer_id);
