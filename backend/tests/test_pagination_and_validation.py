"""Every list is bounded, and every enum-like or free-text query parameter is
validated before it reaches a query."""
from __future__ import annotations

import pytest

from app.api import dashboard as dashboard_api
from app.api import timeline as timeline_api
from app.core.ratelimit import SlidingWindowLimiter
from app.schemas.common import MAX_PAGE, MAX_PAGE_SIZE
from tests.conftest import sign_in, super_admin_headers


@pytest.fixture
def manager(client, users):
    return sign_in(client, users["Navya Rupawat"])


def error_code(response) -> str:
    return response.json()["error"]["code"]


# ------------------------------------------------------------- page bounds
@pytest.mark.parametrize(
    "path",
    ["/api/leads", "/api/references", "/api/feedback", "/api/notifications"],
)
def test_page_size_over_the_maximum_is_a_422(client, manager, path) -> None:
    too_big = 101 if path == "/api/notifications" else MAX_PAGE_SIZE + 1
    response = client.get(path, headers=manager, params={"page_size": too_big})
    assert response.status_code == 422, response.text
    assert error_code(response) == "VALIDATION_ERROR"


@pytest.mark.parametrize("params", [{"page": 0}, {"page": MAX_PAGE + 1}, {"page_size": 0}])
def test_page_bounds_are_enforced(client, manager, params) -> None:
    assert client.get("/api/leads", headers=manager, params=params).status_code == 422


def test_customer_and_audit_pages_are_bounded(client) -> None:
    headers = super_admin_headers(client)
    for path in ("/api/customers", "/api/admin/audit"):
        response = client.get(path, headers=headers, params={"page_size": MAX_PAGE_SIZE + 1})
        assert response.status_code == 422, path
        assert client.get(path, headers=headers, params={"page_size": 5}).status_code == 200


def test_the_largest_page_the_frontend_asks_for_still_works(client, manager) -> None:
    response = client.get("/api/leads", headers=manager, params={"page_size": 200})
    assert response.status_code == 200
    assert response.json()["page_size"] == 200


# ------------------------------------------------------------- enums
@pytest.mark.parametrize(
    "path, params",
    [
        ("/api/leads", {"status": "WON_BIG"}),
        ("/api/leads", {"origin": "SOMEWHERE"}),
        ("/api/leads", {"priority": "URGENT"}),
        ("/api/references", {"outcome": "MAYBE"}),
        ("/api/references/accounts", {"reference_status": "SORT_OF"}),
        ("/api/work-queue", {"bucket": "EVERYTHING"}),
        ("/api/feedback", {"rating": "TERRIBLE"}),
    ],
)
def test_an_invalid_enum_value_is_a_422(client, manager, path, params) -> None:
    response = client.get(path, headers=manager, params=params)
    assert response.status_code == 422, response.text


def test_a_valid_enum_value_is_accepted(client, manager) -> None:
    assert client.get("/api/leads", headers=manager, params={"status": "NEW"}).status_code == 200
    assert (
        client.get("/api/references", headers=manager, params={"outcome": "YES"}).status_code
        == 200
    )
    assert (
        client.get(
            "/api/work-queue", headers=manager, params={"bucket": "ASSIGNED_BY_HEAD"}
        ).status_code
        == 200
    )


def test_customer_reference_status_is_validated(client) -> None:
    headers = super_admin_headers(client)
    bad = client.get("/api/customers", headers=headers, params={"reference_status": "x"})
    assert bad.status_code == 422
    good = client.get(
        "/api/customers", headers=headers, params={"reference_status": "NOT_ASKED"}
    )
    assert good.status_code == 200


