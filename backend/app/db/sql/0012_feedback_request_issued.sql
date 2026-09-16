-- =====================================================================
-- 0012_feedback_request_issued.sql
--
-- Composing a feedback request is not sending it.
--
-- THE PROBLEM
-- -----------
-- Opening the "send feedback request" dialog created the request with status
-- SENT and audited FEEDBACK_REQUEST_SENT - before anybody had sent anything.
-- The code has to exist at that moment (it is part of the link in the
-- message), but existing is not the same as having gone out. The dialog could
-- be closed, the send button is disabled when no form link is configured,
-- and a manager opening it hit a refusal on the actual send. Every one of
-- those left a request recorded as SENT, so the pending queue said AWAITING
-- for accounts nobody had messaged. The demo database had 7 such requests
-- while no form link was even configured.
--
-- THE CHANGE
-- ----------
--   ISSUED   the code exists and a message was drafted. Nothing proven sent.
--   SENT     the assignee confirmed it went out ("mark sent").
--
-- Only SENT reads as "asked, awaiting" in the queue. An ISSUED code still
-- matches if a response arrives - the customer may have been sent the text by
-- other means - and completes the request exactly as a SENT one would.
--
-- EXISTING ROWS - by evidence, not assumption
-- -------------------------------------------
-- Pressing send has always logged a FEEDBACK_REQUESTED activity on the
-- subject's own timeline, in the same transaction. A SENT request with such an
-- activity at or after its creation really was sent and stays SENT. One
-- without it was only ever composed, and becomes ISSUED. Nothing is deleted.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

{{#sqlite}}
CREATE TABLE feedback_requests_rebuilt (
    id             {{UUID}}     PRIMARY KEY,
    reference      VARCHAR(20)  NOT NULL UNIQUE,
    token          VARCHAR(64)  NOT NULL UNIQUE,
    subject_type   VARCHAR(10)  NOT NULL,
    lead_id        {{UUID}}     REFERENCES leads(id) ON DELETE CASCADE,
    customer_id    {{UUID}}     REFERENCES customers(id) ON DELETE CASCADE,
    owner_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'ISSUED',
    channel        VARCHAR(20),
    -- Set when the code is issued, and moved forward when it is actually sent.
    sent_at        {{TS}}       NOT NULL DEFAULT {{NOW}},
    completed_at   {{TS}},
    expires_at     {{TS}},
    created_at     {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_feedback_requests_status
        CHECK (status IN ('ISSUED', 'SENT', 'COMPLETED', 'EXPIRED', 'CANCELLED')),
    CONSTRAINT ck_feedback_requests_subject_type
        CHECK (subject_type IN ('LEAD', 'CUSTOMER')),
    CONSTRAINT ck_feedback_requests_one_subject
        CHECK ((lead_id IS NULL) <> (customer_id IS NULL))
);

INSERT INTO feedback_requests_rebuilt (
    id, reference, token, subject_type, lead_id, customer_id, owner_user_id,
    status, channel, sent_at, completed_at, expires_at, created_at
)
SELECT
    r.id, r.reference, r.token, r.subject_type, r.lead_id, r.customer_id, r.owner_user_id,
    CASE
        WHEN r.status <> 'SENT' THEN r.status
        WHEN r.lead_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM lead_activities a
            WHERE a.lead_id = r.lead_id
              AND a.activity_type = 'FEEDBACK_REQUESTED'
              AND a.created_at >= r.created_at
        ) THEN 'SENT'
        WHEN r.customer_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM customer_activities a
            WHERE a.customer_id = r.customer_id
              AND a.activity_type = 'FEEDBACK_REQUESTED'
              AND a.created_at >= r.created_at
        ) THEN 'SENT'
        ELSE 'ISSUED'
    END,
    r.channel, r.sent_at, r.completed_at, r.expires_at, r.created_at
FROM feedback_requests r;

DROP TABLE feedback_requests;

ALTER TABLE feedback_requests_rebuilt RENAME TO feedback_requests;

CREATE INDEX ix_feedback_requests_status ON feedback_requests (status, sent_at);
CREATE INDEX ix_feedback_requests_owner ON feedback_requests (owner_user_id, status);
CREATE INDEX ix_feedback_requests_lead ON feedback_requests (lead_id);
CREATE INDEX ix_feedback_requests_customer ON feedback_requests (customer_id);
{{/sqlite}}

{{#postgresql}}
ALTER TABLE feedback_requests DROP CONSTRAINT ck_feedback_requests_status;

UPDATE feedback_requests r SET status = 'ISSUED'
WHERE r.status = 'SENT'
  AND NOT (
        (r.lead_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM lead_activities a
            WHERE a.lead_id = r.lead_id
              AND a.activity_type = 'FEEDBACK_REQUESTED'
              AND a.created_at >= r.created_at))
     OR (r.customer_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM customer_activities a
            WHERE a.customer_id = r.customer_id
              AND a.activity_type = 'FEEDBACK_REQUESTED'
              AND a.created_at >= r.created_at))
  );

ALTER TABLE feedback_requests ALTER COLUMN status SET DEFAULT 'ISSUED';
ALTER TABLE feedback_requests ADD CONSTRAINT ck_feedback_requests_status
    CHECK (status IN ('ISSUED', 'SENT', 'COMPLETED', 'EXPIRED', 'CANCELLED'));
{{/postgresql}}
