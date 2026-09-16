"use client";

import {
  CalendarClock,
  Check,
  CheckCircle2,
  Clock,
  Mail,
  Percent,
  Phone,
  Plus,
  Search,
  UserPlus,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { Fragment, Suspense, useMemo, useState } from "react";

import { StatTile, StatTileSkeleton } from "@/components/dashboard/StatTile";
import { PageHeader } from "@/components/layout/PageHeader";
import { SendRequestButton } from "@/components/customers/SendRequestDialog";
import type { ReferenceSubject } from "@/components/references/RecordReferenceDialog";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, ReferenceStatusBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState, Skeleton, TableSkeleton } from "@/components/ui/Feedback";
import { FilterBar } from "@/components/ui/FilterBar";
import { Input, Select } from "@/components/ui/Form";
import { Pagination } from "@/components/ui/Pagination";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate, formatNumber } from "@/lib/format";
import { REFERENCE_STATUS_LABELS, isCompletedReference } from "@/lib/roles";
import { useAsync, useDebounced } from "@/lib/useAsync";
import type { ReferenceStats } from "@/types/api";

const PAGE_SIZE = 25;

// Opens from "Record ask"; nothing to ship with the table until then.
const RecordReferenceDialog = dynamic(
  () =>
    import("@/components/references/RecordReferenceDialog").then(
      (m) => m.RecordReferenceDialog,
    ),
  { ssr: false },
);

/**
 * Why the eligible count is smaller than the won count.
 *
 * "22 converted" next to "3 ready to ask" invites the conclusion that the
 * portal lost nineteen accounts. It did not — they are in one of two
 * explainable states, and saying which one is the whole job of this hint.
 */
function wonHint(stats: ReferenceStats): string {
  if (stats.converted_leads === 0) return "Nothing won yet";
  const parts: string[] = [];
  if (stats.eligible_accounts > 0) {
    parts.push(`${formatNumber(stats.eligible_accounts)} ready to ask`);
  }
  if (stats.waiting_period > 0) {
    parts.push(
      `${formatNumber(stats.waiting_period)} before their reference date`,
    );
  }
  return parts.join(" · ");
}

/* An empty table has three quite different causes. Collapsing them into "no
   accounts" is how "the sheet was never synced" gets read as "we have no
   customers", which is a much worse piece of news than the truth. */
function emptyTitle(stats: ReferenceStats | null, term: string): string {
  if (term) return "No accounts match";
  if (!stats || stats.converted_leads === 0) return "No converted leads yet";
  if (stats.awaiting_sync > 0 && stats.eligible_accounts === 0) {
    return "No synced post-sale records yet";
  }
  if (stats.waiting_period > 0 && stats.eligible_accounts === 0) {
    return "Nothing is eligible yet";
  }
  return "No accounts match";
}

function emptyDescription(stats: ReferenceStats | null, term: string): string {
  if (term) return "Nothing in your scope matches that search.";
  if (!stats || stats.converted_leads === 0) {
    return "Accounts appear here once a lead in your scope is marked Converted and the post-sale sync supplies its invoice date.";
  }
  if (stats.awaiting_sync > 0 && stats.eligible_accounts === 0) {
    return `${formatNumber(stats.awaiting_sync)} won ${
      stats.awaiting_sync === 1 ? "account is" : "accounts are"
    } waiting on the post-sale sync. Nobody can be asked for a reference until it says they were invoiced.`;
  }
  if (stats.waiting_period > 0 && stats.eligible_accounts === 0) {
    return `${formatNumber(stats.waiting_period)} won ${
      stats.waiting_period === 1 ? "account has" : "accounts have"
    } not reached their reference date yet.`;
  }
  return "You see the accounts you own, plus everyone below you in the reporting chain.";
}

