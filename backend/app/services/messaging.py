"""Ready-to-send request messages for WhatsApp and email.

The team should never retype "could you leave us a review, here is the link".
The template lives in `app_settings` (one place, editable by an admin without
a deploy), the server renders it with the customer and sender filled in, and
the client opens WhatsApp or the mail app with the text already in it.

Two deliberate choices:

  * Rendering happens on the SERVER, so what gets logged on the customer's
    timeline is the same text the template produced — not something the
    browser assembled separately.
  * Placeholders are substituted by an allow-list, never by `str.format` on a
    user-editable string. `format` on an admin-typed template turns a stray
    brace into a KeyError, and `{0.__class__}` into an information leak.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.errors import invalid
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.org import User
from app.services import runtime_settings

# Anything with name/mobile/email compose() can address. A Customer also has
# sap_code; a Lead does not, so a Lead's {sap_code} placeholder is left blank.
MessageSubject = Customer | Lead

_DIGITS = re.compile(r"\D+")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# India. Numbers in the SAP book are stored as exported — `+91 97111 22505`,
# `9701082000` — so the country code is added only when it is missing.
DEFAULT_COUNTRY_CODE = "91"
NATIONAL_NUMBER_LENGTH = 10


class Purpose(StrEnum):
    FEEDBACK = "FEEDBACK"


class Channel(StrEnum):
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


# purpose -> (link setting, whatsapp template, email subject, email body)
TEMPLATE_KEYS: dict[str, tuple[str, str, str, str]] = {
    Purpose.FEEDBACK: (
        "company.feedback_form_url",
        "message.feedback_whatsapp",
        "message.feedback_email_subject",
        "message.feedback_email_body",
    ),
}


@dataclass(frozen=True, slots=True)
class ComposedMessage:
    purpose: str
    channel: str
    to: str | None
    subject: str | None
    body: str
    link: str
    # The deep link the browser opens: wa.me/... or mailto:...
    url: str | None
    link_configured: bool
    missing_contact: str | None
    # The normalised digits wa.me expects. Returned so the client can rebuild
    # the link when somebody edits the message before sending, without
    # duplicating the country-code rules in JavaScript.
    whatsapp_number: str | None = None

    def as_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "channel": self.channel,
            "to": self.to,
            "subject": self.subject,
            "body": self.body,
            "link": self.link,
            "url": self.url,
            "link_configured": self.link_configured,
            "missing_contact": self.missing_contact,
            "whatsapp_number": self.whatsapp_number,
        }


def render(template: str, context: dict[str, str]) -> str:
    """Substitute {placeholders} from an allow-list.

    An unknown placeholder is left exactly as typed rather than raising, so a
    typo in a template shows up in the preview instead of breaking the button.
    """
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return context.get(key, match.group(0))

    return _PLACEHOLDER.sub(replace, template or "")


def whatsapp_number(mobile: str | None) -> str | None:
    """Turn a stored mobile into the digits wa.me expects.

    Returns None when there is nothing usable — better an explained "no
    mobile on file" than a link that opens a chat with the wrong person.
    """
    digits = _DIGITS.sub("", mobile or "")
    if not digits:
        return None
    if len(digits) == NATIONAL_NUMBER_LENGTH:
        return DEFAULT_COUNTRY_CODE + digits
    # Already carries a country code (or a leading 0 that has to go).
    digits = digits.lstrip("0")
    if len(digits) == NATIONAL_NUMBER_LENGTH:
        return DEFAULT_COUNTRY_CODE + digits
    return digits if len(digits) >= 11 else None


def whatsapp_url(mobile: str | None, text: str) -> str | None:
    number = whatsapp_number(mobile)
    if number is None:
        return None
    return f"https://wa.me/{number}?text={urllib.parse.quote(text)}"


def mailto_url(email: str | None, subject: str, body: str) -> str | None:
    if not email:
        return None
    query = urllib.parse.urlencode(
        {"subject": subject, "body": body}, quote_via=urllib.parse.quote
    )
    return f"mailto:{email}?{query}"


def prefilled(link: str, entry_id: str | None, code: str | None) -> str:
    """Add the feedback reference to a Google Form URL.

    Google Forms prefills a field with `?usp=pp_url&entry.<id>=<value>`, and
    that value comes back as its own column in the response sheet - which is
    how a submission finds its way back to the ask that caused it.

    Anything missing (no form URL, no entry id, no request) leaves the link
    exactly as configured. A form without the reference field still works;
    its responses just fall back to contact matching.
    """
    if not link or not entry_id or not code:
        return link

    entry = entry_id.strip()
    if not entry.startswith("entry."):
        # Admins paste the whole prefill URL, or just the number. Accept both.
        digits = "".join(ch for ch in entry if ch.isdigit())
        if not digits:
            return link
        entry = f"entry.{digits}"

    separator = "&" if "?" in link else "?"
    query = urllib.parse.urlencode({"usp": "pp_url", entry: code})
    return f"{link}{separator}{query}"


def compose(
    db: Session,
    subject: MessageSubject,
    sender: User,
    *,
    purpose: str,
    channel: str,
    reference_code: str | None = None,
) -> ComposedMessage:
    """Build the message and the deep link that opens it.

    `subject` is a Customer or a Lead — anything with name/mobile/email.
    `sap_code` is Customer-only; a Lead leaves that placeholder blank.

    `reference_code` is the feedback request's `FB-….token`, prefilled into
    the form so the response can be matched back. Absent, the link is sent
    exactly as configured.
    """
    if purpose not in TEMPLATE_KEYS:
        raise invalid(f"Unknown request type {purpose!r}.")
    if channel not in (Channel.WHATSAPP, Channel.EMAIL):
        raise invalid(f"Unknown channel {channel!r}.")

    link_key, whatsapp_key, subject_key, body_key = TEMPLATE_KEYS[purpose]
    link = str(runtime_settings.get(db, link_key) or "")

    if purpose == Purpose.FEEDBACK and reference_code:
        link = prefilled(
            link,
            str(runtime_settings.get(db, "feedback.form_reference_entry_id") or ""),
            reference_code,
        )

    context = {
        "customer_name": subject.name,
        "sap_code": getattr(subject, "sap_code", "") or "",
        "sender_name": sender.name,
        "our_company": str(runtime_settings.get(db, "company.name") or "Pouchwale"),
        "link": link,
    }

    if channel == Channel.WHATSAPP:
        body = render(str(runtime_settings.get(db, whatsapp_key) or ""), context)
        return ComposedMessage(
            purpose=purpose,
            channel=channel,
            to=subject.mobile,
            subject=None,
            body=body,
            link=link,
            url=whatsapp_url(subject.mobile, body) if link else None,
            link_configured=bool(link),
            missing_contact=None if whatsapp_number(subject.mobile) else "mobile",
            whatsapp_number=whatsapp_number(subject.mobile),
        )

    email_subject = render(str(runtime_settings.get(db, subject_key) or ""), context)
    body = render(str(runtime_settings.get(db, body_key) or ""), context)
    return ComposedMessage(
        purpose=purpose,
        channel=channel,
        to=subject.email,
        subject=email_subject,
        body=body,
        link=link,
        url=mailto_url(subject.email, email_subject, body) if link else None,
        link_configured=bool(link),
        missing_contact=None if subject.email else "email",
    )
