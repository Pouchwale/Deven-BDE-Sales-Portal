"""Who is scored on the dashboard, and what the people table lists.

* The Super Admin runs the portal and has no pipeline: never a row in the
  people table.
* A manager is scored twice: the team (their whole subtree) and their own
  accounts, separately.
* Admin and Super Admin get no personal score block.
"""
from __future__ import annotations

from app.core.constants import Role
from tests.conftest import sign_in, super_admin_headers


def _dashboard(client, headers) -> dict:
    response = client.get("/api/dashboard", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_super_admin_is_never_a_row_in_the_people_table(client, users) -> None:
    for headers in (sign_in(client, users["Shail Patel"]), super_admin_headers(client)):
        rows = _dashboard(client, headers)["reports"]
        assert rows, "the admin table should list people"
        assert all(row["role"] != Role.SUPER_ADMIN for row in rows)


def test_manager_gets_a_personal_score_beside_the_team_score(client, users) -> None:
    data = _dashboard(client, sign_in(client, users["Navya Rupawat"]))
    assert data["shape"] == "MANAGER"
    mine = data["my_reference"]
    assert mine is not None
    assert set(mine) == {"eligible_accounts", "references_taken", "reference_score"}
    # Own accounts are a subset of the team's.
    assert mine["eligible_accounts"] <= data["references"]["eligible_accounts"]
    assert mine["references_taken"] <= data["references"]["references_taken"]


def test_manager_personal_score_matches_their_row_elsewhere(client, users) -> None:
    """The admin table's row for a manager and the manager's own 'my score'
    are the same number - one definition, two screens."""
    navya = users["Navya Rupawat"]
    mine = _dashboard(client, sign_in(client, navya))["my_reference"]
    row = next(
        r
        for r in _dashboard(client, sign_in(client, users["Shail Patel"]))["reports"]
        if r["user_id"] == str(navya.id)
    )
    assert row["eligible_accounts"] == mine["eligible_accounts"]
    assert row["references_on_eligible"] == mine["references_taken"]
    assert row["reference_score"] == mine["reference_score"]


def test_admins_and_employees_get_no_personal_score_block(client, users) -> None:
    for headers in (
        sign_in(client, users["Shail Patel"]),
        super_admin_headers(client),
        sign_in(client, users["Parth Fulvani"]),
    ):
        assert _dashboard(client, headers)["my_reference"] is None
