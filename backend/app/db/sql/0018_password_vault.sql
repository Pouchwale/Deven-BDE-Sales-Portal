-- =====================================================================
-- 0018_password_vault.sql
--
-- users.password_encrypted - a Fernet-encrypted copy of the password so the
-- Super Admin can reveal it (business request). NOT plaintext: the key is
-- PASSWORD_VIEW_KEY in the environment and never touches the database.
-- Sign-in still verifies only users.hashed_password.
--
-- NULL until a password is set (or backfilled with
-- `python -m app.seeds.backfill_password_vault`).
-- =====================================================================

ALTER TABLE users ADD COLUMN password_encrypted TEXT;
