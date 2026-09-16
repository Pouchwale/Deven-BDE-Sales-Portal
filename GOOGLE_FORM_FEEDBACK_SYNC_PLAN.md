# Google Form → Portal feedback sync — implementation plan

**Status: approved and implemented.** See the implementation log at the end.

Closing the loop the module is missing today: the portal can *ask* for feedback
but has no idea when it arrives. This plan connects the form response back to
the request that caused it.

---

## 0. The two findings that shape everything below

Before the sections you asked for, two facts from the audit that change what is
worth building.

**1. There is no feedback-request record. Anywhere.**

`GET /feedback/pending` (`feedback_analysis.py:213-293`) *derives* the queue on
every call:

- a `Lead` with `dispatched_at IS NOT NULL` and no `FEEDBACK_REQUESTED` activity
- a `Customer` with no `Feedback` row pointing at it

Pressing **Send request** writes a timeline activity and nothing else. There is
no row to carry an ID, no row to change status on, and nothing for a form
response to point back at. **Creating that record is the core of this work** —
almost everything else is plumbing around it.

**2. The company's Google Form has never been supplied.**

`company.feedback_form_url` is seeded empty (`roster.py:148`), and
`docs/OPEN_QUESTIONS.md` Q5 records that the export was never sent. So I cannot
inspect the real questions, scale, or department wording, and this plan does
not invent them. What the repo *does* encode is the expected shape, in
`feedback_mapping.py:29-75` — and that mapper is the thing to reuse rather than
replace.

---

## 1. The existing feedback module

| Concern | File | Notes |
|---|---|---|
| Feedback record | `backend/app/models/feedback.py:52-95` | Has `source_row_hash` (unique) — **already an idempotency key** |
| Department rating | `models/feedback.py:98-124` | Keeps `raw_value` beside the normalised number |
| Alert | `models/feedback.py:127-156` | One OPEN row per department, partial unique index |
| Import batch | `models/feedback.py:20-49` | `column_map`, `errors` JSON, status |
| API | `backend/app/api/feedback.py` | 11 endpoints; `/pending` at `:202`, `/analysis` at `:129` |
| Pending queue | `services/feedback_analysis.py:213-293` | Derived, not stored — see §0 |
| Analysis | `services/feedback_analysis.py:48-118` | Department summary over a rolling window |
| Alerts + notify | `services/feedback_analysis.py:129-211` | `evaluate(notify=True)`; recipients at `:178-199` |
| Trend | `services/feedback_analysis.py:316` | `responses_per_month` |
| Import — column resolution | `services/feedback_mapping.py` | 3-pass resolve, never guesses |
| Import — commit | `services/feedback_import.py:267-470` | Row → Feedback + ratings + activity + audit |
| Customer matching | `services/customer_timeline.py:58-88` | Exact mobile → email → company. **No fuzzy matching** |
| "Send request" — compose | `services/messaging.py:151-202` | Renders `{link}` from `company.feedback_form_url` |
| "Send request" — record | `api/timeline.py:83` (customer), `api/leads.py:195` (lead) | Writes an activity |
| Customer model | `backend/app/models/customer.py` | SAP-sourced; `owner_user_id` |
| Lead model | `backend/app/models/lead.py` | `dispatched_at`, `assigned_to_user_id` |
| Dashboard metrics | `services/dashboard.py:103-160` | `feedback_pending`, `_feedback_panel` |
| Auth / roles | `core/deps.py`, `core/authority.py` | `CurrentUser`, `AdminUser`, `VisibilityScope` |
| Feedback scope | `authority.feedback_department_scope` | **Department-scoped, not reporting-line** |
| Notifications | `services/notifications.py:27-57` | `create(..., dedupe_key=)` |
| Audit | `services/audit.py` | Writes in the caller's transaction, never commits |
| Rate limiting | `core/ratelimit.py` | `SlidingWindowLimiter`, in-process |
| Frontend page | `frontend/src/app/(portal)/feedback/page.tsx` | Tabs: analysis, responses, alerts, pending, import |
| Send dialog | `components/customers/SendRequestDialog.tsx` | Shared by 5 pages |

**Threshold configuration** (`runtime_settings.feedback_config`): `scale_max`,
`alert_threshold`, `alert_min_responses`, `alert_window_days` — all editable in
Settings. **This plan hardcodes none of them.**

**Departments** are seeded as Sales, Production, Quality, Dispatch, Accounts,
Customer Support (`roster.py:61-68`), and the importer creates unknown ones
rather than dropping the rating.

---

## 2. What Google integration already exists

**None.** Searched for `gspread`, `sheets.googleapis`, `google-api-python`,
`oauth2`, `service_account`, `webhook`, `hmac`, `X-Signature` — zero hits in
`backend/app` and `frontend/src`. `requirements.txt` has no Google dependency.

**`POST /feedback/import/sheet` does not exist.** What exists is a two-step
*file* upload:

```
POST /feedback/import/dry-run   multipart .xlsx/.csv → proposed column map
POST /feedback/import/commit    same file, confirmed → creates Feedback rows
```

### What can be reused, and what cannot

| Reuse | Why |
|---|---|
| ✅ `feedback_mapping.py` — whole file | Header resolution, rating parse + rescale, department patterns. A sheet row and an xlsx row are the same dict |
| ✅ `customer_timeline.match_customer` | Already exact-only; the fallback when a token is missing |
| ✅ `Feedback.source_row_hash` | Second-line idempotency, already unique-indexed |
| ✅ `feedback_analysis.evaluate()` | Alerts + notifications, unchanged |
| ✅ Audit, notifications, rate limiter | Unchanged |
| ⚠️ `feedback_import.commit()` | Logic is right, but it is welded to a `FeedbackImport` batch and a file. **Extract the row→Feedback block into a shared `feedback_ingest.py`** used by both paths — do not fork it |
| ❌ The dry-run/confirm dance | A webhook has no human to confirm a mapping. Mapping is resolved once and stored on the sync config |

