/**
 * Mirrors the FastAPI response schemas in backend/app/schemas.
 *
 * Kept hand-written rather than generated: the surface is small, and a typo
 * here surfaces as a type error at the call site rather than as `undefined`
 * on screen.
 */

export type Role = "SUPER_ADMIN" | "ADMIN" | "MANAGER" | "BDE" | "SALES";

/** How the company addresses somebody. Admin-set; never inferred from a name. */
export type Honorific = "SIR" | "MAAM";

export type ReferenceStatus = "NOT_ASKED" | "TAKEN" | "PENDING" | "DECLINED";

/** The error envelope every failure shares. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Message {
  message: string;
}

/* ------------------------------------------------------------------ users */
export interface User {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  role: Role;
  title: string | null;
  /** "SIR" | "MAAM" | null. Set by an administrator, never inferred. */
  honorific: Honorific | null;
  manager_id: string | null;
  team_id: string | null;
  heads_department_id: string | null;
  is_active: boolean;
  must_change_password: boolean;
  plain_password?: string | null;
  deactivated_at: string | null;
  created_at: string;
}

export interface UserDetail extends User {
  manager_name: string | null;
  team_name: string | null;
  heads_department_name: string | null;
  direct_report_count: number;
  /** Whether the CALLER may act on this user — the frontend hides controls
   *  from this rather than re-deriving the authority rules. */
  can_act_on: boolean;
}

export interface OrgNode {
  id: string;
  name: string;
  role: Role;
  title: string | null;
  team_name: string | null;
  is_active: boolean;
  reports: OrgNode[];
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  must_change_password: boolean;
  user: User;
}

export interface Team {
  id: string;
  name: string;
  code: string;
}

export interface Department {
  id: string;
  name: string;
  code: string;
}

export interface AssignableRoles {
  roles: Role[];
}

/* -------------------------------------------------------------- customers */
export interface InvoiceLine {
  id: string;
  invoice_no: string;
  invoice_date: string | null;
  fgpo_code: string | null;
  item_description: string | null;
  sales_person: string | null;
}

export interface Customer {
  id: string;
  sap_code: string;
  name: string;
  mobile: string | null;
  email: string | null;
  owner_user_id: string | null;
  sap_sales_person: string | null;
  first_invoice_date: string | null;
  last_invoice_date: string | null;
  invoice_count: number;
  reference_status: ReferenceStatus;
  next_reference_date: string | null;
  last_reference_asked_at: string | null;
  created_at: string;
  owner_name: string | null;
  line_count: number;
  feedback_count: number;
  feedback_average: number | null;
  /** How many times this account has said no to a reference ask. */
  decline_count: number;
}

export interface CustomerFeedback {
  id: string;
  submitted_at_source: string | null;
  overall_rating: number | null;
  overall_comments: string | null;
  would_recommend: string | null;
  handled_by_name: string | null;
  departments: { name: string; rating: number | null; comments: string | null }[];
}

export interface CustomerDetail extends Customer {
  invoice_lines: InvoiceLine[];
  feedback: CustomerFeedback[];
}

/** One entry in a completed customer's history. */
export interface TimelineEntry {
  id: string;
  kind: "ACTIVITY" | "REFERENCE" | "FEEDBACK";
  activity_type: string;
  title: string;
  remark: string | null;
  actor_name: string | null;
  created_at: string;
  undone_at: string | null;
  can_undo: boolean;
  related_type: string | null;
  related_id: string | null;
  rating: number | null;
}


/** The only thing the portal asks a customer for. */
export type MessagePurpose = "FEEDBACK";
export type MessageChannel = "WHATSAPP" | "EMAIL";

/**
 * A request message the server has already filled in.
 *
 * The text is rendered server-side so what lands on the customer's timeline
 * is the same text the template produced, not something the browser built.
 */
export interface ComposedMessage {
  purpose: MessagePurpose;
  channel: MessageChannel;
  /** The customer's mobile or email, as SAP stores it. */
  to: string | null;
  subject: string | null;
  body: string;
  /** The feedback form or Google review URL the message points at. */
  link: string;
  /** The deep link that opens WhatsApp or the mail client, ready to send. */
  url: string | null;
  link_configured: boolean;
  /** "mobile" or "email" when the customer has no way to receive this. */
  missing_contact: string | null;
  /** Normalised digits for wa.me, so an edited body can be re-linked. */
  whatsapp_number: string | null;
}

