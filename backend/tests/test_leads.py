"""Module 2 — assigned leads, the work queue and notifications."""
from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import advance_lead, eligible_lead, sign_in, super_admin_headers


def error_code(response) -> str:
    return response.json()["error"]["code"]


def make_lead(client, headers, assignee_id, **overrides) -> dict:
    payload = {
        "name": "Kiran Shah",
        "company_name": "Shah Foods",
        "mobile": "9800000000",
        "assigned_to_user_id": str(assignee_id),
        **overrides,
    }
    response = client.post("/api/leads", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------ assignment
def test_a_manager_assigns_a_lead_to_their_report(client, users) -> None:
    headers = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, headers, users["Parth Fulvani"].id, priority="HIGH")

    assert lead["status"] == "NEW"
    assert lead["origin"] == "ASSIGNED_BY_HEAD"
    assert lead["assigned_to_name"] == "Parth Fulvani"
    assert lead["assigned_by_name"] == "Navya Rupawat"
    # The assignment itself is the first entry in the history.
    assert lead["activities"][0]["activity_type"] == "ASSIGNED"


def test_a_field_user_cannot_create_leads(client, users) -> None:
    response = client.post(
        "/api/leads",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"name": "Nope", "assigned_to_user_id": str(users["Parth Fulvani"].id)},
    )
    assert response.status_code == 403


def test_cannot_assign_outside_your_chain(client, users) -> None:
    response = client.post(
        "/api/leads",
        headers=sign_in(client, users["Navya Rupawat"]),
        json={"name": "Wrong chain", "assigned_to_user_id": str(users["Parag Sharma"].id)},
    )
    assert response.status_code == 403


def test_cannot_assign_to_a_deactivated_person(client, users, db) -> None:
    users["Shivani Patel"].is_active = False
    db.flush()

    response = client.post(
        "/api/leads",
        headers=sign_in(client, users["Navya Rupawat"]),
        json={"name": "To nobody", "assigned_to_user_id": str(users["Shivani Patel"].id)},
    )
    assert response.status_code == 422


def test_notifies_the_assignee_in_the_same_transaction(client, users) -> None:
    make_lead(
        client, sign_in(client, users["Navya Rupawat"]), users["Parth Fulvani"].id
    )

    parth = sign_in(client, users["Parth Fulvani"])
    body = client.get("/api/notifications", headers=parth).json()
    assert body["total"] == 1
    assert body["items"][0]["type"] == "LEAD_ASSIGNED"
    assert "Kiran Shah" in body["items"][0]["title"]


# ---------------------------------------------------------- reassignment
def test_shailesh_cannot_touch_a_lead_held_by_ramanesh(client, users) -> None:
    """A lead held by somebody above you is not merely un-editable — it is
    invisible, because the list scope is your own subtree. 404 is the stronger
    answer, and it is what stops ids being probed across the hierarchy."""
    admin = sign_in(client, users["Shail Patel"])
    lead = make_lead(client, admin, users["Ramanesh Nair"].id)

    response = client.patch(
        f"/api/leads/{lead['id']}",
        headers=sign_in(client, users["Shailesh Prajapati"]),
        json={"assigned_to_user_id": str(users["Parag Sharma"].id)},
    )
    assert response.status_code == 404