# ------------------------------------------------------------- sort and filter allow-lists
def test_an_unknown_grouping_is_a_422(client, manager) -> None:
    response = client.get(
        "/api/references/follow-ups", headers=manager, params={"group_by": "name; DROP TABLE"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("value", ["lead' OR 1=1 --", "x" * 61, "lower_case"])
def test_audit_filters_accept_only_codes(client, value) -> None:
    response = client.get(
        "/api/admin/audit", headers=super_admin_headers(client), params={"action": value}
    )
    assert response.status_code == 422


def test_audit_filter_with_a_real_code_works(client) -> None:
    response = client.get(
        "/api/admin/audit", headers=super_admin_headers(client), params={"action": "SAP_IMPORTED"}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path", ["/api/leads", "/api/references", "/api/references/accounts", "/api/feedback"]
)
def test_search_terms_are_length_bounded(client, manager, path) -> None:
    response = client.get(path, headers=manager, params={"search": "a" * 121})
    assert response.status_code == 422


def test_ids_must_be_uuids(client, manager) -> None:
    assert client.get("/api/leads/not-a-uuid", headers=manager).status_code == 422
    assert (
        client.get("/api/leads", headers=manager, params={"assigned_to": "1 OR 1=1"}).status_code
        == 422
    )


# ------------------------------------------------------------- bare lists are capped
@pytest.mark.parametrize(
    "path, params",
    [
        ("/api/references/accounts", {"limit": 0}),
        ("/api/references/accounts", {"limit": 5_001}),
        ("/api/references/follow-ups", {"limit": 1_001}),
        ("/api/feedback/pending", {"limit": 5_001}),
        ("/api/work-queue", {"limit": 201}),
    ],
)
def test_list_limits_are_bounded(client, manager, path, params) -> None:
    assert client.get(path, headers=manager, params=params).status_code == 422


def test_a_list_limit_is_honoured(client, manager) -> None:
    response = client.get("/api/references/follow-ups", headers=manager, params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()) <= 1


def test_admin_lists_are_bounded(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    assert (
        client.get("/api/feedback/alerts", headers=admin, params={"limit": 1_001}).status_code
        == 422
    )
    assert (
        client.get("/api/feedback/sync/customers", headers=admin, params={"limit": 5_001})
        .status_code
        == 422
    )
    capped = client.get("/api/feedback/sync/customers", headers=admin, params={"limit": 2})
    assert capped.status_code == 200 and len(capped.json()) <= 2


def test_the_customer_timeline_is_capped(client, monkeypatch) -> None:
    headers = super_admin_headers(client)
    customer = client.get("/api/customers", headers=headers, params={"page_size": 1}).json()[
        "items"
    ][0]
    monkeypatch.setattr(timeline_api, "TIMELINE_MAX_ENTRIES", 1)
    for remark in ("first", "second"):
        response = client.post(
            f"/api/customers/{customer['id']}/timeline",
            headers=headers,
            json={"activity_type": "NOTE", "remark": remark},
        )
        assert response.status_code == 200, response.text
    timeline = client.get(f"/api/customers/{customer['id']}/timeline", headers=headers)
    assert timeline.status_code == 200
    entries = timeline.json()
    assert len(entries) == 1
    assert entries[0]["remark"] == "second"     # the cap drops the OLDEST


# ------------------------------------------------------------- runtime settings
@pytest.mark.parametrize(
    "values",
    [
        {"feedback.alert_threshold": "not a number"},
        {"feedback.alert_threshold": -1},
        {"feedback.rating_scale_max": 5.5},
        {"feedback.rating_scale_max": True},
        {"company.feedback_form_url": "javascript:alert(1)"},
        {"company.feedback_form_url": "file:///etc/passwd"},
        {"message.feedback_whatsapp": "x" * 10_001},
    ],
)
def test_invalid_setting_values_are_refused(client, users, values) -> None:
    admin = sign_in(client, users["Shail Patel"])
    response = client.patch("/api/admin/settings", headers=admin, json={"values": values})
    assert response.status_code == 422, response.text


def test_valid_setting_values_are_saved(client, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    response = client.patch(
        "/api/admin/settings",
        headers=admin,
        json={
            "values": {
                "feedback.alert_threshold": 3.5,
                "feedback.rating_scale_max": 5,
                "company.feedback_form_url": "https://docs.google.com/forms/d/e/x/viewform",
            }
        },
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------- analytics rate limit
def test_the_dashboard_is_rate_limited_per_user(client, manager, monkeypatch) -> None:
    monkeypatch.setattr(dashboard_api, "analytics_limiter", SlidingWindowLimiter(2, 60))
    assert client.get("/api/dashboard", headers=manager).status_code == 200
    assert client.get("/api/dashboard", headers=manager).status_code == 200
    response = client.get("/api/dashboard", headers=manager)
    assert response.status_code == 429
    assert error_code(response) == "RATE_LIMITED"
