"""The business rules added in the September 2026 change set.

One file for the new rules, so the decisions behind them are readable in one
place. Each section names the rule and then proves it at the API - not only at
the service - because the requirement in every case was "impossible to bypass",
and a rule proven only against a Python function is a rule the HTTP layer can
still skip.

The decisions these encode, and why they are what they are, are in
BUSINESS_RULES_CHANGE_AUDIT.md.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core import eligibility
from app.core.validators import InvalidPhone, normalise_phone
from app.models.post_sale import PostSaleRecord
from tests.conftest import advance_lead, eligible_lead, sign_in, super_admin_headers

# =========================================================== phone numbers


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("9875003040", "9875003040"),
        # The same number, written the three ways SAP writes it.
        ("+91 97111 22505", "9711122505"),
        ("091-9711122505", "9711122505"),
        ("09875003040", "9875003040"),
        ("919875003040", "9875003040"),
        # Ten digits that happen to start 91 are a number, not a country code.
        ("9198750304", "9198750304"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_a_phone_number_is_recognised_however_it_is_written(raw, stored) -> None:
    assert normalise_phone(raw) == stored


@pytest.mark.parametrize(
    "raw",
    [
        "98750",           # too short
        "987500304",       # nine digits
        "98750030401",     # eleven
        "ABC9875003",      # letters
        "abcdefghij",      # letters only
        "1",               # the classic: must not become +91 anything
        "12345",
    ],
)
def test_anything_that_is_not_ten_digits_is_refused(raw) -> None:
    with pytest.raises(InvalidPhone):
        normalise_phone(raw)


def test_normalisation_never_invents_a_valid_number() -> None:
    """The dangerous failure mode, named explicitly.

    Nine digits must not become ten, and a stray "1" must not be padded into
    something that looks dialable. Recognising the same number written
    differently is the job; manufacturing a number is not.
    """
    for raw in ("1", "12345", "987500304"):
        with pytest.raises(InvalidPhone):
            normalise_phone(raw)


def test_the_api_refuses_a_bad_phone_even_though_the_form_would_have(
    client, users
) -> None:
    """Frontend validation is a courtesy. This is the enforcement."""
    navya = sign_in(client, users["Navya Rupawat"])
    response = client.post(
        "/api/leads",
        headers=navya,
        json={
            "name": "Bad Number",
            "mobile": "98750",
            "assigned_to_user_id": str(users["Parth Fulvani"].id),
        },
    )
    assert response.status_code == 422, response.text


def test_a_good_phone_is_stored_canonically_through_the_api(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = client.post(
        "/api/leads",
        headers=navya,
        json={
            "name": "Good Number",
            "mobile": "+91 98750 03040",
            "assigned_to_user_id": str(users["Parth Fulvani"].id),
        },
    )
    assert lead.status_code == 201, lead.text
    assert lead.json()["mobile"] == "9875003040"


# ==================================================== the 10-day SAP rule


def test_the_waiting_period_is_measured_from_the_invoice() -> None:
    today = date(2026, 9, 11)
    invoiced = date(2026, 9, 1)

    # Exactly ten days counts. An off-by-one here is a customer nobody asks
    # for an extra day.
    assert eligibility.is_eligible(invoiced, today=today) is True
    assert eligibility.is_eligible(invoiced + timedelta(days=1), today=today) is False
    assert eligibility.is_eligible(invoiced - timedelta(days=5), today=today) is True
    assert eligibility.eligible_on(invoiced) == date(2026, 9, 11)


def test_no_invoice_means_not_eligible() -> None:
    """Which is the entire answer for converted leads: they have no invoice
    date, so they are not eligible until SAP makes them a customer."""
    assert eligibility.is_eligible(None) is False
    assert eligibility.eligible_on(None) is None


def test_a_freshly_invoiced_account_is_not_yet_askable(client, db, users) -> None:
    """The rule, end to end, against a real won lead.

    The invoice date now comes from the post-sale sheet rather than SAP, so
    the test syncs one - which is also the only way an account becomes
    askable in production.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = eligible_lead(client, users, name="Freshly Invoiced")

    # Synced 30 days ago by the helper, so it is askable.
    rows = client.get("/api/references/accounts", headers=navya).json()
    assert [r for r in rows if r["subject_id"] == lead["id"]]

    # Move the invoice to three days ago: too soon to ask.
    record = db.execute(
        select(PostSaleRecord).where(PostSaleRecord.lead_id == lead["id"])
    ).scalars().one()
    record.invoice_date = date.today() - timedelta(days=3)
    db.commit()
    after = client.get("/api/references/accounts", headers=navya).json()
    assert not [r for r in after if r["subject_id"] == lead["id"]]

    # ...and it comes back on the day the waiting period ends.
    record.invoice_date = date.today() - timedelta(days=eligibility.ELIGIBILITY_DAYS)
    db.commit()
    back = client.get("/api/references/accounts", headers=navya).json()
    assert [r for r in back if r["subject_id"] == lead["id"]]


