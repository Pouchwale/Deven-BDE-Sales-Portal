import { Check, X } from "lucide-react";

import { cn } from "@/lib/cn";

/**
 * One environment variable, and whether the server has it.
 *
 * Used by the two setup cards that report configuration they cannot edit -
 * the assistant (GROQ_API_KEY, CHAT_ENABLED) and the Google Form sync
 * (GOOGLE_SYNC_SECRET, GOOGLE_SYNC_URL). Both had their own copy of this row.
 *
 * It never shows a value, only whether one is set: a secret an admin screen
 * can read back is a secret you have lost.
 */
export function ConfigRow({
  label,
  ok,
  variable,
}: {
  label: string;
  ok: boolean;
  variable: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <dt className="text-muted">{label}</dt>
      <dd className="flex items-center gap-1.5">
        <span className="font-mono text-[11.5px] text-subtle">{variable}</span>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-medium",
            ok ? "bg-success-soft text-success" : "bg-warning-soft text-warning",
          )}
        >
          {ok ? <Check className="size-3" /> : <X className="size-3" />}
          {ok ? "set" : "missing"}
        </span>
      </dd>
    </div>
  );
}
