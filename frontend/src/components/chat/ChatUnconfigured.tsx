"use client";

import { KeyRound } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ChatSetup } from "@/types/api";

/**
 * What the drawer shows when the assistant is switched on but has no key.
 *
 * Two audiences, one panel. An administrator can fix this, so they get the
 * variable name and the file; everybody else cannot, so they get a plain
 * sentence and somebody to ask. `setup` is null for a non-admin — the server
 * decides that, not this component — and it never carries the key itself.
 */
export function ChatUnconfigured({ setup }: { setup: ChatSetup | null }) {
  const isAdmin = setup !== null;

  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-8 text-center">
      <div
        className={cn(
          "animate-pop grid size-12 place-items-center rounded-2xl",
          "bg-warning-soft text-warning ring-1 ring-warning/20",
        )}
      >
        <KeyRound className="size-5" aria-hidden />
      </div>

      <h3 className="mt-3 text-[15px] font-semibold text-content">Not set up yet</h3>

      {isAdmin ? (
        <>
          <p className="mt-1 max-w-[17rem] text-[13px] leading-relaxed text-muted">
            The assistant is switched on, but there is no {setup.provider} API key
            for it to call.
          </p>

          <div className="mt-5 w-full space-y-2 rounded-xl border border-line bg-surface-2 px-3.5 py-3 text-left">
            <p className="text-[12.5px] font-medium text-content">To finish</p>
            <ol className="ml-4 list-decimal space-y-1.5 text-[12.5px] leading-relaxed text-muted marker:text-subtle">
              <li>
                Get a key from{" "}
                <span className="font-mono text-[12px] text-content">console.groq.com/keys</span>.
              </li>
              <li>
                Put it in{" "}
                <span className="font-mono text-[12px] text-content">GROQ_API_KEY</span> in{" "}
                <span className="font-mono text-[12px] text-content">backend/.env</span>.
              </li>
              <li>Restart the backend.</li>
            </ol>
            <p className="pt-0.5 text-[11.5px] leading-relaxed text-subtle">
              The key stays on the server — it is never sent to the browser.
              Admin&nbsp;&rsaquo;&nbsp;Settings shows the same state.
            </p>
          </div>
        </>
      ) : (
        <p className="mt-1 max-w-[16rem] text-[13px] leading-relaxed text-muted">
          The assistant isn&rsquo;t ready to answer yet. An administrator needs to
          finish setting it up.
        </p>
      )}
    </div>
  );
}
