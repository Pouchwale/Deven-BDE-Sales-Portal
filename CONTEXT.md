# Session context — BDE & Sales Portal

Handover notes as of **2026-09-25**. Read this first, then `README.md`.
No secrets are in this file; they live in `backend/.env` locally and in the
Render dashboard.

---

## The project

- **Repo root:** `D:\GP3\Portal - V2` (git remote `github.com/Pouchwale/Deven`, branch `main`).
- **Backend:** FastAPI + SQLAlchemy, Python venv at `backend/.venv`. Postgres in
  production, SQLite for tests. Hand-written migrations in `backend/app/db/sql/`.
- **Frontend:** Next.js (a newer version with breaking changes — read
  `frontend/node_modules/next/dist/docs/` before writing Next code), TypeScript,
  Tailwind. Built as a **static export** into `backend/app/web` and served by
  the backend (see "Single service" below). `next dev` still works as before.
- **Live site:** **`https://deven-bde-sales-portal-backend.onrender.com`** — one
  Render web service (the backend) serving pages and `/api`, on the **free
  plan**, plus a Render Postgres. The old frontend service
  (`deven-bde-sales-portal-frontend.onrender.com`) only redirects there.
  The data was copied from this PC with `deploy/backup/copy-to-render.ps1`.
- **After ANY frontend change:** `cd frontend && npm run export:backend`, and
  commit `backend/app/web` with it. `backend/tests/test_web.py` fails otherwise.
- **Render account:** the portal is **not** in the Render workspace connected to
  Claude (that one, "My Workspace", only holds the unrelated "Mihir" services).
  Claude cannot read the portal's Render logs, env vars or billing — the user
  has to.

## How passwords work (important background)

- Sign-in checks only a **bcrypt hash** — one-way, cannot be read back.
- For the Super Admin's "show password" feature there is a second,
  **Fernet-encrypted copy** in `users.password_encrypted`
  (`backend/app/core/password_vault.py`). The key is `PASSWORD_VIEW_KEY`, or one
  derived from `SECRET_KEY` when that is blank.
- The copy is written whenever a password is set, and refreshed on every
  successful sign-in (`password_vault.refresh_copy` in `backend/app/api/auth.py`).
- Reveal endpoint: Super Admin only, one user at a time, every look audited
  (`services/users.py: reveal_password`).
- **"Not available"** in User admin = no copy, or a copy encrypted under a
  different key. Likely on Render because the DB came from this PC: if Render's
  `PASSWORD_VIEW_KEY` / `SECRET_KEY` differs from local, the copied copies can't
  be decrypted. Fix: set Render's `PASSWORD_VIEW_KEY` to the local value (or make
  `SECRET_KEY` match if local `PASSWORD_VIEW_KEY` is blank). Each user's copy also
  repairs itself at their next sign-in.

## Super Admin sign-in from env (`backend/app/services/super_admin_env.py`)

- `SUPER_ADMIN_USERNAME` / `SUPER_ADMIN_PASSWORD` are applied **only when new**
  (empty DB, or the values were edited). What was applied is remembered as a
  hash in `app_settings` key `internal.super_admin_env_applied`.
- Deliberate design (commit `a82ba2f`, test
  `test_old_env_credentials_at_sign_in_do_not_undo_a_portal_change`): typing the
  old env password does **not** override a password changed in the portal. Do
  not "fix" sign-in by forcing env credentials at login — it breaks that rule.
- To recover a locked-out Super Admin: set `SUPER_ADMIN_PASSWORD` in Render to a
  **new** value → applied at restart (and at sign-in), unlocks and reactivates.
- Lockout: `LOGIN_LOCKOUT_THRESHOLD` failures → locked `LOGIN_LOCKOUT_MINUTES`
  (15). A locked account gets the same "Incorrect username or password" even
  with the right password.

---

## Done (committed and pushed to `main`)

1. **"Set password" instead of "Not available"** — `frontend/src/components/admin/PasswordReveal.tsx`
   takes an optional `onSetPassword`; when no readable copy exists it shows a
   **Set password** button that opens the existing reset dialog. Wired in
   `frontend/src/app/(portal)/admin/users/page.tsx` (row key includes
   `password_changed_at` so the row refreshes after a reset) and in
   `UserDetailPanel` in `frontend/src/components/admin/UserActionDialogs.tsx`.
   Only offered when `can_act_on`. `tsc` and `eslint` pass; not yet viewed in a
   browser.
