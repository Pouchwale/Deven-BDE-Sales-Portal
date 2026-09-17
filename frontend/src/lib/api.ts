/**
 * The single door to the backend.
 *
 * Everything goes through `request`, so cookie credentials, the CSRF header,
 * the error envelope and the "your session expired" path are handled in
 * exactly one place.
 */
import type {
  ApiErrorBody,
  AskableAccount,
  AssignableRoles,
  AssignedByMeCount,
  AuditEvent,
  ChatEvent,
  ChatStatus,
  CommitResult,
  ComposedMessage,
  CreateLeadBody,
  CreateUserBody,
  Conversation,
  ConversationDetail,
  Customer,
  CustomerDetail,
  CustomerStats,
  Dashboard,
  Department,
  DryRunResult,
  Feedback,
  FeedbackAlert,
  FeedbackAnalysis,
  FollowUpDue,
  ImportSummary,
  Lead,
  LeadDetail,
  LeadStats,
  LeadStatus,
  Message,
  MessageChannel,
  MessagePurpose,
  NeedsReviewItem,
  Notification,
  OrgNode,
  Page,
  PasswordSetResult,
  FeedbackPopulation,
  PendingFeedbackItem,
  RecordReferenceBody,
  Reference,
  ReferenceStats,
  SyncEvent,
  SyncResult,
  SyncStatus,
  Team,
  TimelineEntry,
  TokenResponse,
  UpdateUserBody,
  User,
  UserActivityItem,
  UserDetail,
  WorkQueue,
} from "@/types/api";

/**
 * Where the API lives.
 *
 * By default: the same origin that served the page. `/api/*` is proxied to
 * the backend (a Next.js rewrite in development, the reverse proxy in
 * production), which is what lets the session cookie be first-party and
 * HttpOnly. `NEXT_PUBLIC_API_URL` overrides this only when explicitly set.
 */
function resolveApiUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "") ?? "";
}

export const API_URL = resolveApiUrl();

/** Never names an internal address: the person cannot act on it. */
const NETWORK_ERROR_MESSAGE = "Cannot reach the server. Please try again.";

/** Must match the backend's CSRF_COOKIE_NAME. */
const CSRF_COOKIE = "bde_csrf";

/** Where older builds kept a bearer token. Removed once on load. */
const LEGACY_TOKEN_KEY = "bde_portal_token";

/** A failure that carries the backend's machine-readable code. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** True when the only fix is to sign in again. */
  get isAuthFailure(): boolean {
    return this.status === 401;
  }

  get needsPasswordChange(): boolean {
    return this.code === "PASSWORD_CHANGE_REQUIRED";
  }
}

/* --------------------------------------------------------------- session */
/**
 * The session lives in an HttpOnly cookie the page cannot read - by design,
 * so an injected script cannot steal it. What the page CAN read is the CSRF
 * cookie, which it echoes in a header on every state-changing request.
 */
export function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === CSRF_COOKIE) return decodeURIComponent(rest.join("="));
  }
  return null;
}

/** Drop the bearer token older builds stored in localStorage. */
export function clearLegacyToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(LEGACY_TOKEN_KEY);
  } catch {
    // Private windows and hardened browser settings can throw on access.
  }
}

function csrfHeader(method: string): Record<string, string> {
  if (method === "GET" || method === "HEAD") return {};
  const token = readCsrfToken();
  return token ? { "X-CSRF-Token": token } : {};
}

/* ---------------------------------------------------------------- request */
type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const search = params.toString();
  return `${API_URL}${path}${search ? `?${search}` : ""}`;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal } = options;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      signal,
      credentials: "include",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...csrfHeader(method),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    // A dead backend is by far the most common cause here, and "Failed to
    // fetch" tells the user nothing actionable.
    throw new ApiError(0, "NETWORK_ERROR", NETWORK_ERROR_MESSAGE, {});
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const envelope = payload as ApiErrorBody | null;
    const error = envelope?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN",
      error?.message ?? `Request failed with status ${response.status}.`,
      error?.details ?? {},
    );
  }

  return payload as T;
}

export interface SapImportResult {
  files: {
    filename: string;
    total_rows: number;
    customers_created: number;
    customers_updated: number;
    lines_created: number;
    lines_skipped: number;
    leads_created: number;
    owners_changed: number;
    error_count: number;
    errors: { row?: number; customer?: string | null; type?: string; message: string }[];
    unmatched_sales_people: string[];
    not_in_file: string[];
  }[];
  mobiles_dropped: number;
}