export interface CustomerStats {
  total: number;
  owned: number;
  unowned: number;
  invoice_lines: number;
  invoices: number;
  not_asked: number;
  taken: number;
  pending: number;
  declined: number;
  with_feedback: number;
  awaiting_feedback: number;
}

/* -------------------------------------------------------------- dashboard */
/**
 * The password that was just set, returned once.
 *
 * There is no endpoint that reads an EXISTING password, and there cannot be:
 * they are stored as one-way hashes, so the plaintext is kept nowhere. This
 * is the single moment the value is readable.
 */
export interface PasswordSetResult {
  message: string;
  must_change_password: boolean;
}

export interface OrgKpis {
  users_in_scope: number;
  active_users: number;
  unread_notifications: number;
}

export interface TeamRollup {
  team_name: string;
  members: number;
  open_leads: number;
  converted: number;
}

/**
 * One person in the caller's subtree.
 *
 * Every field is about LEADS — the work these people actually do. The columns
 * this replaced (customers, invoices, last invoice, a per-person feedback
 * rating) all came from the SAP book, which had no link to any lead: somebody
 * carrying forty open leads showed "0 customers" and read as idle.
 */
export interface ReportRow {
  user_id: string;
  name: string;
  role: Role;
  title: string | null;
  team_name: string | null;
  is_active: boolean;
  open_leads: number;
  converted: number;
  references_taken: number;
  followups_due: number;
  /** This person's eligible accounts, and how many gave a reference. */
  eligible_accounts: number;
  references_on_eligible: number;
  /** -(still owing a reference / eligible) %. 10 accounts, 7 given = -30. */
  reference_score: number;
  /** Last time this person did anything on a lead of theirs. */
  last_activity_at: string | null;
}

export interface DashboardDepartmentRating {
  department_id: string;
  department_name: string;
  average_rating: number | null;
  response_count: number;
  below_threshold: boolean;
}

export interface AlertBrief {
  department_id: string;
  department_name: string;
  average_rating: number;
  response_count: number;
  threshold: number;
}

export interface ImportHealth {
  filename: string;
  status: string;
  total_rows: number;
  created_count: number;
  skipped_count: number;
  error_count: number;
  created_at: string;
}

export interface Dashboard {
  shape: "ADMIN" | "MANAGER" | "PERSONAL";
  generated_at: string;
  user_name: string;
  role: Role;

  references: ReferenceStats;
  leads: LeadStats;
  org: OrgKpis;
  /** Null when the caller has no feedback rights at all, so the UI hides the
   *  whole section rather than showing zeroes it is not entitled to. */
  feedback: FeedbackStats | null;
  /** Converted leads and customers still owed a feedback ask — scoped by the
   *  reporting chain, so it is present even when `feedback` is null. */
  feedback_pending: number;

  department_ratings: DashboardDepartmentRating[];
  monthly_feedback: MonthlyVolume[];
  alerts: AlertBrief[];

  teams: TeamRollup[];
  reports: ReportRow[];
  imports: ImportHealth[];
}

/* ----------------------------------------------------------- request bodies */
export interface CreateUserBody {
  /** Optional. "SIR" | "MAAM" | null. */
  honorific?: Honorific | null;
  name: string;
  email: string;
  password: string;
  role: Role;
  phone?: string | null;
  title?: string | null;
  manager_id?: string | null;
  team_id?: string | null;
}

export type UpdateUserBody = Partial<{
  name: string;
  email: string;
  phone: string | null;
  honorific: Honorific | null;
  title: string | null;
  role: Role;
  manager_id: string | null;
  team_id: string | null;
  heads_department_id: string | null;
  is_active: boolean;
}>;

/* ------------------------------------------------------------ references */
/**
 * What the customer answered.
 *
 *   YES         gave a reference        -> completed, reference received
 *   NOT_SHARED  asked, none to give     -> completed, nothing to chase
 *   NO          not right now           -> still open, owes a follow-up date
 */
export type ReferenceOutcome = "YES" | "NO" | "NOT_SHARED";

export interface Reference {
  id: string;
  /** Exactly one of these is set — a reference comes from a SAP customer or
   *  from a lead the portal saw converted. */
  customer_id: string | null;
  lead_id: string | null;
  requested_by_user_id: string | null;
  outcome: ReferenceOutcome;
  asked_on: string;
  next_reference_date: string | null;
  referred_name: string | null;
  referred_company: string | null;
  referred_mobile: string | null;
  referred_email: string | null;
  notes: string | null;
  converted_lead_id: string | null;
  created_at: string;
  customer_name: string | null;
  customer_sap_code: string | null;
  /** Whoever gave the reference, whichever table they live in. */
  source_name: string | null;
  source_type: "CUSTOMER" | "LEAD";
  requested_by_name: string | null;
}

