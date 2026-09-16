"use client";

import { Save } from "lucide-react";
import { useState } from "react";

import { AssistantStatus } from "@/components/admin/AssistantStatus";
import { SapImportButton } from "@/components/admin/SapImportButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { ErrorState, InlineError, Skeleton } from "@/components/ui/Feedback";
import { Field, Input, Textarea } from "@/components/ui/Form";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isSuperAdmin } from "@/lib/roles";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";

interface SettingSpec {
  key: string;
  label: string;
  hint: string;
  type: "number" | "text" | "url" | "template";
  step?: string;
}

const FEEDBACK_SETTINGS: SettingSpec[] = [
  {
    key: "feedback.rating_scale_max",
    label: "Rating scale maximum",
    hint: "Every imported rating is normalised onto this scale.",
    type: "number",
  },
  {
    key: "feedback.alert_threshold",
    label: "Alert threshold",
    hint: "A department averaging below this is flagged.",
    type: "number",
    step: "0.1",
  },
  {
    key: "feedback.alert_min_responses",
    label: "Minimum responses",
    hint: "Below this, a low average is a bad day rather than a bad department.",
    type: "number",
  },
  {
    key: "feedback.alert_window_days",
    label: "Rolling window (days)",
    hint: "How far back the average looks.",
    type: "number",
  },
];

const COMPANY_SETTINGS: SettingSpec[] = [
  {
    key: "reference.default_followup_days",
    label: "Default follow-up gap (days)",
    hint: "Suggested when a customer says “ask me later”.",
    type: "number",
  },
  {
    key: "company.feedback_form_url",
    label: "Feedback form link",
    hint: "The Google Form customers fill in. Until this is set, feedback requests have no link to send.",
    type: "url",
  },
  // Without this the link goes out with no reference code in it, so no
  // response can be matched to the ask that caused it - only to a phone
  // number, and only when exactly one asked lead has that number. It was
  // settable through the API alone, which meant the form could be "set up"
  // from this page and still never match a single response.
  {
    key: "feedback.form_reference_entry_id",
    label: "Form field for the reference code",
    hint: "In Google Forms: ⋮ → Get pre-filled link, type anything into the reference-code question, copy the link, and paste it here (or just its entry.123456 part). The portal then pre-fills each customer's code so their answer matches automatically.",
    type: "text",
  },
  {
    key: "company.name",
    label: "Company name",
    hint: "How the company signs off in outgoing messages.",
    type: "text",
  },
];

/**
 * The text the team sends when asking for feedback.
 *
 * Edited here so nobody retypes it per customer, and so the wording can change
 * without a deploy. {placeholders} are filled in per customer on the server.
 */
const TEMPLATE_GROUPS: { title: string; specs: SettingSpec[] }[] = [
  {
    title: "Asking for feedback",
    specs: [
      {
        key: "message.feedback_whatsapp",
        label: "WhatsApp message",
        hint: "",
        type: "template",
      },
      {
        key: "message.feedback_email_subject",
        label: "Email subject",
        hint: "",
        type: "text",
      },
      {
        key: "message.feedback_email_body",
        label: "Email body",
        hint: "",
        type: "template",
      },
    ],
  },
];

const MESSAGE_TEMPLATES: SettingSpec[] = TEMPLATE_GROUPS.flatMap(
  (group) => group.specs,
);

const PLACEHOLDERS: { token: string; meaning: string }[] = [
  { token: "{customer_name}", meaning: "the lead's name" },
  { token: "{sap_code}", meaning: "archived SAP customers only — blank for leads" },
  { token: "{sender_name}", meaning: "whoever is sending it" },
  { token: "{our_company}", meaning: "the company name above" },
  { token: "{link}", meaning: "the feedback form link" },
];

