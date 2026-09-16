"""Who may see whom, and who may act on whom.

Every visibility and authority decision in the portal is made here. A router
that re-implements one of these checks is a bug, because two copies of a rule
drift and the drift is invisible until somebody sees data they should not.

Two rules do all the work (plan v3 s3):

  Rule 1  You may never act on somebody who outranks you.
  Rule 2  You may never hand out a role above your own.

and one derived rule that falls out of them:

  Rule 3  Demotion is not an escape hatch - a manager cannot demote a peer
          in order to act on them, because Rule 1 blocks the demotion.

Rank is a derived integer where LOWER MEANS MORE SENIOR (SUPER_ADMIN is 0).
Read every comparison below with that in mind: `rank(target) < rank(actor)`
means "the target outranks the actor".
"""
from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.constants import ADMIN_ROLES, ALL_ROLES, ROLE_RANK, Role
from app.models.org import User


class _All:
    """Sentinel for "every user", returned to admins by visible_user_ids.

    A sentinel rather than None so that `if scope is ALL` reads unambiguously
    at the call sites, and so an accidental `len(scope)` fails loudly instead
    of quietly meaning "nobody".
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ALL"

    def __contains__(self, item: object) -> bool:
        return True

    def __bool__(self) -> bool:
        return True


ALL: Final = _All()

# A reporting chain deeper than this is a data error, not an org. The bound
# also stops a cycle introduced outside the API from hanging a request.
MAX_CHAIN_DEPTH: Final = 32


def rank(role_or_user: str | User) -> int:
    """Seniority as an integer. Lower is more senior."""
    role = role_or_user.role if isinstance(role_or_user, User) else str(role_or_user)
    try:
        return ROLE_RANK[role]
    except KeyError:  # pragma: no cover - guarded by a CHECK constraint
        raise ValueError(f"Unknown role {role!r}") from None


# --------------------------------------------------------------- the chain
def subtree_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Every id below `user_id` in the reporting chain, at any depth.

    A recursive CTE rather than a Python loop: one round trip instead of one
    per level, and identical SQL on SQLite and PostgreSQL. UNION (not UNION
    ALL) makes a cycle terminate rather than spin.
    """
    stmt = text(
        """
        WITH RECURSIVE chain(id) AS (
            SELECT id FROM users WHERE manager_id = :actor_id
            UNION
            SELECT u.id FROM users u JOIN chain c ON u.manager_id = c.id
        )
        SELECT id FROM chain
        """
    )
    rows = db.execute(stmt, {"actor_id": str(user_id)}).scalars().all()
    return {row if isinstance(row, uuid.UUID) else uuid.UUID(str(row)) for row in rows}