/**
 * A won account that can be asked to refer somebody.
 *
 * ONE kind of subject: a lead this portal converted, whose post-sale record
 * says it was invoiced at least ten days ago. The SAP customer book used to
 * be the other kind, which is how this module reported 17 accounts while
 * Assigned Leads reported 22 conversions.
 */
export interface AskableAccount {
  subject_type: "LEAD";
  subject_id: string;
  subject_name: string;
  company_name: string | null;
  mobile: string | null;
  email: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  reference_status: ReferenceStatus;
  last_reference_asked_at: string | null;
  next_reference_date: string | null;
  decline_count: number;
  /** The sheet's Reference Date — the day this account became askable. */
  reference_date: string | null;
  converted_at: string | null;
}

export interface FollowUpDue {
  subject_type: "LEAD";
  subject_id: string;
  subject_name: string;
  company_name: string | null;
  mobile: string | null;
  email: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  next_reference_date: string;
  last_reference_asked_at: string | null;
  days_overdue: number;
  decline_count: number;
}

/**
 * Reference progress, all of it derived from ONE population.
 *
 * The first four always add up, which is what makes a small eligible count
 * explainable instead of alarming:
 *
 *     eligible_accounts + waiting_period + awaiting_sync === converted_leads
 *
 * Identical on `/references/stats` and inside the dashboard payload — both
 * come from the same service, so no field here is optional any more.
 */
export interface ReferenceStats {
  /** Won leads in scope. The whole book. */
  converted_leads: number;
  /** ...invoiced at least 10 days ago, so they may be asked. The denominator. */
  eligible_accounts: number;
  /** ...with no post-sale record synced yet. Not "no customers". */
  awaiting_sync: number;
  /** ...synced, but inside the 10-day wait. */
  waiting_period: number;
  /** Asks that are finished: gave one OR had none to give. */
  requests_completed: number;
  /** References actually received. Deliberately NOT the same number. */
  references_taken: number;
  references_pending: number;
  references_declined: number;
  not_asked: number;
  follow_ups_due: number;
  /** requests_completed / eligible_accounts, as a percentage. */
  completion_rate: number;
  reference_rate: number;
  /**
   * The agreed score: -(eligible accounts still owing a reference) %.
   * 10 eligible with 7 references reads -30; everything asked reads 0.
   */
  reference_score: number;
}

export interface RecordReferenceBody {
  /** Exactly one of these — the customer or the converted lead being asked. */
  customer_id?: string | null;
  lead_id?: string | null;
  outcome: ReferenceOutcome;
  asked_on?: string | null;
  next_reference_date?: string | null;
  referred_name?: string | null;
  referred_company?: string | null;
  referred_mobile?: string | null;
  referred_email?: string | null;
  notes?: string | null;
  requested_by_user_id?: string | null;
}

/* ----------------------------------------------------------------- leads */
export type LeadStatus =
  | "NEW"
  | "CONTACTED"
  | "NOT_CONTACTED"
  | "NURTURING"
  | "PRE_QUALIFIED"
  | "QUALIFIED"
  | "CONVERTED"
  | "JUNK"
  | "LOST";

export type LeadPriority = "LOW" | "MEDIUM" | "HIGH";

export interface LeadActivity {
  id: string;
  activity_type: string;
  from_status: string | null;
  to_status: string | null;
  remark: string | null;
  created_at: string;
  actor_user_id: string | null;
  actor_name: string | null;
  undone_at: string | null;
  /** Whether the CALLER may undo this entry — derived server-side, so the
   *  button and the endpoint cannot disagree. */
  can_undo: boolean;
}

export interface Lead {
  id: string;
  name: string;
  company_name: string | null;
  mobile: string | null;
  email: string | null;
  city: string | null;
  requirement: string | null;
  origin: string;
  origin_reference_id: string | null;
  status: LeadStatus;
  priority: LeadPriority;
  assigned_to_user_id: string | null;
  assigned_by_user_id: string | null;
  assigned_at: string | null;
  next_follow_up_date: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  dispatched_at: string | null;
  assigned_to_name: string | null;
  assigned_by_name: string | null;
  activity_count: number;
  last_activity_at: string | null;
  /** How many times ownership has changed hands — a quick red flag without
   *  opening the full timeline. */
  reassignment_count: number;
}