def test_the_same_rule_governs_the_feedback_queue(client, db, users) -> None:
    """Both modules, ONE population.

    They read the same eligible set from `services.metrics`, which is what
    stops the dashboard saying 20 pending while the module says 11.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = eligible_lead(client, users, name="Owed Feedback")

    owed = client.get("/api/feedback/pending", headers=navya).json()
    assert [row for row in owed if row["id"] == lead["id"]]

    record = db.execute(
        select(PostSaleRecord).where(PostSaleRecord.lead_id == lead["id"])
    ).scalars().one()
    record.invoice_date = date.today()
    db.commit()

    after = client.get("/api/feedback/pending", headers=navya).json()
    assert not [row for row in after if row["id"] == lead["id"]]


# ============================================== the "Not shared" outcome


def _an_account(client, users) -> tuple[dict, str]:
    """A synced, eligible won lead - the only thing that is askable now."""
    navya = sign_in(client, users["Navya Rupawat"])
    lead = eligible_lead(client, users, name=f"Ref Subject {uuid.uuid4().hex[:6]}")
    return navya, lead["id"]


def test_not_shared_completes_the_request_without_producing_a_reference(
    client, users
) -> None:
    """The distinction the whole change exists for.

    "I have nobody to refer" finishes the conversation - the customer should
    never be chased about it again - but no reference came of it. Counting it
    as one would claim a referral the company never received.
    """
    navya, customer_id = _an_account(client, users)

    recorded = client.post(
        "/api/references",
        headers=navya,
        json={"lead_id": customer_id, "outcome": "NOT_SHARED", "notes": "None to give."},
    )
    assert recorded.status_code == 201, recorded.text

    stats = client.get("/api/references/stats", headers=navya).json()
    assert stats["requests_completed"] >= 1, "the conversation is finished"
    assert stats["references_taken"] == 0, "but no reference was received"
    assert stats["references_declined"] >= 1


def test_a_completed_account_leaves_the_ask_queue(client, users) -> None:
    """§16: enforced by the query, not by hiding a button."""
    navya, customer_id = _an_account(client, users)
    client.post(
        "/api/references",
        headers=navya,
        json={"lead_id": customer_id, "outcome": "NOT_SHARED"},
    )

    not_asked = client.get(
        "/api/references/accounts",
        headers=navya,
        params={"reference_status": "NOT_ASKED"},
    ).json()
    assert not [r for r in not_asked if r["subject_id"] == customer_id]

    due = client.get("/api/references/follow-ups", headers=navya).json()
    assert not [r for r in due if r["subject_id"] == customer_id]


def test_not_right_now_stays_in_the_queue(client, users) -> None:
    """The outcome that is NOT completion. Kept distinct from "not shared" -
    that separation is the point of the change."""
    navya, customer_id = _an_account(client, users)
    soon = (date.today() + timedelta(days=7)).isoformat()

    client.post(
        "/api/references",
        headers=navya,
        json={
            "lead_id": customer_id,
            "outcome": "NO",
            "next_reference_date": soon,
        },
    )

    rows = client.get("/api/references/accounts", headers=navya).json()
    row = next(r for r in rows if r["subject_id"] == customer_id)
    assert row["reference_status"] == "PENDING", "still open, comes back later"


def test_not_right_now_must_say_when(client, users) -> None:
    """The database requires it; this turns the constraint into a sentence."""
    navya, customer_id = _an_account(client, users)
    response = client.post(
        "/api/references",
        headers=navya,
        json={"lead_id": customer_id, "outcome": "NO"},
    )
    assert response.status_code == 422, response.text
    # The sentence lives in details.fields - that is where a schema-level
    # failure lands, and it is what the form shows under the field.
    detail = " ".join(
        field["message"] for field in response.json()["error"]["details"]["fields"]
    )
    assert "ask again" in detail, detail
    assert "not shared" in detail, "it should name the outcome that needs no date"


def test_gave_a_reference_is_the_only_outcome_that_produced_one(
    client, users
) -> None:
    navya, customer_id = _an_account(client, users)
    client.post(
        "/api/references",
        headers=navya,
        json={
            "lead_id": customer_id,
            "outcome": "YES",
            "referred_name": "Meera Joshi",
            "referred_mobile": "9900011122",
        },
    )

    stats = client.get("/api/references/stats", headers=navya).json()
    assert stats["references_taken"] >= 1
    assert stats["requests_completed"] >= stats["references_taken"]

    rows = client.get("/api/references/accounts", headers=navya).json()
    row = next(r for r in rows if r["subject_id"] == customer_id)
    assert row["reference_status"] == "TAKEN"


# ===================================================== customer authority


@pytest.mark.parametrize("name", ["Shail Patel", "Navya Rupawat", "Parth Fulvani"])
def test_the_customer_book_is_refused_at_the_api_not_just_hidden(
    client, users, name
) -> None:
    """Hiding the nav item is not a permission. Every route that browses the
    book answers 403, including to an Admin."""
    headers = sign_in(client, users[name])
    for path in ("/api/customers", "/api/customers/stats"):
        assert client.get(path, headers=headers).status_code == 403, f"{name} {path}"


def test_the_workflows_that_need_a_customer_still_work(client, users) -> None:
    """The other half of the decision, and the reason it was not a blanket
    lock: Reference Tracking and the feedback queue read customer rows, and
    locking those would have emptied two modules for everyone but one person.
    """
    parth = sign_in(client, users["Parth Fulvani"])
    assert client.get("/api/references/accounts", headers=parth).status_code == 200
    assert client.get("/api/feedback/pending", headers=parth).status_code == 200


def test_the_super_admin_still_has_the_book(client) -> None:
    assert client.get("/api/customers", headers=super_admin_headers(client)).status_code == 200

# ================================================== lead log ownership


def _lead_for(client, users, assignee: str = "Parth Fulvani") -> dict:
    navya = sign_in(client, users["Navya Rupawat"])
    return client.post(
        "/api/leads",
        headers=navya,
        json={"name": "Owned Log", "assigned_to_user_id": str(users[assignee].id)},
    ).json()


def test_only_the_assignee_may_log_activity(client, users) -> None:
    """The log is a first-hand record. Somebody who was not on the call
    cannot write it truthfully, and it would be attributed to the assignee."""
    lead = _lead_for(client, users)
    body = {"activity_type": "CALL", "remark": "Spoke to them."}

    ok = client.post(
        f"/api/leads/{lead['id']}/activities",
        headers=sign_in(client, users["Parth Fulvani"]),
        json=body,
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize(
    "name",
    [
        "Navya Rupawat",    # the manager who assigned it
        "Shail Patel",      # an admin
        "Portal Owner",     # a super admin
        "Muskan Makhija",   # a peer on the same team
    ],
)
def test_nobody_else_may_log_activity_however_senior(client, users, name) -> None:
    """Seniority is not authorship. An admin outranks a BDE and still cannot
    write "I called them" on that BDE's lead."""
    lead = _lead_for(client, users)
    response = client.post(
        f"/api/leads/{lead['id']}/activities",
        headers=sign_in(client, users[name]),
        json={"activity_type": "CALL", "remark": "Not mine to write."},
    )
    # A peer cannot see the lead at all, so 404 is right for them; everyone
    # who CAN see it is refused with 403. Either way, nothing is written.
    assert response.status_code in (403, 404), f"{name}: {response.status_code}"


