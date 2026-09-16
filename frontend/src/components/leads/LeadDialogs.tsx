"use client";

import { Undo2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { SendRequestButton } from "@/components/customers/SendRequestDialog";
import { InlineError, Spinner } from "@/components/ui/Feedback";
import { Field, Input, Select, Textarea } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { ApiError, api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  CLOSED_LEAD_STATUSES,
  LEAD_STATUS_LABELS,
  LEAD_STATUS_TONE,
  NEXT_STATUSES,
} from "@/lib/leads";
import { isAdmin } from "@/lib/roles";
import { formatDateTime } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { LeadDetail, LeadStatus, User } from "@/types/api";
import { phoneError } from "@/lib/phone";
import { useAuth } from "@/lib/auth";

/* ---------------------------------------------------------- create lead */
export function CreateLeadDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [assignees, setAssignees] = useState<User[]>([]);
  const [form, setForm] = useState({
    name: "",
    company_name: "",
    mobile: "",
    email: "",
    city: "",
    requirement: "",
    priority: "MEDIUM",
    assigned_to_user_id: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // The assignee list is the server's answer to "who may I act on?", so the
  // dropdown cannot offer somebody the API would refuse.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    api.users
      .actionable(controller.signal)
      .then(setAssignees)
      .catch(() => {
        /* the select stays empty; the API still enforces the rule */
      });
    return () => controller.abort();
  }, [open]);

  function set<K extends keyof typeof form>(key: K, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.leads.create({
        name: form.name,
        assigned_to_user_id: form.assigned_to_user_id,
        company_name: form.company_name || null,
        mobile: form.mobile || null,
        email: form.email || null,
        city: form.city || null,
        requirement: form.requirement || null,
        priority: form.priority as "LOW" | "MEDIUM" | "HIGH",
      });
      toast.success("Lead assigned", `${form.name} is now on their list.`);
      onSaved();
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
      size="lg"
      title="Assign a lead"
      description="The assignee is notified straight away, in the same transaction."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" form="lead-form" loading={saving}>
            Assign lead
          </Button>
        </>
      }
    >
      <form id="lead-form" onSubmit={submit} className="space-y-4" noValidate>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Contact name" htmlFor="lead-name" required>
            <Input
              id="lead-name"
              required
              minLength={2}
              value={form.name}
              onChange={(event) => set("name", event.target.value)}
            />
          </Field>
          <Field label="Company" htmlFor="lead-company">
            <Input
              id="lead-company"
              value={form.company_name}
              onChange={(event) => set("company_name", event.target.value)}
            />
          </Field>
          <Field
            label="Mobile"
            htmlFor="lead-mobile"
            error={phoneError(form.mobile) ?? undefined}
          >
            <Input
              id="lead-mobile"
              inputMode="numeric"
              autoComplete="tel"
              placeholder="10 digits"
              value={form.mobile}
              onChange={(event) => set("mobile", event.target.value)}
            />
          </Field>
          <Field label="Email" htmlFor="lead-email">
            <Input
              id="lead-email"
              type="email"
              value={form.email}
              onChange={(event) => set("email", event.target.value)}
            />
          </Field>
        </div>

        <Field label="Requirement" htmlFor="lead-requirement">
          <Textarea
            id="lead-requirement"
            value={form.requirement}
            onChange={(event) => set("requirement", event.target.value)}
            placeholder="What are they looking for?"
          />
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field
            label="Assign to"
            htmlFor="lead-assignee"
            required
            hint="Only people you can act on."
          >
            <Select
              id="lead-assignee"
              required
              value={form.assigned_to_user_id}
              onChange={(event) => set("assigned_to_user_id", event.target.value)}
            >
              <option value="" disabled>
                Choose a person…
              </option>
              {assignees.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Priority" htmlFor="lead-priority">
            <Select
              id="lead-priority"
              value={form.priority}
              onChange={(event) => set("priority", event.target.value)}
            >
              <option value="LOW">Low</option>
              <option value="MEDIUM">Medium</option>
              <option value="HIGH">High</option>
            </Select>
          </Field>
        </div>

        {error ? <InlineError>{errorMessage(error)}</InlineError> : null}
      </form>
    </Modal>
  );
}

