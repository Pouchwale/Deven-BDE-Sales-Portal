"""Every screen that shows the same number must show the same number.

The lead list, the lead KPIs, the dashboard, the team rollups, the reference
module and the assistant all count the same things. Each of them is asked,
for several roles, over one deliberately uneven book of work - leads at
different stages, won accounts that are askable, still waiting, and not yet
synced - and the answers are compared. A disagreement here is two sources of
truth, which is the bug `services/metrics.py` exists to prevent.
"""
from __future__ import annotations

import pytest

from app.core import authority
from app.core.constants import OPEN_LEAD_STATUSES, LeadStatus
from app.services.chat import tools as chat_tools
from app.services.chat.registry import ToolContext
from tests.conftest import advance_lead, sign_in, super_admin_headers, sync_post_sale

ROLES = (
    "Portal Owner",
    "Shail Patel",
    "Navya Rupawat",
    "Ramanesh Nair",
    "Shailesh Prajapati",
    "Parth Fulvani",
    "Parag Sharma",
)


def _lead(client, headers, assignee, name: str, mobile: str) -> dict:
    response = client.post(
        "/api/leads",
        headers=headers,
        json={"name": name, "mobile": mobile, "assigned_to_user_id": str(assignee.id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _move(client, headers, lead, status: str) -> None:
    response = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=headers,
        json={"status": status, "remark": "Reconciliation fixture."},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def book(client, users):
    """An uneven book across both teams."""
    navya = sign_in(client, users["Navya Rupawat"])
    shailesh = sign_in(client, users["Shailesh Prajapati"])
    admin = sign_in(client, users["Shail Patel"])

    parth, muskan = users["Parth Fulvani"], users["Muskan Makhija"]
    parag, sanjeev = users["Parag Sharma"], users["Sanjeev Singh"]

    # Parth: one askable win (already gave a reference), one win still inside
    # its waiting period, one lead mid-pipeline.
    askable = _lead(client, navya, parth, "Recon Askable", "9811100001")
    advance_lead(client, navya, askable["id"])
    sync_post_sale(client, admin, lead=askable, invoiced_days_ago=40)

    waiting = _lead(client, navya, parth, "Recon Waiting", "9811100002")
    advance_lead(client, navya, waiting["id"])
    sync_post_sale(client, admin, lead=waiting, invoiced_days_ago=2)

    nurturing = _lead(client, navya, parth, "Recon Nurturing", "9811100003")
    advance_lead(client, navya, nurturing["id"], to="NURTURING")

    # Muskan: won but nothing synced yet.
    unsynced = _lead(client, navya, muskan, "Recon Unsynced", "9811100004")
    advance_lead(client, navya, unsynced["id"])

    # Sales: an askable win, a lost lead and one not reached.
    sales_win = _lead(client, shailesh, parag, "Recon Sales Win", "9811100005")
    advance_lead(client, shailesh, sales_win["id"])
    sync_post_sale(client, admin, lead=sales_win, invoiced_days_ago=30)

    lost = _lead(client, shailesh, parag, "Recon Lost", "9811100006")
    _move(client, shailesh, lost, "LOST")

    not_reached = _lead(client, shailesh, sanjeev, "Recon Not Reached", "9811100007")
    _move(client, shailesh, not_reached, "NOT_CONTACTED")

    response = client.post(
        "/api/references",
        headers=sign_in(client, parth),
        json={"lead_id": askable["id"], "outcome": "YES", "referred_name": "A Friend"},
    )
    assert response.status_code == 201, response.text
    return users


def _headers(client, users, name):
    if name == "Portal Owner":
        return super_admin_headers(client)
    return sign_in(client, users[name])


def _tool(db, user, name, args=None) -> dict:
    ctx = ToolContext(
        db=db,
        actor=user,
        scope=authority.visible_user_ids(db, user),
        department_scope=authority.feedback_department_scope(user),
    )
    outcome = chat_tools.dispatch(ctx, name, args or {})
    assert outcome["ok"], outcome
    return outcome["result"]


@pytest.mark.parametrize("name", ROLES)
def test_lead_counts_agree_everywhere(client, db, book, name) -> None:
    users = book
    user = users[name]
    h = _headers(client, users, name)

    stats = client.get("/api/leads/stats", headers=h).json()
    listed = client.get("/api/leads?page_size=1", headers=h).json()
    open_listed = client.get("/api/leads?page_size=1&open_only=true", headers=h).json()
    dashboard = client.get("/api/dashboard", headers=h).json()

    assert stats["total"] == listed["total"] == dashboard["leads"]["total"]
    assert stats["open"] == open_listed["total"] == dashboard["leads"]["open"]
    assert stats == dashboard["leads"]

    # Stage by stage, the list filter and the KPI are the same count, and the
    # stages add up to the total.
    for stage in LeadStatus:
        by_filter = client.get(
            f"/api/leads?page_size=1&status={stage.value}", headers=h
        ).json()["total"]
        assert stats[stage.value.lower()] == by_filter, stage
    assert sum(stats[s.value.lower()] for s in LeadStatus) == stats["total"]
    assert sum(stats[s.lower()] for s in OPEN_LEAD_STATUSES) == stats["open"]

    # The assistant reads the same functions with the same actor. It also sees
    # `closed`, which the HTTP schema does not carry - and which is, by
    # construction, what is left of the total once the open leads are gone.
    tool_stats = _tool(db, user, "get_lead_stats")
    assert {key: tool_stats[key] for key in stats} == stats
    assert tool_stats["closed"] == stats["total"] - stats["open"]
    assert _tool(db, user, "list_leads", {})["total"] == listed["total"]
    summary = _tool(db, user, "get_my_work_summary")
    assert {key: summary["leads"][key] for key in stats} == stats


@pytest.mark.parametrize("name", ROLES)
def test_reference_population_agrees_everywhere(client, db, book, name) -> None:
    users = book
    user = users[name]
    h = _headers(client, users, name)

    module = client.get("/api/references/stats", headers=h).json()
    dashboard = client.get("/api/dashboard", headers=h).json()
    accounts = client.get("/api/references/accounts", headers=h).json()
    pending = client.get("/api/feedback/pending", headers=h).json()
    feedback_population = client.get("/api/feedback/pending/summary", headers=h).json()

    assert dashboard["references"] == module
    assert module["eligible_accounts"] == len(accounts)
    assert (
        module["eligible_accounts"] + module["waiting_period"] + module["awaiting_sync"]
        == module["converted_leads"]
    )
    # The won book is the pipeline's own CONVERTED count - one universe.
    assert module["converted_leads"] == dashboard["leads"]["converted"]
    # Feedback measures the same eligible population. No responses exist in
    # this book, so every eligible account is still owed an ask.
    assert feedback_population["eligible"] == module["eligible_accounts"]
    assert feedback_population["converted"] == module["converted_leads"]
    assert len(pending) == feedback_population["pending"] == dashboard["feedback_pending"]
    assert (
        module["references_taken"]
        + module["references_pending"]
        + module["references_declined"]
        + module["not_asked"]
        == module["eligible_accounts"]
    )

    assert _tool(db, user, "get_reference_stats") == module
    assert _tool(db, user, "list_reference_accounts")["total"] == len(accounts)
    assert _tool(db, user, "list_pending_feedback_requests")["total"] == len(pending)
    assert _tool(db, user, "get_my_work_summary")["references"] == module


def test_the_book_is_what_the_fixture_built(client, book) -> None:
    """Guards the other tests from passing vacuously on an empty book."""
    module = client.get("/api/references/stats", headers=super_admin_headers(client)).json()
    assert module["converted_leads"] == 4
    assert module["eligible_accounts"] == 2
    assert module["waiting_period"] == 1
    assert module["awaiting_sync"] == 1
    assert module["references_taken"] == 1

    parth = client.get(
        "/api/references/stats", headers=sign_in(client, book["Parth Fulvani"])
    ).json()
    assert (parth["converted_leads"], parth["eligible_accounts"]) == (2, 1)


def test_team_rollup_adds_up_to_the_company(client, book) -> None:
    dashboard = client.get("/api/dashboard", headers=super_admin_headers(client)).json()
    teams = dashboard["teams"]
    assert sum(row["open_leads"] for row in teams) == dashboard["leads"]["open"]
    assert sum(row["converted"] for row in teams) == dashboard["leads"]["converted"]


@pytest.mark.parametrize("name", ["Navya Rupawat", "Ramanesh Nair", "Shailesh Prajapati"])
def test_manager_report_rows_add_up_to_their_scope(client, db, book, name) -> None:
    users = book
    manager = users[name]
    h = sign_in(client, manager)
    dashboard = client.get("/api/dashboard", headers=h).json()
    rows = dashboard["reports"]

    own = client.get(
        f"/api/leads?page_size=1&open_only=true&assigned_to={manager.id}", headers=h
    ).json()["total"]
    assert sum(row["open_leads"] for row in rows) + own == dashboard["leads"]["open"]

    own_converted = client.get(
        f"/api/leads?page_size=1&status=CONVERTED&assigned_to={manager.id}", headers=h
    ).json()["total"]
    assert (
        sum(row["converted"] for row in rows) + own_converted
        == dashboard["leads"]["converted"]
    )
    # Per-person eligible accounts are the same population the module counts.
    own_eligible = sum(
        1
        for account in client.get("/api/references/accounts", headers=h).json()
        if account["owner_user_id"] == str(manager.id)
    )
    assert (
        sum(row["eligible_accounts"] for row in rows) + own_eligible
        == dashboard["references"]["eligible_accounts"]
    )

    # The assistant's team tool is built from the same rows.
    workload = _tool(db, manager, "get_team_workload", {"limit": 50})
    assert workload["total"] == len(rows)
    assert sum(item["open_leads"] for item in workload["items"]) == sum(
        row["open_leads"] for row in rows
    )