export interface SapSyncStatus {
  /** Whether the server has a workbook configured. Its path is never sent. */
  linked: boolean;
  linked_file_name: string | null;
  file_found: boolean;
  file_saved_at: string | null;
  auto_sync: boolean;
  watcher_running: boolean;
  interval_seconds: number;
  in_sync: boolean;
  last_checked_at: string | null;
  last_synced_at: string | null;
  last_result: SapImportResult["files"][number] | null;
  last_error: string | null;
  last_error_at: string | null;
}

/** Multipart upload. Kept separate so `request` stays a JSON-only path — the
 *  browser must set its own multipart boundary, so no Content-Type here. */
async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);

  let response: Response;
  try {
    response = await fetch(buildUrl(path), {
      method: "POST",
      credentials: "include",
      headers: csrfHeader("POST"),
      body: form,
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", NETWORK_ERROR_MESSAGE, {});
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const error = (payload as ApiErrorBody | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN",
      error?.message ?? `Upload failed with status ${response.status}.`,
      error?.details ?? {},
    );
  }
  return payload as T;
}

/* -------------------------------------------------------------------- sse */
/**
 * The assistant's reply, as it arrives.
 *
 * Deliberately not `EventSource`: that can only GET and cannot send the
 * CSRF header a POST needs. So this is `fetch` + a reader, following
 * `upload`'s precedent of stepping around `request` while reusing the same
 * cookie credentials, CSRF header and `ApiError`.
 */
export async function* streamChat(
  message: string,
  conversationId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  let response: Response;
  try {
    response = await fetch(buildUrl("/api/chat/messages"), {
      method: "POST",
      signal,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...csrfHeader("POST"),
      },
      body: JSON.stringify({
        message,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      }),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "NETWORK_ERROR", NETWORK_ERROR_MESSAGE, {});
  }

  // Failures arrive before the stream starts, as an ordinary error envelope.
  if (!response.ok || !response.body) {
    const text = await response.text();
    let error: ApiErrorBody["error"] | undefined;
    try {
      error = (JSON.parse(text) as ApiErrorBody).error;
    } catch {
      /* not JSON - fall through to the generic message */
    }
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN",
      error?.message ?? "The assistant is not responding right now.",
      error?.details ?? {},
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line; anything after the last one is
      // a partial frame that has to wait for the next chunk.
      let split = buffer.indexOf("\n\n");
      while (split !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const event = parseFrame(frame);
        if (event) yield event;
        split = buffer.indexOf("\n\n");
      }
    }
  } finally {
    // Closing the tab or hitting stop should not leave the reader dangling.
    reader.cancel().catch(() => {});
  }
}

function parseFrame(frame: string): ChatEvent | null {
  let name = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!name || !data) return null;

  try {
    return { type: name, ...JSON.parse(data) } as ChatEvent;
  } catch {
    // A malformed frame is not worth killing the stream over.
    return null;
  }
}

