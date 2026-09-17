"use client";

import {
  History,
  KeyRound,
  Lock,
  MoreHorizontal,
  Pencil,
  Search,
  ShieldCheck,
  Unlock,
  UserPlus,
  Trash2,
  UserX,
  Undo2,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";

import { PasswordReveal } from "@/components/admin/PasswordReveal";
import { PageHeader } from "@/components/layout/PageHeader";
import { Avatar } from "@/components/ui/Avatar";
import { ActiveBadge, Badge, RoleBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/Feedback";
import { Input, Switch } from "@/components/ui/Form";
import { Pagination } from "@/components/ui/Pagination";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { useToast } from "@/lib/toast";
import { useAsync, useDebounced } from "@/lib/useAsync";
import type { UserDetail } from "@/types/api";
import { isSuperAdmin, withHonorific } from "@/lib/roles";

const PAGE_SIZE = 25;

// Five dialogs, ~850 lines, every one of them behind a click. They used to be
// part of the page's first load even though the table is what people come for.
const ACTION_DIALOGS = () => import("@/components/admin/UserActionDialogs");
const UserDetailPanel = dynamic(() => ACTION_DIALOGS().then((m) => m.UserDetailPanel), {
  ssr: false,
});
const DeleteUserDialog = dynamic(() => ACTION_DIALOGS().then((m) => m.DeleteUserDialog), {
  ssr: false,
});
const ResetPasswordDialog = dynamic(
  () => ACTION_DIALOGS().then((m) => m.ResetPasswordDialog),
  { ssr: false },
);
const DeactivateDialog = dynamic(() => ACTION_DIALOGS().then((m) => m.DeactivateDialog), {
  ssr: false,
});
const UserActivityDialog = dynamic(
  () => ACTION_DIALOGS().then((m) => m.UserActivityDialog),
  { ssr: false },
);
const UnlockUserDialog = dynamic(() => ACTION_DIALOGS().then((m) => m.UnlockUserDialog), {
  ssr: false,
});
const UserFormDialog = dynamic(
  () => import("@/components/admin/UserFormDialog").then((m) => m.UserFormDialog),
  { ssr: false },
);

export default function AdminUsersPage() {
  const { user: actor } = useAuth();
  const toast = useToast();

  const [search, setSearch] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [page, setPage] = useState(1);

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<UserDetail | null>(null);
  const [resetting, setResetting] = useState<UserDetail | null>(null);
  const [deleting, setDeleting] = useState<UserDetail | null>(null);
  const [viewing, setViewing] = useState<UserDetail | null>(null);
  const [deactivating, setDeactivating] = useState<UserDetail | null>(null);
  const [auditing, setAuditing] = useState<UserDetail | null>(null);
  const [unlocking, setUnlocking] = useState<UserDetail | null>(null);

  const debouncedSearch = useDebounced(search, 300);

  // Reset to page 1 in the handlers, not an effect — otherwise a filter
  // change can ask for a page the new result set does not have.
  function changeSearch(value: string) {
    setSearch(value);
    setPage(1);
  }

  function changeIncludeInactive(value: boolean) {
    setIncludeInactive(value);
    setPage(1);
  }

  const listing = useAsync(
    (signal) =>
      api.users.list(
        {
          search: debouncedSearch || undefined,
          include_inactive: includeInactive,
          page,
          page_size: PAGE_SIZE,
        },
        signal,
      ),
    [debouncedSearch, includeInactive, page],
  );

  async function reactivate(person: UserDetail) {
    try {
      await api.users.reactivate(person.id);
      toast.success("Reactivated", `${person.name} can sign in again.`);
      listing.reload();
    } catch (cause) {
      toast.error("Could not reactivate", errorMessage(cause));
    }
  }

  // How many of the rows on this page are deactivated. Without this the
  // toggle can reveal one row at the bottom of twenty and look inert.
  // name -> honorific, for the people on this page. The listing returns a
  // manager's NAME but not their honorific, and one lookup map beats a
  // request per row.
  const managerHonorifics = new Map(
    (listing.data?.items ?? [])
      .filter((person) => person.honorific)
      .map((person) => [person.name, person.honorific] as const),
  );

  const inactiveCount = (listing.data?.items ?? []).filter(
    (person) => !person.is_active,
  ).length;

  if (!actor) return null;

  return (
    <>
      <PageHeader
        title="User administration"
        description={
          <>
            Create people, change reporting lines and change passwords. Every action is
            checked against your own rank and written to the audit trail — you can only
            act on people below you.
          </>
        }
        actions={
          <Button onClick={() => setCreating(true)}>
            <UserPlus className="size-4" aria-hidden />
            Add person
          </Button>
        }
      />

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-4 border-b border-line p-3.5">
          <div className="min-w-52 flex-1">
            <Input
              type="search"
              placeholder="Search by name or email…"
              icon={<Search className="size-4" />}
              value={search}
              onChange={(event) => changeSearch(event.target.value)}
              aria-label="Search users"
            />
          </div>
          <Switch
            checked={includeInactive}
            onChange={changeIncludeInactive}
            label="Show deactivated"
            hint={
              includeInactive && inactiveCount > 0
                ? `${inactiveCount} shown`
                : undefined
            }
          />
        </div>

        {listing.error ? (
          <ErrorState error={listing.error} onRetry={listing.reload} />
        ) : listing.loading && !listing.data ? (
          <TableSkeleton rows={8} columns={7} />
        ) : listing.data && listing.data.items.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="Nobody matches"
            description="Try a different search term."
          />
        ) : listing.data ? (
          <>
            <TableWrap>
              <Table>
                <THead>
                  <tr>
                    <TH>Person</TH>
                    <TH>Role</TH>
                    <TH>Reports to</TH>
                    <TH>Team</TH>
                    <TH>Status</TH>
                    <TH>Last sign-in</TH>
                    <TH align="right">Actions</TH>
                  </tr>
                </THead>
                <TBody>
                  {listing.data.items.map((person) => (
                    <TR key={person.id}>
                      <TD>
                        <div className="flex items-center gap-2.5">
                          <Avatar name={person.name} size="sm" />
                          <span className="min-w-0 flex-1">
                            <button
                              type="button"
                              onClick={() => setViewing(person)}
                              aria-label={`Open ${person.name}'s account`}
                              className="block text-left hover:underline"
                            >
                              <span className="block truncate font-medium text-content">
                                {person.name}
                                {person.id === actor.id ? (
                                  <span className="ml-1.5 text-[11.5px] font-normal text-subtle">
                                    (you)
                                  </span>
                                ) : null}
                              </span>
                            </button>
                            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-subtle">
                              {person.username ? (
                                <span className="font-medium text-muted">@{person.username}</span>
                              ) : null}
                              <span className="truncate">{person.email}</span>
                              {isSuperAdmin(actor?.role) ? (
                                <>
                                  <span className="text-subtle/40">•</span>
                                  <PasswordReveal key={person.id} userId={person.id} />
                                </>
                              ) : null}
                            </div>
                          </span>
                        </div>
                      </TD>
                      <TD data-label="Role">
                        <RoleBadge role={person.role} />
                      </TD>
                      <TD data-label="Reports to" className="whitespace-nowrap text-muted">
                        {person.manager_name ? (
                          // The honorific belongs to the manager, not to this
                          // row - the listing carries only their name, so it
                          // is resolved from the page's own rows when they
                          // are on it.
                          withHonorific(
                            person.manager_name,
                            managerHonorifics.get(person.manager_name) ?? null,
                          )
                        ) : (
                          <span className="text-subtle">Nobody</span>
                        )}
                      </TD>
                      <TD data-label="Team" className="whitespace-nowrap text-muted">
                        {person.team_name ?? <span className="text-subtle">—</span>}
                      </TD>
                      <TD data-label="Status">
                        {person.is_active ? (
                          person.is_locked ? (
                            <span className="flex flex-col gap-0.5">
                              <Badge className="border-transparent bg-danger-soft text-danger">
                                <Lock className="size-3" aria-hidden />
                                Locked
                              </Badge>
                              <span className="text-[11px] text-subtle">
                                {person.failed_login_count} failed attempt
                                {person.failed_login_count === 1 ? "" : "s"}
                              </span>
                            </span>
                          ) : person.must_change_password ? (
                            <span className="flex flex-col gap-0.5">
                              <ActiveBadge active />
                              <Badge className="border-transparent bg-warning-soft text-warning">
                                Password change pending
                              </Badge>
                            </span>
                          ) : (
                            <ActiveBadge active />
                          )
                        ) : (
                          <span className="flex flex-col gap-0.5">
                            <ActiveBadge active={false} />
                            <span className="text-[11px] text-subtle">
                              {formatDate(person.deactivated_at)}
                            </span>
                          </span>
                        )}
                      </TD>
                      <TD
                        data-label="Last sign-in"
                        className="whitespace-nowrap text-muted"
                      >
                        {person.last_login_at ? (
                          <span title={formatDateTime(person.last_login_at)}>
                            {formatRelative(person.last_login_at)}
                          </span>
                        ) : (
                          <span className="text-subtle">Never</span>
                        )}
                      </TD>
                      <TD data-actions align="right">
                        {/* Reactivating is the one thing you come to a
                            deactivated row to do. Burying it in the overflow
                            menu made it effectively invisible. */}
                        {!person.is_active && person.can_act_on ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            className="mr-1.5"
                            onClick={() => reactivate(person)}
                          >
                            <Undo2 className="size-3.5" aria-hidden />
                            Activate
                          </Button>
                        ) : null}
                        <RowActions
                          person={person}
                          onEdit={() => setEditing(person)}
                          onReset={() => setResetting(person)}
                          onDelete={
                            isSuperAdmin(actor?.role)
                              ? () => setDeleting(person)
                              : undefined
                          }
                          onDeactivate={() => setDeactivating(person)}
                          onReactivate={() => reactivate(person)}
                          onActivity={() => setAuditing(person)}
                          onUnlock={() => setUnlocking(person)}
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
              noun="people"
            />
          </>
        ) : null}
      </Card>

      {/* `key` remounts each dialog when its target changes, so the form
          state comes from a fresh initialiser rather than a reset effect.
          Each is mounted only while it has a target - they rendered nothing
          otherwise, so only the fetch timing changes. */}
      {viewing ? (
        <UserDetailPanel
          key={`view-${viewing.id}`}
          user={viewing}
          canRevealPassword={isSuperAdmin(actor?.role)}
          onClose={() => setViewing(null)}
          onEdit={() => { setEditing(viewing); setViewing(null); }}
          onSetPassword={() => { setResetting(viewing); setViewing(null); }}
          onDeactivate={() => { setDeactivating(viewing); setViewing(null); }}
          onReactivate={() => { const target = viewing; setViewing(null); if (target) void reactivate(target); }}
        />
      ) : null}

      {creating ? (
        <UserFormDialog
          open
          onClose={() => setCreating(false)}
          onSaved={listing.reload}
          actor={actor}
        />
      ) : null}
      {editing ? (
        <UserFormDialog
          key={`edit-${editing.id}`}
          open
          onClose={() => setEditing(null)}
          onSaved={listing.reload}
          user={editing}
          actor={actor}
        />
      ) : null}
      {deleting ? (
        <DeleteUserDialog
          key={`delete-${deleting.id}`}
          open
          onClose={() => setDeleting(null)}
          onDone={listing.reload}
          onDeactivateInstead={() => setDeactivating(deleting)}
          user={deleting}
        />
      ) : null}

      {resetting ? (
        <ResetPasswordDialog
          key={`reset-${resetting.id}`}
          open
          onClose={() => setResetting(null)}
          onDone={listing.reload}
          user={resetting}
        />
      ) : null}
      {deactivating ? (
        <DeactivateDialog
          key={`deactivate-${deactivating.id}`}
          open
          onClose={() => setDeactivating(null)}
          onDone={listing.reload}
          user={deactivating}
        />
      ) : null}
      {auditing ? (
        <UserActivityDialog
          key={`activity-${auditing.id}`}
          user={auditing}
          onClose={() => setAuditing(null)}
        />
      ) : null}
      {unlocking ? (
        <UnlockUserDialog
          key={`unlock-${unlocking.id}`}
          user={unlocking}
          onClose={() => setUnlocking(null)}
          onDone={listing.reload}
        />
      ) : null}
    </>
  );
}

/**
 * Row actions.
 *
 * `can_act_on` comes from the server, which derives it from the same rule the
 * mutation endpoints enforce — so the menu never offers something that would
 * come back 403.
 */
function RowActions({
  person,
  onEdit,
  onReset,
  onDeactivate,
  onReactivate,
  onActivity,
  onUnlock,
  onDelete,
}: {
  person: UserDetail;
  onEdit: () => void;
  onReset: () => void;
  onDeactivate: () => void;
  onReactivate: () => void;
  onActivity: () => void;
  onUnlock: () => void;
  /** Omitted for anyone who is not a Super Admin, so the menu never offers
   *  an action the server would refuse. */
  onDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!person.can_act_on) {
    return (
      <span className="text-[12px] text-subtle" title="You cannot act on this person">
        —
      </span>
    );
  }

  return (
    <div className="relative inline-block text-left" ref={ref}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Actions for ${person.name}`}
      >
        <MoreHorizontal className="size-4" aria-hidden />
      </Button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 w-48 origin-top-right animate-scale-in overflow-hidden rounded-xl border border-line bg-surface p-1.5 card-shadow-lg"
        >
          <MenuItem
            icon={Pencil}
            label="Edit"
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
          />
          <MenuItem
            icon={KeyRound}
            label="Change password"
            onClick={() => {
              setOpen(false);
              onReset();
            }}
          />
          {person.is_locked ? (
            <MenuItem
              icon={Unlock}
              label="Unlock"
              onClick={() => {
                setOpen(false);
                onUnlock();
              }}
            />
          ) : null}
          {person.is_active ? (
            <MenuItem
              icon={UserX}
              label="Deactivate"
              destructive
              onClick={() => {
                setOpen(false);
                onDeactivate();
              }}
            />
          ) : (
            <MenuItem
              icon={Undo2}
              label="Activate"
              onClick={() => {
                setOpen(false);
                onReactivate();
              }}
            />
          )}
          <MenuItem
            icon={History}
            label="View activity"
            onClick={() => {
              setOpen(false);
              onActivity();
            }}
          />
          {onDelete ? (
            <MenuItem
              icon={Trash2}
              label="Delete permanently"
              destructive
              onClick={() => {
                setOpen(false);
                onDelete();
              }}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
  destructive = false,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={
        "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors " +
        (destructive
          ? "text-muted hover:bg-danger-soft hover:text-danger"
          : "text-muted hover:bg-surface-hover hover:text-content")
      }
    >
      <Icon className="size-4" aria-hidden />
      {label}
    </button>
  );
}
