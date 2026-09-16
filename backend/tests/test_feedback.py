"""Module 3 — feedback import, analysis and alerts.

The real Google Forms export has not been supplied (plan Q5), so these build
files in the shape Google Forms produces. That is a test fixture, not seeded
data: nothing here reaches the seeded database.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.feedback import Feedback, FeedbackAlert, FeedbackDepartmentRating
from app.models.org import Department
from app.services import feedback_analysis
from app.services.feedback_mapping import parse_rating, parse_timestamp, resolve_columns
from tests.conftest import advance_lead, eligible_lead, sign_in, super_admin_headers

HEADERS = [
    "Timestamp",
    "Customer Name",
    "Company Name",
    "Mobile Number",
    "Email Address",
    "BDE / Salesperson who handled you",
    "Overall, how satisfied are you with our service?",
    "How would you rate our Sales team?",
    "Any comments about Sales?",
    "How would you rate our Production team?",
    "Any comments about Production?",
    "How would you rate our Quality team?",
    "Would you recommend us",
    "Any other comments or suggestions?",
]


def build_csv(rows: list[list[str]], headers: list[str] | None = None) -> bytes:
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers or HEADERS)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def response_row(
    *, name: str, sales: str, production: str, quality: str = "4", overall: str = "4"
) -> list[str]:
    stamp = date.today().strftime("%d/%m/%Y") + " 10:15:00"
    return [
        stamp,
        name,
        f"{name} Foods",
        "9800000000",
        f"{name.lower()}@example.com",
        "Parth Fulvani",
        overall,
        sales,
        "",
        production,
        "Slow dispatch.",
        quality,
        "Yes",
        "Good overall.",
    ]


def upload(client, headers, content: bytes, path: str, filename="responses.csv"):
    return client.post(
        path, headers=headers, files={"file": (filename, content, "text/csv")}
    )


# ------------------------------------------------------ column resolution
def test_headers_resolve_to_fields_and_departments() -> None:
    resolution = resolve_columns(HEADERS)

    assert resolution.fields["submitted_at_source"] == "Timestamp"
    assert resolution.fields["customer_name"] == "Customer Name"
    assert resolution.fields["company_name"] == "Company Name"
    assert resolution.fields["mobile"] == "Mobile Number"
    assert resolution.fields["email"] == "Email Address"
    assert (
        resolution.fields["handled_by_name"]
        == "BDE / Salesperson who handled you"
    )
    # "How would you rate our Sales team?" also contains "rate", so the
    # department patterns have to win over the overall-rating phrases.
    assert (
        resolution.fields["overall_rating"]
        == "Overall, how satisfied are you with our service?"
    )
    assert set(resolution.department_ratings) == {"Sales", "Production", "Quality"}
    assert set(resolution.department_comments) == {"Sales", "Production"}
    assert resolution.unmapped == []


def test_an_unrecognised_column_is_reported_not_guessed() -> None:
    resolution = resolve_columns(HEADERS + ["What is your favourite colour?"])
    assert resolution.unmapped == ["What is your favourite colour?"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4", "4.00"),
        ("4 - Satisfied", "4.00"),
        ("5 - Very Satisfied", "5.00"),
        ("Excellent", "5.00"),
        ("Poor", "2.00"),
        ("8/10", "4.00"),
        ("10", "5.00"),
        ("", None),
        ("no idea", None),
    ],
)
def test_ratings_normalise_onto_our_scale(raw: str, expected: str | None) -> None:
    result = parse_rating(raw, scale_max=5)
    assert (str(result) if result is not None else None) == expected


def test_timestamps_are_read_day_first() -> None:
    parsed = parse_timestamp("05/06/2026 12:37:42")
    assert parsed is not None
    assert (parsed.day, parsed.month) == (5, 6)


# ---------------------------------------------------------------- dry run
def test_dry_run_reports_the_mapping_and_writes_nothing(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    content = build_csv(
        [response_row(name="Alpha", sales="4", production="2") for _ in range(3)]
    )

    response = upload(client, admin, content, "/api/feedback/import/dry-run")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_rows"] == 3
    assert body["can_commit"] is True
    assert set(body["column_map"]["department_ratings"]) == {
        "Sales",
        "Production",
        "Quality",
    }
    assert len(body["samples"]) == 3
    assert body["samples"][0]["customer_name"] == "Alpha"
    assert body["samples"][0]["department_ratings"]["Production"]["rating"] == "2.00"

    # Nothing was written.
    assert db.scalar(select(func.count(Feedback.id))) == 0


def test_dry_run_flags_a_department_the_portal_does_not_know(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    headers = HEADERS + ["How would you rate our Logistics team?"]
    content = build_csv(
        [response_row(name="Beta", sales="4", production="4") + ["3"]], headers
    )

    body = upload(client, admin, content, "/api/feedback/import/dry-run").json()
    assert "Logistics" in body["unknown_departments"]
    assert any("Logistics" in warning for warning in body["warnings"])


def test_dry_run_refuses_a_file_with_no_ratings(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    content = build_csv([["x"] * 3], ["Timestamp", "Customer Name", "Email Address"])

    body = upload(client, admin, content, "/api/feedback/import/dry-run").json()
    assert body["can_commit"] is False

    commit = upload(client, admin, content, "/api/feedback/import/commit")
    assert commit.status_code == 422
    assert commit.json()["error"]["code"] == "UNMAPPED_COLUMNS"


def test_only_an_admin_may_import(client, users) -> None:
    content = build_csv([response_row(name="Gamma", sales="4", production="4")])
    for name in ("Navya Rupawat", "Parth Fulvani"):
        response = upload(
            client, sign_in(client, users[name]), content, "/api/feedback/import/dry-run"
        )
        assert response.status_code == 403, name


# ----------------------------------------------------------------- commit
def test_commit_writes_responses_and_department_ratings(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    content = build_csv(
        [
            response_row(name="Alpha", sales="5", production="2"),
            response_row(name="Bravo", sales="4", production="3"),
        ]
    )

    body = upload(client, admin, content, "/api/feedback/import/commit").json()
    assert body["created_count"] == 2
    assert body["error_count"] == 0
    assert body["status"] == "SUCCESS"
    assert body["ratings_created"] == 6          # 2 responses x 3 departments

    assert db.scalar(select(func.count(Feedback.id))) == 2
    assert db.scalar(select(func.count(FeedbackDepartmentRating.id))) == 6

    row = db.execute(
        select(Feedback).where(Feedback.customer_name == "Alpha")
    ).scalar_one()
    assert row.overall_rating == Decimal("4.00")
    # The salesperson resolved to a real account.
    assert row.handled_by_user_id == users["Parth Fulvani"].id
    assert row.source == "GOOGLE_FORMS_IMPORT"


def test_the_source_wording_is_kept_beside_the_normalised_number(client, db, users) -> None:
    """A scale change later must not silently rewrite history."""
    admin = sign_in(client, users["Shail Patel"])
    content = build_csv([response_row(name="Alpha", sales="5 - Very Satisfied", production="4")])
    upload(client, admin, content, "/api/feedback/import/commit")

    sales = db.execute(select(Department).where(Department.name == "Sales")).scalar_one()
    rating = db.execute(
        select(FeedbackDepartmentRating).where(
            FeedbackDepartmentRating.department_id == sales.id
        )
    ).scalar_one()
    assert rating.rating == Decimal("5.00")
    assert rating.raw_value == "5 - Very Satisfied"


def test_reimporting_the_same_export_creates_nothing(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    content = build_csv(
        [
            response_row(name="Alpha", sales="4", production="4"),
            response_row(name="Bravo", sales="4", production="4"),
        ]
    )

    first = upload(client, admin, content, "/api/feedback/import/commit").json()
    assert first["created_count"] == 2

    second = upload(client, admin, content, "/api/feedback/import/commit").json()
    assert second["created_count"] == 0
    assert second["skipped_count"] == 2
    assert [e["reason"] for e in second["errors"]] == ["duplicate_in_db", "duplicate_in_db"]

    # The reason is still visible later, from the import history list.
    history = client.get("/api/feedback/imports", headers=admin).json()
    assert history[0]["errors"][0]["reason"] == "duplicate_in_db"
    assert db.scalar(select(func.count(Feedback.id))) == 2


def test_a_new_department_in_the_form_is_created_and_reported(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    headers = HEADERS + ["How would you rate our Logistics team?"]
    content = build_csv(
        [response_row(name="Delta", sales="4", production="4") + ["3"]], headers
    )

    body = upload(client, admin, content, "/api/feedback/import/commit").json()
    assert body["departments_created"] == ["Logistics"]
    assert db.execute(
        select(Department).where(Department.name == "Logistics")
    ).scalar_one_or_none() is not None


def test_comments_land_against_the_right_department(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    upload(
        client,
        admin,
        build_csv([response_row(name="Echo", sales="4", production="2")]),
        "/api/feedback/import/commit",
    )

    production = db.execute(
        select(Department).where(Department.name == "Production")
    ).scalar_one()
    rating = db.execute(
        select(FeedbackDepartmentRating).where(
            FeedbackDepartmentRating.department_id == production.id
        )
    ).scalar_one()
    assert rating.comments == "Slow dispatch."


# ----------------------------------------------------------------- alerts
def _import_low_production(client, admin, count: int = 6) -> dict:
    content = build_csv(
        [
            response_row(name=f"Cust{index}", sales="5", production="1")
            for index in range(count)
        ]
    )
    return upload(client, admin, content, "/api/feedback/import/commit").json()


def test_an_alert_fires_once_for_the_whole_batch(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    body = _import_low_production(client, admin, count=6)

    assert body["created_count"] == 6
    # Six rows, one evaluation, one alert — not six.
    assert body["alerts"]["raised"] == ["Production"]
    assert db.scalar(select(func.count(FeedbackAlert.id))) == 1

    alert = db.execute(select(FeedbackAlert)).scalar_one()
    assert alert.status == "OPEN"
    assert alert.response_count == 6
    assert float(alert.average_rating) == 1.0


def test_a_department_above_the_threshold_raises_nothing(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    content = build_csv(
        [response_row(name=f"Ok{i}", sales="5", production="5") for i in range(6)]
    )
    body = upload(client, admin, content, "/api/feedback/import/commit").json()

    assert body["alerts"]["raised"] == []
    assert db.scalar(select(func.count(FeedbackAlert.id))) == 0


def test_too_few_responses_is_a_bad_day_not_a_bad_department(client, db, users) -> None:
    """Below the minimum sample, a low average means nothing."""
    admin = sign_in(client, users["Shail Patel"])
    body = _import_low_production(client, admin, count=2)

    assert body["created_count"] == 2
    assert body["alerts"]["raised"] == []
    assert db.scalar(select(func.count(FeedbackAlert.id))) == 0


def test_recovery_resolves_the_open_alert(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    _import_low_production(client, admin, count=6)
    assert db.scalar(select(func.count(FeedbackAlert.id))) == 1

    # A wave of good ratings pulls the average back above the threshold.
    good = build_csv(
        [
            response_row(name=f"Better{index}", sales="5", production="5")
            for index in range(20)
        ]
    )
    body = upload(client, admin, good, "/api/feedback/import/commit").json()

    assert body["alerts"]["resolved"] == ["Production"]
    alert = db.execute(select(FeedbackAlert)).scalar_one()
    assert alert.status == "RESOLVED"
    assert alert.resolved_at is not None
    # Still exactly one row — recovery resolves, it does not delete.
    assert db.scalar(select(func.count(FeedbackAlert.id))) == 1


def test_an_alert_notifies_the_admins(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    _import_low_production(client, admin, count=6)

    body = client.get("/api/notifications", headers=admin).json()
    alerts = [item for item in body["items"] if item["type"] == "DEPARTMENT_ALERT"]
    assert len(alerts) == 1
    assert "Production" in alerts[0]["title"]


def test_an_alert_can_be_assigned_and_cleared(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    _import_low_production(client, admin, count=6)
    alert = db.execute(select(FeedbackAlert)).scalar_one()

    assigned = client.post(
        f"/api/feedback/alerts/{alert.id}/assign",
        headers=admin,
        json={"user_id": str(users["Navya Rupawat"].id)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assigned_to_name"] == "Navya Rupawat"

    cleared = client.post(
        f"/api/feedback/alerts/{alert.id}/assign", headers=admin, json={"user_id": None}
    )
    assert cleared.json()["assigned_to_name"] is None


def test_a_bde_cannot_assign_an_alert(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    _import_low_production(client, admin, count=6)
    alert = db.execute(select(FeedbackAlert)).scalar_one()

    response = client.post(
        f"/api/feedback/alerts/{alert.id}/assign",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"user_id": str(users["Parth Fulvani"].id)},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------- pending
def test_a_bde_can_read_how_the_company_is_scoring(client, users) -> None:
    """Feedback is how the business sees itself, so the numbers are open.

    A BDE who cannot see that Dispatch is averaging 2.4 has no way to make
    sense of the complaint they are about to take. What stays shut is the
    alert list - an alert is a job somebody owns, not a number to read.
    """
    parth = sign_in(client, users["Parth Fulvani"])

    analysis = client.get("/api/feedback/analysis", headers=parth)
    assert analysis.status_code == 200
    assert analysis.json()["departments"], "department averages are company-wide"
    assert analysis.json()["alerts"] == [], "but the alert queue is not theirs"

    assert client.get("/api/feedback/alerts", headers=parth).status_code == 403
    assert client.get("/api/feedback/pending", headers=parth).status_code == 200


def test_converting_a_lead_does_not_open_a_feedback_ask(client, users) -> None:
    """Winning the deal is not delivering it.

    Feedback is asked ten days after SAP invoices the work. A converted lead
    has no invoice, so it is not owed a feedback request yet - it becomes one
    when SAP turns it into a customer. Before this rule, converting put a lead
    straight into the queue and somebody could be asked how the work went
    before any work had shipped.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = client.post(
        "/api/leads",
        headers=navya,
        json={"name": "Scoped Ask", "assigned_to_user_id": str(users["Parth Fulvani"].id)},
    ).json()
    advance_lead(client, navya, lead["id"])

    def sees(user) -> bool:
        rows = client.get("/api/feedback/pending", headers=sign_in(client, user)).json()
        return any(row["id"] == lead["id"] for row in rows)

    for who in ("Parth Fulvani", "Navya Rupawat", "Ramanesh Nair"):
        assert not sees(users[who]), who


