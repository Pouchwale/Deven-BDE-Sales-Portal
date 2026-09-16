"use client";

import { AlertTriangle, Loader2, Lock, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ApiError, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ERROR_HINTS } from "@/lib/roles";

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn("animate-shimmer rounded-md bg-surface-2", className)}
      {...props}
    />
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-subtle", className)} aria-hidden />;
}

/** Placeholder rows that match the real table's shape, so the layout does
 *  not jump when the data lands. */
export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="divide-y divide-line" data-testid="table-skeleton">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex items-center gap-4 px-4 py-3">
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <Skeleton
              key={columnIndex}
              className={cn("h-4", columnIndex === 0 ? "w-1/4" : "flex-1")}
              style={{ animationDelay: `${rowIndex * 60 + columnIndex * 30}ms` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "animate-fade-up flex flex-col items-center justify-center px-6 py-14 text-center",
        className,
      )}
    >
      <span className="animate-pop mb-3.5 inline-flex size-11 items-center justify-center rounded-xl border border-line bg-surface-2 text-subtle">
        <Icon className="size-5" />
      </span>
      <p className="text-sm font-semibold text-content">{title}</p>
      {description ? (
        <p className="mt-1 max-w-sm text-[13px] leading-relaxed text-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const code = error instanceof ApiError ? error.code : null;
  const hint = code ? ERROR_HINTS[code] : null;

  /* A refusal is not a malfunction.
   *
   * Reaching an admin-only page by typing its URL used to render "Could not
   * load this" over a red warning triangle, which reads as a broken portal
   * and invites somebody to retry, reload, and eventually report a bug. The
   * server behaved correctly; only the presentation was wrong. */
  const forbidden = error instanceof ApiError && error.status === 403;
  if (forbidden) {
    return (
      <div
        role="status"
        className={cn(
          "animate-fade-up flex flex-col items-center justify-center px-6 py-12 text-center",
          className,
        )}
      >
        <span className="animate-pop mb-3.5 inline-flex size-11 items-center justify-center rounded-xl bg-surface-2 text-subtle">
          <Lock className="size-5" />
        </span>
        <p className="text-sm font-semibold text-content">Not available to your role</p>
        <p className="mt-1 max-w-md text-[13px] leading-relaxed text-muted">
          {hint ?? errorMessage(error)}
        </p>
      </div>
    );
  }

  return (
    <div
      role="alert"
      className={cn(
        "animate-fade-up flex flex-col items-center justify-center px-6 py-12 text-center",
        className,
      )}
    >
      <span className="animate-pop mb-3.5 inline-flex size-11 items-center justify-center rounded-xl bg-danger-soft text-danger">
        <AlertTriangle className="size-5" />
      </span>
      <p className="text-sm font-semibold text-content">Could not load this</p>
      <p className="mt-1 max-w-md text-[13px] leading-relaxed text-muted">
        {hint ?? errorMessage(error)}
      </p>
      {onRetry ? (
        <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry}>
          <RefreshCw className="size-3.5" aria-hidden />
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/** An inline form-level error, e.g. above a dialog's buttons. */
export function InlineError({ children }: { children: React.ReactNode }) {
  return (
    <p
      role="alert"
      className="animate-fade-up flex items-start gap-2 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-[13px] leading-snug text-danger"
    >
      <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}
