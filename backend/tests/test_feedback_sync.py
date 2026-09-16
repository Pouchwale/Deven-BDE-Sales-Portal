"""Google Form responses coming back into the portal.

Numbered against section 20 of GOOGLE_FORM_FEEDBACK_SYNC_PLAN.md.

Nothing here touches Google. The webhook is exercised with real signatures
over real payloads, which is the point: a test that bypasses verification
would be testing a door that is propped open.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.feedback import (
    Feedback,
    FeedbackRequest,
    FeedbackSyncEvent,
)
from app.models.system import Notification
from app.services import feedback_sync
from tests.conftest import eligible_lead, sign_in, super_admin_headers

SECRET = "test-shared-secret-value"
WEBHOOK = "/api/feedback/sync/webhook"


@pytest.fixture
def sync_on(monkeypatch):
    """Configure the shared secret without touching the developer's .env."""
    monkeypatch.setattr(settings, "GOOGLE_SYNC_SECRET", SECRET, raising=False)
    settings.__dict__.pop("feedback_sync_enabled", None)
    from app.api.feedback_sync import webhook_limiter

    webhook_limiter.clear()
    yield
    settings.__dict__.pop("feedback_sync_enabled", None)


def deliver(client, payload: dict, *, secret: str = SECRET, timestamp=None):
    """POST a response the way Apps Script would, signature and all."""
    body = json.dumps(payload).encode()
    return client.post(
        WEBHOOK,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Portal-Signature": feedback_sync.sign(body, secret, timestamp),
        },
    )


def answers(**overrides) -> dict:
    """A response in the shape the company's form produces."""
    base = {
        "Timestamp": "2026-09-07 14:23:01",
        "Reference code": "",
        "Your name": "Kiran Shah",
        "Company name": "Shah Foods",
        "Mobile number": "9876500011",
        "Email address": "kiran@shahfoods.example",
        "Overall, how satisfied are you with us?": "4",
        "Would you recommend us?": "Yes",
        "How would you rate our Production team?": "4",
        "Any comments about Production?": "Packaging improved a lot.",
        "How would you rate our Dispatch team?": "2",
        "Any other comments or suggestions": "Please keep the same manager.",
    }
    base.update(overrides)
    return base


def response_payload(response_id: str = "resp-1", **answer_overrides) -> dict:
    return {"response_id": response_id, "answers": answers(**answer_overrides)}


def make_request(client, db, users) -> FeedbackRequest:
    """A real ask, made the way the UI makes one: compose, then send.

    Composing only ISSUES the code (it has to be in the link). The ask counts
    as sent when the assignee confirms it went out - which is the step the UI
    takes, and the one that makes the queue say "awaiting".
    """
    # The subject is a won lead the post-sale sheet has invoiced - the same
    # population Reference Tracking and the pending queue use. Done as the
    # ASSIGNEE, because the ask is recorded on that lead's own log.
    lead = eligible_lead(client, users, name="Sync Subject")
    assignee = sign_in(client, users["Parth Fulvani"])
    composed = client.get(
        f"/api/leads/{lead['id']}/message",
        headers=assignee,
        params={"channel": "EMAIL"},
    )
    assert composed.status_code == 200, composed.text
    sent = client.post(
        f"/api/leads/{lead['id']}/message/sent",
        headers=assignee,
        json={"purpose": "FEEDBACK", "channel": "EMAIL"},
    )
    assert sent.status_code == 200, sent.text
    request = (
        db.execute(
            select(FeedbackRequest).where(
                FeedbackRequest.lead_id == uuid.UUID(lead["id"])
            )
        )
        .scalars()
        .first()
    )
    assert request is not None
    return request


