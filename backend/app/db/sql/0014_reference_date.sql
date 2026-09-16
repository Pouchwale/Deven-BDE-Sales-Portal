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
