"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { InlineError } from "@/components/ui/Feedback";
import { Field, Input, Select } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { ApiError, api, errorMessage } from "@/lib/api";
import { ERROR_HINTS, roleLabel } from "@/lib/roles";
import { useToast } from "@/lib/toast";
import type { Role, Team, User, UserDetail, Honorific } from "@/types/api";
import { phoneError } from "@/lib/phone";

interface FormState {
  name: string;
  email: string;
  password: string;
  role: string;
  title: string;
  honorific: string;
  phone: string;
  manager_id: string;
  team_id: string;
}

const EMPTY: FormState = {
  name: "",
  email: "",
  password: "",
  role: "",
  title: "",
  honorific: "",
  phone: "",
  manager_id: "",
  team_id: "",
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

  const [form, setForm] = useState<FormState>(() =>
    user
      ? {
          name: user.name,
          email: user.email,
          password: "",
          role: user.role,
          title: user.title ?? "",
          honorific: user.honorific ?? "",
          phone: user.phone ?? "",
          manager_id: user.manager_id ?? "",
          team_id: user.team_id ?? "",
        }
      : EMPTY,
  );
  const [roles, setRoles] = useState<Role[]>([]);
  const [managers, setManagers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
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
    ])
      .then(([roleResponse, actionable, teamList]) => {
        setRoles(roleResponse.roles);
        setManagers(actionable);
        setTeams(teamList);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      });

    return () => controller.abort();
  }, [open]);

  const managerOptions = useMemo(() => {
    const options = managers.filter((candidate) => candidate.id !== user?.id);
    // An admin may also place someone directly under themselves.
    if (!options.some((option) => option.id === actor.id)) {
      options.unshift({ ...actor, name: `${actor.name} (you)` });
    }
    return options;
  }, [managers, actor, user?.id]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);

    try {
      if (editing && user) {
        await api.users.update(user.id, {
          name: form.name,
          email: form.email,
          title: form.title || null,
          honorific: (form.honorific || null) as Honorific | null,
          phone: form.phone || null,
          role: form.role as Role,
          manager_id: form.manager_id || null,
          team_id: form.team_id || null,
        });
        toast.success("Saved", `${form.name} has been updated.`);
      } else {
        await api.users.create({
          name: form.name,
          email: form.email,
          password: form.password,
          role: form.role as Role,
          title: form.title || null,
          honorific: (form.honorific || null) as Honorific | null,
          phone: form.phone || null,
          manager_id: form.manager_id || null,
          team_id: form.team_id || null,
        });
        toast.success(
          "Account created",
          `${form.name} must change the password at first sign-in.`,
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
          ? "Role and reporting-line changes are re-checked against your own authority."
          : "The account starts with a password you set, which they must replace at first sign-in."
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" form="user-form" loading={saving}>
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
              value={form.email}
              onChange={(event) => set("email", event.target.value)}
            />
          </Field>
        </div>

        {editing ? null : (
          <Field
            label="Initial password"
            htmlFor="password"
            required
            hint="At least 8 characters. They must change it at first sign-in."
          >
            <Input
              id="password"
              type="text"
              required
              minLength={8}
              autoComplete="new-password"
              value={form.password}
              onChange={(event) => set("password", event.target.value)}
            />
          </Field>
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
              {teams.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field label="Phone" htmlFor="phone" error={phoneError(form.phone) ?? undefined}>
          <Input
            id="phone"
            value={form.phone}
            onChange={(event) => set("phone", event.target.value)}
          />
        </Field>

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
