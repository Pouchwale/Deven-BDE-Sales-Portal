-- =====================================================================
-- 0017_usernames.sql
--
-- A short sign-in name beside the email address, so people sign in as
-- "navya" or "superadmin" instead of typing a full address.
--
--   users.username   lowercase, unique, NULL until assigned
--
-- Existing accounts are given one by `python -m app.seeds.assign_usernames`
-- (first name, falling back to longer forms on a clash - see
-- app/core/usernames.py). The email address keeps working as a sign-in too.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

ALTER TABLE users ADD COLUMN username VARCHAR(40);

CREATE UNIQUE INDEX ux_users_username ON users (username);