def ancestor_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Every id above `user_id`, nearest first, excluding the user."""
    found: set[uuid.UUID] = set()
    current: uuid.UUID | None = user_id
    for _ in range(MAX_CHAIN_DEPTH):
        manager_id = db.execute(
            text("SELECT manager_id FROM users WHERE id = :id"), {"id": str(current)}
        ).scalar()
        if manager_id is None:
            break
        manager_uuid = (
            manager_id if isinstance(manager_id, uuid.UUID) else uuid.UUID(str(manager_id))
        )
        if manager_uuid in found:  # a pre-existing cycle; stop rather than spin
            break
        found.add(manager_uuid)
        current = manager_uuid
    return found


def is_ancestor(db: Session, actor: User, target: User) -> bool:
    """Is `actor` somewhere above `target` in the chain?"""
    return actor.id in ancestor_ids(db, target.id)


def chain_to_top(db: Session, user: User) -> list[User]:
    """The managers above this user, nearest first."""
    out: list[User] = []
    seen: set[uuid.UUID] = {user.id}
    current = user
    for _ in range(MAX_CHAIN_DEPTH):
        if current.manager_id is None or current.manager_id in seen:
            break
        manager = db.get(User, current.manager_id)
        if manager is None:
            break
        out.append(manager)
        seen.add(manager.id)
        current = manager
    return out


# ------------------------------------------------------------- visibility
def visible_user_ids(db: Session, actor: User) -> set[uuid.UUID] | _All:
    """Whose rows `actor` may see. ALL means no filter at all.

    A manager sees their own subtree at any depth - nothing sideways, nothing
    above. A field user sees only themselves.
    """
    if actor.role in ADMIN_ROLES:
        return ALL
    if actor.role == Role.MANAGER:
        return {actor.id} | subtree_ids(db, actor.id)
    return {actor.id}


def can_view_user(db: Session, actor: User, target_id: uuid.UUID) -> bool:
    scope = visible_user_ids(db, actor)
    return scope is ALL or target_id in scope


# -------------------------------------------------------------- Rule 1
def can_act_on(db: Session, actor: User, target: User) -> bool:
    """May `actor` edit, reset, deactivate, re-role or re-parent `target`?

    "Act on" also covers reassigning a lead away from them and opening their
    personal dashboard.

    Self-service is deliberately NOT covered here: changing your own name or
    password goes through PATCH /api/me, which allows a strictly smaller set
    of fields. Returning False for self keeps the two paths from blurring.
    """
    if actor.id == target.id:
        return False
    if not can_view_user(db, actor, target.id):
        return False
    if rank(target) < rank(actor):
        return False                      # the target outranks the actor
    if rank(target) == rank(actor) and not is_ancestor(db, actor, target):
        return False                      # peers cannot act on each other
    return True


def actionable_user_ids(db: Session, actor: User) -> set[uuid.UUID]:
    """Everyone `actor` may act on. Populates the assignee dropdowns.

    Derived from can_act_on rather than reimplementing the rule, so the
    dropdown and the endpoint that validates the submission can never
    disagree.
    """
    scope = visible_user_ids(db, actor)
    if scope is ALL:
        candidates = db.query(User).all()
    else:
        candidates = db.query(User).filter(User.id.in_(scope)).all()
    return {u.id for u in candidates if can_act_on(db, actor, u)}


# -------------------------------------------------------------- Rule 2
def assignable_roles(actor: User) -> list[str]:
    """The roles `actor` may grant, on creation and on promotion alike.

    Returned by GET /api/users/assignable-roles so the frontend renders the
    dropdown from the server's answer and never hard-codes the role list.
    """
    return [r for r in ALL_ROLES if ROLE_RANK[r] > rank(actor)]


def can_grant_role(actor: User, role: str) -> bool:
    return role in assignable_roles(actor)


# ------------------------------------------------- reporting-line integrity
def would_create_cycle(
    db: Session, user_id: uuid.UUID, new_manager_id: uuid.UUID | None
) -> bool:
    """Would pointing `user` at `new_manager` close a loop?

    A loop hides people from everyone above it, so it is refused at the API
    edge rather than defended against on every read.
    """
    if new_manager_id is None:
        return False
    if new_manager_id == user_id:
        return True
    return user_id in ancestor_ids(db, new_manager_id)


def manager_rank_is_valid(manager: User, report_role: str) -> bool:
    """A manager must outrank their report, or be a MANAGER over a MANAGER.

    The equal-rank exception exists for the real Ramanesh -> Shailesh line.
    It is safe because can_act_on still requires ancestry for equal ranks,
    and would_create_cycle stops the relationship being mutual.
    """
    if rank(manager) < ROLE_RANK[report_role]:
        return True
    return rank(manager) == ROLE_RANK[report_role] and manager.role == Role.MANAGER


# ------------------------------------------------------------- feedback
def feedback_department_scope(
    actor: User,
) -> set[uuid.UUID] | _All:
    """Which departments' feedback `actor` may read.

    Admins see everything. Everyone else sees only the department they are
    flagged as head of - a plain MANAGER with no flag sees no feedback at
    all, because customer feedback is about rated departments, not about
    reporting lines. (plan v3 s8.7)
    """
    if actor.role in ADMIN_ROLES:
        return ALL
    if actor.heads_department_id:
        return {actor.heads_department_id}
    return set()
