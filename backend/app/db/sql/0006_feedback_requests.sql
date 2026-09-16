-- =====================================================================
-- 0006_feedback_requests.sql
--
-- Closing the feedback loop: the portal could ASK for feedback but had no
-- way of knowing when it arrived.
--
-- Until now "pending feedback" was DERIVED on every request - a dispatched
-- lead with no FEEDBACK_REQUESTED activity, or a customer with no response
-- on file. That works for "who still needs asking" and cannot answer
-- anything else: there is no row to carry an id, no row to change status
-- on, and nothing for a Google Form response to point back at.
--
-- feedback_requests is that row. It is created when somebody actually
-- presses Send, NOT for every askable record - "not yet asked" remains the
-- absence of a request, because modelling absence as a row is how you end
-- up with two queues that disagree.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================


-- ---------------------------------------------------- feedback_requests
CREATE TABLE feedback_requests (
    id             {{UUID}}     PRIMARY KEY,

    -- Two halves of one identifier, joined by a dot in the form field:
    --     FB-2026-00127.k7x9m2q4vB8nS1dLpR0wZ6tYcU3aH5jF
    -- `reference` is for humans - it is quoted on the phone and read in the
    -- response sheet. `token` is what matching actually trusts: a bare
    -- sequential reference is guessable, and guessing one would let a
    -- stranger attach a response to somebody else's account.
    reference      VARCHAR(20)  NOT NULL UNIQUE,
    token          VARCHAR(64)  NOT NULL UNIQUE,

    -- Exactly one of lead_id / customer_id. Leads and SAP customers are
    -- deliberately separate tables (see README, "Two kinds of record"), and
    -- the CHECK below keeps that true here rather than trusting the service.
    subject_type   VARCHAR(10)  NOT NULL,
    lead_id        {{UUID}}     REFERENCES leads(id) ON DELETE CASCADE,
    customer_id    {{UUID}}     REFERENCES customers(id) ON DELETE CASCADE,

    owner_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,

    status         VARCHAR(20)  NOT NULL DEFAULT 'SENT',
    channel        VARCHAR(20),

    sent_at        {{TS}}       NOT NULL DEFAULT {{NOW}},
    completed_at   {{TS}},
    -- After this, the request stops counting as "waiting" so the response
    -- rate has a denominator that does not grow forever.
    expires_at     {{TS}},
    created_at     {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_feedback_requests_status
        CHECK (status IN ('SENT', 'COMPLETED', 'EXPIRED', 'CANCELLED')),
    CONSTRAINT ck_feedback_requests_subject_type
        CHECK (subject_type IN ('LEAD', 'CUSTOMER')),
    -- Exactly one subject, never both, never neither.
    CONSTRAINT ck_feedback_requests_one_subject
        CHECK ((lead_id IS NULL) <> (customer_id IS NULL))
);

CREATE INDEX ix_feedback_requests_status ON feedback_requests (status, sent_at);
CREATE INDEX ix_feedback_requests_owner ON feedback_requests (owner_user_id, status);
CREATE INDEX ix_feedback_requests_lead ON feedback_requests (lead_id);
CREATE INDEX ix_feedback_requests_customer ON feedback_requests (customer_id);


-- -------------------------------------------------- feedback_sync_events
-- Every inbound delivery, whether or not it became feedback.
--
-- Written and committed BEFORE processing, so a crash mid-ingest leaves a
-- visible record instead of losing a customer's response. This is both the
-- idempotency ledger and the admin's reconciliation view.
CREATE TABLE feedback_sync_events (
    id                    {{UUID}}     PRIMARY KEY,

    -- Google's own response id. UNIQUE is the idempotency guarantee: a
    -- retried delivery hits this constraint and becomes a no-op.
    external_response_id  VARCHAR(128) NOT NULL UNIQUE,

    source                VARCHAR(20)  NOT NULL,
    -- The payload HASH, never the payload. A response carries a customer's
    -- name, mobile, email and free text; none of that belongs in a log
    -- table that exists to answer "did this arrive?".
    payload_hash          VARCHAR(64)  NOT NULL,

    status                VARCHAR(20)  NOT NULL DEFAULT 'RECEIVED',
    feedback_id           {{UUID}}     REFERENCES feedback(id) ON DELETE SET NULL,
    error_code            VARCHAR(40),
    error_detail          VARCHAR(500),

    received_at           {{TS}}       NOT NULL DEFAULT {{NOW}},
    processed_at          {{TS}},

    CONSTRAINT ck_feedback_sync_events_status
        CHECK (status IN ('RECEIVED', 'PROCESSED', 'FAILED', 'DUPLICATE')),
    CONSTRAINT ck_feedback_sync_events_source
        CHECK (source IN ('WEBHOOK', 'PULL', 'FILE'))
);

CREATE INDEX ix_feedback_sync_events_status
    ON feedback_sync_events (status, received_at);


-- ------------------------------------------------------- feedback (alter)
-- Three new columns, and one widened CHECK: `source` has to admit
-- GOOGLE_FORMS_WEBHOOK now that responses arrive without a file.
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt - the
-- same approach 0004 took with customer_references. Postgres alters in
-- place. test_schema_parity asserts the two end up equivalent.

{{#sqlite}}
CREATE TABLE feedback_rebuilt (
    id                   {{UUID}}     PRIMARY KEY,
    source_row_hash      VARCHAR(64)  UNIQUE,
    import_id            {{UUID}}     REFERENCES feedback_imports(id) ON DELETE SET NULL,
    source               VARCHAR(30)  NOT NULL DEFAULT 'GOOGLE_FORMS_IMPORT',

    submitted_at_source  {{TS}},
    customer_name        VARCHAR(200),
    company_name         VARCHAR(200),
    mobile               VARCHAR(30),
    email                VARCHAR(255),

    handled_by_name      VARCHAR(120),
    handled_by_user_id   {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,

    overall_rating       NUMERIC(4, 2),
    overall_rating_raw   VARCHAR(40),
    overall_comments     TEXT,
    would_recommend      VARCHAR(20),

    created_at           {{TS}}       NOT NULL DEFAULT {{NOW}},
    customer_id          {{UUID}}     REFERENCES customers(id) ON DELETE SET NULL,

    feedback_request_id  {{UUID}}     REFERENCES feedback_requests(id) ON DELETE SET NULL,
    match_status         VARCHAR(20)  NOT NULL DEFAULT 'MATCHED_CONTACT',
    external_response_id VARCHAR(128),

    CONSTRAINT ck_feedback_source CHECK (
        source IN ('INTERNAL', 'PUBLIC_LINK', 'GOOGLE_FORMS_IMPORT', 'GOOGLE_FORMS_WEBHOOK')
    ),
    CONSTRAINT ck_feedback_match_status CHECK (
        match_status IN ('MATCHED_TOKEN', 'MATCHED_CONTACT', 'UNMATCHED', 'DUPLICATE')
    )
);

-- Everything already on file arrived by import and was matched, if at all,
-- on contact details. That is exactly what MATCHED_CONTACT means, so the
-- default is the truth rather than a placeholder.
INSERT INTO feedback_rebuilt (
    id, source_row_hash, import_id, source, submitted_at_source,
    customer_name, company_name, mobile, email, handled_by_name,
    handled_by_user_id, overall_rating, overall_rating_raw, overall_comments,
    would_recommend, created_at, customer_id
)
SELECT
    id, source_row_hash, import_id, source, submitted_at_source,
    customer_name, company_name, mobile, email, handled_by_name,
    handled_by_user_id, overall_rating, overall_rating_raw, overall_comments,
    would_recommend, created_at, customer_id
FROM feedback;

DROP TABLE feedback;

ALTER TABLE feedback_rebuilt RENAME TO feedback;

-- Dropped with the old table, so they come back here.
CREATE INDEX ix_feedback_submitted_at ON feedback (submitted_at_source);
CREATE INDEX ix_feedback_customer ON feedback (customer_id);
{{/sqlite}}

{{#postgresql}}
ALTER TABLE feedback ADD COLUMN feedback_request_id {{UUID}} REFERENCES feedback_requests(id) ON DELETE SET NULL;
ALTER TABLE feedback ADD COLUMN match_status VARCHAR(20) NOT NULL DEFAULT 'MATCHED_CONTACT';
ALTER TABLE feedback ADD COLUMN external_response_id VARCHAR(128);

ALTER TABLE feedback DROP CONSTRAINT ck_feedback_source;
ALTER TABLE feedback ADD CONSTRAINT ck_feedback_source CHECK (
    source IN ('INTERNAL', 'PUBLIC_LINK', 'GOOGLE_FORMS_IMPORT', 'GOOGLE_FORMS_WEBHOOK')
);
ALTER TABLE feedback ADD CONSTRAINT ck_feedback_match_status CHECK (
    match_status IN ('MATCHED_TOKEN', 'MATCHED_CONTACT', 'UNMATCHED', 'DUPLICATE')
);
{{/postgresql}}

CREATE INDEX ix_feedback_request ON feedback (feedback_request_id);
CREATE INDEX ix_feedback_match_status ON feedback (match_status);
