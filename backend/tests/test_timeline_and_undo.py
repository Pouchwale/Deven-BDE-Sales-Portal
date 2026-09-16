"""The customer timeline, feedback requests, and undo.

A customer that came from SAP is a COMPLETED lead — a won deal. It never
enters the lead pipeline, and its open business is feedback and references.
These tests hold that line.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.models.customer import Customer, CustomerActivity
from app.models.feedback import Feedback
from app.models.lead import Lead
from app.services.customer_timeline import match_customer, normalise_mobile
from tests.conftest import auth, eligible_lead, sign_in, super_admin_headers, token_for
from tests.test_feedback import build_csv, response_row, upload


def customer_of(client, search: str) -> dict:
    """Find one SAP account by name.

    Looked up as the Super Admin regardless of who the test is acting as:
    browsing the customer book is Super Admin only, and WHICH customer these
    tests use is scaffolding, not the thing under test. The call being tested
    still runs as whoever the test signed in.
    """
    return client.get(
        "/api/customers", params={"search": search}, headers=super_admin_headers(client)
    ).json()["items"][0]


# ------------------------------------------ SAP customers are completed
def test_sap_customers_are_completed_not_pending_leads(client, db, users) -> None:
    """The 17 SAP accounts are won deals. They must never appear as leads."""
    assert db.scalar(select(func.count(Customer.id))) == 17
    assert db.scalar(select(func.count(Lead.id))) == 0

    admin = sign_in(client, users["Shail Patel"])
    assert client.get("/api/leads/stats", headers=admin).json()["total"] == 0
    assert client.get("/api/leads/stats", headers=admin).json()["new"] == 0

    for customer in db.execute(select(Customer)).scalars():
        assert customer.is_converted is True


def test_the_sap_values_are_never_rewritten(client, db, users) -> None:
    """Nothing in the portal edits what SAP supplied.

    One exception, and it is deliberate: the mobile is stored canonically.
    SAP writes the same number three ways (`+91 97111 22505`, `091-...`,
    bare), and storing them verbatim meant two records of one person did not
    compare equal. The digits are identical either way - see
    `app/core/validators.py`, which normalises but never invents.
    """
    guiltfree = db.execute(
        select(Customer).where(Customer.sap_code == "C1730")
    ).scalar_one()
    assert guiltfree.name == "GUILTFREE INDUSTRIES LIMITED"
    assert guiltfree.mobile == "9711122505", "the same number, canonically"

    # Logging activity against it does not touch the SAP fields.
    parth = sign_in(client, users["Shail Patel"])
    client.post(
        f"/api/customers/{guiltfree.id}/timeline",
        headers=parth,
        json={"activity_type": "CALL", "remark": "Spoke to Ronak."},
    )
    db.refresh(guiltfree)
    assert guiltfree.name == "GUILTFREE INDUSTRIES LIMITED"
    assert guiltfree.mobile == "9711122505"


# ------------------------------------------------------- the timeline
def test_a_completed_customer_has_a_timeline(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = customer_of(client, "SPLICECONN")

    assert client.get(
        f"/api/customers/{customer['id']}/timeline", headers=headers
    ).json() == []

    response = client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=headers,
        json={"activity_type": "FEEDBACK_REQUESTED", "remark": "Sent the form."},
    )
    assert response.status_code == 200, response.text
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["title"] == "Feedback requested"
    assert entries[0]["remark"] == "Sent the form."
    assert entries[0]["actor_name"] == "Parth Fulvani"
    assert entries[0]["can_undo"] is True


def test_the_timeline_merges_references_and_feedback(client, users) -> None:
    """Three sources, one history — and no duplicate storage."""
    headers = sign_in(client, users["Parth Fulvani"])
    customer = customer_of(client, "SPLICECONN")

    client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=headers,
        json={"activity_type": "CALL", "remark": "Called about feedback."},
    )
    client.post(
        "/api/references",
        headers=headers,
        json={
            "customer_id": customer["id"],
            "outcome": "YES",
            "referred_name": "Rakesh Mehta",
        },
    )

    entries = client.get(
        f"/api/customers/{customer['id']}/timeline", headers=headers
    ).json()
    kinds = {entry["kind"] for entry in entries}
    assert kinds == {"ACTIVITY", "REFERENCE"}
    # A reference is an answer the customer gave; it is corrected by asking
    # again, not by being rubbed out.
    reference = next(e for e in entries if e["kind"] == "REFERENCE")
    assert reference["can_undo"] is False


def test_only_manual_types_can_be_logged(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = customer_of(client, "SPLICECONN")

    response = client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=headers,
        json={"activity_type": "FEEDBACK_RECEIVED", "remark": "faked"},
    )
    assert response.status_code == 422


def test_the_timeline_is_scoped(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    parag_customer = customer_of(client, "MADHURAJ")

    navya = sign_in(client, users["Navya Rupawat"])
    assert (
        client.get(
            f"/api/customers/{parag_customer['id']}/timeline", headers=navya
        ).status_code
        == 404
    )


# ------------------------------------------------------------ undo
def test_undoing_a_timeline_entry_marks_it_rather_than_deleting_it(
    client, db, users
) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = customer_of(client, "SPLICECONN")

    entries = client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=headers,
        json={"activity_type": "CALL", "remark": "Wrong customer."},
    ).json()
    entry_id = entries[0]["id"]

    after = client.post(
        f"/api/customers/{customer['id']}/timeline/{entry_id}/undo", headers=headers
    ).json()

    # Still there, and marked — "this happened and was then taken back".
    assert len(after) == 1
    assert after[0]["undone_at"] is not None
    assert after[0]["can_undo"] is False
    assert db.scalar(select(func.count(CustomerActivity.id))) == 1


def test_an_entry_cannot_be_undone_twice(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = customer_of(client, "SPLICECONN")
    entries = client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=headers,
        json={"activity_type": "NOTE", "remark": "oops"},
    ).json()
    entry_id = entries[0]["id"]

    client.post(f"/api/customers/{customer['id']}/timeline/{entry_id}/undo", headers=headers)
    second = client.post(
        f"/api/customers/{customer['id']}/timeline/{entry_id}/undo", headers=headers
    )
    assert second.status_code == 409


def test_you_cannot_undo_someone_elses_entry(client, users) -> None:
    """Undo is a correction, not a way to rewrite a colleague's record."""
    admin = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "SPLICECONN")
    entries = client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=admin,
        json={"activity_type": "NOTE", "remark": "the admin's note"},
    ).json()

    parth = sign_in(client, users["Parth Fulvani"])
    response = client.post(
        f"/api/customers/{customer['id']}/timeline/{entries[0]['id']}/undo",
        headers=parth,
    )
    assert response.status_code == 403