---

## 3. Target workflow

```
BDE opens Feedback ▸ Pending
  └─ presses "Send request"
       └─ POST /feedback/requests            ← NEW: creates the record
            reference  FB-2026-00127
            token      k7x9m2q4…  (32 chars, unguessable)
            status     SENT
       └─ messaging.compose() renders {link} as
            https://docs.google.com/forms/d/e/…/viewform
              ?usp=pp_url&entry.4839201=FB-2026-00127.k7x9m2q4…
       └─ WhatsApp / email opens, BDE sends it
       └─ activity logged (unchanged)

Customer opens the link
  └─ the reference field is PREFILLED and marked "do not edit"
  └─ customer answers the company's existing questions
  └─ submits

Google Form
  └─ appends a row to the linked Google Sheet
  └─ installable onFormSubmit trigger fires (Form-bound, not Sheet-bound)

Apps Script
  └─ builds {responseId, submittedAt, answers{question: value}}
  └─ signs it:  HMAC-SHA256(secret, "<unix-ts>.<body>")
  └─ POST https://portal…/api/feedback/sync/webhook
  └─ on non-2xx: writes the row to a retry queue, retries with backoff

Portal  POST /api/feedback/sync/webhook   (the only unauthenticated route)
  ├─ 1. signature + timestamp window   → 401, nothing else said
  ├─ 2. rate limit                     → 429
  ├─ 3. payload schema                 → 422
  ├─ 4. idempotency: responseId seen?  → 200 {"status":"duplicate"} (not an error)
  ├─ 5. record a FeedbackSyncEvent     ← every delivery, outcome or not
  ├─ 6. resolve columns (feedback_mapping)
  ├─ 7. MATCH:
  │      a. reference.token → FeedbackRequest  (primary)
  │      b. match_customer(mobile,email,company) (fallback)
  │      c. neither → status UNMATCHED, still stored
  ├─ 8. create Feedback (+ department ratings) via feedback_ingest
  ├─ 9. FeedbackRequest.status → COMPLETED, completed_at set
  ├─ 10. CustomerActivity FEEDBACK_RECEIVED  (already exists as a type)
  ├─ 11. notify the request owner
  ├─ 12. feedback_analysis.evaluate()  → department alerts + their notifications
  └─ 13. commit, return 200 {"status":"stored","feedback_id":…}

Dashboard / Department analysis
  └─ read the same tables they already read. No change needed.
```

Steps 6-12 are one transaction. Step 5 is written **first and committed
separately** so a failure in 6-12 still leaves a visible record — see §15.

---

## 4. Customer matching — the important one

### Recommendation: **C (unique token), with E/D/F as a documented fallback**

Not name. Never fuzzy. The reasons are in §4.5.

### The identifier

One field in the form carries **both** halves, joined by a dot:

```
FB-2026-00127.k7x9m2q4vB8nS1dLpR0wZ6tYcU3aH5jF
└─── reference ───┘ └──────────── token ─────────────┘
   human-readable      32 chars, secrets.token_urlsafe(24)
```

**Why both.** A bare sequential reference is guessable — anyone could submit a
response tagged `FB-2026-00128` and attach it to another company's account. A
bare random token is safe but meaningless to the admin reading the sheet or the
BDE on the phone. Together: the sheet stays readable, and matching verifies the
half that cannot be guessed.

| Question | Answer |
|---|---|
| Generated | On `POST /feedback/requests`, when the BDE presses Send. `reference` = `FB-{year}-{5-digit counter}` from a per-year sequence; `token` = `secrets.token_urlsafe(24)` |
| Stored | `feedback_requests.reference` (unique) and `.token` (unique, indexed) |
| Reaches the customer | Appended to `company.feedback_form_url` as a Google Forms prefill parameter — see §5 |
| Appears in the sheet | As the answer to the prefilled question, in its own column |
| Used for matching | Split on the first `.`; look up by **token**; verify the reference matches the same row. Mismatch → treat as missing |
| Missing | Fall back to `match_customer(mobile, email, company)` — exact only. If that succeeds, store the Feedback linked to the customer with `match_method=CONTACT`. If not → `UNMATCHED` |
| Duplicate response | Same `responseId` → idempotent no-op (§9). *Different* `responseId`, same request → store both, keep `FeedbackRequest.status=COMPLETED`, flag the second as `match_status=DUPLICATE_REQUEST` for admin review. Never silently drop |
| Unmatchable | Stored anyway, `match_status=UNMATCHED`, visible in Feedback ▸ Sync with a **Resolve** action that lets an admin attach it to a customer/request by hand |

### Match precedence

```
1. token           exact, unguessable, one row      → MATCH_TOKEN
2. mobile          normalised digits, exact         → MATCH_CONTACT
3. email           lowercased, exact                → MATCH_CONTACT
4. company name    whitespace-normalised, exact     → MATCH_CONTACT
5. nothing                                          → UNMATCHED
```

Steps 2-4 are `customer_timeline.match_customer` unchanged — already exactly
this order, already exact-only.

### Why not fuzzy matching

Asked for explicitly, so stated plainly. Fuzzy matching on company name
(Levenshtein, trigram, token-set) would attach a response to the wrong account
some fraction of the time, and **the failure is silent and permanent**:

- The customer never sees it, so it is never reported.
- A wrong rating lands in the wrong department's rolling average, which drives
  alerts, which drive notifications to a department head about work their team
  did not do.
- "Shah Foods" vs "Shah Foods & Beverages" vs "Shah Food Products" are three
  different accounts in a real SAP book. Nothing in the string says which.