def test_a_manager_can_still_read_and_move_the_lead(client, users) -> None:
    """Read and stage stay with management - only the LOG is the assignee's.

    This is the line the change draws: a head still runs the pipeline and
    still reports on it; what they cannot do is write first-hand notes on
    somebody else's conversation.
    """
    lead = _lead_for(client, users)
    navya = sign_in(client, users["Navya Rupawat"])

    assert client.get(f"/api/leads/{lead['id']}", headers=navya).status_code == 200
    moved = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=navya,
        json={"status": "CONTACTED", "remark": "Reached them this morning."},
    )
    assert moved.status_code == 200, moved.text


# ================================================ the lead state machine


def _new_lead(client, users, name="Pipeline") -> tuple[dict, dict, dict]:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = client.post(
        "/api/leads",
        headers=navya,
        json={"name": name, "assigned_to_user_id": str(users["Parth Fulvani"].id)},
    ).json()
    return lead, navya, sign_in(client, users["Parth Fulvani"])


def _move(client, headers, lead_id, status):
    return client.post(
        f"/api/leads/{lead_id}/status",
        headers=headers,
        json={"status": status, "remark": f"Moving to {status}."},
    )


def test_a_new_lead_starts_at_new(client, users) -> None:
    lead, _, _ = _new_lead(client, users)
    assert lead["status"] == "NEW"


@pytest.mark.parametrize(
    "path",
    [
        # The four things that can happen to a brand new lead.
        ["CONTACTED"],
        ["NOT_CONTACTED"],
        ["JUNK"],
        ["LOST"],
        # The contacted route, all the way to won.
        ["CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED", "CONVERTED"],
        # The not-contacted route joins it once somebody picks up.
        ["NOT_CONTACTED", "CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED", "CONVERTED"],
        # A deal can die at any of the working stages.
        ["CONTACTED", "NURTURING", "LOST"],
        ["CONTACTED", "NURTURING", "PRE_QUALIFIED", "LOST"],
        ["CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED", "LOST"],
        ["NOT_CONTACTED", "JUNK"],
        ["NOT_CONTACTED", "LOST"],
        ["CONTACTED", "JUNK"],
    ],
)
def test_every_legal_route_through_the_pipeline(client, users, path) -> None:
    lead, _, parth = _new_lead(client, users, name="/".join(path))
    for step in path:
        response = _move(client, parth, lead["id"], step)
        assert response.status_code == 200, f"{step}: {response.text}"
        assert response.json()["status"] == step


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        # Skipping stages is the whole thing the pipeline exists to prevent.
        ([], "QUALIFIED"),
        ([], "NURTURING"),
        ([], "CONVERTED"),
        (["CONTACTED"], "QUALIFIED"),
        (["CONTACTED"], "PRE_QUALIFIED"),
        (["NOT_CONTACTED"], "QUALIFIED"),
        (["NOT_CONTACTED"], "NURTURING"),
        (["CONTACTED", "NURTURING"], "QUALIFIED"),
        (["CONTACTED", "NURTURING", "PRE_QUALIFIED"], "CONVERTED"),
        # Backwards is not a move either.
        (["CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED"], "NURTURING"),
        # And the three terminal states are terminal.
        (["CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED", "CONVERTED"], "QUALIFIED"),
        (["JUNK"], "CONTACTED"),
        (["LOST"], "CONTACTED"),
    ],
)
def test_illegal_moves_are_refused(client, users, path, illegal) -> None:
    lead, _, parth = _new_lead(client, users, name=f"{'-'.join(path)}-x-{illegal}")
    for step in path:
        assert _move(client, parth, lead["id"], step).status_code == 200

    refused = _move(client, parth, lead["id"], illegal)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "INVALID_TRANSITION"
    # The error carries what IS allowed, so the UI never has to guess.
    assert "allowed" in refused.json()["error"]["details"]


def test_a_stage_change_needs_a_remark(client, users) -> None:
    """A lead that moved with no reason recorded is the half nobody can use
    a year later - or the day somebody else inherits the account."""
    lead, _, parth = _new_lead(client, users)
    bare = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "CONTACTED"},
    )
    assert bare.status_code == 422, bare.text
    assert "remark" in bare.json()["error"]["message"].lower()

    # Whitespace is not a remark either.
    blank = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "CONTACTED", "remark": "   "},
    )
    assert blank.status_code == 422


def test_a_logged_activity_needs_a_remark(client, users) -> None:
    lead, _, parth = _new_lead(client, users)
    response = client.post(
        f"/api/leads/{lead['id']}/activities",
        headers=parth,
        json={"activity_type": "CALL"},
    )
    assert response.status_code == 422, response.text


# ------------------------------------------------------- the escape hatch


