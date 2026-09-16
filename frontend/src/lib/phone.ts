/**
 * The phone rule, mirrored from the backend.
 *
 * `backend/app/core/validators.py` is the authority — it is what actually
 * refuses bad data, and it refuses it whether or not a browser was involved.
 * This copy exists so somebody typing nine digits is told before they press
 * save, not after a round trip. If the two ever disagree, the backend wins and
 * this file is the bug.
 */

const NATIONAL_NUMBER_LENGTH = 10;
const COUNTRY_CODE = "91";

export const PHONE_ERROR = "Enter a valid 10-digit mobile number.";

/** Ten digits, or null. Blank is not an error — most of these fields are optional. */
export function normalisePhone(value: string): string | null {
  const text = value.trim();
  if (!text) return null;

  let digits = text.replace(/\D+/g, "");
  if (!digits) return null;

  // A trunk zero and a country code can both be present: `091-9711122505`.
  if (digits.length > NATIONAL_NUMBER_LENGTH && digits.startsWith("0")) {
    digits = digits.slice(1);
  }
  if (
    digits.length === COUNTRY_CODE.length + NATIONAL_NUMBER_LENGTH &&
    digits.startsWith(COUNTRY_CODE)
  ) {
    digits = digits.slice(COUNTRY_CODE.length);
  }

  return digits.length === NATIONAL_NUMBER_LENGTH ? digits : null;
}

/**
 * The message to show under the field, or null when there is nothing to say.
 *
 * Blank returns null: an empty optional field is not a mistake, and marking it
 * red while somebody is still deciding whether to fill it in is noise.
 */
export function phoneError(value: string): string | null {
  if (!value.trim()) return null;
  return normalisePhone(value) === null ? PHONE_ERROR : null;
}