# ------------------------------------------------ 11, 12, 22. the door
def test_an_unsigned_delivery_is_refused(client, db, sync_on) -> None:
    body = json.dumps(response_payload()).encode()
    response = client.post(WEBHOOK, content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 401
    # And nothing was written - unauthenticated noise must not be a storage
    # vector.
    assert db.execute(select(FeedbackSyncEvent)).scalars().first() is None
    assert db.execute(select(Feedback)).scalars().first() is None


def test_a_tampered_body_is_refused(client, db, sync_on) -> None:
    payload = response_payload()
    body = json.dumps(payload).encode()
    signature = feedback_sync.sign(body, SECRET)

    payload["answers"]["Overall, how satisfied are you with us?"] = "1"
    response = client.post(
        WEBHOOK,
        content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Portal-Signature": signature},
    )
    assert response.status_code == 401
    assert db.execute(select(FeedbackSyncEvent)).scalars().first() is None


def test_the_wrong_secret_is_refused(client, sync_on) -> None:
    assert deliver(client, response_payload(), secret="not-the-secret").status_code == 401


def test_a_replay_outside_the_window_is_refused(client, sync_on) -> None:
    stale = int(time.time()) - settings.GOOGLE_SYNC_MAX_SKEW_SECONDS - 60
    assert deliver(client, response_payload(), timestamp=stale).status_code == 401


def test_a_valid_signature_from_inside_the_window_is_accepted(client, sync_on) -> None:
    recent = int(time.time()) - 30
    assert deliver(client, response_payload(), timestamp=recent).status_code == 200


def test_an_oversized_payload_is_refused_before_parsing(client, db, sync_on) -> None:
    payload = response_payload()
    payload["answers"]["Any other comments or suggestions"] = "x" * 70_000
    assert deliver(client, payload).status_code == 413
    assert db.execute(select(FeedbackSyncEvent)).scalars().first() is None


def test_the_webhook_is_off_without_a_secret(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "GOOGLE_SYNC_SECRET", "", raising=False)
    settings.__dict__.pop("feedback_sync_enabled", None)
    assert deliver(client, response_payload()).status_code == 503
    settings.__dict__.pop("feedback_sync_enabled", None)


def test_a_malformed_payload_is_rejected(client, sync_on) -> None:
    body = b"not json at all"
    response = client.post(
        WEBHOOK,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Portal-Signature": feedback_sync.sign(body, SECRET),
        },
    )
    assert response.status_code == 422


def test_a_payload_without_a_response_id_is_rejected(client, sync_on) -> None:
    assert deliver(client, {"answers": answers()}).status_code == 422
    assert deliver(client, {"response_id": "x"}).status_code == 422


# --------------------------------------------------- 1, 2, 3, 4. the loop
def test_a_response_with_a_code_matches_its_request(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)

    result = deliver(client, response_payload(**{"Reference code": request.code}))
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "stored"
    assert body["match_status"] == "MATCHED_TOKEN"
    assert body["reference"] == request.reference

    feedback = db.execute(select(Feedback).where(Feedback.external_response_id == "resp-1")).scalars().one()
    assert feedback.feedback_request_id == request.id
    assert feedback.customer_id == request.customer_id
    assert float(feedback.overall_rating) == 4.0
    assert feedback.overall_comments == "Please keep the same manager."

    db.refresh(request)
    assert request.status == "COMPLETED"
    assert request.completed_at is not None


def test_department_ratings_and_comments_are_stored(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)
    deliver(client, response_payload(**{"Reference code": request.code}))

    feedback = db.execute(select(Feedback).where(Feedback.external_response_id == "resp-1")).scalars().one()
    scores = {
        rating.department.name: (rating.rating, rating.comments)
        for rating in feedback.department_ratings
    }
    assert float(scores["Production"][0]) == 4.0
    assert scores["Production"][1] == "Packaging improved a lot."
    assert float(scores["Dispatch"][0]) == 2.0
    # The source wording is kept beside the number.
    assert all(rating.raw_value for rating in feedback.department_ratings)


