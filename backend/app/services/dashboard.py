"""The dashboard, in three shapes.

Every query here takes the visibility scope. A KPI that ignores scope leaks
data past the hierarchy, which is worse than a wrong number.

Feedback is the exception, and deliberately so: its scope is departments, not
people, so it comes from authority.feedback_department_scope and is omitted
entirely for somebody with no feedback rights.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.authority import ALL, _All, feedback_department_scope, visible_user_ids
from app.core.constants import (
    ADMIN_ROLES,
    LeadStatus,
    OPEN_LEAD_STATUSES,
    ReferenceStatus,
    Role,
)
from app.db.base import utcnow
from app.models.customer import SapImport
from app.models.feedback import Feedback
from app.models.lead import Lead, LeadActivity
from app.models.org import Department, Team, User
from app.models.reference import CustomerReference
from app.models.system import Notification
from app.services import feedback_analysis, leads as lead_service, metrics
from app.services import references as reference_service
from app.services import runtime_settings


def _shape_for(actor: User) -> str:
    if actor.role in ADMIN_ROLES:
        return "ADMIN"
    if actor.role == Role.MANAGER:
        return "MANAGER"
    return "PERSONAL"


def build(db: Session, actor: User) -> dict:
    # Read-only from here on, and several panels below start from the same
    # post-sale population: compute it once per scope, not once per panel.
    with metrics.memoized_population(db):
        return _build(db, actor)


def _build(db: Session, actor: User) -> dict:
    scope = visible_user_ids(db, actor)
    shape = _shape_for(actor)

    # ------------------------------------------------------- references
    references = reference_service.stats(db, actor, scope)

    # ----------------------------------------------------------- leads
    leads = lead_service.stats(db, scope)

    # ------------------------------------------------------------- org
    # Both counts exclude deactivated people. A leaver is not "a user in
    # scope": they cannot be assigned anything and appear on no other screen,
    # so counting them here made this the one number that still knew about
    # them - 22 against a Team page showing 21.
    # The same count answers both fields (they were two identical queries).
    user_stmt = select(func.count(User.id)).where(User.is_active.is_(True))
    if scope is not ALL:
        user_stmt = user_stmt.where(User.id.in_(scope))
    active_in_scope = db.scalar(user_stmt) or 0

    org = {
        "users_in_scope": active_in_scope,
        "active_users": active_in_scope,
        "unread_notifications": db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == actor.id, Notification.is_read.is_(False)
            )
        )
        or 0,
    }

    result: dict = {
        "shape": shape,
        "generated_at": utcnow(),
        "user_name": actor.name,
        "role": actor.role,
        "references": references,
        "my_reference": None,
        "leads": leads,
        "org": org,
        "feedback": None,
        # Scoped by people, not by department: these are the caller's own
        # converted leads and customers still owed an ask, which is their work
        # whether or not they may read the feedback module's analysis.
        # Counted, not listed: the number is all this screen shows.
        "feedback_pending": feedback_analysis.pending_count(db, actor, scope),
        "department_ratings": [],
        "monthly_feedback": [],
        "alerts": [],
        "teams": [],
        "reports": [],
        "imports": [],
    }

    # -------------------------------------------------------- feedback
    department_scope = feedback_department_scope(actor)
    if department_scope is ALL or department_scope:
        result.update(_feedback_panel(db, department_scope))

    if shape == "ADMIN":
        result["teams"] = _team_rollup(db)
        result["imports"] = _import_health(db)
        result["reports"] = report_rows(db, scope, exclude_id=actor.id, limit=25)
    elif shape == "MANAGER":
        result["reports"] = report_rows(db, scope, exclude_id=actor.id)
        # The team figure above covers the whole subtree; a manager who also
        # carries accounts of their own is scored on those separately.
        own = metrics.reference_scores_by_user(db, [actor.id], today=date.today())[actor.id]
        result["my_reference"] = {
            "eligible_accounts": own["eligible"],
            "references_taken": own["taken"],
            "reference_score": own["score"],
        }

    return result


def _feedback_panel(db: Session, department_scope) -> dict:
    config = runtime_settings.feedback_config(db)
    summary = feedback_analysis.department_summary(db, departments=department_scope)

    total = db.scalar(select(func.count(Feedback.id))) or 0
    imported = (
        db.scalar(select(func.count(Feedback.id)).where(Feedback.import_id.is_not(None)))
        or 0
    )
    average = db.scalar(
        select(func.avg(Feedback.overall_rating)).where(
            Feedback.overall_rating.is_not(None)
        )
    )
    alerts = feedback_analysis.open_alerts(db, departments=department_scope)
    names = dict(db.execute(select(Department.id, Department.name)).all())

    return {
        "feedback": {
            "total_responses": total,
            "imported_responses": imported,
            "average_rating": round(float(average), 2) if average is not None else None,
            "rating_scale_max": int(config["scale_max"]),
            "departments_below_threshold": sum(
                1 for row in summary if row["below_threshold"]
            ),
            "open_alerts": len(alerts),
        },
        "department_ratings": [
            {
                "department_id": row["department_id"],
                "department_name": row["department_name"],
                "average_rating": row["average_rating"],
                "response_count": row["response_count"],
                "below_threshold": row["below_threshold"],
            }
            for row in summary
        ],
        # A full year, so the frontend can offer a 3/6/12-month toggle
        # without a second round trip.
        "monthly_feedback": feedback_analysis.responses_per_month(db, months=12),
        "alerts": [
            {
                "department_id": alert.department_id,
                "department_name": names.get(alert.department_id, "—"),
                "average_rating": float(alert.average_rating),
                "response_count": alert.response_count,
                "threshold": float(alert.threshold),
            }
            for alert in alerts
        ],
    }


def _team_rollup(db: Session) -> list[dict]:
    """Headcount and pipeline per team. Unassigned staff are a row too -
    people with no team is a fact worth surfacing, not hiding.

    Counted from LEADS. This used to count owned SAP customers, which told an
    administrator nothing about what a team was working on: a team carrying
    sixty live leads and a team carrying none looked identical if neither
    happened to own an imported account.
    """

    # Three grouped queries for the whole table, not three per team: this used
    # to issue 3 x (teams + 1) round trips on every dashboard load. The numbers
    # are unchanged - a lead still counts towards the team of whoever it is
    # assigned to, and an unassigned lead still counts towards nobody.
    headcount = dict(
        db.execute(
            # Headcount means people who still work here.
            select(User.team_id, func.count(User.id))
            .where(User.is_active.is_(True))
            .group_by(User.team_id)
        ).all()
    )
    # Deliberately NOT filtered by is_active: deactivating someone does not
    # reassign their leads, so excluding them here would quietly shrink the
    # team's pipeline by however much the leaver was carrying.
    pipeline: dict[tuple, int] = {
        (team_id, status): count
        for team_id, status, count in db.execute(
            select(User.team_id, Lead.status, func.count(Lead.id))
            .join(User, Lead.assigned_to_user_id == User.id)
            .where(Lead.status.in_((*OPEN_LEAD_STATUSES, LeadStatus.CONVERTED)))
            .group_by(User.team_id, Lead.status)
        ).all()
    }

    def counts(team_id) -> tuple[int, int]:
        open_leads = sum(
            pipeline.get((team_id, status), 0) for status in OPEN_LEAD_STATUSES
        )
        return open_leads, pipeline.get((team_id, LeadStatus.CONVERTED), 0)

    out: list[dict] = []
    for team in db.execute(select(Team).order_by(Team.sort_order, Team.name)).scalars():
        open_leads, converted = counts(team.id)
        out.append(
            {
                "team_name": team.name,
                "members": headcount.get(team.id, 0),
                "open_leads": open_leads,
                "converted": converted,
            }
        )

    open_leads, converted = counts(None)
    out.append(
        {
            "team_name": "Unassigned",
            "members": headcount.get(None, 0),
            "open_leads": open_leads,
            "converted": converted,
        }
    )
    return out


def report_rows(
    db: Session,
    scope: set[uuid.UUID] | _All,
    *,
    exclude_id: uuid.UUID,
    limit: int | None = None,
) -> list[dict]:
    """Per-person activity for everyone in the caller's subtree.

    Built from grouped queries rather than one per person, so a twenty-person
    subtree is a handful of round trips and not sixty.
    """
    # Active people only. A deactivated account is not a person with a
    # workload: nobody can nudge them, nothing can be assigned to them, and
    # ordering by least-recent-activity would pin them to the top of the table
    # forever. This one filter also covers the assistant, which reads these
    # same rows through user_service.team_workload.
    stmt = (
        select(User)
        .where(
            User.id != exclude_id,
            User.is_active.is_(True),
            # The Super Admin runs the portal and carries no pipeline; a row
            # of zeroes for them is noise in a table about people's work.
            User.role != Role.SUPER_ADMIN,
        )
        # `team_name` below reads the relationship for every row.
        .options(selectinload(User.team))
    )
    if scope is not ALL:
        stmt = stmt.where(User.id.in_(scope))
    users = list(db.execute(stmt.order_by(User.name)).scalars().all())
    if not users:
        return []

    ids = [user.id for user in users]
    today = date.today()
    scores = metrics.reference_scores_by_user(db, ids, today=today)

    def grouped(stmt) -> dict:
        return dict(db.execute(stmt).all())

    # Everything here is about LEADS, which is the work these people actually
    # do. The columns this replaced - customers, invoices, last invoice,
    # per-person feedback rating - all came from the SAP book, which had no
    # relationship to any lead and therefore told you nothing about the
    # person's pipeline. A salesperson with 40 open leads showed "0 customers"
    # and looked idle.
    open_leads = grouped(
        select(Lead.assigned_to_user_id, func.count(Lead.id))
        .where(Lead.assigned_to_user_id.in_(ids), Lead.status.in_(OPEN_LEAD_STATUSES))
        .group_by(Lead.assigned_to_user_id)
    )
    converted = grouped(
        select(Lead.assigned_to_user_id, func.count(Lead.id))
        .where(
            Lead.assigned_to_user_id.in_(ids),
            Lead.status == LeadStatus.CONVERTED,
        )
        .group_by(Lead.assigned_to_user_id)
    )
    followups = grouped(
        select(Lead.assigned_to_user_id, func.count(Lead.id))
        .where(
            Lead.assigned_to_user_id.in_(ids),
            Lead.status == LeadStatus.CONVERTED,
            Lead.reference_status == ReferenceStatus.PENDING,
            Lead.next_reference_date.is_not(None),
            Lead.next_reference_date <= today,
        )
        .group_by(Lead.assigned_to_user_id)
    )
    # Referrals this person recorded against LEADS. The subject filter matters:
    # without it the column counted asks against the archived SAP customer book
    # - every "reference" on the demo Team table came from there, while the
    # Reference module (which counts leads) said nobody had taken one. Same
    # person, two screens, two answers.
    #
    # Counted per ASK, not per account, on purpose: this is attribution - "who
    # brought referrals in" - so a customer who names two people credits two.
    references_taken = grouped(
        select(CustomerReference.requested_by_user_id, func.count(CustomerReference.id))
        .where(
            CustomerReference.requested_by_user_id.in_(ids),
            CustomerReference.outcome == "YES",
            CustomerReference.lead_id.is_not(None),
        )
        .group_by(CustomerReference.requested_by_user_id)
    )
    # Last time this person did anything on a lead. Replaces "last invoice",
    # which measured SAP's activity rather than theirs.
    last_activity = grouped(
        select(LeadActivity.actor_user_id, func.max(LeadActivity.created_at))
        .where(LeadActivity.actor_user_id.in_(ids))
        .group_by(LeadActivity.actor_user_id)
    )

    rows = [
        {
            "user_id": user.id,
            "name": user.name,
            "role": user.role,
            "title": user.title,
            "team_name": user.team.name if user.team else None,
            "is_active": user.is_active,
            "open_leads": open_leads.get(user.id, 0),
            "converted": converted.get(user.id, 0),
            "references_taken": references_taken.get(user.id, 0),
            "followups_due": followups.get(user.id, 0),
            "eligible_accounts": scores[user.id]["eligible"],
            "references_on_eligible": scores[user.id]["taken"],
            "reference_score": scores[user.id]["score"],
            "last_activity_at": last_activity.get(user.id),
        }
        for user in users
    ]
    # Staleness first: the person with nothing recent is the one to look at.
    rows.sort(key=lambda r: (r["last_activity_at"] is not None, r["last_activity_at"]))
    return rows[:limit] if limit else rows


def _import_health(db: Session, limit: int = 5) -> list[dict]:
    return [
        {
            "filename": batch.filename,
            "status": batch.status,
            "total_rows": batch.total_rows,
            "created_count": batch.created_count,
            "skipped_count": batch.skipped_count,
            "error_count": batch.error_count,
            "created_at": batch.created_at,
        }
        for batch in db.execute(
            select(SapImport).order_by(SapImport.created_at.desc()).limit(limit)
        ).scalars()
    ]
