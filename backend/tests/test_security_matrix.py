"""The authorization matrix, end to end over HTTP.

Every row here is a request somebody could actually send: a role against a
screen, one person's session against another person's record, a legal and an
illegal pipeline move. Nothing is asserted against a service function in
isolation, because the question being answered is "what does the API let this
caller do", and only the API can answer that.

The org is the seeded roster (app/seeds/roster.py). The roster has no SALES
role account - its sales people are BDE-ranked - so one of them is switched to
SALES for the duration of a test. The transaction rolls that back.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import authority
from app.core.constants import (
    ALLOWED_LEAD_TRANSITIONS,
    AuditAction,
    LeadStatus,
    Role,
)
from app.main import app as fastapi_app
from app.models.chat import ChatConversation
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.system import AuditEvent, Notification
from tests.conftest import eligible_lead, sign_in, super_admin_headers

# ------------------------------------------------------------------ setup
#: Routes that are reachable without a session, on purpose.
PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("GET", "/api/health/db"),
    ("POST", "/api/auth/login"),
    # Ending a session you do not have is a no-op, not an error.
    ("POST", "/api/auth/logout"),
    # Its own door: an HMAC signature, tested in test_feedback_sync.py.
    ("POST", "/api/feedback/sync/webhook"),
}


def _lead(client, headers, assignee, name) -> dict:
    response = client.post(
        "/api/leads",
        headers=headers,
        json={"name": name, "mobile": None, "assigned_to_user_id": str(assignee.id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def world(client, users, db):
    """Three leads in three places in the tree, and a session per role."""
    parag = users["Parag Sharma"]
    parag.role = Role.SALES  # a real SALES account for the matrix
    db.commit()

    navya = sign_in(client, users["Navya Rupawat"])
    shailesh = sign_in(client, users["Shailesh Prajapati"])

    headers = {
        "super_admin": super_admin_headers(client),
        "admin": sign_in(client, users["Shail Patel"]),
        "bde_manager": navya,
        "sales_manager": shailesh,
        "sales_head": sign_in(client, users["Ramanesh Nair"]),
        "bde": sign_in(client, users["Parth Fulvani"]),
        "other_bde": sign_in(client, users["Muskan Makhija"]),
        "sales": sign_in(client, parag),
    }
    leads = {
        "parth": _lead(client, navya, users["Parth Fulvani"], "Matrix Parth Co"),
        "muskan": _lead(client, navya, users["Muskan Makhija"], "Matrix Muskan Co"),
        "parag": _lead(client, shailesh, parag, "Matrix Parag Co"),
    }
    return {"h": headers, "leads": leads, "users": users}


# ------------------------------------------------- 1. nobody signed in
def _concrete(path: str) -> str:
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        out = out[:start] + str(uuid.uuid4()) + out[end + 1:]
    return out


def test_every_non_public_route_requires_a_session(db) -> None:
    """Enumerated from the app itself, so a route added tomorrow is covered."""
    from app.db.session import get_db

    fastapi_app.dependency_overrides[get_db] = lambda: db
    checked = 0
    try:
        # A fresh client: the shared fixture's cookie jar may hold a session
        # from an earlier sign-in, which would make this test meaningless.
        with TestClient(fastapi_app) as anonymous:
            for route in fastapi_app.routes:
                if not isinstance(route, APIRoute):
                    continue
                for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                    if (method, route.path) in PUBLIC_ROUTES:
                        continue
                    response = anonymous.request(method, _concrete(route.path))
                    assert response.status_code == 401, (
                        f"{method} {route.path} -> {response.status_code}"
                    )
                    checked += 1
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
    assert checked >= 80


def test_a_garbage_bearer_token_is_refused(client) -> None:
    response = client.get(
        "/api/leads", headers={"Authorization": "Bearer not-a-real-session"}
    )
    assert response.status_code == 401


# ------------------------------------------------------ 2. role x screen
ALL_ROLES = ("super_admin", "admin", "bde_manager", "sales_manager", "bde", "sales")

#: (method, path, roles that get 200). Everyone else must get 403.
ROLE_MATRIX = [
    ("GET", "/api/dashboard", ALL_ROLES),
    ("GET", "/api/leads", ALL_ROLES),
    ("GET", "/api/leads/stats", ALL_ROLES),
    ("GET", "/api/work-queue", ALL_ROLES),
    ("GET", "/api/references", ALL_ROLES),
    ("GET", "/api/references/stats", ALL_ROLES),
    ("GET", "/api/references/accounts", ALL_ROLES),
    ("GET", "/api/feedback", ALL_ROLES),
    ("GET", "/api/feedback/analysis", ALL_ROLES),
    ("GET", "/api/feedback/pending", ALL_ROLES),
    ("GET", "/api/notifications", ALL_ROLES),
    ("GET", "/api/chat/status", ALL_ROLES),
    ("GET", "/api/chat/conversations", ALL_ROLES),
    ("GET", "/api/users", ("super_admin", "admin", "bde_manager", "sales_manager")),
    ("GET", "/api/users/org-chart", ("super_admin", "admin", "bde_manager", "sales_manager")),
    ("GET", "/api/users/actionable", ("super_admin", "admin", "bde_manager", "sales_manager")),
    ("GET", "/api/admin/settings", ("super_admin", "admin")),
    ("GET", "/api/feedback/imports", ("super_admin", "admin")),
    ("GET", "/api/feedback/sync/status", ("super_admin", "admin")),
    ("GET", "/api/post-sale/status", ("super_admin", "admin")),
    ("GET", "/api/post-sale/unmatched", ("super_admin", "admin")),
    ("GET", "/api/admin/audit", ("super_admin",)),
    ("GET", "/api/admin/sap-sync/status", ("super_admin",)),
    ("GET", "/api/customers", ("super_admin",)),
    ("GET", "/api/customers/stats", ("super_admin",)),
]


@pytest.mark.parametrize("method,path,allowed", ROLE_MATRIX)
def test_role_matrix(client, world, method, path, allowed) -> None:
    for role in ALL_ROLES:
        response = client.request(method, path, headers=world["h"][role])
        expected = 200 if role in allowed else 403
        assert response.status_code == expected, (
            f"{role} {method} {path} -> {response.status_code}: {response.text[:200]}"
        )


def test_only_admins_see_assistant_setup_and_nobody_sees_the_key(
    client, world, monkeypatch
) -> None:
    from app.core.config import settings

    secret = "gsk_matrix_secret_value_1234567890"
    monkeypatch.setattr(settings, "GROQ_API_KEY", secret, raising=False)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True, raising=False)
    settings.__dict__.pop("chat_enabled", None)
    try:
        for role in ALL_ROLES:
            response = client.get("/api/chat/status", headers=world["h"][role])
            assert response.status_code == 200
            assert "gsk_" not in response.text and secret[-10:] not in response.text
            setup = response.json()["setup"]
            if role in ("super_admin", "admin"):
                assert setup["has_api_key"] is True
            else:
                assert setup is None
        settings_body = client.get("/api/admin/settings", headers=world["h"]["admin"]).text
        assert "gsk_" not in settings_body and secret[-10:] not in settings_body
    finally:
        settings.__dict__.pop("chat_enabled", None)


# -------------------------------------------------------- 3. data scope
def _visible_lead_ids(client, headers) -> tuple[set[str], int]:
    body = client.get("/api/leads?page_size=200", headers=headers).json()
    return {row["id"] for row in body["items"]}, body["total"]


@pytest.mark.parametrize(
    "role,sees,hidden",
    [
        ("super_admin", {"parth", "muskan", "parag"}, set()),
        ("admin", {"parth", "muskan", "parag"}, set()),
        ("bde_manager", {"parth", "muskan"}, {"parag"}),
        ("sales_manager", {"parag"}, {"parth", "muskan"}),
        # Two levels above Parag: the subtree is any depth.
        ("sales_head", {"parag"}, {"parth", "muskan"}),
        ("bde", {"parth"}, {"muskan", "parag"}),
        ("other_bde", {"muskan"}, {"parth", "parag"}),
        ("sales", {"parag"}, {"parth", "muskan"}),
    ],
)
def test_lead_list_scope(client, world, role, sees, hidden) -> None:
    ids, total = _visible_lead_ids(client, world["h"][role])
    for key in sees:
        assert world["leads"][key]["id"] in ids, f"{role} should see {key}"
    for key in hidden:
        assert world["leads"][key]["id"] not in ids, f"{role} must not see {key}"
    stats = client.get("/api/leads/stats", headers=world["h"][role]).json()
    assert stats["total"] == total


def test_a_field_user_only_ever_gets_their_own_rows(client, world) -> None:
    parth = world["users"]["Parth Fulvani"]
    body = client.get("/api/leads?page_size=200", headers=world["h"]["bde"]).json()
    assert body["items"] and all(
        row["assigned_to_user_id"] == str(parth.id) for row in body["items"]
    )
    # A filter naming somebody else narrows inside the scope; it never widens it.
    muskan = world["users"]["Muskan Makhija"]
    body = client.get(
        f"/api/leads?assigned_to={muskan.id}", headers=world["h"]["bde"]
    ).json()
    assert body["total"] == 0


def test_manager_team_view_is_their_own_subtree(client, world, db) -> None:
    navya = world["users"]["Navya Rupawat"]
    scope = authority.visible_user_ids(db, navya)
    body = client.get("/api/users?page_size=200", headers=world["h"]["bde_manager"]).json()
    assert body["items"]
    assert {uuid.UUID(row["id"]) for row in body["items"]} <= scope
    dashboard = client.get("/api/dashboard", headers=world["h"]["bde_manager"]).json()
    assert {row["user_id"] for row in dashboard["reports"]} <= {str(i) for i in scope}


# --------------------------------------------------- 4. cross-account
def _lead_attacks(lead_id: str) -> list[tuple[str, str, dict | None]]:
    return [
        ("GET", f"/api/leads/{lead_id}", None),
        ("PATCH", f"/api/leads/{lead_id}", {"name": "Hijacked"}),
        ("POST", f"/api/leads/{lead_id}/status", {"status": "CONTACTED", "remark": "x"}),
        ("POST", f"/api/leads/{lead_id}/activities", {"activity_type": "CALL", "remark": "x"}),
        ("GET", f"/api/leads/{lead_id}/message?channel=WHATSAPP", None),
        ("POST", f"/api/leads/{lead_id}/message/sent", {"channel": "WHATSAPP"}),
    ]


@pytest.mark.parametrize(
    "attacker,victim",
    [
        ("bde", "muskan"),          # BDE -> another BDE
        ("bde", "parag"),           # BDE -> Sales
        ("sales", "parth"),         # Sales -> BDE
        ("bde_manager", "parag"),   # manager -> sideways team
        ("sales_manager", "parth"),
    ],
)
def test_cross_account_lead_access_is_invisible(client, world, db, attacker, victim) -> None:
    lead = world["leads"][victim]
    for method, path, body in _lead_attacks(lead["id"]):
        response = client.request(method, path, headers=world["h"][attacker], json=body)
        # 404, not 403: an id outside your scope must not be confirmed to exist.
        # 422 only where the body is rejected before the lookup runs.
        assert response.status_code in (404, 422), (
            f"{attacker} {method} {path} -> {response.status_code}"
        )
        assert response.status_code != 200
    row = db.get(Lead, uuid.UUID(lead["id"]))
    db.refresh(row)
    assert row.name != "Hijacked" and row.status == "NEW"


def test_reference_on_someone_elses_lead_is_invisible(client, world) -> None:
    response = client.post(
        "/api/references",
        headers=world["h"]["bde"],
        json={
            "lead_id": world["leads"]["muskan"]["id"],
            "outcome": "YES",
            "referred_name": "A Friend",
        },
    )
    assert response.status_code == 404


def test_reference_cannot_be_credited_to_somebody_you_cannot_act_on(client, users, db) -> None:
    lead = eligible_lead(client, users, name="Credit Probe Co")
    response = client.post(
        "/api/references",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={
            "lead_id": lead["id"],
            "outcome": "YES",
            "referred_name": "A Friend",
            "requested_by_user_id": str(users["Muskan Makhija"].id),
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "actor,assignee,expected",
    [
        ("bde_manager", "Parag Sharma", 403),        # outside the chain
        ("sales_manager", "Ramanesh Nair", 403),     # above the actor
        ("bde", "Muskan Makhija", 403),              # a peer
        ("bde_manager", "Muskan Makhija", 200),      # own report
    ],
)
def test_reassignment_targets_are_validated(client, world, actor, assignee, expected) -> None:
    lead_key = "parag" if actor == "sales_manager" else "parth"
    response = client.patch(
        f"/api/leads/{world['leads'][lead_key]['id']}",
        headers=world["h"][actor],
        json={"assigned_to_user_id": str(world["users"][assignee].id)},
    )
    assert response.status_code == expected, response.text


def test_a_manager_cannot_create_a_lead_for_somebody_outside_their_chain(client, world) -> None:
    response = client.post(
        "/api/leads",
        headers=world["h"]["sales_manager"],
        json={"name": "Wrong team", "assigned_to_user_id": str(world["users"]["Parth Fulvani"].id)},
    )
    assert response.status_code == 403


def test_customer_outside_scope_is_blocked(client, world, db) -> None:
    navya = world["users"]["Navya Rupawat"]
    scope = authority.visible_user_ids(db, navya)
    outside = db.execute(
        select(Customer).where(
            (Customer.owner_user_id.is_(None)) | (Customer.owner_user_id.not_in(scope))
        )
    ).scalars().first()
    if outside is None:
        pytest.skip("the fixture SAP book has no customer outside this scope")
    h = world["h"]["bde_manager"]
    # Browsing the book is Super Admin only...
    assert client.get(f"/api/customers/{outside.id}", headers=h).status_code == 403
    # ...and the timeline routes that stay open are scoped: invisible, not denied.
    assert client.get(f"/api/customers/{outside.id}/timeline", headers=h).status_code == 404
    assert client.post(
        f"/api/customers/{outside.id}/timeline",
        headers=h,
        json={"activity_type": "NOTE", "remark": "probe"},
    ).status_code == 404
    assert client.get(
        f"/api/customers/{outside.id}/message?purpose=FEEDBACK&channel=WHATSAPP", headers=h
    ).status_code == 404


def test_employees_cannot_manage_users(client, world) -> None:
    muskan = world["users"]["Muskan Makhija"]
    for role in ("bde", "sales"):
        h = world["h"][role]
        assert client.post(
            "/api/users",
            headers=h,
            json={"name": "Sneaky", "email": "sneaky@example.com", "role": "BDE"},
        ).status_code in (403, 422)
        assert client.post(
            "/api/users",
            headers=h,
            json={
                "name": "Sneaky Person",
                "email": "sneaky@example.com",
                "role": "BDE",
                "password": "Sneaky@Pass123",
            },
        ).status_code == 403
        assert client.post(
            f"/api/users/{muskan.id}/reset-password", headers=h, json={}
        ).status_code in (403, 422)
        assert client.delete(f"/api/users/{muskan.id}", headers=h).status_code == 403
        assert client.post(f"/api/users/{muskan.id}/unlock", headers=h).status_code == 403
        assert client.patch(
            f"/api/users/{muskan.id}", headers=h, json={"name": "Renamed"}
        ).status_code == 403
        # Another user's profile.
        assert client.get(f"/api/users/{muskan.id}", headers=h).status_code in (403, 404)
        assert client.get(f"/api/users/{muskan.id}/activity", headers=h).status_code in (403, 404)


def test_a_manager_cannot_open_a_profile_outside_their_chain(client, world) -> None:
    parag = world["users"]["Parag Sharma"]
    assert client.get(
        f"/api/users/{parag.id}", headers=world["h"]["bde_manager"]
    ).status_code == 404


def test_employees_cannot_import_or_administer(client, world) -> None:
    upload = {"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
    for role in ("bde", "sales", "bde_manager"):
        h = world["h"][role]
        assert client.post("/api/feedback/import/dry-run", headers=h, files=upload).status_code == 403
        assert client.post("/api/feedback/import/commit", headers=h, files=upload).status_code == 403
        assert client.post("/api/admin/sap-import", headers=h).status_code == 403
        assert client.post("/api/admin/sap-import/upload", headers=h, files=upload).status_code == 403
        assert client.get("/api/admin/sap-sync/status", headers=h).status_code == 403
        assert client.post(
            "/api/post-sale/sync", headers=h, json={"rows": [{"customer_name": "x"}]}
        ).status_code == 403
        assert client.post("/api/feedback/sync/run", headers=h).status_code == 403
        assert client.post("/api/feedback/evaluate-alerts", headers=h).status_code == 403
        assert client.get("/api/admin/audit", headers=h).status_code == 403
        assert client.patch("/api/admin/settings", headers=h, json={"values": {}}).status_code == 403
        assert client.post("/api/customers/backfill-feedback-links", headers=h).status_code == 403


def test_notifications_are_private(client, world, db) -> None:
    muskan = world["users"]["Muskan Makhija"]
    theirs = db.execute(
        select(Notification).where(Notification.user_id == muskan.id)
    ).scalars().first()
    assert theirs is not None and theirs.is_read is False

    h = world["h"]["bde"]
    listed = client.get("/api/notifications?page_size=100", headers=h).json()
    assert str(theirs.id) not in {row["id"] for row in listed["items"]}

    response = client.post(f"/api/notifications/{theirs.id}/read", headers=h)
    assert response.status_code == 200
    db.refresh(theirs)
    assert theirs.is_read is False


def test_chat_conversations_are_private(client, world, db) -> None:
    muskan = world["users"]["Muskan Makhija"]
    conversation = ChatConversation(
        user_id=muskan.id, title="Muskan's own", permission_fingerprint="x"
    )
    db.add(conversation)
    db.commit()

    h = world["h"]["bde"]
    assert client.get(f"/api/chat/conversations/{conversation.id}", headers=h).status_code == 404
    assert client.delete(f"/api/chat/conversations/{conversation.id}", headers=h).status_code == 404
    assert str(conversation.id) not in client.get("/api/chat/conversations", headers=h).text
    # Even an administrator cannot read somebody else's assistant history.
    assert client.get(
        f"/api/chat/conversations/{conversation.id}", headers=world["h"]["super_admin"]
    ).status_code == 404
    assert db.get(ChatConversation, conversation.id) is not None


def test_a_forged_identity_in_a_body_changes_nothing(client, world) -> None:
    """Extra identity fields are not part of any schema; the session decides."""
    parth = world["users"]["Parth Fulvani"]
    response = client.post(
        "/api/leads",
        headers=world["h"]["bde"],
        json={
            "name": "Forged",
            "assigned_to_user_id": str(parth.id),
            "role": "SUPER_ADMIN",
            "user_id": str(world["users"]["Shail Patel"].id),
        },
    )
    assert response.status_code == 403


# ----------------------------------------------------- 5. state machine
def _set_status(db, lead_id: str, status: str) -> None:
    row = db.get(Lead, uuid.UUID(lead_id))
    row.status = status
    row.closed_at = None
    db.commit()


def test_every_transition_is_enforced_server_side(client, world, db) -> None:
    lead_id = world["leads"]["parth"]["id"]
    h = world["h"]["bde"]
    for current in LeadStatus:
        allowed = ALLOWED_LEAD_TRANSITIONS[current]
        for target in LeadStatus:
            if target == current:
                continue
            _set_status(db, lead_id, current)
            response = client.post(
                f"/api/leads/{lead_id}/status",
                headers=h,
                json={"status": target.value, "remark": "matrix"},
            )
            if target in allowed:
                assert response.status_code == 200, f"{current}->{target}: {response.text}"
            else:
                assert response.status_code == 422, f"{current}->{target} was allowed"
                assert response.json()["error"]["code"] == "INVALID_TRANSITION"


def test_the_documented_pipeline() -> None:
    table = ALLOWED_LEAD_TRANSITIONS
    assert table[LeadStatus.NOT_CONTACTED] >= {LeadStatus.CONTACTED, LeadStatus.JUNK, LeadStatus.LOST}
    assert LeadStatus.NURTURING in table[LeadStatus.CONTACTED]
    assert table[LeadStatus.NURTURING] == {LeadStatus.PRE_QUALIFIED, LeadStatus.LOST}
    assert table[LeadStatus.PRE_QUALIFIED] == {LeadStatus.QUALIFIED, LeadStatus.LOST}
    assert table[LeadStatus.QUALIFIED] == {LeadStatus.CONVERTED, LeadStatus.LOST}
    for final in (LeadStatus.JUNK, LeadStatus.LOST, LeadStatus.CONVERTED):
        assert table[final] == frozenset()


def test_a_stage_change_needs_a_remark(client, world) -> None:
    response = client.post(
        f"/api/leads/{world['leads']['parth']['id']}/status",
        headers=world["h"]["bde"],
        json={"status": "CONTACTED"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("final", ["LOST", "JUNK", "CONVERTED"])
def test_only_administrators_reopen_a_closed_lead(client, world, db, final) -> None:
    lead_id = world["leads"]["parth"]["id"]
    body = {"status": "NEW", "remark": "Closed by mistake."}
    for role, expected in (
        ("bde", 403),
        ("bde_manager", 403),
        ("admin", 200),
        ("super_admin", 200),
    ):
        _set_status(db, lead_id, final)
        response = client.post(f"/api/leads/{lead_id}/reopen", headers=world["h"][role], json=body)
        assert response.status_code == expected, f"{role}: {response.text}"
        if expected == 200:
            assert response.json()["status"] == "NEW"
        # A normal move out of a final stage is refused for everyone.
        _set_status(db, lead_id, final)
        moved = client.post(
            f"/api/leads/{lead_id}/status",
            headers=world["h"][role],
            json={"status": "CONTACTED", "remark": "try"},
        )
        assert moved.status_code == 422


def test_the_assignee_owns_the_activity_log(client, world) -> None:
    lead_id = world["leads"]["parth"]["id"]
    entry = {"activity_type": "CALL", "remark": "Spoke to them."}
    for role in ("bde_manager", "admin", "super_admin"):
        response = client.post(f"/api/leads/{lead_id}/activities", headers=world["h"][role], json=entry)
        assert response.status_code == 403, role
    assert client.post(
        f"/api/leads/{lead_id}/activities", headers=world["h"]["bde"], json=entry
    ).status_code == 200
    # A system-only activity type cannot be forged by hand.
    assert client.post(
        f"/api/leads/{lead_id}/activities",
        headers=world["h"]["bde"],
        json={"activity_type": "STATUS_CHANGED", "remark": "forged"},
    ).status_code == 422


def test_a_manager_may_move_a_report_lead_along(client, world) -> None:
    """Existing rule: reading and reporting are management's; only the log is
    the assignee's."""
    response = client.post(
        f"/api/leads/{world['leads']['parth']['id']}/status",
        headers=world["h"]["bde_manager"],
        json={"status": "CONTACTED", "remark": "Manager update."},
    )
    assert response.status_code == 200


