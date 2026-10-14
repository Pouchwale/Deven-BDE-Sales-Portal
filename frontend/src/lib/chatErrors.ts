import { ApiError } from "@/lib/api";

/**
 * Why the assistant could not answer, in terms a person can act on.
 *
 * "Something went wrong" for everything leaves people pressing the button
 * again for a problem that will not go away (no key configured) and giving up
 * on one that would (a busy minute). Each kind gets its own title, and only
 * the kinds where trying again can help offer a retry.
 *
 * Messages are the server's own user-safe wording where it sent one - the
 * backend never puts provider responses, keys or tracebacks in them - and a
 * plain default otherwise.
 */
export type ChatFailureKind =
  | "offline"
  | "provider"
  | "auth"
  | "rate_limit"
  | "invalid"
  | "config"
  | "unknown";

export interface ChatFailure {
  kind: ChatFailureKind;
  title: string;
  message: string;
  retryable: boolean;
  /** The server no longer has this conversation; a retry starts a new one. */
  conversationGone?: boolean;
}

const TITLES: Record<ChatFailureKind, string> = {
  offline: "Can't reach the portal server",
  provider: "The AI service isn't responding",
  auth: "Your session has expired",
  rate_limit: "Too many requests",
  invalid: "That request couldn't be processed",
  config: "The assistant isn't configured",
  unknown: "The assistant couldn't answer",
};

const DEFAULTS: Record<ChatFailureKind, string> = {
  offline: "Check your connection, then try again. The server may also be waking up.",
  provider: "The AI provider is slow or unavailable right now. Try again in a moment.",
  auth: "Sign in again to keep using the assistant.",
  rate_limit: "Give it a moment, then try again.",
  invalid: "Try rephrasing, or start a new conversation.",
  config: "An administrator needs to finish setting it up. Admin › Settings shows what is missing.",
  unknown: "Try again. If it keeps happening, start a new conversation.",
};

function failure(
  kind: ChatFailureKind,
  message: string | null | undefined,
  extra: Partial<ChatFailure> = {},
): ChatFailure {
  return {
    kind,
    title: TITLES[kind],
    message: message?.trim() || DEFAULTS[kind],
    retryable: kind !== "auth" && kind !== "config",
    ...extra,
  };
}

/** An `error` event from inside the stream. Codes from services/chat/client.py. */
export function failureFromEvent(code: string, message: string): ChatFailure {
  switch (code) {
    case "NOT_CONFIGURED":
      return failure("config", message);
    case "RATE_LIMITED":
      return failure("rate_limit", message);
    case "TIMEOUT":
    case "UNAVAILABLE":
      return failure("provider", message);
    default:
      return failure("unknown", message);
  }
}

/** Anything thrown before or while reading the stream. */
export function failureFromError(error: unknown): ChatFailure {
  if (!(error instanceof ApiError)) return failure("offline", null);

  const { status, code, message } = error;
  if (status === 0 || code === "NETWORK_ERROR") return failure("offline", null);
  if (status === 401) return failure("auth", null);
  if (status === 429) return failure("rate_limit", message);
  if (status === 404) {
    return failure("invalid", "That conversation no longer exists. Retrying starts a new one.", {
      conversationGone: true,
    });
  }
  if (status === 400 || status === 422) return failure("invalid", message);
  // 503 from our own API means the feature is switched off or keyless;
  // 502/504 (and a 503 without our envelope) are the host in front of it.
  if (status === 503 && code !== "UNKNOWN") return failure("config", message);
  if (status === 502 || status === 503 || status === 504) return failure("offline", null);
  return failure("unknown", null);
}

/** The stream closed without saying it was done. */
export const STREAM_CUT: ChatFailure = failure(
  "offline",
  "The connection dropped before the answer finished. Try again.",
);
