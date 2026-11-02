# Back-Up & Restore (Super Admin)

The portal's own backup and recovery module: **Super Admin → Back-Up &
Restore** (`/admin/backups`). It complements — it does not replace — the
Windows tools in `deploy/backup/`, which keep backing up the LIVE database
from the owner's PC every night.

| | Where |
|---|---|
| Page | `frontend/src/app/(portal)/admin/backups/page.tsx`, dialogs in `frontend/src/components/admin/BackupDialogs.tsx` |
| API | `backend/app/api/backups.py` — every route requires the Super Admin |
| Engine | `backend/app/services/backups/` (`storage`, `pgtools`, `catalog`, `project`, `engine`, `restore`, `jobs`, `scheduler`) |
| Tests | `backend/tests/test_backups.py` |

---

## 1. What it does

**Create backup** (runs in the background; progress survives a page refresh):

- **Only database** → `GP3_Database_YYYYMMDD_HHMMSS.dump` — `pg_dump -Fc
  --no-owner --no-acl`: every table and row, primary/foreign/unique/check
  constraints, indexes, sequences *with their current values*, views,
  functions, triggers and extensions. Ownership and grants are left out on
  purpose: Render's database user is not a superuser, and roles are
  cluster-level objects that a restore elsewhere must not try to recreate.
  A `…dump.manifest.json` travels beside it.
- **With code — full project** → `GP3_Full_Backup_YYYYMMDD_HHMMSS.zip`:
  `backend/ frontend/ deploy/ docs/ .github/ database/ README.md CONTEXT.md
  .env.example … BACKUP_MANIFEST.json`, with the real folder structure. The
  file list is the git-tracked project (an allow-list of folders plus a
  deny-list checked on every file): no `.env`, keys, `.dpapi`, dumps, ZIPs,
  logs, spreadsheets, `node_modules`, `.venv` or caches.

**Verification** — a backup shows **Verified** only when all pass: file
exists and is non-empty · SHA-256 matches the catalog · (ZIP) archive
intact and every file matches the manifest's checksum · (dump) `pg_restore
--list` reads it and it holds table data · optionally a test restore into a
separate database (`BACKUP_TEST_DATABASE_URL`).

**Restore** — from the history, an uploaded `.dump`, or an uploaded full
ZIP (its database dump is restored; its *code* is only unpacked for
inspection — application code is never replaced from a web request):

1. validate the file (type, size limit, `PGDMP` signature, checksum,
   `pg_restore --list`, ZIP: no `..`/absolute paths/links/zip bombs)
2. show what it is and **the exact target database**
3. the Super Admin types the target database's name **and** their password
4. a dump from a *different* database needs an extra explicit tick
5. a fresh **safety backup** of the target is taken and **verified** — no
   verified safety backup, no restore
6. `pg_restore --clean --if-exists --single-transaction`: any error rolls the
   whole restore back and the database is unchanged; `lock_timeout` stops it
   hanging behind a busy connection
7. the result is checked (tables, users, schema version); if it fails, the
   safety backup is put back automatically
8. newer migrations are applied, the module's own settings are re-applied,
   and the Super Admin env record is adopted (no password changes)

What one transaction cannot promise: a restore that *succeeds* replaces
everything written after the backup was taken. That is what the safety
backup is for — it appears in the history as "Safety (before restore)".

**Why the history lives on the backup disk, not in the database:** a restore
replaces the database. A catalog stored there would forget the backup just
restored and rewind to whatever list the dump contained. `backup-logs/`
(catalog, jobs, activity) survives any restore. Settings live in
`app_settings` and are re-applied after a restore. Every action is ALSO
written to the normal audit trail (`BACKUP_*` actions) — never a password,
connection string or key.

---

## 2. Storage layout

```
<backup folder>/
├── full-project/       GP3_Full_Backup_*.zip
├── database-only/      GP3_Database_*.dump  (+ .manifest.json)
├── restore-staging/    uploads, safety backups, temporary unpacking
└── backup-logs/
    ├── catalog/<id>.json   one record per backup (kept after deletion)
    ├── jobs/<id>.json      progress of each background job
    ├── activity.jsonl      every action, one line each
    └── scheduler.json      last automatic run