def test_only_an_administrator_can_reopen_a_closed_lead(client, users) -> None:
    """The narrow replacement for Undo.

    Terminal means terminal for the people who work leads. An administrator
    can put a mis-clicked one back, and that is itself recorded.
    """
    lead, navya, parth = _new_lead(client, users)
    assert _move(client, parth, lead["id"], "JUNK").status_code == 200

    for name in ("Parth Fulvani", "Navya Rupawat"):
        refused = client.post(
            f"/api/leads/{lead['id']}/reopen",
            headers=sign_in(client, users[name]),
            json={"status": "NEW", "remark": "Let me back in."},
        )
        assert refused.status_code == 403, f"{name}: {refused.status_code}"

    done = client.post(
        f"/api/leads/{lead['id']}/reopen",
        headers=sign_in(client, users["Shail Patel"]),
        json={"status": "NEW", "remark": "Marked junk by mistake."},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "NEW"

    # Recorded, not erased: the JUNK move and the reopen both survive.
    kinds = [(a["from_status"], a["to_status"]) for a in done.json()["activities"]]
    assert ("JUNK", "NEW") in kinds, kinds
    assert ("NEW", "JUNK") in kinds, kinds


def test_reopening_withdraws_feedback_eligibility(client, users) -> None:
    """Converting is what made it a won deal. Taking that back has to take
    the consequences back too, or a lead nobody won sits in the queue."""
    lead, navya, parth = _new_lead(client, users)
    advance_lead(client, parth, lead["id"])

    reopened = client.post(
        f"/api/leads/{lead['id']}/reopen",
        headers=sign_in(client, users["Shail Patel"]),
        json={"status": "NEW", "remark": "Converted in error."},
    ).json()
    assert reopened["dispatched_at"] is None
    assert reopened["closed_at"] is None


def test_an_open_lead_cannot_be_reopened(client, users) -> None:
    lead, _, parth = _new_lead(client, users)
    assert _move(client, parth, lead["id"], "CONTACTED").status_code == 200

    response = client.post(
        f"/api/leads/{lead['id']}/reopen",
        headers=sign_in(client, users["Shail Patel"]),
        json={"status": "NEW", "remark": "Nothing to reopen."},
    )
    assert response.status_code == 422, response.text


# ============================================ one universe, one set of numbers


RECONCILE_ROLES = [
    "Portal Owner",      # Super Admin - the whole company
    "Shail Patel",       # Admin       - the whole company
    "Navya Rupawat",     # BDE head    - her chain
    "Ramanesh Nair",     # Sales head  - his chain
    "Parth Fulvani",     # BDE         - his own work
]


@pytest.mark.parametrize("name", RECONCILE_ROLES)
def test_every_surface_reports_the_same_lead_count(client, users, name) -> None:
    """§34/§46: the same user, the same question, the same number.

    Assigned Leads, the dashboard and the assistant used to count from
    different places, so one screen said 22 and the next said 17. They now all
    resolve through `services.metrics` over one scope, and this asserts that
    rather than trusting it.
    """
    headers = sign_in(client, users[name])

    listing = client.get("/api/leads?page_size=200", headers=headers).json()
    module = client.get("/api/leads/stats", headers=headers).json()
    dashboard = client.get("/api/dashboard", headers=headers).json()

    assert listing["total"] == module["total"], f"{name}: list vs module"
    assert dashboard["leads"]["total"] == module["total"], f"{name}: dashboard"
    assert dashboard["leads"]["converted"] == module["converted"], f"{name}: converted"
    # And the rows really are the ones counted, not a page of something else.
    assert len(listing["items"]) == min(module["total"], 200)


@pytest.mark.parametrize("name", RECONCILE_ROLES)
def test_the_stage_counts_add_up_to_the_total(client, users, name) -> None:
    """A total that is not the sum of its parts is two numbers pretending to
    be one."""
    stats = client.get(
        "/api/leads/stats", headers=sign_in(client, users[name])
    ).json()
    stages = [
        "new", "contacted", "not_contacted", "nurturing",
        "pre_qualified", "qualified", "converted", "junk", "lost",
    ]
    assert sum(stats[stage] for stage in stages) == stats["total"], name


@pytest.mark.parametrize("name", RECONCILE_ROLES)
def test_the_post_sale_population_always_adds_up(client, users, name) -> None:
    """eligible + waiting + awaiting_sync == converted, for everyone.

    This is what makes a small eligible count explainable instead of alarming:
    the difference is always accounted for by one of the other two.
    """
    headers = sign_in(client, users[name])
    reference = client.get("/api/references/stats", headers=headers).json()
    leads = client.get("/api/leads/stats", headers=headers).json()

    assert reference["converted_leads"] == leads["converted"], name
    assert (
        reference["eligible_accounts"]
        + reference["waiting_period"]
        + reference["awaiting_sync"]
        == reference["converted_leads"]
    ), name


@pytest.mark.parametrize("name", RECONCILE_ROLES)
def test_reference_and_feedback_share_one_population(client, users, name) -> None:
    """The two modules measure the same accounts.

    They did not before: references counted SAP customers and feedback counted
    converted leads, which is how the dashboard could say 20 pending while the
    module said 11.
    """
    headers = sign_in(client, users[name])
    eligible = client.get("/api/references/stats", headers=headers).json()[
        "eligible_accounts"
    ]
    pending = client.get("/api/feedback/pending", headers=headers).json()

    # Everything owed feedback is an eligible account; nothing else can be.
    assert len(pending) <= eligible, name
    assert all(row["type"] == "LEAD" for row in pending), name


def test_the_assistant_reports_the_portals_numbers(client, users, monkeypatch) -> None:
    """§32: the assistant may not become a third opinion.

    It reads the dashboard's own payload, so this asserts the two agree for a
    manager and for a field user - the two scopes most likely to diverge.
    """
    from app.core import authority
    from app.db.session import SessionLocal
    from app.services.chat import tools as chat_tools

    for name in ("Navya Rupawat", "Parth Fulvani"):
        user = users[name]
        portal = client.get(
            "/api/leads/stats", headers=sign_in(client, user)
        ).json()

        with SessionLocal() as session:
            actor = session.get(type(user), user.id)
            scope = authority.visible_user_ids(session, actor)
            ctx = chat_tools.ToolContext(
                db=session,
                actor=actor,
                scope=scope,
                department_scope=authority.feedback_department_scope(actor),
            )
            outcome = chat_tools.dispatch(ctx, "get_my_work_summary", {})

        assert outcome["ok"], f"{name}: {outcome}"
        assert outcome["result"]["leads"]["total"] == portal["total"], name
        assert outcome["result"]["leads"]["converted"] == portal["converted"], name


@pytest.mark.parametrize(
    ("name", "forbidden_owner"),
    [
        # A BDE head must not reach the Sales chain by naming one of its people.
        ("Navya Rupawat", "Parag Sharma"),
        # ...and a Sales head must not reach hers.
        ("Ramanesh Nair", "Parth Fulvani"),
    ],
)
def test_scope_cannot_be_widened_by_a_query_parameter(
    client, users, name, forbidden_owner
) -> None:
    """§12/§42: the filter narrows what you may see. It never widens it.

    Asking for somebody else's leads by id returns nothing, rather than
    returning them because you asked politely.
    """
    headers = sign_in(client, users[name])
    target = users[forbidden_owner]

    body = client.get(
        "/api/leads",
        headers=headers,
        params={"assigned_to": str(target.id), "page_size": 200},
    ).json()
    assert body["total"] == 0, f"{name} reached {forbidden_owner}'s leads"


# ================================================= filtering happens in the DB


def test_reference_accounts_filter_on_the_server(client, users) -> None:
    """S42: a filter is a query parameter, not a browser-side array narrow.

    Narrowing a fetched page in React finds only the matches that happened to
    be on that page, which looks like an answer and is not one.
    """
    from tests.conftest import eligible_lead

    lead = eligible_lead(client, users, name="Filterable Traders")
    headers = sign_in(client, users["Navya Rupawat"])

    everything = client.get("/api/references/accounts", headers=headers).json()
    assert any(row["subject_name"] == "Filterable Traders" for row in everything)

    hit = client.get(
        "/api/references/accounts", headers=headers, params={"search": "filterable"}
    ).json()
    assert [row["subject_name"] for row in hit] == ["Filterable Traders"]

    miss = client.get(
        "/api/references/accounts", headers=headers, params={"search": "no such name"}
    ).json()
    assert miss == []

    owned = client.get(
        "/api/references/accounts",
        headers=headers,
        params={"owner_id": str(users["Parth Fulvani"].id)},
    ).json()
    assert all(row["owner_user_id"] == str(users["Parth Fulvani"].id) for row in owned)
    assert any(row["subject_id"] == lead["id"] for row in owned)


def test_an_owner_filter_cannot_reach_outside_the_callers_scope(client, users) -> None:
    """The owner dropdown narrows. It does not widen - asking for somebody
    else's owner id returns nothing, rather than returning them."""
    body = client.get(
        "/api/references/accounts",
        headers=sign_in(client, users["Ramanesh Nair"]),
        params={"owner_id": str(users["Parth Fulvani"].id)},
    ).json()
    assert body == []


def test_feedback_rating_bands_filter_on_the_server(client, users) -> None:
    """The three bands partition the rated responses, with no overlap.

    "Who is unhappy" is the question a department head actually asks, so the
    bands are cut at the configured alert threshold rather than at an
    arbitrary score.
    """
    headers = sign_in(client, users["Shail Patel"])
    threshold = client.get("/api/feedback/analysis", headers=headers).json()["threshold"]

    bands = {
        band: client.get(
            "/api/feedback", headers=headers, params={"rating": band, "page_size": 200}
        ).json()
        for band in ("LOW", "MID", "HIGH")
    }
    for band, body in bands.items():
        for row in body["items"]:
            rating = row["overall_rating"]
            assert rating is not None, f"{band} returned an unrated response"
            if band == "LOW":
                assert rating < threshold
            elif band == "MID":
                assert threshold <= rating < threshold + 1
            else:
                assert rating >= threshold + 1

    # Together they account for every rated response, and count each once.
    everything = client.get(
        "/api/feedback", headers=headers, params={"page_size": 200}
    ).json()
    rated = [row for row in everything["items"] if row["overall_rating"] is not None]
    assert sum(body["total"] for body in bands.values()) == len(rated)


def test_an_unknown_rating_band_is_refused_not_ignored(client, users) -> None:
    """A filter the server does not understand must fail loudly. Ignoring it
    returns the unfiltered list, which reads as "everyone is happy"."""
    response = client.get(
        "/api/feedback",
        headers=sign_in(client, users["Shail Patel"]),
        params={"rating": "TERRIBLE"},
    )
    assert response.status_code == 422


def test_the_assistant_is_not_a_second_door_into_the_customer_book(
    client, users
) -> None:
    """S27/S42: what the UI closed, the assistant must not reopen.

    Browsing the SAP archive became Super Admin only. A BDE who cannot open
    /customers must not be able to ask for the same rows in a sentence -
    otherwise the restriction is decoration.
    """
    from app.core import authority
    from app.db.session import SessionLocal
    from app.services.chat import registry, tools  # noqa: F401 - registers them

    tool_names = ("list_customers", "get_customer", "get_customer_timeline")

    for name, allowed in (
        ("Parth Fulvani", False),
        ("Navya Rupawat", False),
        ("Shail Patel", False),      # plain Admin: also not the archive's owner
        ("Portal Owner", True),
    ):
        user = users[name]
        with SessionLocal() as session:
            actor = session.get(type(user), user.id)
            ctx = registry.ToolContext(
                db=session,
                actor=actor,
                scope=authority.visible_user_ids(session, actor),
                department_scope=authority.feedback_department_scope(actor),
            )
            for tool in tool_names:
                outcome = registry.dispatch(ctx, tool, {})
                if allowed:
                    # It may fail for a missing argument; it must not be refused.
                    assert outcome.get("error", {}).get("code") != "NOT_PERMITTED", (
                        f"{name} was refused {tool}"
                    )
                else:
                    assert not outcome["ok"], f"{name} ran {tool}"
                    assert outcome["error"]["code"] == "NOT_PERMITTED", (
                        f"{name} got {outcome} from {tool}"
                    )

            # And the tool is not even offered to them, so the model does not
            # propose something that can only ever be refused.
            offered = {spec["name"] for spec in registry.schemas_for(actor)}
            for tool in tool_names:
                assert (tool in offered) is allowed, f"{name}: {tool} offered={tool in offered}"


# ================================================= demo audit: fixes pinned


def _record(client, headers, lead_id, outcome, **extra):
    body = {"lead_id": lead_id, "outcome": outcome, **extra}
    return client.post("/api/references", headers=headers, json=body)


def test_a_given_reference_cannot_be_dragged_back_to_pending(client, users) -> None:
    """Found by the demo audit, over the API: completed stays completed.

    The roll-up is last-write-wins, so recording "not right now" after "gave
    a reference" put the account back in the follow-up queue AND removed it
    from references received. The UI never offered that action - which is
    exactly why only a direct API call exposed it.
    """
    navya, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])

    assert _record(client, parth, lead_id, "YES", referred_name="Meera").status_code == 201
    taken = client.get("/api/references/stats", headers=parth).json()["references_taken"]

    regress = _record(client, parth, lead_id, "NO", next_reference_date=date.today().isoformat())
    assert regress.status_code == 409, regress.text
    downgrade = _record(client, parth, lead_id, "NOT_SHARED")
    assert downgrade.status_code == 409, downgrade.text

    after = client.get("/api/references/stats", headers=parth).json()
    assert after["references_taken"] == taken, "a received reference was erased"
    due = client.get("/api/references/follow-ups", headers=parth).json()
    assert not any(row["subject_id"] == lead_id for row in due)