def test_the_completed_request_leaves_the_pending_queue(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)
    headers = sign_in(client, users["Shail Patel"])

    before = client.get("/api/feedback/pending", headers=headers).json()
    assert any(item["request_reference"] == request.reference for item in before)

    deliver(client, response_payload(**{"Reference code": request.code}))

    after = client.get("/api/feedback/pending", headers=headers).json()
    assert all(item["id"] != str(request.customer_id) for item in after)


# ------------------------------------------------------- 5. idempotency
def test_the_same_response_twice_creates_one_record(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)
    payload = response_payload(**{"Reference code": request.code})

    first = deliver(client, payload)
    second = deliver(client, payload)

    assert first.json()["status"] == "stored"
    assert second.status_code == 200, "a duplicate is not an error - retries must stop"
    assert second.json()["status"] == "duplicate"

    assert len(db.execute(select(Feedback)).scalars().all()) == 1
    assert len(db.execute(select(FeedbackSyncEvent)).scalars().all()) == 1


# --------------------------------------------- 6, 7, 8. matching fallbacks
def test_no_code_falls_back_to_contact_matching(client, db, users, sync_on) -> None:
    headers = sign_in(client, users["Shail Patel"])
    # The book is Super Admin only; the ask below is still composed as the
    # admin, which is the thing under test.
    customer = client.get(
        "/api/customers", headers=super_admin_headers(client)
    ).json()["items"][0]

    result = deliver(
        client,
        response_payload(
            **{"Reference code": "", "Company name": customer["name"],
               "Mobile number": "", "Email address": ""}
        ),
    )
    assert result.json()["match_status"] == "MATCHED_CONTACT"

    feedback = db.execute(select(Feedback)).scalars().one()
    assert str(feedback.customer_id) == customer["id"]
    assert feedback.feedback_request_id is None


def test_an_unknown_code_does_not_attach_anything(client, db, sync_on) -> None:
    result = deliver(
        client,
        response_payload(
            **{"Reference code": "FB-2026-99999.madeuptokenvalue",
               "Company name": "Nobody We Know", "Mobile number": "", "Email address": ""}
        ),
    )
    assert result.json()["match_status"] == "UNMATCHED"

    feedback = db.execute(select(Feedback)).scalars().one()
    assert feedback.customer_id is None
    assert feedback.feedback_request_id is None
    # Stored regardless: a response that reached us is never lost.
    assert float(feedback.overall_rating) == 4.0


def test_a_guessed_reference_without_the_token_does_not_match(
    client, db, users, sync_on
) -> None:
    """The half a stranger could guess is not the half that is trusted."""
    request = make_request(client, db, users)

    result = deliver(
        client,
        response_payload(
            **{"Reference code": request.reference,
               "Company name": "Nobody We Know", "Mobile number": "", "Email address": ""}
        ),
    )
    assert result.json()["match_status"] == "UNMATCHED"
    db.refresh(request)
    assert request.status == "SENT", "a guessed reference must not complete the ask"


def test_a_mismatched_reference_and_token_does_not_match(
    client, db, users, sync_on
) -> None:
    request = make_request(client, db, users)
    forged = f"FB-2026-00099.{request.token}"

    result = deliver(
        client,
        response_payload(
            **{"Reference code": forged, "Company name": "Nobody We Know",
               "Mobile number": "", "Email address": ""}
        ),
    )
    assert result.json()["match_status"] == "UNMATCHED"


def test_a_cancelled_request_stops_matching(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)
    code = request.code
    request.status = "CANCELLED"
    db.commit()

    result = deliver(
        client,
        response_payload(
            **{"Reference code": code, "Company name": "Nobody We Know",
               "Mobile number": "", "Email address": ""}
        ),
    )
    assert result.json()["match_status"] == "UNMATCHED"


