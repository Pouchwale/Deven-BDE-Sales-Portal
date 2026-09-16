# Open questions and assumptions

Everything here is something I had to decide without a documented answer, or
something the plan flags as unconfirmed. Each entry says what I did, so
nothing is silently assumed.

---

## Confirmed by the business

### C1. The roster is the 22 accounts as listed — confirmed

Re-supplied and matched exactly. Locked by
`tests/test_seed_config.py::test_each_person_has_the_confirmed_role_manager_and_team`,
which asserts the role, manager and team of all 22 people. Changing
`roster.py` now fails a test unless the change was intended.

### C2. Sales-team members hold the role `BDE`, not `SALES` — confirmed

The tree annotates every leaf as `(BDE)`, including the six under Shailesh who
sit in the **Sales team**. Implemented literally: role `BDE`, team `Sales`.

`BDE` and `SALES` share rank 3 and have identical permissions, so this is a
reporting distinction, not a permissions one. The `SALES` role exists in the
schema and is simply unused by the seed —
`test_sales_team_members_hold_the_bde_role` asserts nobody holds it.

### C3. No department heads — confirmed

No department-head accounts are created and `users.heads_department_id` is
null for everyone. `test_there_are_no_department_heads` keeps it that way.

The attribute stays in the schema, unused, so appointing a head later is a
data change rather than a migration. **One consequence worth knowing before
Phase 5:** per plan §8.7 a plain `MANAGER` has no feedback scope, so with no
department heads, **only `ADMIN` and `SUPER_ADMIN` will be able to read
customer feedback**. If field or team leads should see their own department's
feedback, that needs either a head appointed or a different rule — say which
and it is a small change in `authority.feedback_department_scope`.

Say the word if you would rather the attribute were removed from the schema
altogether; it is one migration and one column.

### C4. The Google review link — supplied

Stored in `app_settings` under `company.google_review_url`, **byte-identical**
to what was supplied (607 characters,
`test_google_review_url_is_configured_verbatim` asserts the length and the
trailing place CID). The portal only ever links out to it — it never
generates, pre-fills, submits or posts a review.

One caution: the supplied URL is a full Google *search* result link and
carries session-scoped parameters (`ei`, `ved`, `sca_esv`, `biw`/`bih`) that
will eventually stop resolving. The durable part is the place CID in the
`#lrd` fragment, `0xcb408a341fb4b288`, whose stable equivalent is:

```
https://www.google.com/maps?cid=14645857944683721352
```

I have **not** substituted it — the configured value is the one you actually
checked in a browser. Swapping is one line in `backend/app/seeds/roster.py`,
or an edit in the admin panel once `/admin/settings` exists.

### C5. No database server for now — confirmed

Development runs on **SQLite**, a local file, with no server to install or
connect to. Nothing in the codebase requires PostgreSQL to be reachable.

The SQL-first approach is kept intact for production exactly as intended: the
same hand-written migrations in `backend/app/db/sql/` render for PostgreSQL
whenever you want to move, and `docs/schema.postgres.sql` is the rendered
production DDL, regenerated on demand. Switching is one environment variable
plus `pip install psycopg2-binary` — see `docs/DATABASE.md`. No code changes.

---

## Decided in code — please confirm

### A1. Email addresses are generated

Not in any source document. Generated as `firstname.lastname@pouchwale.com`
(`owner@pouchwale.com` for the Super Admin), the domain taken from the address
this work was requested from.

**Fix:** edit `backend/app/seeds/roster.py` — either change `EMAIL_DOMAIN` or
give a `SeedUser` an explicit `email=`. The seed matches on email, so correct
them and re-run; nothing else changes.

### A3. Bhakti Shah owns one SAP account

Plan §13's ownership list omits her, but the real export credits her with
customer `C2080` (GULABS). Ownership is derived from the file rather than from
the plan's table, so she owns it. All nine other counts match the plan exactly.

### A4. Two cells in the SAP screenshot differ from the CSV on disk

I used `data/sap/sap_invoices_real.csv`, copied byte-for-byte from the export.
Reading the screenshot you pasted, two cells look different — most likely my
misreading of the image rather than a real discrepancy, but worth a glance:

| Row | Field | CSV on disk | My reading of the image |
|---|---|---|---|
| 8 (C2132) | Customer Name | `KALIRA BIOTECH` | `KAHIRA BIOTECH` |
| 23 (C1962) | Mobile | `8126110131` | `81264 10434` |

If the CSV is right, ignore this. If not, correct the CSV and re-run the
seed — the import is idempotent and keyed on the row contents.

### A5. "Converted customer" means "SAP has invoiced them" (plan Q10)

Every row in the export is an invoice line, so all 17 customers are treated as
converted. When the live SAP B1 connection is built, this is the field to
confirm.

### A6. Departments are the plan's six

`Sales · Production · Quality · Dispatch · Accounts · Customer Support`.
Provisional — §8.3 says the real form defines the real list, and the importer
reports an unmatched department rather than creating one.

---

## Still genuinely blocking

### Q5 — the Google Forms export *(blocks Phase 5)*

**This is the one I cannot work around.** The column headers, the rating scale
(1–5? 1–10? worded?) and the exact department wording all come from the file.
The importer is built to discover and confirm them, but it cannot be tested or
configured against a form I have not seen.

Send the `.xlsx`, or just its header row and two sample rows.

**It now blocks a second thing.** The form's *public URL* — the link a
customer clicks — is what a feedback request message sends. Until it is set
in **Settings ▸ Company ▸ Feedback form link** (`company.feedback_form_url`),
the Send request dialog composes the feedback message but disables sending
and explains why. Google *review* requests are unaffected and work today.

That URL is one paste in Settings, needs no code change, and is independent of
the export itself — so it can be supplied long before the `.xlsx` is.

---

## Answered by the plan, implemented as stated

| # | Question | Implemented |
|---|---|---|
| Q1 | Reference follow-ups: separate queue or lead rows? | Separate queue tab. `leads.origin` exists and is specified, so a change of mind is a data operation. |
| Q2 | May an Admin create another Admin? | No. Only `SUPER_ADMIN` creates Admins — enforced by Rules 1 and 2, asserted by two tests. |
| Q3 | Department heads | Attribute retained in the schema, but **no heads appointed** — see C3. |
| Q3b | Does a team head get their department's feedback automatically? | No. Moot for now — see C3. |
| Q4 | May a Manager create users? | No. Admin-only; managers read and assign. |
| Q6 | "SAP Accounts Owned" = converted customers assigned | Yes, and derived from the file rather than hardcoded. |
| Q7 | Are the seven genuinely unassigned? | Yes — no manager, no team, visible to Admin/Super Admin only. |
| Q8 | Do peer managers see each other? | No — subtree only. Ramanesh cannot see Navya. |

---

## One deviation from the plan, and why

**Alembic was not used.** Plan §14 names an "Alembic baseline with
`render_as_batch=True`". You asked for the SQL to be maintained so production
changes are easier, and Alembic's autogenerate makes the Python models the
source of truth — the SQL becomes a generated artefact rather than the thing
under review.

Instead: hand-written SQL migrations in `backend/app/db/sql/`, a small runner
with checksum-enforced immutability, and a parity test that fails the build if
the models and the SQL drift apart. Every §5.6 concern the plan raises about
SQLite/PostgreSQL parity is handled — see `docs/DATABASE.md`.

The trade-off: no autogenerate and no automatic downgrades. Say the word if
you would rather have Alembic and I will convert; the schema itself would not
change.
