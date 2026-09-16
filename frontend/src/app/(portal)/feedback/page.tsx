"use client";

import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Clock,
  FileSpreadsheet,
  FlaskConical,
  Gauge,
  MessageSquareHeart,
  Star,
  Upload,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { RangeToggle, type MonthRange } from "@/components/charts/RangeToggle";
import { HBarChart, TrendLine } from "@/components/charts/lazy";
import { StatTile, StatTileSkeleton } from "@/components/dashboard/StatTile";
import { CustomerReviews } from "@/components/feedback/CustomerReviews";
import { PendingQueue, PendingSummary } from "@/components/feedback/PendingQueue";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, InlineError, Skeleton, TableSkeleton } from "@/components/ui/Feedback";
import { FilterBar } from "@/components/ui/FilterBar";
import { Select } from "@/components/ui/Form";
import { Pagination } from "@/components/ui/Pagination";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { useAuth } from "@/lib/auth";
import { api, errorMessage } from "@/lib/api";
import { isAdmin, isLeadership } from "@/lib/roles";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useToast } from "@/lib/toast";
import { useAsync, useDebounced } from "@/lib/useAsync";
import type {
  CommitResult,
  DryRunResult,
  FeedbackPopulation,
  User,
} from "@/types/api";

const PAGE_SIZE = 25;

// The Google-Form sync console: an admin-only sub-tab, and the heaviest thing
// on this route. Fetched when that tab is opened.
const SyncPanel = dynamic(
  () => import("@/components/feedback/SyncPanel").then((m) => m.SyncPanel),
  { ssr: false, loading: () => <Skeleton className="h-96 rounded-card" /> },
);

function FeedbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const requestedTab = params.get("tab") ?? "analysis";

  const [page, setPage] = useState(1);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((value) => value + 1);

  // Customer-review filters. Debounced search, so typing does not fire a
  // request per keystroke; the two selects apply immediately.
  const [reviewSearchInput, setReviewSearchInput] = useState("");
  const reviewSearch = useDebounced(reviewSearchInput, 300);
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [ratingFilter, setRatingFilter] = useState("");

  function changeFilter(apply: () => void) {
    apply();
    setPage(1);
  }

  const analysis = useAsync((signal) => api.feedback.analysis(signal), [nonce]);

  /**
   * Everyone signed in reads this module now. Customer feedback is how the
   * company sees its own performance, and a BDE who cannot see that Dispatch
   * is at 2.9 has no way to understand the complaint they are about to take.
   *
   * What stays narrow is acting: the alert queue and its assignment are for
   * administrators and department heads, and the API returns an empty alert
   * list to everybody else rather than a 403.
   */
  const { user } = useAuth();
  const admin = isAdmin(user?.role);

  // Only fetched for an admin - the endpoint is admin-only, and asking as a
  // BDE would be a guaranteed 403 on every page load.
  const syncStatus = useAsync(
    (signal) => (admin ? api.feedback.sync.status(signal) : Promise.resolve(null)),
    [admin, nonce],
  );
  const tab = requestedTab;

  // Every filter is a query parameter: the DATABASE narrows the list. A
  // department head hunting unhappy customers must not be shown only the
  // unhappy ones who happened to land on the page already fetched.
  const responses = useAsync(
    (signal) =>
      api.feedback.list(
        {
          search: reviewSearch || undefined,
          department_id: departmentFilter || undefined,
          rating: ratingFilter || undefined,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
    // Not keyed on `tab`: the query does not take one, so including it
    // refetched the identical list on every tab switch.
    [reviewSearch, departmentFilter, ratingFilter, page, nonce],
  );
  const pending = useAsync((signal) => api.feedback.pending(signal), [nonce]);
  // Why the queue is the size it is. Same service as Reference Tracking's
  // KPIs, so the two modules cannot describe different books.
  const population = useAsync((signal) => api.feedback.pendingSummary(signal), [nonce]);
  // Manager+ only, and it feeds the alert-assignee dropdown - which a BDE
  // never sees. Fetching it unconditionally 403'd on every BDE page load.
  const canAssign = isLeadership(user?.role);
  const actionable = useAsync(
    (signal) => (canAssign ? api.users.actionable(signal) : Promise.resolve([])),
    [canAssign],
  );
  const [trendRange, setTrendRange] = useState<MonthRange>(6);
  const toast = useToast();

  async function assignAlert(alertId: string, userId: string) {
    try {
      await api.feedback.assignAlert(alertId, userId || null);
      toast.success(userId ? "Alert assigned" : "Assignment cleared");
      reload();
    } catch (cause) {
      toast.error("Could not update the assignment", errorMessage(cause));
    }
  }

  if (analysis.error) {
    return (
      <>
        <PageHeader title="Feedback &amp; Reviews" />
        <Card>
          <ErrorState error={analysis.error} onRetry={analysis.reload} />
        </Card>
      </>
    );
  }

  const stats = analysis.data?.stats;

  return (
    <>
      <PageHeader
        title="Feedback &amp; Reviews"
        description="What customers said about each department, on a rolling window. A department that drops below the threshold raises one alert until it recovers."
      />

      {analysis.data && analysis.data.alerts.length > 0 ? (
        <div className="mb-5 flex items-start gap-3 rounded-card border border-warning/30 bg-warning-soft px-4 py-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <div>
            <p className="text-[13.5px] font-semibold text-content">
              {analysis.data.alerts.length === 1
                ? "A department needs attention"
                : `${analysis.data.alerts.length} departments need attention`}
            </p>
            <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">
              {analysis.data.alerts
                .map(
                  (alert) =>
                    `${alert.department_name} (${alert.average_rating}/${analysis.data?.scale_max} over ${alert.response_count} ratings)`,
                )
                .join(" · ")}{" "}
              — threshold is {analysis.data.threshold}, over the last{" "}
              {analysis.data.window_days} days.
            </p>
          </div>
        </div>
      ) : null}

      <div className="mb-5 grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        {analysis.loading || !stats ? (
          Array.from({ length: 4 }).map((_, index) => <StatTileSkeleton key={index} index={index} />)
        ) : (
          <>
            <StatTile
              index={0}
              label="Feedback responses"
              value={stats.total_responses}
              hint={`${formatNumber(stats.imported_responses)} imported from the form`}
              icon={MessageSquareHeart}
            />
            <StatTile
              index={1}
              label="Average rating"
              value={
                stats.average_rating === null
                  ? "—"
                  : `${stats.average_rating}/${stats.rating_scale_max}`
              }
              hint="Overall satisfaction"
              icon={Gauge}
            />
            <StatTile
              index={2}
              label="Would recommend"
              value={
                stats.would_recommend_rate === null ? "—" : `${stats.would_recommend_rate}%`
              }
              hint="Of those who answered"
              icon={Star}
              accent="neutral"
            />
            <StatTile
              index={3}
              label="Attention needed"
              value={stats.departments_below_threshold}
              hint="Departments below threshold"
              icon={AlertTriangle}
              accent={stats.departments_below_threshold > 0 ? "danger" : "neutral"}
            />
          </>
        )}
      </div>

      {(
        <Tabs
          className="mb-4 w-fit"
          active={tab}
          onChange={(next) => router.replace(`/feedback?tab=${next}`)}
          tabs={[
            { key: "analysis", label: "Analysis" },
            { key: "responses", label: "Customer reviews", count: responses.data?.total },
            // Only for the people who work them. Everyone else reads the
            // department averages and the reviews; an always-empty tab would
            // just look broken.
            ...(analysis.data?.handles_alerts
              ? [{ key: "alerts", label: "Alerts", count: analysis.data.alerts.length }]
              : []),
            { key: "pending", label: "Pending requests", count: pending.data?.length },
            { key: "import", label: "Import" },
            // Admin only: a BDE cannot fix a webhook and does not need to
            // know one exists.
            ...(admin
              ? [{ key: "sync", label: "Sync", count: syncStatus.data?.needs_review || undefined }]
              : []),
          ]}
        />
      )}

      {/* --------------------------------------------------- analysis */}
      {tab === "analysis" && analysis.data ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="block">
              <CardTitle>
                Department average rating (out of {analysis.data.scale_max})
              </CardTitle>
              <CardDescription>
                Last {analysis.data.window_days} days. A department below the dashed
                line is flagged.
              </CardDescription>
            </CardHeader>
            <CardBody>
              <HBarChart
                data={analysis.data.departments.map((row) => ({
                  label: row.department_name,
                  value: row.average_rating ?? 0,
                  flagged: row.below_threshold,
                }))}
                max={analysis.data.scale_max}
                threshold={analysis.data.threshold}
                thresholdLabel={`Threshold ${analysis.data.threshold}`}
                valueFormatter={(value) => (value === 0 ? "—" : value.toFixed(2))}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Responses per month</CardTitle>
                <CardDescription>Volume over time.</CardDescription>
              </div>
              <RangeToggle value={trendRange} onChange={setTrendRange} />
            </CardHeader>
            <CardBody>
              <TrendLine
                data={analysis.data.monthly.slice(-trendRange).map((row) => ({
                  label: row.month,
                  value: row.responses,
                }))}
              />
            </CardBody>
          </Card>

          <Card className="overflow-hidden lg:col-span-2">
            <CardHeader className="block">
              <CardTitle>Departments</CardTitle>
              <CardDescription>
                Every department, including those with no responses — &ldquo;no
                data&rdquo; is a finding, not a row to hide.
              </CardDescription>
            </CardHeader>
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>Department</TH>
                    <TH align="right">Average</TH>
                    <TH align="right">Responses</TH>
                    <TH>Status</TH>
                  </tr>
                </THead>
                <TBody>
                  {analysis.data.departments.map((row) => (
                    <TR key={row.department_id}>
                      <TD className="font-medium">{row.department_name}</TD>
                      <TD align="right">
                        {row.average_rating === null ? (
                          <span className="text-subtle">—</span>
                        ) : (
                          row.average_rating.toFixed(2)
                        )}
                      </TD>
                      <TD align="right" className={row.response_count === 0 ? "text-subtle" : ""}>
                        {formatNumber(row.response_count)}
                      </TD>
                      <TD>
                        {row.below_threshold ? (
                          <Badge className="border-transparent bg-danger-soft text-danger">
                            Below threshold
                          </Badge>
                        ) : !row.enough_responses ? (
                          <Badge className="border-line bg-surface-2 text-subtle">
                            Not enough responses
                          </Badge>
                        ) : (
                          <Badge className="border-transparent bg-success-soft text-success">
                            Healthy
                          </Badge>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </Card>
        </div>
      ) : null}

      {/* -------------------------------------------------- responses */}
      {tab === "responses" ? (
        <>
          {responses.data?.items.some((row) => row.is_sample) ? (
            <div className="mb-3 flex items-start gap-3 rounded-card border border-warning/30 bg-warning-soft px-4 py-3">
              <FlaskConical className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
              <div>
                <p className="text-[13.5px] font-semibold text-content">
                  Some of these are sample reviews
                </p>
                <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">
                  They are marked <span className="font-medium">Sample</span> and exist
                  only to show what this screen looks like with data in it. They count
                  towards the averages above. Remove them with{" "}
                  <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11.5px]">
                    python -m app.seeds.sample_feedback --remove
                  </code>
                  .
                </p>
              </div>
            </div>
          ) : null}

          <Card className="mb-3 overflow-hidden">
            <FilterBar
              className="border-b-0"
              search={reviewSearchInput}
              onSearchChange={(value) =>
                changeFilter(() => setReviewSearchInput(value))
              }
              searchPlaceholder="Search customer, company or what they wrote…"
              searchLabel="Search reviews"
              resultCount={responses.data?.total}
              resultNoun="reviews"
              filters={[
                {
                  key: "department",
                  label: "Department",
                  value: departmentFilter,
                  anyLabel: "All departments",
                  options: (analysis.data?.departments ?? []).map((department) => ({
                    value: department.department_id,
                    label: department.department_name,
                    count: department.response_count,
                  })),
                  onChange: (value) => changeFilter(() => setDepartmentFilter(value)),
                },
                {
                  key: "rating",
                  label: "Rating",
                  value: ratingFilter,
                  anyLabel: "Any rating",
                  // Bands around the configured threshold, because "who is
                  // unhappy" is the question, and the threshold is already
                  // what the rest of the module means by unhappy.
                  options: [
                    { value: "LOW", label: `Below ${analysis.data?.threshold ?? 3}` },
                    { value: "MID", label: "Around the threshold" },
                    { value: "HIGH", label: "Happy" },
                  ],
                  onChange: (value) => changeFilter(() => setRatingFilter(value)),
                },
              ]}
            />
          </Card>

          {responses.error ? (
            <Card>
              <ErrorState error={responses.error} onRetry={responses.reload} />
            </Card>
          ) : responses.loading && !responses.data ? (
            <Card>
              <TableSkeleton rows={4} columns={3} />
            </Card>
          ) : responses.data ? (
            <>
              <CustomerReviews
                items={responses.data.items}
                departments={analysis.data?.departments ?? []}
                scaleMax={analysis.data?.scale_max ?? 5}
                threshold={analysis.data?.threshold ?? 3}
              />
              <Card className="mt-3">
                <Pagination
                  page={responses.data.page}
                  pageSize={responses.data.page_size}
                  total={responses.data.total}
                  onPageChange={setPage}
                  noun="reviews"
                />
              </Card>
            </>
          ) : null}
        </>
      ) : null}

      {tab === "alerts" && analysis.data ? (
        <Card className="overflow-hidden">
          {analysis.data.alerts.length === 0 ? (
            <EmptyState
              icon={AlertTriangle}
              title="No open alerts"
              description="A department raises one alert when its rolling average drops below the threshold, and it resolves itself on recovery."
            />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>Department</TH>
                    <TH align="right">Average</TH>
                    <TH align="right">Ratings</TH>
                    <TH align="right">Threshold</TH>
                    <TH>Opened</TH>
                    <TH>Assigned to</TH>
                  </tr>
                </THead>
                <TBody>
                  {analysis.data.alerts.map((alert) => (
                    <TR key={alert.id}>
                      <TD className="font-medium">{alert.department_name}</TD>
                      <TD align="right" className="font-semibold text-danger">
                        {Number(alert.average_rating).toFixed(2)}
                      </TD>
                      <TD align="right">{formatNumber(alert.response_count)}</TD>
                      <TD align="right" className="text-muted">
                        {Number(alert.threshold).toFixed(1)}
                      </TD>
                      <TD className="whitespace-nowrap text-muted">
                        {formatDateTime(alert.opened_at)}
                      </TD>
                      <TD>
                        <Select
                          aria-label={`Assign ${alert.department_name}'s alert`}
                          className="w-auto min-w-36"
                          value={alert.assigned_to_user_id ?? ""}
                          onChange={(event) => void assignAlert(alert.id, event.target.value)}
                        >
                          <option value="">Unassigned</option>
                          {(actionable.data ?? []).map((person: User) => (
                            <option key={person.id} value={person.id}>
                              {person.name}
                            </option>
                          ))}
                        </Select>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </Card>
      ) : null}

      {/* --------------------------------------------------- pending */}
      {tab === "pending" ? (
        <>
          {pending.data ? (
            <PendingSummary
              items={pending.data}
              received={analysis.data?.stats.total_responses ?? 0}
              averageRating={analysis.data?.stats.average_rating ?? null}
              scaleMax={analysis.data?.scale_max ?? 5}
            />
          ) : null}

          <Card className="overflow-hidden">
            {pending.error ? (
              <ErrorState error={pending.error} onRetry={pending.reload} />
            ) : pending.loading && !pending.data ? (
              <TableSkeleton rows={6} columns={4} />
            ) : pending.data && pending.data.length === 0 ? (
              /* An empty queue has four quite different causes, and only one
                 of them means the work is done. A bare "nobody is waiting"
                 read as "we are all caught up" even when the real answer was
                 "the post-sale sheet has never been synced". */
              <EmptyState
                icon={Clock}
                title={pendingEmptyTitle(population.data)}
                description={pendingEmptyDescription(population.data)}
              />
            ) : pending.data ? (
              <PendingQueue items={pending.data} onChanged={reload} />
            ) : null}
          </Card>
        </>
      ) : null}

      {/* ------------------------------------------------------- sync */}
      {tab === "sync" && admin ? <SyncPanel /> : null}

      {/* ----------------------------------------------------- import */}
      {tab === "import" ? <ImportPanel onImported={reload} /> : null}
    </>
  );
}

/**
 * The two-step import.
 *
 * The dry run is not optional: Google Forms headers are whole question
 * sentences, they change when somebody edits the form, and a silent
 * mismapping corrupts the analysis invisibly. Nothing is written until the
 * mapping on screen has been seen.
 */
function ImportPanel({ onImported }: { onImported: () => void }) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<DryRunResult | null>(null);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const history = useAsync((signal) => api.feedback.imports(signal), [result]);

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runDryRun(chosen: File) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setPreview(await api.feedback.dryRun(chosen));
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const committed = await api.feedback.commit(file);
      setResult(committed);
      setPreview(null);
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      toast.success(
        "Import complete",
        `${committed.created_count} created, ${committed.skipped_count} skipped.`,
      );
      onImported();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader className="block">
          <CardTitle>Import the Google Forms export</CardTitle>
          <CardDescription>
            Upload the .xlsx or .csv. You will see exactly what mapped to what before
            anything is written.
          </CardDescription>
        </CardHeader>
        <CardBody className="space-y-4">
          <label className="flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-surface-2 px-6 py-8 text-center transition-colors hover:border-brand-500 hover:bg-surface-hover">
            <Upload className="mb-2 size-5 text-subtle" aria-hidden />
            <span className="text-[13.5px] font-medium text-content">
              {file ? file.name : "Choose the export file"}
            </span>
            <span className="mt-0.5 text-[12px] text-subtle">
              .xlsx or .csv, up to 10MB
            </span>
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xls,.csv"
              className="sr-only"
              onChange={(event) => {
                const chosen = event.target.files?.[0] ?? null;
                setFile(chosen);
                if (chosen) void runDryRun(chosen);
              }}
            />
          </label>

          {busy ? <p className="text-[13px] text-muted">Working…</p> : null}
          {error ? <InlineError>{errorMessage(error)}</InlineError> : null}

          {preview ? (
            <div className="space-y-3.5 rounded-xl border border-line bg-surface-2 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[13.5px] font-semibold text-content">
                  Dry run — {formatNumber(preview.total_rows)} row
                  {preview.total_rows === 1 ? "" : "s"}
                </p>
                <Badge
                  className={
                    preview.can_commit
                      ? "border-transparent bg-success-soft text-success"
                      : "border-transparent bg-danger-soft text-danger"
                  }
                >
                  {preview.can_commit ? "Ready" : "Cannot import"}
                </Badge>
              </div>

              {preview.warnings.length > 0 ? (
                <ul className="space-y-1">
                  {preview.warnings.map((warning) => (
                    <li key={warning} className="flex items-start gap-2 text-[12.5px] text-warning">
                      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                      {warning}
                    </li>
                  ))}
                </ul>
              ) : null}

              <div>
                <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-subtle">
                  Fields
                </p>
                <dl className="space-y-1">
                  {Object.entries(preview.column_map.fields).map(([field, header]) => (
                    <div key={field} className="flex items-baseline gap-2 text-[12.5px]">
                      <dt className="w-40 shrink-0 font-mono text-muted">{field}</dt>
                      <dd className="min-w-0 truncate text-content">{header}</dd>
                    </div>
                  ))}
                </dl>
              </div>

              <div>
                <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-subtle">
                  Department ratings
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {Object.keys(preview.column_map.department_ratings).map((name) => (
                    <Badge
                      key={name}
                      className={
                        preview.unknown_departments.includes(name)
                          ? "border-transparent bg-warning-soft text-warning"
                          : "border-transparent bg-success-soft text-success"
                      }
                    >
                      {name}
                      {preview.unknown_departments.includes(name) ? " (new)" : ""}
                    </Badge>
                  ))}
                  {Object.keys(preview.column_map.department_ratings).length === 0 ? (
                    <span className="text-[12.5px] text-danger">
                      No department rating columns recognised.
                    </span>
                  ) : null}
                </div>
              </div>

              {preview.column_map.unmapped.length > 0 ? (
                <div>
                  <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-subtle">
                    Ignored columns
                  </p>
                  <p className="text-[12.5px] text-muted">
                    {preview.column_map.unmapped.join(" · ")}
                  </p>
                </div>
              ) : null}

              <div className="flex justify-end gap-2 border-t border-line pt-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setPreview(null);
                    setFile(null);
                    if (fileRef.current) fileRef.current.value = "";
                  }}
                  disabled={busy}
                >
                  Cancel
                </Button>
                <Button onClick={commit} loading={busy} disabled={!preview.can_commit}>
                  Confirm and import
                </Button>
              </div>
            </div>
          ) : null}

          {result ? (
            <div className="rounded-xl border border-success/30 bg-success-soft p-4">
              <p className="text-[13.5px] font-semibold text-content">
                Imported {formatNumber(result.created_count)} response
                {result.created_count === 1 ? "" : "s"}
              </p>
              <p className="mt-1 text-[12.5px] text-muted">
                {formatNumber(result.skipped_count)} already present ·{" "}
                {formatNumber(result.ratings_created)} department ratings
                {result.departments_created.length > 0
                  ? ` · new departments: ${result.departments_created.join(", ")}`
                  : ""}
              </p>
              {result.alerts.raised.length > 0 ? (
                <p className="mt-1 text-[12.5px] font-medium text-danger">
                  Alert raised: {result.alerts.raised.join(", ")}
                </p>
              ) : null}
              {result.alerts.resolved.length > 0 ? (
                <p className="mt-1 text-[12.5px] font-medium text-success">
                  Recovered: {result.alerts.resolved.join(", ")}
                </p>
              ) : null}
            </div>
          ) : null}

          <p className="text-[12px] leading-relaxed text-subtle">
            Re-importing the same export creates nothing — every response is keyed on a
            hash of its source row. Department averages are re-evaluated once for the
            whole batch, not once per row.
          </p>
        </CardBody>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader className="block">
          <CardTitle>Import history</CardTitle>
          <CardDescription>What has been ingested, and when.</CardDescription>
        </CardHeader>
        {history.loading && !history.data ? (
          <TableSkeleton rows={3} columns={2} />
        ) : history.data && history.data.length === 0 ? (
          <EmptyState
            icon={FileSpreadsheet}
            title="Nothing imported yet"
            description="The real Google Forms export has not been supplied."
          />
        ) : history.data ? (
          <ul className="divide-y divide-line">
            {history.data.map((batch) => {
              const hasDetail = Boolean(batch.errors?.length);
              const open = expanded.has(batch.id);
              return (
                <li key={batch.id} className="px-5 py-3">
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-center justify-between gap-2 text-left",
                      !hasDetail && "cursor-default",
                    )}
                    onClick={() => hasDetail && toggle(batch.id)}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      {hasDetail ? (
                        open ? (
                          <ChevronDown className="size-3.5 shrink-0 text-subtle" aria-hidden />
                        ) : (
                          <ChevronRight className="size-3.5 shrink-0 text-subtle" aria-hidden />
                        )
                      ) : null}
                      <span className="truncate font-mono text-[12px] text-content">
                        {batch.filename}
                      </span>
                    </span>
                    <Badge
                      className={
                        batch.status === "SUCCESS"
                          ? "border-transparent bg-success-soft text-success"
                          : batch.status === "PARTIAL"
                            ? "border-transparent bg-warning-soft text-warning"
                            : "border-transparent bg-danger-soft text-danger"
                      }
                    >
                      {batch.status}
                    </Badge>
                  </button>
                  <p className="mt-1 text-[12px] text-muted">
                    {formatNumber(batch.total_rows)} rows ·{" "}
                    {formatNumber(batch.created_count)} created ·{" "}
                    {formatNumber(batch.skipped_count)} skipped
                  </p>
                  <p className="text-[11.5px] text-subtle">
                    {formatDateTime(batch.created_at)}
                  </p>
                  {open && batch.errors ? (
                    <ul className="mt-2 space-y-1 rounded-lg border border-line bg-surface-2 p-2.5">
                      {batch.errors.map((row, index) => (
                        <li key={index} className="text-[11.5px] text-muted">
                          <span className="font-mono text-subtle">Row {row.row}</span>{" "}
                          {row.message}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </Card>
    </div>
  );
}

export default function FeedbackPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-card" />}>
      <FeedbackInner />
    </Suspense>
  );
}

/* ------------------------------------------------------------------ empties */

function pendingEmptyTitle(population: FeedbackPopulation | null): string {
  if (!population || population.converted === 0) return "No converted leads yet";
  if (population.eligible === 0 && population.awaiting_sync > 0) {
    return "No synced post-sale records yet";
  }
  if (population.eligible === 0 && population.waiting > 0) {
    return "Nothing is eligible yet";
  }
  return "Nobody is waiting on an ask";
}

function pendingEmptyDescription(population: FeedbackPopulation | null): string {
  if (!population || population.converted === 0) {
    return "Accounts appear here once a lead in your scope is marked Converted and the post-sale sync supplies its invoice date.";
  }
  if (population.eligible === 0 && population.awaiting_sync > 0) {
    return `${formatNumber(population.awaiting_sync)} won ${
      population.awaiting_sync === 1 ? "account is" : "accounts are"
    } waiting on the post-sale sync. Feedback is only asked once the sheet says the customer was invoiced.`;
  }
  if (population.eligible === 0 && population.waiting > 0) {
    return `${formatNumber(population.waiting)} won ${
      population.waiting === 1 ? "account is" : "accounts are"
    } still inside the 10-day wait after their invoice date.`;
  }
  return `All ${formatNumber(population.eligible)} eligible ${
    population.eligible === 1 ? "account has" : "accounts have"
  } answered. Nothing is outstanding.`;
}
