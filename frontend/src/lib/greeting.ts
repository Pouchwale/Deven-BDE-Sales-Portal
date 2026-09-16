/**
 * The human layer on top of the numbers.
 *
 * Three small things, all derived — nothing here invents data. The summary in
 * particular reads only fields the dashboard already fetched, so it costs no
 * extra request and cannot drift from the tiles underneath it.
 */
import { firstNameWithHonorific } from "@/lib/roles";
import type { Dashboard, Honorific } from "@/types/api";

/* ------------------------------------------------------------- the hour */
export type PartOfDay = "morning" | "afternoon" | "evening";

/** Local to the reader, not the server. Somebody in Ahmedabad at 9pm should
 *  not be told good morning because a container in another timezone thinks so. */
export function partOfDay(now: Date = new Date()): PartOfDay {
  const hour = now.getHours();
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  return "evening";
}

const SALUTATION: Record<PartOfDay, string> = {
  morning: "Good morning",
  afternoon: "Good afternoon",
  evening: "Good evening",
};

export function greeting(
  name: string | undefined,
  now: Date = new Date(),
  honorific: Honorific | null = null,
): string {
  const salutation = SALUTATION[partOfDay(now)];
  const first = firstNameWithHonorific(name, honorific);
  return first ? `${salutation}, ${first}` : salutation;
}

/* ------------------------------------------------------------- the quote */
/**
 * Short, professional, and about doing the work rather than about greatness.
 * Anything that would embarrass somebody demoing this to their management has
 * no place here.
 */
const QUOTES: readonly string[] = [
  "Small progress, repeated daily, becomes remarkable results.",
  "Focus on the next important thing.",
  "Consistency turns effort into results.",
  "The follow-up is where most of the value is.",
  "Clear the small things early; the big ones need the room.",
  "A short call today beats a long one next week.",
  "Ask the question you have been putting off.",
  "Finish what is nearly done before starting what is not.",
  "The best time to log it is while you remember it.",
  "Steady beats busy.",
  "Close the loop. Every time.",
  "Attention is the scarce resource, not time.",
  "Do the useful thing, not the urgent-looking one.",
  "A customer who feels heard says yes more often.",
];

/**
 * One quote per person per day, chosen deterministically.
 *
 * Deterministic on purpose: a quote that changes on every render is noise,
 * and one that changes on every reload makes the page feel unstable. Keying
 * on the person as well as the date means two people at the next desk do not
 * see the same line all day.
 */
export function quoteOfTheDay(seed: string | undefined, now: Date = new Date()): string {
  const day = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
  const key = `${day}|${seed ?? ""}`;

  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  return QUOTES[Math.abs(hash) % QUOTES.length]!;
}

/* ----------------------------------------------------------- the summary */
export interface FocusLine {
  text: string;
  href?: string;
  /** Drives the dot colour — attention, or merely worth knowing. */
  tone: "attention" | "neutral";
}

const plural = (count: number, one: string, many: string) =>
  `${count} ${count === 1 ? one : many}`;

/**
 * What actually needs this person today, in their own words.
 *
 * Ordered by how much it is costing to ignore: something overdue first,
 * something waiting second, something merely open last. Capped at three —
 * a list of eight things is a list of nothing.
 *
 * Every line is backed by a field the dashboard returned. There are no
 * percentages, no trends and no comparisons, because the API returns no
 * previous period to compare against and a made-up "+12% this week" would be
 * a lie on the most-looked-at screen in the product.
 */
export function focusLines(data: Dashboard | null): FocusLine[] {
  if (!data) return [];

  const lines: FocusLine[] = [];
  // `alerts` is deliberately not read here: the dashboard already gives a
  // department alert its own banner, directly below this, with the rating and
  // the threshold on it. Saying it twice in two styles is noise.
  const { leads, references, feedback_pending } = data;

  if (leads.follow_ups_due > 0) {
    lines.push({
      text: `${plural(leads.follow_ups_due, "follow-up", "follow-ups")} due today or earlier.`,
      href: "/leads",
      tone: "attention",
    });
  }

  if (references.follow_ups_due > 0) {
    lines.push({
      text: `${plural(references.follow_ups_due, "reference follow-up", "reference follow-ups")} to come back to.`,
      href: "/references?tab=follow-ups",
      tone: "attention",
    });
  }

  if (leads.no_recent_activity > 0) {
    lines.push({
      text: `${plural(leads.no_recent_activity, "lead has", "leads have")} had nothing logged in a week.`,
      href: "/leads",
      tone: "attention",
    });
  }

  if (feedback_pending > 0) {
    lines.push({
      text: `${plural(feedback_pending, "customer is", "customers are")} still owed a feedback request.`,
      href: "/feedback?tab=pending",
      tone: "neutral",
    });
  }

  if (references.not_asked > 0) {
    lines.push({
      text: `${plural(references.not_asked, "won account has", "won accounts have")} never been asked for a reference.`,
      href: "/references",
      tone: "neutral",
    });
  }

  if (lines.length === 0 && leads.open > 0) {
    lines.push({
      text: `${plural(leads.open, "open lead", "open leads")}, and nothing overdue.`,
      href: "/leads",
      tone: "neutral",
    });
  }

  return lines.slice(0, 3);
}

/** What to say when the list is empty — which is a real state, not a gap. */
export const ALL_CLEAR = "You're all caught up. Nice work.";
