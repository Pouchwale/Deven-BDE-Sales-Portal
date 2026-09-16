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
    id             {{UUID}}     PRIMARY KEY,

    lead_id        {{UUID}}     REFERENCES leads(id) ON DELETE CASCADE,
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

    synced_at      {{TS}},
    created_at     {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at     {{TS}}       NOT NULL DEFAULT {{NOW}},

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
