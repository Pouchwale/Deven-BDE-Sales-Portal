# Installing the feedback sync

What connects the company's Google Form to the portal, so a submitted response
turns itself into feedback against the right customer.

**Nothing here needs a Google Cloud project, a service account, an OAuth
client or an API key.** The script runs on Google's servers as the form owner;
the portal holds one shared secret and never talks to Google's APIs.

---

## Before you start

You need three things the portal cannot supply for you:

| | |
|---|---|
| The Google Form | Edit access, so you can add a question and open the script editor |
| A portal URL on public HTTPS | Google cannot reach `localhost`. Until the portal is deployed, use the manual **Sync now** button instead — the script's retry queue holds responses in the meantime |
| A shared secret | Any long random string. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

---

## 1. Add the reference field to the form

One new question, at the top:

> **Reference code**
> Short answer · Required

Do not change any existing question. The portal resolves your questions by
their wording, so the ones already there keep working untouched.

### Get its prefill id

1. In the form: ⋮ → **Get pre-filled link**
2. Type anything into Reference code, then **Get link** → **Copy link**
3. The link contains `entry.4839201=…` — that number is what you need

Put it in the portal: **Admin ▸ Settings ▸ Feedback form reference field**.
Paste either `entry.4839201` or just `4839201`.

Also paste the form's normal share URL into **Feedback form link**.

---

## 2. Configure the portal

In `backend/.env`:

```
GOOGLE_SYNC_SECRET=<the long random string>
GOOGLE_SYNC_URL=<the Web App URL from step 4, once you have it>
```

Restart the backend. **Admin ▸ Settings** now shows the assistant-style
status card for sync; it should say the webhook is configured.

---

## 3. Install the script

1. Open the **form** (not the response sheet) → ⋮ → **Script editor**
2. Paste `FeedbackSync.gs` over the default `Code.gs`
3. **Project Settings** → **Script Properties** → add two:

   | Property | Value |
   |---|---|
   | `PORTAL_URL` | `https://your-portal/api/feedback/sync/webhook` |
   | `PORTAL_SECRET` | the same value as `GOOGLE_SYNC_SECRET` |

4. **Triggers** → **Add trigger**:
   - Function: `onFormSubmit`
   - Event source: **From form**
   - Event type: **On form submit**
5. Approve the permissions prompt (it asks to connect to an external service —
   that is the portal)
6. Optional but recommended — a second trigger:
   - Function: `retryQueued`
   - Event source: **Time-driven** → Hour timer → Every hour

---

## 4. Deploy the Web App (for manual sync)

Only needed if you want the portal's **Sync now** button, which is the
fallback when the webhook could not deliver.

**Deploy** → **New deployment** → **Web app**:

- Execute as: **Me**
- Who has access: **Anyone with the link**

Copy the resulting URL into `GOOGLE_SYNC_URL` in `backend/.env` and restart.

> "Anyone with the link" sounds alarming and is not the security boundary.
> The script verifies the same HMAC signature the portal does, and answers
> `{"error":"unauthorized"}` to anything unsigned.

---

## 5. Check it

In the script editor, run **`testConnection`** once and open **Executions**.

| Log says | Means |
|---|---|
| `Reachable. A 422 here is expected` | Working. The probe carries no ratings, so the portal correctly refuses it |
| `PORTAL_SECRET does not match` | The two secrets differ |
| `Could not reach PORTAL_URL` | The portal is not on public HTTPS, or the URL is wrong |

Then submit a real test response through the form and check
**Feedback ▸ Sync** in the portal.

---

## What happens if something breaks

| | |
|---|---|
| Portal down | The script queues the response and retries hourly. Nothing is lost |
| Portal rejects the payload | Logged in Executions, and visible in **Feedback ▸ Sync ▸ Failed** |
| Somebody deletes the trigger | Responses accumulate unsent. Run `resendAll` — the portal skips everything it has already seen |
| A response has no reference code | It still arrives; the portal matches on mobile, email or exact company name, and puts it in **Needs review** if none of those match |
| Backfilling old responses | Run `resendAll` once. Safe to repeat |

---

## What the portal does with a delivery

```
signature → replay window → size → schema → idempotency → ingest
```

A repeat delivery of the same `response_id` is answered `200 {"status":
"duplicate"}` and changes nothing — that is why `resendAll` is safe.

The payload is never stored. The portal keeps its SHA-256 hash, the response
id and the outcome, because a table that answers "did this arrive?" has no
business holding a customer's name, mobile and comments.
