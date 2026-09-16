"use client";

import { Users } from "lucide-react";
import Link from "next/link";

import { Avatar } from "@/components/ui/Avatar";
import { RoleBadge } from "@/components/ui/Badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { cn } from "@/lib/cn";
import { formatNumber, formatRelative } from "@/lib/format";
import type { ReportRow } from "@/types/api";

/**
 * "How is each of my people doing" — the question the reporting chain exists
 * to answer, so this is the manager dashboard's centrepiece.
 *
 * Every column is about LEADS, because that is the work these people do. The
 * columns this replaced — Customers, Invoices, Last invoice, and a per-person
 * feedback rating — all came from the SAP book, which had no link to any lead.
 * Somebody carrying forty open leads showed "0 customers" and read as idle,
 * and a per-person feedback average was borrowed from a department score they
 * did not own.
 *
 * The server sorts stalest first: the person with nothing recent is the one
 * worth looking at, and burying them at the bottom of an alphabetical list is
 * how they stay buried.
 */
export function ReportsTable({
  reports,
  title = "Your team",
  description,
}: {
  reports: ReportRow[];
  title?: string;
  description?: string;
}) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="block">
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          {description ?? "Sorted by staleness — least recent activity first."}
        </CardDescription>
      </CardHeader>

      {reports.length === 0 ? (
        <EmptyState
          icon={Users}
          title="Nobody reports to you"
          description="When people are placed under you in the reporting chain, their activity appears here."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <tr>
                <TH>Person</TH>
                <TH>Role</TH>
                <TH>Team</TH>
                <TH align="right">Open leads</TH>
                <TH align="right">Converted</TH>
                <TH align="right">References</TH>
                <TH align="right">Score</TH>
                <TH align="right">Follow-ups due</TH>
                <TH align="right">Last activity</TH>
              </tr>
            </THead>
            <TBody>
              {reports.map((row) => (
                <TR key={row.user_id}>
                  <TD>
                    <Link
                      href={`/team?focus=${row.user_id}`}
                      className="flex items-center gap-2.5 hover:underline"
                    >
                      <Avatar name={row.name} size="xs" />
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-content">
                          {row.name}
                        </span>
                        {row.title ? (
                          <span className="block truncate text-[12px] text-subtle">
                            {row.title}
                          </span>
                        ) : null}
                      </span>
                    </Link>
                  </TD>
                  <TD>
                    <RoleBadge role={row.role} />
                  </TD>
                  <TD className="text-muted">{row.team_name ?? "—"}</TD>
                  <TD
                    data-label="Open leads"
                    align="right"
                    className={cn(row.open_leads === 0 && "text-subtle")}
                  >
                    {formatNumber(row.open_leads)}
                  </TD>
                  <TD data-label="Converted" align="right">
                    {row.converted > 0 ? (
                      <span className="font-medium text-success">
                        {formatNumber(row.converted)}
                      </span>
                    ) : (
                      <span className="text-subtle">0</span>
                    )}
                  </TD>
                  <TD
                    data-label="References"
                    align="right"
                    className={cn(row.references_taken === 0 && "text-subtle")}
                  >
                    {formatNumber(row.references_taken)}
                  </TD>
                  {/* -(eligible accounts still owing a reference) %, so 7 of
                      10 asked reads -30%. Nothing eligible reads "—" rather
                      than 0%, which would look like a perfect score. */}
                  <TD
                    data-label="Score"
                    align="right"
                    title={
                      row.eligible_accounts > 0
                        ? `${row.references_on_eligible} of ${row.eligible_accounts} eligible accounts gave a reference`
                        : "No eligible accounts yet"
                    }
                  >
                    {row.eligible_accounts === 0 ? (
                      <span className="text-subtle">—</span>
                    ) : (
                      <span
                        className={cn(
                          "font-medium",
                          row.reference_score === 0
                            ? "text-success"
                            : row.reference_score <= -50
                              ? "text-warning"
                              : "text-content",
                        )}
                      >
                        {row.reference_score}%
                      </span>
                    )}
                  </TD>
                  <TD data-label="Follow-ups due" align="right">
                    {row.followups_due > 0 ? (
                      <span className="font-medium text-warning">
                        {formatNumber(row.followups_due)}
                      </span>
                    ) : (
                      <span className="text-subtle">0</span>
                    )}
                  </TD>
                  {/* Their own last touch on a lead — not when SAP last
                      invoiced something, which measured SAP and not them. */}
                  <TD
                    data-label="Last activity"
                    align="right"
                    className="whitespace-nowrap text-muted"
                  >
                    {row.last_activity_at ? (
                      formatRelative(row.last_activity_at)
                    ) : (
                      <span className="text-subtle">No activity</span>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </Card>
  );
}