export interface LeadDetail extends Lead {
  activities: LeadActivity[];
}

/** One row of "who did I assign leads to, and how many". */
export interface AssignedByMeCount {
  user_id: string | null;
  name: string;
  count: number;
}

export interface LeadStats {
  total: number;
  open: number;
  new: number;
  contacted: number;
  not_contacted: number;
  nurturing: number;
  pre_qualified: number;
  qualified: number;
  converted: number;
  junk: number;
  lost: number;
  follow_ups_due: number;
  no_recent_activity: number;
}

export interface WorkQueue {
  assigned_leads: Lead[];
  reference_follow_ups: FollowUpDue[];
  assigned_total: number;
  follow_up_total: number;
}

export interface CreateLeadBody {
  name: string;
  assigned_to_user_id: string;
  company_name?: string | null;
  mobile?: string | null;
  email?: string | null;
  city?: string | null;
  requirement?: string | null;
  priority?: LeadPriority;
  next_follow_up_date?: string | null;
}

/* -------------------------------------------------------------- feedback */
export interface DepartmentRating {
  department_id: string;
  department_name: string;
  rating: string | null;
  raw_value: string | null;
  comments: string | null;
}

export interface Feedback {
  id: string;
  source: string;
  submitted_at_source: string | null;
  customer_name: string | null;
  company_name: string | null;
  mobile: string | null;
  email: string | null;
  handled_by_name: string | null;
  handled_by_user_id: string | null;
  overall_rating: string | null;
  overall_rating_raw: string | null;
  overall_comments: string | null;
  would_recommend: string | null;
  created_at: string;
  department_ratings: DepartmentRating[];
  /** Part of the demonstration batch, not a real customer's opinion. */
  is_sample: boolean;
}

export interface DepartmentSummary {
  department_id: string;
  department_name: string;
  average_rating: number | null;
  response_count: number;
  below_threshold: boolean;
  enough_responses: boolean;
}

export interface FeedbackAlert {
  id: string;
  department_id: string;
  department_name: string | null;
  status: string;
  average_rating: string;
  response_count: number;
  threshold: string;
  window_days: number;
  opened_at: string;
  resolved_at: string | null;
  resolved_reason: string | null;
  assigned_to_user_id: string | null;
  assigned_to_name: string | null;
}

/** Someone still owed a feedback ask — a dispatched lead or a converted
 *  customer with no response on file yet. Two sources, one queue. */
export interface PendingFeedbackItem {
  type: "LEAD" | "CUSTOMER";
  id: string;
  name: string;
  company_name: string | null;
  mobile: string | null;
  email: string | null;
  since: string;
  owner_user_id: string | null;
  owner_name: string | null;
  /** NOT_ASKED — nobody has asked. AWAITING — asked, still waiting. */
  state: "NOT_ASKED" | "AWAITING";
  request_id: string | null;
  request_reference: string | null;
  requested_at: string | null;
}

/* ------------------------------------------------------ google form sync */
export interface SyncStatus {
  webhook_configured: boolean;
  pull_configured: boolean;
  form_url_configured: boolean;
  reference_field_configured: boolean;
  last_delivery_at: string | null;
  processed: number;
  failed: number;
  duplicates: number;
  needs_review: number;
}

/** One delivery. A hash and an outcome — never the payload. */
export interface SyncEvent {
  id: string;
  external_response_id: string;
  source: "WEBHOOK" | "PULL" | "FILE";
  status: "RECEIVED" | "PROCESSED" | "FAILED" | "DUPLICATE";
  payload_hash: string;
  feedback_id: string | null;
  error_code: string | null;
  error_detail: string | null;
  received_at: string;
  processed_at: string | null;
}

export interface SyncResult {
  new_responses: number;
  already_synced: number;
  unmatched: number;
  errors: number;
}

export interface NeedsReviewItem {
  id: string;
  match_status: "UNMATCHED" | "DUPLICATE";
  customer_name: string | null;
  company_name: string | null;
  overall_rating: number | null;
  submitted_at: string | null;
  created_at: string;
}

/**
 * The population the Feedback module measures, and why part of it is not
 * actionable yet. The first four always add up:
 *
 *     eligible + waiting + awaiting_sync === converted
 *
 * Identical arithmetic to `ReferenceStats`, from the same service — the two
 * modules are provably looking at the same book.
 */