An unmatched response sitting in a review queue costs an admin thirty seconds.
A mismatched one corrupts the analysis and nobody finds out. **Unmatched is the
safe failure; mismatched is not.**

---

## 5. Google Form design

**The company's form has not been supplied** (§0). This section therefore
specifies *the minimum change* to whatever form exists, and preserves every
existing question untouched.

### The one required change

Add **one short-answer question** to the top of the form:

> **Reference code** *(required, do not edit)*
> Short answer

That is the whole change. No existing question is altered, renamed, reordered
or removed — the mapper resolves them by content, not position
(`feedback_mapping.py:104`).

The company then supplies the field's prefill entry id, obtained once via
Google Forms ▸ ⋮ ▸ **Get pre-filled link**: the URL contains `entry.4839201=…`.
That id goes into a new setting `feedback.form_reference_entry_id`.

If the company declines to add the field, the system still works — every
response falls to contact matching, and anything ambiguous lands in the
UNMATCHED queue. Expect a materially higher unmatched rate, because a customer
typing their own company name will not match SAP's spelling.

### Field roles

| Role | Source | Mapper key |
|---|---|---|
| Unique request ID | **new prefilled question** | `feedback_reference` *(new)* |
| Submission timestamp | Google's own | `submitted_at_source` |
| Customer information | existing questions | `customer_name`, `company_name`, `mobile`, `email` |
| Feedback metadata | existing | `handled_by_name` |
| Overall rating | existing | `overall_rating` |
| Department rating | existing | matched by `DEPARTMENT_RATING_PATTERNS` |
| Department comment | existing | matched by `DEPARTMENT_COMMENT_PATTERNS` |
| Additional comments | existing | `overall_comments` |
| Would recommend | existing | `would_recommend` |

Only `feedback_reference` is new. Everything else is already in
`FEEDBACK_COLUMN_MAP`.

---

## 6. Google Sheet structure

The linked response sheet is whatever Google generates — column order is
Google's, headers are the question text. **The plan does not require a
particular layout**, because the mapper resolves by content.

Expected shape, with the mapping the portal applies:

| Sheet column (header = the question) | Portal field |
|---|---|
| `Timestamp` | `Feedback.submitted_at_source` |
| `Reference code` | `FeedbackRequest.token` → the match |
| `Your name` | `Feedback.customer_name` |
| `Company name` | `Feedback.company_name` |
| `Mobile number` | `Feedback.mobile` |
| `Email address` | `Feedback.email` |
| `BDE / salesperson who handled you` | `Feedback.handled_by_name` → `handled_by_user_id` |
| `Overall, how satisfied are you…` | `Feedback.overall_rating` + `overall_rating_raw` |
| `Would you recommend us…` | `Feedback.would_recommend` |
| `How would you rate our Production team?` | `FeedbackDepartmentRating(department=Production).rating` |
| `Any comments about Production?` | `FeedbackDepartmentRating(department=Production).comments` |
| `Any other comments or suggestions` | `Feedback.overall_comments` |
| *(added by Apps Script)* `Synced at` | write-back marker, portal never reads it |

Ratings are normalised onto `feedback.rating_scale_max` by
`feedback_mapping.parse_rating`, which handles `4`, `4/5`, `Very satisfied (5)`
and rescales when the form's scale differs. `raw_value` keeps the original.

**A header the mapper cannot resolve is reported, never guessed** — same rule as
the file importer, surfaced in the Sync panel instead of the dry run.

---

## 7. Integration architecture

| Option | Verdict |
|---|---|
| **A. Portal polls the Sheets API** | ❌ Needs a Google Cloud project, a service account, OAuth scopes, key rotation, and the sheet shared with a robot account. **The company has supplied no Google credentials.** Heaviest option, most to keep working |
| **B. Apps Script on submit → portal webhook** | ✅ **Recommended.** Runs as the form owner, so **no credentials leave Google and none enter the portal**. Feedback lands in seconds |
| **C. Scheduled sync job** | ⚠️ Useful as a *safety net*, not a primary. Same credential problem as A if it pulls from Sheets — avoided by pulling from Apps Script instead |
| **D. Manual sync** | ⚠️ Required as a fallback and for reconciliation; unacceptable as the primary — it reintroduces the human step this work exists to remove |

### Recommendation: **B primary + a pull-based C/D fallback**

```
PRIMARY (event-driven)
  Form submit → Apps Script trigger → POST /api/feedback/sync/webhook

FALLBACK (pull, admin-triggered or scheduled)
  Portal → GET <Apps Script Web App URL>?since=<watermark>
        → returns rows not yet marked "Synced at"
```

The fallback deliberately pulls from **Apps Script**, not from the Sheets API.
Same shared secret, same payload shape, one code path on the portal side — and
still no Google credentials on the portal.

### Tradeoffs, stated honestly

| | Cost |
|---|---|
| **B needs a public HTTPS URL.** The portal currently runs on `localhost:8000`. Until it is deployed, the webhook physically cannot fire | This is why the fallback is MVP, not future scope: the pull path works from localhost today (outbound), so the integration is testable before deployment |
| Apps Script quotas | ~20k `UrlFetch` calls/day on a consumer account. Feedback volume is nowhere near |
| A Google outage delays feedback | Apps Script retry queue + the pull fallback both recover it |
| Apps Script is code the company owns | It lives outside this repo. Ship it as a reviewed file in `docs/apps-script/` with install instructions |

---

## 8. Secure webhook design

`POST /api/feedback/sync/webhook` would be **the only unauthenticated route in
the portal.** That deserves justification and hardening, not a shrug.

It cannot use the bearer token: Apps Script has no portal session, and putting a
long-lived portal JWT into a script the company edits is worse than a scoped
shared secret that does exactly one thing.

### Layers, in order of evaluation

