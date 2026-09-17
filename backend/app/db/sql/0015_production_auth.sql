-- =====================================================================
-- 0015_production_auth.sql
--
-- Production authentication.
--
-- 1. users.plain_password is DROPPED. Migration 0013 stored every password
--    in plaintext so an administrator could read it back. Only the bcrypt
--    hash in users.hashed_password is kept from here on; an administrator
--    SETS a password and can never see one.
--
-- 2. Server-managed sessions (user_sessions). A signed token alone cannot be
--    revoked, so sign-out, a password reset and a deactivation could not end
--    a session that had already been issued. Every token now names a row
--    here, and a revoked or expired row ends the session immediately.
--    Only a SHA-256 of the session secret is stored, never the secret.
--
-- 3. Sign-in bookkeeping on users: last successful sign-in, consecutive
--    failures and a temporary lock, so brute force is bounded per account
--    and not only per IP address.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================

ALTER TABLE users DROP COLUMN plain_password;

ALTER TABLE users ADD COLUMN last_login_at {{TS}};
ALTER TABLE users ADD COLUMN last_login_ip VARCHAR(45);
ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until {{TS}};

CREATE TABLE user_sessions (
    id              {{UUID}}     PRIMARY KEY,
    user_id         {{UUID}}     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      VARCHAR(64)  NOT NULL UNIQUE,
    created_at      {{TS}}       NOT NULL,
    last_seen_at    {{TS}}       NOT NULL,
    expires_at      {{TS}}       NOT NULL,
    revoked_at      {{TS}},
    revoked_reason  VARCHAR(40),
    ip_address      VARCHAR(45),
    user_agent      VARCHAR(255)
);

CREATE INDEX ix_user_sessions_user_active ON user_sessions (user_id, revoked_at);
CREATE INDEX ix_user_sessions_expires_at ON user_sessions (expires_at);