def test_another_referral_can_still_be_added_to_a_taken_account(client, users) -> None:
    """The "+" on a completed row: one customer naming a second person."""
    _, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])

    assert _record(client, parth, lead_id, "YES", referred_name="First").status_code == 201
    second = _record(client, parth, lead_id, "YES", referred_name="Second")
    assert second.status_code == 201, second.text

    account = next(
        row for row in client.get("/api/references/accounts", headers=parth).json()
        if row["subject_id"] == lead_id
    )
    assert account["reference_status"] == "TAKEN"


def test_not_shared_is_final(client, users) -> None:
    """"Nobody to refer" ends the conversation - no "+", no ask again, and the
    API agrees with the buttons rather than trusting them."""
    _, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])

    assert _record(client, parth, lead_id, "NOT_SHARED").status_code == 201
    for outcome, extra in (
        ("YES", {"referred_name": "Late"}),
        ("NO", {"next_reference_date": date.today().isoformat()}),
        ("NOT_SHARED", {}),
    ):
        response = _record(client, parth, lead_id, outcome, **extra)
        assert response.status_code == 409, f"{outcome}: {response.text}"


def test_team_references_count_leads_not_the_sap_archive(client, users, db) -> None:
    """Found by the demo audit: every reference on the demo Team table came
    from archived SAP customers, while the Reference module - which counts
    leads - said nobody had taken one."""
    from app.models.customer import Customer
    from app.models.reference import CustomerReference

    parth = users["Parth Fulvani"]
    admin = sign_in(client, users["Shail Patel"])

    def team_references() -> int:
        rows = client.get("/api/dashboard", headers=admin).json()["reports"]
        return next(r for r in rows if r["user_id"] == str(parth.id))["references_taken"]

    before = team_references()

    customer = db.execute(select(Customer)).scalars().first()
    db.add(CustomerReference(
        customer_id=customer.id, requested_by_user_id=parth.id,
        outcome="YES", asked_on=date.today(), referred_name="Archive Referral",
    ))
    db.flush()
    assert team_references() == before, "an archived SAP ask moved the Team table"

    _, lead_id = _an_account(client, users)
    assert _record(client, sign_in(client, parth), lead_id, "YES",
                   referred_name="Real Referral").status_code == 201
    assert team_references() == before + 1, "a real lead referral did not count"