# ------------------------------------------------------- 14. duplicates
def test_a_second_response_for_one_request_is_flagged_not_merged(
    client, db, users, sync_on
) -> None:
    request = make_request(client, db, users)
    code = request.code

    deliver(client, response_payload("resp-1", **{"Reference code": code}))
    deliver(client, response_payload("resp-2", **{"Reference code": code}))

    rows = db.execute(select(Feedback).order_by(Feedback.created_at)).scalars().all()
    assert len(rows) == 2, "both are kept - never silently dropped"
    assert rows[0].match_status == "MATCHED_TOKEN"
    assert rows[1].match_status == "DUPLICATE"

    db.refresh(request)
    assert request.status == "COMPLETED"


# ------------------------------------------------ 9, 10. bad data
def test_a_response_with_no_rating_is_recorded_as_failed(client, db, sync_on) -> None:
    payload = response_payload(
        **{
            "Overall, how satisfied are you with us?": "",
            "How would you rate our Production team?": "",
            "How would you rate our Dispatch team?": "",
        }
    )
    result = deliver(client, payload)

    assert result.json()["status"] == "failed"
    event = db.execute(select(FeedbackSyncEvent)).scalars().one()
    assert event.status == "FAILED"
    assert event.error_code == "NO_RATING"
    assert db.execute(select(Feedback)).scalars().first() is None


def test_an_unknown_department_does_not_create_one(client, db, users, sync_on) -> None:
    """A typo in a form question must not silently spawn a department."""
    from app.models.org import Department

    before = {d.name for d in db.execute(select(Department)).scalars()}

    request = make_request(client, db, users)
    deliver(
        client,
        response_payload(
            **{"Reference code": request.code,
               "How would you rate our Telepathy team?": "5"}
        ),
    )

    after = {d.name for d in db.execute(select(Department)).scalars()}
    assert after == before, "the webhook never creates departments"

    # The rest of the response still landed.
    feedback = db.execute(select(Feedback)).scalars().one()
    assert float(feedback.overall_rating) == 4.0


