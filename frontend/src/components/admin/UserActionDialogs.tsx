"use client";

import { useEffect, useState } from "react";

import {
  PASSWORD_MIN_LENGTH,
  PASSWORD_POLICY_HINT,
  passwordProblem,
} from "@/components/admin/passwordPolicy";
import { PasswordReveal } from "@/components/admin/PasswordReveal";

import { History, KeyRound, Lock, Pencil, Trash2, Unlock } from "lucide-react";

import { ActiveBadge, Badge, RoleBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorState, InlineError, TableSkeleton } from "@/components/ui/Feedback";
import { Field, PasswordInput, Select } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { Pagination } from "@/components/ui/Pagination";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, formatRelative } from "@/lib/format";
import { roleLabel } from "@/lib/roles";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";
import type { User, UserActivityItem, UserDetail } from "@/types/api";

/** Status shown for an account: Locked wins over Active, Inactive over both. */
export function AccountStatusBadge({ user }: { user: UserDetail }) {
  if (!user.is_active) return <ActiveBadge active={false} />;
  if (user.is_locked)
    return (
      <Badge className="border-transparent bg-danger-soft text-danger">
        <Lock className="size-3" aria-hidden />
        Locked
      </Badge>
    );
  return <ActiveBadge active />;
}

/* -------------------------------------------------------- set password */
/**
 * Change somebody's password.
 *
 * The administrator types the new one twice (the eye shows what was typed);
 * it is sent once and never echoed back. The server answers only after the
 * change is committed AND re-read from the database, so "Password updated
 * successfully" means the next sign-in will accept it. The Super Admin can
 * look at the current password afterwards with the eye in the account panel.
 */
export function ResetPasswordDialog({
  open,
  onClose,
  onDone,
  user,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  user: UserDetail | null;
}) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  // Off by default: the password set here is the one the person uses.
  const [mustChange, setMustChange] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const problem = passwordProblem(password);
  const mismatch = confirm.length > 0 && confirm !== password;
  const ready = password.length > 0 && !problem && confirm === password;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!user || !ready) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.users.setPassword(user.id, password, confirm, mustChange);
      // Nothing about the password is kept in this component once it closes.
      setPassword("");
      setConfirm("");
      toast.success("Password updated successfully.", result.message);
      onDone();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="sm"
      title={`Change password for ${user?.name ?? ""}`}
      description="Their current sessions end immediately and any sign-in lockout is cleared."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" form="reset-form" loading={saving} disabled={!ready}>
            Change password
          </Button>
        </>
      }
    >
      <form id="reset-form" onSubmit={submit} className="space-y-4" noValidate>
        <Field
          label="New password"
          htmlFor="new-password"
          required
          error={problem}
          hint={PASSWORD_POLICY_HINT}
        >
          <PasswordInput
            id="new-password"
            required
            minLength={PASSWORD_MIN_LENGTH}
            autoComplete="new-password"
            value={password}
            invalid={Boolean(problem)}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <Field
          label="Confirm new password"
          htmlFor="confirm-password"
          required
          error={mismatch ? "The two passwords do not match." : null}
        >
          <PasswordInput
            id="confirm-password"
            required
            autoComplete="new-password"
            value={confirm}
            invalid={mismatch}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>

        <label className="flex items-start gap-2.5 text-[13px] text-muted">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--brand-600)]"
            checked={mustChange}
            onChange={(event) => setMustChange(event.target.checked)}
          />
          <span>
            Require a password change at next sign-in
            <span className="mt-0.5 block text-[12px] text-subtle">
              Leave off to hand them this password to keep. Turn on to make them pick
              their own after signing in with it.
            </span>
          </span>
        </label>

        {error ? <InlineError>{errorMessage(error)}</InlineError> : null}
      </form>
    </Modal>
  );
}

/* ---------------------------------------------------------------- delete */
/** Plain words for what is handed over, by the table it lives in. */
const HANDOVER_LABELS: Record<string, string> = {
  customers: "customers",
  invoice_lines: "invoice lines",
  leads: "leads",
  customer_references: "reference asks",
  feedback_requests: "feedback requests",
  feedback: "feedback replies",
  feedback_alerts: "feedback alerts",
  users: "direct reports",
};

/**
 * Delete an account - the only way to remove somebody. Super Admin only.
 *
 * Nothing they worked on is lost: whatever they own moves to the person
 * chosen here, in the same transaction as the delete, and the activity
 * history keeps their name. The server decides what has to move; this dialog
 * shows its answer and asks only who takes it.
 */
