"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { InlineError } from "@/components/ui/Feedback";
import { Field, Input, Select, Textarea } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { ApiError, api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useToast } from "@/lib/toast";
import type { AskableAccount, FollowUpDue, ReferenceOutcome } from "@/types/api";
import { phoneError } from "@/lib/phone";

/** Whoever is being asked: a converted lead (archived SAP asks are history). Both carry
 *  `subject_type`/`subject_id`, so this dialog does not care which. */
export type ReferenceSubject = Pick<
  AskableAccount | FollowUpDue,
  "subject_type" | "subject_id" | "subject_name"
>;

function inDays(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

/**
 * Recording one ask.
 *
 * Three outcomes, two of which END the conversation:
 *
 *   Gave a reference  -> completed, and a reference was received
 *   Not shared        -> completed, nothing to chase, no reference received
 *   Not right now     -> still open, comes back on the follow-up date
 *
 * "Not shared" exists because without it a customer who had nobody to refer
 * had to be filed as "not right now", and then reappeared in the follow-up
 * queue forever as work nobody could ever close.
 *
 * The outcomes need different information, so the form changes shape
 * rather than showing every field and hoping. A "no" without a date to ask
 * again on is refused — by this form and by the database CHECK behind it.
 */
const OUTCOMES: {
  value: ReferenceOutcome;
  label: string;
  hint: string;
  tone: string;
}[] = [
  {
    value: "YES",
    label: "Gave a reference",
    hint: "Capture who they referred.",
    tone: "border-success bg-success-soft",
  },
  {
    value: "NOT_SHARED",
    label: "Not shared",
    hint: "Asked and answered. Nothing to chase.",
    tone: "border-danger bg-danger-soft",
  },
  {
    value: "NO",
    label: "Not right now",
    hint: "Set a date to ask again.",
    tone: "border-warning bg-warning-soft",
  },
];


export function RecordReferenceDialog({
  open,
  onClose,
  onSaved,
  subject,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  subject: ReferenceSubject | null;
}) {
  const toast = useToast();

  const [outcome, setOutcome] = useState<ReferenceOutcome>("YES");
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [mobile, setMobile] = useState("");
  const [email, setEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [askAgain, setAskAgain] = useState(() => inDays(30));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const subjectName = subject?.subject_name;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!subject) return;

    setSaving(true);
    setError(null);
    try {
      await api.references.record({
        // Exactly one, matching the CHECK behind the table. Every askable
        // subject is a converted lead now; `customer_id` stays in the body
        // because the archived SAP references still point at it.
        customer_id: null,
        lead_id: subject.subject_id,
        outcome,
        referred_name: outcome === "YES" ? name || null : null,
        referred_company: outcome === "YES" ? company || null : null,
        referred_mobile: outcome === "YES" ? mobile || null : null,
        referred_email: outcome === "YES" ? email || null : null,
        // Only "not right now" owes a date. The other two are finished.
        next_reference_date: outcome === "NO" ? askAgain : null,
        notes: notes || null,
      });
      toast.success(
        outcome === "YES"
          ? "Reference recorded"
          : outcome === "NOT_SHARED"
            ? "Marked as not shared"
            : "Follow-up scheduled",
        outcome === "YES"
          ? `${subjectName} gave a reference.`
          : `You will be reminded to ask ${subjectName} again.`,
      );
      onSaved();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setSaving(false);
    }
  }

  const detail =
    error instanceof ApiError && Array.isArray(error.details.fields)
      ? (error.details.fields as { message: string }[])[0]?.message
      : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={`Ask ${subjectName ?? "this customer"} for a reference`}
      description="Recorded against you, and it moves the customer's reference status."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" form="reference-form" loading={saving}>
            Record
          </Button>
        </>
      }
    >
      <form id="reference-form" onSubmit={submit} className="space-y-4" noValidate>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {OUTCOMES.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setOutcome(option.value)}
              className={cn(
                "rounded-xl border p-3 text-left transition-colors",
                outcome === option.value
                  ? option.tone
                  : "border-line bg-surface hover:bg-surface-hover",
              )}
            >
              <span className="block text-[13.5px] font-semibold text-content">
                {option.label}
              </span>
              <span className="mt-0.5 block text-[12px] text-muted">
                {option.hint}
              </span>
            </button>
          ))}
        </div>

        {outcome === "YES" ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Referred person" htmlFor="referred-name">
              <Input
                id="referred-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Their contact's name"
              />
            </Field>
            <Field label="Company" htmlFor="referred-company">
              <Input
                id="referred-company"
                value={company}
                onChange={(event) => setCompany(event.target.value)}
              />
            </Field>
            <Field
              label="Mobile"
              htmlFor="referred-mobile"
              error={phoneError(mobile) ?? undefined}
            >
              <Input
                id="referred-mobile"
                value={mobile}
                onChange={(event) => setMobile(event.target.value)}
              />
            </Field>
            <Field label="Email" htmlFor="referred-email">
              <Input
                id="referred-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>
            <p className="text-[12px] text-subtle sm:col-span-2">
              At least one of name, company or mobile is needed — a reference with no
              way to reach anybody is not a reference.
            </p>
          </div>
        ) : outcome === "NO" ? (
          // Only "not right now" comes back round, so only "not right now"
          // asks when. "Not shared" is a finished conversation - offering it a
          // follow-up date would contradict the thing it means, and the
          // database refuses to store one anyway.
          <div className="space-y-3">
            <Field
              label="Ask again on"
              htmlFor="ask-again"
              required
              hint="You will get a reminder on this date. It is required — “ask me later” without a date is a lost thread."
            >
              <Input
                id="ask-again"
                type="date"
                required
                value={askAgain}
                onChange={(event) => setAskAgain(event.target.value)}
              />
            </Field>
            <Select
              aria-label="Quick pick"
              value=""
              onChange={(event) =>
                event.target.value && setAskAgain(inDays(Number(event.target.value)))
              }
            >
              <option value="">Quick pick…</option>
              <option value="14">In 2 weeks</option>
              <option value="30">In 1 month</option>
              <option value="90">In 3 months</option>
              <option value="180">In 6 months</option>
            </Select>
          </div>
        ) : null}

        <Field label="Notes" htmlFor="reference-notes">
          <Textarea
            id="reference-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Anything worth remembering for next time."
          />
        </Field>

        {error ? <InlineError>{detail ?? errorMessage(error)}</InlineError> : null}
      </form>
    </Modal>
  );
}
