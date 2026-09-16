"use client";

import {
  History,
  MessageSquareHeart,
  Phone,
  RotateCcw,
  Star,
  Undo2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, InlineError, TableSkeleton } from "@/components/ui/Feedback";
import { Select, Textarea } from "@/components/ui/Form";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";
import type { TimelineEntry } from "@/types/api";

/** What a person can log by hand against a completed customer. */
const LOGGABLE = [
  { value: "FEEDBACK_REQUESTED", label: "Asked for feedback" },
  { value: "CALL", label: "Called" },
  { value: "WHATSAPP", label: "WhatsApp" },
  { value: "EMAIL", label: "Emailed" },
  { value: "MEETING", label: "Met" },
  { value: "NOTE", label: "Note" },
];

const KIND_TONE: Record<TimelineEntry["kind"], string> = {
  ACTIVITY: "bg-surface-2 text-subtle",
  REFERENCE: "bg-info-soft text-info",
  FEEDBACK: "bg-success-soft text-success",
};

type Filter = "ALL" | "NOTES" | "FEEDBACK" | "REFERENCE";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "ALL", label: "All" },
  { key: "NOTES", label: "Notes & calls" },
  { key: "FEEDBACK", label: "Feedback" },
  { key: "REFERENCE", label: "References" },
];

const FEEDBACK_ACTIVITY_TYPES = new Set(["FEEDBACK_REQUESTED", "REVIEW_REQUESTED"]);

function matchesFilter(entry: TimelineEntry, filter: Filter): boolean {
  switch (filter) {
    case "ALL":
      return true;
    case "FEEDBACK":
      return entry.kind === "FEEDBACK" || FEEDBACK_ACTIVITY_TYPES.has(entry.activity_type);
    case "REFERENCE":
      return entry.kind === "REFERENCE";
    case "NOTES":
      return entry.kind === "ACTIVITY" && !FEEDBACK_ACTIVITY_TYPES.has(entry.activity_type);
  }
}

/**
 * The history of a COMPLETED customer.
 *
 * A customer that came from SAP is a won deal — it has already been through
 * the lead pipeline. What is still open about it is feedback and references,
 * so this is a feedback timeline rather than a stage machine.
 */
export function CustomerTimeline({ customerId }: { customerId: string }) {
  const toast = useToast();

  const [nonce, setNonce] = useState(0);
  const [activityType, setActivityType] = useState(LOGGABLE[0]!.value);
  const [remark, setRemark] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [filter, setFilter] = useState<Filter>("ALL");

  const timeline = useAsync(
    (signal) => api.customers.timeline(customerId, signal),
    [customerId, nonce],
  );

  const reload = () => setNonce((value) => value + 1);

  // Filtered once. The list and the "nothing matches" test used to run the
  // same filter separately, so every render walked the timeline twice.
  const visible = useMemo(
    () => (timeline.data ?? []).filter((entry) => matchesFilter(entry, filter)),
    [timeline.data, filter],
  );

  async function log() {
    setBusy(true);
    setError(null);
    try {
      await api.customers.logActivity(customerId, activityType, remark || null);
      setRemark("");
      toast.success("Logged to the timeline");
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  async function undo(entry: TimelineEntry) {
    setBusy(true);
    setError(null);
    try {
      await api.customers.undoActivity(customerId, entry.id);
      toast.success("Undone", "The entry stays on the timeline, marked.");
      reload();
    } catch (cause) {
      toast.error("Could not undo", errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="block">
        <CardTitle>Feedback timeline</CardTitle>
        <CardDescription>
          This account is a completed deal. What is still open is the feedback and
          the reference — every touchpoint lands here.
        </CardDescription>
      </CardHeader>

      <CardBody className="space-y-3 border-b border-line bg-surface-2">
        <div className="flex flex-wrap items-end gap-2">
          <Select
            value={activityType}
            onChange={(event) => setActivityType(event.target.value)}
            aria-label="What happened"
            className="w-auto min-w-44"
          >
            {LOGGABLE.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
          <Button onClick={log} loading={busy}>
            Add to timeline
          </Button>
        </div>
        <Textarea
          value={remark}
          onChange={(event) => setRemark(event.target.value)}
          placeholder="Remark — what was said, what to do next."
          className="min-h-16 bg-surface"
          aria-label="Remark"
        />
        {error ? <InlineError>{errorMessage(error)}</InlineError> : null}
      </CardBody>

      {timeline.data && timeline.data.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 border-b border-line bg-surface-2 px-5 py-2.5">
          {FILTERS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setFilter(option.key)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors",
                filter === option.key
                  ? "border-transparent bg-brand-600 text-white dark:bg-brand-500"
                  : "border-line bg-surface text-muted hover:text-content",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}

      {timeline.error ? (
        <ErrorState error={timeline.error} onRetry={timeline.reload} />
      ) : timeline.loading && !timeline.data ? (
        <TableSkeleton rows={4} columns={2} />
      ) : timeline.data && timeline.data.length === 0 ? (
        <EmptyState
          icon={History}
          title="Nothing logged yet"
          description="Ask for feedback or record a reference — it all appears here."
        />
      ) : timeline.data && visible.length === 0 ? (
        <EmptyState
          icon={History}
          title="Nothing matches this filter"
          description="Switch back to “All” to see the full history."
        />
      ) : timeline.data ? (
        <ol className="divide-y divide-line">
          {visible.map((entry) => (
            <li
              key={`${entry.kind}-${entry.id}`}
              className={cn("flex items-start gap-3 px-5 py-3.5", entry.undone_at && "opacity-60")}
            >
              <span
                className={cn(
                  "mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg",
                  KIND_TONE[entry.kind],
                )}
                aria-hidden
              >
                {entry.kind === "FEEDBACK" ? (
                  <MessageSquareHeart className="size-4" />
                ) : entry.kind === "REFERENCE" ? (
                  <Star className="size-4" />
                ) : (
                  <Phone className="size-4" />
                )}
              </span>

              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium text-content">
                  <span className={cn(entry.undone_at && "line-through")}>{entry.title}</span>
                  {entry.rating !== null ? (
                    <Badge className="border-transparent bg-success-soft text-success">
                      {entry.rating.toFixed(1)}
                    </Badge>
                  ) : null}
                  {entry.undone_at ? (
                    <Badge className="border-line bg-surface-2 text-subtle">
                      <Undo2 className="size-3" aria-hidden />
                      Undone
                    </Badge>
                  ) : null}
                </p>
                {entry.remark ? (
                  <p className="mt-0.5 break-words text-[12.5px] leading-relaxed text-muted">
                    {entry.remark}
                  </p>
                ) : null}
                <p className="mt-1 text-[11.5px] text-subtle">
                  {entry.actor_name ?? "System"} · {formatRelative(entry.created_at)} ·{" "}
                  {formatDateTime(entry.created_at)}
                </p>
              </div>

              {entry.can_undo ? (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void undo(entry)}
                  title="Take this entry back"
                >
                  <RotateCcw className="size-3.5" aria-hidden />
                  Undo
                </Button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </Card>
  );
}