| # | Control | Design |
|---|---|---|
| 1 | **HTTPS** | Deployment requirement. Reject plain HTTP at the proxy |
| 2 | **HMAC signature** | `X-Portal-Signature: t=<unix>,v1=<hex>`, where `v1 = HMAC_SHA256(GOOGLE_SYNC_SECRET, f"{t}.{raw_body}")`. Compared with `hmac.compare_digest` |
| 3 | **Replay window** | Reject `\|now − t\| > 300s`. Bounds a captured request to five minutes |
| 4 | **Idempotency** | Even inside the window, a replayed `responseId` is a no-op (§9) |
| 5 | **Body cap** | Reject > 64 KB before parsing |
| 6 | **Rate limit** | `SlidingWindowLimiter`, e.g. 60/min per source IP. Reuses `core/ratelimit.py` |
| 7 | **Schema validation** | Pydantic; unknown top-level fields rejected |
| 8 | **Value validation** | See below |
| 9 | **IP allowlist** | ⚠️ *Not recommended.* Google's Apps Script egress ranges are undocumented and change. A stale allowlist is an outage that looks like a bug. Note it as a deployment-level option if the company's network team wants it |

### Data validation — the spreadsheet is not trusted

| Field | Rule |
|---|---|
| `responseId` | ≤128 chars, printable ASCII |
| reference/token | Must match a `FeedbackRequest.token` **that exists**. A token for a `CANCELLED` request → UNMATCHED, not an attach |
| ratings | Parsed by `feedback_mapping.parse_rating`; out of range → the rating is dropped, the response is still stored, the row is flagged |
| comments | Length-capped, stored as text, **never rendered as HTML** — the frontend already escapes by default (no `dangerouslySetInnerHTML` anywhere in the chat or feedback components) |
| timestamps | Parsed by `feedback_mapping.parse_timestamp`; unparseable → fall back to received-at, flagged |
| department names | Resolved against existing departments. Unknown → **the file importer creates it**; the webhook should **not**. A typo in a form question would otherwise silently spawn a department. Unknown → store the rating raw, flag `UNKNOWN_DEPARTMENT`, let an admin map it |
| customer identity | Never taken from the payload. Derived from the token, or from exact contact match. The payload cannot name a `customer_id` |

The last row is the important one: **no field in the payload can address a
portal record directly.** Same principle as the chat tool schemas.

### Secret handling

`GOOGLE_SYNC_SECRET` in `backend/.env`, never in `app_settings` (an admin UI
that can read a secret back is a secret you have lost), never returned by any
endpoint, never logged. The admin UI shows `configured` / `not configured`,
exactly like the assistant's key.

---

## 9. Idempotency

**Mandatory, and layered.**

### Primary key: `external_response_id`

Use a **Form-bound** `onFormSubmit` trigger (`FormApp`), not a Sheet-bound one,
because the event gives `e.response.getId()` — a stable, Google-assigned id for
that response. A Sheet-bound trigger only knows a row number, and row numbers
shift when anyone sorts or deletes.

`feedback_sync_events.external_response_id` is **UNIQUE**. A repeat delivery hits
the constraint and returns `200 {"status":"duplicate"}` — a 200, deliberately,
so Apps Script stops retrying.

### Second line: `source_row_hash`

Already exists and is already unique (`models/feedback.py:57`). If a response
arrives through the *webhook* and later through a *file import* of the same
sheet, the hash catches it even though the ids differ.

### Third line: the request's own state

A `FeedbackRequest` already `COMPLETED` does not transition again. The second
response is stored and flagged, not merged.

### Ordering

The sync event row is inserted and **committed first**, before processing. If
the process then crashes, the delivery is on record as `FAILED` rather than
vanishing — and a retry finds the unique id and does not double-process.

---

## 10. Status model

### `FeedbackRequest.status`

```
        SENT ──────────────► COMPLETED     (a response matched)
          │
          ├────────────────► EXPIRED       (no response after N days)
          └────────────────► CANCELLED     (BDE withdrew it)
```

Four states. **`PENDING` is deliberately not one of them.**

Adding a PENDING row for every dispatched lead and converted customer would
duplicate a queue that already works (`feedback_analysis.pending_requests`),
require backfilling every historical record, and need a second mechanism to keep
the rows in step with the derived source. "Not yet asked" is the *absence* of a
request, and modelling absence as a row is how you get two disagreeing queues.

`EXPIRED` is a background transition (`expired_after_days`, configurable,
default 30). It exists so the response-rate metric has a denominator that
stops growing, and so the UI can distinguish "waiting" from "they are not
going to answer".

### `Feedback.match_status` — a property of the *response*

```
MATCHED_TOKEN     the reference resolved      (the happy path)
MATCHED_CONTACT   token missing, contact matched exactly
UNMATCHED         stored, needs a human
DUPLICATE         a second response for an already-completed request
```

`UNMATCHED` belongs here, not on the request — an unmatched response by
definition has no request to sit on.

### `FeedbackSyncEvent.status`

`RECEIVED → PROCESSED | FAILED | DUPLICATE`. This is the reconciliation ledger.

---

## 11. UI changes

Minimal, inside the existing visual language. No new page.

### Feedback ▸ Pending becomes three states in one table

| State | Row shows |
|---|---|
| Not asked | `[Send request]` — unchanged |
| Awaiting response | `FB-2026-00127` · sent 3 days ago · `[Resend]` `[Cancel]` |
| Received | ✓ Feedback received · 4/5 · `[View feedback]` |

Today a customer who was *asked* still shows as "pending" until they answer,
because the queue only knows whether a `Feedback` row exists. With request rows,
"asked and waiting" becomes visible — which is the actual daily question a BDE
has.

### KPI strip above the table

Four tiles, reusing `StatTile`:

