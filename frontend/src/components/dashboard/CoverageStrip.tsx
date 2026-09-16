"use client";

import { useMemo } from "react";

import { cn } from "@/lib/cn";
import type { Dashboard } from "@/types/api";

/**
 * Where the book stands, as three proportions rather than three numbers.
 *
 * A KPI tile answers "how many". This answers "out of how many", which is the
 * question a number on its own always leaves open — 5 references is excellent
 * against 8 accounts and poor against 80.
 *
 * Every bar is a ratio the API already returns or that divides two fields it
 * returns. There is no target, no benchmark and no comparison to last month,
 * because none of those exist in the data.
 */
interface Bar {
  label: string;
  done: number;
  total: number;
  caption: string;
  tone: "brand" | "success" | "warning";
}

const TRACK: Record<Bar["tone"], string> = {
  brand: "bg-brand-600",
  success: "bg-success",
  warning: "bg-warning",
};

export function CoverageStrip({ data }: { data: Dashboard }) {
  const bars = useMemo<Bar[]>(() => {
    // The denominator is ELIGIBLE accounts, not every won lead: an account
    // nobody is allowed to ask yet is not an account somebody failed to ask,
    // and counting it here would make the bar report a failure that has not
    // happened. The four reference states partition exactly that set, so
    // their sum is the same number `eligible_accounts` reports.
    const references = data.references;
    const accounts = references.eligible_accounts;
    const asked = accounts - references.not_asked;
    const answered = data.feedback?.total_responses ?? 0;

    const out: Bar[] = [
      {
        label: "References asked",
        done: asked,
        total: accounts,
        caption: `${references.references_taken} said yes`,
        tone: "brand",
      },
      {
        label: "Leads converted",
        done: data.leads.converted,
        total: data.leads.total,
        caption: `${data.leads.open} still open`,
        tone: "success",
      },
    ];

    // Only when the caller may read feedback at all — otherwise the bar would
    // be a zero they are not entitled to interpret.
    if (data.feedback) {
      out.push({
        label: "Feedback received",
        done: answered,
        total: answered + data.feedback_pending,
        caption: `${data.feedback_pending} still owed`,
        tone: "warning",
      });
    }

    return out.filter((bar) => bar.total > 0);
  }, [data]);

  if (bars.length === 0) return null;

  return (
    <section
      className={cn(
        "stagger mb-4 grid grid-cols-1 gap-x-8 gap-y-4 rounded-card border border-line",
        "bg-surface px-5 py-4 card-shadow sm:grid-cols-2 lg:grid-cols-3",
      )}
      style={{ "--index": 5 } as React.CSSProperties}
      aria-label="Coverage"
    >
      {bars.map((bar) => (
        <Meter key={bar.label} {...bar} />
      ))}
    </section>
  );
}

function Meter({ label, done, total, caption, tone }: Bar) {
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-[12.5px] font-medium text-muted">{label}</p>
        <p className="text-[12.5px] tabular-nums text-subtle">
          <span className="text-[15px] font-semibold text-content">{done}</span>
          <span className="mx-0.5">/</span>
          {total}
        </p>
      </div>

      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-hover"
        role="img"
        aria-label={`${label}: ${done} of ${total}, ${percent} percent`}
      >
        {/* Width transitions on mount, so the bar fills rather than appears.
            One property, GPU-cheap, and it settles in a quarter second. */}
        <div
          className={cn("h-full rounded-full transition-[width] duration-500 ease-out", TRACK[tone])}
          style={{ width: `${percent}%` }}
        />
      </div>

      <p className="mt-1.5 text-[11.5px] text-subtle">
        {percent}% · {caption}
      </p>
    </div>
  );
}
