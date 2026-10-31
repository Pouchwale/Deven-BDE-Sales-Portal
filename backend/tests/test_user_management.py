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
    session_secret,
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
    headers = auth(session_secret(login))

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


def test_user_list_puts_the_super_admin_first_then_names_in_order(client, users) -> None:
    items = client.get(
        "/api/users?page_size=200", headers=super_admin_headers(client)
    ).json()["items"]
    assert items[0]["role"] == Role.SUPER_ADMIN
    rest = [i["name"] for i in items if i["role"] != Role.SUPER_ADMIN]
    assert rest == sorted(rest)


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
            "password": "Onboard@2026x",
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
            "password": "Onboard@2026x",
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
            "password": "Onboard@2026x",
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
            "password": "Onboard@2026x",
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
            "password": "Onboard@2026x",
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
            "password": "Onboard@2026x",
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


def test_an_omitted_password_is_refused(client, users) -> None:
    """The server never invents a password, so it never holds a plaintext it
    would have to hand back. Omitting one is a 422 and changes nothing."""
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
    assert response.status_code == 422, response.text
    assert "KnownFirst" not in response.text

    still = client.post(
        "/api/auth/login", json={"email": target.email, "password": "KnownFirst@2026"}
    )
    assert still.status_code == 200, "a refused reset must not rotate the password"


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


# ------------------------------------------------- removing somebody: delete
# Deactivation was removed (owner's request, 2026-10-30). The only way to
# remove a person is DELETE /api/users/{id}, Super Admin only, which hands
# whatever they own to somebody else first. Older accounts may still be
# inactive in the data; those are reproduced by setting the flag directly.
def _mark_inactive(db, user) -> None:
    user.is_active = False
    db.commit()


def test_delete_without_a_hand_over_is_refused_and_names_the_work(client, users) -> None:
    response = client.delete(
        f"/api/users/{users['Navya Rupawat'].id}", headers=super_admin_headers(client)
    )
    assert response.status_code == 409, response.text
    handover = {
        f"{row['table']}.{row['column']}": row["rows"]
        for row in response.json()["error"]["details"]["handover"]
    }
    assert handover["users.manager_id"] == 4
    assert users["Navya Rupawat"].is_active is True


def test_deleting_a_manager_hands_reports_and_work_to_the_chosen_person(
    client, users, db
) -> None:
    from app.models.lead import Lead
    from app.models.org import User

    navya, ramanesh = users["Navya Rupawat"], users["Ramanesh Nair"]
    lead = client.post(
        "/api/leads",
        headers=sign_in(client, users["Shail Patel"]),
        json={"name": "Handover Ltd", "mobile": "9800000732", "assigned_to_user_id": str(navya.id)},
    )
    assert lead.status_code in (200, 201), lead.text

    preview = client.get(f"/api/users/{navya.id}/deletion", headers=super_admin_headers(client))
    assert preview.status_code == 200 and preview.json()["handover"]

    response = client.delete(
        f"/api/users/{navya.id}",
        headers=super_admin_headers(client),
        params={"hand_over_to": str(ramanesh.id)},
    )
    assert response.status_code == 200, response.text
    assert "Ramanesh Nair" in response.json()["message"]

    db.expire_all()
    assert db.get(User, navya.id) is None
    assert db.get(User, users["Parth Fulvani"].id).manager_id == ramanesh.id
    assert db.get(Lead, uuid.UUID(lead.json()["id"])).assigned_to_user_id == ramanesh.id


def test_history_keeps_the_deleted_persons_name(client, users, db) -> None:
    from app.models.system import AuditEvent

    parth = users["Parth Fulvani"]
    sign_in(client, parth)  # a sign-in writes an audit row with Parth as actor
    response = client.delete(
        f"/api/users/{parth.id}",
        headers=super_admin_headers(client),
        params={"hand_over_to": str(users["Muskan Makhija"].id)},
    )
    assert response.status_code == 200, response.text
    named = db.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.actor_name == "Parth Fulvani")
    )
    assert named and named > 0


def test_every_link_to_a_user_is_handled_by_delete() -> None:
    """Each foreign key to users is handed over, keeps a name, is cleared, or
    goes with the person - so a table added later cannot lose its owner
    without somebody deciding which of the four it is."""
    from app.db.base import Base
    from app.services import users as user_service

    classified = set(
        user_service.HANDED_OVER
        + user_service.NAMED_HISTORY
        + user_service.CLEARED
        + user_service.REMOVED_WITH_THEM
    )
    found = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if any(fk.column.table.name == "users" for fk in column.foreign_keys)
    }
    assert found == classified, found ^ classified


def test_edit_can_no_longer_deactivate(client, users) -> None:
    target = users["Bimal"]
    response = client.patch(
        f"/api/users/{target.id}",
        headers=sign_in(client, users["Shail Patel"]),
        json={"is_active": False, "title": "Still here"},
    )
    assert response.status_code in (200, 422), response.text
    assert target.is_active is True


def test_deactivate_and_reactivate_routes_are_gone(client, users) -> None:
    headers = super_admin_headers(client)
    target = users["Bimal"]
    assert client.post(f"/api/users/{target.id}/reactivate", headers=headers).status_code in (404, 405)
    assert client.delete(f"/api/users/{target.id}/permanent", headers=headers).status_code in (404, 405)


def test_an_inactive_account_cannot_sign_in(client, users, db) -> None:
    target = users["Bimal"]
    _mark_inactive(db, target)
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


