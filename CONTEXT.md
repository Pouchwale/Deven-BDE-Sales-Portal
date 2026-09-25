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

## Done this session (uncommitted)

1. **"Set password" instead of "Not available"** — `frontend/src/components/admin/PasswordReveal.tsx`
   takes an optional `onSetPassword`; when no readable copy exists it shows a
   **Set password** button that opens the existing reset dialog. Wired in
   `frontend/src/app/(portal)/admin/users/page.tsx` (row key includes
   `password_changed_at` so the row refreshes after a reset) and in
   `UserDetailPanel` in `frontend/src/components/admin/UserActionDialogs.tsx`.
   Only offered when `can_act_on`. `tsc` and `eslint` pass; not yet viewed in a
   browser.
2. **Keep-alive cron job** — `render.yaml` (Blueprint, repo root) declares cron
   `portal-keepalive`, schedule `*/14 3-14 * * 1-6` (UTC = 08:30–20:30 IST,
   Mon–Sat), running `deploy/render/keepalive.py` (stdlib only; pings each URL in
   `PING_URLS`, 3 attempts 30 s apart, exits non-zero on failure).
   - Working hours only on purpose: two free services awake 24/7 ≈ 1,440
     instance-hours/month vs Render's 750 free, after which Render suspends free
     services until the next month.
   - Render cron jobs are paid (starter, ~$1/month minimum).
   - To enable: push, Render → New → Blueprint → this repo, set
     `PING_URLS=https://deven-bde-sales-portal-frontend.onrender.com/health`.
   - The user asked for "every 15 min"; 14 is used so a ping always lands
     before the 15-minute idle spin-down.

**Also uncommitted, not from this session:** `frontend/src/app/login/page.tsx`
(better sign-in error messages; retries Render's 429 `hibernate-rate-limited`
during the wake loop instead of blaming the password) and
`backend/app/seeds/reset_users.py`. Review before committing.

---

## Open issue: Admin can't sign in on the live site

- User reports "Incorrect username or password" for Admin (`@superadmin`,
  typing `ChangeMe@123`) on Render.
- Finding: **every** request to the live frontend, including `/health`,
  returned `HTTP 429` with header `x-render-routing: hibernate-rate-limited` for
  10+ minutes on 2026-09-24 (~15:20–15:45 IST). That is Render refusing to wake
  the sleeping free service — the request never reaches the app.
- Most likely: free instance-hours for the month are used up (suspended until
  Oct 1). Not confirmed — needs the portal's Render dashboard → Billing.
- Next steps for the user:
  1. Check free usage. If exhausted: move frontend + backend to Starter
     (~$7/month each; they then never sleep) or wait until Oct 1.
  2. If not exhausted: Manual Deploy → Restart on both services.
  3. Once up, if Admin still fails: probably a lockout — wait 15 min, or set a
     new `SUPER_ADMIN_PASSWORD` in Render.
  4. Set `PASSWORD_VIEW_KEY` on Render to match local (see above).
  5. Push the uncommitted changes and apply the Blueprint.
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

# Keep-alive script locally
PING_URLS=https://example.com/ backend/.venv/Scripts/python.exe deploy/render/keepalive.py
```
