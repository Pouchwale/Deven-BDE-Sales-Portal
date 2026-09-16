"""Sample customer reviews, so the feedback module can be seen working.

WHY THIS EXISTS, AND WHY IT IS SEPARATE
---------------------------------------
Everywhere else in this project the rule is that nothing is invented: every
number on a screen comes from the real SAP import or from work somebody did in
the portal. `app/seeds/seed.py` says so and holds to it.

This module is the one deliberate exception, and it is a separate file with a
separate command precisely so the exception stays visible instead of leaking
into the normal seed.

The real customer feedback arrives from a Google Form / sheet that is not
connected yet. Until it is, the Customer reviews screen has nothing to draw
and looks broken rather than empty. These rows fill it in so the team can see
the shape of the thing - what a review looks like, how one customer's scores
roll up into a department average, and what a flagged department looks like.

HOW IT IS MARKED
----------------
Every row belongs to one `feedback_imports` batch whose source is SAMPLE. That
is what the API reads to mark each response `is_sample`, and what the UI badges
so nobody mistakes this for a real customer's opinion. There is no hidden flag
and no magic string in the data itself.

HOW TO REMOVE IT
----------------
    python -m app.seeds.sample_feedback --remove

That deletes the responses, their department ratings, and the batch. Nothing
else references them, so nothing else changes. Run it before the portal carries
real feedback.

    python -m app.seeds.sample_feedback            # load (idempotent)
    python -m app.seeds.sample_feedback --remove   # delete
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.constants import (
    FeedbackImportSource,
    FeedbackMatchStatus,
    FeedbackSource,
    ImportStatus,
)
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models.customer import Customer
from app.models.feedback import Feedback, FeedbackDepartmentRating, FeedbackImport
from app.models.org import Department, User

SAMPLE_FILENAME = "SAMPLE DATA - remove before go-live"


# (department name, rating, comment). A blank comment is normal: most people
# score every question and write on one or two.
Scores = tuple[tuple[str, int, str], ...]


class Review:
    __slots__ = ("sap_code", "days_ago", "overall", "recommend", "comment", "scores")

    def __init__(
        self,
        sap_code: str,
        days_ago: int,
        overall: int,
        recommend: str,
        comment: str,
        scores: Scores,
    ) -> None:
        self.sap_code = sap_code
        self.days_ago = days_ago
        self.overall = overall
        self.recommend = recommend
        self.comment = comment
        self.scores = scores


# Eight responses against real accounts from the SAP book. The spread is not
# random: Dispatch is the weak department across several customers, so the
# department average drops below the alert threshold and the whole chain -
# review, rolled-up average, flagged department - can be seen end to end.
REVIEWS: tuple[Review, ...] = (
    Review(
        "C2080", 3, 5, "Yes",
        "Third order with Pouchwale. The pouches arrive looking like the proof, "
        "which is not something we could say about our last supplier.",
        (
            ("Sales", 5, "Understood the artwork brief on the first call."),
            ("Production", 5, ""),
            ("Quality", 5, "Seal strength has been consistent across all three runs."),
            ("Dispatch", 3, "A day later than committed, but they told us in advance."),
            ("Accounts", 5, ""),
            ("Customer Support", 5, ""),
        ),
    ),
    Review(
        "C1730", 6, 3, "Maybe",
        "No complaint about the pouches themselves. The delivery date is the problem.",
        (
            ("Sales", 5, "Pricing and samples were handled quickly."),
            ("Production", 4, ""),
            ("Quality", 4, ""),
            ("Dispatch", 2, "Committed 12 days, delivered on the 19th. We had to move our launch."),
            ("Accounts", 3, ""),
            ("Customer Support", 3, "Had to chase for a status update twice."),
        ),
    ),
    Review(
        "C1282", 9, 2, "No",
        "Wanted to like it. The seal failures cost us a full day of repacking.",
        (
            ("Sales", 4, ""),
            ("Production", 2, "Around 200 pouches out of 5000 had a weak seal."),
            ("Quality", 2, "The batch should not have passed a check."),
            ("Dispatch", 3, ""),
            ("Accounts", 4, ""),
            ("Customer Support", 3, "Raised it on WhatsApp, took two days to get a reply."),
        ),
    ),
    Review(
        "C2077", 12, 4, "Yes",
        "Good product. Sort the delivery timing out and we would order more often.",
        (
            ("Sales", 4, ""),
            ("Production", 4, ""),
            ("Quality", 5, "Matte finish came out exactly as the sample."),
            ("Dispatch", 2, "Two days later than promised, with no call."),
            ("Accounts", 4, ""),
            ("Customer Support", 4, ""),
        ),
    ),
    Review(
        "C1567", 15, 4, "Yes",
        "Happy overall. The billing needs attention.",
        (
            ("Sales", 5, "Manish got us a revised quote the same day."),
            ("Production", 4, ""),
            ("Quality", 4, ""),
            ("Dispatch", 4, ""),
            ("Accounts", 2, "Invoice carried our old GST number and took three emails to correct."),
            ("Customer Support", 4, ""),
        ),
    ),
    Review(
        "C2278", 18, 5, "Yes",
        "Quick sampling and a fair price. We got the finish right on the second trial.",
        (
            ("Sales", 5, ""),
            ("Production", 5, "Turned the repeat order around in nine days."),
            ("Quality", 4, ""),
            ("Dispatch", 5, "Arrived a day early, properly packed."),
            ("Accounts", 4, ""),
            ("Customer Support", 5, ""),
        ),
    ),
    Review(
        "C2262", 22, 3, "Maybe",
        "The team is easy to deal with. Scheduling is where it falls down.",
        (
            ("Sales", 4, ""),
            ("Production", 3, ""),
            ("Quality", 4, ""),
            ("Dispatch", 2, "Second consignment in a row that slipped by about a week."),
            ("Accounts", 3, ""),
            ("Customer Support", 4, ""),
        ),
    ),
    Review(
        "C2132", 26, 4, "Yes",
        "Reliable quality. We keep coming back for that.",
        (
            ("Sales", 4, ""),
            ("Production", 4, ""),
            ("Quality", 4, ""),
            ("Dispatch", 2, "Transport was arranged late and nobody told us."),
            ("Accounts", 4, ""),
            ("Customer Support", 3, ""),
        ),
    ),
)


def _batch(db: Session) -> FeedbackImport | None:
    return db.execute(
        select(FeedbackImport).where(
            FeedbackImport.source == FeedbackImportSource.SAMPLE
        )
    ).scalars().first()


def load(db: Session) -> int:
    """Create the sample batch. Idempotent - a second run creates nothing."""
    if _batch(db) is not None:
        print("Sample feedback is already loaded. Nothing to do.")
        return 0

    departments = {
        row.name: row for row in db.execute(select(Department)).scalars()
    }
    customers = {
        row.sap_code: row for row in db.execute(select(Customer)).scalars()
    }
    owners = {row.id: row for row in db.execute(select(User)).scalars()}

    missing = {r.sap_code for r in REVIEWS} - set(customers)
    if missing:
        print(
            "These SAP accounts are not in the database: "
            + ", ".join(sorted(missing))
            + "\nRun `python -m app.seeds.seed` first.",
            file=sys.stderr,
        )
        return 1

    batch = FeedbackImport(
        filename=SAMPLE_FILENAME,
        source=FeedbackImportSource.SAMPLE,
        total_rows=len(REVIEWS),
        created_count=len(REVIEWS),
        skipped_count=0,
        error_count=0,
        status=ImportStatus.SUCCESS,
    )
    db.add(batch)
    db.flush()

    now = utcnow()
    for review in REVIEWS:
        customer = customers[review.sap_code]
        owner = owners.get(customer.owner_user_id)

        response = Feedback(
            import_id=batch.id,
            source=FeedbackSource.GOOGLE_FORMS_IMPORT,
            submitted_at_source=now - timedelta(days=review.days_ago),
            customer_name=customer.name,
            company_name=customer.name,
            mobile=customer.mobile,
            email=customer.email,
            handled_by_name=owner.name if owner else None,
            handled_by_user_id=owner.id if owner else None,
            overall_rating=Decimal(review.overall),
            overall_rating_raw=f"{review.overall} out of 5",
            overall_comments=review.comment,
            would_recommend=review.recommend,
            # Linked to the real account, which is what makes the customer's
            # own page show its feedback too.
            customer_id=customer.id,
            match_status=FeedbackMatchStatus.MATCHED_CONTACT,
        )
        db.add(response)
        db.flush()

        for name, rating, comment in review.scores:
            department = departments.get(name)
            if department is None:
                continue
            db.add(
                FeedbackDepartmentRating(
                    feedback_id=response.id,
                    department_id=department.id,
                    rating=Decimal(rating),
                    raw_value=f"{rating} out of 5",
                    comments=comment or None,
                )
            )

    db.commit()
    print(f"Loaded {len(REVIEWS)} sample reviews.")
    print("Remove them with: python -m app.seeds.sample_feedback --remove")
    return 0


def remove(db: Session) -> int:
    """Delete the sample batch and everything in it."""
    batch = _batch(db)
    if batch is None:
        print("No sample feedback is loaded. Nothing to do.")
        return 0

    ids = list(
        db.execute(
            select(Feedback.id).where(Feedback.import_id == batch.id)
        ).scalars()
    )
    # Department ratings cascade from feedback, but be explicit rather than
    # trusting a cascade that only some dialects enforce.
    if ids:
        db.execute(
            delete(FeedbackDepartmentRating).where(
                FeedbackDepartmentRating.feedback_id.in_(ids)
            )
        )
        db.execute(delete(Feedback).where(Feedback.id.in_(ids)))
    db.delete(batch)
    db.commit()
    print(f"Removed {len(ids)} sample reviews.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remove",
        action="store_true",
        help="delete the sample reviews instead of creating them",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        return remove(db) if args.remove else load(db)


if __name__ == "__main__":
    raise SystemExit(main())