def test_an_inactive_account_drops_out_of_every_headcount(client, users, db) -> None:
    """One flag, every list: an inactive account from before deactivation was
    removed is counted nowhere until somebody deletes it."""
    target = users["Bimal"]
    headers = sign_in(client, users["Shail Patel"])

    before_dash = client.get("/api/dashboard", headers=headers).json()
    assert target.name in [row["name"] for row in before_dash["reports"]]

    _mark_inactive(db, target)

    dashboard = client.get("/api/dashboard", headers=headers).json()
    assert target.name not in [row["name"] for row in dashboard["reports"]]
    assert all(row["is_active"] for row in dashboard["reports"])

    chart = client.get("/api/users/org-chart", headers=headers).json()
    assert target.name not in _org_names(chart)

    roster = client.get("/api/users", headers=headers, params={"page_size": 200}).json()
    assert target.name not in [row["name"] for row in roster["items"]]


def test_inactive_accounts_can_still_be_listed_so_they_can_be_deleted(
    client, users, db
) -> None:
    target = users["Bimal"]
    headers = sign_in(client, users["Shail Patel"])
    _mark_inactive(db, target)

    roster = client.get(
        "/api/users",
        headers=headers,
        params={"page_size": 200, "include_inactive": True},
    ).json()
    row = next(r for r in roster["items"] if r["name"] == target.name)
    assert row["is_active"] is False


def test_deleting_somebody_moves_their_pipeline_with_them(client, users, db) -> None:
    """The team's pipeline follows the person taking over; nothing is lost."""
    from app.models.lead import Lead

    target, receiver = users["Parth Fulvani"], users["Muskan Makhija"]
    client.post(
        "/api/leads",
        headers=sign_in(client, users["Navya Rupawat"]),
        json={"name": "Leaver Pipeline Ltd", "mobile": "9800000731", "assigned_to_user_id": str(target.id)},
    )
    db.expire_all()
    total_before = db.scalar(select(func.count(Lead.id)))
    theirs = db.scalar(select(func.count(Lead.id)).where(Lead.assigned_to_user_id == target.id))
    receivers = db.scalar(select(func.count(Lead.id)).where(Lead.assigned_to_user_id == receiver.id))
    assert theirs > 0

    response = client.delete(
        f"/api/users/{target.id}",
        headers=super_admin_headers(client),
        params={"hand_over_to": str(receiver.id)},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.scalar(select(func.count(Lead.id))) == total_before
    assert db.scalar(
        select(func.count(Lead.id)).where(Lead.assigned_to_user_id == receiver.id)
    ) == receivers + theirs


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

    # ...and not through the admin route either: nobody may act on themselves,
    # and the last active Super Admin is refused with that specific reason.
    response = client.patch(
        f"/api/users/{owner.id}",
        headers=sign_in(client, owner),
        json={"role": "BDE"},
    )
    assert response.status_code in (403, 409)
    assert owner.role == Role.SUPER_ADMIN


# ---------------------------------------------------------- audit trail
def test_every_gated_mutation_writes_an_audit_row(client, db, users) -> None:
    from app.models.system import AuditEvent

    # Signed in first: a sign-in writes its own audit row now.
    headers = sign_in(client, users["Shail Patel"])
    before = db.query(AuditEvent).count()
    client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Audited Hire",
            "email": new_email(),
            "password": "Onboard@2026x",
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


# ---------------------------------------------- delete: accounts with no work
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

    response = client.delete(f"/api/users/{created['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert "was deleted" in response.json()["message"]
    assert db.get(User, uuid.UUID(created["id"])) is None


def test_a_session_row_alone_does_not_block_deletion(client, users, db) -> None:
    """user_sessions is sign-in bookkeeping (ON DELETE CASCADE), not work.
    Found by the e2e run: a session made an account undeletable."""
    from app.core import sessions as session_store
    from app.models.org import User
    from app.services import users as user_service

    headers = super_admin_headers(client)
    created = client.post(
        "/api/users",
        headers=headers,
        json={
            "name": "Session Only",
            "email": new_email(),
            "password": "Kq7vRt2mWx9p",
            "confirm_password": "Kq7vRt2mWx9p",
            "role": Role.BDE,
        },
    ).json()
    target = db.get(User, uuid.UUID(created["id"]))
    session_store.create_session(db, target)
    db.flush()

    assert user_service.handover_counts(db, target) == []
    user_service.delete_user(db, users["Portal Owner"], target)
    db.flush()
    assert db.get(User, target.id) is None


def test_only_a_super_admin_can_delete(client, users) -> None:
    """Deleting is the only way to remove somebody, and it is the Super Admin's alone."""
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
        f"/api/users/{created['id']}",
        headers=sign_in(client, users["Shail Patel"]),
    )
    assert response.status_code == 403, response.text


def test_a_super_admin_cannot_delete_themselves(client, users) -> None:
    response = client.delete(
        f"/api/users/{users['Portal Owner'].id}",
        headers=super_admin_headers(client),
    )
    # 409 when they are the last active Super Admin, 403 (self) otherwise.
    assert response.status_code in (403, 409)


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
    client.delete(f"/api/users/{created['id']}", headers=headers)

    assert db.scalar(select(func.count(Lead.id))) == before_leads
    assert db.scalar(select(func.count(User.id))) == before_users


def test_an_explicit_null_password_is_refused(client, users) -> None:
    """`new_password: null` and an absent key are the same intent: no
    password. Both are refused - nothing is generated server-side."""
    response = client.post(
        f"/api/users/{users['Shivani Patel'].id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": None, "must_change": True},
    )
    assert response.status_code == 422, response.text


def test_an_empty_password_is_still_refused(client, users) -> None:
    """No length rule any more, but a blank password is not a password."""
    response = client.post(
        f"/api/users/{users['Shivani Patel'].id}/reset-password",
        headers=sign_in(client, users["Shail Patel"]),
        json={"new_password": ""},
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
