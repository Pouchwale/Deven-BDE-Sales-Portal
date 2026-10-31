"use client";

import {
  AlertTriangle,
  Building2,
  CalendarClock,
  CheckCircle2,
  Inbox,
  ListTodo,
  Percent,
  ReceiptText,
  Target,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { RangeToggle, type MonthRange } from "@/components/charts/RangeToggle";
import { HBarChart, TrendLine } from "@/components/charts/lazy";
import { ReportsTable } from "@/components/dashboard/ReportsTable";
import {
  StatSection,
  StatSectionSkeleton,
  StatTile,
} from "@/components/dashboard/StatTile";
import { CoverageStrip } from "@/components/dashboard/CoverageStrip";
import { DashboardGreeting } from "@/components/dashboard/DashboardGreeting";
import { PageHeader } from "@/components/layout/PageHeader";
import { FEEDBACK_MODULE_OPEN } from "@/lib/features";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { ErrorState, Skeleton } from "@/components/ui/Feedback";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { useEnterStagger } from "@/lib/motion";
import { useAsync } from "@/lib/useAsync";

export default function DashboardPage() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useAsync((signal) => api.dashboard(signal), []);
  const [trendRange, setTrendRange] = useState<MonthRange>(6);
  const bodyRef = useRef<HTMLDivElement>(null);
  // One entrance, when the numbers first land - not on every background
  // refresh, and never for anybody who has asked for reduced motion.
  useEnterStagger(bodyRef, [Boolean(data)]);

  if (error) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <Card>
          <ErrorState error={error} onRetry={reload} />
        </Card>
      </>
    );
  }

  return (
    <>
      <DashboardGreeting
        name={user?.name}
        honorific={user?.honorific ?? null}
        data={data}
        loading={loading}
      />

      {/* ------------------------------------------- attention banner */}
      {data && data.alerts.length > 0 && FEEDBACK_MODULE_OPEN ? (
        <Link href="/feedback" className="mb-5 block">
          <div className="flex items-start gap-3 rounded-card border border-warning/30 bg-warning-soft px-4 py-3 transition-colors hover:border-warning/50">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            <div className="min-w-0">
              <p className="text-[13.5px] font-semibold text-content">
                {data.alerts.length === 1
                  ? "A department needs attention"
                  : `${data.alerts.length} departments need attention`}
              </p>
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">
                {data.alerts
                  .map(
                    (alert) =>
                      `${alert.department_name} (${alert.average_rating}/${
                        data.feedback?.rating_scale_max ?? 5
                      } over ${alert.response_count} ratings)`,
                  )
                  .join(" · ")}
                {" — threshold is "}
                {data.alerts[0]?.threshold}.
              </p>
            </div>
          </div>
        </Link>
      ) : null}

      {loading || !data ? (
        <div aria-busy="true" aria-label="Loading your numbers">
          <Skeleton className="mb-4 h-[104px] w-full rounded-card" />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {Array.from({ length: 2 }).map((_, index) => (
              <StatSectionSkeleton key={index} />
            ))}
          </div>
        </div>
      ) : (
        <div ref={bodyRef}>
          {/* Where the book stands as proportions, not counts. A tile says
              "how many"; this says "out of how many", which is the question a
              bare number always leaves open. */}
          <CoverageStrip data={data} />

          {/* -------------------------------------------------------------
              Three per module: where the book stands, and what is waiting on
              somebody. Derived numbers ride in the hint rather than taking a
              tile of their own — a dashboard you have to read twice is worse
              than one that shows less.
             ------------------------------------------------------------- */}
          <div className="mb-4 grid grid-cols-1 items-stretch gap-4 lg:grid-cols-2">
          <StatSection title="Reference Tracking" href="/references" icon={Users}>
            {/* Only accounts that can be asked TODAY. Won accounts still
                inside their waiting period are left out on purpose (agreed
                2026-10-13): counting them next to "ready" numbers read as
                missing work. They appear here on their reference date. */}
            <StatTile
              index={0}
              label="Ready to ask"
              value={data.references.not_asked + data.references.references_pending}
              hint={
                data.references.eligible_accounts > 0
                  ? `${formatNumber(data.references.not_asked)} not asked · ${formatNumber(data.references.references_pending)} later`
                  : "None ready to ask yet"
              }
              icon={Building2}
              href="/references"
            />
            {/* A manager's own list, separate from the team's: the same
                accounts as the My tasks tab on Reference Tracking. */}
            {data.shape === "MANAGER" && data.my_reference ? (
              <StatTile
                index={1}
                label="My tasks"
                value={data.my_reference.open_tasks}
                hint={
                  data.my_reference.open_tasks > 0
                    ? "Your own accounts still to ask — open the list"
                    : "Nothing on your own list"
                }
                icon={ListTodo}
                accent={data.my_reference.open_tasks > 0 ? "warning" : "neutral"}
                href="/references?tab=mine"
              />
            ) : null}
            {/*
              Done over total, not a bare number.

              The numerator is requests COMPLETED - gave one, or said they had
              none - because both mean the account has been worked and should
              not be chased again. References actually received is the smaller
              number underneath, kept separate on purpose: reporting the two as
              one would claim referrals the company never got.

              The denominator is accounts that are ELIGIBLE, ten days past the
              invoice date the post-sale sync supplied. An account nobody is
              allowed to ask yet is not an account somebody failed to ask.
            */}
            <StatTile
              index={1}
              label="References completed"
              value={`${data.references.requests_completed} / ${data.references.eligible_accounts}`}
              hint={
                data.references.eligible_accounts > 0
                  ? `${data.references.completion_rate}% of eligible accounts · ${data.references.references_taken} gave one`
                  : "No accounts are eligible yet"
              }
              icon={CheckCircle2}
              accent={data.references.requests_completed > 0 ? "success" : "neutral"}
              href="/references?tab=references"
            />
            {/* The agreed score: the reference gap left to close, as a
                negative percentage of the eligible book. 10 accounts with 7
                references reads -30%; nothing left to ask reads 0%.

                Admin and Super Admin are not scored, so they see no score
                tile. A manager sees the team's score and, separately, their
                own on the accounts assigned to them personally. */}
            {data.shape !== "ADMIN" ? (
              <StatTile
                index={2}
                label={data.shape === "MANAGER" ? "Team reference score" : "Reference score"}
                value={`${data.references.reference_score}%`}
                hint={
                  data.references.eligible_accounts > 0
                    ? `${data.references.requests_completed} of ${data.references.eligible_accounts} taken or declined`
                    : "No accounts are eligible yet"
                }
                icon={Percent}
                accent={scoreAccent(
                  data.references.reference_score,
                  data.references.eligible_accounts,
                )}
                href="/references"
              />
            ) : null}
            {data.shape === "MANAGER" && data.my_reference ? (
              <StatTile
                index={3}
                label="My reference score"
                value={`${data.my_reference.reference_score}%`}
                hint={
                  data.my_reference.eligible_accounts > 0
                    ? `${data.my_reference.references_taken} of your ${data.my_reference.eligible_accounts} accounts taken or declined`
                    : "None of your own accounts are eligible yet"
                }
                icon={Percent}
                accent={scoreAccent(
                  data.my_reference.reference_score,
                  data.my_reference.eligible_accounts,
                )}
                href="/references"
              />
            ) : null}
            <StatTile
              index={4}
              label="Follow-ups due"
              value={data.references.follow_ups_due}
              hint="Said “later” — ask again on the date set"
              icon={CalendarClock}
              accent={data.references.follow_ups_due > 0 ? "warning" : "neutral"}
              href="/references?tab=follow-ups"
            />
          </StatSection>

          <StatSection title="Assigned Leads" href="/leads" icon={Target}>
            <StatTile
              index={0}
              label="Open leads"
              value={data.leads.open}
              hint="Still being worked"
              icon={Inbox}
              href="/leads?open_only=true"
            />
            {/* Named differently from Reference Tracking's tile on purpose:
                two KPIs reading "Follow-ups due" on one screen is a question,
                not an answer. */}
            <StatTile
              index={1}
              label="Lead follow-ups"
              value={data.leads.follow_ups_due}
              hint="Today or earlier"
              icon={CalendarClock}
              accent={data.leads.follow_ups_due > 0 ? "warning" : "neutral"}
              href="/leads?open_only=true"
            />
            {/* No "Converted" tile: the dashboard shows work still to do
                (agreed 2026-10-13). Won leads are on the Leads page. */}
          </StatSection>

          </div>

          {/* ------------------------------------------------- charts */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card data-enter>
              <CardHeader className="block">
                <CardTitle>Reference status of eligible accounts</CardTitle>
                <CardDescription>
                  Where each account that may be asked sits in the cycle.
                </CardDescription>
              </CardHeader>
              <CardBody>
                <HBarChart
                  data={[
                    { label: "Not asked", value: data.references.not_asked },
                    { label: "Taken", value: data.references.references_taken },
                    {
                      label: "Pending",
                      value: data.references.references_pending,
                    },
                    {
                      label: "Declined",
                      value: data.references.references_declined,
                    },
                  ]}
                  emptyMessage={
                    data.references.converted_leads > 0
                      ? "No accounts are eligible to ask yet."
                      : "No converted leads in your scope."
                  }
                />
              </CardBody>
            </Card>

            <Card data-enter>
              <CardHeader className="block">
                <CardTitle>Lead pipeline</CardTitle>
                <CardDescription>Open and closed leads by stage.</CardDescription>
              </CardHeader>
              <CardBody>
                <HBarChart
                  // In pipeline order, so the chart reads as the journey it
                  // is rather than an alphabetical list of states.
                  data={[
                    { label: "New", value: data.leads.new },
                    { label: "Not contacted", value: data.leads.not_contacted },
                    { label: "Contacted", value: data.leads.contacted },
                    { label: "Nurturing", value: data.leads.nurturing },
                    { label: "Pre-qualified", value: data.leads.pre_qualified },
                    { label: "Qualified", value: data.leads.qualified },
                    { label: "Converted", value: data.leads.converted },
                    { label: "Lost", value: data.leads.lost },
                    { label: "Junk", value: data.leads.junk },
                  ]}
                  emptyMessage="No leads assigned yet."
                />
                {data.leads.total === 0 ? (
                  <p className="mt-4 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[12.5px] leading-relaxed text-muted">
                    Nothing is seeded. Leads appear here once a manager assigns one.
                  </p>
                ) : null}
              </CardBody>
            </Card>

            {data.feedback ? (
              <>
                <Card data-enter>
                  <CardHeader className="block">
                    <CardTitle>
                      Department average rating (out of {data.feedback.rating_scale_max})
                    </CardTitle>
                    <CardDescription>
                      Rolling window. A department below the dashed line is flagged.
                    </CardDescription>
                  </CardHeader>
                  <CardBody>
                    <HBarChart
                      data={data.department_ratings.map((row) => ({
                        label: row.department_name,
                        value: row.average_rating ?? 0,
                        flagged: row.below_threshold,
                        hint: `${row.response_count} ratings`,
                      }))}
                      max={data.feedback.rating_scale_max}
                      threshold={data.alerts[0]?.threshold ?? 3}
                      thresholdLabel={`Threshold ${data.alerts[0]?.threshold ?? 3}`}
                      valueFormatter={(value) => (value === 0 ? "—" : value.toFixed(2))}
                      emptyMessage="No departments configured."
                    />
                  </CardBody>
                </Card>

                <Card data-enter>
                  <CardHeader>
                    <div>
                      <CardTitle>Feedback responses per month</CardTitle>
                      <CardDescription>Volume over time.</CardDescription>
                    </div>
                    <RangeToggle value={trendRange} onChange={setTrendRange} />
                  </CardHeader>
                  <CardBody>
                    <TrendLine
                      data={data.monthly_feedback.slice(-trendRange).map((row) => ({
                        label: row.month,
                        value: row.responses,
                      }))}
                    />
                  </CardBody>
                </Card>
              </>
            ) : null}
          </div>

          {/* ------------------------------------------ people table */}
          {data.shape !== "PERSONAL" ? (
            <div className="mt-4" data-enter>
              <ReportsTable
                reports={data.reports}
                title={data.shape === "ADMIN" ? "People" : "Your team"}
                description={
                  data.shape === "ADMIN"
                    ? "Everyone in the organisation, least recent activity first."
                    : "Everyone below you in the reporting chain, least recent activity first."
                }
              />
            </div>
          ) : null}
        </div>
      )}

      <div className="mt-4 flex items-center gap-2 text-[11.5px] text-subtle">
        <ReceiptText className="size-3.5" aria-hidden />
        Nothing here is seeded — every number comes from work done in the portal, counted once and
        shown the same way on every screen.
      </div>
    </>
  );
}

/** Green once every eligible account has given a reference, amber when more
 *  than half still owe one. */
function scoreAccent(score: number, eligible: number): "success" | "warning" | "neutral" {
  if (score === 0 && eligible > 0) return "success";
  if (score <= -50) return "warning";
  return "neutral";
}
