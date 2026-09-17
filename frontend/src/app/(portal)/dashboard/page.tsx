"use client";

import {
  AlertTriangle,
  Building2,
  CalendarClock,
  CheckCircle2,
  Gauge,
  Inbox,
  Percent,
  ReceiptText,
  Send,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { RangeToggle, type MonthRange } from "@/components/charts/RangeToggle";
import { HBarChart, TrendLine } from "@/components/charts/lazy";
import { ReportsTable } from "@/components/dashboard/ReportsTable";
import { StatSection, StatTile, StatTileSkeleton } from "@/components/dashboard/StatTile";
import { CoverageStrip } from "@/components/dashboard/CoverageStrip";
import { DashboardGreeting } from "@/components/dashboard/DashboardGreeting";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/Feedback";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import type { ReferenceStats } from "@/types/api";

/**
 * Why the eligible count is smaller than the won count — said on the tile.
 *
 * A bare "22" next to a "3" invites the wrong conclusion. These are three
 * genuinely different situations and only one of them is a problem:
 * nothing synced at all, synced but still inside the 10-day wait, or
 * everything already actionable.
 */
function referenceHint(references: ReferenceStats): string {
  if (references.converted_leads === 0) return "Nothing won yet";
  if (references.eligible_accounts > 0) {
    return `${formatNumber(references.eligible_accounts)} ready to ask today`;
  }
  if (references.waiting_period > 0) {
    return `${formatNumber(references.waiting_period)} before their reference date`;
  }
  return "None ready to ask yet";
}

export default function DashboardPage() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useAsync((signal) => api.dashboard(signal), []);
  const [trendRange, setTrendRange] = useState<MonthRange>(6);

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
      {data && data.alerts.length > 0 ? (
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
        <div className="grid grid-cols-2 gap-2.5 sm:gap-3.5 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <StatTileSkeleton key={index} index={index} />
          ))}
        </div>
      ) : (
        <>
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
          <StatSection title="Reference Tracking" href="/references">
            {/*
              The won book, and why only part of it is actionable.

              These always add up - eligible + waiting + awaiting sync equals
              converted - so a small "eligible" number is explained on the tile
              rather than reading as "we have no customers". Before this, the
              tile counted SAP accounts while the next module counted converted
              leads, and the two had no row in common.
            */}
            <StatTile
              index={0}
              label="Converted leads"
              value={data.references.converted_leads}
              hint={referenceHint(data.references)}
              icon={Building2}
              href="/leads?status=CONVERTED"
            />
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
                    ? `${data.references.references_taken} of ${data.references.eligible_accounts} gave a reference`
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
                    ? `${data.my_reference.references_taken} of ${data.my_reference.eligible_accounts} of your own accounts`
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
              hint="Ask again today"
              icon={CalendarClock}
              accent={data.references.follow_ups_due > 0 ? "warning" : "neutral"}
              href="/references?tab=follow-ups"
            />
          </StatSection>

          <StatSection title="Assigned Leads" href="/leads">
            <StatTile
              index={0}
              label="Open leads"
              value={data.leads.open}
              hint={`${formatNumber(data.leads.total)} in your scope`}
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
            <StatTile
              index={2}
              label="Converted"
              value={data.leads.converted}
              hint="Won — ready to ask for feedback"
              icon={CheckCircle2}
              accent={data.leads.converted > 0 ? "success" : "neutral"}
              href="/leads?status=CONVERTED"
            />
          </StatSection>

          {/* Always shown, even without rights to the analysis below it: the
              pending queue is the caller's OWN outstanding asks, and hiding it
              is what would break the trail from a converted lead. */}
          <StatSection title="Customer feedback" href="/feedback?tab=pending">
            <StatTile
              index={0}
              label="Feedback pending"
              value={data.feedback_pending}
              hint="Converted work nobody has asked about"
              icon={Send}
              accent={data.feedback_pending > 0 ? "warning" : "neutral"}
              href="/feedback?tab=pending"
            />
            {data.feedback ? (
              <>
                <StatTile
                  index={1}
                  label="Average rating"
                  value={
                    data.feedback.average_rating === null
                      ? "—"
                      : `${data.feedback.average_rating}/${data.feedback.rating_scale_max}`
                  }
                  hint={`From ${formatNumber(data.feedback.total_responses)} responses`}
                  icon={Gauge}
                  href="/feedback"
                />
                <StatTile
                  index={2}
                  label="Needs attention"
                  value={data.feedback.departments_below_threshold}
                  hint="Departments below threshold"
                  icon={AlertTriangle}
                  accent={data.feedback.departments_below_threshold > 0 ? "danger" : "neutral"}
                  href="/feedback?tab=analysis"
                />
              </>
            ) : null}
          </StatSection>

          {/* ------------------------------------------------- charts */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card className="stagger" style={{ "--index": 0 } as React.CSSProperties}>
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

            <Card className="stagger" style={{ "--index": 1 } as React.CSSProperties}>
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
                <Card className="stagger" style={{ "--index": 2 } as React.CSSProperties}>
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

                <Card className="stagger" style={{ "--index": 3 } as React.CSSProperties}>
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
            <div className="mt-4">
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
        </>
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
