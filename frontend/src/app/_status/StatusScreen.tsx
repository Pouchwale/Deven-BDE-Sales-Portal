import { AlertTriangle, Compass } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * The body of the 404 and error pages.
 *
 * Deliberately plain: no data, no auth, no providers. An error page that can
 * itself fail to render is worse than none. Private folder (`_status`), so
 * Next never treats it as a route.
 */
export function StatusScreen({
  kind,
  title,
  description,
  reference,
  actions,
  fullPage = true,
}: {
  kind: "not-found" | "error";
  title: string;
  description: string;
  /** Error digest - safe to show, it only matches a server log line. */
  reference?: string | null;
  actions: React.ReactNode;
  fullPage?: boolean;
}) {
  const Icon = kind === "error" ? AlertTriangle : Compass;

  return (
    <div
      className={cn(
        "flex items-center justify-center px-4",
        fullPage ? "min-h-dvh py-10" : "py-16",
      )}
    >
      <div
        role={kind === "error" ? "alert" : undefined}
        className="animate-fade-up w-full max-w-md rounded-card border border-line bg-surface px-6 py-10 text-center card-shadow sm:px-10"
      >
        <span
          className={cn(
            "animate-pop mx-auto mb-4 inline-flex size-12 items-center justify-center rounded-2xl",
            kind === "error" ? "bg-danger-soft text-danger" : "bg-surface-2 text-subtle",
          )}
        >
          <Icon className="size-5" aria-hidden />
        </span>
        {kind === "not-found" ? (
          <p className="text-[12px] font-semibold uppercase tracking-wider text-subtle">404</p>
        ) : null}
        <h1 className="mt-1 text-lg font-semibold text-content">{title}</h1>
        <p className="mx-auto mt-2 max-w-sm text-[13.5px] leading-relaxed text-muted">
          {description}
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-2.5">{actions}</div>
        {reference ? (
          <p className="mt-6 text-[11.5px] text-subtle">
            Reference: <span className="font-mono">{reference}</span>
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Button look for a plain <a>/<Link>, matching `Button` variants. */
export const linkButtonClass = {
  primary:
    "inline-flex h-9.5 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-700 active:bg-brand-800",
  secondary:
    "inline-flex h-9.5 items-center justify-center gap-2 rounded-lg border border-line bg-surface px-4 text-sm font-medium text-content transition-colors hover:border-line-strong hover:bg-surface-hover",
};
