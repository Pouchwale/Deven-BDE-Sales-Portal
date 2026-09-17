"""Customer feedback — Module 3.

Read scope is the union computed in authority.feedback_department_scope:
admins see everything, a flagged department head sees their own department's
ratings, and a plain manager sees nothing — customer feedback is about rated
departments, not about reporting lines.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.dashboard import check_analytics_rate
from app.core import authority
from app.core.authority import ALL
from app.core.constants import AlertStatus, ErrorCode, FeedbackImportSource
from app.core.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    LeadershipUser,
    VisibilityScope,
    client_ip,
)
from app.core.errors import ApiError, forbidden, invalid, not_found
from app.models.feedback import (
    Feedback,
    FeedbackAlert,
    FeedbackDepartmentRating,
    FeedbackImport,
)
from app.models.org import Department, User
from app.schemas.common import MAX_LIST_ITEMS, MAX_PAGE, MAX_PAGE_SIZE, Message, Page
from app.schemas.feedback import (
    AlertAssign,
    AlertOut,
    CommitRequest,
    DepartmentRatingOut,
    DepartmentSummary,
    FeedbackAnalysis,
    FeedbackOut,
    FeedbackStats,
    ImportSummary,
    MonthlyVolume,
    PendingFeedbackItem,
)
from app.services import (
    customer_source,
    feedback_analysis,
    feedback_import,
    feedback_requests,
    metrics,
    runtime_settings,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])

# 10 MB, per plan v3 s8.1.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
#: The pending queue is paged in the browser; this only bounds the response.
MAX_PENDING_ITEMS = 5_000


def _action_scope_or_403(db: Session, actor):
    """Which departments this caller may ACT on - open, assign and resolve
    alerts. Still administrators and department heads only: an alert is a job
    somebody owns, not a number everybody reads.
    """
    scope = authority.feedback_department_scope(actor)
    if scope is not ALL and not scope:
        raise forbidden(
            "Feedback alerts are handled by administrators and department heads.",
            ErrorCode.FORBIDDEN,
        )
    return scope


def _may_see_contacts(actor) -> bool:
    """Whether this caller may read the customer's phone number and email.

    Everybody can read what customers said and how each department scores -
    that is how the company sees its own performance. Contact details are a
    different thing: they are only needed by somebody who is going to reply,
    so they stay with administrators and department heads.
    """
    scope = authority.feedback_department_scope(actor)
    return scope is ALL or bool(scope)


def _redact_contacts(out: FeedbackOut) -> FeedbackOut:
    out.mobile = None
    out.email = None
    return out


def _department_names(db: Session) -> dict[uuid.UUID, str]:
    return dict(db.execute(select(Department.id, Department.name)).all())


def _department_order(db: Session) -> dict[uuid.UUID, int]:
    """Where each department sits in the company's own ordering.

    Ratings come back in whatever order they were written, which makes two
    responses impossible to compare at a glance. Sorting by the configured
    order means Sales is always the first line of every card.
    """
    return {
        row[0]: index
        for index, row in enumerate(
            db.execute(
                select(Department.id).order_by(Department.sort_order, Department.name)
            ).all()
        )
    }


def _user_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def _with_assignee(alert: FeedbackAlert, departments: dict, users: dict) -> AlertOut:
    out = AlertOut.model_validate(alert)
    out.department_name = departments.get(alert.department_id)
    out.assigned_to_name = users.get(alert.assigned_to_user_id) if alert.assigned_to_user_id else None
    return out


def _stats(db: Session, scope) -> FeedbackStats:
    config = runtime_settings.feedback_config(db)
    summary = feedback_analysis.department_summary(db, departments=scope)

    total = db.scalar(select(func.count(Feedback.id))) or 0
    imported = (
        db.scalar(
            select(func.count(Feedback.id)).where(Feedback.import_id.is_not(None))
        )
        or 0
    )
    average = db.scalar(
        select(func.avg(Feedback.overall_rating)).where(
            Feedback.overall_rating.is_not(None)
        )
    )
    recommend_yes = (
        db.scalar(
            select(func.count(Feedback.id)).where(
                func.lower(func.coalesce(Feedback.would_recommend, "")).like("y%")
            )
        )
        or 0
    )
    recommend_any = (
        db.scalar(
            select(func.count(Feedback.id)).where(
                Feedback.would_recommend.is_not(None), Feedback.would_recommend != ""
            )
        )
        or 0
    )

    return FeedbackStats(
        total_responses=total,
        imported_responses=imported,
        average_rating=round(float(average), 2) if average is not None else None,
        rating_scale_max=config["scale_max"],
        departments_below_threshold=sum(1 for row in summary if row["below_threshold"]),
        open_alerts=len(feedback_analysis.open_alerts(db, departments=scope)),
        would_recommend_rate=(
            round(recommend_yes / recommend_any * 100, 1) if recommend_any else None
        ),
    )


@router.get("/analysis", response_model=FeedbackAnalysis)
def analysis(actor: CurrentUser, db: DbSession) -> FeedbackAnalysis:
    """Everything on one screen: per-department averages, the response trend,
    and - for whoever handles them - the open alerts.

    The numbers are company-wide for every signed-in user. Feedback is how the
    business knows how it is doing, and a BDE who cannot see that the Dispatch
    department is at 2.4 has no way to understand the complaint they are about
    to receive. The alerts list stays narrow, because an alert is a job.
    """
    check_analytics_rate(actor, "feedback-analysis")
    alert_scope = authority.feedback_department_scope(actor)
    scope = ALL
    config = runtime_settings.feedback_config(db)
    names = _department_names(db)

    open_alerts = (
        feedback_analysis.open_alerts(db, departments=alert_scope)
        if alert_scope is ALL or alert_scope
        else []
    )
    users = _user_names(
        db, {a.assigned_to_user_id for a in open_alerts if a.assigned_to_user_id}
    )
    alerts = [_with_assignee(alert, names, users) for alert in open_alerts]

    return FeedbackAnalysis(
        handles_alerts=alert_scope is ALL or bool(alert_scope),
        stats=_stats(db, scope),
        departments=[
            DepartmentSummary(**row)
            for row in feedback_analysis.department_summary(db, departments=scope)
        ],
        alerts=alerts,
        monthly=[
            # A full year, so the frontend's 3/6/12-month toggle needs no
            # second round trip.
            MonthlyVolume(**row)
            for row in feedback_analysis.responses_per_month(db, months=12)
        ],
        threshold=float(config["threshold"]),
        window_days=int(config["window_days"]),
        scale_max=int(config["scale_max"]),
    )


@router.get("/alerts", response_model=list[AlertOut])
def alerts(
    actor: CurrentUser,
    db: DbSession,
    include_resolved: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=MAX_LIST_ITEMS),
) -> list[AlertOut]:
    scope = _action_scope_or_403(db, actor)
    names = _department_names(db)

    stmt = select(FeedbackAlert)
    if not include_resolved:
        stmt = stmt.where(FeedbackAlert.status == AlertStatus.OPEN)
    if scope is not ALL:
        stmt = stmt.where(FeedbackAlert.department_id.in_(scope))

    rows = list(
        db.execute(stmt.order_by(FeedbackAlert.opened_at.desc()).limit(limit)).scalars()
    )
    users = _user_names(db, {a.assigned_to_user_id for a in rows if a.assigned_to_user_id})
    return [_with_assignee(alert, names, users) for alert in rows]


@router.post("/alerts/{alert_id}/assign", response_model=AlertOut)
def assign_alert(
    alert_id: uuid.UUID, payload: AlertAssign, actor: LeadershipUser, db: DbSession
) -> AlertOut:
    """Hand a flagged department to a person, or clear the assignee."""
    alert = db.get(FeedbackAlert, alert_id)
    if alert is None:
        raise not_found("Alert not found.")
    scope = _action_scope_or_403(db, actor)
    if scope is not ALL and alert.department_id not in scope:
        raise forbidden("That department is outside your feedback scope.")

    feedback_analysis.assign_alert(db, actor, alert, payload.user_id)
    db.commit()

    names = _department_names(db)
    users = _user_names(db, {alert.assigned_to_user_id} if alert.assigned_to_user_id else set())
    return _with_assignee(alert, names, users)


@router.get("/pending", response_model=list[PendingFeedbackItem])
def pending_requests(
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
    limit: int = Query(default=MAX_PENDING_ITEMS, ge=1, le=MAX_PENDING_ITEMS),
) -> list[PendingFeedbackItem]:
    """Everyone still owed a feedback ask.

    Converted leads whose post-sale record says they were invoiced 10+ days
    ago and who have no completed response on file. The same population
    Reference Tracking works from - see `/feedback/pending/summary`.
    """
    items = feedback_analysis.pending_requests(db, actor, scope)
    return [PendingFeedbackItem(**item) for item in items[:limit]]


@router.get("/pending/summary")
def pending_summary(_: CurrentUser, db: DbSession, scope: VisibilityScope) -> dict:
    """The population this module measures, and why part of it is not here yet.

    Deliberately the SAME call Reference Tracking's KPIs come from, so the two
    modules cannot describe different books. It is what lets the empty queue
    say "12 accounts are waiting on the post-sale sync" instead of showing a
    zero that reads as "we have no customers" - very different problems that a
    bare 0 cannot tell apart.
    """
    return metrics.feedback_metrics(db, scope)


@router.delete("/requests/{request_id}", response_model=Message)
def cancel_request(
    request_id: uuid.UUID,
    request: Request,
    actor: CurrentUser,
    db: DbSession,
    scope: VisibilityScope,
) -> Message:
    """Withdraw an ask. Its code stops matching from this point on.

    Scoped like the record it belongs to: 404, not 403, for somebody else's.
    """
    cancelled = feedback_requests.cancel(
        db, actor, scope, request_id, ip_address=client_ip(request)
    )
    db.commit()
    return Message(message=f"{cancelled.reference} cancelled.")


@router.get("", response_model=Page[FeedbackOut])
def list_feedback(
    actor: CurrentUser,
    db: DbSession,
    department_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    #: Overall rating band. "LOW" is what a department head is actually
    #: hunting for; narrowing a fetched page in the browser would only find
    #: the unhappy customers who happened to be on it.
    rating: str | None = Query(default=None, pattern="^(LOW|MID|HIGH)$"),
    page: int = Query(default=1, ge=1, le=MAX_PAGE),
    page_size: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
) -> Page[FeedbackOut]:
    """Every response, to everyone signed in - see `analysis` for why.

    What each department scored and what the customer wrote is the whole point
    of the module. Contact details are stripped for anyone who is not an
    administrator or a department head.
    """
    scope = ALL
    contacts = _may_see_contacts(actor)
    names = _department_names(db)
    order = _department_order(db)
    sample_imports = set(
        db.execute(
            select(FeedbackImport.id).where(
                FeedbackImport.source == FeedbackImportSource.SAMPLE
            )
        ).scalars()
    )

    stmt = select(Feedback).options(selectinload(Feedback.department_ratings))

    # A department head only sees responses that actually rated their
    # department — not every response with their column blanked out.
    if scope is not ALL:
        stmt = stmt.where(
            Feedback.id.in_(
                select(FeedbackDepartmentRating.feedback_id).where(
                    FeedbackDepartmentRating.department_id.in_(scope)
                )
            )
        )
    if department_id is not None:
        stmt = stmt.where(
            Feedback.id.in_(
                select(FeedbackDepartmentRating.feedback_id).where(
                    FeedbackDepartmentRating.department_id == department_id
                )
            )
        )
    if search:
        term = search.strip().lower()
        stmt = stmt.where(
            func.lower(func.coalesce(Feedback.customer_name, "")).contains(term)
            | func.lower(func.coalesce(Feedback.company_name, "")).contains(term)
            | func.lower(func.coalesce(Feedback.overall_comments, "")).contains(term)
        )
    if rating is not None:
        # Bands, not an exact score: the question is "who is unhappy", and
        # the configured threshold is what the rest of the module already
        # means by that. Unrated responses match no band.
        threshold = float(runtime_settings.feedback_config(db)["threshold"])
        stmt = stmt.where(Feedback.overall_rating.is_not(None))
        if rating == "LOW":
            stmt = stmt.where(Feedback.overall_rating < threshold)
        elif rating == "MID":
            stmt = stmt.where(
                Feedback.overall_rating >= threshold,
                Feedback.overall_rating < threshold + 1,
            )
        else:
            stmt = stmt.where(Feedback.overall_rating >= threshold + 1)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(
                func.coalesce(Feedback.submitted_at_source, Feedback.created_at).desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .unique()
        .all()
    )

    items: list[FeedbackOut] = []
    for row in rows:
        out = FeedbackOut.model_validate(row)
        out.department_ratings = [
            DepartmentRatingOut(
                department_id=rating.department_id,
                department_name=names.get(rating.department_id, "—"),
                rating=rating.rating,
                raw_value=rating.raw_value,
                comments=rating.comments,
            )
            for rating in sorted(
                row.department_ratings,
                key=lambda r: order.get(r.department_id, 99),
            )
        ]
        out.is_sample = row.import_id in sample_imports
        items.append(out if contacts else _redact_contacts(out))

    return Page[FeedbackOut](items=items, total=total, page=page, page_size=page_size)


# ------------------------------------------------------------- importing
@router.get("/imports", response_model=list[ImportSummary])
def import_history(_: AdminUser, db: DbSession) -> list[ImportSummary]:
    return [
        ImportSummary.model_validate(batch)
        for batch in feedback_import.import_history(db)
    ]


async def _read_upload(file: UploadFile) -> bytes:
    """Bounded read + content check. The client filename is only a label."""
    # .xls (BIFF) cannot be read by anything installed; refused up front.
    name = customer_source.safe_display_name(file.filename)
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if suffix not in (".xlsx", ".csv"):
        raise invalid("Upload the Google Forms export as .xlsx or .csv.")
    # One byte past the limit is enough to know it is too big, without
    # holding an arbitrarily large body in memory.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    try:
        customer_source.check_bytes(content, suffix)
    except customer_source.SourceFileRejected as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR, str(exc), status_code=exc.status_code
        ) from None
    return content


@router.post("/import/dry-run")
async def import_dry_run(
    actor: AdminUser, db: DbSession, file: UploadFile = File(...)
) -> dict:
    """Resolve the mapping and show it. Writes nothing.

    Not optional: form headers are whole sentences that change when somebody
    edits the form, and a silent mismapping corrupts the analysis invisibly.
    """
    content = await _read_upload(file)
    config = runtime_settings.feedback_config(db)
    result = feedback_import.dry_run(
        db, content, customer_source.safe_display_name(file.filename), scale_max=config["scale_max"]
    )
    return result.as_dict()


@router.post("/import/commit")
async def import_commit(
    actor: AdminUser,
    db: DbSession,
    file: UploadFile = File(...),
    create_missing_departments: bool = Query(default=True),
) -> dict:
    """Write the batch, then evaluate alerts ONCE for the whole import."""
    content = await _read_upload(file)
    config = runtime_settings.feedback_config(db)

    preview = feedback_import.dry_run(
        db, content, customer_source.safe_display_name(file.filename), scale_max=config["scale_max"]
    )
    if not preview.can_commit:
        raise ApiError(
            ErrorCode.UNMAPPED_COLUMNS,
            "No rating column could be resolved in this file. "
            "Check the dry run before committing.",
            status_code=422,
            details=preview.as_dict(),
        )

    result = feedback_import.commit(
        db,
        content,
        customer_source.safe_display_name(file.filename),
        actor=actor,
        scale_max=config["scale_max"],
        create_missing_departments=create_missing_departments,
    )
    # Once per batch, not once per row.
    alerts_changed = feedback_analysis.evaluate(db)
    db.commit()

    return {**result.as_dict(), "alerts": alerts_changed}


@router.post("/evaluate-alerts")
def evaluate_alerts(_: AdminUser, db: DbSession) -> dict:
    """Re-run the alert evaluation by hand, e.g. after changing the threshold."""
    changed = feedback_analysis.evaluate(db)
    db.commit()
    return changed


__all__ = ["router", "FeedbackImport", "CommitRequest"]
