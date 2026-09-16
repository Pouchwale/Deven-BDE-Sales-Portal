"use client";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

export interface TabDefinition {
  key: string;
  label: string;
  count?: number;
}

/**
 * A tab strip that reads as one control.
 *
 * The active tab lives in the URL (`?tab=`) at every call site, so a link to a
 * particular view — from a dashboard KPI, say — lands where it says it will.
 *
 * On a narrow screen the strip scrolls sideways rather than wrapping or
 * shrinking: five tabs stacked over two rows reads as two controls, and tabs
 * squeezed below their label are unreadable. The scroll is contained to the
 * strip — the page itself never moves.
 */
export function Tabs({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: TabDefinition[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "flex gap-1 rounded-xl border border-line bg-surface-2 p-1",
        // The scroll container has to be BOUNDED or it is not a scroll
        // container: call sites pass `w-fit`, which sizes to content and let
        // a five-tab strip push the whole page 200px wide on a phone.
        // `max-w-full` after `className` wins, so no call site can undo it.
        "overflow-x-auto scrollbar-none",
        className,
        "max-w-full",
      )}
    >
      {tabs.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={selected}
            data-testid={`tab-${tab.key}`}
            onClick={() => onChange(tab.key)}
            className={cn(
              "relative flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium",
              "transition-colors duration-150 active:scale-[0.98]",
              selected ? "text-content" : "text-muted hover:text-content",
            )}
          >
            {/* The pill is its own element, keyed on the active tab, so it
                re-plays its entrance wherever the selection lands. */}
            {selected ? (
              <span
                key={active}
                aria-hidden
                className="animate-scale-in absolute inset-0 rounded-lg bg-surface card-shadow"
              />
            ) : null}
            <span className="relative">{tab.label}</span>
            {tab.count !== undefined ? (
              <span
                className={cn(
                  "relative rounded-full px-1.5 text-[11px] tabular-nums transition-colors duration-150",
                  selected ? "bg-brand-600/12 text-brand-700 dark:text-brand-300" : "bg-line text-subtle",
                )}
              >
                {formatNumber(tab.count)}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
