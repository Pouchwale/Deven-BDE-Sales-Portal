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
