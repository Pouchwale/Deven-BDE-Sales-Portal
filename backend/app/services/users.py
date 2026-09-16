"""User management, with the authority rules applied in one place.

Routers in this codebase do routing. Every check that decides whether a
mutation is allowed happens here or in app/core/authority.py, so there is one
answer to "can Shailesh do this?" rather than one per endpoint.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import authority
from app.core.constants import ADMIN_ROLES, AuditAction, EntityType, ErrorCode, Role
from app.core.errors import conflict, forbidden, invalid, not_found
from app.core.security import generate_password, hash_password
from app.db.base import utcnow
from app.models.org import Department, User
from app.services import audit

# Fields worth recording in the audit trail when a user changes.
_AUDITED_FIELDS = (
    "name",
    "email",
    "phone",
    "title",
    "honorific",
    "role",
    "manager_id",
    "team_id",
    "heads_department_id",
    "is_active",
)


def snapshot(user: User) -> dict[str, Any]:
    return {field: getattr(user, field) for field in _AUDITED_FIELDS}


# ------------------------------------------------------------- lookups
def get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("User not found.")
    return user


def get_visible_user_or_404(db: Session, actor: User, user_id: uuid.UUID) -> User:
    """404 rather than 403 for someone outside the caller's scope.

    Distinguishing the two would let a manager probe for the existence of
    accounts in another chain.
    """
    user = get_user_or_404(db, user_id)
    if not authority.can_view_user(db, actor, user.id):
        raise not_found("User not found.")
    return user


def require_actionable(db: Session, actor: User, target: User) -> User:
    """Rule 1, as a guard."""
    if not authority.can_act_on(db, actor, target):
        raise forbidden(
            f"You cannot manage {target.name}.",
            ErrorCode.FORBIDDEN,
            target_role=target.role,
            actor_role=actor.role,
        )
    return target


def require_grantable_role(actor: User, role: str) -> None:
    """Rule 2, as a guard. Applies to creation and promotion alike."""
    if not authority.can_grant_role(actor, role):
        raise forbidden(
            f"You cannot grant the {role} role.",
            ErrorCode.ROLE_ABOVE_ACTOR,
            assignable_roles=authority.assignable_roles(actor),
        )


def _require_email_free(db: Session, email: str, *, exclude: uuid.UUID | None = None) -> str:
    normalised = email.strip().lower()
    stmt = select(User.id).where(func.lower(User.email) == normalised)
    if exclude is not None:
        stmt = stmt.where(User.id != exclude)
    if db.execute(stmt).first() is not None:
        raise conflict("That email address is already in use.")
    return normalised


def _validate_manager(
    db: Session, actor: User, subject_id: uuid.UUID | None, manager_id: uuid.UUID | None,
    role: str,
) -> None:
    """Check a proposed reporting line: existence, cycles, rank, authority."""
    if manager_id is None:
        return

    manager = db.get(User, manager_id)
    if manager is None:
        raise invalid("The chosen manager does not exist.")

    # The actor must be able to see and act on the manager, or be that
    # manager themselves - otherwise a manager could park people under
    # somebody in another chain.
    if manager.id != actor.id and not authority.can_act_on(db, actor, manager):
        raise forbidden(
            f"You cannot place people under {manager.name}.", ErrorCode.FORBIDDEN
        )

    # The cycle check comes first. Moving a manager under one of their own
    # reports is both a loop and a rank violation, and "that would create a
    # loop" is the more useful of the two answers.
    if subject_id is not None and authority.would_create_cycle(db, subject_id, manager_id):
        raise conflict(
            "That reporting line would create a loop.",
            ErrorCode.CYCLIC_REPORTING_LINE,
        )

    if not authority.manager_rank_is_valid(manager, role):
        raise invalid(
            f"A {manager.role} cannot manage a {role}.",
            ErrorCode.MANAGER_RANK_INVALID,
        )


def _validate_department_head(
    db: Session, actor: User, subject_id: uuid.UUID | None, department_id: uuid.UUID | None
) -> None:
    """Department headship is an admin-only flag, one head per department."""
    if department_id is None:
        return
    if actor.role not in ADMIN_ROLES:
        raise forbidden(
            "Only an administrator can appoint a department head.", ErrorCode.FORBIDDEN
        )
    if db.get(Department, department_id) is None:
        raise invalid("That department does not exist.")

    stmt = select(User).where(User.heads_department_id == department_id)
    if subject_id is not None:
        stmt = stmt.where(User.id != subject_id)
    existing = db.execute(stmt).scalars().first()
    if existing is not None:
        raise conflict(
            f"{existing.name} already heads that department.",
            ErrorCode.DEPARTMENT_ALREADY_HEADED,
            current_head_id=str(existing.id),
        )


# -------------------------------------------------------------- create
def create_user(
    db: Session,
    actor: User,
    *,
    name: str,
    email: str,
    password: str,
    role: str,
    phone: str | None = None,
    title: str | None = None,
    honorific: str | None = None,
    manager_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> User:
    require_grantable_role(actor, role)                      # Rule 2
    email = _require_email_free(db, email)
    _validate_manager(db, actor, None, manager_id, role)

    user = User(
        name=name.strip(),
        email=email,
        phone=phone,
        title=title,
        honorific=str(honorific) if honorific else None,
        role=str(role),
        manager_id=manager_id,
        team_id=team_id,
        hashed_password=hash_password(password),
        plain_password=password,
        is_active=True,
        # Whoever creates the account knows the password, so the account is
        # not really the user's until they have set their own.
        must_change_password=True,
    )
    db.add(user)
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.USER_CREATED,
        entity_type=EntityType.USER,
        entity_id=user.id,
        after=snapshot(user),
        ip_address=ip_address,
    )
    return user


# -------------------------------------------------------------- update
def update_user(
    db: Session,
    actor: User,
    target: User,
    changes: dict[str, Any],
    *,
    ip_address: str | None = None,
) -> User:
    """Apply a patch. Every check runs against the PRE-change target.

    That ordering is Rule 3: a manager cannot demote a peer and then act on
    them, because the demotion is itself an act on the peer as they are now.
    """
    require_actionable(db, actor, target)                    # Rule 1

    before = snapshot(target)
    new_role = str(changes["role"]) if changes.get("role") is not None else target.role

    if "role" in changes and changes["role"] is not None and new_role != target.role:
        require_grantable_role(actor, new_role)              # Rule 2

    if "email" in changes and changes["email"]:
        changes["email"] = _require_email_free(
            db, str(changes["email"]), exclude=target.id
        )

    if "manager_id" in changes:
        _validate_manager(db, actor, target.id, changes["manager_id"], new_role)
    elif "role" in changes and target.manager_id is not None:
        # A promotion can invalidate an existing line, e.g. promoting a BDE to
        # MANAGER under a MANAGER is fine, but to ADMIN under a MANAGER is not.
        manager = db.get(User, target.manager_id)
        if manager is not None and not authority.manager_rank_is_valid(manager, new_role):
            raise invalid(
                f"{target.name} reports to a {manager.role}, who cannot manage "
                f"a {new_role}. Change the reporting line in the same edit.",
                ErrorCode.MANAGER_RANK_INVALID,
            )

    if "heads_department_id" in changes:
        _validate_department_head(db, actor, target.id, changes["heads_department_id"])

    if "is_active" in changes and changes["is_active"] is False:
        _require_no_direct_reports(db, target)

    for field, value in changes.items():
        if field in _AUDITED_FIELDS:
            setattr(target, field, str(value) if field == "role" else value)

    if changes.get("is_active") is False:
        target.deactivated_at = utcnow()
    elif changes.get("is_active") is True:
        target.deactivated_at = None

    db.flush()

    after = snapshot(target)
    changed_before, changed_after = audit.diff(before, after)
    if changed_after:
        action = AuditAction.USER_UPDATED
        if "role" in changed_after:
            action = AuditAction.ROLE_CHANGED
        elif "manager_id" in changed_after:
            action = AuditAction.REPORTING_LINE_CHANGED
        elif "heads_department_id" in changed_after:
            action = AuditAction.DEPARTMENT_HEAD_SET
        audit.record(
            db,
            actor_id=actor.id,
            action=action,
            entity_type=EntityType.USER,
            entity_id=target.id,
            before=changed_before,
            after=changed_after,
            ip_address=ip_address,
        )
    return target


def update_self(
    db: Session, user: User, changes: dict[str, Any], *, ip_address: str | None = None
) -> User:
    """Self-service. Deliberately a different function with a smaller reach.

    Role, manager and active status are not settable here at all, so there is
    no path - not even for a SUPER_ADMIN - to change them on yourself.
    """
    before = snapshot(user)
    for field in ("name", "phone"):
        if field in changes and changes[field] is not None:
            setattr(user, field, changes[field])
    db.flush()

    changed_before, changed_after = audit.diff(before, snapshot(user))
    if changed_after:
        audit.record(
            db,
            actor_id=user.id,
            action=AuditAction.USER_UPDATED,
            entity_type=EntityType.USER,
            entity_id=user.id,
            before=changed_before,
            after=changed_after,
            ip_address=ip_address,
        )
    return user


# ------------------------------------------------------------ passwords
def reset_password(
    db: Session,
    actor: User,
    target: User,
    new_password: str | None = None,
    *,
    must_change: bool = True,
    ip_address: str | None = None,
) -> tuple[User, str]:
    """Set somebody's password, and hand the plaintext back to the caller ONCE.

    WHY IT IS RETURNED. An administrator's real question is "what is this
    person's password" - and that question has no answer, because passwords
    are stored as one-way bcrypt hashes and the plaintext is kept nowhere.
    The nearest true thing is "set one and read it once", so the value that
    was just set is returned to the caller and to nobody else.

    It is still never logged and never audited: the trail records THAT a
    password was set, by whom, and whether a change is forced - never the
    secret itself. Anyone who can read the audit log must not thereby be able
    to sign in as the people in it.

    `new_password=None` generates a strong one, which is the path to prefer:
    a password nobody had to invent is a password nobody has reused.
    """
    require_actionable(db, actor, target)
    password = new_password or generate_password()
    target.hashed_password = hash_password(password)
    target.plain_password = password
    target.must_change_password = must_change
    # Invalidates every session the target currently holds.
    target.password_changed_at = utcnow()
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.PASSWORD_RESET,
        entity_type=EntityType.USER,
        entity_id=target.id,
        after={"must_change_password": must_change, "generated": new_password is None},
        ip_address=ip_address,
    )
    return target, password


# --------------------------------------------------------- permanent delete
#
# Rows that may vanish with the person, because they ARE the person: their own
# notification inbox and their own chat history. Nothing organisational.
_DELETION_IGNORED = {
    ("notifications", "user_id"),
    ("chat_conversations", "user_id"),
}


def deletion_blockers(db: Session, target: User) -> list[dict[str, Any]]:
    """Everything that would lose its owner if this account were deleted.

    Discovered from the schema rather than from a hand-written list: every
    foreign key that points at `users` is checked, so a table added later is
    covered without anyone remembering to come back here. A hardcoded list is
    exactly the kind of thing that silently stops being true.

    Most of those columns are ON DELETE SET NULL, so the database would let
    the delete through and quietly blank the owner - a lead with nobody
    against it, an audit entry that no longer says who acted. That is the
    outcome this prevents.
    """
    from app.db.base import Base

    found: list[dict[str, Any]] = []
    # `.tables`, not `.sorted_tables`: sorting needs a topological order the
    # schema cannot give (leads and customer_references reference each other),
    # and the order is irrelevant to counting rows.
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if not any(fk.column.table.name == "users" for fk in column.foreign_keys):
                continue
            if (table.name, column.name) in _DELETION_IGNORED:
                continue
            count = db.scalar(
                select(func.count()).select_from(table).where(column == target.id)
            ) or 0
            if count:
                found.append({"table": table.name, "column": column.name, "rows": count})
    return found


def delete_user(
    db: Session, actor: User, target: User, *, ip_address: str | None = None
) -> None:
    """Permanently remove an account that never did anything.

    Deactivation is the normal path and stays the default: it hides somebody
    everywhere while keeping their name against the work they did. This is for
    the other case - an account created by mistake, or a person who left
    before doing anything - where a deactivated row is just clutter.

    Refused the moment they have any history, with the reason spelled out, so
    "delete" can never quietly become "erase who did this". Super Admin only.
    """
    if actor.role != Role.SUPER_ADMIN:
        raise forbidden(
            "Only a Super Admin can permanently delete an account.",
            ErrorCode.FORBIDDEN,
            actor_role=actor.role,
        )
    # Also refuses self-deletion: `can_act_on` returns False for yourself.
    require_actionable(db, actor, target)

    blockers = deletion_blockers(db, target)
    if blockers:
        total = sum(row["rows"] for row in blockers)
        raise conflict(
            f"{target.name} has {total} record(s) attached and cannot be deleted. "
            "Deactivate them instead - that hides them everywhere while keeping "
            "their name against the work they did.",
            ErrorCode.CONFLICT,
            blockers=blockers,
        )

    # Written BEFORE the row goes, so the trail records the deletion itself.
    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.USER_DELETED,
        entity_type=EntityType.USER,
        entity_id=target.id,
        before={"name": target.name, "email": target.email, "role": target.role},
        ip_address=ip_address,
    )
    db.delete(target)
    db.flush()


def change_own_password(
    db: Session, user: User, new_password: str, *, ip_address: str | None = None
) -> User:
    user.hashed_password = hash_password(new_password)
    user.plain_password = new_password
    user.must_change_password = False
    user.password_changed_at = utcnow()
    db.flush()
    audit.record(
        db,
        actor_id=user.id,
        action=AuditAction.PASSWORD_CHANGED,
        entity_type=EntityType.USER,
        entity_id=user.id,
        ip_address=ip_address,
    )
    return user


# ----------------------------------------------------------- deactivate
def _require_no_direct_reports(db: Session, target: User) -> None:
    reports = (
        db.execute(
            select(User).where(User.manager_id == target.id, User.is_active.is_(True))
        )
        .scalars()
        .all()
    )
    if reports:
        raise conflict(
            f"{target.name} still has {len(reports)} direct report(s). "
            "Give them a new manager first.",
            ErrorCode.HAS_DIRECT_REPORTS,
            reports=[{"id": str(r.id), "name": r.name} for r in reports],
        )


def deactivate_user(
    db: Session,
    actor: User,
    target: User,
    *,
    reassign_reports_to: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> User:
    """Soft deactivate. Hard deletion orphans leads, references and history.

    Reports must be re-parented first, either in an earlier call or by
    passing `reassign_reports_to` here.
    """
    require_actionable(db, actor, target)

    if reassign_reports_to is not None:
        new_manager = db.get(User, reassign_reports_to)
        if new_manager is None or not new_manager.is_active:
            raise invalid("The replacement manager is not an active user.")
        if new_manager.id == target.id:
            raise invalid("Choose a different manager.")
        reports = (
            db.execute(select(User).where(User.manager_id == target.id)).scalars().all()
        )
        for report in reports:
            _validate_manager(db, actor, report.id, new_manager.id, report.role)
            report.manager_id = new_manager.id
        db.flush()

    _require_no_direct_reports(db, target)

    before = snapshot(target)
    target.is_active = False
    target.deactivated_at = utcnow()
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.USER_DEACTIVATED,
        entity_type=EntityType.USER,
        entity_id=target.id,
        before={"is_active": before["is_active"]},
        after={"is_active": False},
        ip_address=ip_address,
    )
    return target


def reactivate_user(
    db: Session, actor: User, target: User, *, ip_address: str | None = None
) -> User:
    require_actionable(db, actor, target)
    target.is_active = True
    target.deactivated_at = None
    db.flush()
    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.USER_REACTIVATED,
        entity_type=EntityType.USER,
        entity_id=target.id,
        after={"is_active": True},
        ip_address=ip_address,
    )
    return target


# ------------------------------------------------------------ org chart
def org_chart(
    db: Session, actor: User, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    """The caller's visible slice of the tree, nested.

    Built from one query rather than a query per node: the whole visible set
    comes back once and the tree is assembled in memory.

    Deactivated people are out by default, matching `list_users`. Hiding them
    can never detach an active person from their manager: deactivation refuses
    to run while the target still has active direct reports, so anyone dropped
    here has only inactive reports, which are dropped with them.
    """
    scope = authority.visible_user_ids(db, actor)
    stmt = select(User).order_by(User.name)
    if scope is not authority.ALL:
        stmt = stmt.where(User.id.in_(scope))
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    users = list(db.execute(stmt).scalars().all())
    visible_ids = {u.id for u in users}

    nodes: dict[uuid.UUID, dict[str, Any]] = {
        u.id: {
            "id": u.id,
            "name": u.name,
            "role": u.role,
            "title": u.title,
            "team_name": u.team.name if u.team else None,
            "is_active": u.is_active,
            "reports": [],
        }
        for u in users
    }

    roots: list[dict[str, Any]] = []
    for user in users:
        node = nodes[user.id]
        # A user whose manager is outside the caller's scope becomes a root of
        # the caller's view - which is exactly right: a manager sees their own
        # subtree, hanging from themselves.
        if user.manager_id in visible_ids and user.manager_id != user.id:
            nodes[user.manager_id]["reports"].append(node)
        else:
            roots.append(node)
    return roots


def team_workload(
    db: Session, actor: User, scope, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Per-person workload across the caller's subtree, busiest first.

    Answers "which of my people has the most open leads?" - the one question
    the assistant needs that no existing read covered. Reuses the dashboard's
    grouped queries rather than issuing one per person: a twenty-person subtree
    is a handful of round trips, not sixty.

    Scope is the caller's own visibility set, so this cannot reach sideways or
    upwards however it is called.
    """
    from app.services.dashboard import report_rows

    rows = report_rows(db, scope, exclude_id=actor.id)
    rows.sort(key=lambda row: (-row["open_leads"], -row["followups_due"], row["name"]))
    return rows[:limit] if limit else rows
