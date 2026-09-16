"""User directory and administration.

Route order matters: the literal paths (/actionable, /assignable-roles,
/org-chart) are declared before /{user_id}, otherwise FastAPI matches
"actionable" as a user id and every one of them 422s.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, or_, select

from app.core import authority
from app.core.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    LeadershipUser,
    SuperAdminUser,
    client_ip,
)
from app.models.org import Department, Team, User
from app.schemas.common import Message, Page
from app.schemas.user import (
    AssignableRoles,
    DepartmentOut,
    OrgNode,
    PasswordSetOut,
    ResetPasswordRequest,
    SelfUpdate,
    TeamOut,
    UserCreate,
    UserDetail,
    UserOut,
    UserUpdate,
)
from app.services import users as user_service

router = APIRouter(tags=["users"])


def _to_detail(db: DbSession, actor: User, user: User) -> UserDetail:
    detail = UserDetail.model_validate(user)
    detail.manager_name = user.manager.name if user.manager else None
    detail.team_name = user.team.name if user.team else None
    detail.heads_department_name = (
        user.heads_department.name if user.heads_department else None
    )
    detail.direct_report_count = db.scalar(
        select(func.count(User.id)).where(
            User.manager_id == user.id, User.is_active.is_(True)
        )
    ) or 0
    detail.can_act_on = authority.can_act_on(db, actor, user)
    return detail


# --------------------------------------------------------------- listing
@router.get("/users", response_model=Page[UserDetail])
def list_users(
    actor: LeadershipUser,
    db: DbSession,
    search: str | None = Query(default=None, max_length=120),
    role: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> Page[UserDetail]:
    """Everyone in the caller's subtree. Scoped, never post-filtered."""
    scope = authority.visible_user_ids(db, actor)

    stmt = select(User)
    if scope is not authority.ALL:
        stmt = stmt.where(User.id.in_(scope))
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    if role:
        stmt = stmt.where(User.role == role)
    if search:
        # func.lower + contains is portable; ILIKE is PostgreSQL-only.
        term = search.strip().lower()
        stmt = stmt.where(
            or_(
                func.lower(User.name).contains(term),
                func.lower(User.email).contains(term),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(
            stmt.order_by(User.name).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return Page[UserDetail](
        items=[_to_detail(db, actor, u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/users/actionable", response_model=list[UserOut])
def list_actionable_users(actor: LeadershipUser, db: DbSession) -> list[UserOut]:
    """Everyone the caller may act on - populates every assignee dropdown.

    Derived from the same can_act_on used to validate the submission, so the
    dropdown and the validation cannot disagree.
    """
    ids = authority.actionable_user_ids(db, actor)
    if not ids:
        return []
    rows = (
        db.execute(
            select(User)
            .where(User.id.in_(ids), User.is_active.is_(True))
            .order_by(User.name)
        )
        .scalars()
        .all()
    )
    return [UserOut.model_validate(u) for u in rows]


@router.get("/users/assignable-roles", response_model=AssignableRoles)
def get_assignable_roles(actor: LeadershipUser) -> AssignableRoles:
    """Rule 2, as data. The frontend renders the role dropdown from this and
    never hard-codes the role list."""
    return AssignableRoles(roles=authority.assignable_roles(actor))


@router.get("/users/org-chart", response_model=list[OrgNode])
def get_org_chart(
    actor: LeadershipUser,
    db: DbSession,
    include_inactive: bool = Query(default=False),
) -> list[OrgNode]:
    """Active people by default, same as `/users`. The Team page drives both
    from one switch, so the two halves of that page can never disagree about
    who is on the team."""
    return [
        OrgNode.model_validate(node)
        for node in user_service.org_chart(db, actor, include_inactive=include_inactive)
    ]


@router.get("/users/{user_id}", response_model=UserDetail)
def get_user(user_id: uuid.UUID, actor: LeadershipUser, db: DbSession) -> UserDetail:
    user = user_service.get_visible_user_or_404(db, actor, user_id)
    return _to_detail(db, actor, user)


# -------------------------------------------------------------- mutations
@router.post("/users", response_model=UserDetail, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate, request: Request, actor: AdminUser, db: DbSession
) -> UserDetail:
    user = user_service.create_user(
        db,
        actor,
        name=payload.name,
        email=str(payload.email),
        password=payload.password,
        role=str(payload.role),
        phone=payload.phone,
        title=payload.title,
        honorific=str(payload.honorific) if payload.honorific else None,
        manager_id=payload.manager_id,
        team_id=payload.team_id,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return _to_detail(db, actor, user)


@router.patch("/users/{user_id}", response_model=UserDetail)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    actor: AdminUser,
    db: DbSession,
) -> UserDetail:
    target = user_service.get_visible_user_or_404(db, actor, user_id)
    # exclude_unset so "not mentioned" and "explicitly set to null" stay
    # distinguishable - clearing a manager is a real operation.
    changes = payload.model_dump(exclude_unset=True)
    user_service.update_user(db, actor, target, changes, ip_address=client_ip(request))
    db.commit()
    db.refresh(target)
    return _to_detail(db, actor, target)


@router.post("/users/{user_id}/reset-password", response_model=PasswordSetOut)
def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    request: Request,
    actor: AdminUser,
    db: DbSession,
) -> PasswordSetOut:
    """Set somebody's password. The password is never returned.

    Existing passwords cannot be shown at all - they are one-way hashes. The
    new one is not echoed either: whoever set it already has it, so sending it
    back would put a live credential in a response, a browser cache and any
    proxy log for no gain.
    """
    target = user_service.get_visible_user_or_404(db, actor, user_id)
    user_service.reset_password(
        db,
        actor,
        target,
        payload.new_password,
        must_change=payload.must_change,
        ip_address=client_ip(request),
    )
    db.commit()
    return PasswordSetOut(
        message=(
            f"{target.name} must choose a new password at their next sign-in."
            if payload.must_change
            else f"{target.name} can sign in with this password."
        ),
        must_change_password=payload.must_change,
    )


@router.delete("/users/{user_id}", response_model=Message)
def deactivate_user(
    user_id: uuid.UUID,
    request: Request,
    actor: AdminUser,
    db: DbSession,
    # A query parameter rather than a body: DELETE bodies are optional in the
    # spec and several HTTP clients drop them silently.
    new_manager_id: uuid.UUID | None = Query(default=None),
) -> Message:
    """Soft deactivate. Hard deletion would orphan leads and history."""
    target = user_service.get_visible_user_or_404(db, actor, user_id)
    user_service.deactivate_user(
        db,
        actor,
        target,
        reassign_reports_to=new_manager_id,
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(message=f"{target.name} has been deactivated.")


@router.delete("/users/{user_id}/permanent", response_model=Message)
def delete_user_permanently(
    user_id: uuid.UUID, request: Request, actor: SuperAdminUser, db: DbSession
) -> Message:
    """Permanently remove an account that has no history. Super Admin only.

    Deliberately a separate route from DELETE /users/{id}, which soft
    deactivates. Two very different outcomes should not share one verb and
    differ by a flag somebody can pass by accident.

    Refused with a 409 listing what is attached the moment the person has any
    history - see `users.delete_user`.
    """
    target = user_service.get_visible_user_or_404(db, actor, user_id)
    name = target.name
    user_service.delete_user(db, actor, target, ip_address=client_ip(request))
    db.commit()
    return Message(message=f"{name} was permanently deleted.")


@router.post("/users/{user_id}/reactivate", response_model=UserDetail)
def reactivate_user(
    user_id: uuid.UUID, request: Request, actor: AdminUser, db: DbSession
) -> UserDetail:
    target = user_service.get_visible_user_or_404(db, actor, user_id)
    user_service.reactivate_user(db, actor, target, ip_address=client_ip(request))
    db.commit()
    db.refresh(target)
    return _to_detail(db, actor, target)


# ----------------------------------------------------------- self-service
@router.patch("/me", response_model=UserOut)
def update_me(
    payload: SelfUpdate, request: Request, user: CurrentUser, db: DbSession
) -> UserOut:
    """Name and phone only. Role, manager and active status are not settable
    here by anyone, including a SUPER_ADMIN."""
    user_service.update_self(
        db, user, payload.model_dump(exclude_unset=True), ip_address=client_ip(request)
    )
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


# --------------------------------------------------------------- lookups
@router.get("/teams", response_model=list[TeamOut])
def list_teams(_: CurrentUser, db: DbSession) -> list[TeamOut]:
    rows = (
        db.execute(
            select(Team).where(Team.is_active.is_(True)).order_by(Team.sort_order, Team.name)
        )
        .scalars()
        .all()
    )
    return [TeamOut.model_validate(t) for t in rows]


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(_: CurrentUser, db: DbSession) -> list[DepartmentOut]:
    rows = (
        db.execute(
            select(Department)
            .where(Department.is_active.is_(True))
            .order_by(Department.sort_order, Department.name)
        )
        .scalars()
        .all()
    )
    return [DepartmentOut.model_validate(d) for d in rows]