export interface FeedbackPopulation {
  converted: number;
  eligible: number;
  waiting: number;
  awaiting_sync: number;
  /** Eligible accounts that have answered. */
  received: number;
  /** Eligible accounts that have not. */
  pending: number;
}

export interface MonthlyVolume {
  month: string;
  responses: number;
}

export interface FeedbackStats {
  total_responses: number;
  imported_responses: number;
  average_rating: number | null;
  rating_scale_max: number;
  departments_below_threshold: number;
  open_alerts: number;
  would_recommend_rate: number | null;
}

export interface FeedbackAnalysis {
  /** Whether this caller works the alert queue. */
  handles_alerts: boolean;
  stats: FeedbackStats;
  departments: DepartmentSummary[];
  alerts: FeedbackAlert[];
  monthly: MonthlyVolume[];
  threshold: number;
  window_days: number;
  scale_max: number;
}

export interface ImportSummary {
  id: string;
  filename: string;
  source: string;
  status: string;
  total_rows: number;
  created_count: number;
  skipped_count: number;
  error_count: number;
  created_at: string;
  uploaded_by_user_id: string | null;
  /** Row-level detail for both hard errors and duplicate skips. */
  errors: { row: number; type: "error" | "skipped"; message: string; column?: string; reason?: string }[] | null;
}

export interface DryRunResult {
  filename: string;
  total_rows: number;
  column_map: {
    fields: Record<string, string>;
    department_ratings: Record<string, string>;
    department_comments: Record<string, string>;
    unmapped: string[];
  };
  samples: Record<string, unknown>[];
  known_departments: string[];
  unknown_departments: string[];
  missing_fields: string[];
  duplicate_rows: number;
  warnings: string[];
  can_commit: boolean;
}

export interface CommitResult {
  import_id: string | null;
  filename: string;
  total_rows: number;
  created_count: number;
  skipped_count: number;
  error_count: number;
  errors: { row: number; type: "error" | "skipped"; message: string; column?: string; reason?: string }[];
  ratings_created: number;
  departments_created: string[];
  status: string;
  alerts: { raised: string[]; resolved: string[]; departments: number };
}

/* --------------------------------------------------------- notifications */
export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  entity_type: string | null;
  entity_id: string | null;
  is_read: boolean;
  created_at: string;
}

/* ------------------------------------------------------------ audit etc. */
export interface AuditEvent {
  id: string;
  actor_user_id: string | null;
  actor_name: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

/* ----------------------------------------------------------------- chat */
export interface ChatSetup {
  /** Which LLM service answers. Shown to admins; not a secret. */
  provider: string;
  has_api_key: boolean;
  flag_enabled: boolean;
  model: string;
}

/** An opener the UI may offer. `topic` orders them; it does not permit them —
 *  the server has already filtered the list to this role. */
export interface Suggestion {
  text: string;
  topic: string;
}

export interface ChatStatus {
  /** The deployment wants an assistant (`CHAT_ENABLED`). Gates the launcher. */
  available: boolean;
  /** It can actually answer — that, and a key. Gates the composer. */
  enabled: boolean;
  suggestions: Suggestion[];
  /** Administrators only — everyone else gets null. Never the key itself. */
  setup: ChatSetup | null;
}

export interface ChatMessage {
  id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

/**
 * Something the assistant found that you might want to act on.
 *
 * Deliberately not a copy of the record: an id, a name and the two facts you
 * need to decide whether to click. Contact details are never present — the
 * server strips them (`services/chat/cards.py`), and the page this links to
 * is where the full record is read, under the normal permissions.
 *
 * Ephemeral. Cards arrive on the stream and are never persisted, so reopening
 * a past conversation shows the prose without them.
 */
export interface ResultCard {
  kind: "lead" | "account" | "department" | "count";
  id: string;
  title: string;
  subtitle: string | null;
  badges: { label: string; tone: string }[];
  meta: string[];
  href: string;
  action: string;
}

/**
 * One frame of the assistant's reply stream.
 *
 * `tool` is advisory — it says what is being looked up and carries no data.
 * `cards` carries the small, contact-free projection described above; the full
 * rows the model saw never reach the browser, and nothing on the stream is
 * written down.
 */
export type ChatEvent =
  | { type: "start"; conversation_id: string }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; label: string }
  | { type: "cards"; items: ResultCard[] }
  | { type: "error"; code: string; message: string }
  | {
    type: "done";
    conversation_id: string;
    message_id: string;
    usage: { input: number; output: number };
  };
