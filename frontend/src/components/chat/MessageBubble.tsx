"use client";

import { AlertTriangle } from "lucide-react";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { MessageContent } from "@/components/chat/MessageContent";
import { ResultCards } from "@/components/chat/ResultCards";
import { ToolTrail } from "@/components/chat/ToolTrail";
import { cn } from "@/lib/cn";
import type { Turn } from "@/lib/chat";
import type { Role } from "@/types/api";

export function MessageBubble({
  turn,
  role,
  onNavigate,
}: {
  turn: Turn;
  /** The signed-in person's role — decides which deep links are offered. */
  role: Role;
  onNavigate?: () => void;
}) {
  const mine = turn.role === "USER";

  if (mine) {
    return (
      <div className="animate-fade-up flex justify-end">
        <div
          className={cn(
            "max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-3.5 py-2",
            "text-[13.5px] leading-relaxed text-white shadow-sm",
          )}
        >
          <p className="whitespace-pre-wrap">{turn.content}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-up flex gap-2.5">
      <div
        className={cn(
          "mt-0.5 grid size-7 shrink-0 place-items-center rounded-full",
          "bg-brand-50 text-brand-600 ring-1 ring-brand-100",
          "dark:bg-brand-950 dark:text-brand-300 dark:ring-brand-900",
        )}
      >
        <AssistantMark className="size-4" />
      </div>

      <div className="min-w-0 flex-1">
        {turn.error ? (
          <div
            className={cn(
              "flex items-start gap-2 rounded-xl border border-danger/25 bg-danger-soft",
              "px-3 py-2 text-[13px] leading-relaxed text-danger",
            )}
          >
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            <span>{turn.error}</span>
          </div>
        ) : (
          <div className="space-y-2 text-[13.5px] leading-relaxed text-content">
            {turn.content ? <MessageContent text={turn.content} /> : null}

            {turn.pending && turn.activity ? (
              <p className="flex items-center gap-2 text-[12.5px] text-subtle">
                <Dots />
                {turn.activity}
              </p>
            ) : null}

            {turn.pending && !turn.content && !turn.activity ? <Dots /> : null}

            {turn.cards?.length ? (
              <ResultCards cards={turn.cards} role={role} onNavigate={onNavigate} />
            ) : null}
          </div>
        )}

        {!turn.pending && turn.used?.length ? (
          <ToolTrail used={turn.used} role={role} onNavigate={onNavigate} />
        ) : null}
      </div>
    </div>
  );
}

/**
 * Three short bars, rising and falling out of phase.
 *
 * Reads as "working" rather than "loading" — and it animates `transform`
 * only, so it stays on the compositor and costs nothing while a stream is
 * in flight. `motion-safe` drops the movement entirely for anybody who has
 * asked for less of it; the bars stay, so the state is still visible.
 */
function Dots() {
  return (
    <span
      className="inline-flex h-3 items-end gap-[3px]"
      role="status"
      aria-label="The assistant is thinking"
    >
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="motion-safe:animate-typing w-[3px] origin-bottom rounded-full bg-brand-400"
          style={{ height: "100%", animationDelay: `${index * 130}ms` }}
        />
      ))}
    </span>
  );
}