- **Awaiting response** — `SENT` requests
- **Received this month**
- **Response rate** — `COMPLETED / (COMPLETED + EXPIRED)`, excluding still-open
- **Average rating** — over the same window the analysis uses

### New tab: Sync *(Admin only)*

Only appears for `AdminUser`. Shows:

- Provider status: webhook configured / not, last delivery, last successful sync
- `[Sync now]` — the pull fallback (§16)
- **Needs review**: `UNMATCHED` and `DUPLICATE` responses with `[Resolve]`
- **Failed deliveries**: `FeedbackSyncEvent.status = FAILED` with the reason and `[Retry]`

The existing Import tab stays exactly as it is — the file path remains valid for
historical backfill.

### Not doing

No redesign of the analysis, responses or alerts tabs. No new charts. No change
to the send dialog beyond the link it renders.

---

## 12. Feedback details view

A `Modal` (the existing component), opened from **View feedback** in Pending and
from the Responses table.

```
Shah Foods & Beverages                    ✓ Received
FB-2026-00127 · requested 4 Sep by Parth Fulvani · submitted 7 Sep

Overall              4.0 / 5      "Would you recommend us?  Yes"

Departments
  Production         4 / 5        "Packaging quality improved a lot."
  Dispatch           2 / 5        "Two days late both times."
  Accounts           5 / 5        —

Additional comments
  "Overall happy. Please keep the same account manager."

Handled by           Parth Fulvani
Matched by           Reference code
Source               Google Form · response 2_ABaOnu…
```

Every field already exists on `Feedback` / `FeedbackDepartmentRating` except
`FB-…`, `requested`, and `Matched by`, which come from the new tables.

**Scope:** rendered through the existing `feedback_department_scope` filter — a
department head sees only the department rows they may read, exactly as
`api/feedback.py:214-287` already does for the list.

---

## 13. Department analysis

**No calculation changes.** `feedback_analysis.department_summary` already
computes average, count and flagged-state over a rolling window, and
`evaluate()` already opens and resolves alerts.

What changes is only that rows arrive continuously instead of in batches. Two
consequences worth designing for:

| Concern | Handling |
|---|---|
| `evaluate()` currently runs on import and on demand | Also call it after each successful webhook ingest, inside the same transaction |
| Alert flapping — one response crossing the line repeatedly | `alert_min_responses` already guards this (an alert needs enough evidence). Verify the value is sensible once real volume exists; do not add hysteresis speculatively |
| Trend | `responses_per_month` unchanged |
| Lowest-performing department | Already `department_summary` sorted by average |

**The threshold stays configurable** — `feedback.alert_threshold`,
`feedback.alert_window_days`, `feedback.alert_min_responses`,
`feedback.rating_scale_max`, all in Settings. This plan reads them and hardcodes
nothing.

---

## 14. Notifications

Two events, mapped onto the existing permission model — no new fan-out rules.

### Feedback received → the request's owner

```
"Feedback received from Shah Foods — 4/5"
```

Recipient: `FeedbackRequest.owner_user_id` (the BDE who sent it). Nobody else —
a manager does not need a notification per response; they have the dashboard.

New `NotificationType.FEEDBACK_RECEIVED`. `dedupe_key =
f"feedback:{feedback_id}"` so a retry cannot double-notify.

### Low department rating → **already built**

`feedback_analysis._notify` (`:178-199`) already notifies the department head
plus every Admin and Super Admin, deduped per department per day. Calling
`evaluate()` after ingest is all that is needed. **Do not add a second
per-response low-rating notification** — it would fire on single data points and
train people to ignore the alert that matters.

### Unmatched response → Admins

```
"A feedback response could not be matched to a customer."
```

Deduped per day, so a bad form change produces one notification rather than
forty. Links to Feedback ▸ Sync.

---

## 15. Error handling

The governing rule: **a response that reached the portal is never lost.** The
sync-event row is written and committed before processing, so every failure has
a record and a retry path.

| Failure | Portal behaviour | Visible where |
|---|---|---|
| Google Sheets unavailable | Not the portal's problem — Apps Script holds the row in its retry queue | — |
| Apps Script fails | Row stays unmarked in the sheet; the pull fallback picks it up | Sync panel: "N rows in the sheet not yet synced" |
| Portal unreachable | Apps Script retries with backoff (5 attempts over ~1h), then leaves it unmarked for the pull | Sync panel |
| Invalid/unknown reference | Stored, `UNMATCHED`, admin notified | Sync ▸ Needs review |
| Duplicate `responseId` | `200 {"status":"duplicate"}`, no write | Sync event ledger |
| Second response, same request | Stored, `DUPLICATE`, flagged | Sync ▸ Needs review |
| Customer not found | Stored, `UNMATCHED` | Sync ▸ Needs review |
| Invalid rating | Rating dropped, response kept, flagged | Details view shows `raw_value` |
| Unknown department | Rating kept raw, **department not auto-created**, flagged | Sync ▸ Needs review, with a map-to-department action |
| Malformed payload | `422`, sync event `FAILED` with the validation error | Sync ▸ Failed |
| Bad signature | `401`, **no sync event row** (unauthenticated noise must not be a storage vector), counter incremented | Logged, rate-limited |
| Database unavailable | `503`; Apps Script retries | Server log |

**PII in logs:** the sync event stores the payload **hash**, not the payload.
Failure logs record the `responseId` and the error class — never the customer's
name, mobile, email or comments. Same rule as the chat audit rows.

---

## 16. Manual sync fallback

`POST /api/feedback/sync/run` — `AdminUser`, rate-limited.

Calls the Apps Script Web App with the shared secret and a `since` watermark,
receives the rows Apps Script has not marked synced, and runs them through
**exactly the same ingest path** as the webhook. Idempotency makes it safe to
press repeatedly — a row already processed returns `duplicate` and is skipped.

