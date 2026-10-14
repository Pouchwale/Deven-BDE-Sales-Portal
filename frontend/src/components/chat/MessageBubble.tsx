"use client";

import { AlertTriangle, LogIn, RotateCcw, Settings2, WifiOff } from "lucide-react";
import { useRef } from "react";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { MessageContent } from "@/components/chat/MessageContent";
import { ResultCards } from "@/components/chat/ResultCards";
import { ToolTrail } from "@/components/chat/ToolTrail";
import { cn } from "@/lib/cn";
import type { Turn } from "@/lib/chat";
import type { ChatFailure } from "@/lib/chatErrors";
import { gsap, prefersReducedMotion, useGSAP } from "@/lib/motion";
import type { Role } from "@/types/api";

export function MessageBubble({
  turn,
  role,
  onNavigate,
  onRetry,
}: {
  turn: Turn;
  /** The signed-in person's role — decides which deep links are offered. */
  role: Role;
  onNavigate?: () => void;
  onRetry?: (replyId: string) => void;
}) {
  const mine = turn.role === "USER";
  const ref = useRef<HTMLDivElement>(null);

  // A small rise for a message that has just been written in this session.
  // Turns reopened from history arrive with server ids and appear at once -
  // replaying a whole conversation's entrance would only be in the way.
  useGSAP(
    () => {
      if (!turn.id.startsWith("local-") || prefersReducedMotion() || !ref.current) return;
      gsap.from(ref.current, {
        opacity: 0,
        y: 6,
        duration: 0.28,
        clearProps: "opacity,transform",
      });
    },
    { scope: ref },
  );

  if (mine) {
    return (
      <div ref={ref} className="flex justify-end">
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
    <div ref={ref} className="flex gap-2.5">
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
          <FailureNotice
            failure={turn.error}
            onRetry={onRetry ? () => onRetry(turn.id) : undefined}
          />
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

const FAILURE_ICON = {
  offline: WifiOff,
  config: Settings2,
  auth: LogIn,
} as const;

/** One failure, said specifically, with the one action that can fix it. */
function FailureNotice({ failure, onRetry }: { failure: ChatFailure; onRetry?: () => void }) {
  const Icon = FAILURE_ICON[failure.kind as keyof typeof FAILURE_ICON] ?? AlertTriangle;
  // Waiting it out is the fix for these; the rest are faults.
  const soft = failure.kind === "rate_limit" || failure.kind === "provider";

  return (
    <div
      role="alert"
      className={cn(
        "rounded-xl border px-3 py-2.5 text-[13px] leading-relaxed",
        soft ? "border-warning/30 bg-warning-soft" : "border-danger/25 bg-danger-soft",
      )}
    >
      <p
        className={cn(
          "flex items-center gap-2 font-semibold",
          soft ? "text-warning" : "text-danger",
        )}
      >
        <Icon className="size-3.5 shrink-0" aria-hidden />
        {failure.title}
      </p>
      <p className="mt-0.5 text-muted">{failure.message}</p>

      {failure.kind === "auth" ? (
        <a
          href="/login"
          className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 text-[12.5px] font-medium text-content transition-colors hover:bg-surface-hover"
        >
          <LogIn className="size-3.5" aria-hidden />
          Sign in again
        </a>
      ) : failure.retryable && onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 text-[12.5px] font-medium text-content transition-colors hover:bg-surface-hover"
        >
          <RotateCcw className="size-3.5" aria-hidden />
          Try again
        </button>
      ) : null}
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
