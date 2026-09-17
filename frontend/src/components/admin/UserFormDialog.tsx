"use client";

import { useEffect, useMemo, useState } from "react";

import {
  PASSWORD_MIN_LENGTH,
  PASSWORD_POLICY_HINT,
  passwordProblem,
} from "@/components/admin/passwordPolicy";
import { Button } from "@/components/ui/Button";
import { InlineError } from "@/components/ui/Feedback";
import { Field, Input, Select } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { ApiError, api, errorMessage } from "@/lib/api";
import { ERROR_HINTS, isAdmin, roleLabel } from "@/lib/roles";
import { useToast } from "@/lib/toast";
import type {
  Department,
  Honorific,
  Role,
  Team,
  UpdateUserBody,
  User,
  UserDetail,
} from "@/types/api";
import { phoneError } from "@/lib/phone";

interface FormState {
  name: string;
  email: string;
  username: string;
  password: string;
  confirm: string;
  mustChange: boolean;
  role: string;
  title: string;
  honorific: string;
  phone: string;
  manager_id: string;
  team_id: string;
  heads_department_id: string;
  status: "active" | "inactive";
}

const EMPTY: FormState = {
  name: "",
  email: "",
  username: "",
  password: "",
  confirm: "",
  mustChange: true,
  role: "",
  title: "",
  honorific: "",
  phone: "",
  manager_id: "",
  team_id: "",
  heads_department_id: "",
  status: "active",
};

