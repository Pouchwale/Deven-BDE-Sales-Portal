/**
 * Lead presentation and the client's copy of the state machine.
 *
 * The server is the authority — it refuses an illegal move with
 * INVALID_TRANSITION and tells you what was allowed. This table exists only so
 * the UI offers the right buttons; it must stay in step with
 * `ALLOWED_LEAD_TRANSITIONS` in backend/app/core/constants.py.
 */
import type { LeadStatus } from "@/types/api";

export const LEAD_STATUS_LABELS: Record<LeadStatus, string> = {
  NEW: "New",
  CONTACTED: "Contacted",
  NOT_CONTACTED: "Not contacted",
  NURTURING: "Nurturing",
  PRE_QUALIFIED: "Pre-qualified",
  QUALIFIED: "Qualified",
  CONVERTED: "Converted",
  JUNK: "Junk",
  LOST: "Lost",
};

export const LEAD_STATUS_TONE: Record<LeadStatus, string> = {
  NEW: "border-line bg-surface-2 text-muted",
  NOT_CONTACTED: "border-line bg-surface-2 text-subtle",
  CONTACTED: "border-transparent bg-info-soft text-info",
  NURTURING: "border-transparent bg-warning-soft text-warning",
  PRE_QUALIFIED:
    "border-transparent bg-brand-100 text-brand-800 dark:bg-brand-950 dark:text-brand-200",
  QUALIFIED:
    "border-transparent bg-brand-100 text-brand-800 dark:bg-brand-950 dark:text-brand-200",
  CONVERTED: "border-transparent bg-success-soft text-success",
  JUNK: "border-line bg-surface-2 text-subtle",
  LOST: "border-transparent bg-danger-soft text-danger",
};

/**
 * Mirrors ALLOWED_LEAD_TRANSITIONS in backend/app/core/constants.py.
 *
 *   NEW ─┬─► CONTACTED ──► NURTURING ──► PRE_QUALIFIED ──► QUALIFIED ──► CONVERTED
 *        ├─► NOT_CONTACTED ─┘
 *        ├─► JUNK                     (terminal)
 *        └─► LOST                     (terminal)
 *
 * No stage can be skipped, and CONVERTED / LOST / JUNK are final. Only an
 * administrator can reopen a finished lead, and that is a separate action.
 */
export const NEXT_STATUSES: Record<LeadStatus, LeadStatus[]> = {
  NEW: ["CONTACTED", "NOT_CONTACTED", "JUNK", "LOST"],
  CONTACTED: ["NURTURING", "JUNK", "LOST"],
  NOT_CONTACTED: ["CONTACTED", "JUNK", "LOST"],
  NURTURING: ["PRE_QUALIFIED", "LOST"],
  PRE_QUALIFIED: ["QUALIFIED", "LOST"],
  QUALIFIED: ["CONVERTED", "LOST"],
  CONVERTED: [],
  JUNK: [],
  LOST: [],
};

/** The three stages nothing follows. */
export const CLOSED_LEAD_STATUSES: LeadStatus[] = ["CONVERTED", "LOST", "JUNK"];

export const LEAD_PRIORITY_TONE: Record<string, string> = {
  HIGH: "border-transparent bg-danger-soft text-danger",
  MEDIUM: "border-line bg-surface-2 text-muted",
  LOW: "border-line bg-surface-2 text-subtle",
};

export const OPEN_LEAD_STATUSES: LeadStatus[] = [
  "NEW",
  "CONTACTED",
  "NOT_CONTACTED",
  "NURTURING",
  "PRE_QUALIFIED",
  "QUALIFIED",
];

// Mirrors STALE_AFTER_DAYS in backend/app/services/leads.py — the same line
// the "no recent activity" dashboard KPI uses.
export const STALE_AFTER_DAYS = 7;

/** An open lead with nothing logged in the last week — the same rule the
 *  dashboard's aggregate count uses, applied per row. */
export function isStale(status: LeadStatus, lastActivityAt: string | null): boolean {
  if (!OPEN_LEAD_STATUSES.includes(status)) return false;
  if (!lastActivityAt) return true;
  const days = (Date.now() - new Date(lastActivityAt).getTime()) / 86_400_000;
  return days >= STALE_AFTER_DAYS;
}


/** The stages in the order the pipeline actually runs. */
export const PIPELINE_ORDER: LeadStatus[] = [
  "NEW",
  "NOT_CONTACTED",
  "CONTACTED",
  "NURTURING",
  "PRE_QUALIFIED",
  "QUALIFIED",
  "CONVERTED",
  "LOST",
  "JUNK",
];

/** Stage -> the field carrying its count in LeadStats. */
export const STAT_KEY: Record<
  LeadStatus,
  "new" | "contacted" | "not_contacted" | "nurturing" | "pre_qualified" | "qualified" | "converted" | "junk" | "lost"
> = {
  NEW: "new",
  CONTACTED: "contacted",
  NOT_CONTACTED: "not_contacted",
  NURTURING: "nurturing",
  PRE_QUALIFIED: "pre_qualified",
  QUALIFIED: "qualified",
  CONVERTED: "converted",
  JUNK: "junk",
  LOST: "lost",
};
