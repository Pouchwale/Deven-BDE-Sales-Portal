"""Ready-to-send feedback requests.

The team should never retype "could you fill in our feedback form, here is the
link". The server renders the template, hands back the deep link that opens
WhatsApp or the mail client, and logs on the customer's timeline that the ask
went out.

The portal never sends anything itself — these tests hold that line too.
"""
from __future__ import annotations

import urllib.parse

import pytest
from sqlalchemy import select

from app.models.customer import Customer
from app.services import messaging, runtime_settings
from tests.conftest import auth, sign_in, super_admin_headers, token_for

FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSc-test/viewform"


def customer_of(client, search: str) -> dict:
    """Find one SAP account by name.

    Looked up as the Super Admin regardless of who the test is acting as:
    browsing the customer book is Super Admin only, and WHICH customer these
    tests use is scaffolding, not the thing under test. The call being tested
    still runs as whoever the test signed in.
    """
    return client.get(
        "/api/customers", params={"search": search}, headers=super_admin_headers(client)
    ).json()["items"][0]


def compose(client, headers, customer_id, purpose, channel) -> dict:
    response = client.get(
        f"/api/customers/{customer_id}/message",
        params={"purpose": purpose, "channel": channel},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------ number handling
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("9701082000", "919701082000"),
        ("+91 97111 22505", "919711122505"),
        ("091-9711122505", "919711122505"),
        ("919701082000", "919701082000"),
        ("", None),
        (None, None),
        # Too short to be anyone's number. Better nothing than a wrong chat.
        ("12345", None),
    ],
)
def test_a_stored_mobile_becomes_the_digits_wa_me_wants(stored, expected) -> None:
    assert messaging.whatsapp_number(stored) == expected


def test_a_template_placeholder_is_substituted_by_allow_list() -> None:
    """Never str.format on an admin-edited string.

    A stray brace would be a KeyError and `{0.__class__}` an information leak,
    so unknown placeholders are left exactly as typed.
    """
    rendered = messaging.render(
        "Hi {customer_name}, {unknown} {0.__class__} {link}",
        {"customer_name": "GULABS", "link": FORM_URL},
    )
    assert rendered == f"Hi GULABS, {{unknown}} {{0.__class__}} {FORM_URL}"


# ------------------------------------------------------ composing a message
def test_a_request_arrives_filled_in(client, db, users) -> None:
    runtime_settings.set_value(db, "company.feedback_form_url", FORM_URL)
    db.commit()
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    message = compose(client, headers, customer["id"], "FEEDBACK", "WHATSAPP")

    assert message["link_configured"] is True
    assert message["missing_contact"] is None
    # The customer, the sender and the link are already in the text.
    assert customer["name"] in message["body"]
    assert users["Shail Patel"].name in message["body"]
    assert message["link"] in message["body"]
    # And the deep link opens WhatsApp with exactly that text.
    assert message["url"].startswith(f"https://wa.me/{message['whatsapp_number']}?text=")
    assert urllib.parse.unquote_plus(message["url"].split("?text=", 1)[1]) == message["body"]


def test_an_email_request_carries_a_subject_and_a_mailto(client, db, users) -> None:
    runtime_settings.set_value(db, "company.feedback_form_url", FORM_URL)
    db.commit()
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    message = compose(client, headers, customer["id"], "FEEDBACK", "EMAIL")

    assert message["subject"]
    assert message["to"] == customer["email"]
    assert message["url"].startswith(f"mailto:{customer['email']}?")
    # A space stays %20 — several mail clients paste a "+" through literally.
    assert "+" not in message["url"].split("?", 1)[1].replace("%2B", "")


def test_a_feedback_request_has_no_link_until_the_form_is_configured(
    client, db, users
) -> None:
    """The real Google Form has not been supplied yet.

    Rather than sending a message with a blank where the link should be, the
    request is composed but marked unsendable so the UI can say why.
    """
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    message = compose(client, headers, customer["id"], "FEEDBACK", "WHATSAPP")
    assert message["link_configured"] is False
    assert message["url"] is None

    runtime_settings.set_value(db, "company.feedback_form_url", FORM_URL)
    db.commit()

    message = compose(client, headers, customer["id"], "FEEDBACK", "WHATSAPP")
    assert message["link_configured"] is True
    assert FORM_URL in message["body"]
    assert message["url"].startswith("https://wa.me/")


