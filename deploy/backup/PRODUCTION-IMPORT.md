# Production import (paid Render PostgreSQL) — procedure, NOT yet run

Written 2026-10-30. Nothing here has been executed against Render. Every step
that writes to production needs the owner's explicit "go" at that step.

## What we know going in

| | |
|---|---|
| Newest production backup on this PC | `D:\GP3\backups\production\production-20261017-111028.dump` (identical in content to the 2026-10-16 dump; last change 2026-10-15 08:43 UTC) |
| Its contents | 22 users (21 active), 94 leads, 12 reference asks (1 taken, 6 "later", 5 declined → 6 completed), 0 feedback, 4 notifications, migration 0019 |
| Old free database | `dpg-dalsb0ad0e5s738fihug-a` — expired; every nightly backup since 2026-10-18 failed against it |
| New paid database | **never backed up and never inspected from this PC** — its contents are unknown |
| Local (development) | 2026-10-17 references merged in (12 asks, 6 completed), plus 26 SAP leads imported locally on 2026-10-29/30 (120 leads) |

So: do not assume the paid database is empty, older, or newer. Look first.

## Step 0 — point the backups at the paid database (owner, once)

Render dashboard → the **paid** PostgreSQL → Connect → *External Database URL*, then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\set-production-credential.ps1
```

This also repairs the nightly *BDE Portal - production backup* task, which has
failed every night since 2026-10-18 because it still points at the expired
free database.

## Step 1 — back up the paid database (read only)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\backup-production.ps1
```

Gives `production-<time>.dump` + `.sha256`, verified with `pg_restore --list`.
Copy it off this PC as well. **No step below runs without this file.**

## Step 2 — confirm the target is the one the live site uses

- Live `/health` → `environment: production`, `database: postgresql`, and
  `commit` = the commit you expect.
- Render dashboard → backend service → Environment → `DATABASE_URL` host must
  equal the host stored in Step 0. If they differ, STOP.

## Step 3 — compare, read only

Count both sides (users / active / leads / reference asks by outcome / lead
reference_status / feedback / notifications / audit / newest audit and sign-in
time / `max(schema_migrations.version)`), and list rows present on one side
only. The local inspection scripts used on 2026-10-30 did exactly this from the
dump files without restoring anything (`pg_restore --data-only -f -`, parse the
COPY blocks).

Decide from the result — do not pick in advance:

| Paid DB looks like | Action |
|---|---|
| Has everything in the 2026-10-17 dump plus newer work | Nothing to import. Stop. |
| Missing only a few rows the dump has (e.g. the 2026-10-15 references) | **Merge** those rows (Step 4a). Keeps anything new on the paid DB. |
| Empty / only a fresh Super Admin | **Full restore** of the chosen dump (Step 4b). |
| Has rows the dump lacks AND lacks rows the dump has | Merge (4a). Never a full restore — it would destroy the paid DB's own new rows. |

Also compare `schema_migrations`: both sides must be at the same version as
this checkout (0019 today). If the paid DB is behind, run migrations first
(Render applies them on deploy); never restore an older schema over a newer one.

**Owner approval required here**, with the comparison in front of them.

## Step 4a — merge (preferred)

One transaction, insert-only for missing rows, plus the specific columns that
changed (e.g. a lead's `reference_status` / `next_reference_date`), plus the
`audit_events` rows about those same records. Never copy `user_sessions`, and
never copy `users.hashed_password` / `password_encrypted` (passwords on the
paid DB stay exactly as they are). Dry run (rolled back) first, show the list,
then apply. Same approach as the local merge of 2026-10-30.

## Step 4b — full restore (only if Step 3 says so)

`pg_restore --clean --if-exists --no-owner --no-acl --single-transaction`
into the paid database from the chosen dump. All or nothing; the Step 1 dump is
the undo.

Then, **before the next restart**:

```sh
python -m app.services.super_admin_env adopt     # run with production's env
```

The restored `app_settings` carries the OLD database's record of which
`SUPER_ADMIN_*` values were applied. If Render's values differ, the next start
treats them as new and resets the Super Admin's password to the env value.
`adopt` records the current values as already applied and changes no account.

## Step 5 — verify

- Counts match what Step 3 predicted; no duplicate reference asks
  (`lead_id, asked_on, outcome` groups with count > 1 = 0).
- Reference Tracking: "References completed" = taken + declined accounts.
- Sign-in: the owner signs in as Super Admin, opens User admin, uses the eye on
  one account, signs out and back in. Optionally restart the Render service and
  sign in again (passwords must survive a restart — they do locally).
- Password-vault key: if the eye shows "Not available" for everyone, Render's
  `PASSWORD_VIEW_KEY` (or `SECRET_KEY`) differs from the key the copies were
  made under. Each copy repairs itself at that person's next sign-in, or set a
  password once in User admin.

## Never

- Never run `copy-from-render.ps1` "the other way" — there is no copy-to-render
  script, on purpose.
- Never restore a dump over production without Step 1's backup existing.
- Never reset passwords in bulk as part of an import.
