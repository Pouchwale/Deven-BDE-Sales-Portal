"""Module 1 — reference tracking."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.core.constants import ReferenceStatus
from app.models.customer import Customer
from app.models.lead import Lead
from tests.conftest import advance_lead, auth, eligible_lead, sign_in, super_admin_headers, token_for


def customer_of(client, search: str) -> dict:
    """Find one SAP account by name.

    Still used by the few tests that are ABOUT the old customer book. The
    reference subject is no longer one of these - see `subject_lead` below.
    """
    response = client.get(
        "/api/customers", params={"search": search}, headers=super_admin_headers(client)
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, f"no customer matching {search}"
    return items[0]


def error_code(response) -> str:
    return response.json()["error"]["code"]


# ------------------------------------------------------------- recording
def test_a_yes_marks_the_customer_taken(client, users, db) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)

    response = client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "YES",
            "referred_name": "Rakesh Mehta",
            "referred_company": "Mehta Snacks",
            "referred_mobile": "9800000001",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "YES"
    assert response.json()["requested_by_name"] == "Parth Fulvani"

    row = db.execute(
        select(Lead).where(Lead.id == customer["id"])
    ).scalar_one()
    assert row.reference_status == ReferenceStatus.TAKEN
    assert row.last_reference_asked_at == date.today()
    # A taken reference clears the "ask again" date.
    assert row.next_reference_date is None


def test_a_no_needs_a_follow_up_date(client, users) -> None:
    """"Ask me later" without a date is a lost thread, not a follow-up."""
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)

    response = client.post(
        "/api/references",
        headers=headers,
        json={"lead_id": customer["id"], "outcome": "NO"},
    )
    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"


def test_a_yes_needs_some_detail_about_the_referral(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)

    response = client.post(
        "/api/references",
        headers=headers,
        json={"lead_id": customer["id"], "outcome": "YES"},
    )
    assert response.status_code == 422


def test_a_no_moves_the_customer_to_pending(client, users, db) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)
    later = (date.today() + timedelta(days=30)).isoformat()

    response = client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "NO",
            "next_reference_date": later,
        },
    )
    assert response.status_code == 201, response.text

    row = db.execute(select(Lead).where(Lead.id == customer["id"])).scalar_one()
    assert row.reference_status == ReferenceStatus.PENDING
    assert row.next_reference_date == date.today() + timedelta(days=30)


def test_cannot_record_against_a_customer_outside_your_scope(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    parag_customer = subject_lead(client, users)

    navya = sign_in(client, users["Navya Rupawat"])
    response = client.post(
        "/api/references",
        headers=navya,
        json={
            "customer_id": parag_customer["id"],
            "outcome": "YES",
            "referred_name": "Someone",
        },
    )
    # 404, not 403 — ids must not be probeable across chains.
    assert response.status_code == 404


def test_an_ask_cannot_be_backdated_into_the_future(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)

    response = client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "YES",
            "referred_name": "Future Person",
            "asked_on": (date.today() + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------- attribution
def test_a_manager_can_credit_someone_in_their_chain(client, users) -> None:
    headers = sign_in(client, users["Navya Rupawat"])
    customer = subject_lead(client, users)

    response = client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "YES",
            "referred_name": "Credited Referral",
            "requested_by_user_id": str(users["Parth Fulvani"].id),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["requested_by_name"] == "Parth Fulvani"


def test_cannot_credit_a_reference_outside_your_chain(client, users) -> None:
    headers = sign_in(client, users["Navya Rupawat"])
    customer = subject_lead(client, users)

    response = client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "YES",
            "referred_name": "Wrong Credit",
            "requested_by_user_id": str(users["Parag Sharma"].id),
        },
    )
    assert response.status_code == 403


# --------------------------------------------------------------- scoping
def test_reference_list_is_scoped_to_the_chain(client, users) -> None:
    parth = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)
    client.post(
        "/api/references",
        headers=parth,
        json={
            "lead_id": customer["id"],
            "outcome": "YES",
            "referred_name": "Parth's Referral",
        },
    )

    # Navya is above Parth, so it is in her scope.
    navya = sign_in(client, users["Navya Rupawat"])
    assert client.get("/api/references", headers=navya).json()["total"] == 1

    # Ramanesh runs a different chain entirely.
    ramanesh = sign_in(client, users["Ramanesh Nair"])
    assert client.get("/api/references", headers=ramanesh).json()["total"] == 0


def test_stats_reflect_what_was_recorded(client, users) -> None:
    """The KPIs, over one population.

    Parth starts with nothing askable: he has won leads, but nothing has been
    synced for them, so `awaiting_sync` carries them and `eligible_accounts`
    is honestly zero. That distinction is the whole point - a bare 0 cannot
    tell "no customers" apart from "nothing synced yet".
    """
    headers = sign_in(client, users["Parth Fulvani"])
    before = client.get("/api/references/stats", headers=headers).json()
    assert before["eligible_accounts"] == 0
    assert before["awaiting_sync"] == before["converted_leads"]
    assert before["requests_completed"] == 0
    assert before["references_taken"] == 0

    # Sync one, and it becomes askable.
    lead = subject_lead(client, users, name="Stats Account")
    after = client.get("/api/references/stats", headers=headers).json()
    assert after["eligible_accounts"] == 1
    assert after["not_asked"] == 1

    client.post(
        "/api/references",
        headers=sign_in(client, users["Navya Rupawat"]),
        json={
            "lead_id": lead["id"],
            "outcome": "YES",
            "referred_name": "Meera Joshi",
            "referred_mobile": "9900011122",
        },
    )
    recorded = client.get("/api/references/stats", headers=headers).json()
    assert recorded["references_taken"] == 1
    assert recorded["requests_completed"] == 1
    assert recorded["reference_rate"] == 100.0
    # The four population numbers always add up.
    assert (
        recorded["eligible_accounts"]
        + recorded["waiting_period"]
        + recorded["awaiting_sync"]
        == recorded["converted_leads"]
    )


def test_follow_ups_show_only_dates_that_have_arrived(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)

    future = (date.today() + timedelta(days=10)).isoformat()
    client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "NO",
            "next_reference_date": future,
        },
    )
    assert client.get("/api/references/follow-ups", headers=headers).json() == []
    # ...but it is visible when you deliberately ask for what is coming.
    upcoming = client.get(
        "/api/references/follow-ups",
        params={"include_future": True},
        headers=headers,
    ).json()
    assert len(upcoming) == 1
    assert upcoming[0]["days_overdue"] == 0


def test_an_overdue_follow_up_reports_how_late_it_is(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)
    past = (date.today() - timedelta(days=4)).isoformat()

    client.post(
        "/api/references",
        headers=headers,
        json={
            "lead_id": customer["id"],
            "outcome": "NO",
            "next_reference_date": past,
        },
    )
    due = client.get("/api/references/follow-ups", headers=headers).json()
    assert len(due) == 1
    assert due[0]["days_overdue"] == 4
    assert due[0]["subject_type"] == "LEAD"
    assert due[0]["subject_id"] == customer["id"]
    assert due[0]["owner_name"] == "Parth Fulvani"


def subject_lead(client, users, name: str = "Ref Subject") -> dict:
    """An account that can actually be asked: won, synced, and past 10 days.

    Reference Tracking runs on converted portal leads whose post-sale record
    says they were invoiced. A SAP customer is no longer a reference subject
    at all - it never had a link to any lead, which is why the two modules
    could never be reconciled.
    """
    import uuid as _uuid

    return eligible_lead(client, users, name=f"{name} {_uuid.uuid4().hex[:6]}")


# ------------------------------------------------- converted leads as accounts
def converted_lead(client, manager_headers, assignee_id, name="Referable Co") -> dict:
    """A lead driven all the way to CONVERTED — a won deal, same as an
    invoiced SAP account, and therefore askable."""
    lead = client.post(
        "/api/leads",
        headers=manager_headers,
        json={"name": name, "mobile": "9800000111", "assigned_to_user_id": str(assignee_id)},
    ).json()
    advance_lead(client, manager_headers, lead["id"])
    return client.get(
        f"/api/leads/{lead['id']}", headers=manager_headers
    ).json()


def test_a_converted_lead_is_not_askable_until_sap_invoices_it(client, users) -> None:
    """Converting is winning the deal, not delivering it.

    A won lead goes to SAP; SAP invoices it; it arrives here as a customer and
    becomes askable ten days later. Until then there is nothing to ask about -
    the customer has not received anything yet.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    before = client.get("/api/references/accounts", headers=navya).json()
    lead = converted_lead(client, navya, users["Parth Fulvani"].id)

    after = client.get("/api/references/accounts", headers=navya).json()
    assert len(after) == len(before), "converting alone adds nothing to ask"
    assert not any(r["subject_id"] == lead["id"] for r in after)