/* ---------------------------------------------------------- lead detail */
export function LeadDetailDialog({
  leadId,
  onClose,
  onChanged,
}: {
  leadId: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  const [lead, setLead] = useState<LeadDetail | null>(null);
  // The parent remounts this per lead, so the initialiser is the load state —
  // setting it inside the effect would be a cascading render.
  const [loading, setLoading] = useState(true);
  const [remark, setRemark] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!leadId) return;
    const controller = new AbortController();
    api.leads
      .get(leadId, controller.signal)
      .then(setLead)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [leadId]);

  // Whose log this is. The API is the enforcement; this decides what to
  // offer, so nobody is handed a button that can only 403.
  const isAssignee = Boolean(lead && user && lead.assigned_to_user_id === user.id);

  /**
   * The stage the user has PICKED but not yet saved.
   *
   * Selecting is not saving. Clicking "Converted" used to write it
   * immediately, which meant a mis-click was a permanent stage change on a
   * pipeline with no way back. Now it stages the choice, the remark becomes
   * required, and nothing is written until Update lead is pressed.
   */
  const [chosen, setChosen] = useState<LeadStatus | null>(null);
  const canUpdate = Boolean(chosen) && remark.trim().length > 0;

  async function move() {
    if (!lead || !chosen) return;
    setBusy(true);
    setError(null);
    try {
      setLead(await api.leads.setStatus(lead.id, chosen, remark.trim()));
      setRemark("");
      setChosen(null);
      toast.success("Lead updated", `Moved to ${LEAD_STATUS_LABELS[chosen]}.`);
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  async function reopen() {
    if (!lead) return;
    setBusy(true);
    setError(null);
    try {
      setLead(await api.leads.reopen(lead.id, remark.trim()));
      setRemark("");
      setChosen(null);
      toast.success("Lead reopened", "It is back at New, and the change is on the timeline.");
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  async function log(activityType: string) {
    if (!lead) return;
    setBusy(true);
    setError(null);
    try {
      setLead(
        await api.leads.addActivity(lead.id, {
          activity_type: activityType,
          remark: remark.trim(),
        }),
      );
      setRemark("");
      toast.success("Logged");
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setBusy(false);
    }
  }

  const detail = error instanceof ApiError ? error.message : null;

  return (
    <Modal
      open={Boolean(leadId)}
      onClose={onClose}
      size="xl"
      title={lead?.name ?? "Lead"}
      description={
        lead
          ? `${lead.company_name ?? "No company"} · assigned to ${
              lead.assigned_to_name ?? "nobody"
            }`
          : undefined
      }
    >
      {loading && !lead ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : lead ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={LEAD_STATUS_TONE[lead.status]}>
              {LEAD_STATUS_LABELS[lead.status]}
            </Badge>
            <Badge className="border-line bg-surface-2 text-muted">
              {lead.priority} priority
            </Badge>
            {lead.mobile ? (
              <a
                href={`tel:${lead.mobile.replace(/\s+/g, "")}`}
                className="text-[12.5px] text-brand-700 hover:underline dark:text-brand-300"
              >
                {lead.mobile}
              </a>
            ) : null}
          </div>

          {lead.requirement ? (
            <p className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-[13px] leading-relaxed text-muted">
              {lead.requirement}
            </p>
          ) : null}

          {/* --------------------------------------------- move it on */}
          <div>
            <p className="mb-2 text-[12.5px] font-semibold text-content">Move this lead</p>
            <Field
              label="Remark"
              htmlFor="lead-remark"
              required
              hint="Required. A stage that moved without a reason tells the next person nothing."
            >
              <Textarea
                id="lead-remark"
                value={remark}
                onChange={(event) => setRemark(event.target.value)}
                placeholder="What happened?"
                className="min-h-16"
              />
            </Field>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {NEXT_STATUSES[lead.status].map((status) => (
                <button
                  key={status}
                  type="button"
                  disabled={busy}
                  aria-pressed={chosen === status}
                  onClick={() => setChosen(chosen === status ? null : status)}
                  className={cn(
                    "rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors",
                    chosen === status
                      ? "border-brand-600 bg-brand-600 text-white"
                      : "border-line bg-surface text-content hover:bg-surface-hover",
                  )}
                >
                  {LEAD_STATUS_LABELS[status]}
                </button>
              ))}
              {NEXT_STATUSES[lead.status].length === 0 ? (
                <p className="text-[12.5px] text-subtle">
                  This stage is final — nothing follows it.
                </p>
              ) : null}
            </div>

            {/*
              The escape hatch, for administrators only.

              CONVERTED, LOST and JUNK are final for everyone who works leads,
              which is what makes the pipeline mean anything. A mis-click still
              has to be fixable, so an admin can send it back to the start -
              and that reopening is itself recorded, rather than erasing what
              came before it the way Undo used to.
            */}
            {CLOSED_LEAD_STATUSES.includes(lead.status) && isAdmin(user?.role) ? (
              <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={busy}
                  disabled={remark.trim().length === 0}
                  onClick={() => void reopen()}
                >
                  Reopen lead
                </Button>
                <span className="text-[12.5px] text-muted">
                  {remark.trim()
                    ? "Sends it back to New. The change is recorded."
                    : "Say why, then reopen."}
                </span>
              </div>
            ) : null}

            {/* Nothing is written until this is pressed, and it cannot be
                pressed without a reason. */}
            {NEXT_STATUSES[lead.status].length > 0 ? (
              <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
                <Button size="sm" loading={busy} disabled={!canUpdate} onClick={() => void move()}>
                  Update lead
                </Button>
                {chosen ? (
                  <span className="text-[12.5px] text-muted">
                    {remark.trim()
                      ? `Will move to ${LEAD_STATUS_LABELS[chosen]}.`
                      : "Add a remark to save this change."}
                  </span>
                ) : (
                  <span className="text-[12.5px] text-subtle">
                    Choose the next stage.
                  </span>
                )}
              </div>
            ) : null}

            {/*
              The log belongs to the person the lead is assigned to. A manager
              can still read it and still move the stage above; what they
              cannot do is write a first-hand note on somebody else's
              conversation. The API refuses it either way - this just stops
              offering a button that would 403.
            */}
            {isAssignee ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {["CALL", "WHATSAPP", "EMAIL", "MEETING", "NOTE"].map((type) => (
                  <Button
                    key={type}
                    size="sm"
                    variant="subtle"
                    // Same rule as a stage change: an entry saying "called"
                    // and nothing else is a tick box, not a record.
                    disabled={busy || remark.trim().length === 0}
                    title={
                      remark.trim() ? undefined : "Write a remark first"
                    }
                    onClick={() => void log(type)}
                  >
                    Log {type.toLowerCase()}
                  </Button>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-[12.5px] text-subtle">
                Only {lead.assigned_to_name ?? "the assignee"} can log activity
                on this lead.
              </p>
            )}
          </div>

          {/* ------------------------------------------------ feedback */}
          {lead.status === "CONVERTED" ? (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
              <p className="mr-auto text-[12.5px] text-muted">
                Completed — eligible for a feedback ask.
              </p>
              <SendRequestButton
                customerId={lead.id}
                customerName={lead.name}
                subjectType="lead"
                label="Send feedback request"
                onSent={onChanged}
              />
            </div>
          ) : null}

          {error ? <InlineError>{detail ?? errorMessage(error)}</InlineError> : null}

          {/* ---------------------------------------------- timeline */}
          <div>
            <p className="mb-2 text-[12.5px] font-semibold text-content">
              History ({lead.activities.length})
            </p>
            <ol className="space-y-2.5 border-l border-line pl-4">
              {lead.activities.map((activity) => (
                <li
                  key={activity.id}
                  className={cn("relative flex items-start gap-2", activity.undone_at && "opacity-60")}
                >
                  <span
                    className="absolute -left-[1.4rem] top-1.5 size-2 rounded-full bg-line-strong"
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-[13px] text-content">
                      <span className={cn(activity.undone_at && "line-through")}>
                        {activity.activity_type === "STATUS_CHANGED"
                          ? `${activity.from_status} → ${activity.to_status}`
                          : activity.activity_type.replace(/_/g, " ").toLowerCase()}
                      </span>
                      {activity.undone_at ? (
                        <Badge className="border-line bg-surface-2 text-subtle">
                          <Undo2 className="size-3" aria-hidden />
                          Undone
                        </Badge>
                      ) : null}
                    </p>
                    {activity.remark ? (
                      <p className="text-[12.5px] text-muted">{activity.remark}</p>
                    ) : null}
                    <p className="text-[11.5px] text-subtle">
                      {activity.actor_name ?? "System"} ·{" "}
                      {formatDateTime(activity.created_at)}
                    </p>
                  </div>

                  {/*
                    Undo was removed on request. Status changes are governed by
                    the state machine now, and the timeline is an append-only
                    record of what actually happened - including the entries
                    somebody wishes they had not made.
                  */}
                </li>
              ))}
            </ol>
          </div>
        </div>
      ) : error ? (
        <InlineError>{errorMessage(error)}</InlineError>
      ) : null}
    </Modal>
  );
}