Returns, and the panel shows:

```
Last sync            7 Sep 2026, 2:14 pm  (by Shail Patel)
New responses        3
Already synced       11
Unmatched            1
Errors               0
```

Optionally scheduled later (§22 future scope) — for MVP it is a button, because
a scheduled job that nobody watches fails silently.

---

## 17. Security & privacy

| Requirement | How |
|---|---|
| No public feedback data endpoint | The webhook **accepts** and returns `{status}`. It never returns feedback data. Everything readable stays behind `CurrentUser` |
| Authentication required | Every read endpoint unchanged. The webhook is signature-authenticated, not anonymous |
| Role-based access | Unchanged: `feedback_department_scope` for analysis and responses, `VisibilityScope` for the pending queue, `AdminUser` for Sync |
| Webhook authentication | HMAC + replay window + idempotency (§8) |
| No secrets to the frontend | `GOOGLE_SYNC_SECRET` and the Web App URL are env-only. `/feedback/sync/status` returns booleans, exactly like `/chat/status` |
| No Google credentials | **None exist.** Apps Script runs as the form owner; the portal holds only a shared secret |
| PII minimised in logs | Payload hash, not payload. No name/mobile/email/comment in any log line |
| No customer data in errors | Error responses carry a code and a generic sentence — the existing `ApiError` envelope |
| Comments are untrusted text | Stored as text, escaped on render. React escapes by default and nothing in the feedback components uses `dangerouslySetInnerHTML` |

---

## 18. Database changes

Migration **`0006_feedback_requests.sql`** (next in sequence after `0005_chat.sql`),
using the established placeholders `{{UUID}} {{TS}} {{NOW}} {{JSON}} {{BOOL}}`
and the dialect blocks added in `db/render.py`.

### New: `feedback_requests`

```sql
id                 {{UUID}}     PRIMARY KEY
reference          VARCHAR(20)  NOT NULL UNIQUE     -- FB-2026-00127
token              VARCHAR(64)  NOT NULL UNIQUE     -- the unguessable half
subject_type       VARCHAR(10)  NOT NULL            -- LEAD | CUSTOMER
lead_id            {{UUID}}     REFERENCES leads(id)     ON DELETE CASCADE
customer_id        {{UUID}}     REFERENCES customers(id) ON DELETE CASCADE
owner_user_id      {{UUID}}     REFERENCES users(id)     ON DELETE SET NULL
status             VARCHAR(20)  NOT NULL DEFAULT 'SENT'
channel            VARCHAR(20)                      -- WHATSAPP | EMAIL
sent_at            {{TS}}       NOT NULL DEFAULT {{NOW}}
completed_at       {{TS}}
expires_at         {{TS}}
created_at         {{TS}}       NOT NULL DEFAULT {{NOW}}

CONSTRAINT ck_feedback_requests_status
  CHECK (status IN ('SENT','COMPLETED','EXPIRED','CANCELLED'))
CONSTRAINT ck_feedback_requests_subject
  CHECK ((lead_id IS NOT NULL) <> (customer_id IS NOT NULL))
```

The second constraint enforces the README's "two kinds of record, deliberately
kept apart" at the schema level: exactly one of the two.

### New: `feedback_sync_events`

```sql
id                    {{UUID}}     PRIMARY KEY
external_response_id  VARCHAR(128) NOT NULL UNIQUE   -- the idempotency key
source                VARCHAR(20)  NOT NULL          -- WEBHOOK | PULL | FILE
payload_hash          VARCHAR(64)  NOT NULL          -- never the payload
status                VARCHAR(20)  NOT NULL          -- RECEIVED|PROCESSED|FAILED|DUPLICATE
feedback_id           {{UUID}}     REFERENCES feedback(id) ON DELETE SET NULL
error_code            VARCHAR(40)
error_detail          VARCHAR(500)                   -- no PII
received_at           {{TS}}       NOT NULL DEFAULT {{NOW}}
processed_at          {{TS}}
```

### Altered: `feedback` — three columns

```sql
feedback_request_id  {{UUID}}     REFERENCES feedback_requests(id) ON DELETE SET NULL
match_status         VARCHAR(20)  NOT NULL DEFAULT 'MATCHED_CONTACT'
external_response_id VARCHAR(128)                    -- convenience, not the unique key
```

### Deliberately NOT added

- `submitted_at` — `submitted_at_source` already exists
- `status` on `feedback` — a response has no lifecycle; the *request* does
- `source` on `feedback` — exists; add `GOOGLE_FORMS_WEBHOOK` to the enum instead
- `sync_status` on `feedback` — that is what `feedback_sync_events.status` is for

### Constants

`FeedbackSource.GOOGLE_FORMS_WEBHOOK`; `FeedbackRequestStatus`,
`FeedbackMatchStatus`, `SyncEventStatus` enums;
`NotificationType.FEEDBACK_RECEIVED`, `.FEEDBACK_UNMATCHED`;
`AuditAction.FEEDBACK_REQUEST_SENT`, `.FEEDBACK_RECEIVED`, `.FEEDBACK_RESOLVED`;
`EntityType.FEEDBACK_REQUEST`.

### Backfill

None required. Historical `Feedback` rows keep `feedback_request_id = NULL` and
`match_status = MATCHED_CONTACT`, which is what they in fact were. The derived
pending queue keeps working for anything with no request row.

---

## 19. What the company must provide