# ----------------------------------------------------- 17. notifications
def test_the_owner_is_notified_once(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)
    payload = response_payload(**{"Reference code": request.code})

    deliver(client, payload)
    deliver(client, payload)  # retry - must not notify twice

    notes = (
        db.execute(
            select(Notification).where(Notification.type == "FEEDBACK_RECEIVED")
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    assert notes[0].user_id == request.owner_user_id
    assert request.reference in (notes[0].body or "")


def test_an_unmatched_response_notifies_admins(client, db, sync_on) -> None:
    deliver(
        client,
        response_payload(
            **{"Company name": "Nobody We Know", "Mobile number": "", "Email address": ""}
        ),
    )
    notes = (
        db.execute(
            select(Notification).where(Notification.type == "FEEDBACK_UNMATCHED")
        )
        .scalars()
        .all()
    )
    assert notes, "somebody has to know it needs a human"


# ------------------------------------------------------- 15, 16. effects
def test_the_dashboard_and_analysis_see_the_new_response(
    client, db, users, sync_on
) -> None:
    headers = sign_in(client, users["Shail Patel"])
    request = make_request(client, db, users)

    before = client.get("/api/dashboard", headers=headers).json()
    deliver(client, response_payload(**{"Reference code": request.code}))
    after = client.get("/api/dashboard", headers=headers).json()

    assert after["feedback_pending"] == before["feedback_pending"] - 1
    assert after["feedback"]["total_responses"] == before["feedback"]["total_responses"] + 1


# ------------------------------------------------------------ 19. scope
def test_the_ledger_and_review_queue_are_admin_only(client, users, sync_on) -> None:
    bde = sign_in(client, users["Parth Fulvani"])
    for path in ("/api/feedback/sync/status", "/api/feedback/sync/events",
                 "/api/feedback/sync/needs-review"):
        assert client.get(path, headers=bde).status_code == 403, path
    assert client.post("/api/feedback/sync/run", headers=bde).status_code == 403


def test_a_bde_cannot_see_another_bdes_request(client, db, users) -> None:
    admin = sign_in(client, users["Shail Patel"])
    customer = client.get(
        "/api/customers", headers=super_admin_headers(client)
    ).json()["items"][0]
    client.get(
        f"/api/customers/{customer['id']}/message",
        headers=admin,
        params={"purpose": "FEEDBACK", "channel": "EMAIL"},
    )
    request = db.execute(select(FeedbackRequest)).scalars().first()
    assert request is not None

    from app.core.authority import visible_user_ids
    from app.services import feedback_requests

    parth = users["Parth Fulvani"]
    scope = visible_user_ids(db, parth)
    if request.owner_user_id != parth.id:
        assert not feedback_requests.can_see(request, scope)


# --------------------------------------------------- 20, 21. no leakage
def test_the_ledger_stores_a_hash_not_the_payload(client, db, users, sync_on) -> None:
    request = make_request(client, db, users)
    deliver(client, response_payload(**{"Reference code": request.code}))

    event = db.execute(select(FeedbackSyncEvent)).scalars().one()
    blob = json.dumps(
        {
            "hash": event.payload_hash,
            "error": event.error_detail,
            "id": event.external_response_id,
        }
    )
    for pii in ("Kiran Shah", "9876500011", "kiran@shahfoods", "keep the same manager"):
        assert pii not in blob, f"{pii!r} reached the sync ledger"


def test_the_secret_never_reaches_the_status_endpoint(client, users, sync_on) -> None:
    body = client.get(
        "/api/feedback/sync/status", headers=sign_in(client, users["Shail Patel"])
    ).text
    assert SECRET not in body
    assert "secret" not in body.lower()
    assert '"webhook_configured":true' in body.replace(" ", "")


def test_no_pii_in_the_logs_for_a_failed_response(client, caplog, sync_on) -> None:
    import logging

    with caplog.at_level(logging.DEBUG):
        deliver(
            client,
            response_payload(
                **{
                    "Overall, how satisfied are you with us?": "",
                    "How would you rate our Production team?": "",
                    "How would you rate our Dispatch team?": "",
                }
            ),
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for pii in ("Kiran Shah", "9876500011", "kiran@shahfoods"):
        assert pii not in logged


# ------------------------------------------------------------ 13. resolve
def test_an_admin_can_attach_an_unmatched_response(client, db, users, sync_on) -> None:
    headers = sign_in(client, users["Shail Patel"])
    deliver(
        client,
        response_payload(
            **{"Company name": "Nobody We Know", "Mobile number": "", "Email address": ""}
        ),
    )

    queue = client.get("/api/feedback/sync/needs-review", headers=headers).json()
    assert len(queue) == 1
    feedback_id = queue[0]["id"]

    # The book is Super Admin only; the ask below is still composed as the
    # admin, which is the thing under test.
    customer = client.get(
        "/api/customers", headers=super_admin_headers(client)
    ).json()["items"][0]
    resolved = client.post(
        f"/api/feedback/sync/resolve/{feedback_id}",
        headers=headers,
        json={"customer_id": customer["id"]},
    )
    assert resolved.status_code == 200, resolved.text

    feedback = db.get(Feedback, uuid.UUID(feedback_id))
    db.refresh(feedback)
    assert str(feedback.customer_id) == customer["id"]
    assert feedback.match_status == "MATCHED_CONTACT"
    assert client.get("/api/feedback/sync/needs-review", headers=headers).json() == []


def test_the_status_endpoint_reports_what_is_missing(client, users, sync_on) -> None:
    body = client.get(
        "/api/feedback/sync/status", headers=sign_in(client, users["Shail Patel"])
    ).json()

    assert body["webhook_configured"] is True
    # The form itself has not been supplied yet - the status says so rather
    # than pretending the integration is complete.
    assert body["form_url_configured"] is False
    assert body["reference_field_configured"] is False
