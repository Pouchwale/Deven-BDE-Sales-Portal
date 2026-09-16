"use client";

import {
  CalendarClock,
  CheckCircle2,
  Inbox,
  Plus,
  Repeat2,
  Target,
  TrendingDown,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { StatTile, StatTileSkeleton } from "@/components/dashboard/StatTile";
import { PageHeader } from "@/components/layout/PageHeader";
import { FilterBar } from "@/components/ui/FilterBar";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, Skeleton, TableSkeleton } from "@/components/ui/Feedback";
import { Pagination } from "@/components/ui/Pagination";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDate, formatNumber, formatRelative } from "@/lib/format";
import {
  LEAD_PRIORITY_TONE,
  LEAD_STATUS_LABELS,
  LEAD_STATUS_TONE,
  PIPELINE_ORDER,
  STALE_AFTER_DAYS,
  STAT_KEY,
  isStale,
} from "@/lib/leads";
import { isAdmin, isLeadership } from "@/lib/roles";
import { useAsync, useDebounced } from "@/lib/useAsync";
import type { LeadStatus } from "@/types/api";

const PAGE_SIZE = 25;

// Both dialogs open from a click and render nothing until then, so they are
// fetched at that point rather than shipped with the table.
const CreateLeadDialog = dynamic(
  () => import("@/components/leads/LeadDialogs").then((m) => m.CreateLeadDialog),
  { ssr: false },
);
const LeadDetailDialog = dynamic(
  () => import("@/components/leads/LeadDialogs").then((m) => m.LeadDetailDialog),
  { ssr: false },
);

