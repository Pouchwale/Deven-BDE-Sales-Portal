"use client";

import { ArrowRight, ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { createContext, useContext } from "react";

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Feedback";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useCountUp } from "@/lib/useCountUp";

/**
 * Colour is reserved for meaning. "brand" and "neutral" both render quiet -
 * a dashboard where every tile has its own coloured chip says nothing with
 * colour at all. Success, warning and danger tint the icon (and, in a panel
 * row, the number) because they are telling you something.
 */
const ICON_TONE = {
  brand: "text-subtle",
  neutral: "text-subtle",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
} as const;

const VALUE_TONE = {
  brand: "text-content",
  neutral: "text-content",
  success: "text-content",
  warning: "text-warning",
  danger: "text-danger",
} as const;

export interface StatTileProps {
  label: string;
  value: number | string;
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  accent?: keyof typeof ICON_TONE;
  index?: number;
  /** Where this number lives in full. A KPI that names a page should take you
   *  there — that is the whole point of putting it on a dashboard. */
  href?: string;
}

/** Set by StatSection: tiles inside a module panel render as rows. */
const InPanel = createContext(false);

/**
 * A single headline number.
 *
 * On its own (the KPI row at the top of a module page) it is a light card.
 * Inside a StatSection (the dashboard) it is one row of that module's panel,
 * so a module reads as one block rather than three competing cards.
 */
export function StatTile(props: StatTileProps) {
  const inPanel = useContext(InPanel);
  return inPanel ? <StatRow {...props} /> : <StatCard {...props} />;
}

function useDisplayValue(value: number | string): string {
  // Only a real number counts. "4.2/5" and "34%" are read, not tallied.
  const numeric = typeof value === "number";
  const counted = useCountUp(numeric ? value : 0, numeric);
  return numeric ? formatNumber(counted) : value;
}

function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  accent = "brand",
  index = 0,
  href,
}: StatTileProps) {
  const shown = useDisplayValue(value);

  const card = (
    <Card
      interactive={Boolean(href)}
      className={cn("stagger relative h-full p-3.5 sm:p-4", href && "group")}
      style={{ "--index": index } as React.CSSProperties}
      data-testid="stat"
      data-label={label}
      data-value={String(value)}
    >
      <div className="flex items-start gap-2">
        <Icon className={cn("mt-px size-4 shrink-0", ICON_TONE[accent])} aria-hidden />
        {/* Wraps rather than truncates: "References compl…" on a phone
            hides the one word that says what the number is. */}
        <p className="line-clamp-2 min-w-0 text-[12.5px] font-medium leading-snug text-muted">
          {label}
        </p>
      </div>
      {/* tabular-nums keeps the width steady while the digits run. */}
      <p className="mt-2.5 text-[22px] font-semibold leading-none tracking-tight tabular-nums text-content sm:text-[24px]">
        {shown}
      </p>
      {hint ? (
        <p className="mt-1.5 text-[11.5px] leading-snug text-subtle sm:text-[12px]">{hint}</p>
      ) : null}

      {href ? (
        <ArrowUpRight
          className="absolute right-3 top-3 size-3.5 text-subtle opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
          aria-hidden
        />
      ) : null}
    </Card>
  );

  if (!href) return card;

  return (
    <Link href={href} className="block h-full focus-visible:rounded-card" aria-label={`${label}: ${value}`}>
      {card}
    </Link>
  );
}

function StatRow({ label, value, hint, icon: Icon, accent = "brand", href }: StatTileProps) {
  const shown = useDisplayValue(value);

  const body = (
    <>
      <Icon className={cn("size-4 shrink-0", ICON_TONE[accent])} aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-medium text-content">{label}</span>
        {hint ? (
          <span className="mt-0.5 block text-[12px] leading-snug text-subtle">{hint}</span>
        ) : null}
      </span>
      <span
        className={cn(
          "shrink-0 text-[20px] font-semibold leading-none tracking-tight tabular-nums",
          VALUE_TONE[accent],
        )}
      >
        {shown}
      </span>
    </>
  );

  const shell = "flex items-center gap-3 px-4 py-3 sm:px-5";

  return (
    <li data-testid="stat" data-label={label} data-value={String(value)}>
      {href ? (
        <Link
          href={href}
          aria-label={`${label}: ${value}`}
          className={cn(shell, "transition-colors hover:bg-surface-hover focus-visible:rounded-none")}
        >
          {body}
        </Link>
      ) : (
        <div className={shell}>{body}</div>
      )}
    </li>
  );
}

export function StatTileSkeleton({ index = 0 }: { index?: number }) {
  return (
    <Card className="stagger p-4" style={{ "--index": index } as React.CSSProperties}>
      <div className="flex items-center gap-2">
        <Skeleton className="size-4 rounded" />
        <Skeleton className="h-3.5 w-24" />
      </div>
      <Skeleton className="mt-3 h-6 w-16" />
      <Skeleton className="mt-2 h-3 w-28" />
    </Card>
  );
}

/**
 * One module of the dashboard: a single panel whose rows are its numbers.
 * `data-enter` hands it to the dashboard's one GSAP entrance.
 */
export function StatSection({
  title,
  href,
  icon: Icon,
  children,
}: {
  title: string;
  href?: string;
  icon?: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <section data-enter className="flex flex-col">
      <Card className="flex h-full flex-col overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
          <h2 className="flex items-center gap-2 text-[14px] font-semibold text-content">
            {Icon ? <Icon className="size-4 text-brand-600 dark:text-brand-300" aria-hidden /> : null}
            {title}
          </h2>
          {href ? (
            <Link
              href={href}
              className="group -my-1 -mr-2 inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-[12.5px] font-medium text-brand-700 transition-colors hover:bg-brand-600/8 dark:text-brand-300"
            >
              Open
              <span className="sr-only"> {title}</span>
              <ArrowRight
                className="size-3.5 transition-transform duration-150 group-hover:translate-x-0.5"
                aria-hidden
              />
            </Link>
          ) : null}
        </div>
        <ul className="divide-y divide-line">
          <InPanel.Provider value>{children}</InPanel.Provider>
        </ul>
      </Card>
    </section>
  );
}

/** Placeholder for a whole module panel while the dashboard loads. */
export function StatSectionSkeleton() {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-line px-5 py-3.5">
        <Skeleton className="h-4 w-36" />
      </div>
      {Array.from({ length: 3 }).map((_, index) => (
        <div key={index} className="flex items-center gap-3 border-b border-line px-5 py-3.5 last:border-0">
          <Skeleton className="size-4 rounded" />
          <div className="flex-1">
            <Skeleton className="h-3.5 w-28" />
            <Skeleton className="mt-1.5 h-3 w-40" />
          </div>
          <Skeleton className="h-5 w-10" />
        </div>
      ))}
    </Card>
  );
}
