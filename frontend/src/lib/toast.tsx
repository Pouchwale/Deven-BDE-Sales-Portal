"use client";

import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { cn } from "@/lib/cn";

type ToastTone = "success" | "error" | "info";

interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
  /** Set while the toast plays its exit, just before it is dropped. */
  leaving?: boolean;
}

interface ToastContextValue {
  toast: (tone: ToastTone, title: string, description?: string) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DURATION = { success: 3500, info: 4000, error: 6000 } as const;

const TONE_STYLES: Record<ToastTone, { icon: typeof Info; accent: string; iconClass: string }> = {
  success: { icon: CheckCircle2, accent: "bg-success", iconClass: "text-success" },
  error: { icon: AlertCircle, accent: "bg-danger", iconClass: "text-danger" },
  info: { icon: Info, accent: "bg-info", iconClass: "text-info" },
};

let nextId = 0;

/** Matches --animate-slide-out-right. Kept in step by hand: a toast that
 *  unmounts before its exit finishes just disappears, which is the bug the
 *  animation exists to avoid. */
const EXIT_MS = 200;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  /** Play the exit, then drop the row. */
  const dismiss = useCallback((id: number) => {
    setToasts((current) =>
      current.map((item) => (item.id === id ? { ...item, leaving: true } : item)),
    );
    window.setTimeout(
      () => setToasts((current) => current.filter((item) => item.id !== id)),
      EXIT_MS,
    );
  }, []);

  const toast = useCallback(
    (tone: ToastTone, title: string, description?: string) => {
      const id = (nextId += 1);
      setToasts((current) => [...current, { id, tone, title, description }]);
      // Errors linger: they usually need reading, not glancing at.
      window.setTimeout(() => dismiss(id), DURATION[tone]);
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (title, description) => toast("success", title, description),
      error: (title, description) => toast("error", title, description),
      info: (title, description) => toast("info", title, description),
    }),
    [toast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2.5"
        role="region"
        aria-label="Notifications"
      >
        {toasts.map((item) => {
          const { icon: Icon, accent, iconClass } = TONE_STYLES[item.tone];
          return (
            <div
              key={item.id}
              role="status"
              data-testid="toast"
              className={cn(
                "pointer-events-auto relative flex items-start gap-3 overflow-hidden rounded-xl",
                "border border-line bg-surface p-3.5 pl-4 card-shadow-lg",
                item.leaving ? "animate-slide-out-right" : "animate-slide-in-right",
              )}
            >
              <span className={cn("absolute inset-y-0 left-0 w-1", accent)} aria-hidden />
              <Icon className={cn("mt-0.5 size-4.5 shrink-0", iconClass)} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-content">{item.title}</p>
                {item.description ? (
                  <p className="mt-0.5 text-[13px] leading-snug text-muted">{item.description}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                aria-label="Dismiss"
                className="-m-1 rounded-md p-1 text-subtle transition-colors hover:bg-surface-hover hover:text-content"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
