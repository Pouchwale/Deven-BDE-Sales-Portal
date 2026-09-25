# Session context — BDE & Sales Portal

Handover notes as of **2026-09-24**. Read this first, then `README.md`.
No secrets are in this file; they live in `backend/.env` locally and in the
Render dashboard.

---

## The project

- **Repo root:** `D:\GP3\Portal - V2` (git remote `github.com/Pouchwale/Deven`, branch `main`).
- **Backend:** FastAPI + SQLAlchemy, Python venv at `backend/.venv`. Postgres in
  production, SQLite for tests. Hand-written migrations in `backend/app/db/sql/`.
- **Frontend:** Next.js (a newer version with breaking changes — read
  `frontend/node_modules/next/dist/docs/` before writing Next code), TypeScript,
  Tailwind. The frontend proxies `/api/*` and `/health` to the backend via
  `BACKEND_INTERNAL_URL` (`frontend/next.config.ts`).
- **Live site:** `https://deven-bde-sales-portal-frontend.onrender.com` on Render's
  **free plan**, two web services (frontend + backend) and a Render Postgres.
  The data was copied there from this PC with `deploy/backup/copy-to-render.ps1`.
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
2. **Keep-alive cron job (the one to use)** — `render.yaml` (Blueprint, repo
   root) declares cron `portal-keepalive`, schedule `*/14 3-14 * * 1-6` (UTC =
   08:30–20:30 IST, Mon–Sat), running `deploy/render/keepalive.py` (stdlib only,
   3 attempts 30 s apart, non-zero exit on failure) against
   `PING_URLS=https://deven-bde-sales-portal-frontend.onrender.com/health`.
   The frontend proxies `/health` to the backend, so one ping keeps both awake.
   - Working hours only: both services ≈ 624 of Render's 750 free
     instance-hours a month. Round the clock would be ≈ 1,440 and Render would
     suspend them for the rest of the month.
   - Render cron jobs are paid (starter, ~$1/month minimum).
   - To enable: Render → New → Blueprint → repo `Pouchwale/Deven-BDE-Sales-Portal`,
     branch `main` → Apply. Verified by running the script against the live
     site (200).
   - The user wants the cron job kept in the project.
3. **Backend self-keepalive (built, keep OFF on the free plan)** —
   `backend/app/services/keepalive.py`, started and cancelled in the lifespan in
   `backend/app/main.py`. One asyncio task that GETs `SELF_PUBLIC_URL` every
   `KEEPALIVE_INTERVAL_SECONDS` (default 600, 10 s timeout, one log line per
   ping, never raises). Runs only when `ENABLE_KEEPALIVE=true`; a blank,
   localhost or non-https URL logs one warning and is skipped. Tests:
   `backend/tests/test_keepalive.py`.
   - It runs 24/7, so together with the cron job it exceeds the free hours.
     Use it instead of the cron job only if the frontend moves to a paid plan
     (or the cron job is dropped).
4. **Login page** — `frontend/src/app/login/page.tsx`: Render's 429
   `hibernate-rate-limited` is retried during the wake loop and reported as
   "server is waking up", not as a wrong password; 502/503/504, network errors
   and 5xx get their own messages. 401/422 stay the one generic message.
5. **`backend/app/seeds/reset_users.py`** — terminal tool to reset named
   accounts' passwords (prompt / `--from-file` / `--from-env`, never argv;
   never prints passwords; verifies the new hash; audited).

User preference: commit and push to `main` after every change.

---

## Open issue: Admin can't sign in on the live site

- User reports "Incorrect username or password" for Admin (`@superadmin`,
  typing `ChangeMe@123`) on Render.
- Finding: **every** request to the live frontend, including `/health`,
  returned `HTTP 429` with header `x-render-routing: hibernate-rate-limited` for
  10+ minutes on 2026-09-24 (~15:20–15:45 IST). That is Render refusing to wake
  the sleeping free service — the request never reaches the app.
- Update 2026-09-25: the backend
  (`https://deven-bde-sales-portal-backend.onrender.com`) woke after ~52 s and
  the frontend a few minutes later; frontend `/health`, `/api/health/db`
  (postgresql) and `/login` all returned 200. So the free hours were **not**
  exhausted — the 429 was Render temporarily refusing to wake a sleeping
  service. The keep-alive cron job (above) prevents it during working hours.
- **The Render backend reports `"env": "development"`** in `/health` (only
  shown outside production). `ENV=production` is not set there, so the
  production safeguards in `backend/app/core/config.py` are off. The user should
  set it in the Render backend env (check `production_warnings()` first).
- Next steps for the user:
  1. Apply the `render.yaml` Blueprint (cron job). Leave `ENABLE_KEEPALIVE`
     off on the backend while on the free plan.
  2. Render backend env: `ENV=production` (after reviewing what it enforces).
  3. If Admin still fails once the site is up: probably a lockout — wait 15
     min, or set a new `SUPER_ADMIN_PASSWORD` in Render.
  4. Set `PASSWORD_VIEW_KEY` on Render to match local (see above).
- Checked and ruled out on this PC: no SAP uploader scheduled task, no
  `%APPDATA%\bde-portal` config — so the Excel/Python SAP uploader
  (`deploy/sync/`), which signs in as the Super Admin, is not locking the account
  from here. It could still be configured on another PC (the SAP machine); if the
  lockout recurs, check there for a wrong saved password.

## Useful commands

```sh
# Live health (no sign-in attempt, so no lockout risk)
curl -sD - -o /dev/null https://deven-bde-sales-portal-frontend.onrender.com/health

# Frontend checks
cd frontend && npx tsc --noEmit -p . && npx eslint src

# Backend tests
cd backend && .venv/Scripts/python.exe -m pytest

# Cron script against the live site
PING_URLS=https://deven-bde-sales-portal-frontend.onrender.com/health backend/.venv/Scripts/python.exe deploy/render/keepalive.py

# Keepalive tests only
cd backend && .venv/Scripts/python.exe -m pytest tests/test_keepalive.py -q -p no:cacheprovider
```
