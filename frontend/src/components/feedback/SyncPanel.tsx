"use client";

import { AlertTriangle, Check, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { ConfigRow } from "@/components/ui/ConfigRow";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/Feedback";
import { Select } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";
import type { NeedsReviewItem } from "@/types/api";

/**
 * Where an admin sees whether responses are actually arriving.
 *
 * Everything here is admin-only, which is why it is a separate tab rather
 * than something on the pending queue: a BDE cannot fix a webhook and does
 * not need to know one exists.
 */
export function SyncPanel() {
  const toast = useToast();
  const [nonce, setNonce] = useState(0);
  const [running, setRunning] = useState(false);
  const [resolving, setResolving] = useState<NeedsReviewItem | null>(null);

  const reload = () => setNonce((value) => value + 1);

  const status = useAsync((signal) => api.feedback.sync.status(signal), [nonce]);
  const review = useAsync((signal) => api.feedback.sync.needsReview(signal), [nonce]);
  const events = useAsync(
    (signal) => api.feedback.sync.events({ limit: 25 }, signal),
    [nonce],
  );

  async function runSync() {
    setRunning(true);
    try {
      const result = await api.feedback.sync.run();
      toast.success(
        "Sync finished",
        `${result.new_responses} new · ${result.already_synced} already synced · ` +
          `${result.unmatched} unmatched · ${result.errors} errors`,
      );
      reload();
    } catch (error) {
      toast.error("Could not sync", errorMessage(error));
    } finally {
      setRunning(false);
    }
  }

  if (status.error) return <Card><ErrorState error={status.error} onRetry={reload} /></Card>;

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------- setup */}
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Google Form sync</CardTitle>
            <CardDescription>
              Responses arrive on their own once the form is connected. Nothing here
              holds a Google credential — only a shared secret the script signs with.
            </CardDescription>
          </div>
          <Button
            variant="secondary"
            onClick={runSync}
            loading={running}
            disabled={!status.data?.pull_configured}
            title={
              status.data?.pull_configured
                ? "Pull anything the webhook missed"
                : "Set GOOGLE_SYNC_URL to enable manual sync"
            }
          >
            <RefreshCw className="size-4" aria-hidden />
            Sync now
          </Button>
        </CardHeader>

        <CardBody className="space-y-4">
          {status.loading && !status.data ? (
            <Skeleton className="h-28 rounded-lg" />
          ) : status.data ? (
            <>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
                <ConfigRow label="Webhook secret" ok={status.data.webhook_configured} variable="GOOGLE_SYNC_SECRET" />
                <ConfigRow label="Manual sync URL" ok={status.data.pull_configured} variable="GOOGLE_SYNC_URL" />
                <ConfigRow label="Form link" ok={status.data.form_url_configured} variable="Settings ▸ Company" />
                <ConfigRow label="Reference field" ok={status.data.reference_field_configured} variable="Settings ▸ Feedback" />
              </dl>

              {!status.data.form_url_configured || !status.data.reference_field_configured ? (
                <p className="rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-[12.5px] leading-relaxed text-muted">
                  Until the form link and its reference field are set, requests go out
                  without a code and every response falls back to matching on mobile,
                  email or an exact company name. That works — it just leaves more for
                  the review queue below. See{" "}
                  <span className="font-mono text-[12px] text-content">
                    docs/apps-script/README.md
                  </span>
                  .
                </p>
              ) : null}

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Processed" value={status.data.processed} />
                <Stat label="Needs review" value={status.data.needs_review} tone={status.data.needs_review > 0 ? "warning" : "neutral"} />
                <Stat label="Failed" value={status.data.failed} tone={status.data.failed > 0 ? "danger" : "neutral"} />
                <Stat
                  label="Last delivery"
                  value={status.data.last_delivery_at ? formatRelative(status.data.last_delivery_at) : "—"}
                  small
                />
              </div>
            </>
          ) : null}
        </CardBody>
      </Card>

      {/* ------------------------------------------------ needs review */}
      <Card className="overflow-hidden">
        <CardHeader className="block">
          <CardTitle>Needs review</CardTitle>
          <CardDescription>
            Responses that arrived but could not be placed. Nothing is ever discarded —
            an unmatched response is safer than one attached to the wrong account.
          </CardDescription>
        </CardHeader>

        {review.loading && !review.data ? (
          <Skeleton className="m-4 h-24 rounded-lg" />
        ) : review.data && review.data.length === 0 ? (
          <EmptyState
            icon={Check}
            title="Nothing waiting"
            description="Every response so far found its customer."
          />
        ) : review.data ? (
          <TableWrap>
            <Table>
              <THead>
                <tr>
                  <TH>Who they said they were</TH>
                  <TH>Why</TH>
                  <TH>Arrived</TH>
                  <TH align="right">Action</TH>
                </tr>
              </THead>
              <TBody>
                {review.data.map((item) => (
                  <TR key={item.id}>
                    <TD>
                      <span className="block font-medium text-content">
                        {item.company_name ?? item.customer_name ?? "—"}
                      </span>
                      <span className="block text-[12px] text-subtle">
                        {item.overall_rating !== null ? `Overall ${item.overall_rating}` : "No overall rating"}
                      </span>
                    </TD>
                    <TD>
                      <Badge
                        className={
                          item.match_status === "UNMATCHED"
                            ? "border-transparent bg-warning-soft text-warning"
                            : "border-transparent bg-info-soft text-info"
                        }
                      >
                        {item.match_status === "UNMATCHED" ? "No match" : "Duplicate"}
                      </Badge>
                    </TD>
                    <TD className="whitespace-nowrap text-muted">
                      {formatRelative(item.submitted_at ?? item.created_at)}
                    </TD>
                    <TD align="right">
                      <Button size="sm" variant="secondary" onClick={() => setResolving(item)}>
                        Resolve
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        ) : null}
      </Card>

      {/* ---------------------------------------------------- deliveries */}
      <Card className="overflow-hidden">
        <CardHeader className="block">
          <CardTitle>Deliveries</CardTitle>
          <CardDescription>
            Every response that reached the portal. Hashes and outcomes only — the
            payload itself is never stored.
          </CardDescription>
        </CardHeader>

        {events.loading && !events.data ? (
          <Skeleton className="m-4 h-24 rounded-lg" />
        ) : events.data && events.data.length === 0 ? (
          <EmptyState
            icon={RefreshCw}
            title="Nothing has arrived yet"
            description="Once the Apps Script is installed, submitted responses show up here within seconds."
          />
        ) : events.data ? (
          <TableWrap>
            <Table>
              <THead>
                <tr>
                  <TH>Response</TH>
                  <TH>Source</TH>
                  <TH>Outcome</TH>
                  <TH>When</TH>
                </tr>
              </THead>
              <TBody>
                {events.data.map((event) => (
                  <TR key={event.id}>
                    <TD className="font-mono text-[12px] text-muted">
                      {event.external_response_id.slice(0, 24)}
                      {event.external_response_id.length > 24 ? "…" : ""}
                    </TD>
                    <TD className="text-muted">{event.source}</TD>
                    <TD>
                      <Badge className={outcomeTone(event.status)}>{event.status}</Badge>
                      {event.error_detail ? (
                        <span className="ml-2 text-[12px] text-danger">{event.error_detail}</span>
                      ) : null}
                    </TD>
                    <TD className="whitespace-nowrap text-muted">
                      {formatDateTime(event.received_at)}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>
        ) : null}
      </Card>

      <ResolveDialog
        item={resolving}
        onClose={() => setResolving(null)}
        onResolved={() => {
          setResolving(null);
          reload();
        }}
      />
    </div>
  );
}

function outcomeTone(status: string): string {
  if (status === "PROCESSED") return "border-transparent bg-success-soft text-success";
  if (status === "FAILED") return "border-transparent bg-danger-soft text-danger";
  if (status === "DUPLICATE") return "border-transparent bg-info-soft text-info";
  return "border-line bg-surface-2 text-muted";
}

function Stat({
  label,
  value,
  tone = "neutral",
  small = false,
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "warning" | "danger";
  small?: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <p className="text-[11.5px] text-muted">{label}</p>
      <p
        className={cn(
          small ? "text-[13px]" : "text-[19px] font-semibold tabular-nums",
          tone === "warning" && "text-warning",
          tone === "danger" && "text-danger",
          tone === "neutral" && "text-content",
        )}
      >
        {value}
      </p>
    </div>
  );
}

/** Attach an unmatched response to the account it was actually about. */
function ResolveDialog({
  item,
  onClose,
  onResolved,
}: {
  item: NeedsReviewItem | null;
  onClose: () => void;
  onResolved: () => void;
}) {
  const toast = useToast();
  // "request:<id>" or "customer:<id>" - one picker, two kinds of target.
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);

  // Leads the portal actually asked come first: filing a response there
  // completes the ask, so the lead leaves the pending queue exactly as if the
  // code had matched. The archived SAP customers stay available for history.
  const openRequests = useAsync(
    (signal) => (item ? api.feedback.sync.openRequests(signal) : Promise.resolve(null)),
    [item?.id],
  );
  const customers = useAsync(
    (signal) =>
      // Not the customer book - that is Super Admin only now. This is the
      // narrow admin picker: ids and names, enough to file a response under.
      item ? api.feedback.sync.customers(signal) : Promise.resolve(null),
    [item?.id],
  );

  async function submit() {
    if (!item || !target) return;
    const separator = target.indexOf(":");
    const kind = target.slice(0, separator);
    const id = target.slice(separator + 1);
    setBusy(true);
    try {
      const result = await api.feedback.sync.resolve(
        item.id,
        kind === "request" ? { request_id: id } : { customer_id: id },
      );
      toast.success("Attached", `Now filed under ${result.customer}.`);
      setTarget("");
      onResolved();
    } catch (error) {
      toast.error("Could not attach", errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={item !== null}
      onClose={onClose}
      title="Attach this response"
      description={
        item
          ? `They said they were "${item.company_name ?? item.customer_name ?? "—"}".`
          : undefined
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!target}>
            Attach
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="flex items-start gap-2 rounded-lg border border-warning/25 bg-warning-soft px-3 py-2 text-[12.5px] leading-relaxed text-warning">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            Pick carefully. A response on the wrong account moves that department&rsquo;s
            average, and nothing later will flag it as wrong.
          </span>
        </p>

        <Select
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          aria-label="File this response under"
        >
          <option value="">Choose who this response is from…</option>
          <optgroup label="Leads awaiting feedback">
            {(openRequests.data ?? []).map((ask) => (
              <option key={ask.id} value={`request:${ask.id}`}>
                {ask.name}
                {ask.company_name ? ` — ${ask.company_name}` : ""} ({ask.reference})
              </option>
            ))}
          </optgroup>
          <optgroup label="Archived SAP customers">
            {(customers.data ?? []).map((customer) => (
              <option key={customer.id} value={`customer:${customer.id}`}>
                {customer.name}
                {customer.sap_code ? ` (${customer.sap_code})` : ""}
              </option>
            ))}
          </optgroup>
        </Select>
      </div>
    </Modal>
  );
}
