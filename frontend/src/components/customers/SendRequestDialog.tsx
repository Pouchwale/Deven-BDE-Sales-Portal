"use client";

import {
  AlertTriangle,
  Check,
  Copy,
  Mail,
  MessageCircle,
  Send,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";
import type { ComposedMessage, MessageChannel } from "@/types/api";

/**
 * Compose a feedback request, and open it ready to send.
 *
 * Nobody should be retyping "could you fill in our feedback form, here is the
 * link". The server renders the template with the customer, the sender and the
 * link already filled in; this opens WhatsApp or the mail client with that text
 * in the box, and logs on the customer's timeline that the ask went out.
 *
 * The portal never sends on anyone's behalf — the person presses send in their
 * own app.
 */

const CHANNELS: { key: MessageChannel; label: string; icon: typeof Mail }[] = [
  { key: "WHATSAPP", label: "WhatsApp", icon: MessageCircle },
  { key: "EMAIL", label: "Email", icon: Mail },
];

/** Rebuild the deep link for text the user edited before sending. */
function buildLink(
  message: ComposedMessage,
  subject: string,
  body: string,
): string | null {
  if (message.channel === "WHATSAPP") {
    if (!message.whatsapp_number) return null;
    return `https://wa.me/${message.whatsapp_number}?text=${encodeURIComponent(body)}`;
  }
  if (!message.to) return null;
  // Hand-built rather than URLSearchParams, which encodes a space as "+" —
  // several mail clients paste that through literally.
  return (
    `mailto:${message.to}?subject=${encodeURIComponent(subject)}` +
    `&body=${encodeURIComponent(body)}`
  );
}

function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { key: T; label: string; icon: typeof Mail }[];
  value: T;
  onChange: (key: T) => void;
  label: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="flex gap-1 rounded-xl border border-line bg-surface-2 p-1"
    >
      {options.map((option) => {
        const selected = option.key === value;
        const Icon = option.icon;
        return (
          <button
            key={option.key}
            type="button"
            role="radio"
            aria-checked={selected}
            data-testid={`msg-${option.key.toLowerCase()}`}
            onClick={() => onChange(option.key)}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5",
              "text-[13px] font-medium transition-colors duration-150",
              selected
                ? "bg-surface text-content card-shadow"
                : "text-muted hover:text-content",
            )}
          >
            <Icon className="size-3.5" aria-hidden />
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function SendRequestDialog({
  customerId,
  customerName,
  open,
  onClose,
  onSent,
  subjectType = "customer",
}: {
  customerId: string;
  customerName: string;
  open: boolean;
  onClose: () => void;
  onSent?: () => void;
  subjectType?: "customer" | "lead";
}) {
  const toast = useToast();
  const [channel, setChannel] = useState<MessageChannel>("WHATSAPP");
  // null means "whatever the server rendered". Cleared whenever the channel
  // changes, so switching never carries a stale edit across — done in the
  // handler rather than an effect, which keeps the render pure.
  const [editedBody, setEditedBody] = useState<string | null>(null);
  const [editedSubject, setEditedSubject] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const composed = useAsync(
    (signal) =>
      subjectType === "lead"
        ? api.leads.composeMessage(customerId, channel, signal)
        : api.customers.composeMessage(customerId, "FEEDBACK", channel, signal),
    [customerId, channel, subjectType],
  );

  const message = composed.data;
  const body = editedBody ?? message?.body ?? "";
  const subject = editedSubject ?? message?.subject ?? "";
  const link = message ? buildLink(message, subject, body) : null;

  /**
   * Whether the composed message on screen is for the channel that is
   * selected.
   *
   * `useAsync` keeps the PREVIOUS response while it refetches, so for a moment
   * after switching channel the subject field has already gone (local state)
   * while `message` is still the email one. Pressing send in that window
   * opened the mail client while the UI said WhatsApp - the right text to the
   * right person by entirely the wrong route.
   */
  const composedForThisChannel = message?.channel === channel;
  const ready = Boolean(message?.link_configured && link && composedForThisChannel);

  function switchChannel(next: MessageChannel) {
    setChannel(next);
    setEditedBody(null);
    setEditedSubject(null);
    setCopied(false);
  }

  async function copy() {
    const text = channel === "EMAIL" ? `${subject}\n\n${body}` : body;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Copied", "Paste it wherever you need it.");
    } catch {
      toast.error(
        "Could not copy",
        "Select the text in the box and copy it manually.",
      );
    }
  }

  async function send() {
    if (!message || !link) return;

    setBusy(true);
    try {
      if (channel === "WHATSAPP") {
        // Opened first, from inside the click, or the pop-up blocker eats it.
        window.open(link, "_blank", "noopener,noreferrer");
      } else {
        // mailto hands off to the mail client without leaving the page.
        window.location.href = link;
      }

      if (subjectType === "lead") {
        await api.leads.recordMessageSent(customerId, channel, body);
      } else {
        await api.customers.recordSent(customerId, "FEEDBACK", channel, body);
      }
      toast.success(
        "Feedback request opened",
        "Logged on the timeline. Press send in the app to deliver it.",
      );
      onSent?.();
      onClose();
    } catch (cause) {
      toast.error("Could not log the request", errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const missingLink =
    message && !message.link_configured
      ? "No feedback form link is configured yet. An administrator can set it under Settings."
      : null;

  const missingContact =
    message?.missing_contact === "mobile"
      ? "This customer has no mobile number on file, so WhatsApp cannot be opened."
      : message?.missing_contact === "email"
        ? "This customer has no email address on file, so an email cannot be opened."
        : null;

  const blocker = missingLink ?? missingContact;

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={`Send a request to ${customerName}`}
      description="The message is written for you, link included. Edit it if you like, then open it in the app and press send."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            onClick={copy}
            disabled={!message || composed.loading}
            data-testid="msg-copy"
          >
            {copied ? (
              <Check className="size-3.5 text-success" aria-hidden />
            ) : (
              <Copy className="size-3.5" aria-hidden />
            )}
            Copy message
          </Button>
          <Button
            onClick={send}
            loading={busy}
            disabled={!ready || composed.loading}
            data-testid="msg-send"
            title={blocker ?? undefined}
          >
            <Send className="size-3.5" aria-hidden />
            {channel === "WHATSAPP" ? "Open WhatsApp" : "Open email"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Segmented
          label="How to send it"
          options={CHANNELS}
          value={channel}
          onChange={switchChannel}
        />

        {composed.error ? (
          <p className="text-[13px] font-medium text-danger" role="alert">
            {errorMessage(composed.error)}
          </p>
        ) : null}

        {blocker ? (
          <p
            className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-[13px] text-warning"
            data-testid="msg-blocker"
            role="status"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            {blocker}
          </p>
        ) : null}

        <Field
          label="To"
          hint={
            channel === "WHATSAPP"
              ? "The customer's mobile, as SAP holds it."
              : "The customer's email, as SAP holds it."
          }
        >
          <Input value={message?.to ?? "—"} readOnly aria-readonly />
        </Field>

        {channel === "EMAIL" ? (
          <Field label="Subject" htmlFor="msg-subject">
            <Input
              id="msg-subject"
              value={subject}
              onChange={(event) => setEditedSubject(event.target.value)}
              data-testid="msg-subject"
            />
          </Field>
        ) : null}

        <Field
          label="Message"
          htmlFor="msg-body"
          hint={
            composed.loading
              ? "Composing…"
              : "The link is already in the text. Send it as it is, or reword it."
          }
        >
          <Textarea
            id="msg-body"
            rows={10}
            value={body}
            onChange={(event) => {
              setEditedBody(event.target.value);
              setCopied(false);
            }}
            data-testid="msg-body"
          />
        </Field>
      </div>
    </Modal>
  );
}

/** The button that opens the dialog. Drops into a row, a header or a toolbar. */
export function SendRequestButton({
  customerId,
  customerName,
  onSent,
  size = "sm",
  variant = "secondary",
  label = "Send request",
  subjectType = "customer",
}: {
  customerId: string;
  customerName: string;
  onSent?: () => void;
  size?: "sm" | "md";
  variant?: "primary" | "secondary" | "subtle";
  label?: string;
  subjectType?: "customer" | "lead";
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        size={size}
        variant={variant}
        onClick={() => setOpen(true)}
        data-testid="open-send-request"
        title={
          subjectType === "lead"
            ? "Compose a feedback request"
            : "Compose a feedback or Google review request"
        }
      >
        <Send className="size-3.5" aria-hidden />
        {label}
      </Button>
      {/* Mounted only while open, so each dialog starts from the server's text
          rather than whatever the last one was edited to. */}
      {open ? (
        <SendRequestDialog
          customerId={customerId}
          customerName={customerName}
          open={open}
          onClose={() => setOpen(false)}
          onSent={onSent}
          subjectType={subjectType}
        />
      ) : null}
    </>
  );
}
