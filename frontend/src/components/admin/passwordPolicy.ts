/**
 * The password policy, as the browser shows it.
 *
 * Mirrors backend app/core/passwords.py: any non-empty password is accepted.
 * The only limit is bcrypt's 72 bytes, which almost nobody reaches.
 */
export const PASSWORD_MIN_LENGTH = 1;

/** No rules to explain, so no hint is shown. */
export const PASSWORD_POLICY_HINT = "";

/** A short reason the password will be refused, or null if it looks fine. */
export function passwordProblem(password: string): string | null {
  if (!password) return null;
  if (new TextEncoder().encode(password).length > 72) return "At most 72 characters.";
  return null;
}