export default function SettingsPage() {
  const toast = useToast();
  const { data, error, loading, reload } = useAsync(
    (signal) => api.admin.settings(signal),
    [],
  );

  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<Error | null>(null);
  const { user } = useAuth();

  // Initialised from the server the first time it lands; after that the form
  // is the source of truth until it is saved.
  const values =
    draft ??
    (data
      ? Object.fromEntries(
          Object.entries(data.values).map(([key, value]) => [key, String(value ?? "")]),
        )
      : null);

  function set(key: string, value: string) {
    setDraft({ ...(values ?? {}), [key]: value });
  }

  async function save() {
    if (!values) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload: Record<string, unknown> = {};
      for (const spec of [
        ...FEEDBACK_SETTINGS,
        ...COMPANY_SETTINGS,
        ...MESSAGE_TEMPLATES,
      ]) {
        const raw = values[spec.key] ?? "";
        payload[spec.key] = spec.type === "number" ? Number(raw) : raw;
      }
      await api.admin.updateSettings(payload);
      toast.success("Settings saved", "Alert thresholds take effect immediately.");
      setDraft(null);
      reload();
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setSaving(false);
    }
  }

  async function reevaluate() {
    try {
      const changed = await api.feedback.evaluateAlerts();
      toast.success(
        "Alerts re-evaluated",
        `${changed.raised.length} raised, ${changed.resolved.length} resolved.`,
      );
    } catch (cause) {
      toast.error("Could not re-evaluate", errorMessage(cause));
    }
  }

  if (error) {
    return (
      <>
        <PageHeader title="Settings" />
        <Card>
          <ErrorState error={error} onRetry={reload} />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="Runtime configuration an administrator can change without a deploy. Every change is written to the audit trail."
        actions={
          <Button onClick={save} loading={saving} disabled={!values}>
            <Save className="size-4" aria-hidden />
            Save changes
          </Button>
        }
      />

      {loading || !values ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Skeleton className="h-72 rounded-card" />
          <Skeleton className="h-72 rounded-card" />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="block">
              <CardTitle>Feedback alerts</CardTitle>
              <CardDescription>
                When a department is flagged, and how much evidence it takes.
              </CardDescription>
            </CardHeader>
            <CardBody className="space-y-4">
              {FEEDBACK_SETTINGS.map((spec) => (
                <Field key={spec.key} label={spec.label} htmlFor={spec.key} hint={spec.hint}>
                  <Input
                    id={spec.key}
                    type={spec.type === "number" ? "number" : "text"}
                    step={spec.step}
                    value={values[spec.key] ?? ""}
                    onChange={(event) => set(spec.key, event.target.value)}
                  />
                </Field>
              ))}
              <Button variant="secondary" size="sm" onClick={reevaluate}>
                Re-evaluate alerts now
              </Button>
            </CardBody>
          </Card>

          <Card>
            <CardHeader className="block">
              <CardTitle>Company</CardTitle>
              <CardDescription>Links and defaults used across the portal.</CardDescription>
            </CardHeader>
            <CardBody className="space-y-4">
              {COMPANY_SETTINGS.map((spec) => (
                <Field key={spec.key} label={spec.label} htmlFor={spec.key} hint={spec.hint}>
                  <Input
                    id={spec.key}
                    type={spec.type === "number" ? "number" : "text"}
                    value={values[spec.key] ?? ""}
                    onChange={(event) => set(spec.key, event.target.value)}
                  />
                </Field>
              ))}
            </CardBody>
          </Card>

          {isSuperAdmin(user?.role) ? (
            <Card>
              <CardHeader className="block">
                <CardTitle>SAP data</CardTitle>
                <CardDescription>
                  Import customers from the BDE &amp; Sales SAP workbook. New rows are
                  added and changed details refreshed; nothing is duplicated.
                </CardDescription>
              </CardHeader>
              <CardBody>
                <SapImportButton />
              </CardBody>
            </Card>
          ) : null}

          <AssistantStatus />

          <Card className="lg:col-span-2">
            <CardHeader className="block">
              <CardTitle>Message templates</CardTitle>
              <CardDescription>
                What the team sends when asking a customer for feedback. The
                link is added automatically — nobody types it.
              </CardDescription>
            </CardHeader>
            <CardBody className="space-y-4">
              <dl className="flex flex-wrap gap-x-4 gap-y-1.5 rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-[12.5px]">
                {PLACEHOLDERS.map((placeholder) => (
                  <div key={placeholder.token} className="flex items-center gap-1.5">
                    <dt className="font-mono text-[12px] text-content">
                      {placeholder.token}
                    </dt>
                    <dd className="text-subtle">{placeholder.meaning}</dd>
                  </div>
                ))}
              </dl>

              {/* One column per purpose, so the WhatsApp text and the email
                  that say the same thing sit together. */}
              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                {TEMPLATE_GROUPS.map((group) => (
                  <section key={group.title} className="space-y-4">
                    <h3 className="text-[13px] font-semibold text-content">
                      {group.title}
                    </h3>
                    {group.specs.map((spec) => (
                      <Field key={spec.key} label={spec.label} htmlFor={spec.key}>
                        {spec.type === "template" ? (
                          <Textarea
                            id={spec.key}
                            rows={9}
                            value={values[spec.key] ?? ""}
                            onChange={(event) => set(spec.key, event.target.value)}
                          />
                        ) : (
                          <Input
                            id={spec.key}
                            value={values[spec.key] ?? ""}
                            onChange={(event) => set(spec.key, event.target.value)}
                          />
                        )}
                      </Field>
                    ))}
                  </section>
                ))}
              </div>
            </CardBody>
          </Card>
        </div>
      )}

      {saveError ? (
        <div className="mt-4">
          <InlineError>{errorMessage(saveError)}</InlineError>
        </div>
      ) : null}
    </>
  );
}
