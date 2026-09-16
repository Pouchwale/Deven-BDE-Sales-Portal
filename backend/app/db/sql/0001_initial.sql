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
    id          {{UUID}}      PRIMARY KEY,
    name        VARCHAR(60)   NOT NULL UNIQUE,
    code        VARCHAR(20)   NOT NULL UNIQUE,
    is_active   {{BOOL}}      NOT NULL DEFAULT TRUE,
    sort_order  INTEGER       NOT NULL DEFAULT 0,
    created_at  {{TS}}        NOT NULL DEFAULT {{NOW}}
);


-- ------------------------------------------------------ departments
-- The dimension customer feedback is rated against. Rows are discovered
-- from the real feedback export, never invented. (plan v3 s8.3)
CREATE TABLE departments (
    id          {{UUID}}      PRIMARY KEY,
    name        VARCHAR(80)   NOT NULL UNIQUE,
    code        VARCHAR(20)   NOT NULL UNIQUE,
    is_active   {{BOOL}}      NOT NULL DEFAULT TRUE,
    sort_order  INTEGER       NOT NULL DEFAULT 0,
    created_at  {{TS}}        NOT NULL DEFAULT {{NOW}}
);


-- ------------------------------------------------------------ users
-- manager_id is the spine of the whole permission model: a manager sees
-- their own subtree at any depth, nothing sideways and nothing above.
-- heads_department_id is an ATTRIBUTE, not a role - it grants a feedback
-- read scope and never any authority over people. (plan v3 s8.7)
CREATE TABLE users (
    id                    {{UUID}}     PRIMARY KEY,
    name                  VARCHAR(120) NOT NULL,
    email                 VARCHAR(255) NOT NULL UNIQUE,
    phone                 VARCHAR(30),
    role                  VARCHAR(20)  NOT NULL,
    title                 VARCHAR(80),

    manager_id            {{UUID}}     REFERENCES users(id)       ON DELETE SET NULL,
    team_id               {{UUID}}     REFERENCES teams(id)       ON DELETE SET NULL,
    heads_department_id   {{UUID}}     REFERENCES departments(id) ON DELETE SET NULL,

    hashed_password       VARCHAR(255) NOT NULL,
    is_active             {{BOOL}}     NOT NULL DEFAULT TRUE,
    must_change_password  {{BOOL}}     NOT NULL DEFAULT FALSE,
    -- Tokens issued before this moment are rejected, so a password change
    -- or an admin reset invalidates every existing session immediately.
    password_changed_at   {{TS}},
    deactivated_at        {{TS}},

    created_at            {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at            {{TS}}       NOT NULL DEFAULT {{NOW}},

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
    updated_by_user_id {{UUID}}    REFERENCES users(id) ON DELETE SET NULL,
    updated_at         {{TS}}      NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_app_settings_type CHECK (
        value_type IN ('str', 'int', 'float', 'bool', 'json')
    )
);


-- ------------------------------------------------------ sap_imports
-- One row per SAP customer/invoice extract that was ingested. Keeps the
-- resolved column mapping as an audit trail, like feedback_imports.
CREATE TABLE sap_imports (
    id                  {{UUID}}     PRIMARY KEY,
    filename            VARCHAR(255) NOT NULL,
    source              VARCHAR(30)  NOT NULL DEFAULT 'SAP_FILE',
    uploaded_by_user_id {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    column_map          {{JSON}},
    total_rows          INTEGER      NOT NULL DEFAULT 0,
    created_count       INTEGER      NOT NULL DEFAULT 0,
    updated_count       INTEGER      NOT NULL DEFAULT 0,
    skipped_count       INTEGER      NOT NULL DEFAULT 0,
    error_count         INTEGER      NOT NULL DEFAULT 0,
    errors              {{JSON}},
    status              VARCHAR(20)  NOT NULL DEFAULT 'SUCCESS',
    created_at          {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_sap_imports_status CHECK (status IN ('SUCCESS', 'PARTIAL', 'FAILED')),
    CONSTRAINT ck_sap_imports_source CHECK (source IN ('SAP_FILE', 'SAP_B1_DB'))
);


-- -------------------------------------------------------- customers
-- A converted customer: someone SAP has invoiced. This is the pool the
-- reference module works against.
CREATE TABLE customers (
    id                  {{UUID}}      PRIMARY KEY,
    sap_code            VARCHAR(30)   NOT NULL UNIQUE,
    name                VARCHAR(200)  NOT NULL,
    mobile              VARCHAR(30),
    email               VARCHAR(255),

    -- Nullable on purpose: an unowned customer is visible to ADMIN and
    -- SUPER_ADMIN only, and is the pool a manager assigns from. (plan v3 s6)
    owner_user_id       {{UUID}}      REFERENCES users(id) ON DELETE SET NULL,
    -- The salesperson name exactly as SAP spelled it, kept even when it
    -- cannot be resolved to a portal account.
    sap_sales_person    VARCHAR(120),

    is_converted        {{BOOL}}      NOT NULL DEFAULT TRUE,
    first_invoice_date  DATE,
    last_invoice_date   DATE,
    invoice_count       INTEGER       NOT NULL DEFAULT 0,

    -- Reference tracking roll-up.
    reference_status        VARCHAR(20) NOT NULL DEFAULT 'NOT_ASKED',
    last_reference_asked_at DATE,
    next_reference_date     DATE,

    import_id           {{UUID}}      REFERENCES sap_imports(id) ON DELETE SET NULL,
    created_at          {{TS}}        NOT NULL DEFAULT {{NOW}},
    updated_at          {{TS}}        NOT NULL DEFAULT {{NOW}},

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
    id               {{UUID}}     PRIMARY KEY,
    customer_id      {{UUID}}     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    invoice_no       VARCHAR(40)  NOT NULL,
    invoice_date     DATE,
    fgpo_code        VARCHAR(40),
    item_description TEXT,
    sales_person     VARCHAR(120),
    owner_user_id    {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    source_row_hash  VARCHAR(64)  NOT NULL UNIQUE,
    import_id        {{UUID}}     REFERENCES sap_imports(id) ON DELETE SET NULL,
    created_at       {{TS}}       NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_invoice_lines_customer ON invoice_lines (customer_id, invoice_date);
CREATE INDEX ix_invoice_lines_invoice_no ON invoice_lines (invoice_no);


-- ---------------------------------------------- customer_references
-- "Did this converted customer give us a reference?" One row per ask.
CREATE TABLE customer_references (
    id                    {{UUID}}     PRIMARY KEY,
    customer_id           {{UUID}}     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    -- Who gets the credit. Scoped by visible_user_ids on every list.
    requested_by_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,

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
    converted_lead_id     {{UUID}},

    created_at            {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at            {{TS}}       NOT NULL DEFAULT {{NOW}},

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
    id                   {{UUID}}     PRIMARY KEY,
    name                 VARCHAR(120) NOT NULL,
    company_name         VARCHAR(200),
    mobile               VARCHAR(30),
    email                VARCHAR(255),
    city                 VARCHAR(80),
    requirement          TEXT,

    origin               VARCHAR(30)  NOT NULL DEFAULT 'ASSIGNED_BY_HEAD',
    origin_reference_id  {{UUID}}     REFERENCES customer_references(id) ON DELETE SET NULL,

    status               VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    priority             VARCHAR(10)  NOT NULL DEFAULT 'MEDIUM',

    assigned_to_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    assigned_by_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    assigned_at          {{TS}},
    next_follow_up_date  DATE,
    closed_at            {{TS}},

    created_at           {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at           {{TS}}       NOT NULL DEFAULT {{NOW}},

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
    id             {{UUID}}    PRIMARY KEY,
    lead_id        {{UUID}}    NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    actor_user_id  {{UUID}}    REFERENCES users(id) ON DELETE SET NULL,
    activity_type  VARCHAR(30) NOT NULL,
    from_status    VARCHAR(20),
    to_status      VARCHAR(20),
    remark         TEXT,
    created_at     {{TS}}      NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_lead_activities_lead ON lead_activities (lead_id, created_at);


-- -------------------------------------------------- feedback_imports
-- One row per uploaded Google Forms export. column_map records the mapping
-- the admin actually confirmed, so a later form edit is diagnosable.
CREATE TABLE feedback_imports (
    id                  {{UUID}}     PRIMARY KEY,
    filename            VARCHAR(255) NOT NULL,
    source              VARCHAR(30)  NOT NULL DEFAULT 'GOOGLE_FORMS_XLSX',
    uploaded_by_user_id {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    column_map          {{JSON}},
    total_rows          INTEGER      NOT NULL DEFAULT 0,
    created_count       INTEGER      NOT NULL DEFAULT 0,
    skipped_count       INTEGER      NOT NULL DEFAULT 0,
    error_count         INTEGER      NOT NULL DEFAULT 0,
    errors              {{JSON}},
    status              VARCHAR(20)  NOT NULL DEFAULT 'SUCCESS',
    created_at          {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_feedback_imports_status CHECK (
        status IN ('SUCCESS', 'PARTIAL', 'FAILED')
    )
);


-- --------------------------------------------------------- feedback
CREATE TABLE feedback (
    id                   {{UUID}}     PRIMARY KEY,
    -- sha256 of the normalised source row. Unique, so re-importing the
    -- same export creates nothing. (plan v3 s8.1)
    source_row_hash      VARCHAR(64)  UNIQUE,
    import_id            {{UUID}}     REFERENCES feedback_imports(id) ON DELETE SET NULL,
    source               VARCHAR(30)  NOT NULL DEFAULT 'GOOGLE_FORMS_IMPORT',

    submitted_at_source  {{TS}},
    customer_name        VARCHAR(200),
    company_name         VARCHAR(200),
    mobile               VARCHAR(30),
    email                VARCHAR(255),

    -- The salesperson the respondent named, and the account it resolved to.
    handled_by_name      VARCHAR(120),
    handled_by_user_id   {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,

    overall_rating       NUMERIC(4, 2),
    overall_rating_raw   VARCHAR(40),
    overall_comments     TEXT,
    would_recommend      VARCHAR(20),

    created_at           {{TS}}       NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_feedback_source CHECK (
        source IN ('INTERNAL', 'PUBLIC_LINK', 'GOOGLE_FORMS_IMPORT')
    )
);

CREATE INDEX ix_feedback_submitted_at ON feedback (submitted_at_source);


-- ------------------------------------- feedback_department_ratings
-- raw_value keeps the source wording ("4 - Satisfied", "8/10") next to the
-- normalised number so a scale change never silently rewrites history.
CREATE TABLE feedback_department_ratings (
    id            {{UUID}}      PRIMARY KEY,
    feedback_id   {{UUID}}      NOT NULL REFERENCES feedback(id)    ON DELETE CASCADE,
    department_id {{UUID}}      NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    rating        NUMERIC(4, 2),
    raw_value     VARCHAR(40),
    comments      TEXT,
    created_at    {{TS}}        NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ux_feedback_department UNIQUE (feedback_id, department_id)
);

CREATE INDEX ix_fdr_department ON feedback_department_ratings (department_id, created_at);


-- -------------------------------------------------- feedback_alerts
CREATE TABLE feedback_alerts (
    id              {{UUID}}      PRIMARY KEY,
    department_id   {{UUID}}      NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    status          VARCHAR(20)   NOT NULL DEFAULT 'OPEN',
    average_rating  NUMERIC(4, 2) NOT NULL,
    response_count  INTEGER       NOT NULL,
    threshold       NUMERIC(4, 2) NOT NULL,
    window_days     INTEGER       NOT NULL,
    opened_at       {{TS}}        NOT NULL DEFAULT {{NOW}},
    resolved_at     {{TS}},
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
    id          {{UUID}}     PRIMARY KEY,
    user_id     {{UUID}}     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        VARCHAR(40)  NOT NULL,
    title       VARCHAR(200) NOT NULL,
    body        TEXT,
    entity_type VARCHAR(30),
    entity_id   {{UUID}},
    -- Lets the lazily-generated follow-up reminders fire once per customer
    -- per day without a scheduler. (plan v3 s10)
    dedupe_key  VARCHAR(160) UNIQUE,
    is_read     {{BOOL}}     NOT NULL DEFAULT FALSE,
    read_at     {{TS}},
    created_at  {{TS}}       NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_notifications_user ON notifications (user_id, is_read, created_at);


-- ----------------------------------------------------- audit_events
-- Every authority-gated mutation writes one row, inside the same
-- transaction as the change itself. (plan v3 s9.5)
CREATE TABLE audit_events (
    id            {{UUID}}    PRIMARY KEY,
    actor_user_id {{UUID}}    REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR(60) NOT NULL,
    entity_type   VARCHAR(30),
    entity_id     {{UUID}},
    before        {{JSON}},
    after         {{JSON}},
    ip_address    VARCHAR(45),
    created_at    {{TS}}      NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_audit_events_actor ON audit_events (actor_user_id, created_at);
CREATE INDEX ix_audit_events_entity ON audit_events (entity_type, entity_id);
