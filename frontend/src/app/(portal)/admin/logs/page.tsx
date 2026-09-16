"use client";

import { ChevronDown, ChevronRight, ScrollText, Search } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/Feedback";
import { Input, Select } from "@/components/ui/Form";
import { Pagination } from "@/components/ui/Pagination";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import type { AuditEvent } from "@/types/api";

const PAGE_SIZE = 50;

/**
 * Groups the actions into something a person can scan, rather than a flat
 * list of forty enum values. The value stored is unchanged — this is only
 * how the filter offers them.
 */
const GROUPS: [label: string, prefixes: string[]][] = [
  ["People", ["USER_", "ROLE_", "PASSWORD_", "REPORTING_", "DEPARTMENT_HEAD"]],
  ["Leads", ["LEAD_"]],
  ["References", ["REFERENCE_", "REVIEW_"]],
  ["Feedback", ["FEEDBACK_"]],
  ["Assistant", ["CHAT_"]],
  ["Settings", ["SETTING"]],
];

/** ROLE_CHANGED -> Role changed. The stored value stays as it is. */
function humanise(action: string): string {
  const words = action.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function toneFor(action: string): string {
  if (/DELETED|DEACTIVATED|CANCELLED|LOST/.test(action))
    return "border-transparent bg-danger-soft text-danger";
  if (/CREATED|REACTIVATED|CONVERTED|RECEIVED/.test(action))
    return "border-transparent bg-success-soft text-success";
  if (/CHANGED|UPDATED|ASSIGNED|RESOLVED/.test(action))
    return "border-transparent bg-info-soft text-info";
  return "border-line bg-surface-2 text-muted";
}

// No Suspense boundary here, unlike the other list pages: this one reads no
// search params, so there was nothing for React to suspend on.
export default function LogsPage() {
  const [group, setGroup] = useState("");
  const [entityType, setEntityType] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const listing = useAsync(
    (signal) =>
      api.admin.audit(
        {
          entity_type: entityType || undefined,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
    [entityType, page],
  );

  // Group and free-text filtering happen here rather than server-side: the
  // endpoint filters by exact action and entity, and a page of 50 rows is
  // nothing to sift in the browser. No extra request for a narrowing.
  const rows = (listing.data?.items ?? []).filter((event) => {
    if (group) {
      const prefixes = GROUPS.find(([label]) => label === group)?.[1] ?? [];
      if (!prefixes.some((prefix) => event.action.startsWith(prefix))) return false;
    }
    if (search) {
      const term = search.toLowerCase();
      const haystack = `${event.action} ${event.actor_name ?? ""} ${event.entity_type ?? ""}`;
      if (!haystack.toLowerCase().includes(term)) return false;
    }
    return true;
  });

  return (
    <>
      <PageHeader
        title="Activity log"
        description="Every action the portal recorded, newest first — who did it, to what, and from where. Written in the same transaction as the change itself, so it cannot disagree with the data."
      />

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-3.5">
          <div className="min-w-52 flex-1">
            <Input
              type="search"
              placeholder="Search by action or person…"
              icon={<Search className="size-4" />}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Search the log"
            />
          </div>

          <Select
            value={group}
            onChange={(event) => setGroup(event.target.value)}
            aria-label="Area"
            className="w-auto"
          >
            <option value="">All areas</option>
            {GROUPS.map(([label]) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </Select>

          <Select
            value={entityType}
            onChange={(event) => {
              setEntityType(event.target.value);
              setPage(1);
            }}
            aria-label="Record type"
            className="w-auto"
          >
            <option value="">All records</option>
            {["USER", "LEAD", "CUSTOMER", "REFERENCE", "FEEDBACK", "SETTING", "IMPORT", "CHAT"].map(
              (value) => (
                <option key={value} value={value}>
                  {humanise(value)}
                </option>
              ),
            )}
          </Select>
        </div>

        {listing.error ? (
          <ErrorState error={listing.error} onRetry={listing.reload} />
        ) : listing.loading && !listing.data ? (
          <TableSkeleton rows={10} columns={5} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title={search || group ? "Nothing matches that" : "Nothing recorded yet"}
            description={
              search || group
                ? "Try a broader filter."
                : "Actions appear here as people use the portal."
            }
          />
        ) : (
          <>
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>When</TH>
                    <TH>Who</TH>
                    <TH>Action</TH>
                    <TH>Record</TH>
                    <TH>From</TH>
                  </tr>
                </THead>
                <TBody>
                  {rows.map((event, index) => (
                    <LogRow key={event.id} event={event} index={index} />
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            {listing.data ? (
              <Pagination
                page={listing.data.page}
                pageSize={listing.data.page_size}
                total={listing.data.total}
                onPageChange={setPage}
                noun="entries"
              />
            ) : null}
          </>
        )}
      </Card>
    </>
  );
}

function LogRow({ event, index }: { event: AuditEvent; index: number }) {
  const [open, setOpen] = useState(false);
  // Only rows that actually changed something are worth expanding.
  const hasDetail = Boolean(event.before || event.after);

  return (
    <>
      <TR
        className={cn("stagger", hasDetail && "cursor-pointer")}
        style={{ "--index": index } as React.CSSProperties}
        onClick={hasDetail ? () => setOpen((value) => !value) : undefined}
      >
        <TD className="whitespace-nowrap">
          <span className="block text-[13px] text-content">
            {formatRelative(event.created_at)}
          </span>
          <span className="block text-[11.5px] text-subtle">
            {formatDateTime(event.created_at)}
          </span>
        </TD>

        <TD>
          {event.actor_name ? (
            <span className="inline-flex items-center gap-2">
              <Avatar name={event.actor_name} size="xs" />
              <span className="text-[13px] text-content">{event.actor_name}</span>
            </span>
          ) : (
            <span className="text-[13px] text-subtle">System</span>
          )}
        </TD>

        <TD>
          <span className="inline-flex items-center gap-1.5">
            {hasDetail ? (
              open ? (
                <ChevronDown className="size-3.5 text-subtle" aria-hidden />
              ) : (
                <ChevronRight className="size-3.5 text-subtle" aria-hidden />
              )
            ) : (
              <span className="size-3.5" aria-hidden />
            )}
            <Badge className={toneFor(event.action)}>{humanise(event.action)}</Badge>
          </span>
        </TD>

        <TD className="text-muted">{event.entity_type ? humanise(event.entity_type) : "—"}</TD>

        <TD className="whitespace-nowrap font-mono text-[12px] text-subtle">
          {event.ip_address ?? "—"}
        </TD>
      </TR>

      {open && hasDetail ? (
        <tr className="bg-surface-2">
          <td colSpan={5} className="px-4 py-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <ChangeBlock title="Before" value={event.before} />
              <ChangeBlock title="After" value={event.after} />
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ChangeBlock({
  title,
  value,
}: {
  title: string;
  value: Record<string, unknown> | null;
}) {
  if (!value) {
    return (
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">
          {title}
        </p>
        <p className="text-[12.5px] text-subtle">—</p>
      </div>
    );
  }

  return (
    <div>
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">
        {title}
      </p>
      <dl className="space-y-0.5 rounded-lg border border-line bg-surface px-3 py-2">
        {Object.entries(value).map(([key, entry]) => (
          <div key={key} className="flex gap-2 text-[12.5px]">
            <dt className="shrink-0 text-subtle">{key}</dt>
            <dd className="min-w-0 break-all font-mono text-[12px] text-content">
              {entry === null || entry === undefined ? "—" : String(entry)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