function LeadsInner() {
  const { user } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  /**
   * Who gets which tabs, and why they are not the same question.
   *
   * ASSIGNING and DOING are separate. A manager does both: they hand leads to
   * their team AND carry their own, so they need "My work" as well as
   * "Assigned by me". An administrator only assigns - "My work" is an empty
   * page for them, and an empty page that never fills reads as a broken one.
   *
   *   Admin / Super Admin   Assigned by me · All leads
   *   Manager               My work · Assigned by me · All leads
   *   BDE / Sales           My work · All leads
   *
   * The default tab follows the same logic: land on the thing that is
   * actually yours to do.
   */
  const assigns = isLeadership(user?.role);
  const carriesOwnWork = !isAdmin(user?.role);
  const requestedTab = params.get("tab");
  const tab = requestedTab ?? (carriesOwnWork ? "my-work" : "assigned-by-me");
  const assigneeFilter = params.get("assignee") ?? "";
  const priorityParam = params.get("priority") ?? "";

  /**
   * Put one filter into the URL, keeping the others.
   *
   * The URL is the state, deliberately: a filtered view is then something you
   * can bookmark, reload and paste to a colleague. Rebuilt from the current
   * params each time rather than hand-concatenated, which is how the previous
   * version quietly dropped whichever filter was not being changed.
   */
  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("tab", tab);
    router.replace(`/leads?${next.toString()}`);
    setPage(1);
  }
  const statusParam = params.get("status") ?? "";
  const openOnly = params.get("open_only") === "true";

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [openLead, setOpenLead] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const debounced = useDebounced(search, 300);
  const reload = () => setNonce((value) => value + 1);

  const stats = useAsync((signal) => api.leads.stats(signal), [nonce]);
  const queue = useAsync(
    (signal) =>
      carriesOwnWork
        ? api.workQueue({ mine_only: true }, signal)
        : Promise.resolve(null),
    // The work queue is the caller's own, whichever tab is showing.
    [nonce, carriesOwnWork],
  );

  // Who the caller assigned leads to, and how many each. A grouped count from
  // the server - the chips have to show a number for somebody whose leads are
  // not on the page being looked at.
  const assignedByMe = useAsync(
    (signal) => (assigns ? api.leads.assignedByMeCounts(signal) : Promise.resolve([])),
    [assigns, nonce],
  );

  const onAssignedTab = tab === "assigned-by-me";
  const leads = useAsync(
    (signal) =>
      api.leads.list(
        {
          search: debounced || undefined,
          status: statusParam || undefined,
          open_only: openOnly || undefined,
          // A flag, never a user id: the server resolves it to the token's
          // own subject, so this cannot be pointed at another manager.
          assigned_by_me: onAssignedTab || undefined,
          // `assigned_to`, the name the API actually takes. Sent as
          // assigned_to_user_id it was silently ignored, so picking a person
          // still listed everybody's leads.
          assigned_to: onAssignedTab && assigneeFilter ? assigneeFilter : undefined,
          priority: priorityParam || undefined,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
    // `onAssignedTab` IS a query parameter (assigned_by_me), so it stays;
    // `tab` was redundant with it and refetched on every tab switch.
    [
      debounced,
      statusParam,
      priorityParam,
      openOnly,
      page,
      nonce,
      onAssignedTab,
      assigneeFilter,
    ],
  );

  const assignedTotal = assignedByMe.data?.reduce((sum, row) => sum + row.count, 0) ?? 0;

  function setTab(next: string) {
    router.replace(`/leads?tab=${next}`);
    setPage(1);
  }

  function setAssignee(next: string) {
    router.replace(`/leads?tab=assigned-by-me${next ? `&assignee=${next}` : ""}`);
    setPage(1);
  }

  return (
    <>
      <PageHeader
        title="Assigned Leads"
        description="Two genuinely different kinds of work: prospects a head handed you, and converted customers who asked you to come back later. They are kept apart on purpose."
        actions={
          isLeadership(user?.role) ? (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" aria-hidden />
              Assign a lead
            </Button>
          ) : null
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-5">
        {stats.loading || !stats.data ? (
          Array.from({ length: 5 }).map((_, index) => <StatTileSkeleton key={index} index={index} />)
        ) : (
          <>
            <StatTile index={0} label="Total leads" value={stats.data.total} icon={Target} />
            <StatTile
              index={1}
              label="Open leads"
              value={stats.data.open}
              hint="Not yet closed"
              icon={Inbox}
            />
            <StatTile
              index={2}
              label="Follow-ups due"
              value={stats.data.follow_ups_due}
              hint="Scheduled for today or earlier"
              icon={CalendarClock}
              accent={stats.data.follow_ups_due > 0 ? "warning" : "neutral"}
            />
            <StatTile
              index={3}
              label="No recent activity"
              value={stats.data.no_recent_activity}
              hint="Nothing logged for 7+ days"
              icon={TrendingDown}
              accent={stats.data.no_recent_activity > 0 ? "warning" : "neutral"}
            />
            <StatTile
              index={4}
              label="Converted"
              value={stats.data.converted}
              icon={CheckCircle2}
              accent={stats.data.converted > 0 ? "success" : "neutral"}
            />
          </>
        )}
      </div>

      <Tabs
        className="mb-4 w-fit"
        active={tab}
        onChange={setTab}
        tabs={[
          // Anyone who carries leads of their own, which is everyone except
          // an administrator.
          ...(carriesOwnWork
            ? [
                {
                  key: "my-work",
                  label: "My work",
                  count:
                    (queue.data?.assigned_total ?? 0) +
                    (queue.data?.follow_up_total ?? 0),
                },
              ]
            : []),
          // Anyone who hands leads out, which is everyone from manager up.
          ...(assigns
            ? [
                {
                  key: "assigned-by-me",
                  label: "Assigned by me",
                  count: assignedTotal,
                },
              ]
            : []),
          { key: "all", label: "All leads", count: leads.data?.total },
        ]}
      />

      {/* ---------------------------------------------------- my work */}
      {tab === "my-work" && carriesOwnWork ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card className="overflow-hidden">
            <CardHeader className="block">
              <CardTitle>Assigned leads</CardTitle>
              <CardDescription>Prospects a head handed you.</CardDescription>
            </CardHeader>
            {queue.error ? (
              <ErrorState error={queue.error} onRetry={queue.reload} />
            ) : queue.loading && !queue.data ? (
              <TableSkeleton rows={4} columns={3} />
            ) : queue.data && queue.data.assigned_leads.length === 0 ? (
              <EmptyState
                icon={Target}
                title="Nothing assigned to you"
                description="When a manager assigns you a lead it appears here, and you get a notification."
              />
            ) : queue.data ? (
              <ul className="divide-y divide-line">
                {queue.data.assigned_leads.map((lead) => (
                  <li key={lead.id}>
                    <button
                      type="button"
                      onClick={() => setOpenLead(lead.id)}
                      className="flex w-full items-start justify-between gap-3 px-5 py-3 text-left transition-colors hover:bg-surface-hover"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[13.5px] font-medium text-content">
                          {lead.name}
                        </span>
                        <span className="block truncate text-[12.5px] text-muted">
                          {lead.company_name ?? "No company"}
                          {lead.mobile ? ` · ${lead.mobile}` : ""}
                        </span>
                        <span className="mt-1 flex items-center gap-1.5">
                          <Badge className={LEAD_STATUS_TONE[lead.status]}>
                            {LEAD_STATUS_LABELS[lead.status]}
                          </Badge>
                          {lead.next_follow_up_date ? (
                            <span className="text-[11.5px] text-subtle">
                              Follow up {formatDate(lead.next_follow_up_date)}
                            </span>
                          ) : null}
                        </span>
                      </span>
                      <Badge className={LEAD_PRIORITY_TONE[lead.priority]}>
                        {lead.priority}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>

          <Card className="overflow-hidden">
            <CardHeader className="block">
              <CardTitle>Reference follow-ups</CardTitle>
              <CardDescription>
                Customers who said &ldquo;ask me later&rdquo; — not leads, and never
                counted as leads.
              </CardDescription>
            </CardHeader>
            {queue.loading && !queue.data ? (
              <TableSkeleton rows={4} columns={3} />
            ) : queue.data && queue.data.reference_follow_ups.length === 0 ? (
              <EmptyState
                icon={CalendarClock}
                title="Nothing due"
                description="Record a “not right now” against a customer and the date you set brings them back here."
              />
            ) : queue.data ? (
              <ul className="divide-y divide-line">
                {queue.data.reference_follow_ups.map((row) => (
                  <li key={`${row.subject_type}-${row.subject_id}`} className="px-5 py-3">
                    <Link
                      href={`/references?tab=follow-ups`}
                      className="block truncate text-[13.5px] font-medium text-content hover:underline"
                    >
                      {row.subject_name}
                    </Link>
                    <p className="truncate text-[12.5px] text-muted">
                      {row.mobile ?? row.email ?? row.company_name ?? "Converted lead"}
                    </p>
                    <p className="mt-1 text-[11.5px] text-subtle">
                      Due {formatDate(row.next_reference_date)}
                      {row.days_overdue > 0
                        ? ` · ${formatNumber(row.days_overdue)} day${
                            row.days_overdue === 1 ? "" : "s"
                          } overdue`
                        : ""}
                    </p>
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
        </div>
      ) : null}

      {/* ------------------------------------- assigned by me / all leads */}
      {tab === "all" || onAssignedTab ? (
        <Card className="overflow-hidden">
          {/*
            Who did I give leads to, and how many each.

            Counts come from a grouped query on the server, not from the rows
            on this page - a chip has to show 8 for somebody even when none of
            their eight are on screen.
          */}
          {onAssignedTab ? (
            <div className="flex flex-wrap items-center gap-1.5 border-b border-line p-3.5">
              <span className="mr-1 text-[12px] font-medium text-subtle">
                Assigned to
              </span>
              <button
                type="button"
                onClick={() => setAssignee("")}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[12.5px] transition-colors",
                  assigneeFilter === ""
                    ? "border-brand-600 bg-brand-600 text-white"
                    : "border-line bg-surface text-muted hover:bg-surface-hover",
                )}
              >
                Everyone {assignedTotal}
              </button>
              {(assignedByMe.data ?? []).map((row) => (
                <button
                  key={row.user_id ?? "unassigned"}
                  type="button"
                  disabled={!row.user_id}
                  onClick={() => setAssignee(row.user_id ?? "")}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[12.5px] transition-colors",
                    assigneeFilter === row.user_id
                      ? "border-brand-600 bg-brand-600 text-white"
                      : "border-line bg-surface text-muted hover:bg-surface-hover",
                  )}
                >
                  {row.name} {row.count}
                </button>
              ))}
              {assignedByMe.data && assignedByMe.data.length === 0 ? (
                <span className="text-[12.5px] text-subtle">
                  You have not assigned any leads yet.
                </span>
              ) : null}
            </div>
          ) : null}

          <FilterBar
            search={search}
            onSearchChange={(value) => {
              setSearch(value);
              setPage(1);
            }}
            searchPlaceholder="Search name, company, mobile or email…"
            searchLabel="Search leads"
            resultCount={leads.data?.total}
            resultNoun="leads"
            filters={[
              {
                key: "status",
                label: "Stage",
                value: statusParam,
                anyLabel: "All stages",
                // In pipeline order, with the live count per stage, so the
                // dropdown doubles as a read of where the work is sitting.
                options: PIPELINE_ORDER.map((value) => ({
                  value,
                  label: LEAD_STATUS_LABELS[value],
                  count: stats.data?.[STAT_KEY[value]],
                })),
                onChange: (value) => setParam("status", value),
              },
              {
                key: "priority",
                label: "Priority",
                value: priorityParam,
                anyLabel: "Any priority",
                options: [
                  { value: "HIGH", label: "High" },
                  { value: "MEDIUM", label: "Medium" },
                  { value: "LOW", label: "Low" },
                ],
                onChange: (value) => setParam("priority", value),
              },
              {
                key: "open_only",
                label: "Show",
                value: openOnly ? "open" : "",
                anyLabel: "Everything",
                options: [{ value: "open", label: "Open only" }],
                onChange: (value) => setParam("open_only", value ? "true" : ""),
              },
            ]}
          />

          {leads.error ? (
            <ErrorState error={leads.error} onRetry={leads.reload} />
          ) : leads.loading && !leads.data ? (
            <TableSkeleton rows={8} columns={6} />
          ) : leads.data && leads.data.items.length === 0 ? (
            <EmptyState
              icon={Target}
              title="No leads yet"
              description="Nothing is seeded. A manager assigns a lead and it shows up here for everyone in the chain."
              action={
                isLeadership(user?.role) ? (
                  <Button size="sm" onClick={() => setCreating(true)}>
                    Assign the first lead
                  </Button>
                ) : null
              }
            />
          ) : leads.data ? (
            <>
              <TableWrap>
                <Table>
                  <THead>
                    <tr>
                      <TH>Lead</TH>
                      <TH>Stage</TH>
                      <TH>Priority</TH>
                      <TH>Assigned to</TH>
                      <TH>Follow up</TH>
                      <TH align="right">Last activity</TH>
                    </tr>
                  </THead>
                  <TBody>
                    {leads.data.items.map((lead) => (
                      <TR key={lead.id}>
                        <TD>
                          <button
                            type="button"
                            onClick={() => setOpenLead(lead.id)}
                            className="text-left font-medium text-content hover:underline"
                          >
                            {lead.name}
                          </button>
                          <span className="block truncate text-[12px] text-subtle">
                            {lead.company_name ?? "—"}
                          </span>
                        </TD>
                        <TD data-label="Stage">
                          <Badge className={LEAD_STATUS_TONE[lead.status as LeadStatus]}>
                            {LEAD_STATUS_LABELS[lead.status as LeadStatus]}
                          </Badge>
                        </TD>
                        <TD data-label="Priority">
                          <Badge className={LEAD_PRIORITY_TONE[lead.priority]}>
                            {lead.priority}
                          </Badge>
                        </TD>
                        <TD data-label="Assigned to">
                          {lead.assigned_to_name ? (
                            <span className="flex items-center gap-2">
                              <Avatar name={lead.assigned_to_name} size="xs" />
                              <span className="text-[13px] text-muted">
                                {lead.assigned_to_name}
                              </span>
                              {lead.reassignment_count > 0 ? (
                                <span
                                  className="inline-flex items-center gap-0.5 text-[11px] text-subtle"
                                  title={`Reassigned ${lead.reassignment_count} time${
                                    lead.reassignment_count === 1 ? "" : "s"
                                  }`}
                                >
                                  <Repeat2 className="size-3" aria-hidden />
                                  {lead.reassignment_count}
                                </span>
                              ) : null}
                            </span>
                          ) : (
                            <span className="text-subtle">—</span>
                          )}
                        </TD>
                        <TD data-label="Follow up" className="whitespace-nowrap text-muted">
                          {formatDate(lead.next_follow_up_date)}
                        </TD>
                        <TD
                          align="right"
                          data-label="Last activity"
                          className={cn(
                            "whitespace-nowrap",
                            isStale(lead.status, lead.last_activity_at)
                              ? "font-medium text-warning"
                              : "text-muted",
                          )}
                          title={
                            isStale(lead.status, lead.last_activity_at)
                              ? `Nothing logged for ${STALE_AFTER_DAYS}+ days`
                              : undefined
                          }
                        >
                          {lead.last_activity_at ? (
                            formatRelative(lead.last_activity_at)
                          ) : (
                            <span className="text-subtle">None</span>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
              <Pagination
                page={leads.data.page}
                pageSize={leads.data.page_size}
                total={leads.data.total}
                onPageChange={setPage}
                noun="leads"
              />
            </>
          ) : null}
        </Card>
      ) : null}

      {/* Mounted only while open. They already remounted on every open via
          `key`, and rendered nothing when closed - so this changes no
          behaviour, it just keeps 550 lines of dialog out of the page's
          first load. */}
      {creating ? (
        <CreateLeadDialog
          open
          onClose={() => setCreating(false)}
          onSaved={reload}
        />
      ) : null}
      {openLead ? (
        <LeadDetailDialog
          key={openLead}
          leadId={openLead}
          onClose={() => setOpenLead(null)}
          onChanged={reload}
        />
      ) : null}
    </>
  );
}

export default function LeadsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-card" />}>
      <LeadsInner />
    </Suspense>
  );
}
