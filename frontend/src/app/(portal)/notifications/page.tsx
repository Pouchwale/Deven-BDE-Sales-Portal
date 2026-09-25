"use client";

import { AlertTriangle, Bell, CalendarClock, CheckCheck, Target } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/Feedback";
import { Pagination } from "@/components/ui/Pagination";
import { Tabs } from "@/components/ui/Tabs";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { useNotifications } from "@/lib/notifications";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";

const PAGE_SIZE = 25;

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  LEAD_ASSIGNED: Target,
  LEAD_REASSIGNED: Target,
  REFERENCE_FOLLOWUP_DUE: CalendarClock,
  DEPARTMENT_ALERT: AlertTriangle,
};

const TONES: Record<string, string> = {
  LEAD_ASSIGNED: "bg-info-soft text-info",
  LEAD_REASSIGNED: "bg-surface-2 text-subtle",
  REFERENCE_FOLLOWUP_DUE: "bg-warning-soft text-warning",
  DEPARTMENT_ALERT: "bg-danger-soft text-danger",
};

/** Where clicking a notification should take you. */
function destination(entityType: string | null, entityId: string | null): string {
  if (entityType === "LEAD") return "/leads?tab=my-work";
  if (entityType === "CUSTOMER" && entityId) return `/customers/detail?id=${entityId}`;
  if (entityType === "DEPARTMENT") return "/feedback?tab=alerts";
  return "/dashboard";
}

export default function NotificationsPage() {
  const toast = useToast();
  const { refresh } = useNotifications();

  const [tab, setTab] = useState("all");
  const [page, setPage] = useState(1);
  const [nonce, setNonce] = useState(0);

  const listing = useAsync(
    (signal) =>
      api.notifications.list(
        { unread_only: tab === "unread", page, page_size: PAGE_SIZE },
        signal,
      ),
    [tab, page, nonce],
  );

  const reload = () => {
    setNonce((value) => value + 1);
    refresh();
  };

  async function markOne(id: string) {
    try {
      await api.notifications.markRead(id);
      reload();
    } catch (cause) {
      toast.error("Could not mark as read", errorMessage(cause));
    }
  }

  async function markAll() {
    try {
      const { message } = await api.notifications.markAllRead();
      toast.success(message);
      reload();
    } catch (cause) {
      toast.error("Could not mark all as read", errorMessage(cause));
    }
  }

  const unreadOnPage = listing.data?.items.filter((item) => !item.is_read).length ?? 0;

  return (
    <>
      <PageHeader
        title="Notifications"
        description="New work assigned to you, reference follow-ups that have come round, and department alerts."
        actions={
          <Button variant="secondary" onClick={markAll} disabled={unreadOnPage === 0}>
            <CheckCheck className="size-4" aria-hidden />
            Mark all read
          </Button>
        }
      />

      <Tabs
        className="mb-4 w-fit"
        active={tab}
        onChange={(next) => {
          setTab(next);
          setPage(1);
        }}
        tabs={[
          { key: "all", label: "All" },
          { key: "unread", label: "Unread" },
        ]}
      />

      <Card className="overflow-hidden">
        {listing.error ? (
          <ErrorState error={listing.error} onRetry={listing.reload} />
        ) : listing.loading && !listing.data ? (
          <TableSkeleton rows={6} columns={3} />
        ) : listing.data && listing.data.items.length === 0 ? (
          <EmptyState
            icon={Bell}
            title={tab === "unread" ? "Nothing unread" : "No notifications yet"}
            description="You are notified when a lead is assigned to you, when a reference follow-up comes round, and when a department you head drops below threshold."
          />
        ) : listing.data ? (
          <>
            <ul className="divide-y divide-line">
              {listing.data.items.map((item) => {
                const Icon = ICONS[item.type] ?? Bell;
                return (
                  <li
                    key={item.id}
                    className={cn(
                      "flex items-start gap-3 px-5 py-3.5 transition-colors",
                      item.is_read ? "bg-surface" : "bg-brand-600/[0.04] dark:bg-brand-400/[0.06]",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg",
                        TONES[item.type] ?? "bg-surface-2 text-subtle",
                      )}
                      aria-hidden
                    >
                      <Icon className="size-4" />
                    </span>

                    <div className="min-w-0 flex-1">
                      <Link
                        href={destination(item.entity_type, item.entity_id)}
                        onClick={() => !item.is_read && void markOne(item.id)}
                        className="block"
                      >
                        <p className="flex items-center gap-2 text-[13.5px] font-medium text-content">
                          {item.title}
                          {item.is_read ? null : (
                            <span
                              className="size-1.5 shrink-0 rounded-full bg-brand-600 dark:bg-brand-400"
                              aria-label="Unread"
                            />
                          )}
                        </p>
                        {item.body ? (
                          <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">
                            {item.body}
                          </p>
                        ) : null}
                      </Link>
                      <p className="mt-1 flex items-center gap-2 text-[11.5px] text-subtle">
                        <span>{formatRelative(item.created_at)}</span>
                        <Badge className="border-line bg-surface-2 text-subtle">
                          {item.type.replace(/_/g, " ").toLowerCase()}
                        </Badge>
                      </p>
                    </div>

                    {item.is_read ? null : (
                      <Button size="sm" variant="ghost" onClick={() => void markOne(item.id)}>
                        Mark read
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
            <Pagination
              page={listing.data.page}
              pageSize={listing.data.page_size}
              total={listing.data.total}
              onPageChange={setPage}
              noun="notifications"
            />
          </>
        ) : null}
      </Card>
    </>
  );
}
