"use client";

import { Network, Search, Users } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { OrgTree } from "@/components/team/OrgTree";
import { Avatar } from "@/components/ui/Avatar";
import { ActiveBadge, RoleBadge } from "@/components/ui/Badge";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, Skeleton, TableSkeleton } from "@/components/ui/Feedback";
import { Input, Switch } from "@/components/ui/Form";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { isAdmin } from "@/lib/roles";
import { useAsync, useDebounced } from "@/lib/useAsync";

function TeamPageInner() {
  const { user } = useAuth();
  const searchParams = useSearchParams();
  const focusId = searchParams.get("focus");

  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search, 300);

  // One switch, both halves of the page. Deactivated people are out by
  // default: somebody who has been removed from the portal should not still
  // be counted as a member of the team, in the tree or in the roster.
  const [includeInactive, setIncludeInactive] = useState(false);

  const chart = useAsync(
    (signal) => api.users.orgChart({ include_inactive: includeInactive }, signal),
    [includeInactive],
  );
  const people = useAsync(
    (signal) =>
      api.users.list(
        {
          search: debouncedSearch || undefined,
          include_inactive: includeInactive,
          page_size: 200,
        },
        signal,
      ),
    [debouncedSearch, includeInactive],
  );

  const inactiveShown = people.data?.items.filter((p) => !p.is_active).length ?? 0;

  return (
    <>
      <PageHeader
        title="Team"
        description={
          isAdmin(user?.role)
            ? "Everyone in the organisation. Visibility follows the reporting chain — this view is unrestricted because you are an administrator."
            : "You and everyone below you in the reporting chain, at any depth. Peers and anyone above you are deliberately not shown."
        }
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        {/* --------------------------------------------- org chart */}
        <div className="xl:col-span-3">
          <Card className="overflow-hidden">
            <CardHeader className="block">
              <CardTitle>Reporting chain</CardTitle>
              <CardDescription>
                Click a caret to collapse a branch. The chain is three levels deep in
                places, and nothing here assumes two.
              </CardDescription>
            </CardHeader>
            <CardBody>
              {chart.error ? (
                <ErrorState error={chart.error} onRetry={chart.reload} />
              ) : chart.loading || !chart.data ? (
                <div className="space-y-2">
                  {Array.from({ length: 6 }).map((_, index) => (
                    <Skeleton key={index} className="h-14 rounded-xl" />
                  ))}
                </div>
              ) : chart.data.length === 0 ? (
                <EmptyState
                  icon={Network}
                  title="Nothing to show"
                  description="Nobody reports to you yet."
                />
              ) : (
                <OrgTree nodes={chart.data} focusId={focusId} />
              )}
            </CardBody>
          </Card>
        </div>

        {/* ------------------------------------------------ roster */}
        <div className="xl:col-span-2">
          <Card className="overflow-hidden">
            <CardHeader className="block">
              <CardTitle>People</CardTitle>
              <CardDescription>Everyone within your visibility scope.</CardDescription>
            </CardHeader>

            <div className="space-y-3 border-b border-line p-3.5">
              <Input
                type="search"
                placeholder="Search by name or email…"
                icon={<Search className="size-4" />}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                aria-label="Search people"
              />
              <Switch
                checked={includeInactive}
                onChange={setIncludeInactive}
                label="Show deactivated"
                hint={
                  includeInactive && inactiveShown > 0
                    ? `${inactiveShown} shown`
                    : undefined
                }
              />
            </div>

            {people.error ? (
              <ErrorState error={people.error} onRetry={people.reload} />
            ) : people.loading && !people.data ? (
              <TableSkeleton rows={8} columns={3} />
            ) : people.data && people.data.items.length === 0 ? (
              <EmptyState
                icon={Users}
                title="Nobody matches"
                description="Try a different name or email."
              />
            ) : people.data ? (
              <>
                <TableWrap className="max-h-[32rem] overflow-y-auto">
                  <Table className="min-w-0">
                    <THead className="sticky top-0 z-10">
                      <tr>
                        <TH>Person</TH>
                        <TH align="right">Role</TH>
                      </tr>
                    </THead>
                    <TBody>
                      {people.data.items.map((person) => (
                        <TR key={person.id}>
                          <TD>
                            <span className="flex items-center gap-2.5">
                              <Avatar name={person.name} size="xs" />
                              <span className="min-w-0">
                                <span className="flex items-center gap-1.5">
                                  <span className="truncate font-medium text-content">
                                    {person.name}
                                  </span>
                                  {person.is_active ? null : <ActiveBadge active={false} />}
                                </span>
                                <span className="block truncate text-[12px] text-subtle">
                                  {person.email}
                                </span>
                              </span>
                            </span>
                          </TD>
                          <TD data-label="Role" align="right">
                            <RoleBadge role={person.role} />
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                <p className="border-t border-line px-4 py-2.5 text-[12.5px] text-muted">
                  {formatNumber(people.data.total)}{" "}
                  {people.data.total === 1 ? "person" : "people"} in your scope
                  {includeInactive ? ", deactivated included" : ", excluding deactivated"}
                </p>
              </>
            ) : null}
          </Card>
        </div>
      </div>
    </>
  );
}

export default function TeamPage() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-card" />}>
      <TeamPageInner />
    </Suspense>
  );
}
