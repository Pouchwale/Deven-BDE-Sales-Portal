-- Add plain_password column to users table for Super Admin viewing convenience
ALTER TABLE users ADD COLUMN plain_password VARCHAR(255);
UPDATE users SET plain_password = 'ChangeMe@123' WHERE plain_password IS NULL;