function ReferencesInner() {
  const { user } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const tab = params.get("tab") ?? "customers";
  const statusFilter = params.get("status") ?? "";
  const ownerFilter = params.get("owner") ?? "";
  const outcomeFilter = params.get("outcome") ?? "";

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [asking, setAsking] = useState<ReferenceSubject | null>(null);
  const [nonce, setNonce] = useState(0);

  const debounced = useDebounced(search, 300);

  function setTab(next: string) {
    router.replace(`/references?tab=${next}`);
    setPage(1);
    setSearch("");
  }

  /* Filters live in the URL, so a filtered view is a link somebody can send
     and the back button undoes one filter rather than the whole page. */
  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/references?${next.toString()}`);
    setPage(1);
  }

  function changeSearch(value: string) {
    setSearch(value);
    setPage(1);
  }

  const reload = () => setNonce((value) => value + 1);

  const stats = useAsync((signal) => api.references.stats(signal), [nonce]);
  // One kind of won account: leads this portal converted whose post-sale
  // record says they were invoiced 10+ days ago.
  //
  // Every filter below is a query parameter and the DATABASE does the work.
  // This page used to fetch the list and narrow it in React, which filters
  // only the rows that happened to arrive - an answer-shaped non-answer.
  const accounts = useAsync(
    (signal) =>
      api.references.accounts(
        {
          reference_status: statusFilter || undefined,
          search: debounced || undefined,
          owner_id: ownerFilter || undefined,
        },
        signal,
      ),
    // No `tab` here (and not below): none of these queries takes the tab as a
    // parameter, so including it refetched identical data every time somebody
    // switched tabs. `nonce` still forces a refresh after a recorded ask.
    [statusFilter, ownerFilter, debounced, nonce],
  );
  const references = useAsync(
    (signal) =>
      api.references.list(
        {
          search: debounced || undefined,
          outcome: outcomeFilter || undefined,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
    [debounced, outcomeFilter, page, nonce],
  );
  const [followUpGroupBy, setFollowUpGroupBy] = useState<"due_date" | "owner">("due_date");
  const followUps = useAsync(
    (signal) => api.references.followUps(false, followUpGroupBy, signal),
    [nonce, followUpGroupBy],
  );

  const term = debounced.trim();
  // The server has already applied the filters; this only pages what came
  // back, which is bounded by the eligible population and therefore small.
  const visibleAccounts = useMemo(() => accounts.data ?? [], [accounts.data]);
  const pagedAccounts = useMemo(
    () => visibleAccounts.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleAccounts, page],
  );

  // Owners to filter by, taken from the rows themselves - so the dropdown can
  // only ever offer people the caller is already allowed to see. Built once
  // per result rather than on every keystroke-driven render.
  const owners = useMemo(
    () =>
      Array.from(
        new Map(
          visibleAccounts
            .filter((account) => account.owner_user_id && account.owner_name)
            .map((account) => [
              account.owner_user_id as string,
              account.owner_name as string,
            ]),
        ),
      ).sort((a, b) => a[1].localeCompare(b[1])),
    [visibleAccounts],
  );

  // Every "yes" anyone gave us — the people we were actually referred to.
  const referred = useAsync(
    (signal) =>
      api.references.list(
        { outcome: "YES", search: debounced || undefined, page, page_size: PAGE_SIZE },
        signal,
      ),
    [debounced, page, nonce],
  );

  return (
    <>
      <PageHeader
        title="Reference Tracking"
        description="Every converted customer is somebody who could introduce the next one. Ask, record the answer, and come back when you said you would."
      />

      {/* ------------------------------------------------------ KPIs */}
      <div className="mb-5 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-5">
        {stats.loading || !stats.data ? (
          Array.from({ length: 5 }).map((_, index) => <StatTileSkeleton key={index} index={index} />)
        ) : (
          <>
            {/*
              The whole won book, with the reason only part of it is
              actionable said on the tile. These always add up:

                  eligible + waiting + awaiting sync === converted

              so a small eligible number below is explained rather than
              looking like the portal lost the other twenty.
            */}
            <StatTile
              index={0}
              label="Converted leads"
              value={stats.data.converted_leads}
              hint={wonHint(stats.data)}
              icon={Users}
              href="/leads?status=CONVERTED"
            />
            {/* Completed over eligible, with references actually received
                underneath. The module and the dashboard read the same two
                numbers from the same query, so they cannot disagree. */}
            <StatTile
              index={1}
              label="References completed"
              value={`${stats.data.requests_completed} / ${stats.data.eligible_accounts}`}
              hint={
                stats.data.eligible_accounts > 0
                  ? `${stats.data.references_taken} gave a reference`
                  : "No accounts are eligible yet"
              }
              icon={CheckCircle2}
              accent={stats.data.requests_completed > 0 ? "success" : "neutral"}
            />
            <StatTile
              index={2}
              label="Pending"
              value={stats.data.references_pending}
              hint="Asked, come back later"
              icon={Clock}
              accent="neutral"
            />
            {/* The score the business agreed: the gap left to close, as a
                negative percentage of the eligible book. 0% means every
                eligible account has given a reference. */}
            <StatTile
              index={3}
              label="Reference score"
              value={`${stats.data.reference_score}%`}
              hint={
                stats.data.eligible_accounts > 0
                  ? `${stats.data.references_taken} of ${stats.data.eligible_accounts} gave one · ${stats.data.reference_rate}% taken`
                  : "No accounts are eligible yet"
              }
              icon={Percent}
              accent={
                stats.data.reference_score === 0 && stats.data.eligible_accounts > 0
                  ? "success"
                  : stats.data.reference_score <= -50
                    ? "warning"
                    : "neutral"
              }
            />
            <StatTile
              index={4}
              label="Follow-ups due"
              value={stats.data.follow_ups_due}
              hint="Ask again today"
              icon={CalendarClock}
              accent={stats.data.follow_ups_due > 0 ? "warning" : "neutral"}
            />
          </>
        )}
      </div>

      <Tabs
        className="mb-4 w-fit"
        active={tab}
        onChange={setTab}
        tabs={[
          {
            key: "customers",
            // Counts the accounts the tab actually LISTS — the eligible ones,
            // not every won lead. A tab badge that disagrees with the table
            // under it is the same disease this whole module just had.
            label: "Ready to ask",
            count: stats.data === null ? undefined : stats.data?.eligible_accounts,
          },
          { key: "referred", label: "Referred people", count: referred.data?.total },
          { key: "references", label: "Every ask", count: references.data?.total },
          { key: "follow-ups", label: "Follow-ups due", count: followUps.data?.length },
        ]}
      />

      {/* --------------------------------------------------- accounts */}
      {tab === "customers" ? (
        <Card className="overflow-hidden">
          <FilterBar
            search={search}
            onSearchChange={changeSearch}
            searchPlaceholder="Search name, company, email or mobile…"
            searchLabel="Search accounts"
            resultCount={accounts.data?.length}
            resultNoun="accounts"
            filters={[
              {
                key: "status",
                label: "Reference",
                value: statusFilter,
                anyLabel: "Any status",
                options: Object.entries(REFERENCE_STATUS_LABELS).map(
                  ([value, label]) => ({ value, label }),
                ),
                onChange: (value) => setParam("status", value),
              },
              {
                key: "owner",
                label: "Owner",
                value: ownerFilter,
                anyLabel: "Anyone",
                options: owners.map(([id, name]) => ({ value: id, label: name })),
                onChange: (value) => setParam("owner", value),
              },
            ]}
          />

          {accounts.error ? (
            <ErrorState error={accounts.error} onRetry={accounts.reload} />
          ) : accounts.loading && !accounts.data ? (
            <TableSkeleton rows={8} columns={5} />
          ) : visibleAccounts.length === 0 ? (
            /* Three different situations, and only one of them is a problem.
               A bare "no accounts" hid the difference between "the sheet has
               not been synced" and "you genuinely have no won work". */
            <EmptyState
              icon={Users}
              title={emptyTitle(stats.data, term)}
              description={emptyDescription(stats.data, term)}
            />
          ) : (
            <>
              <TableWrap>
                <Table>
                  <THead>
                    <tr>
                      <TH>Account</TH>
                      <TH>Owner</TH>
                      <TH>Reference</TH>
                      <TH>Last asked</TH>
                      <TH align="right">Action</TH>
                    </tr>
                  </THead>
                  <TBody>
                    {pagedAccounts.map((account) => (
                      <TR key={`${account.subject_type}-${account.subject_id}`}>
                        <TD>
                          <Link
                            href={`/leads?search=${encodeURIComponent(account.subject_name)}`}
                            className="font-medium text-content hover:underline"
                          >
                            {account.subject_name}
                          </Link>
                          <span className="mt-0.5 flex items-center gap-1.5">
                            {account.company_name ? (
                              <span className="truncate text-[11.5px] text-subtle">
                                {account.company_name}
                              </span>
                            ) : null}
                            {/* WHY this row may be asked: the sheet's own
                                Reference Date, not a date we worked out. */}
                            {account.reference_date ? (
                              <span className="whitespace-nowrap text-[11.5px] text-subtle">
                                Reference date {formatDate(account.reference_date)}
                              </span>
                            ) : null}
                          </span>
                        </TD>
                        <TD data-label="Owner">
                          {account.owner_name ? (
                            <span className="flex items-center gap-2">
                              <Avatar name={account.owner_name} size="xs" />
                              <span className="text-[13px] text-muted">
                                {account.owner_name}
                              </span>
                            </span>
                          ) : (
                            <Badge className="border-line bg-surface-2 text-subtle">
                              Unassigned
                            </Badge>
                          )}
                        </TD>
                        <TD data-label="Reference">
                          <span className="flex items-center gap-1.5">
                            <ReferenceStatusBadge status={account.reference_status} />
                            {account.decline_count > 0 ? (
                              <Badge
                                className={
                                  account.decline_count >= 2
                                    ? "border-transparent bg-warning-soft text-warning"
                                    : "border-line bg-surface-2 text-subtle"
                                }
                                title={`Said no ${account.decline_count} time${
                                  account.decline_count === 1 ? "" : "s"
                                }`}
                              >
                                {account.decline_count}× declined
                              </Badge>
                            ) : null}
                          </span>
                        </TD>
                        <TD data-label="Last asked" className="whitespace-nowrap text-muted">
                          {formatDate(account.last_reference_asked_at)}
                        </TD>
                        <TD data-actions align="right">
                          <span className="flex items-center justify-end gap-2">
                            {/*
                              Three different rows, three different actions.

                              TAKEN      the conversation is over AND they gave
                                         one, so "+" offers another.
                              DECLINED   over, nothing to add. No "+": the
                                         plus means "add another reference",
                                         and there is no first one to add to.
                              otherwise  still askable.

                              The backend already keeps completed accounts out
                              of the pending queries; this is the same rule
                              said out loud, not the enforcement.
                            */}
                            {isCompletedReference(account.reference_status) ? (
                              <>
                                <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-success">
                                  <Check className="size-3.5" aria-hidden />
                                  Completed
                                </span>
                                {account.reference_status === "TAKEN" ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    aria-label={`Add another reference for ${account.subject_name}`}
                                    title="Add another reference"
                                    onClick={() => setAsking(account)}
                                  >
                                    <Plus className="size-3.5" aria-hidden />
                                  </Button>
                                ) : null}
                              </>
                            ) : (
                              <>
                                {/* Ask for the feedback first, then log the
                                    reference ask once they have replied. The
                                    feedback ask is the assignee's alone. */}
                                {account.owner_user_id === user?.id ? (
                                  <SendRequestButton
                                    customerId={account.subject_id}
                                    customerName={account.subject_name}
                                    subjectType={
                                      account.subject_type === "LEAD" ? "lead" : "customer"
                                    }
                                    onSent={accounts.reload}
                                    variant="subtle"
                                    label="Ask"
                                  />
                                ) : null}
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  onClick={() => setAsking(account)}
                                >
                                  Record ask
                                </Button>
                              </>
                            )}
                          </span>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={visibleAccounts.length}
                onPageChange={setPage}
                noun="accounts"
              />
            </>
          )}
        </Card>
      ) : null}

      {/* --------------------------------------------- referred people */}
      {tab === "referred" ? (
        <>
          <div className="mb-4">
            <Input
              type="search"
              placeholder="Search by referred name, company or mobile…"
              icon={<Search className="size-4" />}
              value={search}
              onChange={(event) => changeSearch(event.target.value)}
              aria-label="Search referred people"
            />
          </div>

          {referred.error ? (
            <Card>
              <ErrorState error={referred.error} onRetry={referred.reload} />
            </Card>
          ) : referred.loading && !referred.data ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="h-56 rounded-card" />
              ))}
            </div>
          ) : referred.data && referred.data.items.length === 0 ? (
            <Card>
              <EmptyState
                icon={UserPlus}
                title="Nobody has been referred yet"
                description="Record a “gave a reference” against a won account and the person they named appears here, with everything you captured about them."
              />
            </Card>
          ) : referred.data ? (
            <>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {referred.data.items.map((reference, index) => (
                  <Card
                    key={reference.id}
                    className="stagger flex flex-col p-4"
                    style={{ "--index": index } as React.CSSProperties}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-[15px] font-semibold text-content">
                          {reference.referred_name ?? "Name not captured"}
                        </p>
                        {reference.referred_company ? (
                          <p className="mt-0.5 truncate text-[13px] text-muted">
                            {reference.referred_company}
                          </p>
                        ) : null}
                      </div>
                      <Avatar name={reference.referred_name ?? "?"} size="sm" />
                    </div>

                    {/* The whole point of the card: the details you would
                        need to actually get in touch. */}
                    <dl className="mt-3.5 space-y-2 border-t border-line pt-3.5 text-[13px]">
                      <div className="flex items-center gap-2">
                        <Phone className="size-3.5 shrink-0 text-subtle" aria-hidden />
                        {reference.referred_mobile ? (
                          <a
                            href={`tel:${reference.referred_mobile.replace(/\s+/g, "")}`}
                            className="text-brand-700 hover:underline dark:text-brand-300"
                          >
                            {reference.referred_mobile}
                          </a>
                        ) : (
                          <span className="text-subtle">No mobile</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Mail className="size-3.5 shrink-0 text-subtle" aria-hidden />
                        {reference.referred_email ? (
                          <a
                            href={`mailto:${reference.referred_email}`}
                            className="truncate text-brand-700 hover:underline dark:text-brand-300"
                          >
                            {reference.referred_email}
                          </a>
                        ) : (
                          <span className="text-subtle">No email</span>
                        )}
                      </div>
                    </dl>

                    {reference.notes ? (
                      <p className="mt-3 rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-[12.5px] leading-relaxed text-muted">
                        {reference.notes}
                      </p>
                    ) : null}

                    <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line pt-3 text-[11.5px] text-subtle">
                      <span className="text-muted">Referred by</span>
                      {/* Not a link either way: the archived customer book is
                          Super Admin only, and most people reading this card
                          cannot open it. */}
                      <span className="font-medium text-content">
                        {reference.source_name ?? "—"}
                      </span>
                      {reference.source_type === "LEAD" ? (
                        <Badge className="border-transparent bg-info-soft text-info">
                          Converted lead
                        </Badge>
                      ) : (
                        <Badge className="border-line bg-surface-2 text-subtle">
                          Archived
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 text-[11.5px] text-subtle">
                      {formatDate(reference.asked_on)}
                      {reference.requested_by_name
                        ? ` · credited to ${reference.requested_by_name}`
                        : ""}
                    </p>
                  </Card>
                ))}
              </div>
              <Card className="mt-4">
                <Pagination
                  page={referred.data.page}
                  pageSize={referred.data.page_size}
                  total={referred.data.total}
                  onPageChange={setPage}
                  noun="referred people"
                />
              </Card>
            </>
          ) : null}
        </>
      ) : null}

      {/* ------------------------------------------------- references */}
      {tab === "references" ? (
        <Card className="overflow-hidden">
          <FilterBar
            search={search}
            onSearchChange={changeSearch}
            searchPlaceholder="Search by referred name, company or mobile…"
            searchLabel="Search references"
            resultCount={references.data?.total}
            resultNoun="asks"
            filters={[
              {
                key: "outcome",
                label: "Outcome",
                value: outcomeFilter,
                anyLabel: "Any outcome",
                // The same three answers the record dialog offers. "Not
                // shared" is a COMPLETED ask with nothing to chase, which is
                // why it is not folded in with "not right now".
                options: [
                  { value: "YES", label: "Gave a reference" },
                  { value: "NO", label: "Not right now" },
                  { value: "NOT_SHARED", label: "None to give" },
                ],
                onChange: (value) => setParam("outcome", value),
              },
            ]}
          />

          {references.error ? (
            <ErrorState error={references.error} onRetry={references.reload} />
          ) : references.loading && !references.data ? (
            <TableSkeleton rows={6} columns={5} />
          ) : references.data && references.data.items.length === 0 ? (
            <EmptyState
              icon={CheckCircle2}
              title="No references recorded yet"
              description="Nothing is seeded here. Record an ask against a converted customer and it appears in this list."
            />
          ) : references.data ? (
            <>
              <TableWrap>
                <Table>
                  <THead>
                    <tr>
                      <TH>Account</TH>
                      <TH>Outcome</TH>
                      <TH>Referred</TH>
                      <TH>Credited to</TH>
                      <TH>Asked on</TH>
                      <TH>Ask again</TH>
                    </tr>
                  </THead>
                  <TBody>
                    {references.data.items.map((reference) => (
                      <TR key={reference.id}>
                        <TD>
                          {/* Historical asks still point at the archived SAP
                              book, and that archive is Super Admin only now -
                              so this deliberately does NOT link there. A link
                              most of the people reading this page cannot open
                              is worse than no link. */}
                          <span className="flex items-center gap-1.5">
                            <span className="font-medium text-content">
                              {reference.source_name}
                            </span>
                            {reference.customer_id ? (
                              <Badge className="border-line bg-surface-2 text-subtle">
                                Archived
                              </Badge>
                            ) : (
                              <Badge className="border-transparent bg-info-soft text-info">
                                Lead
                              </Badge>
                            )}
                          </span>
                        </TD>
                        <TD>
                          <Badge
                            className={
                              reference.outcome === "YES"
                                ? "border-transparent bg-success-soft text-success"
                                : "border-transparent bg-warning-soft text-warning"
                            }
                          >
                            {reference.outcome === "YES" ? "Gave a reference" : "Later"}
                          </Badge>
                        </TD>
                        <TD>
                          {reference.referred_name || reference.referred_company ? (
                            <>
                              <span className="block text-[13px] text-content">
                                {reference.referred_name ?? "—"}
                              </span>
                              <span className="block text-[12px] text-subtle">
                                {reference.referred_company ?? reference.referred_mobile ?? ""}
                              </span>
                            </>
                          ) : (
                            <span className="text-subtle">—</span>
                          )}
                        </TD>
                        <TD className="whitespace-nowrap text-muted">
                          {reference.requested_by_name ?? "—"}
                        </TD>
                        <TD className="whitespace-nowrap text-muted">
                          {formatDate(reference.asked_on)}
                        </TD>
                        <TD className="whitespace-nowrap text-muted">
                          {formatDate(reference.next_reference_date)}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
              <Pagination
                page={references.data.page}
                pageSize={references.data.page_size}
                total={references.data.total}
                onPageChange={setPage}
                noun="references"
              />
            </>
          ) : null}
        </Card>
      ) : null}

      {/* -------------------------------------------------- follow-ups */}
      {tab === "follow-ups" ? (
        <Card className="overflow-hidden">
          <div className="flex items-center justify-end gap-2 border-b border-line p-3">
            <span className="text-[12.5px] text-subtle">Group by</span>
            <Select
              value={followUpGroupBy}
              onChange={(event) =>
                setFollowUpGroupBy(event.target.value as "due_date" | "owner")
              }
              aria-label="Group follow-ups by"
              className="w-auto min-w-36"
            >
              <option value="due_date">Due date</option>
              <option value="owner">Owner</option>
            </Select>
          </div>
          {followUps.error ? (
            <ErrorState error={followUps.error} onRetry={followUps.reload} />
          ) : followUps.loading && !followUps.data ? (
            <TableSkeleton rows={5} columns={4} />
          ) : followUps.data && followUps.data.length === 0 ? (
            <EmptyState
              icon={CalendarClock}
              title="Nothing due"
              description="When a customer says “ask me later”, the date you set brings them back here."
            />
          ) : followUps.data ? (
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>Account</TH>
                    <TH>Owner</TH>
                    <TH>Contact</TH>
                    <TH>Due</TH>
                    <TH align="right">Action</TH>
                  </tr>
                </THead>
                <TBody>
                  {followUps.data.map((row, index) => {
                    const previousOwner = index > 0 ? followUps.data![index - 1]?.owner_name : null;
                    const showOwnerHeader =
                      followUpGroupBy === "owner" && row.owner_name !== previousOwner;
                    return (
                      <Fragment key={`${row.subject_type}-${row.subject_id}`}>
                        {showOwnerHeader ? (
                          <tr key={`owner-${row.owner_name ?? "none"}`}>
                            <td
                              colSpan={5}
                              className="bg-surface-2 px-4 py-1.5 text-[11.5px] font-semibold uppercase tracking-wider text-subtle"
                            >
                              {row.owner_name ?? "Unassigned"}
                            </td>
                          </tr>
                        ) : null}
                        <TR>
                          <TD>
                            <Link
                              href={`/leads?search=${encodeURIComponent(row.subject_name)}`}
                              className="font-medium text-content hover:underline"
                            >
                              {row.subject_name}
                            </Link>
                            {row.company_name ? (
                              <span className="block truncate text-[11.5px] text-subtle">
                                {row.company_name}
                              </span>
                            ) : null}
                            {row.decline_count >= 2 ? (
                              <Badge
                                className="mt-1 border-transparent bg-warning-soft text-warning"
                                title={`Said no ${row.decline_count} times`}
                              >
                                {row.decline_count}× declined
                              </Badge>
                            ) : null}
                          </TD>
                          <TD className="text-muted">{row.owner_name ?? "—"}</TD>
                          <TD>
                            <span className="block text-[12.5px] text-muted">
                              {row.mobile ?? "—"}
                            </span>
                            <span className="block truncate text-[12px] text-subtle">
                              {row.email ?? ""}
                            </span>
                          </TD>
                          <TD>
                            <span className="block whitespace-nowrap text-[13px] text-content">
                              {formatDate(row.next_reference_date)}
                            </span>
                            {row.days_overdue > 0 ? (
                              <Badge className="mt-0.5 border-transparent bg-warning-soft text-warning">
                                {formatNumber(row.days_overdue)} day
                                {row.days_overdue === 1 ? "" : "s"} overdue
                              </Badge>
                            ) : (
                              <span className="text-[11.5px] text-subtle">Due today</span>
                            )}
                          </TD>
                          <TD align="right">
                            <Button size="sm" onClick={() => setAsking(row)}>
                              Ask now
                            </Button>
                          </TD>
                        </TR>
                      </Fragment>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
          ) : null}
        </Card>
      ) : null}

      {/* Mounted on the click that opens it; it rendered nothing when closed. */}
      {asking ? (
        <RecordReferenceDialog
          key={`${asking.subject_type}-${asking.subject_id}`}
          open
          onClose={() => setAsking(null)}
          onSaved={reload}
          subject={asking}
        />
      ) : null}
    </>
  );
}

export default function ReferencesPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-card" />}>
      <ReferencesInner />
    </Suspense>
  );
}
