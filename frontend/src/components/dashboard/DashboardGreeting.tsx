"use client";

import { Check } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Feedback";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { ALL_CLEAR, focusLines, greeting, quoteOfTheDay } from "@/lib/greeting";
import type { Dashboard, Honorific } from "@/types/api";

/** What this person is looking at. Not decoration - two people reading the
 *  same screen are reading different books, and the tiles do not say so. */
const SCOPE: Record<Dashboard["shape"], string> = {
  ADMIN: "Everything across the organisation",
  MANAGER: "Your reporting chain, at any depth",
  PERSONAL: "Your own work",
};

/**
 * The top of the command centre: who you are, what day it is, and what is
 * actually asking for you.
 *
 * The three layers are deliberate — a greeting alone is decoration, and a
 * list of numbers alone is a report. Together they read as the product
 * knowing something about your morning.
 *
 * Everything below the quote comes from the dashboard payload that was
 * already fetched. No extra request, and nothing that is not a real field.
 */
export function DashboardGreeting({
  name,
  honorific,
  data,
  loading,
}: {
  name: string | undefined;
  honorific: Honorific | null;
  data: Dashboard | null;
  loading: boolean;
}) {
  // Computed once per mount. A greeting that flips at 12:00:01 while somebody
  // is reading it is a distraction, not a feature.
  const hello = useMemo(
    () => greeting(name, new Date(), honorific),
    [name, honorific],
  );
  const quote = useMemo(() => quoteOfTheDay(name), [name]);
  const focus = useMemo(() => focusLines(data), [data]);
  const today = useMemo(
    () =>
      new Date().toLocaleDateString(undefined, {
        weekday: "long",
        day: "numeric",
        month: "long",
      }),
    [],
  );

  return (
    <header
      className={cn(
        "hero-band relative mb-5 overflow-hidden rounded-card border border-line",
        "bg-surface px-5 py-5 card-shadow sm:px-6 sm:py-6",
      )}
    >
      <div className="relative flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="text-[11.5px] font-medium uppercase tracking-[0.08em] text-brand-600 dark:text-brand-300">
            {today}
          </p>
          <h1 className="mt-1 text-[24px] font-semibold leading-tight tracking-[-0.015em] text-content sm:text-[28px]">
            {hello}
          </h1>
          <p className="mt-1.5 max-w-xl border-l-2 border-brand-200 pl-3 text-[13.5px] italic leading-relaxed text-muted dark:border-brand-900">
            {quote}
          </p>
        </div>

        {data ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Badge className="border-line bg-surface-2 text-subtle">
              {SCOPE[data.shape]}
            </Badge>
            <Badge className="border-line bg-surface-2 text-subtle">
              Updated {formatDateTime(data.generated_at)}
            </Badge>
          </div>
        ) : null}
      </div>

      {/* ------------------------------------------------ today's focus */}
      <div className="relative mt-5 border-t border-line pt-4">
        {loading && !data ? (
          <div className="flex flex-wrap gap-2">
            <Skeleton className="h-7 w-56 rounded-full" />
            <Skeleton className="h-7 w-44 rounded-full" />
          </div>
        ) : focus.length === 0 ? (
          <p className="flex items-center gap-2 text-[13px] text-success">
            <Check className="size-4" aria-hidden />
            {ALL_CLEAR}
          </p>
        ) : (
          <>
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.08em] text-subtle">
              Your focus today
            </p>
            <ul className="flex flex-wrap gap-2">
              {focus.map((line, index) => (
                <li
                  key={line.text}
                  className="stagger"
                  style={{ "--index": index } as React.CSSProperties}
                >
                  <FocusChip {...line} />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </header>
  );
}

function FocusChip({
  text,
  href,
  tone,
}: {
  text: string;
  href?: string;
  tone: "attention" | "neutral";
}) {
  const shell = cn(
    "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[12.5px]",
    "transition-[background-color,border-color,transform] duration-150",
    tone === "attention"
      ? "border-warning/30 bg-warning-soft text-warning"
      : "border-line bg-surface-2 text-muted",
    href && "hover:-translate-y-px",
    href && tone === "attention"
      ? "hover:border-warning/55"
      : href && "hover:border-line-strong hover:text-content",
  );

  const body = (
    <>
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          tone === "attention" ? "bg-warning" : "bg-subtle",
        )}
        aria-hidden
      />
      {text}
    </>
  );

  if (!href) return <span className={shell}>{body}</span>;
  return (
    <Link href={href} className={shell}>
      {body}
    </Link>
  );
}