export function DeleteUserDialog({
  open,
  onClose,
  onDone,
  user,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  user: UserDetail | null;
}) {
  const toast = useToast();
  const [receiver, setReceiver] = useState("");
  const [candidates, setCandidates] = useState<User[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const preview = useAsync(
    (signal) =>
      user ? api.users.deletionPreview(user.id, signal) : Promise.resolve({ handover: [] }),
    [user?.id],
  );
  const handover = preview.data?.handover ?? [];
  const needsReceiver = handover.length > 0;

  useEffect(() => {
    if (!open || !needsReceiver) return;
    const controller = new AbortController();
    api.users
      .actionable(controller.signal)
      .then((people) =>
        setCandidates(people.filter((person) => person.id !== user?.id && person.is_active)),
      )
      .catch(() => {
        /* the select simply stays empty; the API still enforces the rule */
      });
    return () => controller.abort();
  }, [open, needsReceiver, user?.id]);

  async function submit() {
    if (!user) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.users.delete(user.id, needsReceiver ? receiver : null);
      toast.success("Account deleted", result.message);
      onDone();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={`Delete ${user?.name ?? ""}?`}
      description="Their account and sign-in are removed. This cannot be undone."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={submit}
            loading={saving}
            disabled={preview.loading || Boolean(preview.error) || (needsReceiver && !receiver)}
          >
            Delete
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {preview.loading ? (
          <p className="text-[13px] text-muted">Checking what {user?.name} owns…</p>
        ) : preview.error ? (
          <InlineError>{errorMessage(preview.error)}</InlineError>
        ) : needsReceiver ? (
          <>
            <div className="rounded-lg border border-line bg-surface-2 px-3 py-2.5">
              <p className="text-[12.5px] font-semibold text-content">
                {user?.name} still has work that will be handed over:
              </p>
              <ul className="mt-1.5 space-y-0.5">
                {handover.map((row) => (
                  <li key={`${row.table}.${row.column}`} className="text-[12.5px] text-muted">
                    · {row.rows} {HANDOVER_LABELS[row.table] ?? row.table}
                  </li>
                ))}
              </ul>
            </div>
            <Field
              label="Hand everything over to"
              htmlFor="receiver"
              required
              hint="Their activity history keeps their name."
            >
              <Select
                id="receiver"
                required
                value={receiver}
                onChange={(event) => setReceiver(event.target.value)}
              >
                <option value="">Choose a person…</option>
                {candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.name} · {roleLabel(candidate.role)}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        ) : (
          <p className="text-[13px] leading-relaxed text-muted">
            {user?.name} owns no customers, leads or references, so nothing needs to be
            handed over.
          </p>
        )}

        {error ? <InlineError>{errorMessage(error)}</InlineError> : null}
      </div>
    </Modal>
  );
}


/* ------------------------------------------------------------ detail panel */
/**
 * One person's account, opened by clicking their name.
 *
 * Editing used to live only behind the row's three-dot menu, which is where
 * actions go to be undiscovered. This is the obvious path - click the person,
 * read the account, act on it - and it deliberately does not restate the Team
 * page: only what an administrator manages here.
 */
export function UserDetailPanel({
  user,
  onClose,
  onEdit,
  onSetPassword,
  onDelete,
  canRevealPassword = false,
}: {
  user: UserDetail | null;
  /** Super Admin only: the password row with the eye button. */
  canRevealPassword?: boolean;
  onClose: () => void;
  onEdit: () => void;
  onSetPassword: () => void;
  /** Super Admin only: deleting is theirs alone. */
  onDelete?: () => void;
}) {
  return (
    <Modal
      open={user !== null}
      onClose={onClose}
      size="sm"
      title={user?.name ?? ""}
      description="Account details. Changing an email or password never changes what this person may see."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          {user?.can_act_on && onDelete ? (
            <Button variant="danger" onClick={onDelete}>
              <Trash2 className="size-3.5" aria-hidden />
              Delete
            </Button>
          ) : null}
          {user?.can_act_on ? (
            <>
              <Button variant="secondary" onClick={onSetPassword}>
                <KeyRound className="size-3.5" aria-hidden />
                Change password
              </Button>
              <Button onClick={onEdit}>
                <Pencil className="size-3.5" aria-hidden />
                Edit account
              </Button>
            </>
          ) : null}
        </>
      }
    >
      {user ? (
        <div className="space-y-3">
          <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-2 text-[13px]">
            <dt className="text-subtle">Email</dt>
            <dd className="break-all font-medium text-content">{user.email}</dd>
            <dt className="text-subtle">Username</dt>
            <dd className="font-medium text-content">{user.username ?? "—"}</dd>
            {canRevealPassword ? (
              <>
                <dt className="text-subtle">Password</dt>
                <dd className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                  <PasswordReveal
                    userId={user.id}
                    onSetPassword={user.can_act_on ? onSetPassword : undefined}
                  />
                  {user.can_act_on ? (
                    <button
                      type="button"
                      onClick={onSetPassword}
                      className="inline-flex items-center gap-1 text-[12.5px] font-medium text-brand-700 hover:underline dark:text-brand-300"
                    >
                      <Pencil className="size-3" aria-hidden />
                      Edit password
                    </button>
                  ) : null}
                </dd>
              </>
            ) : null}
            <dt className="text-subtle">Last sign-in</dt>
            <dd className="text-content" title={formatDateTime(user.last_login_at)}>
              {user.last_login_at ? formatRelative(user.last_login_at) : "Never"}
            </dd>
            <dt className="text-subtle">Password set</dt>
            <dd className="text-content">
              {user.password_changed_at ? formatDateTime(user.password_changed_at) : "—"}
              {user.must_change_password ? (
                <span className="ml-1.5 text-[12px] text-warning">(change required)</span>
              ) : null}
            </dd>
            <dt className="text-subtle">Role</dt>
            <dd><RoleBadge role={user.role} /></dd>
            <dt className="text-subtle">Team</dt>
            <dd className="text-content">{user.team_name ?? "—"}</dd>
            <dt className="text-subtle">Reports to</dt>
            <dd className="text-content">{user.manager_name ?? "Nobody"}</dd>
            <dt className="text-subtle">Title</dt>
            <dd className="text-content">{user.title ?? "—"}</dd>
            <dt className="text-subtle">Phone</dt>
            <dd className="text-content">{user.phone ?? "—"}</dd>
            <dt className="text-subtle">Status</dt>
            <dd>
              <span className="inline-flex items-center gap-2">
                <AccountStatusBadge user={user} />
                {user.is_locked ? (
                  <span className="text-[12px] text-subtle">
                    until {formatDateTime(user.locked_until)}
                  </span>
                ) : null}
              </span>
            </dd>
          </dl>
        </div>
      ) : null}
    </Modal>
  );
}