| # | Item | Why | Blocking? |
|---|---|---|---|
| 1 | **The Google Form's public URL** | `company.feedback_form_url` is empty. Nothing can be sent without it | **Yes** — blocks everything, and already blocks Send request today (Q5) |
| 2 | **The form's questions** (or an .xlsx export) | To confirm the mapper resolves them and the department wording matches | **Yes** for correctness |
| 3 | **A new "Reference code" short-answer question** | §5. The one form change | **Yes** for token matching; without it everything falls to contact matching |
| 4 | **Its prefill `entry.NNNNNNN` id** | From ⋮ ▸ Get pre-filled link | **Yes**, with #3 |
| 5 | **Edit access to the form + linked sheet** | To install the Apps Script trigger | **Yes** |
| 6 | **A Google account that may run Apps Script** | Consumer or Workspace, must own or co-own the form | **Yes** |
| 7 | **A public HTTPS URL for the portal** | The webhook target | For the *webhook*. The pull fallback works without it |
| 8 | **The rating scale actually used** | 1-5? 1-10? worded? Sets `feedback.rating_scale_max` | Yes for correct normalisation |
| 9 | **Confirmation of the department list** | Sales, Production, Quality, Dispatch, Accounts, Customer Support | Yes — the webhook will not auto-create |

**Not needed, and worth saying:** no Google Cloud project, no service account,
no OAuth client, no API key, no billing account. The Apps Script approach avoids
every one of them.

---

## 20. Test plan

All backend, following existing patterns in `backend/tests/`. No test may
require a real Google account or network.

| # | Test | Asserts |
|---|---|---|
| 1 | New submission, valid signature | 200, `Feedback` created, ratings created |
| 2 | Matching by token | `feedback_request_id` set, `match_status=MATCHED_TOKEN` |
| 3 | Status transition | `FeedbackRequest.status` `SENT → COMPLETED`, `completed_at` set |
| 4 | Data stored correctly | Overall, per-department ratings, comments, `raw_value` preserved |
| 5 | Duplicate `responseId` | Second call 200 `duplicate`, **one** `Feedback` row |
| 6 | Missing reference | Falls back to contact match, `MATCHED_CONTACT` |
| 7 | Invalid/unknown token | `UNMATCHED`, response still stored, admin notified |
| 8 | Unknown customer | `UNMATCHED`, not attached to anything |
| 9 | Invalid rating | Rating dropped, response kept, `raw_value` retained |
| 10 | Unknown department | Rating flagged, **department NOT created** |
| 11 | Bad signature | 401, no `Feedback`, no sync event |
| 12 | Replay outside window | 401 even with a valid signature |
| 13 | Manual sync | Idempotent across two consecutive runs |
| 14 | Two responses, one request | Both stored, second `DUPLICATE`, request stays `COMPLETED` |
| 15 | Dashboard | `feedback_pending` drops, average moves |
| 16 | Department analysis | Average recomputed, alert opens when below threshold |
| 17 | Notification | Owner notified once; deduped on retry |
| 18 | Failure recovery | Ingest raises → sync event `FAILED`, retry succeeds, no duplicate |
| 19 | **Scope** | A BDE sees only their own requests; a department head sees only their department's ratings |
| 20 | **No PII in logs** | `caplog` contains no name/mobile/email/comment |
| 21 | **Secret never exposed** | Absent from `/feedback/sync/status`, from any response body, from logs |
| 22 | Oversized body | >64 KB rejected before parsing |

Signature helper in `conftest.py`; a fixture builds a signed request so tests
exercise the real verification rather than bypassing it.

---

## 21. Implementation phases

Each phase leaves the suite green and the app working.

| Phase | Deliverable | Gate |
|---|---|---|
| **1** | This audit | Approved |
| **2** | Migration `0006`, models, constants, enums | `test_schema_parity` green |
| **3** | `feedback_ingest.py` — extract the shared row→Feedback path from `feedback_import.commit()` | **File import still passes every existing test.** This is the riskiest refactor; do it before anything depends on it |
| **4** | `POST /feedback/requests` + reference/token generation + `messaging` prefill | Send request creates a row; the link carries the code |
| **5** | Pending queue reads request rows; three-state UI | Awaiting-response visible |
| **6** | Webhook: signature, replay, idempotency, sync events — **reject-only, no ingest** | Security tests 11, 12, 21, 22 green |
| **7** | Ingest wired into the webhook; matching; status transitions | Tests 1-10, 14 green |
| **8** | Notifications + `evaluate()` after ingest | Tests 16, 17 green |
| **9** | Apps Script in `docs/apps-script/`, with install instructions | Reviewed, not yet installed |
| **10** | Pull fallback + `[Sync now]` + Sync tab + Resolve | Test 13 green |
| **11** | KPI strip, details modal | `npm run check` green |
| **12** | Live end-to-end with the real form | Acceptance criteria (§23) |

Phase 6 before phase 7 is deliberate: **the door is built and proven locked
before anything is allowed through it.**

---

## 22. MVP vs future

### MVP

Form → Sheet → Apps Script → webhook → matched → `COMPLETED` → visible → analysis
updates. Specifically: phases 2-11, token matching with contact fallback,
idempotency, the unmatched queue with manual resolve, the manual sync button,
owner notification, three-state pending UI, details modal.

### Future — explicitly out of scope

- Scheduled automatic sync (cron/APScheduler)
- Reminder chasing for `SENT` requests nearing expiry
- Public feedback links generated by the portal itself (the `PUBLIC_LINK`
  source enum already anticipates this) — would remove Google entirely
- Per-department trend charts beyond the existing monthly volume
- Sentiment analysis of comments (the assistant could summarise them — a
  separate piece of work)
- Multi-form support
- Webhook delivery of feedback *out* to other systems

Scope creep to resist: this is "close the loop", not "rebuild feedback".

---

## 23. Acceptance criteria