def test_you_can_always_hand_off_your_own_lead(client, users) -> None:
    """can_act_on returns False for yourself by design, so reassignment has to
    exempt the self case — otherwise a manager holding a lead could never pass
    it on."""
    navya = sign_in(client, users["Navya Rupawat"])
    admin = sign_in(client, users["Shail Patel"])
    lead = make_lead(client, admin, users["Navya Rupawat"].id)

    response = client.patch(
        f"/api/leads/{lead['id']}",
        headers=navya,
        json={"assigned_to_user_id": str(users["Parth Fulvani"].id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to_name"] == "Parth Fulvani"


def test_cannot_take_a_lead_from_someone_who_outranks_you(client, users, db) -> None:
    """The guard itself, on a state the API will not let you build: a lead
    held by the Super Admin, seen by an Admin whose scope is ALL."""
    admin_headers = sign_in(client, users["Shail Patel"])
    lead = make_lead(client, admin_headers, users["Navya Rupawat"].id)

    from app.models.lead import Lead

    row = db.get(Lead, __import__("uuid").UUID(lead["id"]))
    row.assigned_to_user_id = users["Portal Owner"].id
    db.flush()

    response = client.patch(
        f"/api/leads/{lead['id']}",
        headers=admin_headers,
        json={"assigned_to_user_id": str(users["Parth Fulvani"].id)},
    )
    assert response.status_code == 403
    assert "Portal Owner" in response.json()["error"]["message"]


def test_reassignment_notifies_both_ends(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)

    response = client.patch(
        f"/api/leads/{lead['id']}",
        headers=navya,
        json={"assigned_to_user_id": str(users["Muskan Makhija"].id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to_name"] == "Muskan Makhija"

    took_it = client.get(
        "/api/notifications", headers=sign_in(client, users["Muskan Makhija"])
    ).json()
    assert any(item["type"] == "LEAD_ASSIGNED" for item in took_it["items"])

    lost_it = client.get(
        "/api/notifications", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    assert any(item["type"] == "LEAD_REASSIGNED" for item in lost_it["items"])


# --------------------------------------------------------- state machine
def test_the_pipeline_only_moves_where_it_is_allowed(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    parth = sign_in(client, users["Parth Fulvani"])

    # NEW -> CONVERTED skips the whole pipeline.
    bad = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "CONVERTED", "remark": "Trying to skip the pipeline."},
    )
    assert bad.status_code == 422
    assert error_code(bad) == "INVALID_TRANSITION"
    assert bad.json()["error"]["details"]["allowed"] == [
        "CONTACTED",
        "JUNK",
        "LOST",
        "NOT_CONTACTED",
    ]

    # Nor can it skip a single stage in the middle.
    client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "CONTACTED", "remark": "Reached them."},
    )
    skipped = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "QUALIFIED", "remark": "Trying to skip nurturing."},
    )
    assert skipped.status_code == 422
    assert error_code(skipped) == "INVALID_TRANSITION"

    # The whole legal route, one stage at a time.
    for step in ("NURTURING", "PRE_QUALIFIED", "QUALIFIED", "CONVERTED"):
        ok = client.post(
            f"/api/leads/{lead['id']}/status",
            headers=parth,
            json={"status": step, "remark": f"Moved to {step}."},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == step

    assert ok.json()["closed_at"] is not None
    # CONVERTED is terminal - there is no way back for anyone but an admin.
    final = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "CONTACTED", "remark": "Trying to reopen."},
    )
    assert final.status_code == 422
    assert final.json()["error"]["details"]["allowed"] == []


def test_status_changes_are_recorded_with_who_and_why(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    parth = sign_in(client, users["Parth Fulvani"])

    response = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=parth,
        json={"status": "CONTACTED", "remark": "Called, wants a quote."},
    )
    latest = response.json()["activities"][0]
    assert latest["activity_type"] == "STATUS_CHANGED"
    assert latest["from_status"] == "NEW" and latest["to_status"] == "CONTACTED"
    assert latest["remark"] == "Called, wants a quote."
    assert latest["actor_name"] == "Parth Fulvani"


def test_only_real_activity_types_can_be_logged(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)

    response = client.post(
        f"/api/leads/{lead['id']}/activities",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"activity_type": "STATUS_CHANGED", "remark": "sneaky"},
    )
    assert response.status_code == 422


def test_logging_a_call_can_set_the_next_follow_up(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    when = (date.today() + timedelta(days=3)).isoformat()

    response = client.post(
        f"/api/leads/{lead['id']}/activities",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"activity_type": "CALL", "remark": "No answer.", "next_follow_up_date": when},
    )
    assert response.status_code == 200
    assert response.json()["next_follow_up_date"] == when


