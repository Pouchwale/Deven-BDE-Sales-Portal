"""Admin and team centre, end to end through the API.

Phase 2 exit criterion: every forbidden combination in plan v3 s3 returns 403
with the right code. The authority rules themselves are proven in
test_authority.py; this file proves the routes actually apply them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.core.constants import ErrorCode, Role
from tests.conftest import (
    SEED_PASSWORD,
    auth,
    new_email,
    sign_in,
    super_admin_headers,
    token_for,
)


def error_code(response) -> str:
    return response.json()["error"]["code"]


# ------------------------------------------------------------------ auth
def test_login_and_me(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    response = client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["email"] == "shail.patel@pouchwale.com"
    assert response.json()["role"] == Role.ADMIN


def test_login_rejects_a_wrong_password(client, users) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": users["Shail Patel"].email, "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert error_code(response) == ErrorCode.UNAUTHORIZED


def test_login_does_not_reveal_whether_an_account_exists(client, users) -> None:
    """The same message for a missing account and a wrong password."""
    missing = client.post(
        "/api/auth/login", json={"email": "nobody@pouchwale.com", "password": "whatever"}
    )
    wrong = client.post(
        "/api/auth/login",
        json={"email": users["Shail Patel"].email, "password": "whatever"},
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_seeded_account_must_change_password_before_using_the_portal(client, users) -> None:
    user = users["Parth Fulvani"]
    assert user.must_change_password
    login = client.post(
        "/api/auth/login", json={"email": user.email, "password": SEED_PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    headers = auth(login.json()["access_token"])

    # /auth/me stays reachable so the UI can greet them; everything else does not.
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    blocked = client.get("/api/teams", headers=headers)
    assert blocked.status_code == 403
    assert error_code(blocked) == ErrorCode.PASSWORD_CHANGE_REQUIRED


def test_changing_the_password_clears_the_flag_and_kills_the_old_token(client, users) -> None:
    user = users["Muskan Makhija"]
    headers = auth(token_for(client, user.email))

    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": SEED_PASSWORD, "new_password": "BrandNew@2026"},
    )
    assert changed.status_code == 200
    assert user.must_change_password is False

    # The old token predates password_changed_at, so it is now worthless.
    assert client.get("/api/auth/me", headers=headers).status_code == 401

    fresh = auth(token_for(client, user.email, "BrandNew@2026"))
    assert client.get("/api/teams", headers=fresh).status_code == 200


def test_protected_routes_require_a_token(client) -> None:
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


# ------------------------------------------------------------- listing
def test_user_list_is_scoped_to_the_subtree(client, users) -> None:
    navya = client.get("/api/users", headers=sign_in(client, users["Navya Rupawat"]))
    names = {item["name"] for item in navya.json()["items"]}
    assert names == {
        "Navya Rupawat", "Parth Fulvani", "Muskan Makhija",
        "Aastha Ramchandani", "Shivani Patel",
    }

    ramanesh = client.get("/api/users", headers=sign_in(client, users["Ramanesh Nair"]))
    assert ramanesh.json()["total"] == 8
    assert "Navya Rupawat" not in {i["name"] for i in ramanesh.json()["items"]}

    admin = client.get("/api/users", headers=sign_in(client, users["Shail Patel"]))
    assert admin.json()["total"] == 22


def test_field_users_cannot_list_the_directory(client, users) -> None:
    response = client.get("/api/users", headers=sign_in(client, users["Parth Fulvani"]))
    assert response.status_code == 403
    assert error_code(response) == ErrorCode.FORBIDDEN


def test_a_manager_gets_404_for_someone_outside_their_chain(client, users) -> None:
    """404 rather than 403, so ids cannot be probed for existence."""
    headers = sign_in(client, users["Navya Rupawat"])
    response = client.get(f"/api/users/{users['Parag Sharma'].id}", headers=headers)
    assert response.status_code == 404


def test_org_chart_hangs_from_the_caller(client, users) -> None:
    response = client.get(
        "/api/users/org-chart", headers=sign_in(client, users["Ramanesh Nair"])
    )
    tree = response.json()
    assert len(tree) == 1 and tree[0]["name"] == "Ramanesh Nair"
    shailesh = tree[0]["reports"][0]
    assert shailesh["name"] == "Shailesh Prajapati"
    assert len(shailesh["reports"]) == 6


# ---------------------------------------------------------- Rule 2 routes
def test_assignable_roles_endpoint(client, users) -> None:
    admin = client.get(
        "/api/users/assignable-roles", headers=sign_in(client, users["Shail Patel"])
    )
    assert set(admin.json()["roles"]) == {"MANAGER", "BDE", "SALES"}

    owner = client.get(
        "/api/users/assignable-roles", headers=sign_in(client, users["Portal Owner"])
    )
    assert set(owner.json()["roles"]) == {"ADMIN", "MANAGER", "BDE", "SALES"}

    manager = client.get(
        "/api/users/assignable-roles", headers=sign_in(client, users["Navya Rupawat"])
    )
    assert set(manager.json()["roles"]) == {"BDE", "SALES"}


def test_actionable_endpoint_sizes(client, users) -> None:
    navya = client.get(
        "/api/users/actionable", headers=sign_in(client, users["Navya Rupawat"])
    )
    assert {u["name"] for u in navya.json()} == {
        "Parth Fulvani", "Muskan Makhija", "Aastha Ramchandani", "Shivani Patel",
    }
    shailesh = client.get(
        "/api/users/actionable", headers=sign_in(client, users["Shailesh Prajapati"])
    )
    assert len(shailesh.json()) == 6


# ------------------------------------------------------------- creation
def test_admin_creates_a_bde_under_a_manager(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    response = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "New Starter",
            "email": new_email(),
            "password": "Welcome@2026",
            "role": "BDE",
            "manager_id": str(users["Navya Rupawat"].id),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "BDE"
    assert body["manager_name"] == "Navya Rupawat"
    # Whoever created the account knows the password, so it is not yet theirs.
    assert body["must_change_password"] is True


def test_admin_cannot_create_another_admin(client, users) -> None:
    """Q2: only a SUPER_ADMIN appoints administrators."""
    response = client.post(
        "/api/users",
        headers=sign_in(client, users["Shail Patel"]),
        json={
            "name": "Second Director",
            "email": new_email(),
            "password": "Welcome@2026",
            "role": "ADMIN",
        },
    )
    assert response.status_code == 403
    assert error_code(response) == ErrorCode.ROLE_ABOVE_ACTOR
    assert "ADMIN" not in response.json()["error"]["details"]["assignable_roles"]


def test_super_admin_can_create_an_admin(client, users) -> None:
    response = client.post(
        "/api/users",
        headers=sign_in(client, users["Portal Owner"]),
        json={
            "name": "Second Director",
            "email": new_email(),
            "password": "Welcome@2026",
            "role": "ADMIN",
        },
    )
    assert response.status_code == 201, response.text


def test_managers_cannot_create_users(client, users) -> None:
    """Q4 default: user creation is Admin-only; managers read and assign."""
    response = client.post(
        "/api/users",
        headers=sign_in(client, users["Navya Rupawat"]),
        json={
            "name": "Their Hire",
            "email": new_email(),
            "password": "Welcome@2026",
            "role": "BDE",
        },
    )
    assert response.status_code == 403


def test_duplicate_email_is_rejected(client, users) -> None:
    response = client.post(
        "/api/users",
        headers=sign_in(client, users["Shail Patel"]),
        json={
            "name": "Clash",
            "email": users["Parth Fulvani"].email.upper(),   # case-insensitive
            "password": "Welcome@2026",
            "role": "BDE",
        },
    )
    assert response.status_code == 409
    assert error_code(response) == ErrorCode.CONFLICT


def test_a_bde_cannot_be_given_reports(client, users) -> None:
    response = client.post(
        "/api/users",
        headers=sign_in(client, users["Shail Patel"]),
        json={
            "name": "Under A BDE",
            "email": new_email(),
            "password": "Welcome@2026",
            "role": "BDE",
            "manager_id": str(users["Parth Fulvani"].id),
        },
    )
    assert response.status_code == 422
    assert error_code(response) == ErrorCode.MANAGER_RANK_INVALID


# --------------------------------------------------------------- editing
def test_admin_cannot_edit_the_super_admin(client, users) -> None:
    response = client.patch(
        f"/api/users/{users['Portal Owner'].id}",
        headers=sign_in(client, users["Shail Patel"]),
        json={"name": "Renamed Owner"},
    )
    assert response.status_code == 403
    assert users["Portal Owner"].name == "Portal Owner"


def test_admin_cannot_promote_anyone_to_admin(client, users) -> None:
    response = client.patch(
        f"/api/users/{users['Navya Rupawat'].id}",
        headers=sign_in(client, users["Shail Patel"]),
        json={"role": "ADMIN"},
    )
    assert response.status_code == 403
    assert error_code(response) == ErrorCode.ROLE_ABOVE_ACTOR
    assert users["Navya Rupawat"].role == Role.MANAGER


def test_reporting_line_cycle_is_rejected(client, users) -> None:
    """Point Ramanesh at his own grandchild and the tree would close a loop."""
    response = client.patch(
        f"/api/users/{users['Ramanesh Nair'].id}",
        headers=sign_in(client, users["Shail Patel"]),
        json={"manager_id": str(users["Parag Sharma"].id)},
    )
    assert response.status_code == 409
    assert error_code(response) == ErrorCode.CYCLIC_REPORTING_LINE


def test_moving_a_user_between_chains_works(client, users) -> None:
    response = client.patch(
        f"/api/users/{users['Parth Fulvani'].id}",
        headers=sign_in(client, users["Shail Patel"]),
        json={"manager_id": str(users["Shailesh Prajapati"].id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["manager_name"] == "Shailesh Prajapati"


# ------------------------------------------------------- password reset
def test_setting_a_password_never_returns_it(client, users) -> None:
    """The response says what happened. It never carries the credential.

    Existing passwords cannot be shown at all - they are one-way hashes - and
    the new one is not echoed either: whoever set it already has it, and a
    password in a response body lands in browser caches and proxy logs.
    """
    target = users["Shivani Patel"]
    response = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": "Reset@2026xyz"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "hashed_password" not in body
    assert body["must_change_password"] is True
    assert target.must_change_password is True

    login = client.post(
        "/api/auth/login", json={"email": target.email, "password": "Reset@2026xyz"}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


def test_a_set_password_never_reaches_the_audit_trail(client, users, db) -> None:
    """Whoever can read the audit log must not thereby be able to sign in as
    the people in it. The trail records THAT a password was set, never what
    it was set to."""
    from app.models.system import AuditEvent

    secret = "NeverInTheLog@99"
    client.post(
        f"/api/users/{users['Shivani Patel'].id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": secret},
    )
    blob = " ".join(
        f"{row.before} {row.after}"
        for row in db.execute(select(AuditEvent)).scalars()
    )
    assert secret not in blob
    assert "PASSWORD_RESET" in {
        row.action for row in db.execute(select(AuditEvent)).scalars()
    }


def test_an_omitted_password_is_generated_but_still_not_returned(client, users) -> None:
    """A generated password locks the account rather than leaking it.

    The browser generates the one an administrator hands over, so the server
    never has to send a credential back. Omitting it here still rotates the
    password - the old one stops working - it simply cannot be read.
    """
    target = users["Shivani Patel"]
    client.post(
        f"/api/users/{target.id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": "KnownFirst@2026"},
    )
    response = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={},
    )
    assert response.status_code == 200, response.text
    assert "password" not in response.json()

    stale = client.post(
        "/api/auth/login", json={"email": target.email, "password": "KnownFirst@2026"}
    )
    assert stale.status_code == 401, "the previous password still worked"


def test_a_password_can_be_set_without_forcing_a_change(client, users) -> None:
    """For handing somebody a login they will keep using."""
    target = users["Shivani Patel"]
    response = client.post(
        f"/api/users/{target.id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": "KeepThis@2026", "must_change": False},
    )
    assert response.status_code == 200
    assert response.json()["must_change_password"] is False

    login = client.post(
        "/api/auth/login", json={"email": target.email, "password": "KeepThis@2026"}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is False


# ---------------------------------------------------------- deactivation
def test_deactivate_blocked_while_a_user_has_direct_reports(client, users) -> None:
    response = client.delete(
        f"/api/users/{users['Navya Rupawat'].id}",
        headers=sign_in(client, users["Shail Patel"]),
    )
    assert response.status_code == 409
    assert error_code(response) == ErrorCode.HAS_DIRECT_REPORTS
    assert len(response.json()["error"]["details"]["reports"]) == 4
    assert users["Navya Rupawat"].is_active is True


def test_deactivate_with_reparenting_succeeds(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    response = client.delete(
        f"/api/users/{users['Navya Rupawat'].id}",
        headers=headers,
        params={"new_manager_id": str(users["Ramanesh Nair"].id)},
    )
    assert response.status_code == 200, response.text
    assert users["Navya Rupawat"].is_active is False
    assert users["Navya Rupawat"].deactivated_at is not None
    assert users["Parth Fulvani"].manager_id == users["Ramanesh Nair"].id


def test_a_deactivated_user_cannot_sign_in(client, users) -> None:
    target = users["Bimal"]
    client.delete(f"/api/users/{target.id}", headers=sign_in(client, users["Shail Patel"]))
    assert target.is_active is False
    login = client.post(
        "/api/auth/login", json={"email": target.email, "password": SEED_PASSWORD}
    )
    assert login.status_code == 401


def _org_names(nodes) -> list[str]:
    out: list[str] = []
    for node in nodes:
        out.append(node["name"])
        out += _org_names(node["reports"])
    return out


def test_deactivating_a_user_drops_them_from_every_headcount(client, users) -> None:
    """One switch, every list. A person who has been deactivated should stop
    being counted as a member of the team - on the dashboard, in the org
    chart and in the roster - without anybody having to remember a second
    place to update. This existed as a bug: the dashboard's people table and
    the assistant's team-workload tool both kept listing the leaver.
    """
    target = users["Bimal"]
    headers = sign_in(client, users["Shail Patel"])

    before_dash = client.get("/api/dashboard", headers=headers).json()
    assert target.name in [row["name"] for row in before_dash["reports"]]

    assert client.delete(f"/api/users/{target.id}", headers=headers).status_code == 200

    dashboard = client.get("/api/dashboard", headers=headers).json()
    assert target.name not in [row["name"] for row in dashboard["reports"]]
    # Not merely absent by name - nothing inactive may appear at all.
    assert all(row["is_active"] for row in dashboard["reports"])

    chart = client.get("/api/users/org-chart", headers=headers).json()
    assert target.name not in _org_names(chart)

    roster = client.get("/api/users", headers=headers, params={"page_size": 200}).json()
    assert target.name not in [row["name"] for row in roster["items"]]


def test_deactivated_people_are_still_reachable_when_asked_for(client, users) -> None:
    """Hidden by default, never deleted. The Team page's switch has to be able
    to bring them back or a deactivation would be unauditable from the UI."""
    target = users["Bimal"]
    headers = sign_in(client, users["Shail Patel"])
    client.delete(f"/api/users/{target.id}", headers=headers)

    chart = client.get(
        "/api/users/org-chart", headers=headers, params={"include_inactive": True}
    ).json()
    assert target.name in _org_names(chart)

    roster = client.get(
        "/api/users",
        headers=headers,
        params={"page_size": 200, "include_inactive": True},
    ).json()
    row = next(r for r in roster["items"] if r["name"] == target.name)
    assert row["is_active"] is False


def test_team_headcount_ignores_leavers_but_their_leads_still_count(
    client, users, db
) -> None:
    """Deactivating somebody does not reassign their leads, so the team's
    pipeline must not shrink when they leave - only the headcount does.

    The rollup counts LEADS now. It used to count owned SAP customers, which
    told an administrator nothing about what the team was working on.
    """
    from app.services.dashboard import _team_rollup

    target = users["Parth Fulvani"]
    # Without leads on the books the "pipeline unchanged" assertion below
    # would be 0 == 0 and would prove nothing, so give them one first.
    manager = sign_in(client, users["Navya Rupawat"])
    client.post(
        "/api/leads",
        headers=manager,
        json={
            "name": "Leaver's Pipeline Ltd",
            "mobile": "9800000731",
            "assigned_to_user_id": str(target.id),
        },
    )
    db.expire_all()

    before = {row["team_name"]: row for row in _team_rollup(db)}
    team = target.team.name if target.team else "Unassigned"
    assert before[team]["open_leads"] > 0

    client.delete(f"/api/users/{target.id}", headers=sign_in(client, users["Shail Patel"]))
    db.flush()

    after = {row["team_name"]: row for row in _team_rollup(db)}
    assert after[team]["members"] == before[team]["members"] - 1
    assert after[team]["open_leads"] == before[team]["open_leads"]
    assert after[team]["converted"] == before[team]["converted"]


# ---------------------------------------------------------- self-service
def test_self_service_cannot_change_role_or_manager(client, users) -> None:
    user = users["Parth Fulvani"]
    headers = sign_in(client, user)
    response = client.patch(
        "/api/me",
        headers=headers,
        json={
            "name": "Parth F",
            "role": "ADMIN",                       # ignored, not honoured
            "manager_id": str(users["Portal Owner"].id),
            "is_active": False,
        },
    )
    assert response.status_code == 200
    assert user.name == "Parth F"
    assert user.role == Role.BDE
    assert user.manager_id == users["Navya Rupawat"].id
    assert user.is_active is True


def test_super_admin_cannot_demote_themselves(client, users) -> None:
    """There is no self-service path to a role change, for anyone."""
    owner = users["Portal Owner"]
    client.patch("/api/me", headers=sign_in(client, owner), json={"role": "BDE"})
    assert owner.role == Role.SUPER_ADMIN

    # ...and not through the admin route either: nobody may act on themselves.
    response = client.patch(
        f"/api/users/{owner.id}",
        headers=sign_in(client, owner),
        json={"role": "BDE"},
    )
    assert response.status_code == 403
    assert owner.role == Role.SUPER_ADMIN


# ---------------------------------------------------------- audit trail
def test_every_gated_mutation_writes_an_audit_row(client, db, users) -> None:
    from app.models.system import AuditEvent

    before = db.query(AuditEvent).count()
    client.post(
        "/api/users",
        headers=sign_in(client, users["Shail Patel"]),
        json={
            "name": "Audited Hire",
            "email": new_email(),
            "password": "Welcome@2026",
            "role": "BDE",
            "manager_id": str(users["Navya Rupawat"].id),
        },
    )
    events = db.query(AuditEvent).all()
    assert len(events) == before + 1
    event = events[-1]
    assert event.action == "USER_CREATED"
    assert event.actor_user_id == users["Shail Patel"].id
    # A password hash must never reach the audit trail.
    assert "hashed_password" not in (event.after or {})


# ------------------------------------------------------ permanent delete
def test_an_account_with_no_history_can_be_deleted(client, users, db) -> None:
    """The case this exists for: an account created by mistake."""
    from app.models.org import User

    headers = super_admin_headers(client)
    created = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Typo Person",
            "email": new_email(),
            "password": "Temp@2026abc",
            "role": Role.BDE,
        },
    ).json()

    response = client.delete(f"/api/users/{created['id']}/permanent", headers=headers)
    assert response.status_code == 200, response.text
    assert "permanently deleted" in response.json()["message"]
    assert db.get(User, uuid.UUID(created["id"])) is None


def test_deleting_somebody_with_history_is_refused_with_the_reason(
    client, users
) -> None:
    """Never let "delete" quietly become "erase who did this".

    Most of the columns pointing at a user are ON DELETE SET NULL, so the
    database would happily blank the owner of every lead they worked. The
    refusal is what stands between that and a Super Admin in a hurry.
    """
    response = client.delete(
        f"/api/users/{users['Parth Fulvani'].id}/permanent",
        headers=super_admin_headers(client),
    )
    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["blockers"], "the refusal must say what is attached"
    # And it names tables, so the reason is actionable rather than a shrug.
    assert all({"table", "column", "rows"} <= set(row) for row in details["blockers"])
    assert "Deactivate them instead" in response.json()["error"]["message"]


def test_only_a_super_admin_can_delete_permanently(client, users) -> None:
    """A plain Admin may deactivate. Erasing an account is a bigger act."""
    created = client.post(
        "/api/users",
        headers=super_admin_headers(client),
        json={
            "name": "Not Yours",
            "email": new_email(),
            "password": "Temp@2026abc",
            "role": Role.BDE,
        },
    ).json()

    response = client.delete(
        f"/api/users/{created['id']}/permanent",
        headers=sign_in(client, users["Shail Patel"]),
    )
    assert response.status_code == 403, response.text


def test_a_super_admin_cannot_delete_themselves(client, users) -> None:
    response = client.delete(
        f"/api/users/{users['Portal Owner'].id}/permanent",
        headers=super_admin_headers(client),
    )
    assert response.status_code == 403


def test_deleting_a_user_does_not_touch_anybody_elses_rows(client, users, db) -> None:
    """The blocker check is discovered from the schema, so a table added later
    is covered. This pins the outcome that matters: nothing else moves."""
    from app.models.lead import Lead
    from app.models.org import User

    headers = super_admin_headers(client)
    before_leads = db.scalar(select(func.count(Lead.id)))
    before_users = db.scalar(select(func.count(User.id)))

    created = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Ephemeral",
            "email": new_email(),
            "password": "Temp@2026abc",
            "role": Role.BDE,
        },
    ).json()
    client.delete(f"/api/users/{created['id']}/permanent", headers=headers)

    assert db.scalar(select(func.count(Lead.id))) == before_leads
    assert db.scalar(select(func.count(User.id))) == before_users


def test_an_explicit_null_password_generates_one(client, users) -> None:
    """The browser sends `new_password: null`, not an absent key.

    Those are the same intent and must behave the same. They did not: the
    length constraint sat outside the optional, so pydantic rejected null as
    "not a valid string" and the dialog failed with a 422.
    """
    response = client.post(
        f"/api/users/{users['Shivani Patel'].id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": None, "must_change": True},
    )
    assert response.status_code == 200, response.text
    assert "password" not in response.json(), "a credential came back in the response"


def test_a_too_short_password_is_still_refused(client, users) -> None:
    """Making the field optional must not make it lax."""
    response = client.post(
        f"/api/users/{users['Shivani Patel'].id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": "short"},
    )
    assert response.status_code == 422


def test_a_duplicate_email_is_refused_with_a_readable_message(client, users) -> None:
    """No raw database error reaches the screen."""
    taken = users["Parth Fulvani"].email
    response = client.patch(
        f"/api/users/{users['Shivani Patel'].id}",
        headers=super_admin_headers(client),
        json={"email": taken},
    )
    assert response.status_code == 409, response.text
    message = response.json()["error"]["message"]
    assert "already in use" in message.lower()
    for leak in ("UNIQUE constraint", "sqlite", "Traceback", "SELECT"):
        assert leak.lower() not in response.text.lower()


def test_super_admin_can_change_an_email_and_the_account_moves_with_it(
    client, users, db
) -> None:
    """Credentials change; authorization does not."""
    from app.models.org import User

    target = users["Shivani Patel"]
    before = (target.role, target.team_id, target.manager_id, target.is_active)
    old_email = target.email
    fresh_email = new_email()

    response = client.patch(
        f"/api/users/{target.id}", headers=super_admin_headers(client),
        json={"email": fresh_email},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    stored = db.get(User, target.id)
    assert stored.email == fresh_email
    assert (stored.role, stored.team_id, stored.manager_id, stored.is_active) == before

    assert client.post("/api/auth/login",
                       json={"email": old_email, "password": SEED_PASSWORD}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"email": fresh_email, "password": SEED_PASSWORD}).status_code == 200


def test_a_field_user_cannot_edit_or_reset_anybody(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    target = users["Shivani Patel"]
    assert client.patch(f"/api/users/{target.id}", headers=headers,
                        json={"email": "hijack@pouchwale.com"}).status_code in (403, 404)
    assert client.post(f"/api/users/{target.id}/reset-password", headers=headers,
                       json={"new_password": "Hijack@2026x"}).status_code in (403, 404)


def test_no_user_endpoint_ever_exposes_a_password_or_hash(client, users) -> None:
    headers = super_admin_headers(client)
    for path in ("/api/users?page_size=50", f"/api/users/{users['Parth Fulvani'].id}"):
        text = client.get(path, headers=headers).text
        for leak in ("hashed_password", "password_hash", "$2b$"):
            assert leak not in text, f"{path} leaked {leak}"
