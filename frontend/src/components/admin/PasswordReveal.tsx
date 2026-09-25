"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";

import { Spinner } from "@/components/ui/Feedback";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";

/**
 * A password shown as dots until the eye is pressed. Super Admin only - the
 * API refuses everyone else.
 *
 * Fetched on each press rather than shipped with the user list, so the
 * password is never in a listing response, and every reveal is audited on
 * the server. Hiding it again drops it from memory.
 *
 * Sign-in keeps only a one-way hash, so an account whose readable copy was
 * never made (or was made under another key) cannot be shown - it is filled
 * in at that person's next sign-in, or at once by setting a password here.
 */
export function PasswordReveal({
  userId,
  onSetPassword,
  className,
}: {
  userId: string;
  /** Offered in place of the password when no readable copy exists yet. */
  onSetPassword?: () => void;
  className?: string;
}) {
  const [password, setPassword] = useState<string | null>(null);
  const [state, setState] = useState<"hidden" | "loading" | "shown" | "unavailable">("hidden");
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (state === "shown" || state === "unavailable") {
      setPassword(null);
      setState("hidden");
      return;
    }
    setError(null);
    setState("loading");
    try {
      const result = await api.users.revealPassword(userId);
      if (result.password === null) {
        setState("unavailable");
      } else {
        setPassword(result.password);
        setState("shown");
      }
    } catch (cause) {
      setError(errorMessage(cause));
      setState("hidden");
    }
  }

  const open = state === "shown" || state === "unavailable";

  if (state === "unavailable" && onSetPassword) {
    return (
      <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
        <button
          type="button"
          onClick={onSetPassword}
          title="No readable copy yet - it appears after their next sign-in, or set one now"
          className="text-[12.5px] font-medium text-brand-700 hover:underline dark:text-brand-300"
        >
          Set password
        </button>
        <button
          type="button"
          onClick={toggle}
          aria-label="Hide"
          title="Hide"
          className="grid size-6 shrink-0 place-items-center rounded-md text-subtle transition-colors hover:bg-surface-hover hover:text-content"
        >
          <EyeOff className="size-3.5" aria-hidden />
        </button>
      </span>
    );
  }

  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)}>
      <span
        className={cn(
          "min-w-0 truncate font-mono text-[12.5px]",
          state === "shown" ? "select-all text-content" : "text-subtle",
        )}
        title={error ?? undefined}
      >
        {state === "shown"
          ? password
          : state === "unavailable"
            ? "Not available"
            : error
              ? "Could not load"
              : "••••••••"}
      </span>
      <button
        type="button"
        onClick={toggle}
        disabled={state === "loading"}
        aria-label={open ? "Hide password" : "Show password"}
        title={open ? "Hide password" : "Show password"}
        className="grid size-6 shrink-0 place-items-center rounded-md text-subtle transition-colors hover:bg-surface-hover hover:text-content disabled:opacity-60"
      >
        {state === "loading" ? (
          <Spinner className="size-3.5" />
        ) : open ? (
          <EyeOff className="size-3.5" aria-hidden />
        ) : (
          <Eye className="size-3.5" aria-hidden />
        )}
      </button>
    </span>
  );
}