# --------------------------------------------------------------- scoping
def test_lead_lists_are_scoped(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    make_lead(client, navya, users["Parth Fulvani"].id)

    assert client.get("/api/leads", headers=navya).json()["total"] == 1
    assert (
        client.get(
            "/api/leads", headers=sign_in(client, users["Parth Fulvani"])
        ).json()["total"]
        == 1
    )
    # A different chain sees nothing.
    assert (
        client.get(
            "/api/leads", headers=sign_in(client, users["Ramanesh Nair"])
        ).json()["total"]
        == 0
    )
    # ...and cannot reach it by id either.
    lead_id = client.get("/api/leads", headers=navya).json()["items"][0]["id"]
    assert (
        client.get(
            f"/api/leads/{lead_id}", headers=sign_in(client, users["Parag Sharma"])
        ).status_code
        == 404
    )


def test_lead_stats(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    client.post(
        f"/api/leads/{lead['id']}/status",
        headers=navya,
        json={"status": "CONTACTED", "remark": "Reached them."},
    )

    stats = client.get("/api/leads/stats", headers=navya).json()
    assert stats["total"] == 1
    assert stats["open"] == 1
    assert stats["contacted"] == 1
    assert stats["new"] == 0


# ------------------------------------------------------------ work queue
def test_the_work_queue_keeps_the_two_buckets_apart(client, users) -> None:
    """A reference follow-up is not a lead and must never be counted as one."""
    navya = sign_in(client, users["Navya Rupawat"])
    make_lead(client, navya, users["Parth Fulvani"].id)

    parth = sign_in(client, users["Parth Fulvani"])
    # An askable account is a won lead the post-sale sheet has invoiced -
    # the SAP book is no longer a reference subject at all.
    customer = eligible_lead(client, users, name="Follow Up Subject")
    client.post(
        "/api/references",
        headers=parth,
        json={
            "lead_id": customer["id"],
            "outcome": "NO",
            "next_reference_date": (date.today() - timedelta(days=2)).isoformat(),
        },
    )

    queue = client.get("/api/work-queue", headers=parth).json()
    # One OPEN lead to work, and one account to go back to. The askable
    # account is itself a won lead, so the point is that it sits in the
    # follow-up bucket and not the assigned one - not that Parth owns
    # exactly one row in total.
    assert queue["assigned_total"] == 1
    assert queue["follow_up_total"] == 1
    assert len(queue["assigned_leads"]) == 1
    assert len(queue["reference_follow_ups"]) == 1
    assert (
        queue["assigned_leads"][0]["id"] != queue["reference_follow_ups"][0]["subject_id"]
    )


def test_the_work_queue_can_be_asked_for_one_bucket(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    make_lead(client, navya, users["Parth Fulvani"].id)
    parth = sign_in(client, users["Parth Fulvani"])

    only_leads = client.get(
        "/api/work-queue", params={"bucket": "ASSIGNED_BY_HEAD"}, headers=parth
    ).json()
    assert only_leads["assigned_total"] == 1
    assert only_leads["reference_follow_ups"] == []


def test_mine_only_separates_my_work_from_my_teams(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    make_lead(client, navya, users["Parth Fulvani"].id)

    # Navya's own queue is empty; the lead is Parth's work.
    assert client.get("/api/work-queue", headers=navya).json()["assigned_total"] == 0
    team = client.get(
        "/api/work-queue", params={"mine_only": False}, headers=navya
    ).json()
    assert team["assigned_total"] == 1


# --------------------------------------------------------- notifications
def test_notifications_are_private_to_their_owner(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    make_lead(client, navya, users["Parth Fulvani"].id)

    parth_headers = sign_in(client, users["Parth Fulvani"])
    notification = client.get("/api/notifications", headers=parth_headers).json()["items"][0]

    # Somebody else marking it read must not work.
    other = sign_in(client, users["Muskan Makhija"])
    client.post(f"/api/notifications/{notification['id']}/read", headers=other)

    still_unread = client.get("/api/notifications/unread-count", headers=parth_headers)
    assert still_unread.json()["unread"] == 1


def test_mark_all_read(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    make_lead(client, navya, users["Parth Fulvani"].id)
    parth = sign_in(client, users["Parth Fulvani"])

    assert client.get("/api/notifications/unread-count", headers=parth).json()["unread"] == 1
    client.post("/api/notifications/read-all", headers=parth)
    assert client.get("/api/notifications/unread-count", headers=parth).json()["unread"] == 0


# -------------------------------------------------------------- dispatch
def _converted_lead(client, navya, assignee_id, **overrides) -> dict:
    """Navya may also drive the pipeline for a lead she assigned."""
    lead = make_lead(client, navya, assignee_id, **overrides)
    return advance_lead(client, navya, lead["id"])


def test_reaching_converted_marks_the_lead_dispatched(client, users) -> None:
    """No separate step — completing the pipeline IS the completion action,
    and it is what makes the lead eligible for a feedback ask."""
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    assert lead["dispatched_at"] is None

    for step in ("CONTACTED", "NURTURING", "PRE_QUALIFIED", "QUALIFIED"):
        lead = client.post(
            f"/api/leads/{lead['id']}/status",
            headers=navya,
            json={"status": step, "remark": f"Moved to {step}."},
        ).json()
        assert lead["dispatched_at"] is None, step

    converted = client.post(
        f"/api/leads/{lead['id']}/status",
        headers=navya,
        json={"status": "CONVERTED", "remark": "Won it."},
    ).json()
    assert converted["dispatched_at"] is not None
    assert converted["activities"][0]["activity_type"] == "STATUS_CHANGED"


def test_a_lead_stage_change_cannot_be_undone(client, users) -> None:
    """Undo was removed, endpoint and all.

    "Do not provide the previous undo behavior" is not satisfied by hiding a
    button while the route still answers - so the route is gone. A stage set
    by mistake is corrected by moving the lead on through the state machine,
    and the timeline keeps both moves because both happened.
    """
    navya = sign_in(client, users["Navya Rupawat"])
    lead = _converted_lead(client, navya, users["Parth Fulvani"].id)
    assert lead["dispatched_at"] is not None
    activity_id = lead["activities"][0]["id"]

    response = client.post(
        f"/api/leads/{lead['id']}/activities/{activity_id}/undo", headers=navya
    )
    assert response.status_code == 404, response.text

    # The timeline still reports it as un-undoable, for any client still asking.
    detail = client.get(f"/api/leads/{lead['id']}", headers=navya).json()
    assert all(entry["can_undo"] is False for entry in detail["activities"])


def test_feedback_message_needs_a_converted_lead(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)

    response = client.get(
        f"/api/leads/{lead['id']}/message", headers=navya, params={"channel": "EMAIL"}
    )
    assert response.status_code == 422


def test_asking_moves_an_account_from_not_asked_to_awaiting(client, users) -> None:
    """It stays in the queue, in a different state.

    A row used to disappear the moment somebody asked, which left a BDE unable
    to tell "nobody has asked" from "asked, still waiting". It is owed-feedback
    until the response arrives, so it stays until then.

    Written against a SAP customer rather than a converted lead: since
    eligibility began at the SAP invoice, a converted lead is not owed feedback
    at all until SAP invoices it - see
    test_feedback.test_converting_a_lead_does_not_open_a_feedback_ask.
    """
    # Run as the ASSIGNEE: sending a feedback request writes to the lead's
    # log, and only the person the lead belongs to may do that.
    navya = sign_in(client, users["Parth Fulvani"])
    account = eligible_lead(client, users, name="State Transitions")

    def row():
        body = client.get("/api/feedback/pending", headers=navya).json()
        return next((item for item in body if item["id"] == account["id"]), None)

    before = row()
    assert before is not None, "a synced, invoiced account is owed feedback"
    assert before["state"] == "NOT_ASKED"
    assert before["request_reference"] is None
    customer_id = before["id"]

    # Composing is what issues the reference - the code has to be in the link
    # before the message is written.
    composed = client.get(
        f"/api/leads/{customer_id}/message",
        headers=navya,
        params={"channel": "EMAIL"},
    )
    assert composed.status_code == 200, composed.text

    sent = client.post(
        f"/api/leads/{customer_id}/message/sent",
        headers=navya,
        json={"purpose": "FEEDBACK", "channel": "EMAIL"},
    )
    assert sent.status_code == 200, sent.text

    after = row()
    assert after is not None, "an asked account is still owed feedback"
    assert after["id"] == customer_id
    assert after["state"] == "AWAITING"
    assert after["request_reference"].startswith("FB-")
    assert after["requested_at"] is not None


def test_reassignment_count_shows_on_the_lead(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = make_lead(client, navya, users["Parth Fulvani"].id)
    assert lead["reassignment_count"] == 0

    updated = client.patch(
        f"/api/leads/{lead['id']}",
        headers=navya,
        json={"assigned_to_user_id": str(users["Muskan Makhija"].id)},
    ).json()
    assert updated["reassignment_count"] == 1


def test_follow_up_reminders_fire_once_per_account_per_day(client, users) -> None:
    parth = sign_in(client, users["Parth Fulvani"])
    # An askable account is a won lead the post-sale sheet has invoiced -
    # the SAP book is no longer a reference subject at all.
    customer = eligible_lead(client, users, name="Follow Up Subject")
    client.post(
        "/api/references",
        headers=parth,
        json={
            "lead_id": customer["id"],
            "outcome": "NO",
            "next_reference_date": (date.today() - timedelta(days=1)).isoformat(),
        },
    )

    # Poll repeatedly, as the bell does.
    for _ in range(4):
        client.get("/api/notifications/unread-count", headers=parth)

    body = client.get("/api/notifications", headers=parth).json()
    reminders = [i for i in body["items"] if i["type"] == "REFERENCE_FOLLOWUP_DUE"]
    assert len(reminders) == 1
