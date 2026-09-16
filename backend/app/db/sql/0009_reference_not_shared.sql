-- =====================================================================
-- 0009_reference_not_shared.sql
--
-- A third reference outcome: NOT_SHARED.
--
-- The business distinguishes three answers to "would you refer somebody?":
--
--   YES         they gave a reference            -> completed, reference received
--   NO          not right now, ask me later      -> pending, follow-up required
--   NOT_SHARED  asked and answered, none given   -> completed, nothing to chase
--
-- Only the first two existed. That forced every "no" into the follow-up
-- queue, so a customer who had genuinely declined kept coming back round as
-- work nobody could ever close.
--
-- TWO constraints have to move, and the second one is the reason this is a
-- table rebuild rather than a one-line widening:
--
--   ck_customer_references_outcome   IN ('YES','NO')  ->  adds NOT_SHARED
--   ck_customer_references_followup  "anything that is not YES must carry a
--                                     follow-up date"
--
-- That second constraint encodes the old assumption that not-yes always means
-- ask-again-later. A NOT_SHARED row is complete and has no follow-up date, so
-- under the old rule it could not be stored at all. It now applies to NO
-- alone, which is the outcome that actually means "come back".
--
-- EXISTING ROWS ARE NOT RECLASSIFIED. Every current NO stays a NO. They were
-- recorded when "not right now" was the only way to say no, and turning them
-- into refusals would invent a customer decision that never happened. Anyone
-- who has actually declined gets re-recorded as NOT_SHARED the next time they
-- are asked.
--
-- The per-account roll-up needs no migration: ck_customers_reference_status
-- and ck_leads_reference_status already permit DECLINED, which is the state
-- NOT_SHARED rolls up to. It has simply never been reachable until now.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

{{#sqlite}}
CREATE TABLE customer_references_rebuilt (
    id                    {{UUID}}     PRIMARY KEY,
    customer_id           {{UUID}}     REFERENCES customers(id) ON DELETE CASCADE,
    lead_id               {{UUID}}     REFERENCES leads(id) ON DELETE CASCADE,
    requested_by_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,

    outcome               VARCHAR(20)  NOT NULL,
    asked_on              DATE         NOT NULL,
    next_reference_date   DATE,

    referred_name         VARCHAR(120),
    referred_company      VARCHAR(200),
    referred_mobile       VARCHAR(30),
    referred_email        VARCHAR(255),
    notes                 TEXT,

    converted_lead_id     {{UUID}},

    created_at            {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at            {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_customer_references_outcome CHECK (
        outcome IN ('YES', 'NO', 'NOT_SHARED')
    ),
    -- Only "not right now" owes a follow-up date. YES and NOT_SHARED are
    -- both finished; neither has anything left to chase.
    CONSTRAINT ck_customer_references_followup CHECK (
        outcome <> 'NO' OR next_reference_date IS NOT NULL
    ),
    CONSTRAINT ck_customer_references_subject CHECK (
        (customer_id IS NOT NULL AND lead_id IS NULL)
        OR (customer_id IS NULL AND lead_id IS NOT NULL)
    )
);

INSERT INTO customer_references_rebuilt (
    id, customer_id, lead_id, requested_by_user_id,
    outcome, asked_on, next_reference_date,
    referred_name, referred_company, referred_mobile, referred_email, notes,
    converted_lead_id, created_at, updated_at
)
SELECT
    id, customer_id, lead_id, requested_by_user_id,
    outcome, asked_on, next_reference_date,
    referred_name, referred_company, referred_mobile, referred_email, notes,
    converted_lead_id, created_at, updated_at
FROM customer_references;

DROP TABLE customer_references;

ALTER TABLE customer_references_rebuilt RENAME TO customer_references;

-- Dropped with the old table, so they come back here.
CREATE INDEX ix_customer_references_requested_by
    ON customer_references (requested_by_user_id);
CREATE INDEX ix_customer_references_customer
    ON customer_references (customer_id, asked_on);
CREATE INDEX ix_customer_references_lead
    ON customer_references (lead_id, asked_on);
{{/sqlite}}

{{#postgresql}}
ALTER TABLE customer_references DROP CONSTRAINT ck_customer_references_outcome;
ALTER TABLE customer_references ADD CONSTRAINT ck_customer_references_outcome CHECK (
    outcome IN ('YES', 'NO', 'NOT_SHARED')
);

ALTER TABLE customer_references DROP CONSTRAINT ck_customer_references_followup;
ALTER TABLE customer_references ADD CONSTRAINT ck_customer_references_followup CHECK (
    outcome <> 'NO' OR next_reference_date IS NOT NULL
);
{{/postgresql}}
