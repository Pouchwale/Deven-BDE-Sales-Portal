"""The customer book and the dashboard, both scoped by the reporting chain.

A KPI that ignores scope leaks data past the hierarchy, which is worse than a
wrong number - so every counter here is asserted per role.
"""
from __future__ import annotations

import pytest

from tests.conftest import sign_in, super_admin_headers

# Customers visible to each actor, derived from the real SAP ownership:
#   Navya 2 + Parth 1 + Aastha 2                       = 5
#   Ramanesh: Shailesh 3 + Parag 1 + Sanjeev 1 + Nidhi 1 + Urvish 1 = 7
EXPECTED_VISIBLE_CUSTOMERS = {
    "Portal Owner": 17,
    "Shail Patel": 17,
    "Navya Rupawat": 5,
    "Ramanesh Nair": 7,
    "Shailesh Prajapati": 7,
    "Parth Fulvani": 1,
    "Muskan Makhija": 0,          # owns none
    "Apurva Shah": 4,
    "Bhakti Shah": 1,
}


# Portal Owner is the Super Admin, and is covered by the test below instead.
@pytest.mark.parametrize(
    "name", [n for n in EXPECTED_VISIBLE_CUSTOMERS if n != "Portal Owner"]
)
def test_the_customer_book_is_super_admin_only(client, users, name: str) -> None:
    """Browsing the SAP book is Super Admin's alone.

    Everyone else works their accounts through Reference Tracking and the
    feedback queue, which stay scoped to them - see the test below. This is
    the API, not the nav: hiding the menu item is not a permission.
    """
    response = client.get("/api/customers", headers=sign_in(client, users[name]))
    assert response.status_code == 403, f"{name} reached the customer book"
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_the_super_admin_sees_the_whole_book(client) -> None:
    response = client.get("/api/customers", headers=super_admin_headers(client))
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 17


@pytest.mark.parametrize("name", list(EXPECTED_VISIBLE_CUSTOMERS))
def test_reference_accounts_are_scoped_to_the_reporting_line(
    client, users, name: str
) -> None:
    """Everyone works their own accounts, whoever they are.

    The population is converted LEADS the post-sale sheet has invoiced - not
    the SAP customer book, which had no link to any lead. So this asserts the
    RULE (you only ever see your own chain's accounts) rather than a fixed
    number that depended on the old universe.
    """
    rows = client.get(
        "/api/references/accounts", headers=sign_in(client, users[name])
    ).json()
    assert all(row["subject_type"] == "LEAD" for row in rows), name

    # Everything returned belongs to somebody this person may see.
    visible = client.get(
        "/api/leads?page_size=200", headers=sign_in(client, users[name])
    ).json()
    mine = {row["id"] for row in visible["items"]}
    assert {row["subject_id"] for row in rows} <= mine, name


def test_customer_stats_match_the_list(client) -> None:
    headers = super_admin_headers(client)
    stats = client.get("/api/customers/stats", headers=headers).json()
    assert stats == {
        "total": 17,
        "owned": 17,
        "unowned": 0,
        "invoice_lines": 30,
        "invoices": 23,
        "not_asked": 17,
        "taken": 0,
        "pending": 0,
        "declined": 0,
        # A completed customer's open business is feedback, so this pair is
        # the counterpart to the reference counters above.
        "with_feedback": 0,
        "awaiting_feedback": 17,
    }


def test_customer_detail_carries_its_invoice_lines(client) -> None:
    headers = super_admin_headers(client)
    listing = client.get("/api/customers", params={"search": "HIGH GRADE"}, headers=headers)
    assert listing.json()["total"] == 1
    customer_id = listing.json()["items"][0]["id"]

    detail = client.get(f"/api/customers/{customer_id}", headers=headers).json()
    assert detail["sap_code"] == "C2262"
    assert detail["owner_name"] == "Aastha Ramchandani"
    # Three lines on one invoice - the count is invoices, not lines.
    assert len(detail["invoice_lines"]) == 3
    assert detail["invoice_count"] == 1
    assert {line["invoice_no"] for line in detail["invoice_lines"]} == {"1398"}


def test_a_customer_is_hidden_the_same_way_from_everyone_below_super_admin(
    client, users
) -> None:
    """403, not 404, and for the same reason whoever asks.

    This used to 404 to avoid confirming an id exists outside your chain. The
    whole endpoint is now Super Admin's, so the honest answer is "not yours to
    look at" rather than "no such thing" - and it is the same answer for an
    admin and a manager, which is what makes it unguessable.
    """
    parags = client.get(
        "/api/customers", params={"search": "MADHURAJ"}, headers=super_admin_headers(client)
    )
    customer_id = parags.json()["items"][0]["id"]

    for name in ("Shail Patel", "Navya Rupawat"):
        response = client.get(
            f"/api/customers/{customer_id}", headers=sign_in(client, users[name])
        )
        assert response.status_code == 403, name


def test_search_is_case_insensitive(client) -> None:
    headers = super_admin_headers(client)
    for term in ("gulabs", "GULABS", "GuLaBs"):
        response = client.get("/api/customers", params={"search": term}, headers=headers)
        assert response.json()["total"] == 1, term


def test_search_matches_the_sap_code(client) -> None:
    headers = super_admin_headers(client)
    response = client.get("/api/customers", params={"search": "C2077"}, headers=headers)
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "SWEET KARAM COFFEE INDIA PRIVATE LIMITED"


