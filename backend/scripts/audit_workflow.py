"""End-to-end business workflow audit, over HTTP, against a DISPOSABLE server.

Walks one lead from assignment to feedback and checks, at every stage, that
each dependent module reacts: leads, notifications, dashboard, references,
feedback, team, and the permission rules in between.

REFUSES to run against anything but ENV=e2e. It creates leads, post-sale rows,
references and feedback, and none of that may land in the demo database.

    ENV=e2e DATABASE_URL=sqlite:///./e2e_portal.db GOOGLE_SYNC_SECRET=... \\
        python -m uvicorn app.main:app --port 8000
    AUDIT_SYNC_SECRET=... python scripts/audit_workflow.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = os.environ.get("AUDIT_API", "http://127.0.0.1:8000")
PW = os.environ.get("AUDIT_PASSWORD", "ChangeMe@123")
SECRET = os.environ.get("AUDIT_SYNC_SECRET", "")

results: list[tuple[str, str, str]] = []   # (phase, verdict, detail)
PHASE = "?"


def phase(name: str) -> None:
    global PHASE
    PHASE = name
    print("\n" + "=" * 96 + "\n" + name + "\n" + "=" * 96)


def check(label: str, condition: bool, detail: str = "") -> bool:
    verdict = "PASS" if condition else "FAIL"
    results.append((PHASE, verdict, label + (f" — {detail}" if detail and not condition else "")))
    print(f"  {'ok  ' if condition else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not condition else ""))
    return condition


def note(text: str) -> None:
    results.append((PHASE, "NOTE", text))
    print(f"  note {text}")


def call(path, token=None, method="GET", body=None, raw=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, data) as r:
            text = r.read()
            return r.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read()
        try:
            return e.code, json.loads(text) if text else None
        except Exception:
            return e.code, None


# ------------------------------------------------------------------ guard
code, health = call("/health")
if code != 200 or (health or {}).get("env") != "e2e":
    sys.exit(f"Refusing: {BASE} reports env={(health or {}).get('env')!r}, not 'e2e'.")


def login(email):
    code, body = call("/api/auth/login", method="POST", body={"email": email, "password": PW})
    assert code == 200, f"login failed for {email}: {code} {body}"
    return body["access_token"], body["user"]


T = {}
U = {}
for key, email in {
    "super": "owner@pouchwale.com",
    "admin": "shail.patel@pouchwale.com",
    "navya": "navya.rupawat@pouchwale.com",
    "ramanesh": "ramanesh.nair@pouchwale.com",
    "shailesh": "shailesh.prajapati@pouchwale.com",
    "parth": "parth.fulvani@pouchwale.com",
    "muskan": "muskan.makhija@pouchwale.com",
    "parag": "parag.sharma@pouchwale.com",
    "kevin": "kevin@pouchwale.com",
}.items():
    T[key], U[key] = login(email)

stamp = str(int(time.time()))[-6:]


def unread(who):
    return call("/api/notifications?page_size=100", T[who])[1]


def stats(who):
    return call("/api/leads/stats", T[who])[1]


def dash(who):
    return call("/api/dashboard", T[who])[1]


def refs(who):
    return call("/api/references/stats", T[who])[1]


def fb(who):
    return call("/api/feedback/pending/summary", T[who])[1]


def move(who, lead_id, status, remark="audit step"):
    return call(f"/api/leads/{lead_id}/status", T[who], "POST",
                {"status": status, "remark": remark})


# ============================================================ PHASE 5
phase("PHASE 5 — ASSIGNMENT")
before_notif = unread("parth")
before_stats = {w: stats(w) for w in ("parth", "navya", "admin", "ramanesh", "muskan")}

code, lead = call("/api/leads", T["navya"], "POST", {
    "name": f"Audit Traders {stamp}",
    "company_name": "Audit Traders Pvt Ltd",
    "mobile": f"98{stamp}01"[:10],
    "assigned_to_user_id": U["parth"]["id"],
})
check("manager creates and assigns a lead", code == 201, f"{code} {lead}")
LID = lead["id"]
check("assigned_to is the employee", lead["assigned_to_user_id"] == U["parth"]["id"])
check("assigned_by is the manager", lead.get("assigned_by_user_id") == U["navya"]["id"],
      str(lead.get("assigned_by_user_id")))
check("starts at NEW", lead["status"] == "NEW", lead["status"])
check("has a created timestamp", bool(lead.get("created_at")))
acts = lead.get("activities") or []
check("history records the assignment",
      any(a.get("activity_type") == "ASSIGNED" for a in acts),
      str([a.get("activity_type") for a in acts]))

after_notif = unread("parth")
new_for_parth = [n for n in after_notif["items"]
                 if n.get("entity_id") == LID]
check("employee receives an assignment notification", len(new_for_parth) >= 1,
      f"{len(new_for_parth)} for this lead")
for other in ("muskan", "ramanesh", "kevin"):
    stray = [n for n in unread(other)["items"] if n.get("entity_id") == LID]
    check(f"{other} does NOT receive it", not stray, f"{len(stray)}")

check("employee sees the lead", call(f"/api/leads/{LID}", T["parth"])[0] == 200)
check("assigning manager sees it", call(f"/api/leads/{LID}", T["navya"])[0] == 200)
check("admin sees it", call(f"/api/leads/{LID}", T["admin"])[0] == 200)
check("peer employee cannot open it", call(f"/api/leads/{LID}", T["muskan"])[0] == 404)
check("other chain's manager cannot open it", call(f"/api/leads/{LID}", T["ramanesh"])[0] == 404)

code, mine = call("/api/leads?assigned_by_me=true&page_size=200", T["navya"])
check("'Assigned by me' contains it for the assigning manager",
      any(i["id"] == LID for i in mine["items"]))
check("'Assigned by me' contains only leads she assigned",
      all(i.get("assigned_by_user_id") in (None, U["navya"]["id"]) for i in mine["items"])
      and all(i.get("assigned_by_user_id") == U["navya"]["id"] for i in mine["items"]
              if "assigned_by_user_id" in i),
      "a lead assigned by someone else appeared")
code, admin_mine = call("/api/leads?assigned_by_me=true&page_size=200", T["admin"])
check("'Assigned by me' for another manager does NOT contain it",
      not any(i["id"] == LID for i in admin_mine["items"]))

for who in ("parth", "navya", "admin"):
    s = stats(who)
    check(f"{who}: total leads +1", s["total"] == before_stats[who]["total"] + 1,
          f"{before_stats[who]['total']} -> {s['total']}")
for who in ("ramanesh", "muskan"):
    check(f"{who}: total unchanged", stats(who)["total"] == before_stats[who]["total"])

# ============================================================ PHASE 5 — machine
phase("PHASE 5 — STATE MACHINE")
code, body = move("parth", LID, "QUALIFIED")
check("NEW -> QUALIFIED (skipping) is refused", code == 422,
      f"{code} {((body or {}).get('error') or {}).get('code')}")
code, body = move("parth", LID, "CONVERTED")
check("NEW -> CONVERTED is refused", code == 422, str(code))
code, body = call(f"/api/leads/{LID}/status", T["parth"], "POST", {"status": "CONTACTED"})
check("a stage change without a remark is refused", code == 422, str(code))
code, body = call(f"/api/leads/{LID}/status", T["parth"], "POST",
                  {"status": "CONTACTED", "remark": "   "})
check("a whitespace-only remark is refused", code == 422, str(code))

path = ["CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED", "CONVERTED"]
ok_path = True
for step in path:
    code, body = move("parth", LID, step, f"moved to {step}")
    ok_path &= check(f"legal move to {step}", code == 200, f"{code} {body}")
detail = call(f"/api/leads/{LID}", T["parth"])[1]
changes = [a for a in detail.get("activities", []) if a.get("activity_type") == "STATUS_CHANGED"]
check("every stage change is on the timeline with its remark",
      len(changes) == 5 and all(a.get("remark") for a in changes), f"{len(changes)} recorded")
check("converted lead has closed_at", bool(detail.get("closed_at")))

# Terminal states
code, junk = call("/api/leads", T["navya"], "POST", {
    "name": f"Audit Junk {stamp}", "mobile": f"97{stamp}02"[:10],
    "assigned_to_user_id": U["parth"]["id"]})
JUNK = junk["id"]
check("NEW -> JUNK allowed", move("parth", JUNK, "JUNK")[0] == 200)
check("JUNK is final for the employee", move("parth", JUNK, "CONTACTED")[0] == 422)
check("JUNK is final for the manager", move("navya", JUNK, "CONTACTED")[0] == 422)
code, _ = call(f"/api/leads/{JUNK}/reopen", T["navya"], "POST", {"status": "NEW", "remark": "mistake"})
check("a manager cannot reopen", code == 403, str(code))
code, _ = call(f"/api/leads/{JUNK}/reopen", T["parth"], "POST", {"status": "NEW", "remark": "mistake"})
check("an employee cannot reopen", code == 403, str(code))
code, _ = call(f"/api/leads/{JUNK}/reopen", T["admin"], "POST", {"status": "NEW", "remark": ""})
check("admin reopen without a reason is refused", code == 422, str(code))
code, reopened = call(f"/api/leads/{JUNK}/reopen", T["admin"], "POST",
                      {"status": "NEW", "remark": "marked junk by mistake"})
check("admin can reopen, back to NEW", code == 200 and reopened["status"] == "NEW",
      f"{code} {(reopened or {}).get('status')}")
code, _ = call(f"/api/leads/{JUNK}/reopen", T["super"], "POST", {"status": "NEW", "remark": "x"})
check("reopening an open lead is refused", code == 422, str(code))

code, lost = call("/api/leads", T["navya"], "POST", {
    "name": f"Audit Lost {stamp}", "mobile": f"96{stamp}03"[:10],
    "assigned_to_user_id": U["parth"]["id"]})
LOST = lost["id"]
for step in ("CONTACTED", "NURTURING", "PRE_QUALIFIED"):
    move("parth", LOST, step)
check("PRE_QUALIFIED -> LOST allowed", move("parth", LOST, "LOST")[0] == 200)
check("LOST is final", move("parth", LOST, "NURTURING")[0] == 422)

code, nc = call("/api/leads", T["navya"], "POST", {
    "name": f"Audit NoContact {stamp}", "mobile": f"95{stamp}04"[:10],
    "assigned_to_user_id": U["parth"]["id"]})
NC = nc["id"]
check("NEW -> NOT_CONTACTED allowed", move("parth", NC, "NOT_CONTACTED")[0] == 200)
check("NOT_CONTACTED -> NURTURING (skipping CONTACTED) refused", move("parth", NC, "NURTURING")[0] == 422)
check("NOT_CONTACTED -> CONTACTED allowed", move("parth", NC, "CONTACTED")[0] == 200)
code, _ = move("parth", NC, "JUNK")
note(f"CONTACTED -> JUNK is {'ALLOWED' if code == 200 else 'REFUSED'} "
     "(brief says CONTACTED -> NURTURING only; original instruction allowed junk/lost)")

# ============================================================ PHASE 6
phase("PHASE 6 — ACTIVITY LOG vs STATUS PERMISSION")
code, act = call("/api/leads", T["navya"], "POST", {
    "name": f"Audit Perms {stamp}", "mobile": f"94{stamp}05"[:10],
    "assigned_to_user_id": U["parth"]["id"]})
P = act["id"]
code, _ = call(f"/api/leads/{P}/activities", T["parth"], "POST",
               {"activity_type": "CALL", "remark": "spoke to purchase head"})
check("assignee can log activity", code == 200, str(code))
code, _ = call(f"/api/leads/{P}/activities", T["parth"], "POST", {"activity_type": "CALL"})
check("activity without a remark is refused", code == 422, str(code))
for who, expect in (("navya", 403), ("admin", 403), ("super", 403)):
    code, _ = call(f"/api/leads/{P}/activities", T[who], "POST",
                   {"activity_type": "NOTE", "remark": "written on their behalf"})
    check(f"{who} cannot write on the assignee's log", code == expect, str(code))
code, _ = call(f"/api/leads/{P}/activities", T["muskan"], "POST",
               {"activity_type": "NOTE", "remark": "peer"})
check("a peer cannot write on it (cannot even see it)", code == 404, str(code))
code, _ = move("navya", P, "CONTACTED", "manager confirmed the call")
check("the manager CAN change the stage (separate permission)", code == 200, str(code))
code, _ = move("admin", P, "NURTURING", "admin moved it on")
check("admin CAN change the stage", code == 200, str(code))
code, _ = move("muskan", P, "PRE_QUALIFIED", "peer")
check("a peer cannot change the stage", code == 404, str(code))
code, _ = move("ramanesh", P, "PRE_QUALIFIED", "other chain")
check("the other chain's manager cannot change the stage", code == 404, str(code))

# ============================================================ PHASE 7
phase("PHASE 7 — REFERENCE WORKFLOW")
r0 = refs("parth")
check("converted, never synced -> counted as awaiting sync",
      r0["awaiting_sync"] >= 1 and r0["eligible_accounts"] == 0, json.dumps(r0))
accounts = call("/api/references/accounts", T["parth"])[1]
check("not yet listed as askable", not any(a["subject_id"] == LID for a in accounts))
code, _ = call("/api/references", T["parth"], "POST", {"lead_id": LID, "outcome": "YES", "referred_name": "x"})
check("asking before the post-sale sync is refused by the API", code == 422, str(code))

mobile = f"98{stamp}01"[:10]
ref_code = f"AUDIT-{stamp}-1"
code, sync = call("/api/post-sale/sync", T["admin"], "POST", {"rows": [{
    "external_ref": ref_code, "customer_name": "Audit Traders", "mobile": mobile,
    "invoice_date": (date.today() - timedelta(days=4)).isoformat()}]})
check("post-sale row matches the converted lead by mobile",
      code == 200 and sync["matched"] == 1, json.dumps(sync))
r1 = refs("parth")
check("invoiced 4 days ago -> waiting, not eligible",
      r1["waiting_period"] >= 1 and r1["eligible_accounts"] == 0, json.dumps(r1))
code, _ = call("/api/references", T["parth"], "POST", {"lead_id": LID, "outcome": "YES", "referred_name": "x"})
check("asking inside the 10-day wait is refused", code == 422, str(code))

code, sync = call("/api/post-sale/sync", T["admin"], "POST", {"rows": [{
    "external_ref": ref_code, "customer_name": "Audit Traders", "mobile": mobile,
    "invoice_date": (date.today() - timedelta(days=10)).isoformat()}]})
check("re-sync updates the same row, no duplicate", sync["updated"] == 1, json.dumps(sync))
r2 = refs("parth")
check("day 10 exactly -> eligible", r2["eligible_accounts"] >= 1, json.dumps(r2))
check("eligible + waiting + awaiting == converted",
      r2["eligible_accounts"] + r2["waiting_period"] + r2["awaiting_sync"] == r2["converted_leads"])
accounts = call("/api/references/accounts", T["parth"])[1]
check("now listed as askable, with its reference date",
      any(a["subject_id"] == LID and a.get("reference_date") for a in accounts))

# Not right now -> pending + follow-up
code, _ = call("/api/references", T["parth"], "POST", {
    "lead_id": LID, "outcome": "NO", "next_reference_date": date.today().isoformat(),
    "notes": "busy this month"})
check("'Not right now' records", code == 201, str(code))
code, _ = call("/api/references", T["parth"], "POST", {"lead_id": LID, "outcome": "NO"})
check("'Not right now' without a date is refused", code == 422, str(code))
r3 = refs("parth")
check("pending +1, completed unchanged", r3["references_pending"] >= 1, json.dumps(r3))
fu = call("/api/references/follow-ups", T["parth"])[1]
check("appears in follow-ups due today", any(f["subject_id"] == LID for f in fu))

# Gave reference -> completed, taken
code, _ = call("/api/references", T["parth"], "POST", {
    "lead_id": LID, "outcome": "YES", "referred_name": "Meera Joshi",
    "referred_company": "Joshi Snacks", "referred_mobile": "9811100022"})
check("'Gave reference' records", code == 201, str(code))
r4 = refs("parth")
check("taken +1 and completed +1", r4["references_taken"] >= 1 and r4["requests_completed"] >= 1,
      json.dumps(r4))
fu = call("/api/references/follow-ups", T["parth"])[1]
check("a completed account leaves the follow-up queue", not any(f["subject_id"] == LID for f in fu))
check("dashboard reference tile matches the module",
      dash("parth")["references"]["references_taken"] == r4["references_taken"])

# THE SUSPECTED BUG: can a completed account be overwritten through the API?
taken_before = refs("parth")["references_taken"]
code, _ = call("/api/references", T["parth"], "POST", {
    "lead_id": LID, "outcome": "NO", "next_reference_date": date.today().isoformat()})
after = refs("parth")
fu = call("/api/references/follow-ups", T["parth"])[1]
regressed = code != 409 or any(f["subject_id"] == LID for f in fu) or after["references_taken"] < taken_before
check("a COMPLETED account cannot be dragged back to pending via the API", not regressed,
      f"API returned {code}; taken {taken_before}->{after['references_taken']}; "
      f"back in follow-ups: {any(f['subject_id'] == LID for f in fu)}")

# A second lead for NOT_SHARED
code, l2 = call("/api/leads", T["navya"], "POST", {
    "name": f"Audit NotShared {stamp}", "mobile": f"93{stamp}06"[:10],
    "assigned_to_user_id": U["parth"]["id"]})
L2 = l2["id"]
for step in path:
    move("parth", L2, step)
call("/api/post-sale/sync", T["admin"], "POST", {"rows": [{
    "external_ref": f"AUDIT-{stamp}-2", "mobile": f"93{stamp}06"[:10],
    "invoice_date": (date.today() - timedelta(days=30)).isoformat()}]})
code, _ = call("/api/references", T["parth"], "POST", {"lead_id": L2, "outcome": "NOT_SHARED",
                                                        "next_reference_date": date.today().isoformat()})
check("'Not shared' records (a stray date is dropped, not an error)", code == 201, str(code))
r5 = refs("parth")
acct2 = next((a for a in call("/api/references/accounts", T["parth"])[1]
              if a["subject_id"] == L2), {})
check("'Not shared' counts as completed", acct2.get("reference_status") == "DECLINED",
      str(acct2.get("reference_status")))
check("'Not shared' does NOT count as a reference received",
      r5["references_taken"] == refs("parth")["references_taken"])
fu = call("/api/references/follow-ups", T["parth"])[1]
check("'Not shared' is not in follow-ups", not any(f["subject_id"] == L2 for f in fu))

# ============================================================ PHASE 8
phase("PHASE 8 — FEEDBACK WORKFLOW")
FORM_URL = os.environ.get("AUDIT_FORM_URL", "")
if FORM_URL:
    # Configure a (fake) Google Form on the DISPOSABLE server, so this phase
    # tests whether the workflow is sound rather than whether it is set up.
    code, _ = call("/api/admin/settings", T["admin"], "PATCH", {"values": {
        "company.feedback_form_url": FORM_URL,
        "feedback.form_reference_entry_id": "entry.1234567890",
    }})
    check("feedback form configured for this run", code == 200, str(code))
else:
    note("AUDIT_FORM_URL not set — running with the demo's unconfigured form")
summary = fb("parth")
pending = call("/api/feedback/pending", T["parth"])[1]
check("eligible converted lead is owed feedback (NOT_ASKED)",
      any(p["id"] == LID and p["state"] == "NOT_ASKED" for p in pending),
      str([(p["name"], p["state"]) for p in pending]))
check("feedback population == reference population",
      summary["eligible"] == refs("parth")["eligible_accounts"],
      f"{summary['eligible']} vs {refs('parth')['eligible_accounts']}")

code, composed = call(f"/api/leads/{LID}/message?channel=WHATSAPP", T["parth"])
check("assignee composes a feedback request", code == 200, str(code))
pending = call("/api/feedback/pending", T["parth"])[1]
check("composing alone does NOT count as asked (still NOT_ASKED)",
      any(p["id"] == LID and p["state"] == "NOT_ASKED" for p in pending),
      str([(p["name"], p["state"]) for p in pending if p["id"] == LID]))
blob = json.dumps(composed)
m = re.search(r"FB-\d{4}-\d{5}\.[A-Za-z0-9_\-]+", blob)
check("the message carries an unguessable FB code", bool(m),
      "link_configured={}: no feedback form URL is set, so the message has no link and no code"
      .format((composed or {}).get("link_configured")))
fb_code = m.group(0) if m else ""
code, _ = call(f"/api/leads/{LID}/message/sent", T["parth"], "POST",
               {"channel": "WHATSAPP", "purpose": "FEEDBACK"})
check("marking it sent succeeds", code == 200, str(code))
pending = call("/api/feedback/pending", T["parth"])[1]
check("queue now shows it AWAITING, not gone",
      any(p["id"] == LID and p["state"] == "AWAITING" for p in pending),
      str([(p["name"], p["state"]) for p in pending]))
code, _ = call(f"/api/leads/{LID}/message?channel=WHATSAPP", T["navya"])
check("a manager cannot compose on the assignee's behalf", code == 403, str(code))
code, _ = call(f"/api/leads/{LID}/message/sent", T["navya"], "POST",
               {"channel": "WHATSAPP", "purpose": "FEEDBACK"})
check("a manager cannot mark it sent either", code == 403, str(code))

if not SECRET:
    note("AUDIT_SYNC_SECRET not set — webhook submission skipped")
else:
    def deliver(response_id, answers):
        body = json.dumps({"response_id": response_id, "answers": answers}).encode()
        t = int(time.time())
        sig = hmac.new(SECRET.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        return call("/api/feedback/sync/webhook", method="POST", raw=body,
                    headers={"X-Portal-Signature": f"t={t},v1={sig}"})

    answers = {
        "Timestamp": "2026-09-13 11:00:00",
        "Reference code": fb_code,
        "Your name": "Audit Traders",
        "Mobile number": mobile,
        "Overall, how satisfied are you with us?": "2",
        "How would you rate our Production team?": "2",
        "Any comments about Production?": "Late delivery.",
        "How would you rate our Dispatch team?": "1",
    }
    code, res = deliver(f"audit-{stamp}-1", answers)
    check("customer response arrives and is stored", code == 200 and res["status"] == "stored",
          f"{code} {res}")
    check("matched by the FB code, not by guessing", (res or {}).get("match_status") == "MATCHED_TOKEN",
          str(res))
    pending = call("/api/feedback/pending", T["parth"])[1]
    check("answered account leaves the pending queue", not any(p["id"] == LID for p in pending))
    s2 = fb("parth")
    check("received +1", s2["received"] >= 1, json.dumps(s2))
    check("received + pending == eligible", s2["received"] + s2["pending"] == s2["eligible"],
          json.dumps(s2))

    code, res = deliver(f"audit-{stamp}-1", answers)
    check("the same delivery twice is a no-op", (res or {}).get("status") == "duplicate", str(res))
    code, res = deliver(f"audit-{stamp}-2", dict(answers, **{"Timestamp": "2026-09-13 11:05:00"}))
    check("a second response on an answered code is flagged DUPLICATE",
          (res or {}).get("match_status") == "DUPLICATE", str(res))
    check("and does not double-count received", fb("parth")["received"] == s2["received"])

    # B3 - a second asked lead answers WITHOUT the code, from their own phone.
    l2_mobile = f"93{stamp}06"[:10]
    call(f"/api/leads/{L2}/message?channel=WHATSAPP", T["parth"])
    call(f"/api/leads/{L2}/message/sent", T["parth"], "POST",
         {"channel": "WHATSAPP", "purpose": "FEEDBACK"})
    code, res = deliver(f"audit-{stamp}-lead", {"Timestamp": "2026-09-13 12:00:00",
                                                "Reference code": "",
                                                "Your name": "Audit NotShared",
                                                "Mobile number": f"+91 {l2_mobile}",
                                                "Overall, how satisfied are you with us?": "5"})
    check("an un-coded response matches the LEAD that was asked (not the SAP archive)",
          (res or {}).get("match_status") == "MATCHED_CONTACT"
          and (res or {}).get("reference", "").startswith("FB-"), str(res))
    pending = call("/api/feedback/pending", T["parth"])[1]
    check("and that lead leaves the pending queue", not any(p["id"] == L2 for p in pending))

    code, res = deliver(f"audit-{stamp}-3", {"Timestamp": "x", "Reference code": "",
                                             "Your name": "Nobody Known",
                                             "Mobile number": "9000000000",
                                             "Overall, how satisfied are you with us?": "4"})
    check("a response with no code and no match is kept as UNMATCHED",
          (res or {}).get("match_status") == "UNMATCHED", str(res))
    code, res = deliver(f"audit-{stamp}-4", {"Timestamp": "x", "Reference code": fb_code,
                                             "Your name": "No Ratings"})
    check("a response with no rating is refused as unusable",
          (res or {}).get("status") == "failed" or code >= 400, f"{code} {res}")
    code, _ = call("/api/feedback/sync/webhook", method="POST", raw=b'{"response_id":"x","answers":{}}',
                   headers={"X-Portal-Signature": "t=1,v1=deadbeef"})
    check("an unsigned/forged delivery is rejected", code == 401, str(code))

    analysis = call("/api/feedback/analysis", T["admin"])[1]
    check("department analysis reflects the response",
          any(d.get("response_count", 0) >= 1 for d in analysis.get("departments", [])))
    note("department alerts need FEEDBACK_ALERT_MIN_RESPONSES (5) in the window — one low "
         "rating alone correctly raises none")

# ============================================================ PHASE 9/10
phase("PHASE 9/10 — SHARED POPULATION + CROSS-MODULE RECONCILIATION")
for who in ("super", "admin", "navya", "ramanesh", "shailesh", "parth", "muskan", "parag"):
    s, d, r, f = stats(who), dash(who), refs(who), fb(who)
    listing = call("/api/leads?page_size=200", T[who])[1]
    same = (listing["total"] == s["total"] == d["leads"]["total"]
            and s["converted"] == d["leads"]["converted"] == r["converted_leads"] == f["converted"]
            and r["eligible_accounts"] == f["eligible"]
            and r["eligible_accounts"] + r["waiting_period"] + r["awaiting_sync"] == r["converted_leads"])
    check(f"{who}: leads/dashboard/references/feedback agree "
          f"({s['total']} leads, {s['converted']} conv, {r['eligible_accounts']} eligible)", same,
          f"list={listing['total']} stats={s['total']} dash={d['leads']['total']} "
          f"conv={s['converted']}/{d['leads']['converted']}/{r['converted_leads']}/{f['converted']} "
          f"elig={r['eligible_accounts']}/{f['eligible']}")

# Team rows for the manager
team = dash("navya")["reports"]
prow = next((r for r in team if r["user_id"] == U["parth"]["id"]), None)
if prow:
    ps = stats("parth")
    check("Navya's team row for Parth matches Parth's own stats",
          prow["open_leads"] == ps["open"] and prow["converted"] == ps["converted"],
          f"team open={prow['open_leads']} conv={prow['converted']} vs own open={ps['open']} conv={ps['converted']}")
    check("Parth's reference count on the team row matches the references taken",
          prow["references_taken"] == refs("parth")["references_taken"],
          f"{prow['references_taken']} vs {refs('parth')['references_taken']}")

# ============================================================ PHASE 21
phase("PHASE 21 — EDGE CASES")
code, _ = call("/api/leads", T["navya"], "POST", {"name": "Bad Phone", "mobile": "12345",
                                                 "assigned_to_user_id": U["parth"]["id"]})
check("an invalid phone is refused", code == 422, str(code))
code, _ = call("/api/leads", T["navya"], "POST", {"name": "Wrong Chain", "mobile": "9123456780",
                                                 "assigned_to_user_id": U["parag"]["id"]})
check("a manager cannot assign into another chain", code in (403, 404, 422), str(code))
code, _ = call("/api/leads", T["parth"], "POST", {"name": "Self Assign", "mobile": "9123456781",
                                                 "assigned_to_user_id": U["parth"]["id"]})
note(f"a field BDE creating a lead returns {code}")
code, dup = call("/api/leads", T["navya"], "POST", {"name": f"Audit Traders {stamp}",
                                                   "mobile": mobile,
                                                   "assigned_to_user_id": U["muskan"]["id"]})
note(f"creating a lead with the SAME name+mobile as an existing lead returns {code} "
     "(no duplicate detection on manual entry)")
code, unm = call("/api/post-sale/sync", T["admin"], "POST", {"rows": [{
    "external_ref": f"AUDIT-{stamp}-X", "customer_name": "Stranger Co",
    "mobile": "9555500000", "invoice_date": date.today().isoformat()}]})
check("a post-sale row matching nothing is held UNMATCHED, not attached",
      unm["unmatched"] == 1 and unm["matched"] == 0, json.dumps(unm))
code, amb = call("/api/post-sale/sync", T["admin"], "POST", {"rows": [{
    "external_ref": f"AUDIT-{stamp}-Y", "mobile": mobile,
    "invoice_date": date.today().isoformat()}]})
note(f"post-sale row whose mobile now matches TWO leads (original + manual duplicate): {json.dumps(amb)}")
code, _ = call("/api/post-sale/sync", T["navya"], "POST", {"rows": [{"external_ref": "z"}]})
check("a manager cannot push post-sale data", code == 403, str(code))

# ------------------------------------------------------------------ summary
print("\n" + "=" * 96)
passed = sum(1 for _, v, _ in results if v == "PASS")
failed = [(p, d) for p, v, d in results if v == "FAIL"]
notes = [(p, d) for p, v, d in results if v == "NOTE"]
print(f"{passed} passed, {len(failed)} failed, {len(notes)} notes")
for p, d in failed:
    print(f"  FAIL [{p}] {d}")
for p, d in notes:
    print(f"  NOTE [{p}] {d}")
with open("audit_workflow_results.json", "w") as fh:
    json.dump({"passed": passed, "failed": failed, "notes": notes}, fh, indent=1)
sys.exit(1 if failed else 0)