```

The folder is validated before it is accepted: absolute path, created if
missing, a test file written and removed. Refused: relative paths, the
system temp folder (wiped without warning) and anything inside the project
(it is pushed to a public GitHub repository). There is **no fallback** —
with no valid folder, backups are refused rather than written somewhere
that disappears.

---

## 3. Configuring the folder

### This PC (local)

Super Admin → Back-Up & Restore → **Storage** → `D:\GP3\backups` → *Test
folder* → *Save*. (Already done on 2026-11-02.) The module's subfolders sit
beside the existing `production/` and `local-dev/` folders and never touch
them.

### Render (production) — persistent disk required

A Render web service's normal filesystem is **wiped on every deploy and
restart**. Backups there would vanish. Before using the module on Render:

1. Render → backend service → **Disks** → *Add disk* (paid plans only):
   mount path `/var/data`, size e.g. 10 GB.
2. Environment → add `BACKUP_STORAGE_ROOT=/var/data/backups`.
3. Make sure the PostgreSQL **client tools** (`pg_dump`, `pg_restore`,
   `psql`) of version **18 or newer** are on the server — Render's native
   Python runtime does not guarantee them. Either deploy the backend as a
   Docker service whose image installs `postgresql-client-18`, or set
   `PG_BIN_DIR` to where they are. The page's banner says plainly when they
   are missing; no backup is attempted.
4. Deploy, open the page, check *Storage* shows the disk as writable.

A disk attached to one service is not a backup of that service's host. For
real disaster recovery also keep copies off Render — the Windows nightly
task on the owner's PC already does this for the database, and full-project
ZIPs can be downloaded from the history.

Optional: `BACKUP_TEST_DATABASE_URL` — a separate, disposable database used
only by *Verify → test restore*. Refused if it points at the portal's own
database.

---

## 4. Automatic backups and retention

Settings tab: **Daily database backup** at a set IST time (off by default),
run by the app itself (no browser, works on Render). Retention keeps the
newest backup per day / week / month for the configured counts and prunes
**only scheduled** backups — it never removes the newest verified backup of
any kind. A storage limit and alert percentage warn before the disk fills;
at 100 % new backups are refused.

On this PC the Windows task *BDE Portal - production backup* keeps backing up
the LIVE database nightly to `D:\GP3\backups\production`. The in-app schedule
backs up the database the app itself runs on, so the two never compete.

---

## 5. Manifest format (`BACKUP_MANIFEST.json`, version 1)

```json
{
  "manifest_version": 1,
  "backup_id": "uuid",
  "backup_type": "FULL",
  "created_at": "2026-11-02T04:38:28+00:00",
  "application_commit": "eebf05cf4e74",
  "postgres_version": "18.6",
  "database": "host:port/database",
  "database_dump": "database/GP3_Database_YYYYMMDD_HHMMSS.dump",
  "project_structure": [".github", "backend", "deploy", "docs", "frontend", "..."],
  "files": [{"path": "backend/app/main.py", "size_bytes": 4511, "sha256": "…"}],
  "file_count": 496,
  "excluded": "secrets (.env, keys), node_modules, .venv, caches, logs, data, backups",
  "verification": "Verified after writing; see the portal's backup history."
}
```

The database-only sidecar (`*.dump.manifest.json`) carries `backup_id`,
`created_at`, `database`, `postgres_version`, `dump_format`, `size_bytes`,
`sha256` and `verification`. No credentials appear in either.

---

## 6. Rebuilding a project from a full ZIP (manual, controlled)

1. Unzip anywhere outside the old checkout.
2. `backend/.env` is NOT in the ZIP — recreate it from `.env.example`
   (secrets come from your password manager / Render dashboard).
3. `cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
4. Restore the database from `database/GP3_Database_*.dump` (through the
   page, or `pg_restore --clean --if-exists --no-owner --no-acl
   --single-transaction -d <db> <file>`), then `python -m app.db.migrate upgrade`.
5. `cd frontend && npm ci && npm run export:backend`.

Replacing the code running on Render is a deployment (push to GitHub), never
something this page does.

---

## 7. Verified on 2026-11-02 (local)

- Database-only backup: 217,757 bytes, 26 tables with data, 48 indexes,
  86 constraints — **Verified**.
- Full project backup: 2.2 MB, 496 files matching the manifest, no `.env` —
  **Verified**.
- Restore into a **throwaway PostgreSQL 18 server** (port 55432,
  `D:\GP3\pg-restore-test`), never the real database: seeded with the
  30 Oct data (22 users, 120 leads, 10 references, schema 0019); restore of
  the full ZIP refused without the "different database" confirmation
  (unchanged), then completed with it — safety backup verified first —
  ending at 21 users, 125 leads, 12 references, schema 0020.
- Forced failure of the post-restore check → the safety backup was put
  back automatically and the test database ended exactly as before.
- Automated tests: `tests/test_backups.py` (30 tests) plus the full suite.

## 8. Limitations / manual steps

- **Render:** needs a persistent disk and PostgreSQL 18 client tools (see 3).
  Until then the page reports the missing pieces and refuses to back up.
- A restore signs out anybody whose session is not in the restored data,
  possibly including the Super Admin who ran it — the page says so.
- Large databases: the job runs inside the web process; a deploy or restart
  during a backup marks it *Interrupted* (a restore is all-or-nothing, so an
  interrupted restore leaves the database unchanged or fully restored).
- Test restores need a separate database (`BACKUP_TEST_DATABASE_URL`); the
  local `bde_portal` role cannot create databases.