/* ------------------------------------------------------------------- api */
export const api = {
  auth: {
    /** `identifier` is a username ("navya") or the full email address. */
    login: (identifier: string, password: string) =>
      request<TokenResponse>("/api/auth/login", {
        method: "POST",
        body: { identifier, password },
      }),
    /** Ends this session server-side and clears both cookies. Idempotent. */
    logout: () => request<Message>("/api/auth/logout", { method: "POST" }),
    me: (signal?: AbortSignal) => request<User>("/api/auth/me", { signal }),
    /** Ends EVERY session this person holds, this one included. */
    changePassword: (oldPassword: string, newPassword: string, confirmPassword?: string) =>
      request<Message>("/api/auth/change-password", {
        method: "POST",
        body: {
          old_password: oldPassword,
          new_password: newPassword,
          ...(confirmPassword !== undefined ? { confirm_password: confirmPassword } : {}),
        },
      }),
  },

  users: {
    list: (query?: Query, signal?: AbortSignal) =>
      request<Page<UserDetail>>("/api/users", { query, signal }),
    get: (id: string, signal?: AbortSignal) =>
      request<UserDetail>(`/api/users/${id}`, { signal }),
    actionable: (signal?: AbortSignal) =>
      request<User[]>("/api/users/actionable", { signal }),
    assignableRoles: (signal?: AbortSignal) =>
      request<AssignableRoles>("/api/users/assignable-roles", { signal }),
    orgChart: (query?: Query, signal?: AbortSignal) =>
      request<OrgNode[]>("/api/users/org-chart", { query, signal }),
    create: (body: CreateUserBody) =>
      request<UserDetail>("/api/users", { method: "POST", body }),
    update: (id: string, body: UpdateUserBody) =>
      request<UserDetail>(`/api/users/${id}`, { method: "PATCH", body }),
    /** Set somebody's password. Nothing comes back but a message - whoever
     *  set it already has it. */
    setPassword: (
      id: string,
      newPassword: string,
      confirmPassword: string,
      mustChange = true,
    ) =>
      request<PasswordSetResult>(`/api/users/${id}/reset-password`, {
        method: "POST",
        body: {
          new_password: newPassword,
          confirm_password: confirmPassword,
          must_change: mustChange,
        },
      }),
    /** Super Admin only, audited. `password` is null when no copy exists. */
    revealPassword: (id: string) =>
      request<{ password: string | null }>(`/api/users/${id}/password`),
    /** Clear a sign-in lockout and the failed-attempt counter. */
    unlock: (id: string) =>
      request<UserDetail>(`/api/users/${id}/unlock`, { method: "POST" }),
    /** Audit events about this person or performed by them, newest first. */
    activity: (
      id: string,
      query: { page: number; page_size: number },
      signal?: AbortSignal,
    ) =>
      request<Page<UserActivityItem>>(`/api/users/${id}/activity`, { query, signal }),
    departments: (signal?: AbortSignal) =>
      request<Department[]>("/api/departments", { signal }),
    deactivate: (id: string, newManagerId?: string | null) =>
      request<Message>(`/api/users/${id}`, {
        method: "DELETE",
        query: { new_manager_id: newManagerId ?? undefined },
      }),
    reactivate: (id: string) =>
      request<UserDetail>(`/api/users/${id}/reactivate`, { method: "POST" }),
    /** Permanent, Super Admin only, and refused with a 409 if the person has
     *  any history. Deactivation remains the normal path. */
    deletePermanently: (id: string) =>
      request<Message>(`/api/users/${id}/permanent`, { method: "DELETE" }),
    updateMe: (body: { name?: string; phone?: string | null }) =>
      request<User>("/api/me", { method: "PATCH", body }),
  },

  customers: {
    list: (query?: Query, signal?: AbortSignal) =>
      request<Page<Customer>>("/api/customers", { query, signal }),
    stats: (signal?: AbortSignal) =>
      request<CustomerStats>("/api/customers/stats", { signal }),
    get: (id: string, signal?: AbortSignal) =>
      request<CustomerDetail>(`/api/customers/${id}`, { signal }),

    // A completed customer's history: notes, reference asks and feedback,
    // merged into one view.
    timeline: (id: string, signal?: AbortSignal) =>
      request<TimelineEntry[]>(`/api/customers/${id}/timeline`, { signal }),
    logActivity: (id: string, activityType: string, remark?: string | null) =>
      request<TimelineEntry[]>(`/api/customers/${id}/timeline`, {
        method: "POST",
        body: { activity_type: activityType, remark: remark ?? null },
      }),
    undoActivity: (id: string, activityId: string) =>
      request<TimelineEntry[]>(`/api/customers/${id}/timeline/${activityId}/undo`, {
        method: "POST",
      }),

    // The ready-to-send WhatsApp or email request, filled in by the server.
    composeMessage: (
      id: string,
      purpose: MessagePurpose,
      channel: MessageChannel,
      signal?: AbortSignal,
    ) =>
      request<ComposedMessage>(`/api/customers/${id}/message`, {
        query: { purpose, channel },
        signal,
      }),
    recordSent: (
      id: string,
      purpose: MessagePurpose,
      channel: MessageChannel,
      body?: string,
    ) =>
      request<TimelineEntry[]>(`/api/customers/${id}/message/sent`, {
        method: "POST",
        body: { purpose, channel, body: body ?? null },
      }),
  },

  references: {
    list: (query?: Query, signal?: AbortSignal) =>
      request<Page<Reference>>("/api/references", { query, signal }),
    stats: (signal?: AbortSignal) =>
      request<ReferenceStats>("/api/references/stats", { signal }),
    // Every won account that can be asked — SAP customers and converted
    // leads, merged server-side.
    /** Filtering happens on the SERVER. Narrowing a fetched array in React
     *  would filter only the rows that happened to arrive, which looks like
     *  an answer and is not one. */
    accounts: (
      params: { reference_status?: string; search?: string; owner_id?: string } = {},
      signal?: AbortSignal,
    ) =>
      request<AskableAccount[]>("/api/references/accounts", {
        query: {
          reference_status: params.reference_status,
          search: params.search,
          owner_id: params.owner_id,
        },
        signal,
      }),
    followUps: (
      includeFuture = false,
      groupBy: "due_date" | "owner" = "due_date",
      signal?: AbortSignal,
    ) =>
      request<FollowUpDue[]>("/api/references/follow-ups", {
        query: { include_future: includeFuture, group_by: groupBy },
        signal,
      }),
    record: (body: RecordReferenceBody) =>
      request<Reference>("/api/references", { method: "POST", body }),
  },

  leads: {
    list: (query?: Query, signal?: AbortSignal) =>
      request<Page<Lead>>("/api/leads", { query, signal }),
    stats: (signal?: AbortSignal) => request<LeadStats>("/api/leads/stats", { signal }),
    /** Per-assignee counts for the caller's own assignments. */
    /** Administrators only: send a closed lead back to the start. */
    reopen: (id: string, remark: string) =>
      request<LeadDetail>(`/api/leads/${id}/reopen`, {
        method: "POST",
        body: { status: "NEW", remark },
      }),
    assignedByMeCounts: (signal?: AbortSignal) =>
      request<AssignedByMeCount[]>("/api/leads/assigned-by-me/counts", { signal }),
    get: (id: string, signal?: AbortSignal) =>
      request<LeadDetail>(`/api/leads/${id}`, { signal }),
    create: (body: CreateLeadBody) =>
      request<LeadDetail>("/api/leads", { method: "POST", body }),
    update: (id: string, body: Record<string, unknown>) =>
      request<LeadDetail>(`/api/leads/${id}`, { method: "PATCH", body }),
    setStatus: (id: string, status: LeadStatus, remark?: string) =>
      request<LeadDetail>(`/api/leads/${id}/status`, {
        method: "POST",
        body: { status, remark: remark ?? null },
      }),
    addActivity: (
      id: string,
      body: { activity_type: string; remark?: string | null; next_follow_up_date?: string | null },
    ) => request<LeadDetail>(`/api/leads/${id}/activities`, { method: "POST", body }),
    composeMessage: (id: string, channel: MessageChannel, signal?: AbortSignal) =>
      request<ComposedMessage>(`/api/leads/${id}/message`, {
        query: { channel },
        signal,
      }),
    recordMessageSent: (id: string, channel: MessageChannel, body?: string) =>
      request<LeadDetail>(`/api/leads/${id}/message/sent`, {
        method: "POST",
        body: { purpose: "FEEDBACK", channel, body: body ?? null },
      }),
  },

  workQueue: (query?: Query, signal?: AbortSignal) =>
    request<WorkQueue>("/api/work-queue", { query, signal }),

  feedback: {
    list: (query?: Query, signal?: AbortSignal) =>
      request<Page<Feedback>>("/api/feedback", { query, signal }),
    analysis: (signal?: AbortSignal) =>
      request<FeedbackAnalysis>("/api/feedback/analysis", { signal }),
    alerts: (includeResolved = false, signal?: AbortSignal) =>
      request<FeedbackAlert[]>("/api/feedback/alerts", {
        query: { include_resolved: includeResolved },
        signal,
      }),
    imports: (signal?: AbortSignal) =>
      request<ImportSummary[]>("/api/feedback/imports", { signal }),
    dryRun: (file: File) => upload<DryRunResult>("/api/feedback/import/dry-run", file),
    commit: (file: File) => upload<CommitResult>("/api/feedback/import/commit", file),
    evaluateAlerts: () =>
      request<{ raised: string[]; resolved: string[] }>("/api/feedback/evaluate-alerts", {
        method: "POST",
      }),
    assignAlert: (alertId: string, userId: string | null) =>
      request<FeedbackAlert>(`/api/feedback/alerts/${alertId}/assign`, {
        method: "POST",
        body: { user_id: userId },
      }),
    // Converted leads whose post-sale record says they were invoiced 10+ days
    // ago and who owe a response. The same population Reference Tracking
    // works from.
    pending: (signal?: AbortSignal) =>
      request<PendingFeedbackItem[]>("/api/feedback/pending", { signal }),
    // Why part of that population is not in the queue yet, so an empty queue
    // can explain itself instead of showing a bare zero.
    pendingSummary: (signal?: AbortSignal) =>
      request<FeedbackPopulation>("/api/feedback/pending/summary", { signal }),
    cancelRequest: (requestId: string) =>
      request<Message>(`/api/feedback/requests/${requestId}`, { method: "DELETE" }),

    // Google Form sync. Admin-only, except the webhook itself, which is
    // signed rather than authenticated and is never called from here.
    sync: {
      status: (signal?: AbortSignal) =>
        request<SyncStatus>("/api/feedback/sync/status", { signal }),
      events: (query?: Query, signal?: AbortSignal) =>
        request<SyncEvent[]>("/api/feedback/sync/events", { query, signal }),
      needsReview: (signal?: AbortSignal) =>
        request<NeedsReviewItem[]>("/api/feedback/sync/needs-review", { signal }),
      /** Names only, for the "file this under…" picker. Not the customer book. */
      customers: (signal?: AbortSignal) =>
        request<{ id: string; name: string; sap_code: string | null }[]>(
          "/api/feedback/sync/customers",
          { signal },
        ),
      /** Leads that were asked and have not answered - the first place an
       *  unmatched response should be filed. */
      openRequests: (signal?: AbortSignal) =>
        request<
          { id: string; reference: string; status: string; name: string; company_name: string | null }[]
        >("/api/feedback/sync/open-requests", { signal }),
      run: () => request<SyncResult>("/api/feedback/sync/run", { method: "POST" }),
      /** Exactly one target: a lead's open ask, or an archived customer. */
      resolve: (
        feedbackId: string,
        target: { request_id: string } | { customer_id: string },
      ) =>
        request<{ status: string; customer: string }>(
          `/api/feedback/sync/resolve/${feedbackId}`,
          { method: "POST", body: target },
        ),
    },
  },

  notifications: {
    list: (query?: Query, signal?: AbortSignal) =>
      request<Page<Notification>>("/api/notifications", { query, signal }),
    unreadCount: (signal?: AbortSignal) =>
      request<{ unread: number }>("/api/notifications/unread-count", { signal }),
    markRead: (id: string) =>
      request<Message>(`/api/notifications/${id}/read`, { method: "POST" }),
    markAllRead: () => request<Message>("/api/notifications/read-all", { method: "POST" }),
  },

  admin: {
    settings: (signal?: AbortSignal) =>
      request<{ values: Record<string, unknown> }>("/api/admin/settings", { signal }),
    updateSettings: (values: Record<string, unknown>) =>
      request<{ values: Record<string, unknown> }>("/api/admin/settings", {
        method: "PATCH",
        body: { values },
      }),
    audit: (query?: Query, signal?: AbortSignal) =>
      request<Page<AuditEvent>>("/api/admin/audit", { query, signal }),
    sapImport: () => request<SapImportResult>("/api/admin/sap-import", { method: "POST" }),
    sapSyncStatus: (signal?: AbortSignal) =>
      request<SapSyncStatus>("/api/admin/sap-sync/status", { signal }),
    sapUpload: (file: File) => upload<SapImportResult>("/api/admin/sap-import/upload", file),
  },

  dashboard: (signal?: AbortSignal) => request<Dashboard>("/api/dashboard", { signal }),

  chat: {
    // Not gated on the feature flag: a truthful `false` is what decides
    // whether the launcher renders at all.
    status: (signal?: AbortSignal) => request<ChatStatus>("/api/chat/status", { signal }),
    conversations: (signal?: AbortSignal) =>
      request<Conversation[]>("/api/chat/conversations", { signal }),
    conversation: (id: string, signal?: AbortSignal) =>
      request<ConversationDetail>(`/api/chat/conversations/${id}`, { signal }),
    remove: (id: string) =>
      request<Message>(`/api/chat/conversations/${id}`, { method: "DELETE" }),
    // The send path is `streamChat`, exported separately - it is a stream,
    // not a promise, so it does not fit this table.
  },

  lookups: {
    teams: (signal?: AbortSignal) => request<Team[]>("/api/teams", { signal }),
  },
};

/** Human-readable text for anything thrown by a call. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