# ---------------------------------------------------------- 6. audit
def _audit(db, action, entity_id=None):
    stmt = select(AuditEvent).where(AuditEvent.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == uuid.UUID(str(entity_id)))
    return db.execute(stmt.order_by(AuditEvent.created_at.desc())).scalars().all()


def test_lead_changes_are_audited(client, world, db) -> None:
    lead_id = world["leads"]["parth"]["id"]
    navya = world["users"]["Navya Rupawat"]

    assert _audit(db, AuditAction.LEAD_CREATED, lead_id)

    client.post(
        f"/api/leads/{lead_id}/status",
        headers=world["h"]["bde"],
        json={"status": "CONTACTED", "remark": "Reached them."},
    )
    [event] = _audit(db, AuditAction.LEAD_STATUS_CHANGED, lead_id)
    assert event.actor_user_id == world["users"]["Parth Fulvani"].id
    assert event.before == {"status": "NEW"} and event.after == {"status": "CONTACTED"}

    client.patch(
        f"/api/leads/{lead_id}",
        headers=world["h"]["bde_manager"],
        json={"assigned_to_user_id": str(world["users"]["Muskan Makhija"].id)},
    )
    [event] = _audit(db, AuditAction.LEAD_REASSIGNED, lead_id)
    assert event.actor_user_id == navya.id
    assert event.after == {"assigned_to_user_id": str(world["users"]["Muskan Makhija"].id)}


