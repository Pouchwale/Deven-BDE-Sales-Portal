"use client";

import { ArrowRight } from "lucide-react";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { cn } from "@/lib/cn";
import { greeting } from "@/lib/greeting";
import type { Suggestion } from "@/types/api";

/**
 * What the assistant opens with.
 *
 * Three layers, in order of how much they earn their space: who is here and
 * when, what is actually on their plate, and what they might ask.
 *
 * The suggestions arrive from the server already filtered to what this role
 * can be answered about — a BDE is never invited to ask about a department
 * analysis they would only be refused. The client reorders them by the page
 * you are on and nothing more.
 */
export function ChatEmptyState({
  name,
  suggestions,
  workload,
  onPick,
}: {
  name: string;
  suggestions: Suggestion[];
  /** One line about the person's current load, or null when there is nothing
   *  worth saying. Reused from the dashboard payload — no extra request. */
  workload: string | null;
  onPick: (prompt: string) => void;
}) {
  return (
    <div className="flex flex-col px-4 pb-6 pt-5">
      <div className="px-1">
        <div
          className={cn(
            "animate-pop grid size-9 place-items-center rounded-xl",
            "bg-brand-50 text-brand-600 ring-1 ring-brand-100",
            "dark:bg-brand-950 dark:text-brand-300 dark:ring-brand-900",
          )}
        >
          <AssistantMark className="size-5" />
        </div>

        <h3 className="mt-3 text-[16px] font-semibold leading-snug text-content">
          {greeting(name)}.
        </h3>

        {workload ? (
          <p className="mt-1 text-[13px] leading-relaxed text-muted">{workload}</p>
        ) : null}

        <p className="mt-1 text-[13px] leading-relaxed text-muted">
          What would you like to look into?
        </p>
      </div>

      {suggestions.length ? (
        <div className="mt-5 space-y-1.5">
          <p className="px-1 text-[11px] font-medium uppercase tracking-wide text-subtle">
            Based on where you are
          </p>
          {suggestions.map((suggestion, index) => (
            <button
              key={suggestion.text}
              type="button"
              onClick={() => onPick(suggestion.text)}
              style={{ animationDelay: `${60 + index * 45}ms` }}
              className={cn(
                "stagger group flex w-full items-center justify-between gap-2 rounded-xl",
                "border border-line bg-surface px-3 py-2.5 text-left text-[13px] text-muted",
                "transition-[background-color,border-color,color,transform] duration-150",
                "hover:-translate-y-px hover:border-brand-300 hover:bg-surface-hover hover:text-content",
                "focus-visible:border-brand-400 dark:hover:border-brand-900",
              )}
            >
              <span className="min-w-0">{suggestion.text}</span>
              <ArrowRight
                className={cn(
                  "size-3.5 shrink-0 -translate-x-1 text-subtle opacity-0",
                  "transition-[opacity,transform] duration-150",
                  "group-hover:translate-x-0 group-hover:opacity-100",
                )}
                aria-hidden
              />
            </button>
          ))}
        </div>
      ) : null}

      <p className="mt-5 px-1 text-[11.5px] leading-relaxed text-subtle">
        I only see what you see.
      </p>
    </div>
  );
}