def test_the_assistant_team_rows_carry_no_sap_fields(client, users, db) -> None:
    """The Team payload lost Customers and Last invoice; the assistant's copy
    of it kept them, and told the model everyone had 0 customers."""
    from app.core import authority
    from app.services.chat import registry, tools  # noqa: F401 - registers them

    actor = users["Navya Rupawat"]
    ctx = registry.ToolContext(
        db=db, actor=actor,
        scope=authority.visible_user_ids(db, actor),
        department_scope=authority.feedback_department_scope(actor),
    )
    outcome = registry.dispatch(ctx, "get_team_workload", {})
    assert outcome["ok"], outcome
    for row in outcome["result"]["items"]:
        assert "customers" not in row and "last_invoice_date" not in row, row
        assert {"open_leads", "converted", "references_taken", "last_activity_at"} <= set(row)


@pytest.mark.parametrize(
    "name", ["Portal Owner", "Shail Patel", "Navya Rupawat", "Parth Fulvani"]
)
def test_every_suggested_prompt_is_one_the_role_can_run(users, name) -> None:
    """Found by the demo audit: "Show my customers" was offered to a BDE, and
    the customer tools became Super Admin only - so the chip could only ever
    come back as a refusal, which reads as a broken assistant.

    `suggested_prompts` promises to offer only what the role can answer. A
    "customers" topic routes to the archive tools, so it may only appear for
    the one role those tools serve.
    """
    from app.core.constants import Role
    from app.services.chat.prompt import suggested_prompts

    actor = users[name]
    topics = {item["topic"] for item in suggested_prompts(actor)}
    if actor.role != Role.SUPER_ADMIN:
        assert "customers" not in topics, f"{name} is offered a customer prompt"