/* ------------------------------------------------------------- activity */
const ACTIVITY_PAGE_SIZE = 20;

function humaniseAction(action: string): string {
  const text = action.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function describeChange(event: UserActivityItem): string | null {
  const after = event.after ?? {};
  const keys = Object.keys(after);
  if (keys.length === 0) return null;
  return keys
    .slice(0, 4)
    .map((key) => {
      const value = after[key];
      const shown =
        value === null || value === undefined
          ? "—"
          : typeof value === "object"
            ? "…"
            : String(value);
      return `${key.replace(/_/g, " ")}: ${shown.length > 40 ? `${shown.slice(0, 40)}…` : shown}`;
    })
    .join(" · ");
}

/**
 * One person's audit trail: what was done to their account and what they
 * did. The server strips any password material before it gets here.
 */
export function UserActivityDialog({
  user,
  onClose,
}: {
  user: UserDetail;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const activity = useAsync(
    (signal) =>
      api.users.activity(user.id, { page, page_size: ACTIVITY_PAGE_SIZE }, signal),
    [user.id, page],
  );

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={`Activity — ${user.name}`}
      description="Changes to this account and actions this person took, newest first."
      footer={
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      }
    >
      {activity.error ? (
        <ErrorState error={activity.error} onRetry={activity.reload} />
      ) : activity.loading && !activity.data ? (
        <TableSkeleton rows={6} columns={3} />
      ) : activity.data && activity.data.items.length === 0 ? (
        <EmptyState icon={History} title="No activity yet" />
      ) : activity.data ? (
        <div className="-mx-1">
          <ul className="divide-y divide-line">
            {activity.data.items.map((event) => {
              const about = event.entity_id === user.id && event.entity_type === "USER";
              const detail = describeChange(event);
              return (
                <li key={event.id} className="px-1 py-2.5">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                    <span className="text-[13px] font-medium text-content">
                      {humaniseAction(event.action)}
                    </span>
                    <span
                      className="text-[12px] text-subtle"
                      title={formatDateTime(event.created_at)}
                    >
                      {formatRelative(event.created_at)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[12px] text-muted">
                    {about
                      ? `By ${event.actor_user_id === user.id ? "themselves" : (event.actor_name ?? "system")}`
                      : `On ${(event.entity_type ?? "record").toLowerCase()}`}
                    {event.ip_address ? ` · ${event.ip_address}` : ""}
                  </p>
                  {detail ? (
                    <p className="mt-0.5 break-words text-[12px] text-subtle">{detail}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <Pagination
            page={activity.data.page}
            pageSize={activity.data.page_size}
            total={activity.data.total}
            onPageChange={setPage}
            noun="events"
          />
        </div>
      ) : null}
    </Modal>
  );
}

/* --------------------------------------------------------------- unlock */
export function UnlockUserDialog({
  user,
  onClose,
  onDone,
}: {
  user: UserDetail;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      await api.users.unlock(user.id);
      toast.success("Account unlocked", `${user.name} can try signing in again.`);
      onDone();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      size="sm"
      title={`Unlock ${user.name}?`}
      description="Clears the sign-in lockout and the failed-attempt counter. Their password does not change."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} loading={saving}>
            <Unlock className="size-3.5" aria-hidden />
            Unlock
          </Button>
        </>
      }
    >
      <div className="space-y-2 text-[13px] text-muted">
        <p>
          {user.failed_login_count} failed sign-in attempt
          {user.failed_login_count === 1 ? "" : "s"}
          {user.locked_until ? `, locked until ${formatDateTime(user.locked_until)}` : ""}.
        </p>
        <p>If you did not expect this lockout, consider changing their password instead.</p>
        {error ? <InlineError>{errorMessage(error)}</InlineError> : null}
      </div>
    </Modal>
  );
}