def test_a_customer_with_no_mobile_is_told_so_rather_than_linked_wrongly(
    client, db, users
) -> None:
    runtime_settings.set_value(db, "company.feedback_form_url", FORM_URL)
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")
    row = db.get(Customer, customer["id"])
    row.mobile = None
    db.commit()

    # With the form link configured, a None url can only mean the missing
    # mobile - otherwise this assertion would pass for the wrong reason.
    message = compose(client, headers, customer["id"], "FEEDBACK", "WHATSAPP")
    assert message["missing_contact"] == "mobile"
    assert message["url"] is None
    assert message["whatsapp_number"] is None
    # The text is still composed, so it can be copied and pasted elsewhere.
    assert message["body"]


def test_an_unknown_purpose_or_channel_is_refused(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    for params in (
        {"purpose": "DISCOUNT", "channel": "WHATSAPP"},
        {"purpose": "FEEDBACK", "channel": "SMS"},
        # REVIEW was a real purpose until the Google review feature was
        # removed. It must now be refused like any other unknown word.
        {"purpose": "REVIEW", "channel": "WHATSAPP"},
    ):
        response = client.get(
            f"/api/customers/{customer['id']}/message", params=params, headers=headers
        )
        # 422 VALIDATION_ERROR, the same envelope every other bad input gets.
        assert response.status_code == 422, response.text


def test_the_template_an_admin_edits_is_the_text_that_goes_out(
    client, db, users
) -> None:
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    client.patch(
        "/api/admin/settings",
        headers=headers,
        json={"values": {"message.feedback_whatsapp": "Namaste {customer_name}! {link}"}},
    )
    message = compose(client, headers, customer["id"], "FEEDBACK", "WHATSAPP")
    assert message["body"] == f"Namaste {customer['name']}! {message['link']}"


# ---------------------------------------------------- recording the send
def test_sending_a_feedback_request_is_logged_as_a_feedback_ask(
    client, db, users
) -> None:
    runtime_settings.set_value(db, "company.feedback_form_url", FORM_URL)
    db.commit()

    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    entries = client.post(
        f"/api/customers/{customer['id']}/message/sent",
        headers=headers,
        json={"purpose": "FEEDBACK", "channel": "EMAIL"},
    ).json()

    logged = next(
        entry for entry in entries if entry["activity_type"] == "FEEDBACK_REQUESTED"
    )
    assert "email" in logged["remark"]
    assert FORM_URL in logged["remark"]


def test_an_edited_message_is_what_gets_logged(client, users) -> None:
    """People reword these. The timeline must show what they actually sent."""
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    entries = client.post(
        f"/api/customers/{customer['id']}/message/sent",
        headers=headers,
        json={
            "purpose": "FEEDBACK",
            "channel": "WHATSAPP",
            "body": "Ronak bhai, ek feedback de do please!",
        },
    ).json()

    logged = next(
        entry for entry in entries if entry["activity_type"] == "FEEDBACK_REQUESTED"
    )
    assert "Ronak bhai" in logged["remark"]


def test_a_send_can_be_undone(client, users) -> None:
    """Someone presses the wrong button. The timeline has to be correctable."""
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    entries = client.post(
        f"/api/customers/{customer['id']}/message/sent",
        headers=headers,
        json={"purpose": "FEEDBACK", "channel": "WHATSAPP"},
    ).json()
    note = next(
        entry for entry in entries if entry["activity_type"] == "FEEDBACK_REQUESTED"
    )
    assert note["can_undo"] is True

    after = client.post(
        f"/api/customers/{customer['id']}/timeline/{note['id']}/undo", headers=headers
    )
    assert after.status_code == 200, after.text
    undone = next(entry for entry in after.json() if entry["id"] == note["id"])
    assert undone["undone_at"] is not None


def test_a_bde_cannot_message_someone_elses_customer(client, users) -> None:
    """Visibility is the same door as everywhere else."""
    owner = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")

    outsider = sign_in(client, users["Muskan Makhija"])
    assert client.get(
        f"/api/customers/{customer['id']}/message",
        params={"purpose": "FEEDBACK", "channel": "WHATSAPP"},
        headers=outsider,
    ).status_code == 404
    assert client.post(
        f"/api/customers/{customer['id']}/message/sent",
        headers=outsider,
        json={"purpose": "FEEDBACK", "channel": "WHATSAPP"},
    ).status_code == 404


def test_the_sap_record_is_never_touched_by_sending(client, db, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    customer = customer_of(client, "GULABS")
    before = db.execute(
        select(Customer).where(Customer.id == customer["id"])
    ).scalar_one()
    name, mobile, email = before.name, before.mobile, before.email

    client.post(
        f"/api/customers/{customer['id']}/message/sent",
        headers=headers,
        json={"purpose": "FEEDBACK", "channel": "WHATSAPP"},
    )

    db.refresh(before)
    assert (before.name, before.mobile, before.email) == (name, mobile, email)