def test_a_repeat_invoice_does_not_revoke_eligibility(client, users) -> None:
    """Found by the demo audit, and the kind of thing real Excel data does daily.

    A customer who orders again is invoiced again. Each module used to pick
    a lead's invoice row for itself from an unordered query, so a second row
    dated today could make an account that had ALREADY given a reference
    ineligible again - it vanished from Reference Tracking, and the ask guard
    and the counts disagreed about the same lead.
    """
    from tests.conftest import sync_post_sale

    navya, lead_id = _an_account(client, users)          # invoiced 30 days ago
    parth = sign_in(client, users["Parth Fulvani"])
    assert _record(client, parth, lead_id, "YES", referred_name="Kept").status_code == 201
    before = client.get("/api/references/stats", headers=parth).json()

    lead = client.get(f"/api/leads/{lead_id}", headers=navya).json()
    admin = sign_in(client, users["Shail Patel"])
    repeat = client.post("/api/post-sale/sync", headers=admin, json={"rows": [{
        "external_ref": f"repeat-{lead_id}", "mobile": lead["mobile"],
        "invoice_date": date.today().isoformat(),
    }]}).json()
    assert repeat["matched"] == 1, repeat

    after = client.get("/api/references/stats", headers=parth).json()
    assert after["eligible_accounts"] == before["eligible_accounts"], (before, after)
    assert after["references_taken"] == before["references_taken"]
    listed = client.get("/api/references/accounts", headers=parth).json()
    assert any(row["subject_id"] == lead_id for row in listed), "account dropped out"

    # The guard agrees with the queue: another referral is still accepted.
    again = _record(client, parth, lead_id, "YES", referred_name="Second")
    assert again.status_code == 201, again.text


def test_the_invoice_date_is_chosen_deterministically(client, users, db) -> None:
    """Two matched rows, any insertion order: always the earliest."""
    from app.services import metrics

    _, lead_id = _an_account(client, users)
    import uuid as _uuid
    lid = _uuid.UUID(lead_id)
    admin = sign_in(client, users["Shail Patel"])
    lead = client.get(f"/api/leads/{lead_id}", headers=admin).json()
    client.post("/api/post-sale/sync", headers=admin, json={"rows": [{
        "external_ref": f"later-{lead_id}", "mobile": lead["mobile"],
        "invoice_date": date.today().isoformat(),
    }]})
    chosen = metrics.eligibility_invoice_dates(db, [lid])[lid]
    assert chosen <= date.today() - timedelta(days=10), chosen


# ============================================ B1: composing is not sending


def _owed(client, headers, lead_id):
    return next(
        (row for row in client.get("/api/feedback/pending", headers=headers).json()
         if row["id"] == lead_id),
        None,
    )


def test_composing_issues_a_code_but_does_not_count_as_asked(client, users, db) -> None:
    """Found by the demo audit: opening the dialog recorded the ask as SENT.

    The code must exist when the message is drafted - it is part of the link -
    but the dialog can be closed, and the send button is disabled without a
    form link. None of that is an ask the customer received.
    """
    from app.models.feedback import FeedbackRequest

    _, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])

    composed = client.get(f"/api/leads/{lead_id}/message", headers=parth,
                          params={"channel": "WHATSAPP"})
    assert composed.status_code == 200, composed.text

    request = db.execute(
        select(FeedbackRequest).where(FeedbackRequest.lead_id == uuid.UUID(lead_id))
    ).scalars().one()
    assert request.status == "ISSUED"
    assert _owed(client, parth, lead_id)["state"] == "NOT_ASKED"

    # Composing again reuses the same code rather than issuing a second.
    client.get(f"/api/leads/{lead_id}/message", headers=parth, params={"channel": "EMAIL"})
    assert db.execute(
        select(FeedbackRequest).where(FeedbackRequest.lead_id == uuid.UUID(lead_id))
    ).scalars().all() == [request]


def test_only_confirming_the_send_makes_it_awaiting(client, users, db) -> None:
    from app.models.feedback import FeedbackRequest

    _, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])
    client.get(f"/api/leads/{lead_id}/message", headers=parth, params={"channel": "EMAIL"})

    sent = client.post(f"/api/leads/{lead_id}/message/sent", headers=parth,
                       json={"purpose": "FEEDBACK", "channel": "EMAIL"})
    assert sent.status_code == 200, sent.text

    request = db.execute(
        select(FeedbackRequest).where(FeedbackRequest.lead_id == uuid.UUID(lead_id))
    ).scalars().one()
    db.refresh(request)
    assert request.status == "SENT"
    row = _owed(client, parth, lead_id)
    assert row["state"] == "AWAITING" and row["request_reference"] == request.reference


def test_only_the_assignee_can_ask_for_feedback(client, users) -> None:
    """The ask is recorded on the assignee's own log. A manager composing
    issued a code the real send then refused - leaving a phantom ask."""
    navya, lead_id = _an_account(client, users)
    admin = sign_in(client, users["Shail Patel"])

    for headers in (navya, admin):
        composed = client.get(f"/api/leads/{lead_id}/message", headers=headers,
                              params={"channel": "EMAIL"})
        assert composed.status_code == 403, composed.text
        sent = client.post(f"/api/leads/{lead_id}/message/sent", headers=headers,
                           json={"purpose": "FEEDBACK", "channel": "EMAIL"})
        assert sent.status_code == 403, sent.text

    assert _owed(client, navya, lead_id)["state"] == "NOT_ASKED"


def test_an_issued_code_still_matches_a_response(client, users, db) -> None:
    """ISSUED means "not confirmed sent", not "void". If the customer got the
    text some other way and answers, the answer still lands on the ask."""
    from app.models.feedback import FeedbackRequest
    from app.services import feedback_requests

    _, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])
    client.get(f"/api/leads/{lead_id}/message", headers=parth, params={"channel": "EMAIL"})
    request = db.execute(
        select(FeedbackRequest).where(FeedbackRequest.lead_id == uuid.UUID(lead_id))
    ).scalars().one()

    matched, how = feedback_requests.match_response(db, code=request.code, mobile=None, email=None)
    assert matched is not None and matched.id == request.id and how == "MATCHED_TOKEN"