def test_editing_a_follow_up_date_is_audited_not_a_500(client, world, db) -> None:
    """Regression: a `date` in the audit payload could not be serialised, so
    PATCHing a lead's follow-up date failed with a 500."""
    lead_id = world["leads"]["parth"]["id"]
    response = client.patch(
        f"/api/leads/{lead_id}",
        headers=world["h"]["bde_manager"],
        json={"next_follow_up_date": "2026-12-01"},
    )
    assert response.status_code == 200, response.text
    [event] = _audit(db, AuditAction.LEAD_UPDATED, lead_id)
    assert event.after == {"next_follow_up_date": "2026-12-01"}


def test_references_and_post_sale_are_audited(client, users, db) -> None:
    lead = eligible_lead(client, users, name="Audit Ref Co")
    [synced] = _audit(db, AuditAction.POST_SALE_SYNCED)
    assert synced.actor_user_id == users["Shail Patel"].id
    assert synced.after["matched"] == 1

    response = client.post(
        "/api/references",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"lead_id": lead["id"], "outcome": "YES", "referred_name": "A Friend"},
    )
    assert response.status_code == 201, response.text
    [event] = _audit(db, AuditAction.REFERENCE_RECORDED, response.json()["id"])
    assert event.actor_user_id == users["Parth Fulvani"].id
    assert event.after["outcome"] == "YES"
    # Who was referred is business data, not audit data.
    assert "referred_mobile" not in event.after


def test_alert_assignment_is_audited(client, users, db) -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.models.feedback import FeedbackAlert
    from app.models.org import Department

    department = db.execute(select(Department)).scalars().first()
    alert = FeedbackAlert(
        department_id=department.id,
        status="OPEN",
        average_rating=Decimal("2.10"),
        response_count=6,
        threshold=Decimal("3.00"),
        window_days=30,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(alert)
    db.commit()

    shail = users["Shail Patel"]
    target = users["Navya Rupawat"]
    response = client.post(
        f"/api/feedback/alerts/{alert.id}/assign",
        headers=sign_in(client, shail),
        json={"user_id": str(target.id)},
    )
    assert response.status_code == 200, response.text
    [event] = _audit(db, AuditAction.FEEDBACK_ALERT_ASSIGNED, alert.id)
    assert event.actor_user_id == shail.id
    assert event.after["assigned_to_user_id"] == str(target.id)
