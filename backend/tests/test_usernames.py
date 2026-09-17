"""Signing in with a short username ("navya", "superadmin") instead of the
full email address, and administering those usernames."""
from __future__ import annotations

from sqlalchemy import select

from app.core.constants import Role
from app.core.usernames import normalise_login, suggest_username
from app.models.org import User
from app.models.system import AuditEvent
from tests.conftest import SEED_PASSWORD, new_email, sign_in, super_admin_headers

GOOD_PASSWORD = "Harbour7Lantern"
GENERIC = "Incorrect username or password."


def login(client, identifier: str, password: str = SEED_PASSWORD, field: str = "identifier"):
    return client.post("/api/auth/login", json={field: identifier, "password": password})


# ------------------------------------------------------------ seeded names
def test_seed_gives_every_account_a_username(db) -> None:
    rows = db.execute(select(User.name, User.username, User.role)).all()
    assert rows and all(username for _, username, _ in rows)
    by_name = {name: username for name, username, _ in rows}
    assert by_name["Navya Rupawat"] == "navya"
    assert by_name["Shail Patel"] == "shail"
    assert by_name["Shailesh Prajapati"] == "shailesh"
    assert [u for _, u, role in rows if role == Role.SUPER_ADMIN][0] == "superadmin"


# ------------------------------------------------------------------ sign in
def test_sign_in_with_username(client, users) -> None:
    response = login(client, "navya")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == users["Navya Rupawat"].email
    assert response.json()["user"]["username"] == "navya"


def test_username_ignores_case_and_spaces(client, users) -> None:
    assert login(client, "  NAVYA ").status_code == 200
    assert login(client, "Super Admin").status_code == 200
    assert login(client, "superadmin").status_code == 200


def test_email_still_signs_in_and_old_field_name_still_works(client, users) -> None:
    email = users["Parth Fulvani"].email
    assert login(client, email).status_code == 200
    assert login(client, email.upper(), field="email").status_code == 200
    assert login(client, "parth", field="username").status_code == 200


def test_unknown_username_and_wrong_password_look_identical(client, users) -> None:
    unknown = login(client, "nobody-here")
    wrong = login(client, "navya", "Wrong-password-9")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"] == GENERIC


def test_username_and_email_share_one_lockout(client, users, db) -> None:
    navya = users["Navya Rupawat"]
    for i in range(5):
        identifier = "navya" if i % 2 else navya.email
        assert login(client, identifier, "Wrong-password-9").status_code == 401
    # Locked: the right password is refused whichever name is typed.
    assert login(client, "navya").status_code == 401
    assert login(client, navya.email).status_code == 401


def test_deactivated_account_cannot_sign_in_by_username(client, users, db) -> None:
    kevin = users["Kevin"]
    kevin.is_active = False
    db.commit()
    assert login(client, "kevin").status_code == 401


# ------------------------------------------------------------ administration
def _create(client, headers, **overrides):
    body = {"name": "Riya Mehta", "email": new_email(), "password": GOOD_PASSWORD, "role": "BDE"}
    body.update(overrides)
    return client.post("/api/users", headers=headers, json=body)


def test_new_user_gets_a_username_from_their_first_name(client, users) -> None:
    created = _create(client, super_admin_headers(client))
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "riya"
    assert login(client, "riya", GOOD_PASSWORD).status_code == 200


def test_a_clashing_first_name_falls_back_to_a_longer_form(client, users) -> None:
    created = _create(
        client, super_admin_headers(client), name="Navya Kapoor", email="navya.kapoor@example.com"
    )
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "navya.kapoor"
    # The original Navya still owns "navya".
    assert login(client, "navya").json()["user"]["email"] == users["Navya Rupawat"].email


def test_explicit_username_is_validated_and_unique(client, users) -> None:
    headers = super_admin_headers(client)
    assert _create(client, headers, username="bad name!").status_code == 422
    assert _create(client, headers, username="a@b").status_code == 422
    assert _create(client, headers, username="NAVYA").status_code == 409
    ok = _create(client, headers, username="Riya.M")
    assert ok.status_code == 201, ok.text
    assert ok.json()["username"] == "riya.m"


def test_admin_can_rename_a_username_and_it_is_audited(client, users, db) -> None:
    headers = super_admin_headers(client)
    parth = users["Parth Fulvani"]
    response = client.patch(f"/api/users/{parth.id}", headers=headers, json={"username": "parthf"})
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "parthf"
    assert login(client, "parth").status_code == 401
    assert login(client, "parthf").status_code == 200

    event = db.execute(
        select(AuditEvent)
        .where(AuditEvent.entity_id == parth.id, AuditEvent.action == "USER_UPDATED")
        .order_by(AuditEvent.created_at.desc())
    ).scalars().first()
    assert event is not None and event.after == {"username": "parthf"}


def test_rename_to_a_taken_username_is_refused(client, users) -> None:
    headers = super_admin_headers(client)
    response = client.patch(
        f"/api/users/{users['Parth Fulvani'].id}", headers=headers, json={"username": "shail"}
    )
    assert response.status_code == 409


def test_employee_cannot_change_a_username(client, users) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    response = client.patch(
        f"/api/users/{users['Muskan Makhija'].id}", headers=headers, json={"username": "x1"}
    )
    assert response.status_code in (403, 404)


# ------------------------------------------------------------------ helpers
def test_normalise_login() -> None:
    assert normalise_login(" Super Admin ") == "superadmin"
    assert normalise_login("Navya.Rupawat@Pouchwale.com ") == "navya.rupawat@pouchwale.com"


def test_suggestion_never_collides(db, users) -> None:
    suggested = suggest_username(db, name="Navya", email="navya@pouchwale.com", role="BDE")
    assert suggested not in {"navya"}
    assert db.execute(select(User).where(User.username == suggested)).first() is None