# ======================================= B3: responses match the portal's leads


def _asked_lead(client, users, db):
    """An eligible lead whose assignee composed and sent the ask."""
    from app.models.feedback import FeedbackRequest

    navya, lead_id = _an_account(client, users)
    parth = sign_in(client, users["Parth Fulvani"])
    client.get(f"/api/leads/{lead_id}/message", headers=parth, params={"channel": "EMAIL"})
    client.post(f"/api/leads/{lead_id}/message/sent", headers=parth,
                json={"purpose": "FEEDBACK", "channel": "EMAIL"})
    lead = client.get(f"/api/leads/{lead_id}", headers=navya).json()
    request = db.execute(
        select(FeedbackRequest).where(FeedbackRequest.lead_id == uuid.UUID(lead_id))
    ).scalars().one()
    return lead, request


def test_an_uncoded_response_matches_the_lead_that_was_asked(client, users, db) -> None:
    """Found by the demo audit: without a code, a response could only ever
    attach to an archived SAP customer - never to the lead the portal asked."""
    from app.services import feedback_requests

    lead, request = _asked_lead(client, users, db)
    matched, how = feedback_requests.match_response(
        db, code="", mobile=f"+91 {lead['mobile']}", email=None
    )
    assert matched is not None and matched.id == request.id, "did not find the asked lead"
    assert how == "MATCHED_CONTACT"


def test_contact_matching_never_picks_one_of_two_leads(client, users, db) -> None:
    """Two asked leads sharing a phone is exactly where a guess does harm."""
    from app.models.lead import Lead
    from app.services import feedback_requests

    lead, request = _asked_lead(client, users, db)
    twin = db.get(Lead, uuid.UUID(lead["id"]))
    other = Lead(
        name="Same Phone Co", mobile=twin.mobile, status="CONVERTED",
        assigned_to_user_id=twin.assigned_to_user_id,
    )
    db.add(other)
    db.flush()
    from app.models.feedback import FeedbackRequest
    db.add(FeedbackRequest(
        reference="FB-2099-99991", token="twin-token-for-ambiguity-test",
        subject_type="LEAD", lead_id=other.id, owner_user_id=twin.assigned_to_user_id,
        status="SENT",
    ))
    db.flush()

    matched, _ = feedback_requests.match_response(db, code="", mobile=twin.mobile, email=None)
    assert matched is None, "an ambiguous phone number was attached to one of two leads"


def test_a_response_to_a_leads_ask_is_not_also_filed_under_a_sap_customer(
    client, users, db
) -> None:
    """The webhook used to pass customer_id=None for a lead's ask, and None
    meant "go and look for a customer" - so contact details that happened to
    match an archived account filed the same response twice."""
    from app.models.customer import Customer
    from app.models.feedback import Feedback
    from app.services import feedback_ingest

    lead, request = _asked_lead(client, users, db)
    archive = db.execute(select(Customer)).scalars().first()
    parsed = {
        "submitted_at_source": None, "customer_name": "x", "company_name": archive.name,
        "mobile": archive.mobile, "email": archive.email, "handled_by_name": None,
        "overall_rating": "4", "overall_rating_raw": "4", "overall_comments": None,
        "would_recommend": None, "department_ratings": {},
    }
    from app.services.feedback_mapping import ColumnResolution
    result = feedback_ingest.create_feedback(
        db, parsed, ColumnResolution({}, {}, {}, []), {},
        source="GOOGLE_FORMS_WEBHOOK", feedback_request_id=request.id,
        match_status="MATCHED_TOKEN", customer_id=None, link_customer=False,
    )
    stored = db.get(Feedback, result.feedback.id)
    assert stored.feedback_request_id == request.id
    assert stored.customer_id is None


def test_the_file_importer_completes_a_leads_ask(client, users, db) -> None:
    """The importer is the intake that works without the webhook - and it
    never read the code column, so an exported answer never completed an ask."""
    from app.services import feedback_import

    lead, request = _asked_lead(client, users, db)
    admin_user = users["Shail Patel"]
    # Quote the header that contains a comma, as a real export does.
    csv_bytes = (
        'Timestamp,Reference code,Your name,Mobile number,"Overall, how satisfied are you with us?"\n'
        f'2026-09-13 10:00:00,{request.code},{lead["name"]},{lead["mobile"]},5\n'
    ).encode()

    feedback_import.commit(db, csv_bytes, "export.csv", actor=admin_user, scale_max=5)
    db.flush()
    db.refresh(request)
    assert request.status == "COMPLETED", request.status
    parth = sign_in(client, users["Parth Fulvani"])
    assert _owed(client, parth, lead["id"]) is None, "answered lead still owed feedback"


def test_an_unmatched_response_can_be_filed_under_a_leads_ask(client, users, db) -> None:
    from app.models.feedback import Feedback

    lead, request = _asked_lead(client, users, db)
    stray = Feedback(source="GOOGLE_FORMS_WEBHOOK", match_status="UNMATCHED",
                     customer_name="Typed Their Own Name", overall_rating=3)
    db.add(stray)
    db.flush()

    admin = sign_in(client, users["Shail Patel"])
    open_asks = client.get("/api/feedback/sync/open-requests", headers=admin).json()
    assert any(row["id"] == str(request.id) for row in open_asks)

    resolved = client.post(f"/api/feedback/sync/resolve/{stray.id}", headers=admin,
                           json={"request_id": str(request.id)})
    assert resolved.status_code == 200, resolved.text
    db.refresh(request)
    assert request.status == "COMPLETED"

    both = client.post(f"/api/feedback/sync/resolve/{stray.id}", headers=admin,
                       json={"request_id": str(request.id), "customer_id": str(uuid.uuid4())})
    assert both.status_code == 422, "a response filed under both would count twice"
