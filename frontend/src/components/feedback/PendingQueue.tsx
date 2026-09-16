"use client";

import { Clock, Copy, Send, X } from "lucide-react";
import { useState } from "react";

import { SendRequestButton } from "@/components/customers/SendRequestDialog";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Modal";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { PendingFeedbackItem } from "@/types/api";

/**
 * Who is still owed feedback, and where each one has got to.
 *
 * The state column is the point. Before requests were records, an asked
 * record and an un-asked one looked identical from here, so a BDE had no way
 * to tell "nobody has chased this" from "chased, waiting on them".
 */
export function PendingQueue({
  items,
  onChanged,
}: {
  items: PendingFeedbackItem[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  const [cancelling, setCancelling] = useState<PendingFeedbackItem | null>(null);
  const [busy, setBusy] = useState(false);

  async function confirmCancel() {
    if (!cancelling?.request_id) return;
    setBusy(true);
    try {
      const result = await api.feedback.cancelRequest(cancelling.request_id);
      toast.success("Request cancelled", result.message);
      setCancelling(null);
      onChanged();
    } catch (error) {
      toast.error("Could not cancel", errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <TableWrap>
        <Table>
          <THead>
            <tr>
              <TH>Who</TH>
              <TH>Owner</TH>
              <TH>State</TH>
              <TH align="right">Ask</TH>
            </tr>
          </THead>
          <TBody>
            {items.map((item) => (
              <TR key={`${item.type}-${item.id}`}>
                <TD>
                  <span className="block font-medium text-content">{item.name}</span>
                  <span className="block truncate text-[12px] text-subtle">
                    {item.company_name ?? item.mobile ?? item.email ?? ""}
                  </span>
                </TD>

                <TD data-label="Owner">
                  <Badge
                    className={
                      item.type === "LEAD"
                        ? "border-transparent bg-info-soft text-info"
                        : "border-line bg-surface-2 text-muted"
                    }
                  >
                    {item.type === "LEAD" ? "Converted lead" : "Converted customer"}
                  </Badge>
                  <span className="ml-2 inline-flex items-center gap-1.5">
                    {item.owner_name ? <Avatar name={item.owner_name} size="xs" /> : null}
                    <span className="text-[12.5px] text-muted">
                      {item.owner_name ?? "Unowned"}
                    </span>
                  </span>
                </TD>

                <TD data-label="State">
                  {item.state === "AWAITING" ? (
                    <div className="space-y-0.5">
                      <span className="inline-flex items-center gap-1.5 text-[13px] text-warning">
                        <Clock className="size-3.5" aria-hidden />
                        Awaiting response
                      </span>
                      <ReferenceCode reference={item.request_reference} />
                      <span className="block text-[11.5px] text-subtle">
                        asked {formatRelative(item.requested_at)}
                      </span>
                    </div>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-[13px] text-subtle">
                      <Send className="size-3.5" aria-hidden />
                      Not asked
                      <span className="text-[11.5px]">
                        · {formatRelative(item.since)}
                      </span>
                    </span>
                  )}
                </TD>

                <TD align="right" data-actions>
                  <div className="inline-flex items-center gap-1.5">
                    {/* The ask is the assignee's - it is recorded on their own
                        lead log, and the server refuses anyone else. A manager
                        is shown who sends it rather than a button that would
                        only ever fail. */}
                    {item.type !== "LEAD" || item.owner_user_id === user?.id ? (
                      <SendRequestButton
                        customerId={item.id}
                        customerName={item.name}
                        subjectType={item.type === "LEAD" ? "lead" : "customer"}
                        label={item.state === "AWAITING" ? "Send again" : "Send request"}
                        onSent={onChanged}
                      />
                    ) : (
                      <span className="text-[12px] text-subtle">
                        {item.owner_name ? `${item.owner_name} sends` : "Assignee sends"}
                      </span>
                    )}
                    {item.state === "AWAITING" ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Cancel this request"
                        title="Cancel this request"
                        onClick={() => setCancelling(item)}
                      >
                        <X className="size-4" aria-hidden />
                      </Button>
                    ) : null}
                  </div>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </TableWrap>

      <ConfirmDialog
        open={cancelling !== null}
        onClose={() => setCancelling(null)}
        onConfirm={confirmCancel}
        title={`Cancel ${cancelling?.request_reference ?? "this request"}?`}
        description={`${cancelling?.name ?? ""} goes back to "not asked".`}
        confirmLabel="Cancel request"
        destructive
        loading={busy}
      >
        <p className="text-sm text-muted">
          If they fill the form after this, the response still arrives — it just
          will not be matched to this ask, and lands in Sync ▸ Needs review instead.
        </p>
      </ConfirmDialog>
    </>
  );
}

/** The half a human quotes on the phone. Click to copy. */
function ReferenceCode({ reference }: { reference: string | null }) {
  const toast = useToast();
  if (!reference) return null;

  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard
          ?.writeText(reference)
          .then(() => toast.success("Copied", reference))
          .catch(() => {
            /* clipboard is blocked in some browsers; not worth an error */
          });
      }}
      className={cn(
        "group inline-flex items-center gap-1 rounded px-1 -mx-1 font-mono text-[11.5px]",
        "text-muted transition-colors hover:bg-surface-hover hover:text-content",
      )}
    >
      {reference}
      <Copy className="size-3 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
    </button>
  );
}

/** Four numbers worth having above the queue. */
export function PendingSummary({
  items,
  received,
  averageRating,
  scaleMax,
}: {
  items: PendingFeedbackItem[];
  received: number;
  averageRating: number | null;
  scaleMax: number;
}) {
  const awaiting = items.filter((item) => item.state === "AWAITING").length;
  const notAsked = items.length - awaiting;

  // Answered, out of everyone we asked. A request still open counts in the
  // denominator - it is a chase that has not landed yet - but nothing that
  // was never asked does, or the rate would punish having a backlog.
  const asked = received + awaiting;
  const rate = asked > 0 ? Math.round((received / asked) * 100) : null;

  return (
    <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile label="Not asked" value={notAsked} hint="Nobody has chased these" tone={notAsked > 0 ? "warning" : "neutral"} />
      <Tile label="Awaiting response" value={awaiting} hint="Asked, waiting on them" tone="neutral" />
      <Tile
        label="Response rate"
        value={rate === null ? "—" : `${rate}%`}
        hint="Answered, of everyone asked"
        tone={rate !== null && rate >= 50 ? "success" : "neutral"}
      />
      <Tile
        label="Average rating"
        value={averageRating === null ? "—" : `${averageRating.toFixed(1)}/${scaleMax}`}
        hint="Across every response"
        tone="neutral"
      />
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: number | string;
  hint: string;
  tone: "neutral" | "warning" | "success";
}) {
  return (
    <div className="rounded-card border border-line bg-surface px-3.5 py-3">
      <p className="text-[12px] font-medium text-muted">{label}</p>
      <p
        className={cn(
          "mt-0.5 text-[22px] font-semibold tabular-nums",
          tone === "warning" && "text-warning",
          tone === "success" && "text-success",
          tone === "neutral" && "text-content",
        )}
      >
        {value}
      </p>
      <p className="text-[11.5px] text-subtle">{hint}</p>
    </div>
  );
}
