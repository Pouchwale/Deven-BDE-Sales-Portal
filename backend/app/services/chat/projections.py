"""ORM rows -> the minimal dicts the model is allowed to see.

This module is the PII boundary. Nothing else in the assistant may hand an ORM
object to the language model, and every field the model sees passes through a
function here.

Two rules shape what is in each projection:

  * Contact details are opt-in. `mobile` and `email` appear only in the
    single-record projections, where the user has clearly asked about that one
    person. List projections carry a name, a status and an id.
  * Ids travel instead of records. A card deep-links into the portal, where the
    full row is read under the normal UI permissions.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _number(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


def owner_of(names: dict[uuid.UUID, str], key: uuid.UUID | None) -> str | None:
    """Name for an owner id that the column allows to be NULL.

    An unassigned row is normal, not an error - `None` in, `None` out.
    """
    return names.get(key) if key is not None else None


def listing(items: list[dict], total: int | None = None, *, truncated: bool = False) -> dict:
    """The envelope every list tool returns.

    `count` is what came back; `total` is how many exist. A model that can see
    both can say "showing 25 of 63" instead of implying it saw everything.
    """
    payload: dict[str, Any] = {"count": len(items), "items": items}
    if total is not None:
        payload["total"] = total
    if truncated:
        payload["truncated"] = True
    return payload


# ------------------------------------------------------------------ leads
def lead_row(lead, owner_name: str | None = None) -> dict:
    """One lead in a list. No contact details."""
    return {
        "id": str(lead.id),
        "name": lead.name,
        "company": lead.company_name,
        "status": lead.status,
        "priority": lead.priority,
        "owner": owner_name,
        "next_follow_up_date": _iso(lead.next_follow_up_date),
        "converted": lead.status == "CONVERTED",
    }


def lead_detail(lead, owner_name: str | None = None, activity_count: int = 0) -> dict:
    """One lead, asked about by name. Contact details included."""
    return {
        **lead_row(lead, owner_name),
        "mobile": lead.mobile,
        "email": lead.email,
        "city": lead.city,
        "requirement": lead.requirement,
        "reference_status": lead.reference_status,
        "created_at": _iso(lead.created_at),
        "closed_at": _iso(lead.closed_at),
        "activity_count": activity_count,
    }


# -------------------------------------------------------------- customers
def customer_row(customer, owner_name: str | None = None) -> dict:
    return {
        "id": str(customer.id),
        "name": customer.name,
        "sap_code": customer.sap_code,
        "owner": owner_name,
        "reference_status": customer.reference_status,
        "invoice_count": customer.invoice_count,
        "last_invoice_date": _iso(customer.last_invoice_date),
    }


def customer_detail(customer, owner_name: str | None = None) -> dict:
    return {
        **customer_row(customer, owner_name),
        "mobile": customer.mobile,
        "email": customer.email,
        "next_reference_date": _iso(customer.next_reference_date),
        "last_reference_asked_at": _iso(customer.last_reference_asked_at),
    }


# ------------------------------------------------------------- references
def reference_row(reference, source_name: str | None = None) -> dict:
    """A recorded ask. The referred person's contact details are included:
    a referral you cannot contact is not a referral, and the user asking about
    their own references is entitled to them."""
    return {
        "id": str(reference.id),
        "outcome": reference.outcome,
        "asked_on": _iso(reference.asked_on),
        "referred_by": source_name,
        "referred_name": reference.referred_name,
        "referred_company": reference.referred_company,
        "referred_mobile": reference.referred_mobile,
        "referred_email": reference.referred_email,
        "notes": reference.notes,
        "next_reference_date": _iso(reference.next_reference_date),
    }


def askable_account_row(row: dict) -> dict:
    """`references.askable_accounts` already returns dicts - reshape, do not
    pass through, or a later field added there leaks silently."""
    return {
        "id": str(row["subject_id"]),
        "type": row["subject_type"],
        "name": row["subject_name"],
        "company": row.get("company_name"),
        "sap_code": row.get("sap_code"),
        "owner": row.get("owner_name"),
        "reference_status": row["reference_status"],
        "decline_count": row.get("decline_count", 0),
        "last_reference_asked_at": _iso(row.get("last_reference_asked_at")),
    }


def follow_up_row(row: dict) -> dict:
    return {
        "id": str(row["subject_id"]),
        "type": row["subject_type"],
        "name": row["subject_name"],
        "sap_code": row.get("sap_code"),
        "owner": row.get("owner_name"),
        "due": _iso(row["next_reference_date"]),
        "days_overdue": row["days_overdue"],
        "decline_count": row.get("decline_count", 0),
    }


# --------------------------------------------------------------- feedback
def pending_request_row(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "name": row["name"],
        "company": row.get("company_name"),
        "owner": row.get("owner_name"),
        "waiting_since": _iso(row.get("since")),
    }


def department_rating_row(row: dict) -> dict:
    return {
        "department": row["department_name"],
        "average_rating": _number(row.get("average_rating")),
        "response_count": row["response_count"],
        "below_threshold": row["below_threshold"],
        "enough_responses": row.get("enough_responses", True),
    }


def alert_row(alert, department_name: str | None = None) -> dict:
    return {
        "department": department_name,
        "average_rating": _number(alert.average_rating),
        "response_count": alert.response_count,
        "threshold": _number(alert.threshold),
        "opened_at": _iso(alert.opened_at),
        "status": alert.status,
    }


def customer_review_row(row, department_names: dict) -> dict:
    """One customer's response: what they scored, and what they wrote.

    No mobile and no email, ever - not even for an administrator who could
    read them in the UI. The same rule the result cards follow: a chat
    transcript is the wrong place for a customer's phone number, and the
    model has no use for one it could not dial.
    """
    out: dict = {
        "customer": row.customer_name,
        "submitted": _iso(row.submitted_at_source or row.created_at),
        "overall_rating": _number(row.overall_rating),
    }
    # Empty keys are dropped rather than sent as null. A review carries a lot
    # of optional prose, and `"comment": null` on six departments across ten
    # responses is a few hundred tokens spent saying nothing - against a
    # provider budget measured per minute.
    if row.company_name and row.company_name != row.customer_name:
        out["company"] = row.company_name
    if row.would_recommend:
        out["would_recommend"] = row.would_recommend
    if row.overall_comments:
        out["comment"] = row.overall_comments

    departments = []
    for rating in row.department_ratings:
        entry: dict = {
            "department": department_names.get(rating.department_id),
            "rating": _number(rating.rating),
        }
        if rating.comments:
            entry["comment"] = rating.comments
        departments.append(entry)
    if departments:
        out["departments"] = departments
    return out


# ---------------------------------------------------------------- people
def team_member_row(row: dict) -> dict:
    """A colleague in the caller's own subtree. Work counts only - no contact
    details, because "how is my team doing" is not a request for a directory.

    Keys mirror `dashboard.report_rows`, which is where these come from.
    """
    return {
        "id": str(row["user_id"]),
        "name": row["name"],
        "role": row["role"],
        "team": row.get("team_name"),
        "is_active": row.get("is_active", True),
        "open_leads": row["open_leads"],
        "converted": row.get("converted", 0),
        "follow_ups_due": row["followups_due"],
        "references_taken": row["references_taken"],
        # Lead activity, not SAP. `customers` and `last_invoice_date` used to be
        # here; the Team payload no longer carries them, so they read as
        # "everyone has 0 customers and has never invoiced" - which the model
        # would repeat as fact.
        "last_activity_at": _iso(row.get("last_activity_at")),
    }


# --------------------------------------------------------- notifications
def notification_row(notification) -> dict:
    return {
        "id": str(notification.id),
        "type": notification.type,
        "title": notification.title,
        "body": notification.body,
        "is_read": notification.is_read,
        "created_at": _iso(notification.created_at),
    }


# ------------------------------------------------------------- timeline
def timeline_entry_row(entry: dict) -> dict:
    return {
        "kind": entry["kind"],
        "activity_type": entry.get("activity_type"),
        "title": entry.get("title"),
        "remark": entry.get("remark"),
        "actor": entry.get("actor_name"),
        "rating": entry.get("rating"),
        "undone": entry.get("undone_at") is not None,
        "created_at": _iso(entry.get("created_at")),
    }