def test_the_pending_queue_is_scoped_to_the_reporting_chain(client, users) -> None:
    """Each person is owed feedback for their own accounts and their team's,
    and nobody else's.

    The queue holds converted LEADS the post-sale sheet has invoiced, so the
    test syncs one for Parth first - without that there is nothing owed to
    anybody and the assertions would pass vacuously.
    """
    eligible_lead(client, users, assignee="Parth Fulvani", name="Owed To Parth")

    def owed(user) -> set[str]:
        rows = client.get("/api/feedback/pending", headers=sign_in(client, user)).json()
        return {row["name"] for row in rows}

    parth = owed(users["Parth Fulvani"])
    navya = owed(users["Navya Rupawat"])
    ramanesh = owed(users["Ramanesh Nair"])

    assert parth, "a BDE is owed feedback for the accounts they own"
    assert parth <= navya, "their manager sees everything they do"
    assert not (parth & ramanesh), "a different chain shares nothing"


def test_the_dashboard_counts_what_is_waiting_on_an_ask(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    before = client.get("/api/dashboard", headers=navya).json()["feedback_pending"]

    lead = client.post(
        "/api/leads",
        headers=navya,
        json={
            "name": "Counted Ask",
            "email": "counted@example.com",
            "assigned_to_user_id": str(users["Parth Fulvani"].id),
        },
    ).json()
    advance_lead(client, navya, lead["id"])

    after = client.get("/api/dashboard", headers=navya).json()["feedback_pending"]
    assert after == before, (
        "converting a lead adds nothing: it is owed feedback ten days after "
        "SAP invoices it, not when the deal is won"
    )

    # Syncing one DOES add to the count - it is now owed feedback.
    account = eligible_lead(client, users, name="Asked Not Answered")
    synced = client.get("/api/dashboard", headers=navya).json()["feedback_pending"]
    assert synced == before + 1, "a synced, invoiced account is owed feedback"

    # ...but ASKING does not take it off. It is still owed until somebody
    # answers; what changes is the state on the row, not the count.
    client.post(
        f"/api/leads/{account['id']}/message/sent",
        headers=navya,
        json={"purpose": "FEEDBACK", "channel": "EMAIL"},
    )
    asked = client.get("/api/dashboard", headers=navya).json()["feedback_pending"]
    assert asked == synced, "asking does not answer the question"


def test_an_account_with_no_feedback_is_pending(client, users) -> None:
    """Nothing is owed until something is synced - and then it is.

    The empty case matters as much as the full one: before the sheet arrives
    the queue is honestly empty, rather than full of SAP accounts nobody in
    the portal has ever worked.
    """
    admin = sign_in(client, users["Shail Patel"])
    assert client.get("/api/feedback/pending", headers=admin).json() == []

    lead = eligible_lead(client, users, name="Owes Feedback")
    body = client.get("/api/feedback/pending", headers=admin).json()
    assert [item for item in body if item["id"] == lead["id"]]
    assert all(item["type"] == "LEAD" for item in body)


def test_an_account_drops_off_pending_once_it_answers(client, db, users) -> None:
    """Answered work leaves the queue. That is what makes it "pending".

    The response reaches its account through the FB reference code on the
    request - see services/feedback_requests.py - so completing the request is
    what takes the row off, not a contact-detail match against a SAP row.
    """
    from app.models.feedback import FeedbackRequest
    from app.services import feedback_requests as request_service

    admin = sign_in(client, users["Shail Patel"])
    # The ASSIGNEE composes: the ask belongs to the person the lead is theirs.
    assignee = sign_in(client, users["Parth Fulvani"])
    account = eligible_lead(client, users, name="Answers Back")

    before = client.get("/api/feedback/pending", headers=admin).json()
    assert any(item["id"] == account["id"] for item in before)

    # Composing issues the code; the response carries it back.
    composed = client.get(
        f"/api/leads/{account['id']}/message",
        headers=assignee,
        params={"channel": "EMAIL"},
    )
    assert composed.status_code == 200, composed.text
    request = db.execute(
        select(FeedbackRequest).where(FeedbackRequest.lead_id == account["id"])
    ).scalars().one()
    request_service.mark_completed(db, request)
    db.commit()

    after = client.get("/api/feedback/pending", headers=admin).json()
    assert all(item["id"] != account["id"] for item in after)


# ---------------------------------------------------------------- reading
def test_everyone_reads_the_responses_but_not_the_contact_details(
    client, users
) -> None:
    """The responses are open; the customer's phone and email are not.

    Knowing what a customer said is what the module is for. Their mobile
    number is only needed by somebody who is going to ring them back, so it
    stays with administrators and department heads.
    """
    admin = sign_in(client, users["Shail Patel"])
    upload(
        client,
        admin,
        build_csv([response_row(name="Contactable", sales="5", production="2")]),
        "/api/feedback/import/commit",
    )

    privileged = client.get("/api/feedback", headers=admin).json()["items"][0]
    assert privileged["mobile"], "the fixture must carry a mobile or this proves nothing"

    for name in ("Navya Rupawat", "Ramanesh Nair", "Parth Fulvani"):
        body = client.get("/api/feedback", headers=sign_in(client, users[name]))
        assert body.status_code == 200, name
        row = body.json()["items"][0]
        assert row["overall_comments"] == privileged["overall_comments"], name
        assert row["mobile"] is None, name
        assert row["email"] is None, name


def test_a_department_head_sees_every_department_and_the_contacts(
    client, db, users
) -> None:
    admin = sign_in(client, users["Shail Patel"])
    upload(
        client,
        admin,
        build_csv([response_row(name="Foxtrot", sales="5", production="2")]),
        "/api/feedback/import/commit",
    )

    production = db.execute(
        select(Department).where(Department.name == "Production")
    ).scalar_one()
    head = users["Parth Fulvani"]
    head.heads_department_id = production.id
    db.flush()

    body = client.get("/api/feedback", headers=sign_in(client, head)).json()
    assert body["total"] == 1
    row = body["items"][0]
    # A whole response now, not just their own column: a 2 in Production is
    # read very differently next to a 5 in Sales.
    names = {rating["department_name"] for rating in row["department_ratings"]}
    assert {"Production", "Sales"} <= names
    # And heading a department is enough to be trusted with the contacts.
    assert row["mobile"]


def test_only_the_people_who_work_alerts_are_told_they_do(client, db, users) -> None:
    """The tab is driven by this flag, not by whether the list is empty - an
    administrator on a good week has no alerts either."""
    admin = client.get(
        "/api/feedback/analysis", headers=sign_in(client, users["Shail Patel"])
    ).json()
    assert admin["handles_alerts"] is True

    bde = client.get(
        "/api/feedback/analysis", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    assert bde["handles_alerts"] is False


def test_department_rows_come_back_in_the_companys_own_order(client, users) -> None:
    """Two reviews are only comparable at a glance if their rows line up."""
    admin = sign_in(client, users["Shail Patel"])
    upload(
        client,
        admin,
        build_csv([response_row(name="Ordered", sales="5", production="2")]),
        "/api/feedback/import/commit",
    )
    row = client.get("/api/feedback", headers=admin).json()["items"][0]
    names = [rating["department_name"] for rating in row["department_ratings"]]
    # Sales sorts before Production in the seeded department order.
    assert names.index("Sales") < names.index("Production")


def test_sample_reviews_are_flagged_and_can_be_removed(client, db, users) -> None:
    """A made-up opinion that reads like a real customer's is worse than an
    empty screen, so every sample row says so - and one command clears them."""
    from app.seeds import sample_feedback

    assert sample_feedback.load(db) == 0
    admin = sign_in(client, users["Shail Patel"])

    items = client.get("/api/feedback", headers=admin, params={"page_size": 200}).json()[
        "items"
    ]
    samples = [row for row in items if row["is_sample"]]
    assert len(samples) == len(sample_feedback.REVIEWS)
    assert all(row["department_ratings"] for row in samples)
    assert any(
        rating["comments"] for row in samples for rating in row["department_ratings"]
    )

    sample_feedback.remove(db)
    after = client.get("/api/feedback", headers=admin, params={"page_size": 200}).json()
    assert not any(row["is_sample"] for row in after["items"])


def test_the_head_flag_grants_no_authority_over_people(client, db, users) -> None:
    production = db.execute(
        select(Department).where(Department.name == "Production")
    ).scalar_one()
    head = users["Parth Fulvani"]
    head.heads_department_id = production.id
    db.flush()

    headers = sign_in(client, head)
    assert client.get("/api/feedback/analysis", headers=headers).status_code == 200
    # Still a BDE everywhere else.
    assert client.get("/api/users", headers=headers).status_code == 403
    assert client.get("/api/leads", headers=headers).json()["total"] == 0


def test_analysis_lists_every_department_including_empty_ones(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    body = client.get("/api/feedback/analysis", headers=admin).json()

    assert len(body["departments"]) == 6
    # "No data" is a finding, not a row to hide.
    assert all(row["response_count"] == 0 for row in body["departments"])
    assert body["scale_max"] == 5
    assert body["stats"]["total_responses"] == 0


def test_analysis_reports_averages_after_an_import(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    _import_low_production(client, admin, count=6)

    body = client.get("/api/feedback/analysis", headers=admin).json()
    by_name = {row["department_name"]: row for row in body["departments"]}

    assert by_name["Production"]["average_rating"] == 1.0
    assert by_name["Production"]["below_threshold"] is True
    assert by_name["Sales"]["average_rating"] == 5.0
    assert by_name["Sales"]["below_threshold"] is False
    assert body["stats"]["departments_below_threshold"] == 1
    assert len(body["alerts"]) == 1


def test_import_history_is_recorded(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    upload(
        client,
        admin,
        build_csv([response_row(name="Hotel", sales="4", production="4")]),
        "/api/feedback/import/commit",
        filename="google_form_responses.xlsx.csv",
    )

    history = client.get("/api/feedback/imports", headers=admin).json()
    assert len(history) == 1
    assert history[0]["created_count"] == 1
    assert history[0]["status"] == "SUCCESS"


def test_the_window_excludes_old_responses(client, db, users) -> None:
    """The average is a rolling window, so a bad month long past stops
    dragging a department down."""
    admin = sign_in(client, users["Shail Patel"])
    _import_low_production(client, admin, count=6)

    # Age every response past the window.
    for row in db.execute(select(Feedback)).scalars():
        row.submitted_at_source = row.submitted_at_source - timedelta(days=400)
    db.flush()

    summary = feedback_analysis.department_summary(db)
    production = next(r for r in summary if r["department_name"] == "Production")
    assert production["response_count"] == 0
    assert production["average_rating"] is None
