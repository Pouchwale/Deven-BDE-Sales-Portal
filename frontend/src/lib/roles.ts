/**
 * Role presentation.
 *
 * The list of roles the UI may offer always comes from the server
 * (`GET /api/users/assignable-roles`) — never from this file. These are
 * labels and colours only.
 */
import type { Honorific, ReferenceStatus, Role } from "@/types/api";

export const ROLE_LABELS: Record<Role, string> = {
  SUPER_ADMIN: "Super Admin",
  ADMIN: "Admin",
  MANAGER: "Manager",
  BDE: "BDE",
  SALES: "Sales",
};

/** Badge classes per role. Seniority reads as warmth. */
export const ROLE_TONE: Record<Role, string> = {
  SUPER_ADMIN: "bg-brand-600 text-white border-transparent",
  ADMIN: "bg-brand-100 text-brand-800 border-brand-200 dark:bg-brand-950 dark:text-brand-200 dark:border-brand-900",
  MANAGER: "bg-info-soft text-info border-transparent",
  BDE: "bg-surface-2 text-muted border-line",
  SALES: "bg-surface-2 text-muted border-line",
};

export function roleLabel(role: Role | string): string {
  return ROLE_LABELS[role as Role] ?? role;
}

export function isAdmin(role: Role | undefined | null): boolean {
  return role === "ADMIN" || role === "SUPER_ADMIN";
}

/** Strictly the top role. Permanently deleting an account is theirs alone;
 *  a plain Admin may deactivate, which is reversible. */
export function isSuperAdmin(role: Role | undefined | null): boolean {
  return role === "SUPER_ADMIN";
}

export function isLeadership(role: Role | undefined | null): boolean {
  return isAdmin(role) || role === "MANAGER";
}

/**
 * Browsing the SAP customer book is Super Admin only.
 *
 * Note this is NOT `isAdmin` - a plain Admin is excluded too, deliberately.
 * Hiding the nav item is a courtesy; `app/api/customers.py` is what actually
 * refuses the request.
 */
export function canBrowseCustomers(role: Role | undefined | null): boolean {
  return role === "SUPER_ADMIN";
}

export const REFERENCE_STATUS_LABELS: Record<ReferenceStatus, string> = {
  NOT_ASKED: "Not asked",
  TAKEN: "Taken",
  PENDING: "Pending",
  DECLINED: "Declined",
};

/**
 * Whether the reference conversation is finished for this account.
 *
 * TAKEN they gave one; DECLINED they were asked and had none. Both are done
 * and neither should reappear in the ask queue. NOT_ASKED and PENDING are
 * still work.
 *
 * The backend excludes completed accounts from the pending queries itself -
 * this is for deciding what the row looks like, not for deciding what it is.
 */
export function isCompletedReference(status: ReferenceStatus): boolean {
  return status === "TAKEN" || status === "DECLINED";
}

export const REFERENCE_STATUS_TONE: Record<ReferenceStatus, string> = {
  NOT_ASKED: "bg-surface-2 text-subtle border-line",
  TAKEN: "bg-success-soft text-success border-transparent",
  PENDING: "bg-warning-soft text-warning border-transparent",
  DECLINED: "bg-danger-soft text-danger border-transparent",
};

/** Turns a backend error code into something worth showing a person. */
export const ERROR_HINTS: Record<string, string> = {
  FORBIDDEN: "This section is limited to administrators and managers.",
  ROLE_ABOVE_ACTOR: "You cannot grant a role at or above your own.",
  CYCLIC_REPORTING_LINE: "That reporting line would create a loop.",
  HAS_DIRECT_REPORTS: "Give their reports a new manager first.",
  MANAGER_RANK_INVALID: "A manager must outrank the person reporting to them.",
  DEPARTMENT_ALREADY_HEADED: "That department already has a head.",
  PASSWORD_CHANGE_REQUIRED: "Set a new password before continuing.",
  RATE_LIMITED: "Too many attempts. Wait a moment and try again.",
  NETWORK_ERROR: "The server is not responding.",
};


/* ------------------------------------------------------------ honorific */
/**
 * How the company addresses somebody, when it addresses them that way.
 *
 * Admin-set on the person's record. Nothing here guesses: inferring "sir" or
 * "ma'am" from a first name is guessing a colleague's gender from a string,
 * and deriving it from rank would address every manager identically
 * regardless of what they actually go by. Unset is the normal case.
 */
export const HONORIFIC_LABELS: Record<Honorific, string> = {
  SIR: "sir",
  MAAM: "ma'am",
};

/** "Shail" -> "Shail sir". Falls back to the bare name when unset. */
export function withHonorific(
  name: string | undefined | null,
  honorific: Honorific | null | undefined,
): string {
  const base = (name ?? "").trim();
  if (!base || !honorific) return base;
  return `${base} ${HONORIFIC_LABELS[honorific]}`;
}

/** Just the first name, addressed properly: "Shail sir". */
export function firstNameWithHonorific(
  name: string | undefined | null,
  honorific: Honorific | null | undefined,
): string {
  const first = (name ?? "").trim().split(/\s+/)[0] ?? "";
  return withHonorific(first, honorific);
}
