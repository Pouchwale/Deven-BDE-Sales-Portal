"""Turning one parsed response into a Feedback row.

Extracted from `feedback_import.commit()` so the file importer and the Google
Form sync share it rather than growing two copies that drift. Both arrive at
the same place - a dict of column headers to values - so both end up here.

The two callers differ in exactly two ways, and those are parameters:

  * the file importer CREATES departments it has not seen, because an admin
    is watching a dry run and confirmed the mapping. The webhook must not:
    a typo in a form question would silently spawn a department, and nobody
    would be watching.
  * a webhook response can carry a `feedback_request_id`, which is what
    closes the loop back to the ask.

Nothing here commits. The caller owns the transaction, exactly as the
importer always did.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import (
    CustomerActivityType,
    FeedbackMatchStatus,
    FeedbackSource,
)
from app.db.base import utcnow
from app.models.customer import CustomerActivity
from app.models.feedback import Feedback, FeedbackDepartmentRating
from app.models.org import Department, User
from app.services import customer_timeline
from app.services.feedback_mapping import (
    ColumnResolution,
    normalise_header,
    parse_rating,
    parse_timestamp,
)


def key(name: str) -> str:
    """The case- and space-insensitive form used to index people and
    departments by name.

    Deliberately `normalise_header` rather than a fresh implementation - it
    also strips the BOM Excel leaves on the first cell, and two subtly
    different normalisers is how a name stops matching itself.
    """
    return normalise_header(name)


def department_index(db: Session) -> dict[str, Department]:
    return {key(d.name): d for d in db.execute(select(Department)).scalars()}


def user_index(db: Session) -> dict[str, uuid.UUID]:
    return {key(u.name): u.id for u in db.execute(select(User)).scalars()}


def parse_row(
    row: dict[str, str], resolution: ColumnResolution, *, scale_max: int
) -> dict:
    """One source row -> the fields a Feedback needs, all normalised.

    Ratings are rescaled onto `scale_max` and the source wording is kept
    beside the number, so changing the scale later cannot rewrite history.
    """

    def cell(field_name: str) -> str:
        header = resolution.fields.get(field_name)
        return (row.get(header, "") if header else "").strip()

    ratings = {}
    for department, header in resolution.department_ratings.items():
        raw = (row.get(header, "") or "").strip()
        ratings[department] = {
            "raw": raw,
            "rating": str(parse_rating(raw, scale_max=scale_max) or ""),
        }

    overall_raw = cell("overall_rating")
    return {
        "submitted_at_source": (
            timestamp.isoformat()
            if (timestamp := parse_timestamp(cell("submitted_at_source")))
            else None
        ),
        "customer_name": cell("customer_name") or None,
        "company_name": cell("company_name") or None,
        "mobile": cell("mobile") or None,
        "email": cell("email") or None,
        "handled_by_name": cell("handled_by_name") or None,
        "overall_rating_raw": overall_raw or None,
        "overall_rating": str(parse_rating(overall_raw, scale_max=scale_max) or ""),
        "would_recommend": cell("would_recommend") or None,
        "overall_comments": cell("overall_comments") or None,
        "department_ratings": ratings,
    }


def has_any_rating(parsed: dict) -> bool:
    """A response with no score at all is a blank submission, not feedback."""
    return bool(parsed["overall_rating"]) or any(
        entry["rating"] for entry in parsed["department_ratings"].values()
    )


def ensure_departments(
    db: Session, names: Any, departments: dict[str, Department]
) -> list[str]:
    """Create departments the source named but we do not have.

    Only the file importer calls this, and only after an admin confirmed the
    mapping in a dry run.
    """
    created: list[str] = []
    next_order = (db.scalar(select(func.max(Department.sort_order))) or 0) + 10
    for name in names:
        if key(name) in departments:
            continue
        department = Department(
            name=name,
            code=department_code(name, departments),
            sort_order=next_order,
        )
        db.add(department)
        db.flush()
        departments[key(name)] = department
        created.append(name)
        next_order += 10
    return created


def department_code(name: str, existing: dict[str, Department]) -> str:
    """A short unique code for a department discovered in a form."""
    taken = {d.code for d in existing.values()}
    base = "".join(ch for ch in (name or "").upper() if ch.isalnum())[:10] or "DEPT"
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base[:8]}{suffix}"
        if candidate not in taken:
            return candidate
    return f"DEPT{len(existing) + 1}"


@dataclass
class IngestResult:
    feedback: Feedback
    ratings_created: int = 0
    #: Department names the source rated that we do not have. Empty when the
    #: caller allowed creation.
    unknown_departments: list[str] = field(default_factory=list)


def create_feedback(
    db: Session,
    parsed: dict,
    resolution: ColumnResolution,
    row: dict[str, str],
    *,
    source: str,
    source_row_hash: str | None = None,
    import_id: uuid.UUID | None = None,
    feedback_request_id: uuid.UUID | None = None,
    external_response_id: str | None = None,
    match_status: str = FeedbackMatchStatus.MATCHED_CONTACT,
    customer_id: uuid.UUID | None = None,
    departments: dict[str, Department] | None = None,
    users_by_name: dict[str, uuid.UUID] | None = None,
    create_missing_departments: bool = False,
    link_customer: bool = True,
) -> IngestResult:
    """Write one response, its department ratings and its timeline entry.

    `customer_id` may be passed in when the caller already resolved it (the
    webhook resolves it from the request token). Left None, contact matching
    is attempted here - exact only, never fuzzy: a response on the wrong
    account is worse than one on no account.
    """
    departments = department_index(db) if departments is None else departments
    users_by_name = user_index(db) if users_by_name is None else users_by_name

    if create_missing_departments:
        ensure_departments(db, parsed["department_ratings"].keys(), departments)

    feedback = Feedback(
        source_row_hash=source_row_hash,
        import_id=import_id,
        source=source,
        feedback_request_id=feedback_request_id,
        external_response_id=external_response_id,
        match_status=match_status,
        submitted_at_source=(
            datetime.fromisoformat(parsed["submitted_at_source"])
            if parsed["submitted_at_source"]
            else None
        ),
        customer_name=parsed["customer_name"],
        company_name=parsed["company_name"],
        mobile=parsed["mobile"],
        email=parsed["email"],
        handled_by_name=parsed["handled_by_name"],
        handled_by_user_id=users_by_name.get(key(parsed["handled_by_name"] or "")),
        overall_rating=(
            Decimal(parsed["overall_rating"]) if parsed["overall_rating"] else None
        ),
        overall_rating_raw=parsed["overall_rating_raw"],
        overall_comments=parsed["overall_comments"],
        would_recommend=parsed["would_recommend"],
    )

    # A response that already answered a known ask is never ALSO filed under
    # an archived SAP customer just because their contact details coincide.
    feedback.customer_id = (
        customer_id
        if customer_id is not None or not link_customer
        else customer_timeline.match_customer(
            db,
            mobile=parsed["mobile"],
            email=parsed["email"],
            company=parsed["company_name"],
        )
    )

    db.add(feedback)
    db.flush()

    if feedback.customer_id is not None:
        db.add(
            CustomerActivity(
                customer_id=feedback.customer_id,
                actor_user_id=None,
                activity_type=CustomerActivityType.FEEDBACK_RECEIVED,
                remark=(
                    f"Overall {parsed['overall_rating'] or '—'}"
                    + (
                        f" · {parsed['overall_comments']}"
                        if parsed["overall_comments"]
                        else ""
                    )
                )[:2000],
                related_type="FEEDBACK",
                related_id=feedback.id,
                created_at=feedback.submitted_at_source or utcnow(),
            )
        )

    ratings_created = 0
    unknown: list[str] = []
    for department_name, entry in parsed["department_ratings"].items():
        department = departments.get(key(department_name))
        if department is None:
            # Kept, not dropped: an unmapped department is an admin's problem
            # to resolve, not a rating to lose.
            if entry["rating"] or entry["raw"]:
                unknown.append(department_name)
            continue
        if not entry["rating"] and not entry["raw"]:
            continue
        comment_header = resolution.department_comments.get(department_name)
        db.add(
            FeedbackDepartmentRating(
                feedback_id=feedback.id,
                department_id=department.id,
                rating=Decimal(entry["rating"]) if entry["rating"] else None,
                raw_value=entry["raw"][:40] or None,
                comments=(row.get(comment_header, "").strip() or None)
                if comment_header
                else None,
            )
        )
        ratings_created += 1

    return IngestResult(
        feedback=feedback,
        ratings_created=ratings_created,
        unknown_departments=unknown,
    )


__all__ = [
    "FeedbackSource",
    "IngestResult",
    "create_feedback",
    "department_code",
    "department_index",
    "ensure_departments",
    "has_any_rating",
    "key",
    "parse_row",
    "user_index",
]