export function UserFormDialog({
  open,
  onClose,
  onSaved,
  user,
  actor,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  /** Present when editing; absent when creating. */
  user?: UserDetail | null;
  actor: User;
}) {
  const toast = useToast();
  const editing = Boolean(user);
  const actorIsAdmin = isAdmin(actor.role);

  const [form, setForm] = useState<FormState>(() =>
    user
      ? {
          ...EMPTY,
          name: user.name,
          email: user.email,
          username: user.username ?? "",
          role: user.role,
          title: user.title ?? "",
          honorific: user.honorific ?? "",
          phone: user.phone ?? "",
          manager_id: user.manager_id ?? "",
          team_id: user.team_id ?? "",
          heads_department_id: user.heads_department_id ?? "",
          status: user.is_active ? "active" : "inactive",
        }
      : EMPTY,
  );
  const [roles, setRoles] = useState<Role[]>([]);
  const [managers, setManagers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);

  // The role list and the assignee list both come from the server, which
  // derives them from the same authority rules the submission is validated
  // against — so the form cannot offer something the API will refuse.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();

    Promise.all([
      api.users.assignableRoles(controller.signal),
      api.users.actionable(controller.signal),
      api.lookups.teams(controller.signal),
      actorIsAdmin
        ? api.users.departments(controller.signal)
        : Promise.resolve([] as Department[]),
    ])
      .then(([roleResponse, actionable, teamList, departmentList]) => {
        setRoles(roleResponse.roles);
        setManagers(actionable);
        setTeams(teamList);
        setDepartments(departmentList);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      });

    return () => controller.abort();
  }, [open, actorIsAdmin]);

  const managerOptions = useMemo(() => {
    const options = managers.filter((candidate) => candidate.id !== user?.id);
    // An admin may also place someone directly under themselves.
    if (!options.some((option) => option.id === actor.id)) {
      options.unshift({ ...actor, name: `${actor.name} (you)` });
    }
    // Keep a current manager selectable even when they are no longer in the
    // actionable list, so an unrelated edit does not silently clear the line.
    if (user?.manager_id && !options.some((option) => option.id === user.manager_id)) {
      options.push({
        ...actor,
        id: user.manager_id,
        name: `${user.manager_name ?? "Current manager"} (unchanged)`,
      });
    }
    return options;
  }, [managers, actor, user]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const passwordIssue = editing ? null : passwordProblem(form.password);
  const confirmMismatch = !editing && form.confirm.length > 0 && form.confirm !== form.password;
  const createReady =
    editing ||
    (form.password.length > 0 && !passwordIssue && form.confirm === form.password);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!createReady) return;
    setError(null);
    setSaving(true);

    try {
      if (editing && user) {
        const body: UpdateUserBody = {
          name: form.name,
          email: form.email,
          title: form.title || null,
          honorific: (form.honorific || null) as Honorific | null,
          phone: form.phone || null,
          role: form.role as Role,
          manager_id: form.manager_id || null,
          team_id: form.team_id || null,
        };
        // Only sent when changed: headship and status carry their own checks
        // on the server, which an unrelated edit should not trip.
        if (actorIsAdmin && (form.heads_department_id || null) !== user.heads_department_id) {
          body.heads_department_id = form.heads_department_id || null;
        }
        const nextUsername = form.username.trim().toLowerCase();
        if (nextUsername && nextUsername !== (user.username ?? "")) body.username = nextUsername;
        const nextActive = form.status === "active";
        if (nextActive !== user.is_active) body.is_active = nextActive;

        await api.users.update(user.id, body);
        toast.success("Saved", `${form.name} has been updated.`);
      } else {
        await api.users.create({
          name: form.name,
          email: form.email,
          username: form.username.trim() || null,
          password: form.password,
          confirm_password: form.confirm,
          must_change_password: form.mustChange,
          role: form.role as Role,
          title: form.title || null,
          honorific: (form.honorific || null) as Honorific | null,
          phone: form.phone || null,
          manager_id: form.manager_id || null,
          team_id: form.team_id || null,
        });
        // The password leaves the form state as soon as it has been used.
        setForm((current) => ({ ...current, password: "", confirm: "" }));
        toast.success(
          "Account created",
          form.mustChange
            ? `${form.name} must choose a new password at first sign-in.`
            : `${form.name} can sign in with the password you set.`,
        );
      }
      onSaved();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setSaving(false);
    }
  }

  const code = error instanceof ApiError ? error.code : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={editing ? `Edit ${user?.name}` : "Add a person"}
      description={
        editing
          ? "Role and reporting-line changes are re-checked against your own authority. A role change signs them out everywhere."
          : "The account starts with a password you set."
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" form="user-form" loading={saving} disabled={!createReady}>
            {editing ? "Save changes" : "Create account"}
          </Button>
        </>
      }
    >
      <form id="user-form" onSubmit={onSubmit} className="space-y-4" noValidate>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Full name" htmlFor="name" required>
            <Input
              id="name"
              required
              minLength={2}
              value={form.name}
              onChange={(event) => set("name", event.target.value)}
            />
          </Field>

          <Field label="Email address" htmlFor="email" required>
            <Input
              id="email"
              type="email"
              required
              autoComplete="off"
              value={form.email}
              onChange={(event) => set("email", event.target.value)}
            />
          </Field>

          <Field
            label="Username"
            htmlFor="username"
            hint={
              editing
                ? "What they type to sign in, instead of the full email."
                : "Leave blank to use their first name."
            }
          >
            <Input
              id="username"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder={editing ? undefined : "Enter a username"}
              value={form.username}
              onChange={(event) => set("username", event.target.value)}
            />
          </Field>
        </div>

        {editing ? null : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field
                label="Initial password"
                htmlFor="password"
                required
                error={passwordIssue}
                hint={PASSWORD_POLICY_HINT}
              >
                <Input
                  id="password"
                  type="password"
                  required
                  minLength={PASSWORD_MIN_LENGTH}
                  autoComplete="new-password"
                  value={form.password}
                  invalid={Boolean(passwordIssue)}
                  onChange={(event) => set("password", event.target.value)}
                />
              </Field>
              <Field
                label="Confirm password"
                htmlFor="confirm-password"
                required
                error={confirmMismatch ? "The two passwords do not match." : null}
              >
                <Input
                  id="confirm-password"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={form.confirm}
                  invalid={confirmMismatch}
                  onChange={(event) => set("confirm", event.target.value)}
                />
              </Field>
            </div>
            <label className="flex items-start gap-2.5 text-[13px] text-muted">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-[var(--brand-600)]"
                checked={form.mustChange}
                onChange={(event) => set("mustChange", event.target.checked)}
              />
              <span>
                Require a password change at first sign-in
                <span className="mt-0.5 block text-[12px] text-subtle">
                  Recommended. A password two people know is not a password.
                </span>
              </span>
            </label>
          </>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field
            label="Role"
            htmlFor="role"
            required
            hint="You can only grant roles below your own."
          >
            <Select
              id="role"
              required
              value={form.role}
              onChange={(event) => set("role", event.target.value)}
            >
              <option value="" disabled>
                Choose a role…
              </option>
              {/* When editing somebody who already holds a role you could not
                  grant, keep it selectable so an unrelated edit does not
                  silently demote them. */}
              {editing && form.role && !roles.includes(form.role as Role) ? (
                <option value={form.role}>{roleLabel(form.role)} (unchanged)</option>
              ) : null}
              {roles.map((role) => (
                <option key={role} value={role}>
                  {roleLabel(role)}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Job title" htmlFor="title">
            <Input
              id="title"
              placeholder="e.g. Sales Manager"
              value={form.title}
              onChange={(event) => set("title", event.target.value)}
            />
          </Field>

          <Field
            label="Addressed as"
            htmlFor="honorific"
            hint="Optional. Shown after their name across the portal. Set it here rather than guessing — nothing infers it."
          >
            <Select
              id="honorific"
              value={form.honorific}
              onChange={(event) => set("honorific", event.target.value)}
            >
              <option value="">By name</option>
              <option value="SIR">Sir</option>
              <option value="MAAM">Ma&rsquo;am</option>
            </Select>
          </Field>

          <Field label="Phone" htmlFor="phone" error={phoneError(form.phone) ?? undefined}>
            <Input
              id="phone"
              value={form.phone}
              onChange={(event) => set("phone", event.target.value)}
            />
          </Field>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field
            label="Reports to"
            htmlFor="manager"
            hint="Only people you can act on appear here."
          >
            <Select
              id="manager"
              value={form.manager_id}
              onChange={(event) => set("manager_id", event.target.value)}
            >
              <option value="">Nobody (unassigned)</option>
              {managerOptions.map((manager) => (
                <option key={manager.id} value={manager.id}>
                  {manager.name} · {roleLabel(manager.role)}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Team" htmlFor="team" hint="A label for grouping. It grants nothing.">
            <Select
              id="team"
              value={form.team_id}
              onChange={(event) => set("team_id", event.target.value)}
            >
              <option value="">No team</option>
              {editing &&
              user?.team_id &&
              !teams.some((team) => team.id === user.team_id) &&
              teams.length > 0 ? (
                <option value={user.team_id}>{user.team_name ?? "Current team"} (inactive)</option>
              ) : null}
              {teams.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {editing ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {actorIsAdmin ? (
              <Field
                label="Heads department"
                htmlFor="heads-department"
                hint="Grants read access to that department's feedback. One head per department."
              >
                <Select
                  id="heads-department"
                  value={form.heads_department_id}
                  onChange={(event) => set("heads_department_id", event.target.value)}
                >
                  <option value="">None</option>
                  {user?.heads_department_id &&
                  !departments.some((d) => d.id === user.heads_department_id) &&
                  departments.length > 0 ? (
                    <option value={user.heads_department_id}>
                      {user.heads_department_name ?? "Current department"}
                    </option>
                  ) : null}
                  {departments.map((department) => (
                    <option key={department.id} value={department.id}>
                      {department.name}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}

            <Field
              label="Account status"
              htmlFor="status"
              hint={
                (user?.direct_report_count ?? 0) > 0 && form.status === "inactive"
                  ? "Their direct reports need a new manager first — use Deactivate to move them."
                  : "Inactive accounts cannot sign in. Nothing is deleted."
              }
            >
              <Select
                id="status"
                value={form.status}
                onChange={(event) => set("status", event.target.value as FormState["status"])}
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </Select>
            </Field>
          </div>
        ) : null}

        {error ? (
          <InlineError>
            {code && ERROR_HINTS[code] ? `${ERROR_HINTS[code]} ` : ""}
            {errorMessage(error)}
          </InlineError>
        ) : null}
      </form>
    </Modal>
  );
}
