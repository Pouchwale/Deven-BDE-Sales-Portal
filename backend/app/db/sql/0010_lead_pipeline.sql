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

{{#sqlite}}
CREATE TABLE leads_rebuilt (
    id                   {{UUID}}     PRIMARY KEY,
    name                 VARCHAR(120) NOT NULL,
    company_name         VARCHAR(200),
    mobile               VARCHAR(30),
    email                VARCHAR(255),
    city                 VARCHAR(80),
    requirement          TEXT,

    origin               VARCHAR(30)  NOT NULL DEFAULT 'ASSIGNED_BY_HEAD',
    origin_reference_id  {{UUID}}     REFERENCES customer_references(id) ON DELETE SET NULL,

    status               VARCHAR(20)  NOT NULL DEFAULT 'NEW',
    priority             VARCHAR(10)  NOT NULL DEFAULT 'MEDIUM',

    assigned_to_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    assigned_by_user_id  {{UUID}}     REFERENCES users(id) ON DELETE SET NULL,
    assigned_at          {{TS}},
    next_follow_up_date  DATE,
    closed_at            {{TS}},

    created_at           {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at           {{TS}}       NOT NULL DEFAULT {{NOW}},

    dispatched_at            {{TS}},
    reference_status         VARCHAR(20) NOT NULL DEFAULT 'NOT_ASKED',
    last_reference_asked_at  DATE,
    next_reference_date      DATE,

    CONSTRAINT ck_leads_origin CHECK (
        origin IN ('ASSIGNED_BY_HEAD', 'REFERENCE_FOLLOWUP')
    ),
    CONSTRAINT ck_leads_status CHECK (
        status IN (
            'NEW', 'CONTACTED', 'NOT_CONTACTED', 'NURTURING',
            'PRE_QUALIFIED', 'QUALIFIED', 'CONVERTED', 'JUNK', 'LOST'
        )
    ),
    CONSTRAINT ck_leads_priority CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT ck_leads_reference_status CHECK (
        reference_status IN ('NOT_ASKED', 'TAKEN', 'PENDING', 'DECLINED')
    ),
    CONSTRAINT ck_leads_reference_origin CHECK (
        origin <> 'REFERENCE_FOLLOWUP' OR origin_reference_id IS NOT NULL
    )
);

INSERT INTO leads_rebuilt (
    id, name, company_name, mobile, email, city, requirement,
    origin, origin_reference_id, status, priority,
    assigned_to_user_id, assigned_by_user_id, assigned_at,
    next_follow_up_date, closed_at, created_at, updated_at,
    dispatched_at, reference_status, last_reference_asked_at, next_reference_date
)
SELECT
    id, name, company_name, mobile, email, city, requirement,
    origin, origin_reference_id,
    CASE status
        WHEN 'PENDING'   THEN 'NEW'
        WHEN 'FOLLOW_UP' THEN 'NURTURING'
        ELSE status
    END,
    priority,
    assigned_to_user_id, assigned_by_user_id, assigned_at,
    next_follow_up_date, closed_at, created_at, updated_at,
    dispatched_at, reference_status, last_reference_asked_at, next_reference_date
FROM leads;

DROP TABLE leads;

ALTER TABLE leads_rebuilt RENAME TO leads;

-- Dropped with the old table, so they come back here.
CREATE INDEX ix_leads_origin_assignee ON leads (origin, assigned_to_user_id, status);
CREATE INDEX ix_leads_assignee ON leads (assigned_to_user_id, status);
-- New: "Assigned by me" filters on this, and it had no index of its own.
CREATE INDEX ix_leads_assigner ON leads (assigned_by_user_id, assigned_to_user_id);
{{/sqlite}}

{{#postgresql}}
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
{{/postgresql}}