def test_an_unconverted_lead_is_not_askable(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = client.post(
        "/api/leads",
        headers=navya,
        json={"name": "Still A Prospect", "assigned_to_user_id": str(users["Parth Fulvani"].id)},
    ).json()

    accounts = client.get("/api/references/accounts", headers=navya).json()
    assert all(row["subject_id"] != lead["id"] for row in accounts)

    refused = client.post(
        "/api/references",
        headers=navya,
        json={"lead_id": lead["id"], "outcome": "YES", "referred_name": "Nope"},
    )
    assert refused.status_code == 422


def test_a_reference_cannot_be_recorded_against_an_uninvoiced_lead(
    client, users
) -> None:
    """Refused at the API, not merely hidden from the queue.

    Hiding it would leave the rule walkable by posting the lead id directly,
    which is exactly the kind of gap a UI-only restriction leaves behind.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = converted_lead(client, navya, users["Parth Fulvani"].id)

    recorded = client.post(
        "/api/references",
        headers=navya,
        json={
            "lead_id": lead["id"],
            "outcome": "YES",
            "referred_name": "Meera Joshi",
            "referred_company": "Joshi Traders",
            "referred_mobile": "9900011122",
            "referred_email": "meera@joshi.example",
            "notes": "Happy to talk to us.",
        },
    )
    assert recorded.status_code == 422, recorded.text
    assert "synced" in recorded.json()["error"]["message"]

    # And it is not sitting in the askable list either - refused at the API
    # and absent from the queue are the same rule, enforced twice.
    accounts = client.get("/api/references/accounts", headers=navya).json()
    assert not [r for r in accounts if r["subject_id"] == lead["id"]]


def test_an_uninvoiced_lead_never_reaches_the_follow_up_queue(client, users) -> None:
    """The ask is refused, so there is nothing to follow up.

    Before the 10-day rule a converted lead could be asked and then chased.
    Now the ask itself is refused until SAP invoices the account, which is
    what keeps the follow-up queue free of work nobody is allowed to do.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = converted_lead(client, navya, users["Parth Fulvani"].id)
    past = (date.today() - timedelta(days=3)).isoformat()

    refused = client.post(
        "/api/references",
        headers=navya,
        json={"lead_id": lead["id"], "outcome": "NO", "next_reference_date": past},
    )
    assert refused.status_code == 422, refused.text

    due = client.get("/api/references/follow-ups", headers=navya).json()
    assert not [row for row in due if row["subject_id"] == lead["id"]]


def test_an_ask_belongs_to_exactly_one_account(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    customer = subject_lead(client, users)
    lead = converted_lead(client, navya, users["Parth Fulvani"].id)

    both = client.post(
        "/api/references",
        headers=navya,
        json={
            "lead_id": customer["id"],
            "lead_id": lead["id"],
            "outcome": "YES",
            "referred_name": "Two Masters",
        },
    )
    assert both.status_code == 422

    neither = client.post(
        "/api/references",
        headers=navya,
        json={"outcome": "YES", "referred_name": "Nobody"},
    )
    assert neither.status_code == 422


# ---------------------------------------------------------------- declines
def test_repeat_declines_are_counted_on_the_customer(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    customer = subject_lead(client, users)

    for offset in (10, 20):
        client.post(
            "/api/references",
            headers=headers,
            json={
                "lead_id": customer["id"],
                "outcome": "NO",
                "next_reference_date": (date.today() + timedelta(days=offset)).isoformat(),
            },
        )

    updated = next(
        row
        for row in client.get("/api/references/accounts", headers=headers).json()
        if row["subject_id"] == customer["id"]
    )
    assert updated["decline_count"] == 2

    due = client.get(
        "/api/references/follow-ups", params={"include_future": True}, headers=headers
    ).json()
    assert due[0]["decline_count"] == 2


def test_follow_ups_can_be_grouped_by_owner(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    for name, offset in (("SPLICECONN", 5), ("Jagdamba", 1)):
        customer = customer_of(client, name)
        client.post(
            "/api/references",
            headers=headers,
            json={
                "lead_id": customer["id"],
                "outcome": "NO",
                "next_reference_date": (date.today() - timedelta(days=offset)).isoformat(),
            },
        )

    grouped = client.get(
        "/api/references/follow-ups", params={"group_by": "owner"}, headers=headers
    ).json()
    owners = [row["owner_name"] for row in grouped]
    assert owners == sorted(owners)