# ------------------------------------------------------------- dashboard
def test_admin_dashboard_shape(client, users) -> None:
    body = client.get("/api/dashboard", headers=sign_in(client, users["Shail Patel"])).json()
    assert body["shape"] == "ADMIN"
    assert body["org"]["users_in_scope"] == 22
    # Leads are the universe now; the SAP book drives nothing.
    assert body["references"]["converted_leads"] == body["leads"]["converted"]
    # The SAP invoice counters are gone from the payload: they described a
    # universe no lead belonged to, so they moved nothing and explained nothing.
    assert "invoices" not in body["org"]
    assert "invoice_lines" not in body["org"]
    assert "recent_invoices" not in body

    teams = {row["team_name"]: row for row in body["teams"]}
    assert set(teams) == {"Management", "BDE", "Sales", "Unassigned"}
    assert teams["BDE"]["members"] == 5
    assert teams["Sales"]["members"] == 8
    assert teams["Unassigned"]["members"] == 7 + 1   # +1 for the Portal Owner
    # Pipeline, not the customer book. Every team's leads are a subset of the
    # whole, and the whole is what the admin's own lead KPIs report.
    assert sum(row["open_leads"] for row in teams.values()) == body["leads"]["open"]
    assert sum(row["converted"] for row in teams.values()) == body["leads"]["converted"]

    assert body["imports"] and body["imports"][0]["filename"] == "sap_invoices_real.csv"


def test_manager_dashboard_is_scoped_to_the_subtree(client, users) -> None:
    body = client.get(
        "/api/dashboard", headers=sign_in(client, users["Ramanesh Nair"])
    ).json()
    assert body["shape"] == "MANAGER"
    assert body["org"]["users_in_scope"] == 8
    # Reference work is measured against won LEADS in this manager's chain,
    # not against a SAP book that had no relationship to them.
    assert body["references"]["converted_leads"] == body["leads"]["converted"]

    names = {row["name"] for row in body["reports"]}
    assert names == {
        "Shailesh Prajapati", "Parag Sharma", "Sanjeev Singh",
        "Nidhi Ratnakar", "Pankaj", "Urvish Dave", "Lovjeet",
    }
    # Nobody from Navya's chain leaks in.
    assert "Parth Fulvani" not in names

    # Every column is about leads now. The SAP customer/invoice columns are
    # gone: they measured a book that had no link to any lead, so somebody
    # carrying forty open leads showed "0 customers" and read as idle.
    by_name = {row["name"]: row for row in body["reports"]}
    for row in by_name.values():
        assert set(row) == {
            "user_id", "name", "role", "title", "team_name", "is_active",
            "open_leads", "converted", "references_taken", "followups_due",
            "last_activity_at",
            # The reference score and the two numbers it is computed from.
            "eligible_accounts", "references_on_eligible", "reference_score",
        }, row["name"]


def test_report_rows_sort_stalest_first(client, users) -> None:
    """The person with nothing recent is the one a manager should look at."""
    body = client.get(
        "/api/dashboard", headers=sign_in(client, users["Shailesh Prajapati"])
    ).json()
    # Ordered by the person's own last activity on a lead, not by when SAP
    # last invoiced something - which measured SAP, not them.
    dates = [row["last_activity_at"] for row in body["reports"]]
    assert dates[0] is None, "somebody with nothing logged comes first"
    without_none = [d for d in dates if d]
    assert without_none == sorted(without_none)


def test_personal_dashboard(client, users) -> None:
    body = client.get(
        "/api/dashboard", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    assert body["shape"] == "PERSONAL"
    assert body["org"]["users_in_scope"] == 1
    assert body["references"]["converted_leads"] == body["leads"]["converted"]
    assert body["reports"] == [] and body["teams"] == []
    # Everything else is genuinely zero - no invented leads or references.
    assert body["leads"]["open"] == 0
    assert body["references"]["references_taken"] == 0


def test_dashboard_requires_authentication(client) -> None:
    assert client.get("/api/dashboard").status_code == 401


def test_the_dashboard_carries_nothing_from_sap(client, users) -> None:
    """S27: the operational payload no longer quotes the SAP book at all.

    The customer archive still exists for the Super Admin, but nothing on a
    dashboard is computed from it - so no screen can quietly go back to
    reporting SAP numbers next to lead numbers as if they were the same book.
    """
    for name in ("Parth Fulvani", "Ramanesh Nair", "Shail Patel"):
        body = client.get(
            "/api/dashboard", headers=sign_in(client, users[name])
        ).json()
        assert "recent_invoices" not in body, name
        assert set(body["org"]) == {
            "users_in_scope", "active_users", "unread_notifications",
        }, name
        for row in body["teams"]:
            assert "customers" not in row, name


def test_deactivating_somebody_drops_them_from_every_count(client, users, db) -> None:
    """A leaver is not "a user in scope".

    They cannot be assigned anything and appear on no other screen, so a count
    that still includes them is the one place the portal remembers them - 22
    here against a Team page showing 21, with nothing to explain the gap.
    """
    headers = sign_in(client, users["Shail Patel"])
    before = client.get("/api/dashboard", headers=headers).json()["org"]

    target = users["Aastha Ramchandani"]     # a BDE with no direct reports
    assert client.delete(f"/api/users/{target.id}", headers=headers).status_code == 200
    db.expire_all()

    after = client.get("/api/dashboard", headers=headers).json()["org"]
    assert after["users_in_scope"] == before["users_in_scope"] - 1
    assert after["active_users"] == before["active_users"] - 1
    # The two agree, which is the property that was broken: one counted
    # everybody who had ever existed, the other only the people who remain.
    assert after["users_in_scope"] == after["active_users"]

    roster = client.get("/api/users?page_size=200", headers=headers).json()
    assert roster["total"] == after["users_in_scope"]
