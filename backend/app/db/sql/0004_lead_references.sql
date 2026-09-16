-- =====================================================================
-- 0004_lead_references.sql
--
-- A lead that reached CONVERTED is a won deal — the same kind of thing as
-- an invoiced SAP customer — so it can be asked for a reference too.
--
-- The two records stay in their own tables. A converted lead is NOT
-- invented into the SAP book: `customers` still means "SAP invoiced this",
-- and the reference module merges the two sources when it reads. That keeps
-- the promise in the README, and it stops a later real SAP invoice for the
-- same company arriving as a second, duplicate account.
--
-- So a reference ask now points at EITHER a customer or a lead, and a lead
-- carries the same reference roll-up a customer does.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================


-- ------------------------------------------- leads: the reference roll-up
-- Mirrors customers.reference_status / last_reference_asked_at /
-- next_reference_date, so one follow-up queue can read both.
ALTER TABLE leads ADD COLUMN reference_status VARCHAR(20) NOT NULL DEFAULT 'NOT_ASKED'
    CONSTRAINT ck_leads_reference_status
    CHECK (reference_status IN ('NOT_ASKED', 'TAKEN', 'PENDING', 'DECLINED'));

ALTER TABLE leads ADD COLUMN last_reference_asked_at DATE;
ALTER TABLE leads ADD COLUMN next_reference_date DATE;


-- ------------------------------------ customer_references: two subjects
-- `customer_id` has to become nullable and the table gains a lead_id plus a
-- CHECK that exactly one of them is set. PostgreSQL says that in two lines;
-- SQLite has neither ALTER COLUMN nor ADD CONSTRAINT, so it rebuilds the
-- table. Every leads.origin_reference_id is NULL, so no foreign key is
-- violated while the old table is briefly out of the way.

{{#postgresql}}
ALTER TABLE customer_references ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE customer_references
    ADD COLUMN lead_id {{UUID}} REFERENCES leads(id) ON DELETE CASCADE;

ALTER TABLE customer_references ADD CONSTRAINT ck_customer_references_subject
    CHECK (
        (customer_id IS NOT NULL AND lead_id IS NULL)
        OR (customer_id IS NULL AND lead_id IS NOT NULL)
    );
{{/postgresql}}

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

    CONSTRAINT ck_customer_references_outcome CHECK (outcome IN ('YES', 'NO')),
    CONSTRAINT ck_customer_references_followup CHECK (
        outcome = 'YES' OR next_reference_date IS NOT NULL
    ),
    CONSTRAINT ck_customer_references_subject CHECK (
        (customer_id IS NOT NULL AND lead_id IS NULL)
        OR (customer_id IS NULL AND lead_id IS NOT NULL)
    )
);

INSERT INTO customer_references_rebuilt (
    id, customer_id, lead_id, requested_by_user_id, outcome, asked_on,
    next_reference_date, referred_name, referred_company, referred_mobile,
    referred_email, notes, converted_lead_id, created_at, updated_at
)
SELECT
    id, customer_id, NULL, requested_by_user_id, outcome, asked_on,
    next_reference_date, referred_name, referred_company, referred_mobile,
    referred_email, notes, converted_lead_id, created_at, updated_at
FROM customer_references;

DROP TABLE customer_references;

ALTER TABLE customer_references_rebuilt RENAME TO customer_references;

-- Dropped with the old table, so they come back here.
CREATE INDEX ix_customer_references_requested_by
    ON customer_references (requested_by_user_id);
CREATE INDEX ix_customer_references_customer
    ON customer_references (customer_id, asked_on);
{{/sqlite}}

CREATE INDEX ix_customer_references_lead ON customer_references (lead_id, asked_on);