def test_a_manager_can_undo_their_own_people(client, users) -> None:
    parth = sign_in(client, users["Parth Fulvani"])
    customer = customer_of(client, "SPLICECONN")
    entries = client.post(
        f"/api/customers/{customer['id']}/timeline",
        headers=parth,
        json={"activity_type": "NOTE", "remark": "mis-logged"},
    ).json()

    navya = sign_in(client, users["Navya Rupawat"])
    response = client.post(
        f"/api/customers/{customer['id']}/timeline/{entries[0]['id']}/undo",
        headers=navya,
    )
    assert response.status_code == 200


# ------------------------------------------------------- lead undo
def make_lead(client, headers, assignee_id) -> dict:
    return client.post(
        "/api/leads",
        headers=headers,
        json={"name": "Undo Me", "assigned_to_user_id": str(assignee_id)},
    ).json()


def test_lead_undo_is_gone_entirely(client, users) -> None:
    """Four tests used to live here, covering undo on a lead: moving a stage
    back, only the latest one being reversible, assignments being exempt, and
    a logged call not disturbing the stage.

    The feature was removed, so they are replaced by the one thing still worth
    asserting - that it is really gone, at the route and not just in the UI.
    A mis-set stage is now corrected by moving the lead on through the state
    machine, which leaves both moves on the timeline because both happened.

    The CUSTOMER timeline keeps its undo. That is a different surface, and the
    tests for it are below.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    client.post(
        f"/api/leads/{lead['id']}/status",
        headers=navya,
        json={"status": "CONTACTED", "remark": "Reached them."},
    )

    detail = client.get(f"/api/leads/{lead['id']}", headers=navya).json()
    stage_change = next(
        entry for entry in detail["activities"] if entry["activity_type"] == "STATUS_CHANGED"
    )
    assert stage_change["can_undo"] is False

    gone = client.post(
        f"/api/leads/{lead['id']}/activities/{stage_change['id']}/undo", headers=navya
    )
    assert gone.status_code == 404, gone.text

    # And the stage did not move.
    after = client.get(f"/api/leads/{lead['id']}", headers=navya).json()
    assert after["status"] == "CONTACTED"


# ----------------------------------------- feedback linked to customers
def test_mobile_matching_ignores_formatting() -> None:
    assert normalise_mobile("+91 97111 22505") == normalise_mobile("9711122505")
    assert normalise_mobile("") == ""


def test_imported_feedback_links_to_the_sap_customer(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    gulabs = db.execute(select(Customer).where(Customer.sap_code == "C2080")).scalar_one()

    row = response_row(name="Gulabs Contact", sales="4", production="4")
    row[3] = gulabs.mobile or ""          # Mobile Number column
    row[4] = gulabs.email or ""           # Email Address column
    upload(client, admin, build_csv([row]), "/api/feedback/import/commit")

    feedback = db.execute(select(Feedback)).scalar_one()
    assert feedback.customer_id == gulabs.id

    # ...and it shows on the customer, not just in the feedback module.
    detail = client.get(
        f"/api/customers/{gulabs.id}", headers=super_admin_headers(client)
    ).json()
    assert detail["feedback_count"] == 1
    assert detail["feedback_average"] == 4.0
    assert len(detail["feedback"]) == 1
    assert detail["feedback"][0]["overall_rating"] == 4.0

    # ...and it appears on the customer's timeline.
    timeline = client.get(f"/api/customers/{gulabs.id}/timeline", headers=admin).json()
    assert any(entry["kind"] == "FEEDBACK" for entry in timeline)


def test_unmatched_feedback_is_left_unlinked_rather_than_guessed(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    upload(
        client,
        admin,
        build_csv([response_row(name="Nobody We Know", sales="4", production="4")]),
        "/api/feedback/import/commit",
    )
    feedback = db.execute(select(Feedback)).scalar_one()
    assert feedback.customer_id is None


def test_customer_stats_report_who_has_been_heard_from(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    # The customer counters are part of the book, so they are read as the
    # Super Admin; the import below still runs as the admin.
    book = super_admin_headers(client)
    before = client.get("/api/customers/stats", headers=book).json()
    assert before["with_feedback"] == 0
    assert before["awaiting_feedback"] == 17

    gulabs = db.execute(select(Customer).where(Customer.sap_code == "C2080")).scalar_one()
    row = response_row(name="Gulabs Contact", sales="4", production="4")
    row[3] = gulabs.mobile or ""
    upload(client, admin, build_csv([row]), "/api/feedback/import/commit")

    after = client.get("/api/customers/stats", headers=book).json()
    assert after["with_feedback"] == 1
    assert after["awaiting_feedback"] == 16


def test_the_people_table_is_about_leads_not_invoices(client, users) -> None:
    """§23: the per-person feedback rating is gone, and so is everything else
    that came from the SAP book.

    Individual feedback ratings are not how this business measures a person -
    a customer rating "Dispatch" says nothing about the BDE who sold the job -
    so the column was showing a number nobody should act on.
    """
    admin = sign_in(client, users["Shail Patel"])
    body = client.get("/api/dashboard", headers=admin).json()
    parth = next(row for row in body["reports"] if row["name"] == "Parth Fulvani")

    for gone in ("feedback_responses", "feedback_average", "customers",
                 "invoices", "last_invoice_date"):
        assert gone not in parth, gone
    # What replaced them is the person's actual pipeline.
    assert parth["open_leads"] >= 0 and parth["converted"] >= 0


def test_backfill_links_older_feedback(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    gulabs = db.execute(select(Customer).where(Customer.sap_code == "C2080")).scalar_one()

    row = response_row(name="Gulabs Contact", sales="4", production="4")
    row[3] = gulabs.mobile or ""
    upload(client, admin, build_csv([row]), "/api/feedback/import/commit")

    # Simulate a response imported before matching existed.
    feedback = db.execute(select(Feedback)).scalar_one()
    feedback.customer_id = None
    db.flush()

    response = client.post("/api/customers/backfill-feedback-links", headers=admin)
    assert response.status_code == 200
    assert "1" in response.json()["message"]

    db.refresh(feedback)
    assert feedback.customer_id == gulabs.id


def test_a_reference_follow_up_is_still_not_a_lead(client, users) -> None:
    """The two kinds of work stay apart even with a timeline in play."""
    headers = sign_in(client, users["Parth Fulvani"])
    # An askable account is a WON lead the post-sale sheet has invoiced. It is
    # therefore also a lead row - which is exactly why the two buckets have to
    # be counted separately rather than assumed disjoint.
    customer = eligible_lead(client, users, name="Follow Up Not Lead")
    client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "NO",
            "next_reference_date": (date.today() - timedelta(days=1)).isoformat(),
        },
    )

    queue = client.get("/api/work-queue", headers=headers).json()
    assert queue["follow_up_total"] == 1
    # The won lead is closed, so it is not OPEN work in the assigned bucket.
    assert queue["assigned_total"] == 0
    assert len(queue["assigned_leads"]) == 0
