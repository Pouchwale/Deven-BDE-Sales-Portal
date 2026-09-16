-- Rendered for postgresql by app/db/migrate.py
-- Generated 2026-09-16T06:28:34+00:00
-- Do not edit: edit app/db/sql/*.sql and re-render.

-- ===== 0001_initial.sql =====
-- =====================================================================
-- 0001_initial.sql - BDE & Sales Portal v3 baseline schema
--
-- THIS FILE IS THE SOURCE OF TRUTH FOR THE DATABASE SCHEMA.
-- The SQLAlchemy models in app/models mirror it; tests/test_schema_parity.py
-- fails the build if the two drift apart.
--
-- Dialect placeholders are substituted by app/db/render.py. Named without
-- their braces here so this legend survives rendering intact:
--   UUID   ->  CHAR(36)          on SQLite,  uuid        on PostgreSQL
--   JSON   ->  TEXT              on SQLite,  jsonb       on PostgreSQL
--   TS     ->  TIMESTAMP         on SQLite,  timestamptz on PostgreSQL
--   BOOL   ->  BOOLEAN           on SQLite,  boolean     on PostgreSQL
--   NOW    ->  CURRENT_TIMESTAMP on SQLite,  now()       on PostgreSQL
--
-- Render the production DDL for review with:
--   python -m app.db.migrate render --dialect postgresql
--
-- Conventions
--   * UUID primary keys are generated in Python (uuid4), never by the
--     database, so both dialects behave identically.
--   * Timestamps are stored UTC. Asia/Kolkata is a presentation concern.
--   * Ratings are NUMERIC, never float.
-- =====================================================================


-- ------------------------------------------------------------ teams
-- A flat grouping label for reporting. A team grants NO authority;
-- all authority flows through users.manager_id. (plan v3 s2.3)
CREATE TABLE teams (
    id          uuid      PRIMARY KEY,
    name        VARCHAR(60)   NOT NULL UNIQUE,
    code        VARCHAR(20)   NOT NULL UNIQUE,
    is_active   boolean      NOT NULL DEFAULT TRUE,
    sort_order  INTEGER       NOT NULL DEFAULT 0,
    created_at  timestamptz        NOT NULL DEFAULT now()
);


-- ------------------------------------------------------ departments
-- The dimension customer feedback is rated against. Rows are discovered
-- from the real feedback export, never invented. (plan v3 s8.3)
CREATE TABLE departments (
    id          uuid      PRIMARY KEY,
    name        VARCHAR(80)   NOT NULL UNIQUE,
    code        VARCHAR(20)   NOT NULL UNIQUE,
    is_active   boolean      NOT NULL DEFAULT TRUE,
    sort_order  INTEGER       NOT NULL DEFAULT 0,
    created_at  timestamptz        NOT NULL DEFAULT now()
);


-- ------------------------------------------------------------ users
-- manager_id is the spine of the whole permission model: a manager sees
-- their own subtree at any depth, nothing sideways and nothing above.
-- heads_department_id is an ATTRIBUTE, not a role - it grants a feedback
-- read scope and never any authority over people. (plan v3 s8.7)
CREATE TABLE users (
    id                    uuid     PRIMARY KEY,
    name                  VARCHAR(120) NOT NULL,
    email                 VARCHAR(255) NOT NULL UNIQUE,
    phone                 VARCHAR(30),
    role                  VARCHAR(20)  NOT NULL,
    title                 VARCHAR(80),

    manager_id            uuid     REFERENCES users(id)       ON DELETE SET NULL,
    team_id               uuid     REFERENCES teams(id)       ON DELETE SET NULL,
    heads_department_id   uuid     REFERENCES departments(id) ON DELETE SET NULL,

    hashed_password       VARCHAR(255) NOT NULL,
    is_active             boolean     NOT NULL DEFAULT TRUE,
    must_change_password  boolean     NOT NULL DEFAULT FALSE,
    -- Tokens issued before this moment are rejected, so a password change
    -- or an admin reset invalidates every existing session immediately.
    password_changed_at   timestamptz,
    deactivated_at        timestamptz,

    created_at            timestamptz       NOT NULL DEFAULT now(),
    updated_at            timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_users_role CHECK (
        role IN ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'BDE', 'SALES')
    ),
    -- A user may not be their own manager. Longer cycles cannot be
    -- expressed as a CHECK; they are rejected in app/core/authority.py.
    CONSTRAINT ck_users_not_self_managed CHECK (manager_id IS NULL OR manager_id <> id)
);

CREATE INDEX ix_users_manager_id ON users (manager_id);
CREATE INDEX ix_users_role       ON users (role);
CREATE INDEX ix_users_team_id    ON users (team_id);

-- One head per department (plan v3 s8.7). Partial unique index so the many
-- users with no department do not collide. Both dialects support this.
CREATE UNIQUE INDEX ux_one_head_per_department
    ON users (heads_department_id)
    WHERE heads_department_id IS NOT NULL;


-- ----------------------------------------------------- app_settings
-- Runtime configuration an admin can change without a deploy.
CREATE TABLE app_settings (
    key                VARCHAR(60) PRIMARY KEY,
    value              TEXT,
    value_type         VARCHAR(10) NOT NULL DEFAULT 'str',
    description        VARCHAR(255),
    updated_by_user_id uuid    REFERENCES users(id) ON DELETE SET NULL,
    updated_at         timestamptz      NOT NULL DEFAULT now(),

    CONSTRAINT ck_app_settings_type CHECK (
        value_type IN ('str', 'int', 'float', 'bool', 'json')
    )
);


-- ------------------------------------------------------ sap_imports
-- One row per SAP customer/invoice extract that was ingested. Keeps the
-- resolved column mapping as an audit trail, like feedback_imports.
CREATE TABLE sap_imports (
    id                  uuid     PRIMARY KEY,
    filename            VARCHAR(255) NOT NULL,
    source              VARCHAR(30)  NOT NULL DEFAULT 'SAP_FILE',
    uploaded_by_user_id uuid     REFERENCES users(id) ON DELETE SET NULL,
    column_map          jsonb,
    total_rows          INTEGER      NOT NULL DEFAULT 0,
    created_count       INTEGER      NOT NULL DEFAULT 0,
    updated_count       INTEGER      NOT NULL DEFAULT 0,
    skipped_count       INTEGER      NOT NULL DEFAULT 0,
    error_count         INTEGER      NOT NULL DEFAULT 0,
    errors              jsonb,
    status              VARCHAR(20)  NOT NULL DEFAULT 'SUCCESS',
    created_at          timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_sap_imports_status CHECK (status IN ('SUCCESS', 'PARTIAL', 'FAILED')),
    CONSTRAINT ck_sap_imports_source CHECK (source IN ('SAP_FILE', 'SAP_B1_DB'))
);


-- -------------------------------------------------------- customers
-- A converted customer: someone SAP has invoiced. This is the pool the
-- reference module works against.
CREATE TABLE customers (
    id                  uuid      PRIMARY KEY,
    sap_code            VARCHAR(30)   NOT NULL UNIQUE,
    name                VARCHAR(200)  NOT NULL,
    mobile              VARCHAR(30),
    email               VARCHAR(255),

    -- Nullable on purpose: an unowned customer is visible to ADMIN and
    -- SUPER_ADMIN only, and is the pool a manager assigns from. (plan v3 s6)
    owner_user_id       uuid      REFERENCES users(id) ON DELETE SET NULL,
    -- The salesperson name exactly as SAP spelled it, kept even when it
    -- cannot be resolved to a portal account.
    sap_sales_person    VARCHAR(120),

    is_converted        boolean      NOT NULL DEFAULT TRUE,
    first_invoice_date  DATE,
    last_invoice_date   DATE,
    invoice_count       INTEGER       NOT NULL DEFAULT 0,

    -- Reference tracking roll-up.
    reference_status        VARCHAR(20) NOT NULL DEFAULT 'NOT_ASKED',
    last_reference_asked_at DATE,
    next_reference_date     DATE,

    import_id           uuid      REFERENCES sap_imports(id) ON DELETE SET NULL,
    created_at          timestamptz        NOT NULL DEFAULT now(),
    updated_at          timestamptz        NOT NULL DEFAULT now(),

    CONSTRAINT ck_customers_reference_status CHECK (
        reference_status IN ('NOT_ASKED', 'TAKEN', 'PENDING', 'DECLINED')
    )
);

CREATE INDEX ix_customers_owner_user_id ON customers (owner_user_id);
-- Drives the "reference follow-ups due" bucket of the work queue.
CREATE INDEX ix_customers_followup
    ON customers (reference_status, next_reference_date, owner_user_id);


-- ----------------------------------------------------- invoice_lines
-- One row per SAP invoice line. An invoice number legitimately repeats
-- across lines (one invoice, several items), so the idempotency key is a
-- hash of the whole normalised source row, never the invoice number.
CREATE TABLE invoice_lines (
    id               uuid     PRIMARY KEY,
    customer_id      uuid     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    invoice_no       VARCHAR(40)  NOT NULL,
    invoice_date     DATE,
    fgpo_code        VARCHAR(40),
    item_description TEXT,
    sales_person     VARCHAR(120),
    owner_user_id    uuid     REFERENCES users(id) ON DELETE SET NULL,
    source_row_hash  VARCHAR(64)  NOT NULL UNIQUE,
    import_id        uuid     REFERENCES sap_imports(id) ON DELETE SET NULL,
    created_at       timestamptz       NOT NULL DEFAULT now()
);

CREATE INDEX ix_invoice_lines_customer ON invoice_lines (customer_id, invoice_date);
CREATE INDEX ix_invoice_lines_invoice_no ON invoice_lines (invoice_no);


-- ---------------------------------------------- customer_references
-- "Did this converted customer give us a reference?" One row per ask.
CREATE TABLE customer_references (
    id                    uuid     PRIMARY KEY,
    customer_id           uuid     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    -- Who gets the credit. Scoped by visible_user_ids on every list.
    requested_by_user_id  uuid     REFERENCES users(id) ON DELETE SET NULL,

    outcome               VARCHAR(20)  NOT NULL,
    asked_on              DATE         NOT NULL,
    next_reference_date   DATE,

    referred_name         VARCHAR(120),
    referred_company      VARCHAR(200),
    referred_mobile       VARCHAR(30),
    referred_email        VARCHAR(255),
    notes                 TEXT,

    -- Set when this reference was turned into a lead, so the follow-up
    -- queue can show what came of it.
    converted_lead_id     uuid,

    created_at            timestamptz       NOT NULL DEFAULT now(),
    updated_at            timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_customer_references_outcome CHECK (outcome IN ('YES', 'NO')),
    -- A "no, ask me later" must carry the date to ask again on.
    CONSTRAINT ck_customer_references_followup CHECK (
        outcome = 'YES' OR next_reference_date IS NOT NULL
    )
);

CREATE INDEX ix_customer_references_requested_by
    ON customer_references (requested_by_user_id);
CREATE INDEX ix_customer_references_customer
    ON customer_references (customer_id, asked_on);


-- ------------------------------------------------------------ leads
-- origin splits the two genuinely different kinds of work (plan v3 s7).
CREATE TABLE leads (
    id                   uuid     PRIMARY KEY,
    name                 VARCHAR(120) NOT NULL,
    company_name         VARCHAR(200),
    mobile               VARCHAR(30),
    email                VARCHAR(255),
    city                 VARCHAR(80),
    requirement          TEXT,

    origin               VARCHAR(30)  NOT NULL DEFAULT 'ASSIGNED_BY_HEAD',
    origin_reference_id  uuid     REFERENCES customer_references(id) ON DELETE SET NULL,

    status               VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    priority             VARCHAR(10)  NOT NULL DEFAULT 'MEDIUM',

    assigned_to_user_id  uuid     REFERENCES users(id) ON DELETE SET NULL,
    assigned_by_user_id  uuid     REFERENCES users(id) ON DELETE SET NULL,
    assigned_at          timestamptz,
    next_follow_up_date  DATE,
    closed_at            timestamptz,

    created_at           timestamptz       NOT NULL DEFAULT now(),
    updated_at           timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_leads_origin CHECK (
        origin IN ('ASSIGNED_BY_HEAD', 'REFERENCE_FOLLOWUP')
    ),
    CONSTRAINT ck_leads_status CHECK (
        status IN ('PENDING', 'CONTACTED', 'FOLLOW_UP', 'QUALIFIED', 'CONVERTED', 'LOST')
    ),
    CONSTRAINT ck_leads_priority CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH')),
    -- A reference follow-up lead must say which reference it came from.
    CONSTRAINT ck_leads_reference_origin CHECK (
        origin <> 'REFERENCE_FOLLOWUP' OR origin_reference_id IS NOT NULL
    )
);

CREATE INDEX ix_leads_origin_assignee ON leads (origin, assigned_to_user_id, status);
CREATE INDEX ix_leads_assignee ON leads (assigned_to_user_id, status);


-- -------------------------------------------------- lead_activities
CREATE TABLE lead_activities (
    id             uuid    PRIMARY KEY,
    lead_id        uuid    NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    actor_user_id  uuid    REFERENCES users(id) ON DELETE SET NULL,
    activity_type  VARCHAR(30) NOT NULL,
    from_status    VARCHAR(20),
    to_status      VARCHAR(20),
    remark         TEXT,
    created_at     timestamptz      NOT NULL DEFAULT now()
);

CREATE INDEX ix_lead_activities_lead ON lead_activities (lead_id, created_at);


-- -------------------------------------------------- feedback_imports
-- One row per uploaded Google Forms export. column_map records the mapping
-- the admin actually confirmed, so a later form edit is diagnosable.
CREATE TABLE feedback_imports (
    id                  uuid     PRIMARY KEY,
    filename            VARCHAR(255) NOT NULL,
    source              VARCHAR(30)  NOT NULL DEFAULT 'GOOGLE_FORMS_XLSX',
    uploaded_by_user_id uuid     REFERENCES users(id) ON DELETE SET NULL,
    column_map          jsonb,
    total_rows          INTEGER      NOT NULL DEFAULT 0,
    created_count       INTEGER      NOT NULL DEFAULT 0,
    skipped_count       INTEGER      NOT NULL DEFAULT 0,
    error_count         INTEGER      NOT NULL DEFAULT 0,
    errors              jsonb,
    status              VARCHAR(20)  NOT NULL DEFAULT 'SUCCESS',
    created_at          timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_feedback_imports_status CHECK (
        status IN ('SUCCESS', 'PARTIAL', 'FAILED')
    )
);


-- --------------------------------------------------------- feedback
CREATE TABLE feedback (
    id                   uuid     PRIMARY KEY,
    -- sha256 of the normalised source row. Unique, so re-importing the
    -- same export creates nothing. (plan v3 s8.1)
    source_row_hash      VARCHAR(64)  UNIQUE,
    import_id            uuid     REFERENCES feedback_imports(id) ON DELETE SET NULL,
    source               VARCHAR(30)  NOT NULL DEFAULT 'GOOGLE_FORMS_IMPORT',

    submitted_at_source  timestamptz,
    customer_name        VARCHAR(200),
    company_name         VARCHAR(200),
    mobile               VARCHAR(30),
    email                VARCHAR(255),

    -- The salesperson the respondent named, and the account it resolved to.
    handled_by_name      VARCHAR(120),
    handled_by_user_id   uuid     REFERENCES users(id) ON DELETE SET NULL,

    overall_rating       NUMERIC(4, 2),
    overall_rating_raw   VARCHAR(40),
    overall_comments     TEXT,
    would_recommend      VARCHAR(20),

    created_at           timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_feedback_source CHECK (
        source IN ('INTERNAL', 'PUBLIC_LINK', 'GOOGLE_FORMS_IMPORT')
    )
);

CREATE INDEX ix_feedback_submitted_at ON feedback (submitted_at_source);


-- ------------------------------------- feedback_department_ratings
-- raw_value keeps the source wording ("4 - Satisfied", "8/10") next to the
-- normalised number so a scale change never silently rewrites history.
CREATE TABLE feedback_department_ratings (
    id            uuid      PRIMARY KEY,
    feedback_id   uuid      NOT NULL REFERENCES feedback(id)    ON DELETE CASCADE,
    department_id uuid      NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    rating        NUMERIC(4, 2),
    raw_value     VARCHAR(40),
    comments      TEXT,
    created_at    timestamptz        NOT NULL DEFAULT now(),

    CONSTRAINT ux_feedback_department UNIQUE (feedback_id, department_id)
);

CREATE INDEX ix_fdr_department ON feedback_department_ratings (department_id, created_at);


-- -------------------------------------------------- feedback_alerts
CREATE TABLE feedback_alerts (
    id              uuid      PRIMARY KEY,
    department_id   uuid      NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    status          VARCHAR(20)   NOT NULL DEFAULT 'OPEN',
    average_rating  NUMERIC(4, 2) NOT NULL,
    response_count  INTEGER       NOT NULL,
    threshold       NUMERIC(4, 2) NOT NULL,
    window_days     INTEGER       NOT NULL,
    opened_at       timestamptz        NOT NULL DEFAULT now(),
    resolved_at     timestamptz,
    resolved_reason VARCHAR(255),

    CONSTRAINT ck_feedback_alerts_status CHECK (status IN ('OPEN', 'RESOLVED'))
);

-- At most one OPEN alert per department, enforced by the database rather
-- than by a read-then-write race in the application. (plan v3 s8.5)
CREATE UNIQUE INDEX ux_open_alert_per_dept
    ON feedback_alerts (department_id)
    WHERE status = 'OPEN';


-- ---------------------------------------------------- notifications
CREATE TABLE notifications (
    id          uuid     PRIMARY KEY,
    user_id     uuid     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        VARCHAR(40)  NOT NULL,
    title       VARCHAR(200) NOT NULL,
    body        TEXT,
    entity_type VARCHAR(30),
    entity_id   uuid,
    -- Lets the lazily-generated follow-up reminders fire once per customer
    -- per day without a scheduler. (plan v3 s10)
    dedupe_key  VARCHAR(160) UNIQUE,
    is_read     boolean     NOT NULL DEFAULT FALSE,
    read_at     timestamptz,
    created_at  timestamptz       NOT NULL DEFAULT now()
);

CREATE INDEX ix_notifications_user ON notifications (user_id, is_read, created_at);


-- ----------------------------------------------------- audit_events
-- Every authority-gated mutation writes one row, inside the same
-- transaction as the change itself. (plan v3 s9.5)
CREATE TABLE audit_events (
    id            uuid    PRIMARY KEY,
    actor_user_id uuid    REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR(60) NOT NULL,
    entity_type   VARCHAR(30),
    entity_id     uuid,
    before        jsonb,
    after         jsonb,
    ip_address    VARCHAR(45),
    created_at    timestamptz      NOT NULL DEFAULT now()
);

CREATE INDEX ix_audit_events_actor ON audit_events (actor_user_id, created_at);
CREATE INDEX ix_audit_events_entity ON audit_events (entity_type, entity_id);

-- ===== 0002_timelines_and_undo.sql =====
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
ALTER TABLE lead_activities ADD COLUMN undone_at timestamptz;
ALTER TABLE lead_activities ADD COLUMN undone_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX ix_lead_activities_undone ON lead_activities (lead_id, undone_at);


-- ------------------------------------------------ customer_activities
-- The timeline of a completed customer: feedback asked for and received,
-- Google review requested, calls, notes. Reference asks live in
-- customer_references and are merged into the same view at read time, so the
-- history is not duplicated in two places.
CREATE TABLE customer_activities (
    id                uuid     PRIMARY KEY,
    customer_id       uuid     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    actor_user_id     uuid     REFERENCES users(id) ON DELETE SET NULL,
    activity_type     VARCHAR(30)  NOT NULL,
    remark            TEXT,
    -- Set when the entry points at something concrete: a feedback response,
    -- a reference row, the outbound review link.
    related_type      VARCHAR(30),
    related_id        uuid,
    undone_at         timestamptz,
    undone_by_user_id uuid     REFERENCES users(id) ON DELETE SET NULL,
    created_at        timestamptz       NOT NULL DEFAULT now(),

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
ALTER TABLE feedback ADD COLUMN customer_id uuid REFERENCES customers(id) ON DELETE SET NULL;

CREATE INDEX ix_feedback_customer ON feedback (customer_id);

-- ===== 0003_dispatch_and_alerts.sql =====
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

ALTER TABLE leads ADD COLUMN dispatched_at timestamptz;

ALTER TABLE feedback_alerts ADD COLUMN assigned_to_user_id uuid REFERENCES users(id) ON DELETE SET NULL;

-- ===== 0004_lead_references.sql =====
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

ALTER TABLE customer_references ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE customer_references
    ADD COLUMN lead_id uuid REFERENCES leads(id) ON DELETE CASCADE;

ALTER TABLE customer_references ADD CONSTRAINT ck_customer_references_subject
    CHECK (
        (customer_id IS NOT NULL AND lead_id IS NULL)
        OR (customer_id IS NULL AND lead_id IS NOT NULL)
    );



CREATE INDEX ix_customer_references_lead ON customer_references (lead_id, asked_on);

-- ===== 0005_chat.sql =====
-- =====================================================================
-- 0005_chat.sql
--
-- The in-portal AI assistant's own storage: conversations, the messages
-- in them, and metadata about the tools each answer called.
--
-- What is deliberately NOT here: the tool RESULTS. Storing the rows a tool
-- returned would create a second copy of customer data sitting outside the
-- authority model that produced it - exactly the thing this design exists
-- to avoid. We keep the tool name, its arguments and a row count, which is
-- what an audit needs, and nothing that a leak would hand over.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================


-- ---------------------------------------------------- chat_conversations
CREATE TABLE chat_conversations (
    id          uuid     PRIMARY KEY,
    user_id     uuid     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(120),

    -- A hash of the things that decide what this user may see (role, manager,
    -- department headship). Re-derived on every turn: when it no longer
    -- matches, the history is replayed as text only, so results fetched under
    -- the old permissions are not fed back to the model.
    permission_fingerprint VARCHAR(64) NOT NULL,

    created_at  timestamptz       NOT NULL DEFAULT now(),
    updated_at  timestamptz       NOT NULL DEFAULT now()
);

CREATE INDEX ix_chat_conversations_user
    ON chat_conversations (user_id, updated_at);


-- -------------------------------------------------------- chat_messages
CREATE TABLE chat_messages (
    id               uuid    PRIMARY KEY,
    conversation_id  uuid    NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    role             VARCHAR(20) NOT NULL,
    content          TEXT        NOT NULL,

    -- Recorded so "what does this feature cost" is a query, not a guess.
    input_tokens     INTEGER,
    output_tokens    INTEGER,

    created_at       timestamptz      NOT NULL DEFAULT now(),

    CONSTRAINT ck_chat_messages_role CHECK (role IN ('USER', 'ASSISTANT'))
);

CREATE INDEX ix_chat_messages_conversation
    ON chat_messages (conversation_id, created_at);


-- ------------------------------------------------------ chat_tool_calls
CREATE TABLE chat_tool_calls (
    id           uuid     PRIMARY KEY,
    message_id   uuid     NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    tool_name    VARCHAR(60)  NOT NULL,

    -- The arguments the model asked for - never the rows that came back.
    arguments    jsonb,
    row_count    INTEGER,

    ok           boolean     NOT NULL DEFAULT 1,
    error_code   VARCHAR(40),
    duration_ms  INTEGER,

    created_at   timestamptz       NOT NULL DEFAULT now()
);

CREATE INDEX ix_chat_tool_calls_message
    ON chat_tool_calls (message_id, created_at);

-- ===== 0006_feedback_requests.sql =====
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
    id             uuid     PRIMARY KEY,

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
    lead_id        uuid     REFERENCES leads(id) ON DELETE CASCADE,
    customer_id    uuid     REFERENCES customers(id) ON DELETE CASCADE,

    owner_user_id  uuid     REFERENCES users(id) ON DELETE SET NULL,

    status         VARCHAR(20)  NOT NULL DEFAULT 'SENT',
    channel        VARCHAR(20),

    sent_at        timestamptz       NOT NULL DEFAULT now(),
    completed_at   timestamptz,
    -- After this, the request stops counting as "waiting" so the response
    -- rate has a denominator that does not grow forever.
    expires_at     timestamptz,
    created_at     timestamptz       NOT NULL DEFAULT now(),

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
    id                    uuid     PRIMARY KEY,

    -- Google's own response id. UNIQUE is the idempotency guarantee: a
    -- retried delivery hits this constraint and becomes a no-op.
    external_response_id  VARCHAR(128) NOT NULL UNIQUE,

    source                VARCHAR(20)  NOT NULL,
    -- The payload HASH, never the payload. A response carries a customer's
    -- name, mobile, email and free text; none of that belongs in a log
    -- table that exists to answer "did this arrive?".
    payload_hash          VARCHAR(64)  NOT NULL,

    status                VARCHAR(20)  NOT NULL DEFAULT 'RECEIVED',
    feedback_id           uuid     REFERENCES feedback(id) ON DELETE SET NULL,
    error_code            VARCHAR(40),
    error_detail          VARCHAR(500),

    received_at           timestamptz       NOT NULL DEFAULT now(),
    processed_at          timestamptz,

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


ALTER TABLE feedback ADD COLUMN feedback_request_id uuid REFERENCES feedback_requests(id) ON DELETE SET NULL;
ALTER TABLE feedback ADD COLUMN match_status VARCHAR(20) NOT NULL DEFAULT 'MATCHED_CONTACT';
ALTER TABLE feedback ADD COLUMN external_response_id VARCHAR(128);

ALTER TABLE feedback DROP CONSTRAINT ck_feedback_source;
ALTER TABLE feedback ADD CONSTRAINT ck_feedback_source CHECK (
    source IN ('INTERNAL', 'PUBLIC_LINK', 'GOOGLE_FORMS_IMPORT', 'GOOGLE_FORMS_WEBHOOK')
);
ALTER TABLE feedback ADD CONSTRAINT ck_feedback_match_status CHECK (
    match_status IN ('MATCHED_TOKEN', 'MATCHED_CONTACT', 'UNMATCHED', 'DUPLICATE')
);


CREATE INDEX ix_feedback_request ON feedback (feedback_request_id);
CREATE INDEX ix_feedback_match_status ON feedback (match_status);

-- ===== 0007_honorific.sql =====
-- =====================================================================
-- 0007_honorific.sql
--
-- How a person is addressed, when the company addresses them that way.
--
-- Deliberately a STORED, OPTIONAL field an administrator sets - not
-- something derived from the name or the role. Inferring "sir" or "ma'am"
-- from a first name means guessing somebody's gender from a string, which
-- is wrong often enough to be insulting, and wrong silently. Deriving it
-- from rank alone would be worse: it would address every manager the same
-- way regardless of what they actually go by.
--
-- Null is the normal case and the default. Most people are addressed by
-- name.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

ALTER TABLE users ADD COLUMN honorific VARCHAR(10);

-- ===== 0008_remove_google_review.sql =====
-- =====================================================================
-- 0008_remove_google_review.sql
--
-- The Google review feature is gone.
--
-- The portal used to offer an outbound link to the company's Google review
-- page and log that somebody had handed it over. That is removed: the only
-- feedback the portal now asks for is its own, carrying an FB reference
-- code it can match a response back to.
--
-- What this migration does NOT touch, on purpose:
--
--   * customer_activities rows of type REVIEW_REQUESTED. They record things
--     that really happened. Deleting them would rewrite history to say the
--     ask never occurred. The enum member is kept for the same reason, so
--     the timeline still renders a label for them.
--   * audit_events rows with action REVIEW_REQUESTED, for the same reason,
--     and because an audit trail you edit is not an audit trail.
--
-- What it removes is configuration that nothing reads any more: four rows in
-- app_settings that would otherwise sit there forever, editable in the admin
-- panel, with no effect on anything.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

DELETE FROM app_settings
WHERE key IN (
    'company.google_review_url',
    'message.review_whatsapp',
    'message.review_email_subject',
    'message.review_email_body'
);

-- ===== 0009_reference_not_shared.sql =====
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


ALTER TABLE customer_references DROP CONSTRAINT ck_customer_references_outcome;
ALTER TABLE customer_references ADD CONSTRAINT ck_customer_references_outcome CHECK (
    outcome IN ('YES', 'NO', 'NOT_SHARED')
);

ALTER TABLE customer_references DROP CONSTRAINT ck_customer_references_followup;
ALTER TABLE customer_references ADD CONSTRAINT ck_customer_references_followup CHECK (
    outcome <> 'NO' OR next_reference_date IS NOT NULL
);

-- ===== 0010_lead_pipeline.sql =====
-- =====================================================================
-- 0010_lead_pipeline.sql
--
-- The lead pipeline, rebuilt to the stages the business actually works.
--
-- BEFORE                          AFTER
--   PENDING                         NEW
--   CONTACTED                       CONTACTED
--   -                               NOT_CONTACTED
--   FOLLOW_UP                       NURTURING
--   -                               PRE_QUALIFIED
--   QUALIFIED                       QUALIFIED
--   CONVERTED                       CONVERTED
--   -                               JUNK
--   LOST                            LOST
--
-- Two genuinely new ideas, not renames:
--
--   NOT_CONTACTED  "we have not reached them yet" was previously
--                  indistinguishable from "nobody has touched this", so a
--                  lead somebody had chased three times looked identical to
--                  one nobody had opened.
--   JUNK           a lead that was never real. It used to be filed as LOST,
--                  which put fake leads into the lost-deal numbers and made
--                  the conversion rate look worse than the team's work was.
--
-- PRE_QUALIFIED splits what used to be one jump from CONTACTED to QUALIFIED,
-- so "we think this is real" and "this is ready to close" stop being the
-- same number on a report.
--
-- DATA MIGRATION
-- --------------
-- Two renames, applied to the `leads` table only:
--
--   PENDING    -> NEW
--   FOLLOW_UP  -> NURTURING
--
-- `lead_activities.from_status` / `to_status` are deliberately NOT rewritten.
-- Those rows record what the system called the stage at the time somebody
-- moved it, and editing them would make the timeline claim a move that never
-- happened under that name. `constants.RETIRED_LEAD_STATUSES` carries the old
-- labels so the UI can still render them.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

-- The rename happens INSIDE the rebuild, not before it. The old CHECK is
-- still in force until the old table is dropped, and it does not know the
-- word NEW - so updating first fails on the very rows the migration exists to
-- fix. PostgreSQL drops the constraint first instead, below.


ALTER TABLE leads DROP CONSTRAINT ck_leads_status;

UPDATE leads SET status = 'NEW'       WHERE status = 'PENDING';
UPDATE leads SET status = 'NURTURING' WHERE status = 'FOLLOW_UP';

ALTER TABLE leads ALTER COLUMN status SET DEFAULT 'NEW';
ALTER TABLE leads ADD CONSTRAINT ck_leads_status CHECK (
    status IN (
        'NEW', 'CONTACTED', 'NOT_CONTACTED', 'NURTURING',
        'PRE_QUALIFIED', 'QUALIFIED', 'CONVERTED', 'JUNK', 'LOST'
    )
);

CREATE INDEX ix_leads_assigner ON leads (assigned_by_user_id, assigned_to_user_id);

-- ===== 0011_post_sale_records.sql =====
-- =====================================================================
-- 0011_post_sale_records.sql
--
-- The link between a won portal lead and what the business knows about it
-- after the sale.
--
-- THE PROBLEM THIS SOLVES
-- -----------------------
-- `leads` and `customers` were two unrelated universes. Leads came from the
-- portal; customers came from SAP; nothing joined them. So Assigned Leads
-- could say "22 converted" while Reference Tracking said "17 accounts", and
-- both were right about different things. Measured on the live database:
-- ZERO of the 17 SAP customers matched any of the 67 portal leads on mobile,
-- email or company name. They were never the same records.
--
-- From here the portal has one operational universe - leads - and this table
-- carries what the external sheet adds to a won one.
--
-- WHY NOT JUST REUSE `customers`
-- ------------------------------
-- Because `customers` means "SAP invoiced this", and that meaning is worth
-- keeping for the history already in it. A post-sale record means "the sheet
-- says this lead was delivered and invoiced on this date". Overloading one
-- table with both would put us back where we started.
--
-- lead_id is NULLABLE on purpose. A row whose lead cannot be identified is
-- held as UNMATCHED for a human to resolve. Attaching it to whichever lead
-- looked closest is how a reference ask reaches the wrong customer.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

CREATE TABLE post_sale_records (
    id             uuid     PRIMARY KEY,

    lead_id        uuid     REFERENCES leads(id) ON DELETE CASCADE,
    external_ref   VARCHAR(128) UNIQUE,
    source         VARCHAR(30)  NOT NULL DEFAULT 'EXCEL_SYNC',
    status         VARCHAR(20)  NOT NULL DEFAULT 'UNMATCHED',
    matched_on     VARCHAR(20),

    -- The date the 10-day eligibility rule runs from. Named for the business
    -- meaning, not for whichever column the sheet uses; the importer maps it.
    invoice_date   DATE,

    customer_name  VARCHAR(200),
    company_name   VARCHAR(200),
    mobile         VARCHAR(30),
    email          VARCHAR(255),
    notes          TEXT,

    synced_at      timestamptz,
    created_at     timestamptz       NOT NULL DEFAULT now(),
    updated_at     timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT ck_post_sale_status CHECK (
        status IN ('MATCHED', 'UNMATCHED', 'NEEDS_REVIEW')
    ),
    -- A matched row must say which lead, and an unmatched one must not claim
    -- to have found it. The two halves cannot drift apart.
    CONSTRAINT ck_post_sale_match CHECK (
        (status = 'MATCHED' AND lead_id IS NOT NULL)
        OR (status <> 'MATCHED' AND lead_id IS NULL)
    )
);

CREATE INDEX ix_post_sale_lead ON post_sale_records (lead_id);
CREATE INDEX ix_post_sale_status ON post_sale_records (status, invoice_date);

-- ===== 0012_feedback_request_issued.sql =====
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

-- ===== 0013_plain_password.sql =====
-- Add plain_password column to users table for Super Admin viewing convenience
ALTER TABLE users ADD COLUMN plain_password VARCHAR(255);
UPDATE users SET plain_password = 'ChangeMe@123' WHERE plain_password IS NULL;

-- ===== 0014_reference_date.sql =====
-- =====================================================================
-- 0014_reference_date.sql
--
-- The SAP workbook carries its own "Reference Date" column - the day the
-- business says an account may be asked for a reference. Until now the
-- portal recomputed that date itself (invoice date + 10 days) and never
-- read the column, so the two could silently disagree the day SAP changed
-- the rule.
--
-- From here the column is stored and is what the queue runs on:
--
--   invoice_lines.reference_date      what the sheet said, per row
--   post_sale_records.reference_date  the ONE date a won lead is asked from
--
-- Both are NULLable: a lead that never came from the workbook has no such
-- column, and core/eligibility.py falls back to invoice date + 10 days for
-- those, exactly as before.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

ALTER TABLE invoice_lines ADD COLUMN reference_date DATE;

ALTER TABLE post_sale_records ADD COLUMN reference_date DATE;

