"use client";

import { Check, X } from "lucide-react";

import { AssistantMark } from "@/components/chat/AssistantMark";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { ConfigRow } from "@/components/ui/ConfigRow";
import { Skeleton } from "@/components/ui/Feedback";
import { useChat } from "@/lib/chat";
import { cn } from "@/lib/cn";

/**
 * Why the assistant is, or is not, running.
 *
 * It exists because the feature hides itself completely when it is not
 * configured — which is right for a BDE, who cannot fix it, and useless for
 * an administrator, who then has no way to tell "switched off" from "broken".
 * This is the one place that difference is visible.
 *
 * These two settings are environment variables, not `app_settings` rows: an
 * API key does not belong in a database an admin UI can read back. So this
 * card reports; it does not edit.
 */
export function AssistantStatus() {
  // Read from the chat context rather than asking /chat/status again: the
  // provider wrapping the whole portal has already fetched exactly this.
  const { setup, enabled, statusLoaded } = useChat();

  if (!statusLoaded) return <Skeleton className="h-56 rounded-card" />;

  return (
    <Card>
      <CardHeader className="block">
        <CardTitle className="flex items-center gap-2">
          <AssistantMark className="size-4 text-brand-600 dark:text-brand-300" />
          Assistant
        </CardTitle>
        <CardDescription>
          The in-portal chat. It answers from the portal&rsquo;s own data, through the
          same permissions as every page.
        </CardDescription>
      </CardHeader>

      <CardBody className="space-y-4">
        <div
          className={cn(
            "flex items-center gap-2 rounded-lg border px-3 py-2.5 text-[13px]",
            enabled
              ? "border-transparent bg-success-soft text-success"
              : "border-line bg-surface-2 text-muted",
          )}
        >
          <span
            className={cn(
              "grid size-4 shrink-0 place-items-center rounded-full",
              enabled ? "bg-success text-white" : "bg-subtle/25 text-muted",
            )}
          >
            {enabled ? <Check className="size-2.5" /> : <X className="size-2.5" />}
          </span>
          {enabled
            ? `Running on ${setup?.model ?? "the configured model"}.`
            : "Not running — the launcher is hidden for everyone."}
        </div>

        {setup ? (
          <>
            <dl className="space-y-1.5 text-[13px]">
              <div className="flex items-center justify-between gap-3 py-0.5">
                <dt className="text-muted">API provider</dt>
                <dd className="font-medium text-content">{setup.provider}</dd>
              </div>
              <ConfigRow label="API key" ok={setup.has_api_key} variable="GROQ_API_KEY" />
              <ConfigRow
                label="Feature flag"
                ok={setup.flag_enabled}
                variable="CHAT_ENABLED"
              />
              <div className="flex items-center justify-between gap-3 py-0.5">
                <dt className="text-muted">Model</dt>
                <dd className="font-mono text-[12px] text-content">{setup.model}</dd>
              </div>
            </dl>

            {enabled ? null : (
              <div className="space-y-2 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
                <p className="text-[12.5px] font-medium text-content">To switch it on</p>
                <ol className="ml-4 list-decimal space-y-1 text-[12.5px] text-muted marker:text-subtle">
                  {!setup.has_api_key && (
                    <li>
                      Put a key from{" "}
                      <span className="font-mono text-[12px] text-content">
                        console.groq.com/keys
                      </span>{" "}
                      into <span className="font-mono text-[12px]">GROQ_API_KEY</span> in{" "}
                      <span className="font-mono text-[12px]">backend/.env</span>.
                    </li>
                  )}
                  {!setup.flag_enabled && (
                    <li>
                      Set <span className="font-mono text-[12px]">CHAT_ENABLED=true</span> in the
                      same file.
                    </li>
                  )}
                  <li>Restart the backend. The launcher appears bottom-right.</li>
                </ol>
                <p className="text-[12px] text-subtle">
                  The key is read by the backend only. It is never sent to the browser
                  and never appears in this page&rsquo;s data.
                </p>
              </div>
            )}
          </>
        ) : null}
      </CardBody>
    </Card>
  );
}
