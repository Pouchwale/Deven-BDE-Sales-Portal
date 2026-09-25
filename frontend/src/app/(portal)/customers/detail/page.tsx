"use client";

import { ArrowLeft, Mail, MessageSquareHeart, Phone, ReceiptText } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { CustomerTimeline } from "@/components/customers/CustomerTimeline";
import { SendRequestButton } from "@/components/customers/SendRequestDialog";
import { PageHeader } from "@/components/layout/PageHeader";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, ReferenceStatusBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/Feedback";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-[12.5px] text-muted">{label}</dt>
      <dd className="min-w-0 text-right text-[13px] text-content">{children}</dd>
    </div>
  );
}

/**
 * /customers/detail?id=<uuid>. A query string rather than /customers/<uuid>
 * so the portal can be a static export served by the backend (a path segment
 * would need every id known at build time). The backend redirects the old
 * /customers/<uuid> links here.
 */
export default function CustomerDetailRoute() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-card" />}>
      <CustomerDetailPage />
    </Suspense>
  );
}

function CustomerDetailPage() {
  const id = useSearchParams().get("id") ?? "";

  const [nonce, setNonce] = useState(0);
  const { data, error, loading, reload } = useAsync(
    (signal) => api.customers.get(id, signal),
    [id, nonce],
  );
  const refresh = () => setNonce((value) => value + 1);

  if (error) {
    return (
      <>
        <BackLink />
        <Card>
          <ErrorState error={error} onRetry={reload} />
        </Card>
      </>
    );
  }

  if (loading || !data) {
    return (
      <>
        <BackLink />
        <Skeleton className="h-8 w-72" />
        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Skeleton className="h-64 rounded-card" />
          <Skeleton className="h-64 rounded-card lg:col-span-2" />
        </div>
      </>
    );
  }

  return (
    <>
      <BackLink />

      <PageHeader
        title={data.name}
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-mono text-[12.5px]">{data.sap_code}</span>
            <span aria-hidden>·</span>
            <span>
              {formatNumber(data.invoice_count)}{" "}
              {data.invoice_count === 1 ? "invoice" : "invoices"}
            </span>
            <span aria-hidden>·</span>
            <span>
              {formatNumber(data.invoice_lines.length)}{" "}
              {data.invoice_lines.length === 1 ? "line" : "lines"}
            </span>
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="border-transparent bg-success-soft text-success">
              Completed customer
            </Badge>
            <ReferenceStatusBadge status={data.reference_status} />
            <SendRequestButton
              customerId={data.id}
              customerName={data.name}
              onSent={refresh}
              variant="primary"
              label="Send request"
            />
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* ------------------------------------------------- details */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="block">
              <CardTitle>Account</CardTitle>
            </CardHeader>
            <CardBody className="pt-1">
              <dl className="divide-y divide-line">
                <DetailRow label="Owner">
                  {data.owner_name ? (
                    <span className="flex items-center justify-end gap-2">
                      <Avatar name={data.owner_name} size="xs" />
                      {data.owner_name}
                    </span>
                  ) : (
                    <Badge className="border-line bg-surface-2 text-subtle">Unassigned</Badge>
                  )}
                </DetailRow>
                <DetailRow label="SAP salesperson">
                  {data.sap_sales_person ?? "—"}
                </DetailRow>
                <DetailRow label="Mobile">
                  {data.mobile ? (
                    <a
                      href={`tel:${data.mobile.replace(/\s+/g, "")}`}
                      className="inline-flex items-center gap-1.5 hover:underline"
                    >
                      <Phone className="size-3.5 text-subtle" aria-hidden />
                      <span className="font-mono text-[12.5px]">{data.mobile}</span>
                    </a>
                  ) : (
                    "—"
                  )}
                </DetailRow>
                <DetailRow label="Email">
                  {data.email ? (
                    <a
                      href={`mailto:${data.email}`}
                      className="inline-flex items-center gap-1.5 break-all hover:underline"
                    >
                      <Mail className="size-3.5 shrink-0 text-subtle" aria-hidden />
                      <span className="text-[12.5px]">{data.email}</span>
                    </a>
                  ) : (
                    "—"
                  )}
                </DetailRow>
                <DetailRow label="First invoice">{formatDate(data.first_invoice_date)}</DetailRow>
                <DetailRow label="Last invoice">{formatDate(data.last_invoice_date)}</DetailRow>
              </dl>
            </CardBody>
          </Card>

          <Card>
            <CardHeader className="block">
              <CardTitle>Reference</CardTitle>
              <CardDescription>What this customer said when asked.</CardDescription>
            </CardHeader>
            <CardBody className="pt-1">
              <dl className="divide-y divide-line">
                <DetailRow label="Status">
                  <ReferenceStatusBadge status={data.reference_status} />
                </DetailRow>
                <DetailRow label="Last asked">
                  {formatDate(data.last_reference_asked_at)}
                </DetailRow>
                <DetailRow label="Ask again on">
                  {formatDate(data.next_reference_date)}
                </DetailRow>
              </dl>
            </CardBody>
          </Card>
        </div>

        {/* --------------------------------- timeline and feedback */}
        <div className="space-y-4 lg:col-span-2">
          <CustomerTimeline customerId={data.id} />

          <Card className="overflow-hidden">
            <CardHeader className="block">
              <CardTitle>
                Customer feedback
                {data.feedback_average !== null ? (
                  <span className="ml-2 text-[13px] font-normal text-muted">
                    {data.feedback_average.toFixed(2)} average over{" "}
                    {formatNumber(data.feedback_count)} response
                    {data.feedback_count === 1 ? "" : "s"}
                  </span>
                ) : null}
              </CardTitle>
              <CardDescription>
                Responses matched to this account by mobile, email or company name.
              </CardDescription>
            </CardHeader>

            {data.feedback.length === 0 ? (
              <EmptyState
                icon={MessageSquareHeart}
                title="No feedback from this customer yet"
                description="Ask for feedback from the timeline above. The reply appears here once the customer fills the form in."
              />
            ) : (
              <ul className="divide-y divide-line">
                {data.feedback.map((response) => (
                  <li key={response.id} className="px-5 py-3.5">
                    <div className="flex flex-wrap items-center gap-2">
                      {response.overall_rating !== null ? (
                        <Badge className="border-transparent bg-success-soft text-success">
                          Overall {response.overall_rating.toFixed(1)}
                        </Badge>
                      ) : null}
                      {response.would_recommend ? (
                        <Badge className="border-line bg-surface-2 text-muted">
                          Recommends: {response.would_recommend}
                        </Badge>
                      ) : null}
                      <span className="text-[11.5px] text-subtle">
                        {formatDate(response.submitted_at_source)}
                        {response.handled_by_name ? ` · ${response.handled_by_name}` : ""}
                      </span>
                    </div>

                    {response.overall_comments ? (
                      <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
                        &ldquo;{response.overall_comments}&rdquo;
                      </p>
                    ) : null}

                    {response.departments.length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {response.departments.map((department) => (
                          <Badge
                            key={department.name}
                            className="border-line bg-surface-2 text-muted"
                            title={department.comments ?? undefined}
                          >
                            {department.name}{" "}
                            {department.rating !== null ? department.rating.toFixed(1) : "—"}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>

        {/* ------------------------------------------ invoice lines */}
        <Card className="overflow-hidden">
          <CardHeader className="block">
            <CardTitle>Invoice lines</CardTitle>
            <CardDescription>
              Straight from the SAP export. One invoice can carry several lines — the
              invoice count above counts invoices, not lines.
            </CardDescription>
          </CardHeader>

          {data.invoice_lines.length === 0 ? (
            <EmptyState
              icon={ReceiptText}
              title="No invoice lines"
              description="This account has not been billed in the imported data."
            />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>Invoice</TH>
                    <TH>Date</TH>
                    <TH>FGPO</TH>
                    <TH>Item</TH>
                    <TH>Salesperson</TH>
                  </tr>
                </THead>
                <TBody>
                  {data.invoice_lines.map((line) => (
                    <TR key={line.id}>
                      <TD>
                        <span className="font-mono text-[12.5px] text-content">
                          #{line.invoice_no}
                        </span>
                      </TD>
                      <TD className="whitespace-nowrap text-muted">
                        {formatDate(line.invoice_date)}
                      </TD>
                      <TD>
                        <span className="font-mono text-[12px] text-subtle">
                          {line.fgpo_code ?? "—"}
                        </span>
                      </TD>
                      <TD className="max-w-md">
                        <span className="block text-[13px] text-content">
                          {line.item_description ?? "—"}
                        </span>
                      </TD>
                      <TD className="whitespace-nowrap text-muted">
                        {line.sales_person ?? "—"}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
          </Card>
        </div>
      </div>
    </>
  );
}

function BackLink() {
  return (
    <Link href="/customers" className="mb-4 inline-block">
      <Button variant="ghost" size="sm">
        <ArrowLeft className="size-3.5" aria-hidden />
        All customers
      </Button>
    </Link>
  );
}
