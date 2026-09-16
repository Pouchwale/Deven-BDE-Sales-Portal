/**
 * What the assistant knows about where you are and what is on your plate.
 *
 * Two small helpers, both derived from things the app already has. Neither
 * makes a request: the route comes from the router, and the counts come from
 * the dashboard payload the shell fetched anyway. An assistant that fires its
 * own queries just to write a friendly sentence is a slow assistant.
 */
import type { Dashboard, Suggestion } from "@/types/api";

/** Route prefix → the module it belongs to. Matches the sidebar's own map. */
const ROUTE_TOPIC: [prefix: string, topic: string][] = [
  ["/leads", "leads"],
  ["/references", "references"],
  ["/feedback", "feedback"],
  ["/customers", "customers"],
  ["/team", "team"],
  ["/notifications", "general"],
  ["/dashboard", "general"],
];

export function topicForRoute(pathname: string): string | null {
  return ROUTE_TOPIC.find(([prefix]) => pathname.startsWith(prefix))?.[1] ?? null;
}

/**
 * The server's list, led by whatever is about the page you are on.
 *
 * Reordering only. The list arrives already filtered to what this role can be
 * answered about, so nothing here can surface a prompt the person is not
 * entitled to — and the tool dispatcher re-checks regardless.
 */
export function orderSuggestions(
  suggestions: Suggestion[],
  pathname: string,
  limit = 4,
): Suggestion[] {
  const topic = topicForRoute(pathname);
  if (!topic) return suggestions.slice(0, limit);

  const onTopic = suggestions.filter((s) => s.topic === topic);
  const rest = suggestions.filter((s) => s.topic !== topic);
  return [...onTopic, ...rest].slice(0, limit);
}

/**
 * One sentence about the person's current workload, or nothing.
 *
 * Nothing is a real answer here. A line that says "you have 0 open leads and
 * 0 follow-ups" is worse than silence, and inventing something to fill the
 * space would be worse still.
 */
export function workloadLine(data: Dashboard | null): string | null {
  if (!data) return null;

  const parts: string[] = [];
  const { leads, feedback_pending } = data;

  if (leads.open > 0) {
    parts.push(`${leads.open} open ${leads.open === 1 ? "lead" : "leads"}`);
  }
  if (leads.follow_ups_due > 0) {
    parts.push(
      `${leads.follow_ups_due} ${leads.follow_ups_due === 1 ? "follow-up" : "follow-ups"} due`,
    );
  } else if (leads.open > 0) {
    parts.push("nothing due today");
  }
  if (parts.length === 0 && feedback_pending > 0) {
    parts.push(
      `${feedback_pending} ${feedback_pending === 1 ? "customer" : "customers"} still owed feedback`,
    );
  }

  if (parts.length === 0) return null;
  if (parts.length === 1) return `You have ${parts[0]}.`;
  return `You have ${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}.`;
}

/** The input placeholder, nudged by where the person is. */
export function composerPlaceholder(pathname: string): string {
  switch (topicForRoute(pathname)) {
    case "leads":
      return "Ask about your leads…";
    case "references":
      return "Ask about references and follow-ups…";
    case "feedback":
      return "Ask about feedback and ratings…";
    case "customers":
      return "Ask about your customers…";
    case "team":
      return "Ask about your team…";
    default:
      return "Ask about leads, customers or feedback…";
  }
}
