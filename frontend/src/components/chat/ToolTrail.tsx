"use client";

import { ArrowUpRight, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { cn } from "@/lib/cn";
import { isLeadership } from "@/lib/roles";
import type { Role } from "@/types/api";

/**
 * What the assistant looked at, and where to go and see it yourself.
 *
 * Note what this is NOT: a table of rows. Tool results are never sent to the
 * browser — only the assistant's prose is. So the trail shows the lookups
 * that ran and offers a jump into the module, where the real page applies
 * the real permissions and shows the real data.
 *
 * The `visible` predicates mirror `Sidebar.tsx` exactly. Offering a link to a
 * page that would answer 403 is worse than offering none.
 */
interface Destination {
  href: string;
  label: string;
  visible: (role: Role) => boolean;
}

const DESTINATIONS: Record<string, Destination> = {
  get_lead_stats: { href: "/leads", label: "Assigned Leads", visible: () => true },
  list_leads: { href: "/leads", label: "Assigned Leads", visible: () => true },
  get_lead: { href: "/leads", label: "Assigned Leads", visible: () => true },
  list_overdue_leads: {
    href: "/leads?tab=all&open_only=true",
    label: "Open leads",
    visible: () => true,
  },
  get_reference_stats: {
    href: "/references",
    label: "Reference Tracking",
    visible: () => true,
  },
  list_reference_accounts: {
    href: "/references?tab=customers",
    label: "Won accounts",
    visible: () => true,
  },
  list_references: {
    href: "/references?tab=referred",
    label: "Referred people",
    visible: () => true,
  },
  list_reference_followups: {
    href: "/references?tab=follow-ups",
    label: "Follow-ups due",
    visible: () => true,
  },
  list_pending_feedback_requests: {
    href: "/feedback?tab=pending",
    label: "Feedback pending",
    visible: () => true,
  },
  get_feedback_analysis: {
    href: "/feedback?tab=analysis",
    label: "Feedback analysis",
    visible: isLeadership,
  },
  list_customers: { href: "/customers", label: "Customers", visible: () => true },
  get_customer: { href: "/customers", label: "Customers", visible: () => true },
  get_customer_timeline: { href: "/customers", label: "Customers", visible: () => true },
  get_team_workload: { href: "/team", label: "Team", visible: isLeadership },
  get_notifications: { href: "/notifications", label: "Notifications", visible: () => true },
  get_my_work_summary: { href: "/dashboard", label: "Dashboard", visible: () => true },
};

export function ToolTrail({
  used,
  role,
  onNavigate,
}: {
  /** The lookups that ran, in order. */
  used: { name: string; label: string }[];
  role: Role;
  onNavigate?: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (!used.length) return null;

  const seen = new Set<string>();
  const links: Destination[] = [];
  for (const { name } of used) {
    const destination = DESTINATIONS[name];
    if (!destination || !destination.visible(role)) continue;
    if (seen.has(destination.href)) continue;
    seen.add(destination.href);
    links.push(destination);
  }

  return (
    <div className="mt-2 space-y-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11.5px]",
          "text-subtle transition-colors hover:bg-surface-hover hover:text-muted",
        )}
      >
        <Search className="size-3" aria-hidden />
        {used.length === 1 ? "1 lookup" : `${used.length} lookups`}
      </button>

      {open ? (
        <ul className="animate-fade-in space-y-0.5 pl-1 text-[11.5px] text-subtle">
          {used.map((lookup, index) => (
            <li key={index}>· {lookup.label.replace(/…$/, "")}</li>
          ))}
        </ul>
      ) : null}

      {links.length ? (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              onClick={onNavigate}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border border-line bg-surface-2",
                "px-2 py-0.5 text-[11.5px] font-medium text-muted transition-colors",
                "hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700",
                "dark:hover:border-brand-900 dark:hover:bg-brand-950 dark:hover:text-brand-200",
              )}
            >
              {link.label}
              <ArrowUpRight className="size-3" aria-hidden />
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}