1. A BDE presses **Send request**; a `FeedbackRequest` is created with a visible reference.
2. The message carries a link whose reference field is prefilled.
3. The customer submits; the response appears in the sheet.
4. The portal receives it **without anybody pressing anything**.
5. The correct customer/request is identified by token.
6. `SENT → COMPLETED`, and the pending row flips to ✓ Feedback received.
7. The response is visible in Responses and in the customer's timeline.
8. The owning BDE sees it and is notified.
9. Authorised management can analyse it, within their department scope.
10. Department averages and alerts update.
11. Sending the same response twice creates **one** record.
12. A response that cannot be matched is visible and resolvable, never lost.
13. An unauthenticated or replayed request is rejected.
14. No secret, no Google credential and no customer PII appears in the frontend, in logs, or in any error message.

---

## Final summary

**1. Recommended architecture** — Apps Script `onFormSubmit` → HMAC-signed
portal webhook, with a pull-based manual sync through the same Apps Script as
fallback and reconciliation. No Google credentials anywhere in the portal.

**2. Google Form changes** — one added short-answer question, "Reference code",
prefilled. No existing question touched.

**3. Google Sheet structure** — whatever Google generates; resolved by content
through the existing `feedback_mapping.py`. One added write-back column,
`Synced at`, which the portal never reads.

**4. Backend changes** — `feedback_ingest.py` (extracted, shared), the sync
router (`webhook`, `run`, `status`, `resolve`), `feedback_requests` service,
reference/token generation, `messaging` prefill, `evaluate()` after ingest, two
notification types.

**5. Frontend changes** — pending queue gains two states and a KPI strip; a
details modal; an admin-only Sync tab. No redesign.

**6. Database changes** — `0006`: two new tables, three columns on `feedback`,
new enum values. No backfill.

**7. Security** — HMAC-SHA256 + 5-minute replay window + unique-id idempotency +
rate limit + size cap + strict schema; no payload field can address a portal
record; secret is env-only and never returned.

**8. Company inputs** — nine items in §19; items 1-6 and 8-9 block, item 7
(public HTTPS) blocks only the webhook, not the pull path.

**9. Testing** — 22 backend tests in §20, none needing a real Google account.

**10. MVP** — phases 2-11.

**11. Future** — scheduled sync, reminders, portal-native public links.

**12. Complexity** — **Medium.** Roughly:

| Part | Effort | Risk |
|---|---|---|
| Migration + models | Small | Low |
| Extracting `feedback_ingest` | Medium | **Highest in the plan** — it touches working import code. Phase 3, behind the existing tests |
| Request creation + prefill | Small | Low |
| Webhook + security | Medium | Medium — new unauthenticated surface, mitigated by building it reject-only first |
| Matching + statuses | Medium | Medium |
| Apps Script | Small | Medium — lives outside the repo, cannot be unit-tested here |
| UI | Medium | Low |
| **Total** | ~11 phases | The genuine unknowns are the real form and a deployed HTTPS URL, both external |

**13. Exact implementation order** — the phase table in §21, in order. Phase 3
before anything depends on it; phase 6 before phase 7.

---

### Two things I would flag before you approve

**The form URL is still missing.** `company.feedback_form_url` is empty, which
already prevents Send request from working today. That is item 1 in §19 and it
blocks the whole feature, not just this integration.

**The webhook needs the portal deployed on public HTTPS.** On `localhost` Google
cannot reach it. The pull fallback is in the MVP specifically so the integration
can be built, tested and used before that exists — but if deployment is far off,
say so and I would reorder phases 9-10 ahead of 6-7.

---

## IMPLEMENTATION LOG

Approved and built. Phases 1-11 are complete; phase 12 (live end-to-end
against the real form) waits on the company inputs in §19.

**447 backend tests pass** (35 new), `npm run check` is clean, and the new
modules are pyright-clean. Verified in real Chrome as an admin and as a BDE.

### Where things differ from the plan

| § | Deviation | Reason |
|---|---|---|
| 9 | `source_row_hash` no longer REJECTS a response whose content matches an existing row - it stores it without a hash and labels it `DUPLICATE`. | Two genuinely different responses with identical answers (same second, same wording) collided on the content hash and the second was lost to an IntegrityError. `external_response_id` is the identity for a delivery; the content hash is only the cross-source fallback. Losing a real customer's feedback to a hash collision is not a trade worth making. |
| 11 | The pending queue keeps answered rows out entirely rather than showing a third "Received" state. | "Pending" means still owed. Including answered rows would have made the dashboard KPI count things nobody owes. The received ones live in Responses, where they always did. |
| 12 | The details modal was not built. | Every field it would show is already on the Responses tab, and the Sync tab covers what was actually missing. Deferred rather than duplicated. |
| 13 | `evaluate()` is called after each successful ingest, inside the same transaction, as planned - no other analysis change was needed. | The calculation was already right. |

### Behaviour changes worth knowing

**An asked lead no longer disappears from the pending queue.** It stays, as
`AWAITING`, until somebody answers. Two existing tests asserted the old
behaviour and were updated: a BDE could not previously tell "nobody has
chased this" from "chased, waiting on them", which is the whole point of
making requests records.

The dashboard's `feedback_pending` count therefore no longer drops when a
request is sent - only when a response arrives.

### Found while building

* **`/api/users/actionable` 403'd on every BDE load of the feedback page.**
  Pre-existing: a Manager+ endpoint fetched unconditionally to fill the
  alert-assignee dropdown, which a BDE never sees. Now guarded.
* **The schema-parity helper read the FIRST definition of a CHECK constraint**
  across the concatenated migrations, so it could not see a later migration
  widening one. It now takes the last, which is the one the database has.

### Not yet possible

Everything in §19 still blocks the live path, and item 1 - the form URL -
already blocked *Send request* before this work started. Until it is set,
requests are created and references issued, but the link carries no code and
every response falls back to contact matching. The Sync tab says so in plain
words rather than pretending the integration is finished.
