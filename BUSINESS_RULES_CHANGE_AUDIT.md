# Business rules change — Phase 0 audit

**Status: audit only. No behaviour has been changed.**

Read against the code as it stands on 11 September 2026, and against the live
development database. Every count in this document was measured, not estimated.

Nine of the requested changes are straightforward. **Six are blocked on a
business decision**, because the specification and the existing implementation
genuinely disagree and §42 says not to invent the answer. Those are collected
in [Blocking questions](#blocking-questions) at the end; everything above it is
what I found.

---

## Summary

| # | Change | Verdict |
|---|---|---|
| 1–3 | Management "Assigned by me" view + assignee filter | **Clear.** Fields already exist (`leads.assigned_by_user_id`). |
| 4 | Remove follow-up at assignment | **Clear.** Already optional in the backend; only the dialog asks for it. |
| 5–6 | 10-digit phone validation | **Clear, but existing data violates it** — see §5. |
| 7–9 | SAP 10-day eligibility | **Blocked** — which invoice date, and what happens to converted leads. |
| 10 | Customers → Super Admin only | **Blocked** — as written this breaks Reference Tracking for everyone else. |
| 11 | Reference KPI done/total | Clear once §12–15 are settled. |
| 12–16 | "Not shared" outcome | **Mostly clear**; one naming decision. |
| 17–18 | Lead log ownership | **Blocked** — contradicts §22 as written. |
| 19–21 | Lead state machine | **Blocked** — 9 states requested, 6 exist; mapping is a business decision. |
| 22 | Explicit Update button | Clear. |
| 23 | Remove Undo | Clear, with a consequence worth knowing — see §23. |
| 24, 37, 38 | History, notifications, audit | Already satisfied; verify after changes. |
| 25 | Converted = Won? | **Answered below — no change needed, but read it.** |

---

## 1–3. Management view: "My Work" → "Assigned by me"

**Current.** `frontend/src/app/(portal)/leads/page.tsx` has two tabs, defaulting
to `my-work` (line 51): `My work` and `All leads`. "My work" calls
`api.workQueue({ mine_only: true })`. There is no "assigned by me" concept in
the UI at all.

**Required.** For the assigner (management), replace "My Work" with "Assigned by
me", showing only leads *this authenticated user* assigned, with a per-assignee
filter and counts.

**Good news: the data already exists.** `leads.assigned_by_user_id` is written
on every lead at creation (`app/services/leads.py:191`, `create_lead`) and is a
real foreign key. Nothing needs to be back-filled for leads created through the
portal.

| | |
|---|---|
| Files | `app/api/leads.py` (`list_leads`, line 63), `app/services/leads.py` (`list_leads`, line 83), `frontend/src/app/(portal)/leads/page.tsx` |
| DB | `leads.assigned_by_user_id` — exists, no migration |
| API | `GET /api/leads` gains `assigned_by_me: bool` and `assigned_to_user_id: UUID`; a new counts endpoint or an aggregate on the existing one |
| Enforcement | The filter must resolve to `actor.id` **server-side**. The parameter must be a boolean flag, never a user id the client supplies — otherwise "assigned by me" becomes "assigned by anyone I can name". |

**Conflict — who is "management"?** The spec says "the management/assigner
view". The code has no `is_assigner` concept. The nearest real thing is
`LEADERSHIP_ROLES` (`SUPER_ADMIN`, `ADMIN`, `MANAGER`) — the roles that can
create and assign leads. **Assumption to confirm:** the tab set becomes
"Assigned by me" + "All leads" for those three roles, and BDE/SALES keep "My
work" + "All leads" unchanged. A manager who is also assigned leads personally
would lose their personal queue under a literal reading; that seems wrong, so
this needs a yes/no.

**Risk.** Low. Counts must be a grouped `COUNT(*)` server-side (§39), not a
page of rows counted in React.

---

## 4. Follow-up at assignment

**Current.** `next_follow_up_date` is **already optional** everywhere in the
backend: `Lead.next_follow_up_date` is nullable, `create_lead` takes
`next_follow_up_date: date | None = None`, and the schema does not require it.
Only the frontend dialog offers the field
(`frontend/src/components/leads/LeadDialogs.tsx:40, 77, 189`).

**Required.** Remove it from the assignment flow.

**Verdict.** Frontend-only change — remove the input and stop sending the key.
No migration, no API change, no validation change. The field itself stays,
because it is still set legitimately by `add_activity` when a BDE logs a call
and schedules the next one (`app/services/leads.py:442`). §4 explicitly warns
against removing that, and this is exactly the case it means.

**Risk.** Low, provided the field is removed only from the *assign* dialog and
not from the activity dialog.

---

## 5–6. Phone validation

**Current: there is none.** The only constraint anywhere is `max_length=30`
(`app/schemas/lead.py:66, 81`, `app/schemas/reference.py:30`). No regex, no
digit count, on any endpoint or any form.

**Existing data already violates the proposed rule** (measured just now):

| Table | Column | Rows | Exactly 10 digits | Violating |
|---|---|---|---|---|
| `customers` | `mobile` | 17 | 15 | **2** — `+91 97111 22505`, `+91 91001 88417` (12 digits, real SAP data) |
| `leads` | `mobile` | 8 | 6 | **2** — `asdfghJK`, `qwertyui` (letters, test rows) |
| `customer_references` | `referred_mobile` | 8 | 4 | **4** — `12345670` ×2, `123456789`, `tyunjmk ` |
| `users` | `phone` | 0 | — | — |

**On §6 (dangerous normalisation).** I traced
`messaging.whatsapp_number` (`app/services/messaging.py:110`). The specific
failure named in the spec does **not** occur: `"1"` returns `None`, not
`+91…`. The function strips non-digits, and returns a number only when the
result is exactly 10 digits, or ≥11 after stripping leading zeros. It is
already "return nothing rather than guess". What it does *not* do is validate
on the way **in** — anything can be stored, and the consequence shows up later
as an unsendable message. Validate at the boundary, keep this function as is.

**Conflict — imports.** §5 says the rule applies to "Imports, SAP/customer
imports where appropriate". Applied literally, the next SAP import **rejects
two real customers** whose number carries a `+91` country code. Those are
correct numbers in a different format. Options, needing a decision:

- **(a)** Validate *after* normalisation — strip `+91`/leading zeros first,
  then require 10 digits. `+91 97111 22505` becomes valid. Recommended.
- **(b)** Validate raw input strictly, and normalise SAP data in a one-off
  migration.
- **(c)** Validate portal forms strictly; accept SAP as the system of record
  and flag odd numbers rather than rejecting the row.

Existing dirty rows (letters, 8-digit numbers) also need a decision: leave them,
or null them in a migration. They cannot be *edited* after this change without
being corrected, which may be the desired forcing function.

| | |
|---|---|
| Files | new `app/core/validators.py` (one definition, §28), `app/schemas/lead.py`, `app/schemas/reference.py`, `app/schemas/user.py`, `app/schemas/customer.py`, `app/services/sap_import.py`, `app/services/feedback_ingest.py`, and the matching React forms |
| DB | none |
| Risk | **Medium** — an over-strict import rule silently drops customers. |

---

## 7–9. SAP → 10-day eligibility

**Current.** No waiting period exists. A customer is askable the moment it is
imported.

- **Reference:** `references.askable_accounts` / `askable_leads`
  (`app/services/references.py:294`) — converted leads by status, customers by
  visibility. No date condition.
- **Feedback:** `feedback_analysis.pending_requests`
  (`app/services/feedback_analysis.py:213`) — dispatched leads and customers
  with no response on file. No date condition.

**The source of truth exists.** `customers.first_invoice_date` and
`customers.last_invoice_date` are both populated for all 17 customers, and
`invoice_lines.invoice_date` holds the per-invoice date. `created_at` /
`imported_at` do not need to be used, exactly as §7 requires.

**Immediate impact: none.** Every current customer's invoice is from June–July
2026, so all 17 are already past 10 days on either field. The rule changes
nothing visible today and only affects future imports. That makes it safe to
implement — but it also means **it cannot be verified against real data** until
a fresh invoice arrives, so the tests in §31 become the proof.

**Blocked on two questions** — see [Q2](#q2) and [Q3](#q3):

1. **First or last invoice date?** `first_invoice_date` means "10 days after
   they became a customer" — a one-time gate. `last_invoice_date` means a
   repeat customer becomes *ineligible again* every time they are re-invoiced,
   which would pull accounts back out of the queue after they had already been
   asked. These are materially different rules.
2. **What about converted leads?** They have no invoice date at all —
   `Lead` has no such column, and a converted lead is not a SAP customer until
   it appears in an import under a SAP code. §26 forbids using the conversion
   date. So a literal reading **removes every converted lead from reference and
   feedback work indefinitely** — today that is 21 leads, the majority of the
   reference book.

---

## 10. Customer data → Super Admin only

**Current.** `app/api/customers.py` guards every route with `CurrentUser` +
`VisibilityScope` — any authenticated user, narrowed to the accounts they own
or manage. `customers.owner_user_id` exists precisely so a BDE owns accounts.

**This is the highest-risk item in the document.** Customers are not a
standalone module; they are the spine of two others:

| Depends on customer access | Where |
|---|---|
| Reference Tracking — the entire "won accounts" book | `references.askable_accounts`, `/references` |
| Feedback pending queue | `feedback_analysis.pending_requests` uses `visible_to(actor, scope)` |
| Sending a feedback request | `/customers/{id}/message`, the customer timeline |
| Dashboard KPIs | `customers_unowned`, references denominators, team rollup |
| Assistant | `list_customers`, `get_customer`, `get_customer_timeline` |

Restricting *customer records* to Super Admin would leave every BDE and manager
with an empty Reference Tracking module and an empty feedback queue. §10 itself
says "Do not accidentally break authorized lead/reference/feedback workflows" —
which cannot both be true under a literal reading.

**The distinction that probably resolves it:** the **Customers page** (the SAP
book browser, `/customers`, with contact details and invoice history) is a
different thing from **a customer record being readable by the workflow that
needs it**. My reading is that §10 means the former. That needs confirming
before anything is changed — see [Q1](#q1).

---

## 11. Reference KPI — done / total

**Current.** `references.stats` (`app/services/references.py:494`) returns
`references_taken` and a `reference_rate` percentage over `askable` = all
converted customers + all converted leads. The dashboard tile
(`frontend/src/app/(portal)/dashboard/page.tsx:114`) shows the bare number with
"% of the book" beneath.

**Required.** `4 / 5` plus "80% of converted customers", with the denominator
respecting 10-day eligibility.

**Verdict.** Straightforward once §7–9 and §12–15 are settled — the denominator
is `askable` filtered by eligibility, and the numerator depends on whether
"completed" or "received" is being shown (§15 says they must not be conflated).
Note the CoverageStrip on the dashboard already derives its denominator from the
four reference states; it will need the same eligibility filter or the two will
disagree (§27).

---

## 12–16. "Not shared"

**Current.**

```
ReferenceOutcome   YES, NO                                  (what was recorded)
ReferenceStatus    NOT_ASKED, TAKEN, PENDING, DECLINED      (the roll-up)
```

`record_ask` (`app/services/references.py:137`) maps **YES → TAKEN** and
**everything else → PENDING** with a follow-up date. **`DECLINED` is defined but
never written by any code path.** It is a dead state that the dashboard already
counts and charts (`references_declined`).

That is a lucky fit: **"Not shared" is what `DECLINED` was always meant to be.**
No new roll-up state is needed — only a third *outcome* value, because
`ReferenceOutcome` has no way to express "asked, answered, declined" as distinct
from "asked, come back later".

**Live data:** outcomes are `NO` ×14, `YES` ×8; customer roll-ups are
`NOT_ASKED` ×13, `PENDING` ×1, `TAKEN` ×3. Per §30, the 14 existing `NO` rows
must **stay** "Not right now" (`PENDING`) — they were recorded when that was the
only meaning available, and reclassifying them as "Not shared" would invent a
customer refusal that never happened.

**§15's distinction is real and currently missing.** Completed = TAKEN +
DECLINED; references *received* = TAKEN only. `references_taken` today means
"received", and the dashboard labels it "References taken". Once DECLINED is
reachable, one number cannot serve both and the label must change.

**§14's "+" rule** maps cleanly: show `+` only where `reference_status ==
TAKEN`. `DECLINED` and `PENDING` get no `+`.

| | |
|---|---|
| Files | `app/core/constants.py`, `app/services/references.py`, `app/schemas/reference.py`, `app/api/references.py`, `frontend/src/components/references/RecordReferenceDialog.tsx`, `frontend/src/app/(portal)/references/page.tsx` |
| DB | **Two CHECK constraints must change** — see below |
| Migration | None for existing *rows* (they stay as they are), but the constraints need widening via the SQLite table-rebuild pattern from `0006` |

**Confirmed against the live schema, and one of these is a genuine blocker:**

```sql
ck_customer_references_outcome  CHECK (outcome IN ('YES', 'NO'))
ck_customer_references_followup CHECK (outcome = 'YES' OR next_reference_date IS NOT NULL)
```

The first is expected: a third outcome needs the constraint widened.

**The second one blocks "Not shared" outright.** The database currently
*requires* a follow-up date for every outcome that is not `YES` — the schema
encodes the assumption that "not yes" always means "ask again later". A
"Not shared" row is by definition completed and has no follow-up date, so it
would violate this constraint. The rebuild must relax it to something like
"a follow-up date is required only for the outcome that means *come back
later*". This is the clearest evidence in the codebase that "Not right now"
and "Not shared" were never distinguished — the schema only ever allowed one
of them.

Good news on the roll-up: `ck_customers_reference_status` and
`ck_leads_reference_status` **already permit `DECLINED`** on both tables. If
"Not shared" maps to `DECLINED`, no roll-up migration is needed at all.

One naming decision remains: see [Q4](#q4).

---

## 17–18. Lead log ownership

**Current.** `add_activity` (`app/services/leads.py:442`) performs **no
ownership check at all**. The API route (`app/api/leads.py:267`) resolves the
lead through `get_lead(db, scope, lead_id)` — a *visibility* scope, not an
ownership check. So today any manager or admin who can see a lead can log
activity on it.

**Required.** Only the assigned employee may edit/log. Admin and manager
explicitly cannot.

**This contradicts §22 as written.** §22 puts a status dropdown and an Update
button in the **All Leads** view — which is the management view — and §1–3 build
that view for managers. If only the assignee may change anything, the Update
button in a manager's All Leads view can never work. Either:

- status changes are *not* "the operational lead log" and managers keep them, or
- the Update button is for the assignee only and managers get a read-only view.

Both are defensible; they are different products. See [Q5](#q5).

Note also that `change_status` and `add_activity` are separate service
functions today, so whichever answer is chosen is cheap to implement — the
distinction already exists in the code.

**§18 is already satisfied**: read is `VisibilityScope`, and edit would become a
separate, narrower check. Nothing about read permissions needs to move.

---

## 19–21. The lead state machine

**A centralized transition table already exists** — `ALLOWED_LEAD_TRANSITIONS`
in `app/core/constants.py:127`, enforced in one place by `change_status`
(`app/services/leads.py:397`), which raises `INVALID_TRANSITION` with the legal
moves attached. §19's "do not implement as scattered frontend conditions" is
already honoured. What changes is the *contents* of the table, not the design.

**Current vs required:**

| Current (6) | Required (9) |
|---|---|
| PENDING | NEW |
| CONTACTED | CONTACTED |
| — | NOT_CONTACTED |
| FOLLOW_UP | NURTURING |
| — | PRE_QUALIFIED |
| QUALIFIED | QUALIFIED |
| CONVERTED | CONVERTED |
| LOST | LOST |
| — | JUNK |

Current table:

```
PENDING    → CONTACTED, LOST
CONTACTED  → FOLLOW_UP, QUALIFIED, LOST
FOLLOW_UP  → CONTACTED, QUALIFIED, LOST
QUALIFIED  → FOLLOW_UP, CONVERTED, LOST
CONVERTED  → (terminal)
LOST       → CONTACTED          ← reopen, for a misclick
```

**Live data:** `CONVERTED` ×21, `PENDING` ×8, `CONTACTED` ×1, `QUALIFIED` ×1.
No `FOLLOW_UP` or `LOST` rows exist, which makes the migration smaller than it
looks — but `lead_activities` holds **82 `STATUS_CHANGED` rows** carrying
`from_status`/`to_status` strings that will no longer match the enum. History is
append-only (§24) and must not be rewritten, so the UI has to keep rendering
retired status names.

**Three discrepancies inside the specification itself**, which is why this is
blocked rather than merely large:

1. **§20's table omits `CONTACTED → LOST`.** It lists `CONTACTED: NURTURING`
   only. But §19 says "NURTURING → LOST" and §31 requires `NURTURING → LOST` to
   pass. Is a contacted lead genuinely unable to be lost until it has been
   nurtured?
2. **`PENDING` maps to two different things.** The 8 existing `PENDING` leads
   are "assigned, nothing done yet". That is `NEW` by the new vocabulary — but
   `NOT_CONTACTED` describes it equally well, and the two have different
   onward transitions.
3. **`LOST → CONTACTED` is being removed** (§21), and §23 removes Undo. Today
   those are the *only two* ways back from a mis-set terminal status. After
   this change, one wrong click sets `LOST` or `JUNK` permanently, with no
   recovery path for anyone including an admin. That may be intended; it should
   be a decision rather than a side effect. See [Q6](#q6).

| | |
|---|---|
| Files | `app/core/constants.py`, `app/services/leads.py`, `app/schemas/lead.py`, `app/services/dashboard.py`, `app/services/feedback_analysis.py`, `frontend/src/types/api.ts`, the leads page, the lead dialogs, `tests/test_leads.py` |
| DB | `leads.status` is `VARCHAR(20)` **with a CHECK constraint**: `ck_leads_status CHECK (status IN ('PENDING','CONTACTED','FOLLOW_UP','QUALIFIED','CONVERTED','LOST'))`. So this needs **both** a schema migration (SQLite table rebuild, per `0006`) *and* a data migration of the rows. `OPEN_LEAD_STATUSES` and `CLOSED_LEAD_STATUSES` both change. |
| Risk | **High.** Status strings appear in the dashboard, the feedback queue, the reference queue, the assistant's tool schemas and 82 history rows. The CHECK constraint means a half-applied migration fails loudly rather than corrupting data — which is the good case, but it does mean the rebuild has to be right first time. |

---

## 22–24. Explicit update, Undo, history

**§22 — explicit Update.** The current UI saves on change
(`frontend/src/app/(portal)/leads/page.tsx:277` navigates on select). Adding a
staged selection plus an Update button is frontend-only; the API
(`POST /leads/{id}/status`) already takes one explicit transition.

**§23 — remove Undo.** `undo_activity` exists in `app/services/leads.py:488`
and `app/api/leads.py:245`, and the frontend exposes it. Removing it is
mechanical. **Note the blast radius:** there is a *second*, separate Undo on the
customer timeline (`frontend/src/components/customers/CustomerTimeline.tsx:104`,
`/customers/{id}/timeline/{id}/undo`). §23 names "the lead history/status UI",
so the customer one stays. Removing both would be an over-reach.

**§24 — history.** Already append-only, timestamped and attributed:
`LeadActivity` rows carry `actor_user_id`, `from_status`, `to_status`,
`created_at`, and `undo` marks rather than deletes. Nothing to build; removing
Undo makes it strictly more append-only.

---

## 25. Is CONVERTED a WON account? — **answered**

**Yes, and the code says so explicitly.** From `change_status`
(`app/services/leads.py:412`):

> Reaching CONVERTED is the completion action itself — "won" and "delivered"
> are treated as the same moment, and this is what makes the lead eligible for
> a feedback ask. No separate dispatch step to click through.

Three independent confirmations:

1. `change_status` stamps `lead.dispatched_at = utcnow()` when the status
   becomes `CONVERTED`.
2. `references.askable_leads` treats `status == CONVERTED` as a won account
   that can be asked for a reference.
3. `feedback_analysis.pending_requests` keys the lead half of the queue off
   `dispatched_at IS NOT NULL` — i.e. off conversion.

`CLOSED_LEAD_STATUSES` is `(CONVERTED, LOST)`, and `CONVERTED` is terminal in
the transition table. **No discrepancy to report and no change needed** — but
see §26, because this is precisely what the 10-day rule collides with.

---

## 26. Converted → reference / feedback

Following from §25: conversion is *currently* what makes a lead eligible, via
`dispatched_at`. §26 forbids that and requires SAP invoice + 10 days instead.
A converted lead has no invoice date, so this is the same blocker as
[Q3](#q3).

---

## 27, 37–39. Consistency, notifications, audit, performance

- **§27.** Real risk of drift: the dashboard's CoverageStrip derives reference
  totals independently of `references.stats`. Both need the same eligibility
  filter or they will disagree by exactly the ineligible count. The fix is to
  have one function own the "eligible askable accounts" query and call it from
  both.
- **§37.** `create_lead` notifies the assignee inside the same transaction.
  `change_status` does not notify. Rejected transitions raise before any
  history or notification is written, so §37 is already satisfied — verify
  after the state-machine change.
- **§38.** `audit.record` is called on lead creation, status change, reference
  recording, assignment and user changes, always in the caller's transaction.
  Nothing to add beyond covering new actions.
- **§39.** Watch the "assigned by me" counts (§3) and the eligibility filter
  (§7–9): both must be SQL aggregates. `leads.assigned_by_user_id` has no index
  — worth adding if "Assigned by me" becomes a default view.

---

## Decisions taken

Answered by the product owner on 11 September 2026. These are now the rules;
the questions below are kept for the reasoning behind each one.

| | Decision |
|---|---|
| **Q1** Customer access | **The Customers page only.** `/customers` and its API become Super Admin. Reference Tracking and the feedback queue keep reading customer rows, so BDEs and managers keep working. |
| **Q2** Which invoice date | **`first_invoice_date`** — assumed, not asked. Ten days from when the account was first invoiced, so a repeat invoice cannot pull an account that has already been asked back out of the queue. Say so if you meant the latest invoice. |
| **Q3** Converted leads | **Not eligible until SAP invoices them.** A lead that converts goes to SAP; once SAP invoices it, the account appears as a customer and becomes eligible 10 days later. Conversion alone no longer opens reference or feedback work. |
| **Q4** "Not shared" | **Maps to the existing `DECLINED` roll-up** — assumed, not asked. No new state, no roll-up migration; both CHECK constraints already permit it. |
| **Q5** Who edits | **Assignee logs, manager sets status.** Activity logging (calls, notes, follow-ups) is the assignee's alone. Status changes stay available to management, which is what §22's Update button is for. |
| **Q6** Mistakes | **Admin-only reopen.** LOST and JUNK are terminal for normal users. An Admin or Super Admin can reopen, and the reopen is recorded in history like any other change. |

### What Q3 costs, stated plainly

This is the largest behavioural change in the set. Today 21 converted leads sit
in the reference book and the feedback queue. Under this rule they leave both
immediately, and the reference book becomes the 17 SAP customers until those
leads come back as invoiced accounts.

One structural gap follows from it: nothing currently links a converted lead to
the customer row SAP later creates for it. They are matched by nothing — the
customer arrives keyed on its SAP code. So a lead's reference history does not
follow it into its customer record. That is not a blocker for this change, but
it is the reason the two will look like separate accounts.

---

## Blocking questions

Per §42, these are documented rather than guessed. Each one changes what gets
built.

<a name="q1"></a>
### Q1. What exactly does "Customer data → Super Admin only" cover?
Restricting *all* customer records breaks Reference Tracking and the feedback
queue for every BDE and manager, because both are built on customer rows. Does
it mean the **Customers page/module** (browsing the SAP book with contacts and
invoice history), leaving the workflows that need a customer record intact?

<a name="q2"></a>
### Q2. Ten days from the first invoice, or the latest one?
`first_invoice_date` gates a customer once, when they become a customer.
`last_invoice_date` makes a repeat customer ineligible again after every new
invoice — which would pull accounts back out of the queue after they had
already been asked.

<a name="q3"></a>
### Q3. What happens to converted leads, which have no invoice date?
21 of them exist and they are most of the reference book. §26 forbids using the
conversion date, and they have no SAP invoice until they appear in an import.
Literal reading: they disappear from reference and feedback work indefinitely.

<a name="q4"></a>
### Q4. Is "Not shared" the same thing as the existing unused `DECLINED` state?
`ReferenceStatus.DECLINED` already exists, is already counted on the dashboard,
and no code path ever sets it. Reusing it means no new roll-up state and no data
migration. Confirm it means "asked, answered, no reference given" — not "we
stopped asking".

<a name="q5"></a>
### Q5. Can a manager change a lead's status, or only the assignee?
§17 says only the assigned employee may edit. §22 puts a status Update button in
the management All Leads view. Both cannot hold. Is a *status change* part of
"the operational lead log" (assignee only), or a management action that stays
with managers?

<a name="q6"></a>
### Q6. Should a mis-set LOST or JUNK be recoverable at all?
§21 removes `LOST → CONTACTED`; §23 removes Undo. Those are currently the only
two ways back. After this change one wrong click is permanent for everyone,
including a Super Admin. Intended, or should an admin-only reopen exist?

---

## What I would do first, once those are answered

The order in §41 is sound with one change: **§19–21 (the state machine) should
come last among the backend changes, not third.** It touches the dashboard, both
queues, the assistant's tools and 82 history rows, and every other change is
easier to verify against a pipeline that has not moved underneath it. Phone
validation, the "Assigned by me" view, the Update button and the "Not shared"
outcome are all independent of it and can land first.
