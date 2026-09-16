"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Feedback";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useCountUp } from "@/lib/useCountUp";

const ACCENTS = {
  brand: "bg-brand-600/10 text-brand-700 dark:bg-brand-400/12 dark:text-brand-300",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  neutral: "bg-surface-2 text-subtle",
} as const;

export interface StatTileProps {
  label: string;
  value: number | string;
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  accent?: keyof typeof ACCENTS;
  index?: number;
  /** Where this number lives in full. A KPI that names a page should take you
   *  there — that is the whole point of putting it on a dashboard. */
  href?: string;
}

/**
 * A single headline number.
 *
 * A KPI row of these is the right form for a handful of current values — a
 * grouped bar chart of unrelated measures would be worse in every way.
 */
export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  accent = "brand",
  index = 0,
  href,
}: StatTileProps) {
  // Only a real number counts. "4.2/5" and "34%" are read, not tallied.
  const numeric = typeof value === "number";
  const counted = useCountUp(numeric ? value : 0, numeric);

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[12.5px] font-medium text-muted">{label}</p>
        <span
          className={cn(
            "inline-flex size-7 shrink-0 items-center justify-center rounded-lg sm:size-8",
            ACCENTS[accent],
          )}
          aria-hidden
        >
          <Icon className="size-4" />
        </span>
      </div>
      {/* tabular-nums keeps the width steady while the digits run. The number
          steps down on a phone: at 26px a five-tile row costs a whole screen
          and you scroll past the work to reach it. */}
      <p className="mt-1.5 text-[22px] font-semibold leading-none tracking-tight tabular-nums text-content sm:mt-2 sm:text-[26px]">
        {numeric ? formatNumber(counted) : value}
      </p>
      {hint ? (
        <p className="mt-1 text-[11.5px] leading-snug text-subtle sm:mt-1.5 sm:text-[12px]">
          {hint}
        </p>
      ) : null}

      {href ? (
        <ArrowUpRight
          className="absolute right-3 top-3 size-3.5 text-subtle opacity-0 transition-opacity duration-150 group-hover:opacity-100"
          aria-hidden
        />
      ) : null}
    </>
  );

  const card = (
    <Card
      interactive={Boolean(href)}
      className={cn("stagger relative p-3.5 sm:p-4", href && "group h-full")}
      style={{ "--index": index } as React.CSSProperties}
      data-testid="stat"
      data-label={label}
      data-value={String(value)}
    >
      {body}
    </Card>
  );

  if (!href) return card;

  return (
    <Link href={href} className="block focus-visible:rounded-card" aria-label={`${label}: ${value}`}>
      {card}
    </Link>
  );
}

export function StatTileSkeleton({ index = 0 }: { index?: number }) {
  return (
    <Card className="stagger p-4" style={{ "--index": index } as React.CSSProperties}>
      <div className="flex items-start justify-between gap-3">
        <Skeleton className="h-3.5 w-24" />
        <Skeleton className="size-8 rounded-lg" />
      </div>
      <Skeleton className="mt-3 h-6 w-16" />
      <Skeleton className="mt-2 h-3 w-28" />
    </Card>
  );
}

/** A labelled group of tiles, the way the dashboard is actually read. */
export function StatSection({
  title,
  href,
  children,
}: {
  title: string;
  href?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-5">
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold text-muted">{title}</h3>
        {href ? (
          <Link
            href={href}
            className="text-[12.5px] font-medium text-brand-700 hover:underline dark:text-brand-300"
          >
            Open module
          </Link>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:gap-3.5 lg:grid-cols-3">{children}</div>
    </section>
  );
}