2. **Single service (the real fix for the repeated sign-in failures)** — the
   separate free frontend service kept sleeping and Render refused to wake it
   (`429`, `x-render-routing: hibernate-rate-limited`): a page load sends dozens
   of requests to it, Render throttles the wake-ups, sign-in fails ("Sign-in
   failed" / "Incorrect…" on the old page) while the backend is fine. Two free
   services also cannot both stay awake (750 h/month/workspace).
   - `frontend/next.config.ts`: `PORTAL_STATIC_EXPORT=1` → `output: "export"`
     into `out-static/`; `frontend/scripts/export-to-backend.mjs`
     (`npm run export:backend`) copies it to `backend/app/web` + `SOURCE_HASH`.
   - `backend/app/web.py`: catch-all GET/HEAD route (registered last in
     `main.py`) serving those files: `path`, `path.html`, `path/index.html`,
     plus the route-segment data files (`x/__next.<group>.x.__PAGE__.txt` →
     `x/__next.<group>/x/__PAGE__.txt`). Page CSP + cache headers; unknown
     `/api/*` stays a JSON 404; path traversal refused. HEAD is required (the
     app probes pages with HEAD; FastAPI GET routes 405 it otherwise).
   - Customer page moved to `/customers/detail?id=<uuid>`; old
     `/customers/<uuid>` 308-redirects. Chat cards and notifications updated.
   - Old frontend service: `redirects()` in `next.config.ts` sends everything
     to the backend address when `RENDER` is set (or `PORTAL_MOVED_TO`).
   - Verified: `backend/tests/test_web.py`, and
     `frontend/tests/e2e/single-origin.mjs` (real Chrome, 4 roles × every
     page, 41 checks, all pass against a disposable e2e backend). The old
     `tests/e2e/run.mjs` walkthrough is stale (pre-dashboard-redesign); its
     `#email` selector was updated to `#identifier`, the rest was not.
3. **Free keepalive (no Render cron — Render cron jobs are paid)** —
   `backend/app/services/keepalive.py`, started/cancelled in the lifespan.
   `ENABLE_KEEPALIVE=auto` = on when Render's `RENDER` var is set, off on PR
   previews and locally. Pings the service's own `RENDER_EXTERNAL_URL/health`
   every 600 s, **06:30–00:30 IST every day** (~558 of 750 free hours).
   `KEEPALIVE_FRONTEND_URL` is blank on purpose (waking the old frontend would
   eat hours). `.github/workflows/wake-portal.yml` (free GitHub Action) wakes
   the backend at 06:20 IST daily. The user wants zero spend anywhere.
4. **Login page** — `frontend/src/app/login/page.tsx`: Render's 429
   `hibernate-rate-limited` is retried during the wake loop and reported as
   "server is waking up", not as a wrong password; 502/503/504, network errors
   and 5xx get their own messages. 401/422 stay the one generic message.
5. **`backend/app/seeds/reset_users.py`** — terminal tool to reset named
   accounts' passwords (prompt / `--from-file` / `--from-env`, never argv;
   never prints passwords; verifies the new hash; audited).

User preference: commit and push to `main` after every change.

---

## History of the sign-in failures (resolved by the single service)

- 2026-09-24/25: "Incorrect username or password" / "Sign-in failed" for
  `superadmin` / `navya`. Cause each time: the live frontend returned `429
  hibernate-rate-limited` (Render refusing to wake it); the request never
  reached the app. The backend woke normally. Free hours were not exhausted.
- Fixed by serving everything from the backend (above). If sign-in fails
  again, first `curl -sD - https://deven-bde-sales-portal-backend.onrender.com/health`
  and look for `x-render-routing`.

## Deploys are not reaching Render (found 2026-09-25)

- Pushes to `main` are on GitHub (`git ls-remote` / GitHub API confirm), but
  **neither Render service has deployed any of today's commits**: 20+ min after
  pushing `46bd1c0`, the backend `/login` is still the old JSON 404 and the
  frontend `/login` is 200 with no redirect. So the earlier login-page change
  (`25c14d4`) never went live either; the user kept seeing the OLD page.
- Most likely cause (unconfirmed, needs the dashboard): the workspace's free
  **build pipeline minutes** are used up. Render docs: without a payment method
  Render "disables all new builds for your workspace for the remainder of the
  month … services remain active using their existing build artifacts."
  Minutes reset on the 1st of the month.
- Other possibilities: auto-deploy switched off, or builds failing — the
  service's **Events** page in the Render dashboard shows which.
- Every push builds BOTH services. To save minutes: suspend the old frontend
  service, or turn its auto-deploy off (it only redirects now).

## Security (public repo)

- `github.com/Pouchwale/Deven-BDE-Sales-Portal` is **public**. The default
  seed password `ChangeMe@123` has been in it since 2026-09-16 (README,
  migration 0013, tests) — and the live Super Admin still used it. Anyone could
  sign in as Super Admin and read every password. Tell the user to change the
  Super Admin password (and weak ones like `<name>@123`) and to make the repo
  private (free; check Render still has access through its GitHub app).
- No customer data files are tracked (`data/` is not in git).

## Open items for the user

1. **Render free Postgres expires 30 days after creation** (then 14 days'
   grace, then deleted with all data). Set up ~2026-09-17 → expires
   ~2026-10-17. Plan: move to Neon free (no expiry, 0.5 GB, 100 CU-h/month;
   DB is ~10 MB) — needs the user to create the Neon account.
2. Use the new address. Optionally suspend the old frontend service in Render
   (free) — it only redirects.
3. If `ENABLE_KEEPALIVE` was added on Render earlier, remove it (or `auto`).
4. **The Render backend reports `"env": "development"`** — `ENV=production`
   is not set there. Review `production_warnings()` before switching.
5. `PASSWORD_VIEW_KEY` on Render should match local (see passwords above).
6. Lockout recovery: wait 15 min, or set a new `SUPER_ADMIN_PASSWORD` in Render.

## Useful commands

```sh
# Live health (no sign-in attempt, so no lockout risk)
curl -sD - -o /dev/null https://deven-bde-sales-portal-backend.onrender.com/health

# Frontend checks
cd frontend && npx tsc --noEmit -p . && npx eslint src

# Backend tests
cd backend && .venv/Scripts/python.exe -m pytest

# Rebuild the pages the backend serves (after ANY frontend change)
cd frontend && npm run export:backend

# Browser check, single origin (disposable e2e backend only - see file header)
E2E_APP_URL=http://127.0.0.1:8000 E2E_SEED_PASSWORD=... node tests/e2e/single-origin.mjs

# Keepalive tests only
cd backend && .venv/Scripts/python.exe -m pytest tests/test_keepalive.py -q -p no:cacheprovider
```
