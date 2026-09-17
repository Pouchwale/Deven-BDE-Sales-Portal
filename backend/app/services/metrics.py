"""Every headline number in the portal, defined once.

WHY THIS FILE EXISTS
--------------------
The dashboard, Assigned Leads, the Team page and the assistant all used to
count things for themselves. Four implementations of "how many leads" is four
chances to disagree, and they did: one module counted portal leads while
another counted SAP accounts, so the same person saw "22 converted" on one
screen and "17 accounts" on the next.

Everything here takes a `scope` - the set of user ids the caller may see, or
`ALL` - and answers within it. Nothing here decides WHO may see what; that
stays with `core.authority`, which produced the scope. Two separate jobs:
authority decides the audience, this decides the arithmetic.

THE CANONICAL UNIVERSE
----------------------
`leads` is the operational universe. Every lead count, every stage count and
every conversion figure comes from it.

`post_sale_records` is what an external sheet adds to a WON lead - principally
the invoice date. Reference and feedback work is measured against those,
because a customer becomes askable ten days after they were invoiced, not the
moment a salesperson marked the deal won.

`customers` (the old SAP book) drives NOTHING here. It is history.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from typing import Iterable, Iterator

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core import eligibility
from app.core.authority import ALL, _All
from app.core.constants import (
    CLOSED_LEAD_STATUSES,
    OPEN_LEAD_STATUSES,
    LeadStatus,
    ReferenceStatus,
)
from app.models.lead import Lead
from app.models.post_sale import PostSaleRecord

# --------------------------------------------------------------- scoping


def scoped_leads(scope: set[uuid.UUID] | _All) -> Select:
    """Every lead the caller may see. The one place that decision is applied.

    An empty scope is not "everything" - it is "nothing", and the difference
    matters: getting it wrong the other way shows one person's pipeline to
    somebody with no business seeing it.
    """
    stmt = select(Lead)
    if scope is ALL:
        return stmt
    if not scope:
        return stmt.where(Lead.id.in_([]))
    return stmt.where(Lead.assigned_to_user_id.in_(scope))


# ----------------------------------------------------------------- leads


def lead_metrics(db: Session, scope: set[uuid.UUID] | _All) -> dict:
    """Lead counts by stage, within scope.

    ONE grouped query over one statement, so "total" is by construction the
    sum of the parts rather than a separately-derived number that can drift
    from them. (This used to be thirteen COUNT queries - one per stage plus
    total/open/closed - on every dashboard and Assigned Leads load.)
    """
    stmt = (
        scoped_leads(scope)
        .with_only_columns(Lead.status, func.count(Lead.id))
        .group_by(Lead.status)
    )
    per_status: dict[str, int] = {
        str(status): count for status, count in db.execute(stmt).all()
    }

    def total_of(statuses) -> int:
        return sum(per_status.get(str(status), 0) for status in statuses)

    by_stage = {
        member.value.lower(): per_status.get(member.value, 0) for member in LeadStatus
    }
    return {
        # Every row, including any status outside the current enum (retired
        # names), exactly as the unfiltered COUNT(*) counted them.
        "total": sum(per_status.values()),
        "open": total_of(OPEN_LEAD_STATUSES),
        "closed": total_of(CLOSED_LEAD_STATUSES),
        **by_stage,
    }


def converted_lead_ids(db: Session, scope: set[uuid.UUID] | _All) -> list[uuid.UUID]:
    """The won leads in scope. The starting population for everything
    post-sale, and the reason those numbers can finally be reconciled against
    the pipeline they came from."""
    stmt = scoped_leads(scope).where(Lead.status == LeadStatus.CONVERTED)
    return list(db.execute(stmt.with_only_columns(Lead.id)).scalars())


# ------------------------------------------------------------- post-sale


def eligibility_invoice_dates(
    db: Session, lead_ids: list[uuid.UUID]
) -> dict[uuid.UUID, date]:
    """The ONE invoice date each lead's eligibility runs from.

    A lead can have several matched post-sale rows - a customer who orders
    again is invoiced again - and every module used to pick one for itself:
    the metrics kept whichever row an unordered query returned LAST, the ask
    guard took whichever came FIRST. So a repeat order could silently make an
    account that had already given a reference "not eligible" again, drop it
    out of Reference Tracking, and leave the guard and the counts disagreeing
    about the very same lead.

    POLICY: the EARLIEST matched invoice. Eligibility then only ever moves
    forward - once a customer has been askable, a later order cannot take
    that back. Whether the business would rather restart the clock on every
    new invoice is a decision, not a bug; if so, `func.max` below is the whole
    change, because nothing else in the portal chooses a date.
    """
    if not lead_ids:
        return {}
    return dict(
        db.execute(
            select(PostSaleRecord.lead_id, func.min(PostSaleRecord.invoice_date))
            .where(
                PostSaleRecord.lead_id.in_(lead_ids),
                PostSaleRecord.status == "MATCHED",
                PostSaleRecord.invoice_date.is_not(None),
            )
            .group_by(PostSaleRecord.lead_id)
        ).all()
    )


def reference_ready_dates(
    db: Session, lead_ids: list[uuid.UUID]
) -> dict[uuid.UUID, date]:
    """The ONE day each lead may be asked for a reference.

    The SAP workbook publishes that day in its Reference Date column, so it is
    used verbatim. A lead with no such column (anything not from the workbook)
    falls back to invoice date + 10 days - `core.eligibility` owns both rules.

    Keyed to the EARLIEST invoice, for the same reason as
    `eligibility_invoice_dates`: a repeat order must not pull an account that
    was already askable back out of the queue.
    """
    if not lead_ids:
        return {}
    rows = db.execute(
        select(
            PostSaleRecord.lead_id,
            PostSaleRecord.invoice_date,
            PostSaleRecord.reference_date,
        ).where(
            PostSaleRecord.lead_id.in_(lead_ids),
            PostSaleRecord.status == "MATCHED",
            PostSaleRecord.invoice_date.is_not(None),
        )
    ).all()

    first: dict[uuid.UUID, tuple[date, date | None]] = {}
    for lead_id, invoice_date, reference_date in rows:
        current = first.get(lead_id)
        if current is None or invoice_date < current[0]:
            first[lead_id] = (invoice_date, reference_date)
    ready: dict[uuid.UUID, date] = {}
    for lead_id, (invoice_date, reference_date) in first.items():
        day = eligibility.ready_on(invoice_date, reference_date)
        if day is not None:      # invoice_date is never NULL here
            ready[lead_id] = day
    return ready


def post_sale_population(
    db: Session, scope: set[uuid.UUID] | _All, *, today: date | None = None
) -> dict:
    """How much of the won book is actually actionable, and why not.

    Four numbers that always add up, which is the point:

        converted      won leads in scope
        eligible       ...whose reference date has arrived
        waiting        ...synced, but that date is still ahead
        awaiting_sync  ...no post-sale record at all yet

        eligible + waiting + awaiting_sync == converted

    `awaiting_sync` is what stops the modules reading as "zero customers" when
    the truth is "nothing has been synced yet". Those are very different
    problems and a bare 0 cannot tell them apart.
    """
    day = today or date.today()

    memo = db.info.get(_POPULATION_MEMO)
    memo_key = ("ALL" if scope is ALL else frozenset(scope), day)
    if memo is not None and memo_key in memo:
        cached = memo[memo_key]
        # A fresh list: callers are free to mutate what they get back.
        return {**cached, "eligible_lead_ids": list(cached["eligible_lead_ids"])}

    converted, ready = _converted_with_ready_dates(db, scope)
    if not converted:
        result = {
            "converted": 0,
            "eligible": 0,
            "waiting": 0,
            "awaiting_sync": 0,
            "eligible_lead_ids": [],
        }
        if memo is not None:
            memo[memo_key] = result
        return {**result, "eligible_lead_ids": []}

    eligible_ids, waiting = [], 0
    for lead_id in converted:
        ready_on = ready.get(lead_id)
        if ready_on is None:
            continue
        if ready_on <= day:
            eligible_ids.append(lead_id)
        else:
            waiting += 1

    result = {
        "converted": len(converted),
        "eligible": len(eligible_ids),
        "waiting": waiting,
        "awaiting_sync": len(converted) - len(eligible_ids) - waiting,
        "eligible_lead_ids": eligible_ids,
    }
    if memo is not None:
        memo[memo_key] = {**result, "eligible_lead_ids": list(eligible_ids)}
    return result


def _converted_with_ready_dates(
    db: Session, scope: set[uuid.UUID] | _All
) -> tuple[list[uuid.UUID], dict[uuid.UUID, date]]:
    """The won leads in scope, and each one's reference-ready day.

    The same answer as `converted_lead_ids` followed by
    `reference_ready_dates`, but the second query JOINS to the scoped won
    leads instead of sending every converted lead id back to the database as
    a bound-parameter IN list - a list that grew with the won book and, on
    SQLite, fails outright past its variable limit. (A LEFT JOIN from the
    leads side was tried first; SQLite planned it as a nested scan, 20x
    slower.) The EARLIEST matched invoice still decides the day, exactly as
    `reference_ready_dates` documents.
    """
    converted = converted_lead_ids(db, scope)
    if not converted:
        return [], {}

    stmt = (
        select(
            PostSaleRecord.lead_id,
            PostSaleRecord.invoice_date,
            PostSaleRecord.reference_date,
        )
        .join(Lead, Lead.id == PostSaleRecord.lead_id)
        .where(
            PostSaleRecord.status == "MATCHED",
            PostSaleRecord.invoice_date.is_not(None),
            Lead.status == LeadStatus.CONVERTED,
        )
    )
    if scope is not ALL:
        # Never empty here: an empty scope has no converted leads, above.
        stmt = stmt.where(Lead.assigned_to_user_id.in_(scope))

    first: dict[uuid.UUID, tuple[date, date | None]] = {}
    for lead_id, invoice_date, reference_date in db.execute(stmt).all():
        current = first.get(lead_id)
        if current is None or invoice_date < current[0]:
            first[lead_id] = (invoice_date, reference_date)

    ready: dict[uuid.UUID, date] = {}
    for lead_id, (invoice_date, reference_date) in first.items():
        day = eligibility.ready_on(invoice_date, reference_date)
        if day is not None:
            ready[lead_id] = day
    return converted, ready


#: `Session.info` key for the population memo - see `memoized_population`.
_POPULATION_MEMO = "metrics.post_sale_population"


@contextmanager
def memoized_population(db: Session) -> Iterator[None]:
    """Compute `post_sale_population` once per scope inside this block.

    The dashboard asks for the caller's population twice in one request (the
    reference KPIs and the feedback-pending count), and each ask was a full
    pass over the won book. Deliberately an explicit,
    read-only block rather than a session-wide cache: nothing inside it
    writes, so a memoised answer cannot go stale, and code outside it always
    reads the database afresh.
    """
    if _POPULATION_MEMO in db.info:  # nested: the outer block owns the memo
        yield
        return
    db.info[_POPULATION_MEMO] = {}
    try:
        yield
    finally:
        db.info.pop(_POPULATION_MEMO, None)


# ------------------------------------------------------------- reference


def reference_score(total: int, taken: int) -> float:
    """The reference score: the part of the book still owing a reference.

    Agreed with the business as a negative percentage, so it reads as a gap to
    close rather than as a mark out of ten:

        10 accounts, 7 references  ->  (10 - 7) / 10  ->  -30.0
        10 accounts, 10 references ->                 ->    0.0
        10 accounts, none          ->                 -> -100.0

    The denominator is the ELIGIBLE book - the same population every other
    reference number on the screen uses - so an account nobody may ask yet
    never counts against the person holding it.
    """
    if not total:
        return 0.0
    gap = round((total - taken) / total * 100, 1)
    # `-0.0` is a real float and renders as "-0%", which reads as a penalty for
    # the one person who has asked everybody. Nothing outstanding is 0%.
    return -gap if gap else 0.0


def reference_scores_by_user(
    db: Session, user_ids: Iterable[uuid.UUID], *, today: date | None = None
) -> dict[uuid.UUID, dict]:
    """{user_id: {eligible, taken, score}}, for the per-person table.

    Grouped queries, not one per person: the same reason report_rows is built
    the way it is.
    """
    ids = list(user_ids)
    if not ids:
        return {}
    eligible_ids = post_sale_population(db, set(ids), today=today)["eligible_lead_ids"]
    per_user: dict[uuid.UUID, dict] = {
        user_id: {"eligible": 0, "taken": 0, "score": 0.0} for user_id in ids
    }
    if not eligible_ids:
        return per_user

    rows = db.execute(
        select(
            Lead.assigned_to_user_id, Lead.reference_status, func.count(Lead.id)
        )
        .where(Lead.id.in_(eligible_ids))
        .group_by(Lead.assigned_to_user_id, Lead.reference_status)
    ).all()
    for user_id, status, count in rows:
        entry = per_user.get(user_id)
        if entry is None:
            continue
        entry["eligible"] += count
        if status == ReferenceStatus.TAKEN:
            entry["taken"] += count
    for entry in per_user.values():
        entry["score"] = reference_score(entry["eligible"], entry["taken"])
    return per_user


def reference_metrics(
    db: Session, scope: set[uuid.UUID] | _All, *, today: date | None = None
) -> dict:
    """Reference progress over the eligible population, and only that.

    Two numbers kept apart on purpose, because collapsing them claims
    referrals the company never received:

        completed   the conversation is finished - gave one OR had none
        received    a reference was actually given

    The denominator is `eligible`, never "every won lead": an account nobody
    is allowed to ask yet is not an account somebody failed to ask.
    """
    population = post_sale_population(db, scope, today=today)
    eligible_ids = population["eligible_lead_ids"]
    if not eligible_ids:
        return {
            **{k: v for k, v in population.items() if k != "eligible_lead_ids"},
            "taken": 0,
            "declined": 0,
            "pending": 0,
            "not_asked": 0,
            "completed": 0,
            "completion_rate": 0.0,
            "reference_rate": 0.0,
            "score": 0.0,
        }

    rows = db.execute(
        select(Lead.reference_status, func.count(Lead.id))
        .where(Lead.id.in_(eligible_ids))
        .group_by(Lead.reference_status)
    ).all()
    by_status = {status: count for status, count in rows}

    taken = by_status.get(ReferenceStatus.TAKEN, 0)
    declined = by_status.get(ReferenceStatus.DECLINED, 0)
    pending = by_status.get(ReferenceStatus.PENDING, 0)
    not_asked = by_status.get(ReferenceStatus.NOT_ASKED, 0)
    completed = taken + declined
    total = len(eligible_ids)

    return {
        **{k: v for k, v in population.items() if k != "eligible_lead_ids"},
        "taken": taken,
        "declined": declined,
        "pending": pending,
        "not_asked": not_asked,
        "completed": completed,
        "completion_rate": round(completed / total * 100, 1),
        "reference_rate": round(taken / total * 100, 1),
        "score": reference_score(total, taken),
    }


# -------------------------------------------------------------- feedback


def feedback_metrics(
    db: Session, scope: set[uuid.UUID] | _All, *, today: date | None = None
) -> dict:
    """Feedback progress over the same eligible population.

    Deliberately the same starting set as references. They were different
    before - one counted SAP accounts, the other converted leads - which is
    how the dashboard could say 20 pending while the module said 11.
    """
    from app.models.feedback import Feedback, FeedbackRequest

    population = post_sale_population(db, scope, today=today)
    eligible_ids = population["eligible_lead_ids"]
    base = {k: v for k, v in population.items() if k != "eligible_lead_ids"}
    if not eligible_ids:
        return {**base, "received": 0, "pending": 0}

    # A response reaches its lead through the request it answered: that is
    # what the FB reference code in the form is for. There is no direct
    # feedback -> lead column, and adding one would be a second way to say
    # the same thing.
    answered = db.scalar(
        select(func.count(func.distinct(FeedbackRequest.lead_id)))
        .select_from(FeedbackRequest)
        .join(Feedback, Feedback.feedback_request_id == FeedbackRequest.id)
        .where(FeedbackRequest.lead_id.in_(eligible_ids))
    ) or 0

    return {
        **base,
        "received": answered,
        "pending": len(eligible_ids) - answered,
    }
