"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

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
 *
 * A hidden scroll is a hidden option, so whenever tabs are out of view the
 * strip says so: the edge they are behind fades out, a round arrow button
 * sits on that edge (tap it to bring the next tabs in), and the selected tab
 * is always scrolled into view.
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
  const strip = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = strip.current;
    if (!el) return;
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setMore((was) => (was.left === left && was.right === right ? was : { left, right }));
  }, []);

  useEffect(() => {
    const el = strip.current;
    if (!el) return;
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    el.addEventListener("scroll", measure, { passive: true });
    return () => {
      watch.disconnect();
      el.removeEventListener("scroll", measure);
    };
  }, [measure, tabs.length]);

  // Keep the selected tab in view - after a tap on a half-hidden tab, and on
  // arrival from a link that selects a tab beyond the edge.
  useEffect(() => {
    const el = strip.current;
    const tab = el?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!el || !tab) return;
    const pad = 36; // clear of the arrow button
    if (tab.offsetLeft < el.scrollLeft + pad) {
      el.scrollTo({ left: Math.max(0, tab.offsetLeft - pad), behavior: "smooth" });
    } else if (tab.offsetLeft + tab.offsetWidth > el.scrollLeft + el.clientWidth - pad) {
      el.scrollTo({ left: tab.offsetLeft + tab.offsetWidth - el.clientWidth + pad, behavior: "smooth" });
    }
  }, [active]);

  function nudge(direction: -1 | 1) {
    const el = strip.current;
    if (el) el.scrollBy({ left: direction * Math.max(140, el.clientWidth * 0.7), behavior: "smooth" });
  }

  return (
    <div
      className={cn(
        "relative flex rounded-full border-2 border-ink bg-surface",
        className,
        // The scroll container has to be BOUNDED or it is not a scroll
        // container: call sites pass `w-fit`, which sizes to content and let
        // a five-tab strip push the whole page 200px wide on a phone.
        // `max-w-full` after `className` wins, so no call site can undo it.
        "max-w-full",
      )}
    >
      <div
        ref={strip}
        role="tablist"
        className="flex min-w-0 gap-1 overflow-x-auto p-1 scrollbar-none"
        style={
          {
            // Fade only the edge that has more behind it.
            "--fade-l": more.left ? "44px" : "0px",
            "--fade-r": more.right ? "44px" : "0px",
            maskImage:
              "linear-gradient(to right, transparent 0, #000 var(--fade-l), #000 calc(100% - var(--fade-r)), transparent 100%)",
            WebkitMaskImage:
              "linear-gradient(to right, transparent 0, #000 var(--fade-l), #000 calc(100% - var(--fade-r)), transparent 100%)",
          } as React.CSSProperties
        }
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
                "relative flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-bold",
                "transition-colors duration-150 active:scale-[0.98]",
                selected ? "text-pw-ink" : "text-muted hover:text-content",
              )}
            >
              {/* The pill is its own element, keyed on the active tab, so it
                  re-plays its entrance wherever the selection lands. */}
              {selected ? (
                <span
                  key={active}
                  aria-hidden
                  className="animate-scale-in absolute inset-0 rounded-full border-2 border-ink bg-pw-yellow"
                />
              ) : null}
              <span className="relative whitespace-nowrap">{tab.label}</span>
              {tab.count !== undefined ? (
                <span
                  className={cn(
                    "relative rounded-full px-1.5 text-[11px] tabular-nums transition-colors duration-150",
                    selected ? "bg-pw-ink text-pw-yellow" : "bg-surface-2 text-subtle",
                  )}
                >
                  {formatNumber(tab.count)}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {/* Mouse and touch helpers only: the tabs themselves stay reachable by
          keyboard, so these are left out of the tab order. */}
      {more.left ? (
        <button
          type="button"
          tabIndex={-1}
          aria-label="Show earlier tabs"
          onClick={() => nudge(-1)}
          className="absolute left-1 top-1/2 z-10 grid size-7 -translate-y-1/2 place-items-center rounded-full border-2 border-ink bg-surface text-content shadow-[2px_2px_0_0_var(--stamp)] transition-colors hover:bg-pw-yellow hover:text-pw-ink"
        >
          <ChevronLeft className="size-4" aria-hidden />
        </button>
      ) : null}
      {more.right ? (
        <button
          type="button"
          tabIndex={-1}
          aria-label="Show more tabs"
          onClick={() => nudge(1)}
          className="absolute right-1 top-1/2 z-10 grid size-7 -translate-y-1/2 place-items-center rounded-full border-2 border-ink bg-surface text-content shadow-[2px_2px_0_0_var(--stamp)] transition-colors hover:bg-pw-yellow hover:text-pw-ink"
        >
          <ChevronRight className="size-4" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
