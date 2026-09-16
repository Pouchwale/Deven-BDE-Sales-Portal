"use client";

import { Building2, Search, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { SapImportButton } from "@/components/admin/SapImportButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { SendRequestButton } from "@/components/customers/SendRequestDialog";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, ReferenceStatusBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/Feedback";
import { Input, Select } from "@/components/ui/Form";
import { Pagination } from "@/components/ui/Pagination";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { REFERENCE_STATUS_LABELS } from "@/lib/roles";
import { useAsync, useDebounced } from "@/lib/useAsync";

const PAGE_SIZE = 25;

export default function CustomersPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const debouncedSearch = useDebounced(search, 300);

  // Changing a filter resets to page 1 in the handler rather than in an
  // effect: leaving the page number alone would ask for page 4 of a two-page
  // result and show an empty table.
  function changeSearch(value: string) {
    setSearch(value);
    setPage(1);
  }

  function changeStatus(value: string) {
    setStatus(value);
    setPage(1);
  }

  function clearFilters() {
    setSearch("");
    setStatus("");
    setPage(1);
  }

  const stats = useAsync((signal) => api.customers.stats(signal), []);
  const listing = useAsync(
    (signal) =>
      api.customers.list(
        {
          search: debouncedSearch || undefined,
          reference_status: status || undefined,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
    [debouncedSearch, status, page],
  );

  const filtered = Boolean(debouncedSearch || status);

  return (
    <>
      <PageHeader
        title="Converted customers"
        description={
          <>
            Accounts imported from SAP, scoped to your reporting chain. Values are stored
            exactly as SAP supplied them — nothing is reformatted on the way in. These
            are completed deals; what is still open on each is the feedback and the
            reference.
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2.5">
          <SapImportButton
            onImported={() => {
              stats.reload();
              listing.reload();
            }}
          />
          {stats.data ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge className="border-line bg-surface-2 text-muted">
                {formatNumber(stats.data.total)} customers
              </Badge>
              <Badge className="border-line bg-surface-2 text-muted">
                {formatNumber(stats.data.invoices)} invoices
              </Badge>
              <Badge className="border-line bg-surface-2 text-muted">
                {formatNumber(stats.data.invoice_lines)} lines
              </Badge>
              <Badge
                className={
                  stats.data.with_feedback > 0
                    ? "border-transparent bg-success-soft text-success"
                    : "border-line bg-surface-2 text-subtle"
                }
              >
                {formatNumber(stats.data.with_feedback)} heard from
              </Badge>
            </div>
          ) : null}
          </div>
        }
      />

      <Card className="overflow-hidden">
        {/* Filters in one row above the table. */}
        <div className="flex flex-wrap items-center gap-2.5 border-b border-line p-3.5">
          <div className="relative min-w-52 flex-1">
            <Input
              type="search"
              placeholder="Search name, SAP code, email or mobile…"
              icon={<Search className="size-4" />}
              value={search}
              onChange={(event) => changeSearch(event.target.value)}
              aria-label="Search customers"
              className={search ? "pr-9" : undefined}
            />
            {search ? (
              <button
                type="button"
                onClick={() => changeSearch("")}
                aria-label="Clear search"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-subtle transition-colors hover:text-content"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            ) : null}
          </div>

          <Select
            value={status}
            onChange={(event) => changeStatus(event.target.value)}
            aria-label="Filter by reference status"
            className="w-auto min-w-44"
          >
            <option value="">All reference statuses</option>
            {Object.entries(REFERENCE_STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>

          {filtered ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={clearFilters}
            >
              Clear
            </Button>
          ) : null}
        </div>

        {listing.error ? (
          <ErrorState error={listing.error} onRetry={listing.reload} />
        ) : listing.loading && !listing.data ? (
          <TableSkeleton rows={8} columns={9} />
        ) : listing.data && listing.data.items.length === 0 ? (
          <EmptyState
            icon={Building2}
            title={filtered ? "No customers match those filters" : "No customers in your scope"}
            description={
              filtered
                ? "Try a different search term, or clear the filters."
                : "You see the accounts you own, plus everyone below you in the reporting chain."
            }
            action={
              filtered ? (
                <Button variant="secondary" size="sm" onClick={clearFilters}>
                  Clear filters
                </Button>
              ) : null
            }
          />
        ) : listing.data ? (
          <>
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>Customer</TH>
                    <TH>SAP code</TH>
                    <TH>Owner</TH>
                    <TH>Contact</TH>
                    <TH align="right">Invoices</TH>
                    <TH align="right">Last invoice</TH>
                    <TH>Reference</TH>
                    <TH>Feedback</TH>
                    <TH align="right">Request</TH>
                  </tr>
                </THead>
                <TBody>
                  {listing.data.items.map((customer) => (
                    <TR key={customer.id}>
                      <TD>
                        <Link
                          href={`/customers/${customer.id}`}
                          className="font-medium text-content hover:underline"
                        >
                          {customer.name}
                        </Link>
                      </TD>
                      <TD data-label="SAP code">
                        <span className="font-mono text-[12.5px] text-muted">
                          {customer.sap_code}
                        </span>
                      </TD>
                      <TD data-label="Owner">
                        {customer.owner_name ? (
                          <span className="flex items-center gap-2">
                            <Avatar name={customer.owner_name} size="xs" />
                            <span className="text-[13px] text-muted">
                              {customer.owner_name}
                            </span>
                          </span>
                        ) : (
                          <Badge className="border-line bg-surface-2 text-subtle">
                            Unassigned
                          </Badge>
                        )}
                      </TD>
                      <TD data-label="Contact">
                        <span className="block text-[12.5px] text-muted">
                          {customer.mobile ?? "—"}
                        </span>
                        <span className="block truncate text-[12px] text-subtle">
                          {customer.email ?? "—"}
                        </span>
                      </TD>
                      <TD data-label="Invoices" align="right">{formatNumber(customer.invoice_count)}</TD>
                      <TD data-label="Last invoice" align="right" className="whitespace-nowrap text-muted">
                        {formatDate(customer.last_invoice_date)}
                      </TD>
                      <TD data-label="Reference">
                        <ReferenceStatusBadge status={customer.reference_status} />
                      </TD>
                      <TD data-label="Feedback">
                        {customer.feedback_count > 0 ? (
                          <Badge className="border-transparent bg-success-soft text-success">
                            {customer.feedback_average?.toFixed(1) ?? "—"} ·{" "}
                            {formatNumber(customer.feedback_count)}
                          </Badge>
                        ) : (
                          <Badge className="border-line bg-surface-2 text-subtle">
                            Awaiting
                          </Badge>
                        )}
                      </TD>
                      <TD data-actions align="right">
                        {/* Ask from the list, without opening the account. */}
                        <SendRequestButton
                          customerId={customer.id}
                          customerName={customer.name}
                          onSent={listing.reload}
                          variant="subtle"
                          label="Ask"
                        />
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>

            <Pagination
              page={listing.data.page}
              pageSize={listing.data.page_size}
              total={listing.data.total}
              onPageChange={setPage}
              noun="customers"
            />
          </>
        ) : null}
      </Card>
    </>
  );
}
