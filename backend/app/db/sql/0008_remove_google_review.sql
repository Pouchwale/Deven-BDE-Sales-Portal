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
